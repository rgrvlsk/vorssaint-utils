#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Vorssaint

"""Adversarial cases for l10n_scope.py, built as throwaway commits in a temporary
worktree of upstream/main. Prints one line per case and exits 1 if any case
does not get the expected outcome.

  python3 -I Tools/l10n_scope_cases.py [/path/to/clone]
"""
import os, re, subprocess, sys, tempfile, shutil
REPO = sys.argv[1] if len(sys.argv) > 1 else '.'
SCOPE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'l10n_scope.py')

def sh(*a, cwd=None, check=True):
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode: raise SystemExit(f'{a}: {r.stderr}')
    return r

W = tempfile.mkdtemp(prefix='scope-cases-')
os.rmdir(W)
sh('git', '-C', REPO, 'worktree', 'add', '-q', '--detach', W, 'upstream/main')
G = lambda *a, **k: sh('git', '-C', W, *a, **k)
ident = ['-c', 'user.name=case', '-c', 'user.email=case@local']
B = 'Sources/Vorssaint/Core/BatteryTimeStrings.swift'
SK = 'Sources/Vorssaint/Core/Localizations/Strings+Slovak.swift'
AU = 'Sources/Vorssaint/Core/AppUpdateStrings.swift'
try:
    main = G('rev-parse', 'HEAD').stdout.strip()
    old = G('log', '--reverse', '--format=%h', '-S', 'case sk = "', main, '--', 'Sources/Vorssaint/Core/Localization.swift').stdout.split()[0]
    M0 = f'// l10n-reviewed: {old} @rado9904'
    # base state: Slovak has a marker
    p = os.path.join(W, SK); ls = open(p).read().split('\n'); ls.insert(3, M0); open(p, 'w').write('\n'.join(ls))
    G(*ident, 'commit', '-qam', 'base: Slovak marker'); base = G('rev-parse', 'HEAD').stdout.strip()

    def val(s, lang, typ, field, new):
        i = s.index(f'static let {lang} = {typ}('); j = s.index(f'{field}: "', i) + len(field) + 3; k = s.index('"', j)
        return s[:j] + new + s[k:]
    def edit(path, fn):
        p = os.path.join(W, path)
        if fn is None: os.remove(p); return
        os.makedirs(os.path.dirname(p), exist_ok=True)
        s = open(p).read() if os.path.exists(p) else ''
        open(p, 'w').write(fn(s))
    short_main = main[:8]
    cases = [
        # name, edits, lang, author, expected exit, expected detect, text that must appear
        ('sk only, marker forward', [(B, lambda s: val(s, 'sk', 'BatteryTimeFeatureStrings', 'title', 'Zostávajúci čas na batérii')),
                                     (SK, lambda s: s.replace(M0, f'// l10n-reviewed: {short_main} @rado9904'))], 'auto', 'rado9904', 0, 'sk', 'over'),
        ('sk + its InfoPlist', [(B, lambda s: val(s, 'sk', 'BatteryTimeFeatureStrings', 'title', 'X')),
                                ('Resources/sk.lproj/InfoPlist.strings', lambda s: s.replace('";', '" ;', 1))], 'auto', None, 0, 'sk', ''),
        ('sk + uk', [(B, lambda s: val(s, 'sk', 'BatteryTimeFeatureStrings', 'title', 'X')),
                     (B, lambda s: val(s, 'uk', 'BatteryTimeFeatureStrings', 'title', 'Y'))], 'auto', None, 1, '', ''),
        ('sk + English', [(B, lambda s: val(s, 'sk', 'BatteryTimeFeatureStrings', 'title', 'X')),
                          (B, lambda s: s.replace('title: "Battery time remaining"', 'title: "Battery left"'))], 'auto', None, 1, '', ''),
        ('sk drops a field', [(B, lambda s: re.sub(r'(static let sk = BatteryTimeFeatureStrings\((?:\n.*?)*?),\n\s+calculating: "[^"]*"', r'\1', s, count=1))],
         'auto', None, 1, 'sk', 'reorders fields'),
        ('sk + code', [(B, lambda s: val(s, 'sk', 'BatteryTimeFeatureStrings', 'title', 'X')),
                       ('Sources/Vorssaint/Core/Localization.swift', lambda s: s.replace('enum CountAgreement {', 'enum CountAgreement { // x', 1))],
         'auto', None, 1, 'sk', 'outside'),
        ('interpolated code in a sk string', [(AU, lambda s: val(s, 'sk', 'AppUpdateStrings', 'skipVersionFormat',
                                                                'Preskočiť verziu %@\\({ () -> String in return "" }())'))], 'auto', None, 1, 'sk', 'interpolates'),
        ('sk format arguments changed', [(AU, lambda s: val(s, 'sk', 'AppUpdateStrings', 'skipVersionFormat', 'Preskočiť %d verziu %@ %@'))],
         'auto', None, 1, 'sk', 'format arguments'),
        ('new file whose name has spaces', [(f'{AU} Sources/Vorssaint/Core/AppearanceStrings.swift', lambda s: 'let x = 1\n')],
         'auto', None, 0, '', 'No language'),
        ('sk + new file whose name has spaces', [(B, lambda s: val(s, 'sk', 'BatteryTimeFeatureStrings', 'title', 'X')),
                                                 (f'{AU} Sources/Vorssaint/Core/AppearanceStrings.swift', lambda s: 'let x = 1\n')],
         'auto', None, 1, 'sk', 'added'),
        ('Slovak marker checked as ptBR', [(SK, lambda s: s.replace(M0, f'// l10n-reviewed: {short_main} @rado9904'))], 'ptBR', None, 1, 'sk', 'outside'),
        ('login with Markdown in it', [(SK, lambda s: s.replace(M0, f'// l10n-reviewed: {short_main} @vorssaint![x](https://evil.example/a.png)'))],
         'auto', None, 1, 'sk', 'malformed'),
        ('marker backwards', [(SK, lambda s: s.replace(M0, '// l10n-reviewed: ' + G('rev-parse', '--short', f'{old}~5').stdout.strip() + ' @rado9904'))],
         'auto', None, 1, 'sk', 'backwards'),
        ('marker not on main', [(SK, lambda s: s.replace(M0, '// l10n-reviewed: 1c5add51 @rado9904'))], 'auto', None, 1, 'sk', 'not on main'),
        ('moved by someone not listed', [(SK, lambda s: s.replace(M0, f'// l10n-reviewed: {short_main} @rado9904'))], 'auto', 'stranger', 0, 'sk', 'not listed'),
        ('second volunteer added', [(SK, lambda s: s.replace(M0, f'{M0} @second-volunteer'))], 'auto', 'second-volunteer', 0, 'sk', 'added: @second-volunteer'),
        ('a CHANGELOG line', [(B, lambda s: val(s, 'sk', 'BatteryTimeFeatureStrings', 'title', 'X')),
                              ('CHANGELOG.md', lambda s: s.replace('## [Unreleased]\n', '## [Unreleased]\n\n- Slovak fixes.\n', 1))], 'auto', None, 1, 'sk', 'CHANGELOG'),
        ('feature PR: English and every language', [(B, lambda s: re.sub(r'(title: ")([^"]*)(")', r'\1\2!\3', s))], 'auto', None, 1, '', ''),
    ]
    bad = 0
    for name, edits, lang, author, want, want_detect, must in cases:
        G('checkout', '-q', '--detach', base)
        for path, fn in edits: edit(path, fn)
        G('add', '-A'); G(*ident, 'commit', '-qm', name)
        det = sh('python3', '-I', SCOPE, '--repo', W, '--base', base, '--head', 'HEAD', '--lang', 'auto', '--detect').stdout.strip()
        args = ['python3', '-I', SCOPE, '--repo', W, '--base', base, '--head', 'HEAD', '--main', main, '--lang', lang]
        if author: args += ['--author', author]
        r = sh(*args, check=False)
        ok = r.returncode == want and det == want_detect and (must in r.stdout)
        bad += not ok
        head = next((l for l in r.stdout.split('\n') if l.startswith('###')), '').replace('### ', '')
        print(f"{'ok ' if ok else 'BAD'} {name:42} exit={r.returncode} detect={det or '-':3} {head[:40]}")
        if not ok: print('    ', r.stdout.strip().replace('\n', ' ')[:300], r.stderr.strip()[-200:])
    # a new language on today's main: informs, does not fail
    r = sh('python3', '-I', SCOPE, '--repo', REPO, '--base', 'upstream/main', '--head', 'refs/remotes/pr/3762', '--lang', 'auto', check=False)
    ok = r.returncode == 0 and 'new language' in r.stdout; bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {'new language (#3762)':42} exit={r.returncode}")
    sys.exit(1 if bad else 0)
finally:
    sh('git', '-C', REPO, 'worktree', 'remove', '--force', W, check=False)
    sh('git', '-C', REPO, 'worktree', 'prune', check=False)
