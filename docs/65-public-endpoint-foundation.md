# 8. Public endpoint foundation — `shell.essexrecons.com`

**Status: design and application support only. Nothing in this repo deploys,
buys, configures DNS, opens a port or creates a Cloudflare resource.** Going
public is a separate, manual decision — see the approval step at the end.

Today the dashboard is reachable only over your tailnet
([15-tailscale.md](15-tailscale.md)). That stays the default and the
recommendation. This page describes what the application already enforces so
that, *if* you later decide to put it on a public hostname, the only new
pieces are outside the app — and none of them bypass authentication.

## What the orchestrator enforces on its own

These hold regardless of how the process is reached, and are covered by the
test-suite (`apps/orchestrator/tests/test_security.py`):

| Control | Behaviour |
|---|---|
| Operator login | Every `/api` route except `/api/health`, the HMAC-verified `/api/hooks`, and the two auth endpoints returns **401** without an operator session. No operator configured = **locked**, never open. |
| Password mode (default) | `RECONS_OPERATOR_USER` + `RECONS_OPERATOR_PASSWORD_HASH` (scrypt, generated on the server). Login body must be JSON; wrong passwords and lockouts are audited. |
| Proxy mode | For an OIDC/reverse-proxy layer. Identity header is trusted **only** with a shared secret header (`RECONS_PROXY_SECRET`) **and** an allow-list (`RECONS_OPERATOR_EMAILS`). Missing either → 401. |
| Session cookie | `recons_session`: signed, `HttpOnly`, `Secure`, `SameSite=Strict`, absolute TTL (12 h default). Rotating `RECONS_SESSION_SECRET` signs everyone out. |
| CSRF | Every POST/PUT/PATCH/DELETE needs the session's token in `X-CSRF-Token`; `Sec-Fetch-Site: cross-site` and a foreign `Origin` are refused outright. |
| Rate limits | Login: 10/min per client (and a global cap). API: 600/min per session. |
| Headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, a `'self'`-only CSP, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store` on the API, optional HSTS. |
| Secrets | Written server-side into `shared/secrets.env` (mode 600). No endpoint returns a value, a prefix or a length; audit rows carry only actor/key/action/result. |
| Binding | Loopback only. The unit file binds `127.0.0.1:8330`; nothing here changes that. |

## The shape a public endpoint would take

```
phone / laptop ──HTTPS──▶ Cloudflare Access (OIDC / one-time PIN, your identity only)
                              │  (Access policy: allow tony@essexrecons.com)
                              ▼
                         Cloudflare Tunnel  ── outbound-only connector on the VPS ──▶ 127.0.0.1:8330
                                                                                      orchestrator
```

* **No inbound port opens.** `cloudflared` dials out; `ufw` stays default-deny.
* **Two gates, not one.** Access authenticates the person; the orchestrator
  still requires the operator login (password mode) or a proxy-vouched
  identity (proxy mode). Losing one gate does not expose the app.
* Everything above the dotted line is Cloudflare configuration you would do by
  hand, with your own account, when you approve this. This repo assumes no
  Cloudflare credentials exist and contains none.

### Option A (recommended first): Access in front, password mode behind

Simplest and fully independent: keep `RECONS_AUTH_MODE=password`. You sign in
to Access, then to the dashboard. Nothing else changes in the app.

### Option B: proxy mode (single sign-on)

Use when you want Access to be the only prompt. It requires that **only** the
proxy can reach the loopback port with a header you control:

1. In Cloudflare, add a *Request Header Transform* rule on the hostname that
   sets `X-Recons-Proxy-Secret` to a value you generate with
   `uv run python -m recons_orchestrator.security proxy-secret`.
2. Set in `shared/secrets.env` (chmod 600):

   ```
   RECONS_AUTH_MODE=proxy
   RECONS_PROXY_SECRET=<that value>
   RECONS_OPERATOR_EMAILS=tony@essexrecons.com
   # default identity header is Cf-Access-Authenticated-User-Email
   ```

3. Restart the orchestrator. A request with the identity header but the wrong
   or missing secret is refused; so is any identity not in the list.

Proxy mode still issues the session cookie and still enforces CSRF.

## Environment for a public hostname

```
RECONS_COOKIE_SECURE=1                       # already the default
RECONS_HSTS=1                                # only behind real TLS
RECONS_ALLOWED_ORIGINS=https://shell.essexrecons.com
RECONS_CLIENT_IP_HEADER=CF-Connecting-IP     # so rate limits see real clients
RECONS_SESSION_SECRET=<uv run python -m recons_orchestrator.security session-secret>
```

## What must never be done

* `tailscale funnel` — public without any of the above.
* Binding uvicorn to `0.0.0.0`, or allowing 8330 through `ufw`.
* Running with no operator credential and expecting it to "just work" — it
  is locked by design.
* Proxy mode without a proxy secret, or with an empty allow-list — refused by
  design.
* Any reverse-proxy config that strips `X-CSRF-Token`, rewrites `Origin`, or
  caches `/api`.

## Pre-flight verification (before you would flip DNS)

```bash
./scripts/check-all.sh        # tests, build, config validation, secret scan
./scripts/vps-verify.sh       # loopback only, funnel off, operator hash + session secret set

# From the VPS itself: the gate is on.
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8330/api/agents      # 401
curl -s -I http://127.0.0.1:8330/api/health | grep -i content-security-policy  # present
```

And the acceptance list in [99-acceptance-checklist.md](99-acceptance-checklist.md)
section **M**.

## Manual approval step

Do **not** create the tunnel, the Access application or the DNS record until
every line below is ticked by you, on the day:

- [ ] Tailnet-only access has been working for at least a week with the
      operator login on, and the Audit log shows only your own sign-ins.
- [ ] `vps-verify.sh` passes and `RECONS_SESSION_SECRET` is set (not ephemeral).
- [ ] You have decided between Option A and Option B and set the environment
      accordingly, restarted, and re-run the curl checks above.
- [ ] Backups are current and encrypted ([80-backup-update.md](80-backup-update.md)).
- [ ] You accept that a public hostname is a permanent target: keep Hermes and
      the orchestrator updated ([60-security-hardening.md](60-security-hardening.md) §2).
- [ ] Costs, DNS ownership and the Cloudflare plan are agreed separately.

Only then follow Cloudflare's own documentation for a Tunnel + Access
application pointing at `http://127.0.0.1:8330`. Afterwards, the "phone on
mobile data" test in [30-phone.md](30-phone.md) changes meaning: it should show
the Access login page, never the dashboard.
