"""Command-line entry point.

    ig-studio-mcp                 run the MCP server over stdio (default)
    ig-studio-mcp check           verify the token, its expiry and permissions, and the Instagram account
    ig-studio-mcp refresh-token   re-exchange a long-lived user token and save it to .env
    ig-studio-mcp exchange-token  swap a short-lived user token for a long-lived one

The token is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys

from .auth import TokenStore
from .client import GRAPH_API_VERSION, REQUIRED_SCOPES, IGClient
from .errors import IGStudioError


def _check() -> int:
    async def run() -> int:
        store = TokenStore()
        async with IGClient(store, auto_refresh=False) as client:
            status = await client.token_status()
            if not status.is_valid:
                print("Token is not valid (expired or revoked). See README -> 'Token setup'.")
                return 1
            expiry = "never expires" if status.expires_at is None else f"expires {status.expires_at:%Y-%m-%d %H:%M} UTC"
            print(f"Token: {status.type or 'unknown type'} for app '{status.application}', {expiry}")
            missing = status.missing_scopes(REQUIRED_SCOPES)
            if missing:
                print(f"Warning: token is missing permissions: {', '.join(missing)}")

            accounts = await client.list_instagram_accounts()
            listed = ", ".join(f"@{a.username} ({a.id})" for a in accounts) or "none"
            print(f"Instagram accounts reachable: {listed}")
            profile = await client.get_profile()
        print(f"OK - using @{profile.username} (id {profile.id}, {profile.followers_count} followers)")
        print(f"Graph API {GRAPH_API_VERSION}, credentials from {store.env_path}")
        return 0

    return asyncio.run(run())


def _refresh() -> int:
    async def run() -> int:
        async with IGClient(TokenStore(), auto_refresh=False) as client:
            status = await client.token_status()
            if status.is_valid and status.expires_at is None:
                print(f"Token ({status.type}) never expires - nothing to refresh.")
                return 0
            info = await client.refresh_access_token()
        print(f"Token re-exchanged and saved to .env (valid ~{info.expires_in_days} days).")
        return 0

    return asyncio.run(run())


def _exchange() -> int:
    short = getpass.getpass("Paste short-lived user access token (input hidden): ").strip()
    if not short:
        print("No token entered.", file=sys.stderr)
        return 1

    async def run() -> int:
        async with IGClient(TokenStore(), auto_refresh=False) as client:
            info = await client.exchange_short_lived_token(short)
        print(f"Long-lived token saved to .env (valid ~{info.expires_in_days} days).")
        return 0

    return asyncio.run(run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ig-studio-mcp", description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "check", "refresh-token", "exchange-token"],
    )
    args = parser.parse_args(argv)
    # stdout is the MCP transport when serving, so logs always go to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        from .server import mcp

        mcp.run("stdio")
        return 0
    try:
        return {"check": _check, "refresh-token": _refresh, "exchange-token": _exchange}[args.command]()
    except IGStudioError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
