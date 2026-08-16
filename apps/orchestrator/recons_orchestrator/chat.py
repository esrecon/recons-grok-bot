"""Chat backend — the bridge between the dashboard's chat pane and Hermes.

Sending a message runs the Hermes CLI non-interactively with HERMES_HOME
pointed at the agent's own home, so the reply comes from *that* agent: its
SOUL.md, its memory, its model tier. Output streams back to the browser as
SSE events as it is produced.

The exact CLI syntax is version-fragile (VERIFY), so the command is a
template, overridable without a code change:

    RECONS_CHAT_CMD='hermes -p {text}'     # default

`{text}` is substituted as a single argv element (never through a shell), so
message content cannot inject options or commands. If the command fails, the
error event carries the command actually run and the override hint — the
same honest-failure contract as provider sign-in.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .config import Settings
from .models import AgentRecord

DEFAULT_CHAT_CMD = "hermes -p {text}"


def ensure_shared_auth(agent_home: Path) -> None:
    """Link the default HERMES_HOME's auth store into an agent home.

    Subscription logins (Codex, Nous OAuth) live in the default home; agents
    share credentials by design. A symlink, not a copy, so refreshed tokens
    stay shared. Called at provision time AND before each chat turn, so agents
    created before this existed heal themselves.
    """
    default_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    source = default_home / "auth.json"  # VERIFY filename against Hermes
    target = agent_home / "auth.json"
    try:
        if source.is_file() and not target.exists() and agent_home.is_dir():
            target.symlink_to(source)
    except OSError:
        pass  # degrade to an unauthenticated agent, never a crash


def chat_command(text: str) -> list[str]:
    """Build argv from the template, substituting {text} as one argument."""
    template = os.environ.get("RECONS_CHAT_CMD", DEFAULT_CHAT_CMD)
    argv: list[str] = []
    substituted = False
    for part in shlex.split(template):
        if part == "{text}":
            argv.append(text)
            substituted = True
        else:
            argv.append(part)
    if not substituted:
        argv.append(text)
    return argv


def chat_timeout() -> float:
    try:
        return float(os.environ.get("RECONS_CHAT_TIMEOUT", "300"))
    except ValueError:
        return 300.0


class ChatBackend:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _agent_home(self, record: AgentRecord) -> Path:
        return Path(record.home) if record.home else self._s.home_dir(record.id)

    def stream(self, record: AgentRecord, text: str) -> Iterator[dict[str, Any]]:
        """Run one chat turn; yield ChatEvent dicts, ending with done/error."""
        argv = chat_command(text)
        agent_home = self._agent_home(record)
        if not record.imported:
            # Imported agents own their homes; we never write into those.
            ensure_shared_auth(agent_home)
        env = dict(os.environ)
        env["HERMES_HOME"] = str(agent_home)

        try:
            proc = subprocess.Popen(  # noqa: S603 - argv template is operator-configured
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                bufsize=1,
            )
        except FileNotFoundError:
            yield {
                "type": "error",
                "message": (
                    f"Could not run `{argv[0]}` — is the Hermes CLI installed and on "
                    "PATH for the orchestrator service? Set RECONS_CHAT_CMD if your "
                    "Hermes version uses a different command."
                ),
            }
            return
        except OSError as exc:
            yield {"type": "error", "message": str(exc)}
            return

        assert proc.stdout is not None and proc.stderr is not None
        emitted = False
        try:
            # Line-at-a-time keeps memory bounded and the UI visibly alive.
            for line in proc.stdout:
                emitted = True
                yield {"type": "token", "text": line}
            code = proc.wait(timeout=chat_timeout())
            if code != 0:
                stderr_tail = proc.stderr.read()[-2000:].strip()
                yield {
                    "type": "error",
                    "message": (
                        f"`{' '.join(argv[:-1])} …` exited {code}"
                        + (f": {stderr_tail}" if stderr_tail else "")
                        + " — adjust RECONS_CHAT_CMD if the syntax differs in your Hermes version."
                    ),
                }
                return
            if not emitted:
                yield {
                    "type": "token",
                    "text": "(the agent returned no output)",
                }
            yield {"type": "done"}
        except subprocess.TimeoutExpired:
            proc.kill()
            yield {
                "type": "error",
                "message": (
                    f"The agent did not finish within {int(chat_timeout())}s. "
                    "Raise RECONS_CHAT_TIMEOUT if long tasks are expected."
                ),
            }
        finally:
            if proc.poll() is None:
                # Client went away mid-turn — do not leave a headless hermes running.
                proc.kill()
