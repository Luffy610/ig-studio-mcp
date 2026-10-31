# ig-studio-mcp

[![CI](https://github.com/Luffy610/ig-studio-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Luffy610/ig-studio-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-stdio-purple.svg)](https://modelcontextprotocol.io)

Let Claude run your Instagram professional account through the official Graph API: analytics, comment management and publishing, all on your machine.

ig-studio-mcp is a local [Model Context Protocol](https://modelcontextprotocol.io) server that uses only Meta's official [Instagram API with Unified Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/get-started) (`graph.facebook.com`, Graph API v26.0). There's no scraping, no password login, and no Facebook Page required.

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Connect to Claude](#connect-to-claude)
- [Tools](#tools)
- [Configuration](#configuration)
- [CLI](#cli)
- [Limits worth knowing](#limits-worth-knowing)
- [Development](#development)
- [Contributing](#contributing) · [Security](#security) · [License](#license)

## Features

- **Analytics:** profile, recent posts and reels, per-post insights (reach, views, saves, shares, watch time) and account insights over 1, 7 or 28 days, including follows and unfollows.
- **Audience:** age, gender, country and city of your followers (or of the people you reached or engaged), and the hours your followers are online, in your timezone.
- **Comment management:** read, reply to, hide/unhide and delete comments.
- **Publishing:** single images and carousels, plus a check of your daily publishing quota.
- **Safe by default:** every tool that changes your account previews first and sends nothing until called again with `confirm=true`.
- **One setting:** only an access token is required. The Instagram account is found from the token, and token expiry is read from Meta.
- **Private:** runs locally over stdio. The token stays in your `.env` and is never logged or returned in tool output.

## Quick start

**You need:**
- An Instagram **Creator or Business** account
- A Meta app using the **Instagram API with Unified Login**
- [uv](https://docs.astral.sh/uv/) and Python 3.11+

**1. Install**

```bash
git clone https://github.com/Luffy610/ig-studio-mcp.git
cd ig-studio-mcp
uv sync
```

**2. Get a token.** Follow the step-by-step guide in **[docs/TOKEN_SETUP.md](docs/TOKEN_SETUP.md)**. It takes about 15 minutes and needs no App Review.

**3. Configure.** Copy the example file:

```bash
cp .env.example .env
```

Then set the one required value in `.env`:

```dotenv
IG_ACCESS_TOKEN=EAA...
```

**4. Check**

```bash
uv run ig-studio-mcp check
```

```text
Token: SYSTEM_USER for app 'my app', never expires
Instagram accounts reachable: @your_handle (1784...)
OK - using @your_handle (id 1784..., 1234 followers)
```

## Connect to Claude

### Claude Desktop

Add the server to `claude_desktop_config.json`. On Windows the file is at `%APPDATA%\Claude\claude_desktop_config.json`; on macOS it's at `~/Library/Application Support/Claude/claude_desktop_config.json`.

```json
{
  "mcpServers": {
    "ig-studio": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/ig-studio-mcp", "run", "ig-studio-mcp"]
    }
  }
}
```

On Windows, write the path with escaped backslashes, e.g. `"C:\\path\\to\\ig-studio-mcp"`. Restart Claude Desktop afterwards.

### Claude Code

```bash
claude mcp add ig-studio --scope user -- uv --directory /absolute/path/to/ig-studio-mcp run ig-studio-mcp
```

### MCP Inspector

From the project folder, run:

```bash
npx @modelcontextprotocol/inspector uv run ig-studio-mcp
```

Open the URL it prints, click **Connect**, then go to **Tools → List Tools**.

**Try asking Claude:**
- "How did my last 5 reels do?"
- "Where are my followers from, and when is the best time to post?"
- "Summarise the comments on my latest post and draft replies."
- "Hide the spam comments on my last reel."

## Tools

| Tool | What it does | Key arguments | Permission |
|---|---|---|---|
| `get_profile` | Username, name, bio, website, followers, follows, media count | none | `instagram_basic` |
| `list_recent_media` | Recent posts and reels (newest first) with caption, type, time, likes, comments, link | `limit` 1-100 | `instagram_basic` |
| `get_media_comments` | Top-level comments: id, username, text, time, likes, reply count | `media_id`, `limit` 1-500 | `instagram_manage_comments` |
| `get_media_insights` | Lifetime metrics for a post, reel or story; metrics chosen by media type | `media_id` | `instagram_manage_insights` |
| `get_account_insights` | Account totals for the last 1, 7 or 28 days, including follows and unfollows | `period`: `day`, `week`, `days_28` | `instagram_manage_insights` |
| `get_audience_demographics` | Age, gender, country and city of followers, or of the engaged or reached audience | `audience`, `breakdown`, `timeframe` | `instagram_manage_insights` |
| `get_follower_active_hours` | Average followers online for each hour of the day, plus the best 3 hours | `days` 1-30, `timezone` | `instagram_manage_insights` |
| `reply_to_comment` | Post a public reply | `comment_id`, `message`, `confirm` | `instagram_manage_comments` |
| `hide_comment` | Hide, or unhide with `hide=false`. Reversible | `comment_id`, `hide`, `confirm` | `instagram_manage_comments` |
| `delete_comment` | Permanently delete a comment on your media | `comment_id`, `confirm` | `instagram_manage_comments` |
| `publish_image` | Publish one JPEG with a caption | `image_url`, `caption`, `confirm` | `instagram_content_publish` |
| `publish_carousel` | Publish 2-10 JPEGs as one post | `image_urls`, `caption`, `confirm` | `instagram_content_publish` |
| `get_publishing_limit` | API posts used and remaining in the rolling 24 hours | none | `instagram_content_publish` |

Notes:
- **Media insights:** feed posts and carousels return reach, views, likes, comments, saves, shares, total_interactions, profile_visits and follows. Reels add `ig_reels_avg_watch_time` and `ig_reels_video_view_total_time` (in ms). Stories return reach, views, shares, replies, navigation, follows, profile_visits and total_interactions.
- **Missing metrics:** if Instagram doesn't return a metric for an item, it's listed under `unavailable`; the rest of the call still succeeds.
- **Deprecated metrics are never requested:** `impressions`, `plays`, `video_views`, `profile_views`, `website_clicks`, `engagement`.

### Write tools need confirmation

Every write tool takes `confirm`, which defaults to `false`. With `confirm=false` the tool sends **nothing** to Instagram. Instead it returns a preview: the action, the exact text, and the API call it would make. Claude is told to show you the preview and call again with `confirm=true` only after you approve. In every result, `executed` tells you whether anything was actually sent.

### Publishing

A publish takes three steps:
1. Create a media container.
2. Check the container's `status_code` until it's `FINISHED`. Images are usually ready right away; the server gives up after about 4 minutes, within Meta's 5-minute guidance.
3. Publish the container.

If any step fails, nothing is posted. Images must be **public JPEG URLs**, at most 8 MB, with an aspect ratio between 4:5 and 1.91:1. Captions can have up to 2,200 characters, 30 hashtags and 20 @mentions, and are checked before anything is sent.

## Configuration

Settings are read from `.env`, which is git-ignored. See [`.env.example`](.env.example) for the full list.

| Variable | Required | Purpose |
|---|---|---|
| `IG_ACCESS_TOKEN` | **yes** | Token from **API setup with Unified login → Generate access tokens** (starts with `EAA`). See [docs/TOKEN_SETUP.md](docs/TOKEN_SETUP.md). |
| `IG_ACCOUNT_ID` | no | Which Instagram account to use, if the token can reach several. `check` lists them. |
| `META_APP_ID`, `META_APP_SECRET` | no | Meta app credentials (**App settings → Basic**). Needed only for expiring Facebook Login user tokens. If the secret is set, every call also sends `appsecret_proof`. |
| `IG_STUDIO_ENV_FILE` | no | Path to a different `.env` file. This one is a process environment variable, not a `.env` entry. |

How settings are found:
- **Where `.env` comes from:** `$IG_STUDIO_ENV_FILE`, then `./.env`, then the project root's `.env`.
- **Precedence:** values in `.env` override process environment variables.
- **No restart needed:** the file is re-read on every call, so a new token takes effect immediately.

**Account discovery.** Unified Login system-user tokens find the account through `/me/assigned_instagram_accounts`. Facebook Login user tokens fall back to the Page-linked account in `/me/accounts`.

**Token expiry.** System-user tokens usually never expire. For tokens that do expire, the server reads the expiry from `debug_token`. If `META_APP_ID` and `META_APP_SECRET` are set, it renews a Facebook Login user token when it's within 10 days of expiry. Unified Login tokens with an expiry date have to be regenerated (see [docs/TOKEN_SETUP.md](docs/TOKEN_SETUP.md#refreshing-and-rotating-the-token)).

## CLI

| Command | Purpose |
|---|---|
| `uv run ig-studio-mcp` | Run the MCP server over stdio (what Claude launches) |
| `uv run ig-studio-mcp check` | Show the token type, expiry and missing permissions, list reachable accounts, and fetch the profile |
| `uv run ig-studio-mcp refresh-token` | Renew an expiring Facebook Login user token (does nothing for tokens that never expire) |
| `uv run ig-studio-mcp exchange-token` | Swap a short-lived Facebook Login user token for a long-lived one (needs `META_APP_ID` and `META_APP_SECRET`) |

## Limits worth knowing

- **Insights lag:** data can be up to 48 hours behind.
  - Story insights exist for only 24 hours and need at least 5 viewers.
  - Items inside a carousel have no insights of their own.
- **Follower and audience metrics** (follows/unfollows, demographics, active hours) need 100+ followers, or 100+ engagements in the timeframe for the engaged audience.
  - Demographics: `timeframe` can only be `this_week` or `this_month`, and only affects the engaged and reached audiences.
  - Cities are limited to the top 45.
  - Active hours cover at most the last 30 days. Instagram reports them in Pacific time, and the server converts them to the `timezone` you pass.
- **Comments:**
  - `get_media_comments` returns top-level comments only. Replies are counted, not listed.
  - Hidden comments disappear from the list, so keep the ID if you may want to unhide one.
- **Rate limits:** Instagram rate-limits calls per account over a rolling 24 hours. When Meta says how long to wait, rate-limit errors include it.
- **Publishing quota:** 100 API posts per rolling 24 hours, with a carousel counting as one. Some of Meta's docs still say 50, so check yours with `get_publishing_limit`.
- **API version:** pinned in one place, `GRAPH_API_VERSION` in [`client.py`](src/ig_studio_mcp/client.py).

## Development

```bash
uv sync                    # install, including dev tools
uv run pytest              # 120+ tests, all HTTP mocked; no Instagram account needed
uv run ruff check .        # lint
uv run ruff format .       # format
```

Code layout:

| File | Responsibility |
|---|---|
| [`server.py`](src/ig_studio_mcp/server.py) | MCP tool definitions only |
| [`client.py`](src/ig_studio_mcp/client.py) | Every HTTP call |
| [`auth.py`](src/ig_studio_mcp/auth.py) | Credentials and the `.env` file |
| [`models.py`](src/ig_studio_mcp/models.py) | Trimmed response models |
| [`errors.py`](src/ig_studio_mcp/errors.py) | Meta error codes → actionable messages |
| [`cli.py`](src/ig_studio_mcp/cli.py) | Command-line entry point |

CI runs lint, tests on Python 3.11 to 3.14 (plus Windows and macOS) and a package build on every push and pull request.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md), and check the [changelog](CHANGELOG.md) for what changed recently.

## Security

Please report vulnerabilities privately; see [SECURITY.md](SECURITY.md). Never paste access tokens or `.env` contents into issues.

## License

[MIT](LICENSE) © 2026 Dhruv Kotwani

## Disclaimer

This project is **not affiliated with, endorsed by, or sponsored by Meta Platforms, Inc. or Instagram**. "Instagram", "Facebook" and "Meta" are trademarks of Meta Platforms, Inc. Use it only with accounts you own or manage, and follow [Meta's Platform Terms](https://developers.facebook.com/terms/).
