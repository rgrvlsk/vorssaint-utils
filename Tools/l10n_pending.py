#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Vorssaint

"""What a language's maintainer still has to review, written as the body of
one tracking issue per language.

A language is reviewed up to a marker: a line in the header of its main catalog
(Sources/Vorssaint/Core/Localizations/Strings+<Name>.swift)

    // l10n-reviewed: <commit> @<github-login>

Everything in that language that changed after the marker, written by someone
other than the reviewer, is pending, and so is every line whose English moved
after it was translated. Without a marker, --since gives the commit
(default: the commit that added the language).

Usage:
  l10n_pending.py --repo PATH --ref upstream/main --lang sk [--since SHA] [--reviewer NAME] [--md OUT] [--json OUT]
"""
import argparse, collections, json, re, sys, os
sys.dont_write_bytecode = True   # no __pycache__ next to the tools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l10n_check as L

LOGIN = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})'
MARK = re.compile(rf'^//[ \t]*l10n-reviewed:[ \t]*([0-9a-f]{{7,40}})((?:[ \t]+@{LOGIN})*)[ \t]*$', re.M)
NOREPLY = re.compile(r'(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com', re.I)

def main_catalog_path(git, ref, lang):
    for f in git.files(ref, 'Sources/Vorssaint/Core/Localizations'):
        src = git.show(ref, f) or ''
        if re.search(rf'static let {lang} = Strings\(', src): return f
    return None

def intro_commit(git, ref, lang):
    out = git.run('log', '--reverse', '--format=%H', '-S', f'case {lang} = "', ref, '--',
                  'Sources/Vorssaint/Core/Localization.swift').split()
    return out[0] if out else None

_email_login = {}
def email_login(gh_repo, email, commit):
    """the GitHub account a commit email belongs to, asked once per email"""
    if not gh_repo or not email: return None
    if email not in _email_login:
        import subprocess
        r = subprocess.run(['gh', 'api', f'repos/{gh_repo}/commits/{commit}', '--jq', '.author.login // empty'],
                           capture_output=True, text=True)
        _email_login[email] = r.stdout.strip() if r.returncode == 0 else None
    return _email_login[email]

_pr_author = {}
def pr_author(gh_repo, number):
    """the GitHub login that opened a PR, when gh is available (the weekly job)"""
    if not gh_repo or not number: return None
    if number not in _pr_author:
        import subprocess
        r = subprocess.run(['gh', 'api', f'repos/{gh_repo}/pulls/{number}', '--jq', '.user.login'], capture_output=True, text=True)
        _pr_author[number] = r.stdout.strip() if r.returncode == 0 else None
    return _pr_author[number]

def pending(git, ref, lang, since=None, reviewers=None, gh_repo=None):
    """reviewers: GitHub logins (the marker's volunteers) or git author names"""
    langs = L.langs_of(git, ref)
    path = main_catalog_path(git, ref, lang)
    volunteers = list(reviewers or [])
    if path and not since:
        m = MARK.search(git.show(ref, path) or '')
        if m:
            since = m.group(1)
            volunteers += re.findall(rf'@({LOGIN})', m.group(2))
    if not since:  # no marker: from the commit that added the language, by its author
        since = intro_commit(git, ref, lang)
        volunteers.append(git.run('show', '-s', '--format=%an', since).strip())
    since = git.run('rev-parse', since).strip()
    R = L.Catalog(git, ref, set(langs)); S = L.Catalog(git, since, set(langs))
    keys = {v.lower() for v in volunteers if v}

    blames = {}
    meta = {}
    def who(commit):
        if commit not in meta:
            an, ae, ad, s, body = git.run('show', '-s', '--format=%an%x00%ae%x00%ad%x00%s%x00%b', '--date=short', commit).split('\x00', 4)
            pr = re.findall(r'\(#(\d+)\)', s)
            ids = {an.lower()} | {m.lower() for m in NOREPLY.findall(ae)}
            for name, mail in re.findall(r'^Co-authored-by:\s*(.*?)\s*<([^>]*)>', body, re.M | re.I):
                ids |= {name.lower()} | {m.lower() for m in NOREPLY.findall(mail)}
            for login in (pr_author(gh_repo, pr[-1] if pr else None), email_login(gh_repo, ae.strip(), commit)):
                if login: ids.add(login.lower())
            meta[commit] = dict(author=an, date=ad, subject=s, pr=pr[-1] if pr else None, ids=ids)
        return meta[commit]

    items = []
    for typ, by in sorted(R.inst.items()):
        it = by.get(lang); en = by.get('enUS')
        if not it or not en: continue
        old = {a['label']: a['expr'] for a in S.inst.get(typ, {}).get(lang, {}).get('args', [])} if S.inst.get(typ, {}).get(lang) else {}
        en_now = {a['label']: a['expr'] for a in en['args']}
        en_old = {a['label']: a['expr'] for a in S.inst.get(typ, {}).get('enUS', {}).get('args', [])} if S.inst.get(typ, {}).get('enUS') else {}
        if it['path'] not in blames: blames[it['path']] = git.blame(ref, it['path'])
        bl = blames[it['path']]
        for a in it['args']:
            f = a['label']; now = a['expr']
            was = old.get(f)
            if was is not None and L.meaning(was) == L.meaning(now): continue
            cs = [bl.get(x) for x in range(a['l0'], a['l1'] + 1) if bl.get(x)]
            if not cs: continue
            commit = max(cs, key=lambda x: x[1])[0]; w = who(commit)
            if w['ids'] & keys: continue
            kind = 'new' if was is None else 'rewritten'
            items.append(dict(kind=kind, type=typ, field=f, path=it['path'], line=a['l0'],
                              en=L.literal(en_now.get(f, '')) or L.norm(en_now.get(f, ''))[:200],
                              en_before=(L.literal(en_old[f]) if f in en_old else None),
                              text=L.literal(now) or L.norm(now)[:200],
                              before=(L.literal(was) if was else None), **{k: v for k, v in w.items() if k != 'ids'}))
    # lines whose English moved after translation (from the structural checker)
    F, _ = L.run(git, ref, ref, lang, blame=True)
    seen = {(i['type'], i['field']) for i in items}
    for f in F:
        if f['cat'] != 'M2': continue
        typ = f['msg'].split('.')[0]
        if (typ, f.get('field')) in seen: continue
        # the reviewer already saw any English that changed before the marker
        en_then = {a['label']: a['expr'] for a in S.inst.get(typ, {}).get('enUS', {}).get('args', [])} if S.inst.get(typ, {}).get('enUS') else {}
        en_now_ = {a['label']: a['expr'] for a in R.inst.get(typ, {}).get('enUS', {}).get('args', [])} if R.inst.get(typ, {}).get('enUS') else {}
        fld = f.get('field')
        if fld in en_then and fld in en_now_ and L.meaning(en_then[fld]) == L.meaning(en_now_[fld]): continue
        items.append(dict(kind='stale', type=typ, field=f.get('field'), path=f['path'], line=f['line'],
                          en=f['now'], en_before=f['then'], text=None, before=None,
                          author=None, date=None, subject=None, pr=None))
    return dict(lang=lang, code=langs.get(lang), ref=git.run('rev-parse', ref).strip(), since=since,
                reviewer=' '.join(('@' + v) if re.fullmatch(LOGIN, v) else v for v in dict.fromkeys(volunteers)),
                volunteers=[v for v in dict.fromkeys(volunteers) if re.fullmatch(LOGIN, v)], path=path, items=items)

UI = {
  'en': dict(new='New strings written by others', rewritten='Strings rewritten by others',
             stale='English changed, translation did not',
             head="Reviewed up to `{since}` by {reviewer}. Main: `{ref}`.",
             total="**{n} strings to review**: {parts}.",
             howto="To clear them: a PR that touches only this language and moves the `// l10n-reviewed:` marker "
                   "in `{path}` to the commit you reviewed up to. Partial reviews are fine: move it as far as you got.",
             english='English', more='… and {n} more', none='Nothing left to review.',
             short=dict(new='new', rewritten='rewritten by others', stale='with changed English'),
             cut='… and {groups} more groups. Run `python3 Tools/l10n_pending.py --lang <case>` for the full list.'),
}
KINDS = ('new', 'rewritten', 'stale')

def code(text):
    """repository text shown as inline code: no mentions, links or markup"""
    t = (text or '').replace('\n', ' ')
    fence = '``' if '`' in t else '`'
    return f'{fence} {t} {fence}' if fence == '``' else f'`{t}`'


def issue_body(P, per_group=8, ui='en', limit=60000):
    T = UI[ui]
    c = collections.Counter(i['kind'] for i in P['items'])
    out = [T['head'].format(since=P['since'][:10], reviewer=P['reviewer'], ref=P['ref'][:10]), '',
           T['total'].format(n=len(P['items']), parts=', '.join(f"{c[k]} {T['short'][k]}" for k in KINDS if c[k])), '',
           T['howto'].format(path=P['path']), '']
    for k in KINDS:
        xs = [i for i in P['items'] if i['kind'] == k]
        if not xs: continue
        out += [f'### {T[k]} ({len(xs)})', '']
        groups = collections.OrderedDict()
        for i in xs:
            key = f"#{i['pr']}" if i.get('pr') else (i['subject'][:60] if i.get('subject') else i['type'])
            groups.setdefault(key, []).append(i)
        for key, g in groups.items():
            src = f" · {code(g[0]['author'])} {g[0]['date']}" if g[0].get('author') else ''
            out.append(f"<details><summary>{key}{src} — {len(g)}</summary>\n")
            for i in g[:per_group]:
                if k == 'stale':
                    out.append(f"- `{i['type']}.{i['field']}`: {T['english']} {code(L.worddiff(i['en_before'] or '', i['en'] or ''))}")
                else:
                    out.append(f"- `{i['type']}.{i['field']}`\n  - EN: {code(i['en'][:160])}\n  - {P['lang']}: {code((i['text'] or '')[:160])}")
            if len(g) > per_group: out.append(T['more'].format(n=len(g) - per_group))
            out.append('\n</details>\n')
    body = '\n'.join(out)
    if len(body) > limit:  # GitHub refuses issue bodies over 65,536 characters
        cut = body.rfind('\n</details>\n', 0, limit - 400)
        shown = body[:cut].count('<summary>')
        body = body[:cut] + '\n</details>\n\n' + T['cut'].format(groups=body.count('<summary>') - shown)
    return body

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='.'); ap.add_argument('--ref', default='upstream/main')
    ap.add_argument('--lang', required=True); ap.add_argument('--since'); ap.add_argument('--reviewer', action='append', help='volunteer login or git name; repeatable')
    ap.add_argument('--gh-repo', help='owner/name, to match volunteers by the login that opened each PR')
    ap.add_argument('--md'); ap.add_argument('--json'); ap.add_argument('--ui', default='en', choices=('en',))
    a = ap.parse_args()
    g = L.Git(a.repo)
    P = pending(g, a.ref, a.lang, a.since, a.reviewer, a.gh_repo)
    if a.json: json.dump(P, open(a.json, 'w'), ensure_ascii=False, indent=1)
    body = issue_body(P, ui=a.ui)
    if a.md: open(a.md, 'w').write(body)
    c = collections.Counter(i['kind'] for i in P['items'])
    print(f"{a.lang}: {len(P['items'])} pending ({', '.join(f'{k}={v}' for k, v in c.items())}) since {P['since'][:10]}, reviewer {P['reviewer']}")
