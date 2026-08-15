---
name: Teach Mode
description: Turn a workflow the user just demonstrated into a reusable skill draft, with guardrails, for human approval.
version: 1.0.0
metadata:
  hermes:
    category: meta
---

# Teach Mode

Use this when the user says something like "remember how I did that", "turn that
into a skill", "learn this", or after you have just walked through a repeatable
task together.

Grok Bot records a screen demonstration and drafts a skill from it. Here the
demonstration is the session you just completed — the pages you visited, the
fields you filled, the checks you made — plus whatever the user narrates.

## Steps

1. **Confirm the scope.** Say back, in one or two lines, what the workflow was
   and where it starts (e.g. "log into the supplier portal, add a supplier, email
   them the forms"). Ask the user to correct you before continuing.

2. **Reconstruct the steps** from the session: each navigation, each field and
   the value's *meaning* (not the literal value), each decision point, and how
   you knew the task had succeeded. Where you used the browser, prefer stable
   landmarks (link text, field labels, headings) over positions or coordinates.

3. **Generalise the specifics.** Any value that was true only for this run — a
   supplier name, an invoice number, a date — becomes a named input at the top of
   the skill, not a hard-coded string.

4. **Write the draft** as `SKILL.md` with AgentSkills frontmatter:
   - `name`: short, human, title-case
   - `description`: one line that says *when to use it*, so another agent can
     decide — this is what gets matched against future requests
   - body: numbered steps, an "Inputs" list, and a "Done when" line

5. **Add the guardrail block. This is mandatory.** Every taught skill must state
   where the agent stops and asks. A recording that once included "click Send"
   must never become a skill that always sends. Use wording like:

   > ## Guardrails
   >
   > Ask before sending, paying, ordering, publishing, or deleting anything.
   > Never submit a payment or a final form without explicit approval.

6. **Validate it** by running `tools/teach-mode/validate_skill.py <path>`, and
   fix anything it flags.

7. **Stage it for approval.** Save the draft to the agent's
   `pending/skills/<slug>/SKILL.md`. Do NOT install it into the shared library
   yourself — the user approves it in the dashboard's Skills tab, and only then
   does every agent get it.

8. **Offer a dry run.** Suggest replaying the new skill once with approvals on,
   so the user can watch it work before trusting it.

## Guardrails

Never write a skill that takes an irreversible action without asking. Never bake
a password, API key, or token into a skill — reference the stored secret by name.
If the demonstration included credentials being typed, leave a step that says to
ask the user to sign in, and never record the value.
