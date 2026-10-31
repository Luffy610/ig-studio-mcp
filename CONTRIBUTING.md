# Contributing

Thanks for helping improve ig-studio-mcp! Bug reports, docs fixes and new tools are all welcome.

## Ground rules

- **Never commit or paste secrets.** That includes access tokens, app secrets, `.env` files and real account IDs, whether in code, tests, issues or PRs. Tests use fake values only.
- **Official APIs only.** This project uses Meta's documented Instagram Platform APIs. PRs that add scraping, password login or unofficial endpoints won't be accepted.
- **Writes are confirm-gated.** Any tool that changes the account must take `confirm: bool = False`. When `confirm` is false it returns a preview and makes no HTTP calls, and a test must prove that.
- Be respectful. See the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

You need [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
git clone https://github.com/Luffy610/ig-studio-mcp.git
cd ig-studio-mcp
uv sync
```

Running the tests needs no Instagram account: all HTTP calls are mocked with [respx](https://lundberg.github.io/respx/).

```bash
uv run pytest            # tests
uv run ruff check .      # lint
uv run ruff format .     # format
```

CI runs the same commands on Python 3.11 to 3.14 (Linux) plus Windows and macOS. All checks must pass before a PR is merged.

## Project layout

| File | Responsibility |
|---|---|
| `src/ig_studio_mcp/server.py` | MCP tool definitions only: thin wrappers, docstrings written for an LLM, confirm previews |
| `src/ig_studio_mcp/client.py` | Every HTTP call to `graph.facebook.com`; returns pydantic models |
| `src/ig_studio_mcp/auth.py` | Loading `.env`, token status, persisting tokens |
| `src/ig_studio_mcp/models.py` | Trimmed response models: only fields an LLM needs |
| `src/ig_studio_mcp/errors.py` | Meta error codes → actionable messages; token redaction |
| `src/ig_studio_mcp/cli.py` | `check`, `refresh-token`, `exchange-token` |

## Adding a tool

1. Check the endpoint, fields and permissions against [Meta's Instagram Platform docs](https://developers.facebook.com/documentation/instagram-platform), because they change between API versions. Link the page in your PR.
2. Add the HTTP call to `client.py` and a trimmed model to `models.py`.
3. Add the tool to `server.py` with a docstring covering what it does, when to use it, what it returns and its limits. Set `ToolAnnotations` correctly (read-only, destructive, idempotent).
4. Add respx tests for success and error responses. For write tools, also test that `confirm=False` makes no HTTP calls.
5. Update the tool table in `README.md` and add a line to `CHANGELOG.md` under *Unreleased*.

## Pull requests

- Keep PRs focused; one feature or fix per PR.
- Fill in the PR template checklist.
- Changes to the Graph API version (`GRAPH_API_VERSION` in `client.py`) need a note on what changed in Meta's changelog.
