#!/usr/bin/env python3
"""Validate a SKILL.md before it goes into the shared library.

Deterministic checks that run before a human approves a taught skill:
  * AgentSkills frontmatter present and parseable, with name + description
  * a body with actual steps (not just frontmatter)
  * the mandatory guardrail block — never take irreversible actions unasked
  * no literal secrets baked into the steps

Usage:
    validate_skill.py path/to/SKILL.md [more...]
    validate_skill.py --self-test      # used by scripts/check-all.sh
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - yaml ships with the orchestrator venv
    yaml = None

SECRET_RE = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}|sk-proj-[A-Za-z0-9_-]{8,}|sk-oat[A-Za-z0-9_-]{8,}|AKIA[0-9A-Z]{16}"
)

# A taught skill must carry an explicit stop-and-ask rule. Recording a workflow
# that once included "click Send" must not become an agent that always sends.
GUARDRAIL_HINTS = (
    "ask before",
    "never submit",
    "do not submit",
    "require approval",
    "stop and ask",
    "confirm with",
)


def split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end == -1:
        return None, text
    return text[3:end].strip(), text[end + 4 :]


def validate(path: Path) -> list[str]:
    problems: list[str] = []
    if not path.exists():
        return [f"{path}: file not found"]

    text = path.read_text("utf-8")
    fm_raw, body = split_frontmatter(text)
    if fm_raw is None:
        return [f"{path}: missing YAML frontmatter (--- fenced block at the top)"]

    if yaml is None:
        problems.append(f"{path}: pyyaml unavailable, frontmatter not parsed")
        fm = {}
    else:
        try:
            parsed = yaml.safe_load(fm_raw)
            fm = parsed if isinstance(parsed, dict) else {}
        except yaml.YAMLError as exc:
            return [f"{path}: frontmatter is not valid YAML ({exc})"]

    for field in ("name", "description"):
        if not str(fm.get(field, "")).strip():
            problems.append(f"{path}: frontmatter is missing required '{field}'")

    desc = str(fm.get("description", ""))
    if desc and len(desc) < 15:
        problems.append(
            f"{path}: description is too short to help an agent decide when to use the skill"
        )

    if len(body.strip()) < 40:
        problems.append(f"{path}: body has no usable steps")

    low = text.lower()
    if not any(h in low for h in GUARDRAIL_HINTS):
        problems.append(
            f"{path}: missing a guardrail line — a taught skill must say when to stop "
            f"and ask (e.g. 'Ask before sending, paying, or deleting anything.')"
        )

    if SECRET_RE.search(text):
        problems.append(f"{path}: contains a literal credential — use a stored secret instead")

    return problems


GOOD = """---
name: Invoice Chase
description: Chase an overdue invoice by finding it in the portal and drafting a polite reminder.
version: 1.0.0
---

# Invoice Chase

1. Open the accounts portal and search for the invoice number.
2. Read the due date and the amount outstanding.
3. Draft a short, polite reminder email to the billing contact.

## Guardrails

Ask before sending anything. Never submit payment or credit-note forms without
explicit approval.
"""

BAD_NO_GUARDRAIL = """---
name: Send Blast
description: Sends the outreach email to everyone on the supplier list immediately.
---

# Send Blast

1. Open the list.
2. Click Send on every row.
"""

BAD_NO_FRONTMATTER = "# Just a heading\n\nSteps go here but there is no frontmatter block.\n"


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        good = d / "good.md"
        good.write_text(GOOD)
        assert validate(good) == [], validate(good)

        bad1 = d / "bad1.md"
        bad1.write_text(BAD_NO_GUARDRAIL)
        assert any("guardrail" in p for p in validate(bad1))

        bad2 = d / "bad2.md"
        bad2.write_text(BAD_NO_FRONTMATTER)
        assert any("frontmatter" in p for p in validate(bad2))

        missing = d / "nope.md"
        assert validate(missing)
    print("validate_skill self-test: ok")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    if not argv:
        print(__doc__)
        return 2

    all_problems: list[str] = []
    for arg in argv:
        path = Path(arg)
        if path.is_dir():
            path = path / "SKILL.md"
        all_problems += validate(path)

    if all_problems:
        print("skill validation FAILED:")
        for p in all_problems:
            print(f"  ✗ {p}")
        return 1
    print("skill validation: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
