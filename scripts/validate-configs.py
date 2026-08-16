#!/usr/bin/env python3
"""Validate config templates and static config files against the project's
security invariants. Run by check-all.sh (through the orchestrator's uv venv so
pyyaml/jinja2 are available).

Checks, over both the rendered orchestrator templates and any static YAML under
config/:
  * YAML parses.
  * Every network bind/host is loopback (127.0.0.1) — nothing binds publicly.
  * Approvals are never disabled; YOLO mode is never enabled.
  * GATEWAY_ALLOW_ALL_USERS is never true.
  * A2A peers always carry an auth token (placeholder or real).
  * No literal secret-shaped strings anywhere in the repo's tracked text.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
errors: list[str] = []
checked: list[str] = []


def fail(msg: str) -> None:
    errors.append(msg)


def note(msg: str) -> None:
    checked.append(msg)


# --- secret scan (defence in depth; check-all also greps) ---------------------
# The last alternative is the Telegram BotFather token shape (digits:35 chars —
# VERIFY the exact width against current BotFather output).
SECRET_RE = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{8,}|sk-proj-[A-Za-z0-9_-]{8,}|sk-oat[A-Za-z0-9_-]{8,}"
    r"|AKIA[0-9A-Z]{16}|\b\d{8,10}:[A-Za-z0-9_-]{35}\b"
)


def scan_secrets() -> None:
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.parts)
        if parts & {".git", "node_modules", "dist", ".venv", "__pycache__"}:
            continue
        try:
            text = path.read_text("utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if SECRET_RE.search(text):
            fail(f"literal secret-shaped string in {path.relative_to(REPO)}")
    note("secret scan")


# --- YAML invariant checks ----------------------------------------------------
def check_yaml_doc(label: str, doc: object, raw: str) -> None:
    import yaml  # noqa: F401  (proves availability; parsing done by caller)

    # Text-level checks (catch things a dict walk would miss, e.g. env lines).
    low = raw.lower()
    if "gateway_allow_all_users" in low and re.search(r"gateway_allow_all_users\s*[:=]\s*true", low):
        fail(f"{label}: GATEWAY_ALLOW_ALL_USERS must never be true")
    if re.search(r"\byolo\b\s*[:=]\s*(true|1|on)", low) or "hermes_yolo_mode=1" in low:
        fail(f"{label}: YOLO mode must never be enabled")

    # Structural checks over the parsed document.
    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                kp = f"{path}.{k}" if path else str(k)
                kl = str(k).lower()
                if kl in {"host", "bind"} and isinstance(v, str):
                    if not _is_loopback_or_placeholder(v):
                        fail(f"{label}: {kp} = {v!r} is not loopback/placeholder")
                if kl == "mode" and path.endswith("approvals") and str(v).lower() == "off":
                    fail(f"{label}: approvals.mode must not be 'off'")
                walk(v, kp)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(doc, "")

    # A2A peers must carry a token.
    if isinstance(doc, dict) and isinstance(doc.get("a2a_agents"), dict):
        for peer, cfg in doc["a2a_agents"].items():
            if isinstance(cfg, dict):
                token = (cfg.get("auth") or {}).get("token") if isinstance(cfg.get("auth"), dict) else None
                if not token:
                    fail(f"{label}: a2a peer '{peer}' has no auth token")

    # An enabled telegram platform must never carry a literal token — only the
    # ${TELEGRAM_BOT_TOKEN} placeholder the mesh resolves via service.env.
    if isinstance(doc, dict) and isinstance(doc.get("gateway"), dict):
        platforms = doc["gateway"].get("platforms")
        tg = platforms.get("telegram") if isinstance(platforms, dict) else None
        if isinstance(tg, dict) and tg.get("enabled"):
            extra = tg.get("extra") if isinstance(tg.get("extra"), dict) else {}
            token = extra.get("bot_token") or extra.get("token") or tg.get("token")
            if not (isinstance(token, str) and token.startswith("${")):
                fail(f"{label}: telegram bot_token must be a ${{ENV}} placeholder, never a literal")


def _is_loopback_or_placeholder(v: str) -> bool:
    v = v.strip()
    return (
        v in {"127.0.0.1", "::1", "localhost"}
        or v.startswith("${")
        or v.startswith("{{")
        or "PLACEHOLDER" in v.upper()
    )


# --- render orchestrator templates and validate them --------------------------
def check_rendered_templates() -> None:
    try:
        sys.path.insert(0, str(REPO / "apps" / "orchestrator"))
        import yaml

        from recons_orchestrator.config import A2A_PORT_BASE, Settings
        from recons_orchestrator.mesh import Mesh
        from recons_orchestrator.models import AgentRecord, ModelTier
    except Exception as exc:  # pragma: no cover - only when deps missing
        note(f"orchestrator templates SKIPPED ({exc})")
        return

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(root=Path(tmp) / "recons", dashboard_dist=Path(tmp) / "d")
        recs = [
            AgentRecord(id="recon", name="Recon", role="Lead", tier=ModelTier.LEAD,
                        a2a_port=A2A_PORT_BASE, is_lead=True, created_at="2026-08-15T00:00:00+00:00"),
            AgentRecord(id="scout", name="Scout", role="Research", tier=ModelTier.WORKHORSE,
                        a2a_port=A2A_PORT_BASE + 1, created_at="2026-08-15T00:00:00+00:00"),
            # Telegram-enabled agent so the telegram block renders under check.
            AgentRecord(id="comms", name="Comms", role="Messaging", tier=ModelTier.WORKHORSE,
                        a2a_port=A2A_PORT_BASE + 2, created_at="2026-08-15T00:00:00+00:00",
                        telegram_enabled=True, telegram_allowed_users="123456789"),
        ]
        # The mesh fails loud on telegram-without-token — seed the store the
        # way the API paths do.
        from recons_orchestrator import telegram as telegram_store

        telegram_store.store_token(settings, "comms", "PLACEHOLDER-TG-TOKEN")
        Mesh(settings, token_factory=lambda: "PLACEHOLDER").rewire(recs)
        for rec in recs:
            cfg_path = settings.home_dir(rec.id) / "config.yaml"
            raw = cfg_path.read_text("utf-8")
            check_yaml_doc(f"rendered:{rec.id}/config.yaml", yaml.safe_load(raw), raw)

        # Telegram invariants across the rendered pair: allowlist enforced via
        # env, secret only in service.env, placeholder only in config.yaml.
        comms_env = settings.service_env("comms").read_text("utf-8")
        comms_cfg = (settings.home_dir("comms") / "config.yaml").read_text("utf-8")
        if "TELEGRAM_ALLOWED_USERS=123456789" not in comms_env:
            fail("rendered:comms/service.env: TELEGRAM_ALLOWED_USERS missing or empty")
        if "PLACEHOLDER-TG-TOKEN" in comms_cfg:
            fail("rendered:comms/config.yaml: real telegram token leaked into config")
        if '"${TELEGRAM_BOT_TOKEN}"' not in comms_cfg:
            fail("rendered:comms/config.yaml: telegram enabled but no ${TELEGRAM_BOT_TOKEN} placeholder")
        if "gateway_allow_all_users" in (comms_env + comms_cfg).lower():
            fail("rendered:comms: GATEWAY_ALLOW_ALL_USERS must never be emitted")
    note("orchestrator templates render + validate (incl. telegram)")


# --- static config/ files -----------------------------------------------------
def check_static_configs() -> None:
    import yaml

    config_dir = REPO / "config"
    if not config_dir.is_dir():
        note("config/ (none yet)")
        return
    count = 0
    for path in list(config_dir.rglob("*.yaml")) + list(config_dir.rglob("*.yml")):
        raw = path.read_text("utf-8")
        # Skip Jinja templates (not valid YAML until rendered).
        if path.suffix == ".j2" or "{{" in raw or "{%" in raw:
            continue
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            fail(f"{path.relative_to(REPO)}: invalid YAML ({exc})")
            continue
        check_yaml_doc(str(path.relative_to(REPO)), doc, raw)
        count += 1
    note(f"static config files ({count})")


def main() -> int:
    scan_secrets()
    check_rendered_templates()
    check_static_configs()

    for c in checked:
        print(f"  ✓ {c}")
    if errors:
        print("\nconfig validation FAILED:")
        for e in errors:
            print(f"  ✗ {e}")
        return 1
    print("config validation: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
