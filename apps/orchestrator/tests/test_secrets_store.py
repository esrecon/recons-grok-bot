"""Secrets-store tests. This file is written by the app on the user's behalf, so
it must never corrupt, never leak, and never lose comments."""

from __future__ import annotations

import pytest

from recons_orchestrator.secrets_store import SecretsError, SecretsStore

TEMPLATE = """\
# Shared secrets for ALL agents — chmod 600.

# --- Nous ---
NOUS_API_KEY=

# --- Orchestrator ---
RECONS_WEBHOOK_SECRET=
"""


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "secrets.env"
    path.write_text(TEMPLATE)
    return SecretsStore(path)


def test_set_replaces_in_place_and_keeps_comments(store, tmp_path):
    store.set_many({"NOUS_API_KEY": "abc123"})
    text = (tmp_path / "secrets.env").read_text()
    assert "NOUS_API_KEY=abc123" in text
    assert "# --- Nous ---" in text          # comments survive
    assert text.count("NOUS_API_KEY") == 1   # replaced, not appended


def test_unknown_key_rejected(store):
    with pytest.raises(SecretsError):
        store.set_many({"PATH": "/tmp"})


def test_multiline_value_rejected(store):
    with pytest.raises(SecretsError):
        store.set_many({"NOUS_API_KEY": "a\nb"})


def test_is_set_ignores_empty(store):
    assert store.is_set("NOUS_API_KEY") is False
    store.set_many({"NOUS_API_KEY": "k"})
    assert store.is_set("NOUS_API_KEY") is True


def test_new_key_appended(store, tmp_path):
    store.set_many({"OPENAI_API_KEY": "openai-test-value"})
    assert "OPENAI_API_KEY=openai-test-value" in (tmp_path / "secrets.env").read_text()


def test_write_is_chmod_600(store, tmp_path):
    store.set_many({"NOUS_API_KEY": "k"})
    assert ((tmp_path / "secrets.env").stat().st_mode & 0o777) == 0o600


def test_webhook_secret_generated_once(store):
    assert store.ensure_webhook_secret() is True
    first = store.get("RECONS_WEBHOOK_SECRET")
    assert first and len(first) == 64          # 32 bytes hex
    assert store.ensure_webhook_secret() is False
    assert store.get("RECONS_WEBHOOK_SECRET") == first


def test_ensure_file_creates_with_600(tmp_path):
    path = tmp_path / "new" / "secrets.env"
    SecretsStore(path).ensure_file()
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_unset_clears(store):
    store.set_many({"NOUS_API_KEY": "k"})
    store.unset("NOUS_API_KEY")
    assert store.is_set("NOUS_API_KEY") is False
