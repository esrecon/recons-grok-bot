"""Recons Grok Bot orchestrator.

Turns the Grok Bot-style dashboard's one-click actions into concrete Hermes
Agent profiles: it provisions a permanent agent (its own HERMES_HOME, SOUL.md
and job role) that shares skills and API keys with every other agent, wires the
A2A peer mesh so agents can message each other, and exposes the merged audit
ledger over those exchanges.
"""

__version__ = "0.1.0"
