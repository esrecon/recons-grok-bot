# Plan: the dashboard as Hermes' control centre

Status: implementation plan for this change set. Deployment to
`shell.essexrecons.com` is **not** part of it — see "What this change does not
do" at the end.

## Goal

Finish the Recons dashboard so that it is a complete, phone-friendly visual
control centre for the Hermes agents, and so that it *could later* be placed
behind a public, OIDC-protected endpoint without weakening today's
Tailscale-only posture. Everything here is application code, tests and docs.

## Where the code stood

| Area | Found | Gap |
|---|---|---|
| Orchestrator (`apps/orchestrator`) | Agents, skills, routines, audit, signed webhooks. Every endpoint open. No SPA serving despite the systemd unit pointing at `RECONS_DASHBOARD_DIST`. | No login, sessions, CSRF, rate limit, headers; no chat proxy; no credentials API; no skill content view; no session history API. |
| Dashboard (`apps/dashboard`) | Roster, chat (mock only), Skills, Routines, Audit; PWA; phone layout. | Settings is a placeholder; no login; no sessions view; skills can't be inspected; API client has no CSRF/auth handling. |
| Docs / config | Tailscale-only design; secrets file reserves `RECONS_OPERATOR_*` variables that nothing reads. | No operator-login runbook; no public-endpoint foundation; verify script doesn't check auth settings. |

## Architecture of the change

```
browser ──(cookie: recons_session, header: X-CSRF-Token)──▶ orchestrator (loopback :8330)
                                                            ├─ SecurityMiddleware
                                                            │    · security headers on every response
                                                            │    · /api/* requires operator session
                                                            │      (except /api/health, /api/hooks, /api/auth/*)
                                                            │    · Origin / Sec-Fetch-Site check + CSRF token on
                                                            │      POST/PUT/PATCH/DELETE
                                                            │    · rate limits (login + general)
                                                            ├─ /api/auth/{session,login,logout}
                                                            ├─ /api/settings/{credentials,providers,security}
                                                            ├─ /api/skills/... (+ detail/file inspection)
                                                            ├─ /api/sessions (history from the ledger)
                                                            ├─ /api/agents/{id}/messages (SSE chat proxy)
                                                            ├─ /api/audit (now includes source=operator)
                                                            └─ /  static SPA with index.html fallback
```

### Operator authentication

* `security.py` — `Authenticator` interface with two implementations:
  * **password** (default): `RECONS_OPERATOR_USER` + `RECONS_OPERATOR_PASSWORD_HASH`
    from the environment (the systemd unit already loads `shared/secrets.env`).
    Hashes are scrypt in a PHC-style string, generated on the server with
    `uv run python -m recons_orchestrator.security hash-password`. No plaintext
    password is ever stored.
  * **proxy**: for a future reverse-proxy/OIDC layer (Cloudflare Access or
    similar). Trusts an identity header **only** when the request also carries
    a configured shared secret header (`RECONS_PROXY_SECRET`) and the identity
    is in `RECONS_OPERATOR_EMAILS`. No Cloudflare credentials are assumed or
    configured by this change.
* Sessions: `itsdangerous` signed cookie (`recons_session`), `HttpOnly`,
  `SameSite=Strict`, `Secure` by default, absolute TTL
  (`RECONS_SESSION_TTL_SECONDS`, default 12h). Signed with
  `RECONS_SESSION_SECRET`; if unset a random secret is generated at start
  (sessions then die with the process) and a warning is logged.
* CSRF: every session carries a random token, exposed via
  `GET /api/auth/session`; state-changing requests must send it in
  `X-CSRF-Token`. Plus an `Origin`/`Sec-Fetch-Site` same-origin check. The
  login endpoint requires a JSON body (no simple-form CSRF) and is rate limited.
* If no operator credential is configured the API is **locked** (401 with a
  clear reason), never open.
* Rate limiting: in-memory sliding windows — login attempts per client and
  globally; general per-client API limit. Headers: `X-Content-Type-Options`,
  `X-Frame-Options`, CSP, `Referrer-Policy`, `Permissions-Policy`,
  `Cache-Control: no-store` on `/api`, optional HSTS (`RECONS_HSTS=1`).
* Loopback binding stays the default; nothing here changes how the process is
  bound or published.

### Credentials (Settings)

* `credentials.py` — `CredentialStore` protocol (`status(key)`, `set(key, value,
  actor)`, `remove(key, actor)`) and `EnvFileCredentialStore` over
  `shared/secrets.env` (atomic write, mode 600, comments and order preserved).
  A sidecar `shared/secrets.meta.json` records *when* and *by whom* a key
  changed — never the value.
* A static **provider catalogue** (Claude wrapper, Anthropic API, OpenAI API,
  Nous Portal, Telegram gateway, orchestrator webhook secret) is the only set
  of keys the API will read or write. Auth-related keys are visible as
  configured/not configured but are not writable through the API.
* Responses carry `configured`, `last_updated`, `updated_by` and a health
  probe for local services (the Claude wrapper) — never a value, never a
  prefix, never a length. There is no reveal endpoint.
* Every change appends `{ts, actor, key, provider, action, result}` to
  `audit/operator.jsonl` (source `operator`, kind `credential`), which the
  ledger merges and the Audit tab shows. Tests assert the secret string is
  absent from every response, audit file and export.

### Visual sessions, chat and logs

* `GET /api/sessions` and `GET /api/sessions/{agent}/{id}` group the ledger's
  `state.db` events into per-session transcripts — the history view.
* `POST /api/agents/{id}/messages` streams a live turn as SSE through a
  pluggable `ChatClient`. The production adapter talks A2A to the agent's
  loopback port with an orchestrator edge token (the mesh now mints
  `orchestrator->agent` tokens into each agent's `A2A_PEER_TOKENS`). Exact
  A2A event shapes are `VERIFY` points; tests use a fake client.
* `POST /api/agents/{id}/approvals/{approval_id}` records the operator's
  decision and forwards it through the same client.
* Operator actions (login/logout, skill approve/reject, routine and agent
  changes, credential changes) become `operator` events in the ledger; the
  Audit tab gains a source filter so "operational logs" are one click away.

### Skills

* `GET /api/skills/shared/{slug}` and `/api/skills/pending/{agent}/{slug}`
  return frontmatter, file list, the `SKILL.md` body and review warnings
  (no guardrails section, secret-shaped strings, scripts present). `.../file`
  returns one text file. Slugs and paths are validated and resolved inside the
  skill folder; nothing executes. Approve/reject stay as they are — nothing
  self-installs.

### Dashboard

* `api.ts` becomes session-aware: fetches the CSRF token, sends it on
  mutations, and surfaces 401 as a global "signed out" state.
* New `LoginView`; `App` gates every surface behind it.
* Real `SettingsView`: operator + sign out, providers with status pills,
  set/replace/remove with a write-only input (`type=password`,
  `autocomplete=off`, cleared after save), agent service status, security
  posture panel.
* `SkillsView` detail drawer (plain-text body, file list, warnings).
* New `SessionsView` (agent → session → transcript using the existing bubbles).
* `AuditView` source chips.
* Mock server mirrors the new contract so `npm run dev` and Playwright work;
  e2e adds login, settings (value never echoed), skill detail and sessions.

## Order of work (TDD: failing test → minimal code → run)

1. Plan (this file).
2. Security layer + SPA serving.
3. Operator audit + credentials store + settings API.
4. Skills inspection, sessions API, chat proxy, service status.
5. Dashboard auth + API client.
6. Dashboard views.
7. Mock server + e2e.
8. Docs, secrets example, verify script, README.
9. `./scripts/check-all.sh` green; push; draft PR.

## Security invariants kept

Approvals on, no YOLO, server-side secrets only, no raw key display, no
automatic installation of agent-created skills, loopback binding, Tailscale
`serve` never `funnel`. `validate-configs.py` and `vps-verify.sh` continue to
enforce the config side; the new tests enforce the API side.

## What this change does not do

It does not deploy anything, buy anything, change DNS, open ports, create
Cloudflare resources or touch production. `docs/65-public-endpoint-foundation.md`
describes what a future `shell.essexrecons.com` deployment would need and
ends in an explicit **manual approval** step. There are no instructions
anywhere that expose the dashboard without authentication.
