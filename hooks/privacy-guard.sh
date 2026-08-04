#!/usr/bin/env bash
# Privacy guard — blocks personal data and secrets from entering git history.
#
# Sourced by hooks/pre-commit (and hooks/pre-push as a second line of defence).
# Checks only STAGED content, so it stays fast.
#
# Rationale: .gitignore is not a guarantee. It does not protect against
# `git add -f`, against files added before the ignore rule existed, or against
# secrets pasted into source files. Once committed, removal requires rewriting
# history and force-pushing. This hook is the cheap stage to fail at.

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; RESET='\033[0m'

# ── 1. Filenames that must never be committed ────────────────────────────────
# Personal data, generated run outputs, credentials, office documents.
BLOCKED_FILES='(^|/)config\.toml$
(^|/)\.env$
\.(docx|xlsx|pptx|pdf|numbers|pages)$
\.(pkl|pickle|sqlite|db)$
(^|/)(credentials|token|client_secret|service[-_]account).*\.json$
(^|/)CLAUDE\.md$
(^|/)context/.*$
(^|/)personal.*\.(md|txt|json|toml)$'

# ── 2. Content patterns that must never appear in any staged file ────────────
# Credentials first, then personal-context markers.
declare -a SECRET_PATTERNS=(
  'sk-ant-[A-Za-z0-9_-]{20,}'          'Anthropic API key'
  'sk-[A-Za-z0-9]{32,}'                'OpenAI-style API key'
  'ghp_[A-Za-z0-9]{36}'                'GitHub personal access token'
  'AKIA[0-9A-Z]{16}'                   'AWS access key id'
  'AIza[0-9A-Za-z_-]{35}'              'Google API key'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----' 'private key block'
  '"(refresh_token|client_secret)"'    'OAuth credential'
)

privacy_guard() {
    local staged blocked
    staged="$(git diff --cached --name-only --diff-filter=ACMR)"
    [ -z "$staged" ] && return 0

    echo -e "${YELLOW}privacy-guard: scanning staged files…${RESET}"

    # --- filename check ---
    blocked=""
    while IFS= read -r pattern; do
        [ -z "$pattern" ] && continue
        local hit
        hit="$(echo "$staged" | grep -E -- "$pattern" || true)"
        [ -n "$hit" ] && blocked+="$hit"$'\n'
    done <<< "$BLOCKED_FILES"

    if [ -n "${blocked// /}" ]; then
        echo ""
        echo -e "${RED}COMMIT BLOCKED — never-commit file(s) staged:${RESET}"
        echo "$blocked" | sed '/^$/d' | sort -u | sed 's/^/    /'
        echo ""
        echo "  These hold personal data, generated output, or credentials."
        echo "  Unstage with:  git restore --staged <path>"
        return 1
    fi

    # --- content check (staged blob contents, not working tree) ---
    local i name regex found=0
    for ((i = 0; i < ${#SECRET_PATTERNS[@]}; i += 2)); do
        regex="${SECRET_PATTERNS[i]}"
        name="${SECRET_PATTERNS[i+1]}"
        while IFS= read -r file; do
            [ -z "$file" ] && continue
            # skip binary and example/template files
            case "$file" in *.example|*.example.*|*.parquet|*.png|*.jpg) continue;; esac
            if git show ":$file" 2>/dev/null | grep -Eq -- "$regex"; then
                [ $found -eq 0 ] && echo "" && echo -e "${RED}COMMIT BLOCKED — possible secret in staged content:${RESET}"
                echo "    $file  →  $name"
                found=1
            fi
        done <<< "$staged"
    done

    if [ $found -eq 1 ]; then
        echo ""
        echo "  Move the value to .env (gitignored) and commit only .env.example."
        return 1
    fi

    echo -e "${GREEN}privacy-guard: clean.${RESET}"
    return 0
}
