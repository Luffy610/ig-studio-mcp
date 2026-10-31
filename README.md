# ig-studio-mcp

Let Claude run your Instagram professional account through the official Graph API: analytics, comment management and publishing, all on your machine.

A local [MCP](https://modelcontextprotocol.io) server (stdio) built on Meta's official
[Instagram API with Facebook Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/)
(`graph.facebook.com`, Graph API **v26.0**). It does no scraping and never logs in with a password.

> **Status:** all three phases are implemented: read-only analytics and comments, comment moderation, and publishing.

## Tools

| Tool | What it does | Key args | Permissions |
|---|---|---|---|
| `get_profile` | Username, name, bio, website, followers, follows, media count | none | `instagram_basic`, `pages_read_engagement` |
| `list_recent_media` | Recent posts/reels (newest first) with caption, type, timestamp, likes, comments, permalink | `limit` 1-100 (default 10) | `instagram_basic`, `pages_read_engagement` |
| `get_media_comments` | Top-level comments on a post: id, username, text, timestamp, likes, hidden, reply count | `media_id`, `limit` 1-500 (default 50) | + `instagram_manage_comments` |
| `get_media_insights` | Lifetime metrics for one post/reel/story; metric set chosen by media type | `media_id` | + `instagram_manage_insights` |
| `get_account_insights` | Account totals over the last 1, 7 or 28 days | `period`: `day` / `week` / `days_28` | + `instagram_manage_insights` |
| `reply_to_comment` | Post a public reply to a comment | `comment_id`, `message`, `confirm` | + `instagram_manage_comments` |
| `hide_comment` | Hide (or unhide with `hide=false`) a comment. Reversible | `comment_id`, `hide`, `confirm` | + `instagram_manage_comments` |
| `delete_comment` | Permanently delete a comment on your media | `comment_id`, `confirm` | + `instagram_manage_comments` |
| `publish_image` | Post one JPEG with a caption | `image_url`, `caption`, `confirm` | + `instagram_content_publish` |
| `publish_carousel` | Post 2-10 JPEGs as one post | `image_urls`, `caption`, `confirm` | + `instagram_content_publish` |
| `get_publishing_limit` | Posts used and remaining in the rolling 24 hours | none | + `instagram_content_publish` |

### Publishing

A publish runs Meta's three steps:
1. Create a media container (`POST /{ig-user-id}/media`). For carousels, there's one child container per image plus a parent with `media_type=CAROUSEL`.
2. Check the container's `status_code` until it is `FINISHED`. Images are usually ready on the first check. The server stops waiting after about 4 minutes, within Meta's 5-minute guidance.
3. Publish it (`POST /{ig-user-id}/media_publish`).

The result includes the new post's `media_id` and `permalink`. If a step fails, nothing is posted. Any leftover container expires on its own after 24 hours.

Requirements:
- Images must be public JPEG URLs that Instagram can download itself: no login, no expiring links. At most 8 MB, aspect ratio 4:5 to 1.91:1.
- Captions: at most 2,200 characters, 30 hashtags and 20 @mentions. These limits are checked before anything is sent.
- The live API reports a cap of **100 API posts per rolling 24 hours**; a carousel counts as one. Some of Meta's docs still say 50.
- Carousels are limited to 10 images. Meta's guide says 20, but its error reference says 10, so the server uses 10.
- Every carousel image is cropped to the first image's aspect ratio.

### Write tools need confirmation

Every write tool takes `confirm` (default `false`). With `confirm=false`, the tool sends **nothing** to Instagram. It returns a preview instead: the action, the comment ID, the exact reply text, and the Graph API call it would make. Claude is told to show you that preview and repeat the call with `confirm=true` only after you approve. In the result, `executed` tells you whether anything was actually sent.

Limits from Meta:
- Replies always attach to the top-level comment.
- Hidden comments and live-video comments can't be replied to.
- Comments you wrote on your own media can't be hidden.
- Hidden comments drop out of `get_media_comments`, so keep the comment ID if you may want to unhide it later.
- Deleting works only for comments on your own media.

Metrics returned by `get_media_insights`:

- **Feed posts & carousels:** reach, views, likes, comments, saves, shares, total_interactions, profile_visits, follows
- **Reels:** reach, views, likes, comments, saves, shares, total_interactions, ig_reels_avg_watch_time, ig_reels_video_view_total_time (ms)
- **Stories:** reach, views, shares, replies, navigation, follows, profile_visits, total_interactions

`get_account_insights` returns reach, views, accounts_engaged, total_interactions, likes, comments, shares, saves, replies, profile_links_taps, plus **follows** (new followers) and **unfollows**.

Metrics Instagram doesn't return for a given item are listed under `unavailable` instead of failing the whole call.
Deprecated metrics (`impressions`, `plays`, `video_views`, `profile_views`, `website_clicks`, `engagement`) are not used.

## Prerequisites

1. **An Instagram Creator or Business account.** In the Instagram app: Settings → Account type and tools → Switch to professional account.
2. **A Meta Business portfolio** at [business.facebook.com](https://business.facebook.com) that owns, or has access to, the Instagram account. Add it under Business settings → Accounts → Instagram accounts.
3. **A Meta app** of type *Business* at <https://developers.facebook.com/apps>, connected to that Business portfolio.
4. **Permissions** on the token:
   - `instagram_basic`, `pages_read_engagement`, `pages_show_list`
   - `instagram_manage_comments`
   - `instagram_manage_insights`
   - `instagram_content_publish` (only needed for Phase 3; grant it now to avoid regenerating the token later)
5. **[uv](https://docs.astral.sh/uv/)** and Python 3.11+. **Node.js** is only needed for MCP Inspector.

## Install

```bash
git clone <this repo> ig-studio-mcp
cd ig-studio-mcp
uv sync
cp .env.example .env
```

## Token setup

Only one setting is required: `IG_ACCESS_TOKEN`, a Facebook Login token that starts with `EAA`. The server finds the Instagram account from the token and reads the token's expiry from Meta's `debug_token` endpoint.

> Instagram Login tokens (`IGAA...`) don't work here. They belong to the separate `graph.instagram.com` API.

### Option A: system-user token (recommended, never expires)

1. In [Business settings](https://business.facebook.com/settings) go to **Users → System users → Add**. Create an admin system user.
2. Click **Assign assets** and add both the **Instagram account** (full control) and your **app**.
3. Click **Generate token**. Pick your app, set expiration to **Never**, tick the permissions listed above, and generate.
4. Put the token in `.env`:
   ```dotenv
   IG_ACCESS_TOKEN=EAA...
   ```

This token doesn't expire, so there's nothing to refresh. The server finds the account through `/me/assigned_instagram_accounts`.

### Option B: user token (expires in ~60 days)

1. In [Graph API Explorer](https://developers.facebook.com/tools/explorer/), choose your app, add the permissions listed above, and click **Generate Access Token**. Then log in and pick the Page connected to your Instagram account.
2. Put your app ID and secret in `.env` (from App settings → Basic):
   ```dotenv
   IG_APP_ID=...
   IG_APP_SECRET=...
   ```
3. Exchange the short-lived token (about 1 hour) for a long-lived one (about 60 days):
   ```bash
   uv run ig-studio-mcp exchange-token
   ```
   Paste the token when prompted (input is hidden). The long-lived token is written to `.env`.

With a user token, the account is found through the Facebook Page it's linked to (`/me/accounts`). When the token is within 10 days of expiry and `IG_APP_ID`/`IG_APP_SECRET` are set, the server re-exchanges it automatically, at most once a day. Meta doesn't document whether re-exchanging extends a long-lived user token. If it stops working, generate a new one, or switch to a system-user token.

### Optional settings

| Variable | When you need it |
|---|---|
| `IG_USER_ID` | The token can reach several Instagram accounts. `check` lists them; set this to pick one. |
| `IG_APP_ID`, `IG_APP_SECRET` | User tokens: `exchange-token` and automatic renewal. If `IG_APP_SECRET` is set, every call also sends `appsecret_proof`, which is required when your app has *Require App Secret* turned on. |

### Verify

```bash
uv run ig-studio-mcp check
```

This prints the token type, its expiry, any missing permissions, the reachable Instagram accounts, and `OK - using @yourname ...`.

The access token is never logged or printed. Request URLs are kept out of logs, error messages are scrubbed, and Meta's paging URLs (which contain the token) are never returned by any tool.

### Where `.env` is read from

The server reads `$IG_STUDIO_ENV_FILE` if set, otherwise `./.env`, otherwise the project root's `.env`. It re-reads the file on every call, so a new token takes effect without a restart. Restart only if you switch to a different Instagram account. Values in `.env` take precedence over process environment variables.

## Use with Claude

### Claude Desktop

Edit `claude_desktop_config.json`. On Windows it's at `%APPDATA%\Claude\claude_desktop_config.json`; on macOS, `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ig-studio": {
      "command": "uv",
      "args": ["--directory", "D:\\Projects\\ig-studio-mcp", "run", "ig-studio-mcp"]
    }
  }
}
```

Use the absolute path to your checkout (on macOS/Linux, e.g. `/Users/you/ig-studio-mcp`). Restart Claude Desktop afterwards.

### Claude Code

```bash
claude mcp add ig-studio --scope user -- uv --directory /absolute/path/to/ig-studio-mcp run ig-studio-mcp
```

Then run `claude mcp list` to confirm it's connected.

### MCP Inspector

From the project directory:

```bash
npx @modelcontextprotocol/inspector uv run ig-studio-mcp
```

Open the URL it prints, click **Connect**, then go to **Tools → List Tools** and run each tool.

## CLI

| Command | Purpose |
|---|---|
| `uv run ig-studio-mcp` | Run the MCP server over stdio (what Claude launches) |
| `uv run ig-studio-mcp check` | Show token type, expiry and permissions; list reachable accounts; fetch the profile |
| `uv run ig-studio-mcp refresh-token` | Re-exchange an expiring user token now (no-op for tokens that never expire) |
| `uv run ig-studio-mcp exchange-token` | Swap a short-lived user token for a long-lived one (needs `IG_APP_ID` and `IG_APP_SECRET`) |

## Limits worth knowing

- Insights can lag **up to 48 hours**. Story insights exist only for 24 hours and need at least 5 viewers. Media inside a carousel has no insights of its own.
- Follower metrics (follows/unfollows) may be unavailable for accounts with **fewer than 100 followers**.
- `get_media_comments` returns top-level comments only. Replies are counted, not listed. Comments on age-gated media aren't returned.
- Calls are rate-limited per account over a rolling 24 hours. Rate-limit errors say how many minutes to wait when Meta provides that.
- The Graph API version is pinned in one constant: `GRAPH_API_VERSION` in `src/ig_studio_mcp/client.py`.
- `/me/assigned_instagram_accounts`, used to find the account for system-user tokens, works in practice but isn't in Meta's public reference. If it ever fails, the server falls back to `/me/accounts`, and you can always set `IG_USER_ID`.

## Development

```bash
uv run pytest
```

Layout: `server.py` (MCP tools) → `client.py` (all HTTP) → `auth.py` (credentials / `.env`), with `models.py` (trimmed responses) and `errors.py` (Meta error codes → actionable messages). `cli.py` provides the commands above.

## Disclaimer

This project is **not affiliated with, endorsed by, or sponsored by Meta Platforms, Inc. or Instagram**. "Instagram", "Facebook" and "Meta" are trademarks of Meta Platforms, Inc. Use it only with accounts you own or manage, and follow [Meta's Platform Terms](https://developers.facebook.com/terms/).
