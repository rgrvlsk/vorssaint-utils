#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Vorssaint

# Keeps one tracking issue per maintained language up to date.
# A language is maintained when its main catalog carries a marker line:
#   // l10n-reviewed: <commit> @volunteer [@volunteer ...]
# DRY_RUN=1 prints what would happen instead of calling gh.
set -uo pipefail
TOOLS="${TOOLS:-Tools}"
REF="${REF:-HEAD}"
REPO="${GITHUB_REPOSITORY:-}"
LABEL="l10n-review"
OUT="$(mktemp -d)"
gh_() { if [[ "${DRY_RUN:-0}" == 1 ]]; then echo "DRY gh $*" | cut -c1-160; else gh "$@"; fi; }
failed=0

while IFS= read -r -d '' f; do
  f="${f#"$REF":}"
  src="$(git show "$REF:$f")"
  lang="$(grep -oE 'static let [A-Za-z]+ = Strings\(' <<<"$src" | head -1 | awk '{print $3}')"
  [[ "$lang" =~ ^[A-Za-z]+$ ]] || continue
  name="$(basename "$f" .swift)"; name="${name#Strings+}"
  [[ "$name" =~ ^[A-Za-z]+$ ]] || continue
  if ! python3 -I "$TOOLS/l10n_pending.py" --ref "$REF" --lang "$lang" ${REPO:+--gh-repo "$REPO"} \
       --md "$OUT/$lang.md" --json "$OUT/$lang.json" >/dev/null; then
    echo "$lang: the pending list could not be built"; failed=1; continue
  fi
  count="$(python3 -I -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["items"]))' "$OUT/$lang.json")"
  mentions="$(python3 -I -c 'import json,sys; print(" ".join("@"+v for v in json.load(open(sys.argv[1]))["volunteers"]))' "$OUT/$lang.json")"
  title="Translation review: $name"
  if [[ "${DRY_RUN:-0}" == 1 ]]; then existing="${EXISTING:-}"
  else existing="$(gh issue list --label "$LABEL" --state open --search "\"$title\" in:title" --json number,title \
                     --jq ".[] | select(.title == \"$title\") | .number" | head -1)"; fi
  { [[ -n "$mentions" ]] && printf '%s\n\n' "$mentions"; cat "$OUT/$lang.md"; } > "$OUT/$lang.body"
  if [[ "${DRY_RUN:-0}" == 1 && -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    { printf '## %s (dry run, %s pending)\n\n' "$title" "$count"; head -c 20000 "$OUT/$lang.body"; printf '\n\n'; } >> "$GITHUB_STEP_SUMMARY"
  fi
  if [[ "$count" == 0 ]]; then
    [[ -n "$existing" ]] && gh_ issue close "$existing" --comment "Nothing left to review."
    echo "$lang: up to date"
  elif [[ -n "$existing" ]]; then
    gh_ issue edit "$existing" --body-file "$OUT/$lang.body" || failed=1
    echo "$lang: $count pending, issue #$existing updated"
  else
    gh_ issue create --title "$title" --label "$LABEL" --body-file "$OUT/$lang.body" || failed=1
    echo "$lang: $count pending, issue created"
  fi
done < <(git grep -z -l -E '^//[[:space:]]*l10n-reviewed:' "$REF" -- 'Sources/Vorssaint/Core/Localizations/*.swift')
echo "bodies in $OUT"
exit "$failed"
