"""Credential loading and persistence.

Only the access token is required. It is a graph.facebook.com token starting
with "EAA" (a Unified Login system-user token, or a Facebook Login user token);
the Instagram account ID is discovered from it, and its expiry is read from
Meta's debug_token endpoint. The HTTP calls live in `client.py`; this module
only loads, describes and persists.

Values in the .env file win over process environment variables, so a token
written back to .env is always the one used. The token is never logged or
included in `repr()`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, set_key

from .errors import ConfigError

ENV_FILE_VAR = "IG_STUDIO_ENV_FILE"
ENV_KEYS = ("IG_ACCESS_TOKEN", "IG_ACCOUNT_ID", "META_APP_ID", "META_APP_SECRET")
REFRESH_WINDOW = timedelta(days=10)  # re-exchange expiring user tokens this close to expiry

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_env_path() -> Path:
    """Pick the .env file: $IG_STUDIO_ENV_FILE, ./.env, then the project root's .env."""
    explicit = os.environ.get(ENV_FILE_VAR)
    if explicit:
        return Path(explicit).expanduser()
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    root_env = _PROJECT_ROOT / ".env"
    if root_env.exists():
        return root_env
    return cwd_env


@dataclass(frozen=True)
class Credentials:
    access_token: str = field(repr=False)
    user_id: str | None = None  # optional override: which Instagram account to use
    app_id: str | None = None
    app_secret: str | None = field(default=None, repr=False)

    @property
    def appsecret_proof(self) -> str | None:
        """HMAC-SHA256 of the token keyed by the app secret (sent when a secret is configured)."""
        if not self.app_secret:
            return None
        return hmac.new(self.app_secret.encode(), self.access_token.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class TokenStatus:
    """What Meta's debug_token endpoint says about the token."""

    is_valid: bool
    type: str | None = None
    application: str | None = None
    expires_at: datetime | None = None  # None = never expires
    scopes: tuple[str, ...] = ()

    def needs_refresh(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at - (now or datetime.now(UTC)) <= REFRESH_WINDOW

    def missing_scopes(self, required: tuple[str, ...]) -> list[str]:
        return [scope for scope in required if scope not in self.scopes]


def _epoch(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=UTC)
    return None


def parse_token_status(data: dict[str, Any]) -> TokenStatus:
    """Build a TokenStatus from debug_token's `data` object (expires_at 0 = never expires)."""
    return TokenStatus(
        is_valid=bool(data.get("is_valid")),
        type=data.get("type"),
        application=data.get("application"),
        expires_at=_epoch(data.get("expires_at")),
        scopes=tuple(data.get("scopes") or ()),
    )


class TokenStore:
    """Reads credentials from .env on every load (so external updates are picked up)."""

    def __init__(self, env_path: Path | None = None) -> None:
        self.env_path = env_path or resolve_env_path()

    def _values(self) -> dict[str, str | None]:
        values: dict[str, str | None] = {key: os.environ.get(key) for key in ENV_KEYS}
        if self.env_path.exists():
            values.update({k: v for k, v in dotenv_values(self.env_path).items() if v})
        return values

    def value(self, key: str) -> str | None:
        return (self._values().get(key) or "").strip() or None

    def load(self) -> Credentials:
        token = self.value("IG_ACCESS_TOKEN")
        if not token:
            raise ConfigError(
                f"IG_ACCESS_TOKEN is not set. Copy .env.example to .env and add your token "
                f"(looked in {self.env_path}; override with {ENV_FILE_VAR})."
            )
        if token.startswith("IG"):
            raise ConfigError(
                "IG_ACCESS_TOKEN is an Instagram Login token (starts with 'IG'), but this server uses the "
                "Instagram API with Unified Login on graph.facebook.com, which needs a token starting with 'EAA'. "
                "See docs/TOKEN_SETUP.md."
            )
        return Credentials(
            access_token=token,
            user_id=self.value("IG_ACCOUNT_ID"),
            app_id=self.value("META_APP_ID"),
            app_secret=self.value("META_APP_SECRET"),
        )

    def save_token(self, access_token: str) -> None:
        """Persist a new token to the .env file, keeping every other line."""
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        self.env_path.touch(exist_ok=True)
        set_key(self.env_path, "IG_ACCESS_TOKEN", access_token, quote_mode="never")
