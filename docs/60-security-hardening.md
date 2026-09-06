# 7. Security hardening

These aren't generic best practices — each one answers a specific, documented
thing that has gone wrong with this class of software.

Run the whole list as an audit after install, after any config change, and after
every Hermes update:

```bash
./scripts/vps-verify.sh
```

## Why this is not paranoia

- **40,214 OpenClaw instances** were found exposed on the public internet
  (SecurityScorecard, Feb 2026); ~12,800 were exploitable for remote code
  execution.
- **341 malicious skills** were found in a community skill hub, delivering
  infostealer malware (Koi Security).
- **Hermes has had real 2026 CVEs**: RCE in the memory tool (≤ 2026.4.30, public
  proof-of-concept), unauthenticated prompt-pipeline injection, and a session
  authorization bypass (≤ 0.12.0). All patched in current releases.

Your install can read email, drive logged-in browser sessions, and run commands
on your PC. Treat it accordingly.

## The checklist

### 1. Nothing on the public internet
Every service binds `127.0.0.1` and is published only via `tailscale serve`.
Never `tailscale funnel`. `vps-verify.sh` fails if any non-loopback socket is
listening or funnel is on.
*Why: the 40,000 exposed instances above.*

### 2. Keep Hermes current — this is a control, not hygiene
Pin **≥ v0.20.1**. Run `./scripts/update.sh` regularly (it backs up first, then
updates, then re-verifies), and `hermes doctor` for upstream advisories.
*Why: the CVEs above are fixed in current releases. Staying behind is the
vulnerability.*

### 3. Firewall default-deny
`ufw` default deny inbound; SSH (key-only) and `tailscale0` allowed. Set by
`vps-bootstrap.sh`.

### 4. Approvals stay on
`approvals.mode: smart` in every agent config. **Never** enable YOLO mode
(`--yolo`, `/yolo`, `HERMES_YOLO_MODE=1`). Approval prompts reach you in the
dashboard and on your phone. `validate-configs.py` fails the build if approvals
are off or YOLO appears anywhere.

### 5. Sandbox tool execution
`terminal.backend: docker` on the VPS, with `--cap-drop ALL`, no-new-privileges
and resource limits. The local backend has no isolation.
*Note: the PC ("Deck") runs commands for real by design — that's what it's for.
Its protection is approvals plus the per-caller A2A token.*

### 6. Per-edge A2A tokens
Every directed pair has its own bearer token, in `service.env` (chmod 600),
never in `config.yaml`. Revoke one edge without touching the rest.

### 7. Skills never self-install
`skills.write_approval: true` and `guard_agent_created: true`. Agent-written
skills wait in the dashboard's approval queue. Only install third-party skills
you've read; run `hermes skills audit`.
*Why: the 341 malicious skills.*

### 8. Messaging gateways are allowlisted
If you enable Telegram or similar: per-platform allowlists and DM pairing.
**Never** `GATEWAY_ALLOW_ALL_USERS=true` — `validate-configs.py` fails on it.

### 9. Secrets are 600 and server-side
`shared/secrets.env`, `shared/a2a-tokens.json`, every `service.env`: mode 600.
Provider keys never reach the browser — the orchestrator holds them. The
dashboard's **Settings** tab can *set, replace or remove* a key (written by the
orchestrator into `secrets.env`, mode preserved) and shows only
configured / not configured + who changed it when. There is no "reveal";
values never appear in API responses, audit rows, exports or error messages,
and the test-suite asserts exactly that.

### 10. Backups are encrypted
Transcripts contain your business data. `backup.sh` encrypts with `age` or `gpg`
and refuses to pretend otherwise.

### 11. One dedicated browser profile
The Chrome profile the agents drive should contain **only** the logins they need
— not your everyday browsing.

### 12. A separate Claude load-bearer
Don't drive the same Claude login from both Buzz and this stack simultaneously
(see [40-providers-and-tos.md](40-providers-and-tos.md)).

### 13. Operator login is on, and locked-by-default
Every `/api` route except the health check, the HMAC-verified webhook receiver
and the auth endpoints needs an operator session. With no
`RECONS_OPERATOR_PASSWORD_HASH` the API is **locked**, not open. The quickstart
sets it (`set-operator`, [10-vps.md](10-vps.md) §3b); `vps-verify.sh` fails if
it's missing.
*Why: a tailnet decides which devices can connect; it says nothing about who is
holding the phone.*

### 14. Sessions, CSRF and headers
Signed `HttpOnly` / `Secure` / `SameSite=Strict` cookie with a 12-hour TTL; a
CSRF token on every state-changing request; cross-site requests refused; login
and API rate-limited; `X-Frame-Options: DENY` and a `'self'`-only CSP on every
response. Rotate `RECONS_SESSION_SECRET` to sign everyone out at once.

### 15. Everything the operator does is in the same ledger
Sign-ins (including failures), credential changes, skill approvals, routine
edits, agent pause/resume/remove and approval decisions land in
`audit/operator.jsonl` and show in the Audit tab under **Operator actions**.

### 16. A public hostname is a separate decision
The app is built so it *could* sit behind an OIDC proxy (Cloudflare Access) via
an outbound-only tunnel to loopback — see
[65-public-endpoint-foundation.md](65-public-endpoint-foundation.md). Nothing
in this repo does that for you, and there is no configuration that exposes the
dashboard without authentication.

## Prompt injection: assume it will happen

An agent that reads a web page, an email, or an A2A message is reading text a
stranger may have written. Hermes filters inbound A2A content and blocks peers
from invoking operator commands, but no filter is complete. The real controls are
the ones above: approvals for anything that sends, pays, deletes or publishes;
sandboxed execution; least-privilege browser profile; and an audit log you
actually read.

If an agent ever does something you didn't expect, the **Audit log** is where you
find out what it read just before it did.

## Incident checklist

1. Pause the agent in the dashboard (or `systemctl --user stop hermes-gateway@<id>`).
2. Read the Audit log around the event; export it (`/api/audit/export.jsonl`).
3. Rotate what was exposed: provider keys (Settings tab or `secrets.env`), the
   affected A2A tokens, the webhook secret. Rotate `RECONS_SESSION_SECRET` to
   sign every browser out; change the operator password with `hash-password`.
4. If a skill was involved, remove it from `shared/skills/` and check the
   pending queue.
5. Restore from backup if state is suspect (`restore.sh`, not in-place first).
6. Update Hermes, re-run `vps-verify.sh`, then resume.
