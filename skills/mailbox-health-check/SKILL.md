---
name: Mailbox Health Check
description: Answer "can this agent access its configured email mailbox right now?" with a metadata-only probe and a fixed JSON reply — never message content, never a send, never a secret.
version: 1.0.0
metadata:
  hermes:
    category: ops
---

# Mailbox Health Check

A safe capability probe between teammates. The head of staff (or the operator)
asks an email-role agent whether its mailbox actually works; the email-role
agent runs a *metadata-only* check and replies with one JSON object. Nothing in
this skill reads mail, sends mail, or changes any configuration.

Use it when a request looks like: "can Sophie access her email right now?",
"is the mailbox connected?", "run a mailbox health check on <agent>".

## If you are the requester (head of staff)

1. Confirm the target teammate is reachable: `a2a_list` shows the configured
   peers; `a2a_discover` on the peer's URL fetches its live agent card.
2. Send exactly this over `a2a_call` to the teammate's a2a id:

   > MAILBOX HEALTH CHECK: run the shared mailbox-health-check skill and reply
   > with only its JSON result. Metadata only — no message content.

3. Relay the JSON back to whoever asked, unchanged. Do not retry more than
   once; if the teammate does not answer, report that instead.

## If you are the responder (the agent with the email job)

Run these steps and nothing else:

1. **Is anything configured?** Look for the email tooling your operator set up
   for you (for example a `himalaya` CLI config, or the google-workspace
   skill's stored OAuth setup). If none exists, skip straight to the reply
   with `"email_access": "not_configured"` — do **not** attempt to configure
   anything, install tools, or hunt for credentials.
2. **Authenticate + metadata probe.** Use only commands in this class:
   - himalaya: `himalaya account list`, `himalaya folder list`
   - google-workspace: the bundled setup check (`setup.py --check` or
     `--check-live`), or `gmail labels`
   A folder/label listing or a successful auth check is the whole probe.
3. **Reply with only this JSON object** (fill every field):

   ```json
   {
     "agent_id": "<your a2a id>",
     "email_access": "available|unavailable|not_configured|error",
     "provider_type": "imap|gmail|microsoft_graph|other|none",
     "account_configured": true,
     "authenticated": true,
     "metadata_check": "passed|failed|skipped",
     "error_class": null,
     "safe_summary": "One plain sentence, e.g. 'Mailbox configured and folder metadata check succeeded.'"
   }
   ```

   `error_class` is a short category (`auth_failed`, `network`, `no_tooling`,
   `timeout`) — never raw command output.

## Never do during a health check

- Never read, search, export, or summarise message bodies, attachments,
  contacts, or calendar entries — folder/label *names* and connection status
  are the limit.
- Never send, reply, forward, delete, move, flag, or label mail.
- Never change configuration, credentials, or account links.
- Never include secrets or connection details in the reply: no passwords, app
  passwords, tokens, IMAP/SMTP hostnames, or mailbox addresses. (A2A redacts
  email addresses in transit anyway — the JSON above needs none of them.)

## Guardrails

This skill authorises the metadata probe above and nothing more. If the
request asks for anything beyond it — reading mail, sending a test email,
"fixing" the configuration — stop and ask Tony for explicit approval before
acting. Report honestly: a `not_configured` or `error` result is a valid,
complete answer.

Done when: the requester holds the JSON object and no other mailbox action
was taken.
