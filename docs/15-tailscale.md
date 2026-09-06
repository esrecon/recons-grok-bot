# 2. Tailscale — private access from anywhere

Everything binds to loopback. Tailscale is what makes the dashboard reachable
from your other PC and your phone **without putting anything on the public
internet**.

## Why `serve` and never `funnel`

- **`tailscale serve`** publishes a loopback service to *your tailnet only*, over
  real HTTPS with a valid certificate. Only your own devices can reach it.
- **`tailscale funnel`** publishes it to *the entire internet*. Never use it
  here. `vps-verify.sh` fails if funnel is on.

Real HTTPS matters beyond privacy: the PWA needs a secure context to be
installable on Android, and `serve` gives you that without managing certificates.

## Set it up

On the VPS, with Tailscale connected:

```bash
# Dashboard (and its API) at https://<magicdns-name>/
sudo tailscale serve --bg --https=443 http://127.0.0.1:8330
```

Check it:

```bash
tailscale serve status
tailscale status          # shows your MagicDNS name, e.g. recons-vps.tailXXXX.ts.net
```

Optionally expose the Hermes admin dashboard as a fallback surface on a path:

```bash
# VERIFY the current --set-path syntax for your Tailscale version
sudo tailscale serve --bg --https=443 --set-path /hermes http://127.0.0.1:9119
```

> **VERIFY**: Hermes has no built-in Tailscale integration (upstream feature
> request open), and no built-in TLS — `tailscale serve` is deliberately doing
> that job. Flags have shifted between Tailscale releases; if `--bg` or
> `--set-path` is rejected, check `tailscale serve --help`.

## Lock the tailnet down

In the Tailscale admin console, restrict who can reach the VPS. A minimal ACL:

```json
{
  "acls": [
    {
      "action": "accept",
      "src": ["autogroup:owner"],
      "dst": ["tag:recons:443"]
    }
  ],
  "tagOwners": { "tag:recons": ["autogroup:owner"] }
}
```

Tag the VPS `tag:recons` and only your own devices can reach it — even if
another device joins the tailnet later.

## From another PC

Install Tailscale, sign in, and open `https://<magicdns-name>/`. That's it —
same dashboard, no port forwarding, no VPN config, no exposed ports.

You will be asked for the **operator login** ([10-vps.md](10-vps.md) §3b) even
on the tailnet. That is deliberate: the tailnet decides which *devices* can
reach the VPS, the login decides which *person* can drive the agents, and
neither depends on the other. A future public hostname would add a third gate
in front, never replace these ([65-public-endpoint-foundation.md](65-public-endpoint-foundation.md)).

## Install the app on your Android phone

1. Install **Tailscale** from the Play Store and sign in.
2. Open Chrome and go to `https://<magicdns-name>/`.
3. Chrome shows **Install app** (or menu → *Add to Home screen*).
4. Tap it. You now have a **Recons** icon on your home screen that opens
   full-screen, with no browser chrome — the same app your PC uses, live-synced
   because it talks to the same orchestrator.

The install prompt requires the manifest, the 192/512 icons and the service
worker — all shipped and asserted by the e2e suite (`npm run e2e`).

If the prompt doesn't appear: confirm the URL is `https://` (not an IP), that
Tailscale is connected, and reload once.

## Verify

```bash
./scripts/vps-verify.sh
```

Then the real test: **turn Wi-Fi off on your phone** so it's on mobile data with
Tailscale disconnected, and try the URL. It must fail. Reconnect Tailscale and it
must work.
