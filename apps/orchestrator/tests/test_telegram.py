"""Telegram token store (write-only, 600, atomic) + extraction from a home."""

from __future__ import annotations

from recons_orchestrator import telegram


def test_token_store_round_trip_and_permissions(settings):
    telegram.store_token(settings, "hermes", "12345678:secret-token")
    assert telegram.load_tokens(settings) == {"hermes": "12345678:secret-token"}
    path = telegram.tokens_path(settings)
    assert (path.stat().st_mode & 0o777) == 0o600

    telegram.store_token(settings, "comms", "999:other")
    assert telegram.load_tokens(settings)["hermes"] == "12345678:secret-token"

    telegram.drop_token(settings, "hermes")
    assert "hermes" not in telegram.load_tokens(settings)


def test_extract_from_gateway_platform_block(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "gateway:\n"
        "  platforms:\n"
        "    telegram:\n"
        "      enabled: true\n"
        "      extra:\n"
        "        bot_token: '123:abc'\n"
        "        allowed_users: '42'\n"
    )
    assert telegram.extract_telegram(home) == ("123:abc", "42")


def test_extract_from_home_dotenv(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("model: {}\n")
    (home / ".env").write_text(
        "# local overrides\nTELEGRAM_BOT_TOKEN=456:def\nTELEGRAM_ALLOWED_USERS=7,8\n"
    )
    assert telegram.extract_telegram(home) == ("456:def", "7,8")


def test_extract_returns_none_when_absent(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text("model: {}\n")
    assert telegram.extract_telegram(home) == (None, None)


def test_extract_survives_a_broken_config(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(": not yaml [")
    assert telegram.extract_telegram(home) == (None, None)
