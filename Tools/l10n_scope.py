#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Vorssaint

"""Certify that a translation PR changes one language's strings and nothing else.

A PR is in scope when every change between --base and --head is one of:
  - a string value of the target language, still a plain string literal (no
    \\( interpolation), with the same format arguments as the English, in an
    initializer whose field list does not change;
  - a line of the target language's Resources/<code>.lproj/InfoPlist.strings;
  - the language's review marker, in its main catalog only:
        // l10n-reviewed: <commit> @volunteer [@volunteer ...]
    moving forward to a commit main already has.
Files may only be modified: nothing added, removed, renamed or re-typed.
Anything else fails and is listed.

Notes, never failures: who moved the marker and whether they are listed as a
volunteer, volunteers added or removed, and how far the marker move is backed
by work (strings changed, strings still in English, English that moved).

Usage:
  l10n_scope.py --repo PATH --base REV --head REV --lang sk|auto [--main REV] [--author LOGIN] [--md OUT] [--lang-out FILE]
Exit status: 0 in scope (or a new language), 1 out of scope.
"""
import argparse, os, re, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l10n_check as L

LOGIN = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})'
MARK = re.compile(rf'^//[ \t]*l10n-reviewed:[ \t]*([0-9a-f]{{7,40}})((?:[ \t]+@{LOGIN})*)[ \t]*$')
REF = re.compile(r'^\s*\.?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?\s*$')   # sk.foo or .de: a catalog reference
NEW = 'is new in this PR'

def run_rc(repo, *a):
    return subprocess.run(['git', '-C', repo, *a], capture_output=True).returncode

def changed_files(git, a, b):
    """[(status, path)] from a NUL-separated diff, so no file name can split"""
    out = subprocess.run(['git', '-C', git.repo, 'diff', '-z', '--no-renames', '--name-status', a, b],
                         capture_output=True, check=True).stdout.decode('utf-8', 'surrogateescape').split('\0')
    out = [x for x in out if x != '']
    return list(zip(out[0::2], out[1::2]))

def main_catalog(src, lang):
    return re.search(rf'static let {lang} = Strings\(', src or '') is not None

def marker(src):
    for ln in (src or '').split('\n'):
        m = MARK.match(ln.strip())
        if m: return m.group(1), re.findall(rf'@({LOGIN})', m.group(2))
    return None

def regions(sf, lang):
    """1-based line ranges that belong to the language in this file"""
    out = []
    for it in sf.instances({lang}):
        if it['lang'] == lang: out.append((it['line'], it.get('end', it['line'])))
    is_main = main_catalog(sf.src, lang)
    for i, ln in enumerate(sf.src.split('\n'), 1):
        # any marker line, even a malformed one, belongs to the language of its
        # main catalog; check() then rejects the malformed ones
        if re.match(rf'\s*case\s+\.{lang}\s*:\s*return\s+\.{lang}\s*$', ln) or (is_main and 'l10n-reviewed' in ln):
            out.append((i, i))
    return out

def strip(src, rs):
    return [ln.rstrip() for i, ln in enumerate(src.split('\n'), 1) if not any(a <= i <= b for a, b in rs)]

def check(git, base, head, lang, main=None, author=None):
    langs = L.langs_of(git, head)
    code = langs.get(lang)
    problems, notes, touched = [], [], []
    if lang not in langs: return [f'.{lang} is not a language of this tree'], notes, touched
    if L.langs_of(git, base).get(lang) is None:
        return [f'.{lang} {NEW}: the scope check is for updates to an existing language'], notes, touched
    mb = git.run('merge-base', base, head).strip()
    plist = f'Resources/{code}.lproj/InfoPlist.strings'
    english = {}
    for t, by in L.Catalog(git, head, set(langs)).inst.items():
        if 'enUS' in by: english[t] = {a['label']: a['expr'] for a in by['enUS']['args']}
    old_mark = new_mark = None
    for status, f in changed_files(git, mb, head):
        shown = L.safe(f.encode('unicode_escape').decode('ascii'))
        if status != 'M':
            problems.append(f'`{shown}`: {"added" if status == "A" else "removed" if status == "D" else "changed type"}; a translation PR only edits files that exist'); continue
        if f == plist: touched.append(f); continue
        if not (f.startswith('Sources/') and f.endswith('.swift')):
            problems.append(f'`{shown}`: not a {lang} catalog'); continue
        a_src, b_src = git.show(mb, f), git.show(head, f)
        A, B = L.SwiftFile(f, a_src), L.SwiftFile(f, b_src)
        ra, rb = regions(A, lang), regions(B, lang)
        if not rb:
            problems.append(f'`{shown}`: has no {lang} strings'); continue
        sa, sb = strip(a_src, ra), strip(b_src, rb)
        if sa != sb:
            import difflib
            d = [l for l in difflib.unified_diff(sa, sb, lineterm='', n=0) if l[:1] in '+-' and not l.startswith(('+++', '---'))]
            problems.append(f'`{shown}`: changes outside the {lang} strings, first: {L.safe(d[0][:120]) if d else "?"}')
            continue
        ia = {i['type']: i for i in A.instances({lang}) if i['lang'] == lang}
        ib = {i['type']: i for i in B.instances({lang}) if i['lang'] == lang}
        for t in sorted(set(ia) | set(ib)):
            la = [x['label'] for x in ia[t]['args']] if t in ia else None
            lb = [x['label'] for x in ib[t]['args']] if t in ib else None
            if la != lb:
                problems.append(f'`{shown}`: {t}.{lang} adds, removes or reorders fields'); continue
            old = {x['label']: x['expr'] for x in ia[t]['args']}
            for x in ib[t]['args']:
                if L.norm(x['expr']) == L.norm(old.get(x['label'], '')): continue
                v = L.literal(x['expr'])
                where = f'{t}.{x["label"]} (line {x["l0"]})'
                if v is None and not REF.match(x['expr']):
                    problems.append(f'`{shown}`: {where} is not a plain string'); continue
                if v is not None and L.interpolations(x['expr']):
                    problems.append(f'`{shown}`: {where} interpolates code with \\( ); translations are plain text'); continue
                e = L.literal(english.get(t, {}).get(x['label'], '') or '')
                if v is not None and e is not None and L.placeholders(v) != L.placeholders(e) \
                   and (x['label'].endswith('Format') or L.placeholders(e)):
                    problems.append(f'`{shown}`: {where} has format arguments {L.placeholders(v) or "none"}, the English {L.placeholders(e) or "none"}')
        touched.append(f)
        if main_catalog(b_src, lang):
            ma, mbk = marker(a_src), marker(b_src)
            if any('l10n-reviewed' in l and not MARK.match(l.strip()) for l in b_src.split('\n')):
                problems.append(f'`{shown}`: the review marker is malformed; it must read `// l10n-reviewed: <commit> @volunteer`')
            if ma != mbk: old_mark, new_mark = ma, mbk
    # the marker can only move forward, to a commit main has
    if new_mark:
        new = new_mark[0]
        if run_rc(git.repo, 'rev-parse', '--verify', '--quiet', f'{new}^{{commit}}') != 0:
            problems.append(f'the marker points at `{new}`, which this repository does not have')
        else:
            new_full = git.run('rev-parse', f'{new}^{{commit}}').strip()
            if main and run_rc(git.repo, 'merge-base', '--is-ancestor', new_full, main) != 0:
                problems.append(f'the marker points at `{new}`, which is not on main')
            elif old_mark and run_rc(git.repo, 'merge-base', '--is-ancestor', old_mark[0], new_full) != 0:
                problems.append(f'the marker moves backwards, from `{old_mark[0]}` to `{new}`')
        before = set(old_mark[1]) if old_mark else set(); after = set(new_mark[1])
        if before != after:
            parts = []
            if after - before: parts.append('added: ' + ' '.join('@' + x for x in sorted(after - before)))
            if before - after: parts.append('removed: ' + ' '.join('@' + x for x in sorted(before - after)))
            notes.append('volunteers ' + ', '.join(parts) + '. A change to who looks after the language is for the maintainer to confirm.')
        if author and author.lower() not in {x.lower() for x in (before or after)}:
            notes.append(f'the marker was moved by @{author}, who is not listed as a volunteer for this language. '
                         'That is allowed; a listed volunteer may want to look.')
        if old_mark and not problems:
            notes += coverage(git, mb, head, lang, old_mark, new_mark)
    return problems, notes, touched

def coverage(git, base, head, lang, old_mark, new_mark):
    """how much of what the marker now covers the PR actually touched"""
    import l10n_pending as P
    try:
        items = P.pending(git, new_mark[0], lang, since=old_mark[0], reviewers=old_mark[1] + new_mark[1])['items']
    except Exception as e:  # never fail the check on the report
        return [f'could not compute what the marker covers ({type(e).__name__})']
    if not items: return [f'the marker moves from `{old_mark[0]}` to `{new_mark[0]}`; nothing was waiting for review in between.']
    langs = set(L.langs_of(git, head))
    Hc, Bc = L.Catalog(git, head, langs), L.Catalog(git, base, langs)
    def val(C, t, f, l=lang):
        it = C.inst.get(t, {}).get(l)
        return next((x['expr'] for x in it['args'] if x['label'] == f), None) if it else None
    changed = english = stale = 0
    for i in items:
        if not i['type']: continue
        now, was = val(Hc, i['type'], i['field']), val(Bc, i['type'], i['field'])
        if L.norm(now or '') != L.norm(was or ''): changed += 1
        elif i['kind'] == 'stale': stale += 1
        e = val(Hc, i['type'], i['field'], 'enUS')
        if now is not None and e is not None and L.norm(now) == L.norm(e) and len(L.words(L.literal(now) or '')) >= 2: english += 1
    return [f'the marker moves from `{old_mark[0]}` to `{new_mark[0]}` over {len(items)} strings that were waiting for review: '
            f'this PR changes {changed}, {english} still read in English, and {stale} still follow English that has since changed. '
            'Unchanged strings can be fine as they are; this is for information.']

def markdown(problems, notes, touched, lang):
    if len(problems) == 1 and NEW in problems[0]:
        out = (f"### Translation scope: new language `.{lang}`\n\nA new language adds code as well as strings, "
               "so the one-language scope check does not apply. The structural check below still does.\n")
    elif not problems:
        out = (f"### Translation scope: in scope ✅\n\nOnly `.{lang}` string values and its review marker change "
               f"({len(touched)} file{'s' if len(touched) != 1 else ''}): plain text, same format arguments as the English, "
               "no field lists, no other language, no code. The wording itself is for the language's volunteers.\n")
    else:
        out = (f"### Translation scope: out of scope ❌\n\nA `.{lang}` translation PR may only change that language's "
               "string values and its review marker. These changes fall outside:\n\n" + '\n'.join(f'- {p}' for p in problems) + '\n')
    if notes:
        out += '\n**Notes** (for information, not failures)\n\n' + '\n'.join(f'- {n}' for n in notes) + '\n'
    return out

def line_scores(g, base, head):
    """changed lines per language (English included), and whether anything
    changed that belongs to no language at all"""
    mb = g.run('merge-base', base, head).strip(); score = {}; other = False
    langs = L.langs_of(g, head)
    for status, f in changed_files(g, mb, head):
        if status != 'M': other = True; continue
        m = re.match(r'Resources/(.+)\.lproj/InfoPlist\.strings$', f)
        if m:
            for l, c in langs.items():
                if c == m.group(1): score[l] = score.get(l, 0) + 1
            continue
        if not f.endswith('.swift'): other = True; continue
        changed = set()
        for h in re.finditer(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', g.run('diff', '-U0', mb, head, '--', f), re.M):
            st, n = int(h.group(1)), int(h.group(2) or 1); changed |= set(range(st, st + max(n, 1)))
        sf = L.SwiftFile(f, g.show(head, f) or '')
        owned = set()
        for l in langs:
            rs = regions(sf, l)
            hit = {c for c in changed if any(x <= c <= y for x, y in rs)}
            if hit: score[l] = score.get(l, 0) + len(hit); owned |= hit
        if changed - owned: other = True
    return score, other

def auto_lang(g, base, head):
    """the language that owns most of the changed lines"""
    score, _ = line_scores(g, base, head)
    score.pop('enUS', None)
    return max(score, key=score.get) if score and max(score.values()) > 0 else None

def detect(g, base, head):
    """the language of a translation PR, or None for anything else (a feature
    PR changes English or several languages; a code PR changes none)"""
    score, _ = line_scores(g, base, head)
    if score.get('enUS'): return None
    langs = [l for l, n in score.items() if n and l != 'enUS']
    return langs[0] if len(langs) == 1 else None

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='.'); ap.add_argument('--base', required=True); ap.add_argument('--head', required=True)
    ap.add_argument('--lang', required=True); ap.add_argument('--main'); ap.add_argument('--md')
    ap.add_argument('--author', help='GitHub login of the PR author')
    ap.add_argument('--lang-out', help='write the language that was checked to this file')
    ap.add_argument('--detect', action='store_true', help='only print the language if this is a one-language PR')
    a = ap.parse_args()
    g = L.Git(a.repo)
    if a.detect:
        print(detect(g, a.base, a.head) or ''); sys.exit(0)
    lang = auto_lang(g, a.base, a.head) if a.lang == 'auto' else a.lang
    if not lang:
        md = '### Translation scope\n\nNo language strings change in this PR.\n'
        if a.md: open(a.md, 'w').write(md)
        print(md); sys.exit(0)
    problems, notes, touched = check(g, a.base, a.head, lang, a.main or a.base, a.author)
    if a.lang_out: open(a.lang_out, 'w').write(lang + '\n')
    md = markdown(problems, notes, touched, lang)
    if a.md: open(a.md, 'w').write(md)
    print(md)
    sys.exit(0 if not problems or (len(problems) == 1 and NEW in problems[0]) else 1)
