from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotenv import dotenv_values

from ig_studio_mcp.auth import Credentials, TokenStatus, TokenStore, parse_token_status
from ig_studio_mcp.errors import ConfigError

from .conftest import TOKEN, USER_ID, write_env


def test_load_needs_only_token(store: TokenStore) -> None:
    creds = store.load()
    assert creds.access_token == TOKEN
    assert creds.user_id is None and creds.app_id is None and creds.app_secret is None


def test_load_optional_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env(env, IG_ACCOUNT_ID=USER_ID, META_APP_ID="123", META_APP_SECRET="shh")
    creds = TokenStore(env).load()
    assert (creds.user_id, creds.app_id, creds.app_secret) == (USER_ID, "123", "shh")


def test_env_file_wins_over_process_env(store: TokenStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IG_ACCESS_TOKEN", "EAAstale-from-environment")
    assert store.load().access_token == TOKEN


def test_process_env_used_when_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IG_ACCESS_TOKEN", "EAAfrom-env")
    assert TokenStore(tmp_path / "missing.env").load().access_token == "EAAfrom-env"


def test_missing_token_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="IG_ACCESS_TOKEN is not set"):
        TokenStore(tmp_path / "missing.env").load()


def test_instagram_login_token_rejected(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env(env, token="IGAAsomething")
    with pytest.raises(ConfigError, match="token starting with 'EAA'"):
        TokenStore(env).load()


def test_secrets_not_in_repr(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env(env, META_APP_SECRET="shh-secret")
    text = repr(TokenStore(env).load())
    assert TOKEN not in text and "shh-secret" not in text


def test_appsecret_proof() -> None:
    expected = hmac.new(b"secret", b"EAAtoken", hashlib.sha256).hexdigest()
    assert Credentials("EAAtoken", app_secret="secret").appsecret_proof == expected
    assert Credentials("EAAtoken").appsecret_proof is None


def test_parse_token_status_never_expires() -> None:
    status = parse_token_status(
        {"is_valid": True, "type": "SYSTEM_USER", "expires_at": 0, "scopes": ["instagram_basic"]}
    )
    assert status.expires_at is None and not status.needs_refresh()
    assert status.missing_scopes(("instagram_basic", "pages_read_engagement")) == ["pages_read_engagement"]


@pytest.mark.parametrize(("days_left", "needs"), [(30, False), (11, False), (10, True), (2, True)])
def test_needs_refresh_window(days_left: int, needs: bool) -> None:
    now = datetime(2026, 10, 31, tzinfo=UTC)
    assert TokenStatus(is_valid=True, expires_at=now + timedelta(days=days_left)).needs_refresh(now) is needs


def test_save_token_keeps_other_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env(env, IG_ACCOUNT_ID=USER_ID)
    TokenStore(env).save_token("EAAnew")
    values = dotenv_values(env)
    assert values["IG_ACCESS_TOKEN"] == "EAAnew" and values["IG_ACCOUNT_ID"] == USER_ID


def test_save_token_creates_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / ".env"
    TokenStore(path).save_token("EAAfresh")
    assert dotenv_values(path)["IG_ACCESS_TOKEN"] == "EAAfresh"


def test_resolve_env_path_honours_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom.env"
    write_env(target)
    monkeypatch.setenv("IG_STUDIO_ENV_FILE", str(target))
    assert TokenStore().env_path == target
