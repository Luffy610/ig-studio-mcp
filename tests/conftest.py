from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
import respx

from ig_studio_mcp.auth import TokenStore
from ig_studio_mcp.client import API_URL, IGClient

TOKEN = "EAAtest-secret-token-123"
USER_ID = "17841400000000000"
NEVER_EXPIRES = {
    "data": {
        "is_valid": True,
        "type": "SYSTEM_USER",
        "application": "test app",
        "expires_at": 0,
        "scopes": [
            "instagram_basic",
            "instagram_manage_comments",
            "instagram_manage_insights",
            "instagram_content_publish",
        ],
    }
}


def write_env(path: Path, *, token: str = TOKEN, **extra: str) -> None:
    lines = [f"IG_ACCESS_TOKEN={token}"] + [f"{k}={v}" for k, v in extra.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("IG_ACCOUNT_ID", "IG_ACCESS_TOKEN", "META_APP_ID", "META_APP_SECRET", "IG_STUDIO_ENV_FILE"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    write_env(path)
    return path


@pytest.fixture
def store(env_file: Path) -> TokenStore:
    return TokenStore(env_file)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    """graph.facebook.com/v26.0 with a never-expiring system-user token assigned one IG account."""
    with respx.mock(base_url=API_URL, assert_all_called=False) as router:
        router.get("/debug_token", name="debug_token").respond(json=NEVER_EXPIRES)
        router.get("/me/assigned_instagram_accounts", name="assigned").respond(
            json={"data": [{"id": USER_ID, "username": "studio"}]}
        )
        yield router


@pytest.fixture
async def client(store: TokenStore) -> AsyncIterator[IGClient]:
    async with IGClient(store, httpx.AsyncClient()) as c:
        yield c
