#!/usr/bin/env bash
# check-all.sh — everything CI runs, runnable locally too.
# Sections skip cleanly (with a notice) while a component doesn't exist yet,
# but any existing component that fails its checks fails the whole run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
FAILED=0

# The dashboard's e2e tests use the environment's preinstalled Chromium, so
# never let npm postinstall try to download a browser during CI installs.
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

section() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
skip()    { printf '   (skipped: %s)\n' "$1"; }

section "Shell scripts: bash -n"
while IFS= read -r -d '' f; do
  bash -n "$f" || FAILED=1
done < <(find scripts tools -name '*.sh' -print0 2>/dev/null)
echo "   ok"

section "Orchestrator tests"
if [ -f apps/orchestrator/pyproject.toml ]; then
  (cd apps/orchestrator && uv run --frozen pytest -q) || FAILED=1
else
  skip "apps/orchestrator not present yet"
fi

section "Config validation"
# Run through the orchestrator venv so pyyaml/jinja2 + the package are available
# and the real templates get rendered and checked.
if [ -f scripts/validate-configs.py ]; then
  if [ -f apps/orchestrator/pyproject.toml ]; then
    (cd apps/orchestrator && uv run --frozen python "$ROOT/scripts/validate-configs.py") || FAILED=1
  else
    python3 scripts/validate-configs.py || FAILED=1
  fi
else
  skip "scripts/validate-configs.py not present yet"
fi

section "Skill validator self-test"
if [ -f tools/teach-mode/validate_skill.py ]; then
  python3 tools/teach-mode/validate_skill.py --self-test || FAILED=1
else
  skip "tools/teach-mode not present yet"
fi

section "Dashboard build + tests"
if [ -f apps/dashboard/package.json ]; then
  (cd apps/dashboard \
    && npm ci --no-audit --no-fund \
    && npm run -s build \
    && npm run -s test --if-present) || FAILED=1
else
  skip "apps/dashboard not present yet"
fi

section "Dashboard e2e (Playwright, preinstalled Chromium)"
if [ -f apps/dashboard/playwright.config.ts ]; then
  if [ -x "${PW_CHROMIUM:-/opt/pw-browsers/chromium}" ]; then
    (cd apps/dashboard && npx playwright test) || FAILED=1
  else
    skip "no Chromium at ${PW_CHROMIUM:-/opt/pw-browsers/chromium}"
  fi
else
  skip "playwright config not present yet"
fi

section "Secret scan (literal keys must never be committed)"
if grep -rEn --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=dist \
    'sk-ant-[A-Za-z0-9_-]{8,}|sk-proj-[A-Za-z0-9_-]{8,}|sk-oat[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16}' . ; then
  echo "   FOUND literal secret-shaped strings above"
  FAILED=1
else
  echo "   ok"
fi

if [ "$FAILED" -ne 0 ]; then
  printf '\n\033[31mcheck-all: FAILED\033[0m\n'
  exit 1
fi
printf '\n\033[32mcheck-all: all checks passed\033[0m\n'
