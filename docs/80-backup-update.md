# 9. Backups and updates

## What's worth backing up

| Path | Why |
|---|---|
| `roster.json` | Which agents exist |
| `agents/<id>/home/SOUL.md` | Each agent's identity and job — you wrote these |
| `agents/<id>/home/state.db` | Conversations, tool calls, costs |
| `agents/<id>/home/a2a_audit.jsonl` | The agent-to-agent record |
| `agents/<id>/home/memory/`, `cron/` | What they remember; their routines |
| `agents/<id>/service.env` | HERMES_HOME + A2A tokens |
| `shared/skills/` | Every skill you've taught and approved |
| `shared/secrets.env`, `shared/a2a-tokens.json` | Keys and edge tokens |
| `agents/<id>/.migrate-hermes.json`, `agents/<id>/sync-backups/` | Migration stamp + pre-refresh copies for an agent migrated from another machine (docs/70) |

Not worth backing up: `dashboard/` and `app/` (rebuild from git), and Docker
images.

One honest caveat: the archive tars each agent's `state.db` in place, so a
busy agent's copy can be mid-write. For a guaranteed-consistent copy, pause
the agent first — `migrate-hermes.sh` snapshots its *source* via
`sqlite3 ".backup"` for exactly this reason.

## Back up

```bash
./scripts/backup.sh
```

Writes an encrypted archive to `/opt/recons/backups`. It uses `age` when
`RECIPIENT` is set, otherwise `gpg` symmetric, and warns loudly if neither is
installed rather than quietly writing plaintext. **Transcripts are business
data** — keep them encrypted.

With an age key:

```bash
RECIPIENT=age1... ./scripts/backup.sh
```

Nightly at 03:00 via cron:

```cron
0 3 * * * RECIPIENT=age1... /opt/recons/app/scripts/backup.sh >> /var/log/recons-backup.log 2>&1
```

Copy them off the VPS periodically — a backup that only exists on the machine
you're protecting isn't one.

## Restore

Default is a **dry restore** into a scratch directory so you can look before
you leap:

```bash
./scripts/restore.sh /opt/recons/backups/recons-<stamp>.tar.gz.age
diff -r --brief ./restore-<stamp> /opt/recons | head
```

When you're sure:

```bash
./scripts/restore.sh <archive> --in-place
```

That stops the agents and the orchestrator, restores, fixes the 600 permissions,
and restarts the orchestrator.

**Do a restore drill before you need one.** A backup you've never restored is a
hypothesis.

## Update

```bash
./scripts/update.sh
```

In order: backup → `hermes update` + `hermes doctor` → `git pull` (+ re-sync of
`/opt/recons/app` and `uv sync` when the checkout lives elsewhere) → rebuild the
dashboard and deploy it to where it is served → restart services → `vps-verify.sh`.
It refuses to continue if the backup fails, and it fails loudly if the
orchestrator does not restart — an "update" that keeps serving the old code is
worse than no update.

Run it as the operator account or as root — either works. It restarts units in
the systemd scope your install recorded at bootstrap
(`/opt/recons/systemd-scope`), reaching the owning user's manager when run as
root, and prints the scope it used in step 5.

Updating Hermes promptly is a **security control**, not housekeeping — see
[60-security-hardening.md](60-security-hardening.md).

## After every Hermes update

1. Read `hermes doctor` output.
2. Re-check the `VERIFY` points, since config keys can drift:
   ```bash
   grep -rn "VERIFY" config/ apps/orchestrator/recons_orchestrator/
   ```
3. Confirm each agent still starts:
   ```bash
   systemctl --user list-units 'hermes-gateway@*'   # user-scope installs
   systemctl list-units 'hermes-gateway@*'          # system-scope installs
   ```
4. Run `./scripts/vps-verify.sh` — especially the approvals, sandbox and
   loopback-bind assertions, in case a new default changed underneath you.
5. Send one message to one agent and check it lands in the Audit log.

## Disk

Transcripts grow. Check with:

```bash
du -sh /opt/recons/agents/*/home
```

Export the audit log (`/api/audit/export.jsonl`) before pruning anything, and
prefer archiving a whole retired agent's directory over deleting rows from a
live `state.db`.
