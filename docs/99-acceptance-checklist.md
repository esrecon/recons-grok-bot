# 10. Acceptance checklist

Work down this list on your real hardware. Everything here was designed to be
provable — if a box won't tick, the runbook section is named beside it.

## A. The VPS is private

- [ ] `./scripts/vps-verify.sh` passes with no red lines *(10-vps)*
- [ ] `tailscale serve status` shows the dashboard; funnel is **off** *(15-tailscale)*
- [ ] From a device **not** on your tailnet, the URL fails to load *(15-tailscale)*
- [ ] From your other PC on the tailnet, the dashboard loads over HTTPS *(15-tailscale)*
- [ ] The dashboard asks for the operator login; a wrong password is refused and
      appears in the Audit log under Operator actions › auth *(10-vps §3b)*
- [ ] `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8330/api/agents` prints `401` *(60-security)*
- [ ] Agents survive a reboot: `sudo reboot`, then the roster comes back up *(10-vps)*

## B. Agents exist and are individuals

- [ ] One click creates an agent from a name + job; it appears in the roster *(10-vps)*
- [ ] `/opt/recons/agents/<id>/home/SOUL.md` contains that job *(50-agents-a2a)*
- [ ] Asking the agent "what's your job?" matches its SOUL.md *(50-agents-a2a)*
- [ ] Editing SOUL.md and restarting changes its behaviour; a later agent
      creation does **not** overwrite your edit *(50-agents-a2a)*
- [ ] Pause stops it, resume brings it back with history intact *(50-agents-a2a)*

## C. Shared skills and keys, private memory

- [ ] Every agent's `config.yaml` points at the same `shared/skills` dir *(50-agents-a2a)*
- [ ] One `shared/secrets.env` (chmod 600) serves all agents — no per-agent copies *(60-security)*
- [ ] Two agents do **not** see each other's conversation history *(50-agents-a2a)*

## D. Providers

- [ ] The lead agent answers on Claude via the wrapper *(40-providers)*
- [ ] Another agent answers on the ChatGPT subscription *(40-providers)*
- [ ] A bulk agent answers on Nous Portal *(40-providers)*
- [ ] **Failover drill:** `systemctl --user stop claude-wrapper` → the lead agent
      still answers via its fallback *(40-providers)*
- [ ] **Exit drill:** switch the lead tier to `anthropic` + API key, restart,
      confirm it answers, then switch back *(40-providers)*

## E. Agents talk, and you can see it

- [ ] Ask the lead to delegate to another agent; you get an answer back *(50-agents-a2a)*
- [ ] The Audit log shows both halves as `A → B` and `B → A` *(Audit tab)*
- [ ] The **Agent-to-agent only** filter shows exactly those exchanges *(Audit tab)*
- [ ] An A2A call with a wrong bearer token is rejected *(50-agents-a2a)*
- [ ] Export downloads the full JSONL transcript *(Audit tab)*

## F. The real PC

- [ ] "Deck" answers its agent card over the tailnet *(20-windows-pc)*
- [ ] A VPS agent runs a harmless command on the PC (`whoami`) *(20-windows-pc)*
- [ ] That exchange appears in the Audit log *(20-windows-pc)*
- [ ] An agent reads a page in your **logged-in** Chrome profile via CDP *(20-windows-pc)*
- [ ] The agent profile only has the logins it needs — not your everyday browsing *(60-security)*

## G. Phone

- [ ] The PWA installs to the Android home screen and opens full-screen *(15-tailscale)*
- [ ] Roster → chat → back works one-handed *(30-phone)*
- [ ] A reply streams in on the phone *(30-phone)*
- [ ] An approval card can be approved from the phone *(30-phone)*
- [ ] With Tailscale off (mobile data), it can't connect *(30-phone)*

## H. Approvals and safety

- [ ] Ask an agent to do something that sends/pays/deletes → an approval card
      appears instead of it just happening *(60-security)*
- [ ] Denying stops the action *(60-security)*
- [ ] `grep -r "mode: smart" /opt/recons/agents/*/home/config.yaml` matches every agent *(60-security)*
- [ ] No YOLO anywhere: `grep -ri yolo /opt/recons/agents/` is empty *(60-security)*
- [ ] Tool execution on the VPS runs in Docker *(60-security)*

## I. Teach mode and skills

- [ ] Walk an agent through a small 3-step workflow, then ask it to remember it *(teach-mode skill)*
- [ ] The draft appears in the dashboard's **Needs your approval** queue — it did
      **not** self-install *(Skills tab)*
- [ ] `tools/teach-mode/validate_skill.py <draft>` passes, including the guardrail line
- [ ] Approving moves it into the shared library, and a **different** agent can use it *(Skills tab)*
- [ ] Replay it once with approvals on and watch it work

## J. Routines

- [ ] Create a routine ("every weekday at 8am, …") on one agent *(Routines tab)*
- [ ] It appears in the list and can be paused and re-enabled *(Routines tab)*
- [ ] After it fires, the run shows in the Audit log as a `routine` event *(Audit tab)*

## K. Your existing Hermes and Buzz (optional)

- [ ] Your existing instance is peered and answers a call from the lead agent *(70-existing-hermes)*
- [ ] That exchange appears in the Audit log *(70-existing-hermes)*
- [ ] (If used) a Buzz channel message reaches an agent and the reply comes back *(70-existing-hermes)*

## M. The control centre

- [ ] **Settings** lists every provider with Configured / Not set and a health pill;
      no value is shown anywhere *(Settings tab)*
- [ ] Set a key (e.g. `NOUS_API_KEY`) from Settings: it flips to Configured, the
      Audit log gains `created NOUS_API_KEY` under Operator actions, and the value
      appears **only** in `/opt/recons/shared/secrets.env` (still mode 600) *(60-security §9)*
- [ ] Pause/resume the affected agent; it picks up the new key *(Settings › Agent services)*
- [ ] **Skills › Inspect** on a pending draft shows its body, files and review
      warnings; approving from the inspector moves it into the library *(Skills tab)*
- [ ] **Sessions** lists each agent's conversations and opens a transcript with
      tool activity *(Sessions tab)*
- [ ] Audit log › **Operator actions** shows your sign-in, the credential change
      and the skill approval *(Audit tab)*
- [ ] Sign out, then reload: the login screen, and the API answers 401 *(60-security §13)*
- [ ] A phone can do all of the above one-handed *(30-phone)*

## L. Operations

- [ ] `./scripts/backup.sh` produces an **encrypted** archive *(80-backup-update)*
- [ ] `./scripts/restore.sh <archive>` dry-restores and diffs clean *(80-backup-update)*
- [ ] `./scripts/update.sh` completes and ends with `vps-verify` passing *(80-backup-update)*
- [ ] `hermes doctor` is clean *(80-backup-update)*
- [ ] `grep -rn VERIFY config/ apps/orchestrator/` reviewed after the update *(00-overview)*

---

When A–H and M tick, you have a working private control centre. I–L are what
keep it working. A public `shell.essexrecons.com` is a separate, manual
decision with its own list in [65-public-endpoint-foundation.md](65-public-endpoint-foundation.md).
