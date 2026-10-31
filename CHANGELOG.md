# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `get_audience_demographics`: age, gender, country and city of followers, or of the engaged or reached audience.
- `get_follower_active_hours`: followers online per hour of day, converted to any IANA timezone.
- MIT license, contributing guide, code of conduct, security policy, issue and PR templates.
- GitHub Actions CI: lint (ruff), tests on Python 3.11 to 3.14 plus Windows and macOS, package build check.
- Dependabot for Python dependencies and GitHub Actions.
- `docs/TOKEN_SETUP.md`: step-by-step token guide for the Instagram API with Unified Login.

### Changed
- Optional environment variables renamed: `IG_USER_ID` → `IG_ACCOUNT_ID`, `IG_APP_ID` → `META_APP_ID`, `IG_APP_SECRET` → `META_APP_SECRET`.
- `check` now checks for the Unified Login permissions (`instagram_basic`, `instagram_manage_comments`, `instagram_manage_insights`, `instagram_content_publish`) and no longer expects `pages_read_engagement`.
- Docs and messages refer to Meta's "Instagram API with Unified Login" setup.

## [0.1.0] - 2026-10-31

### Added
- MCP server (stdio) for an Instagram professional account on `graph.facebook.com` (Graph API v26.0).
- Read tools: `get_profile`, `list_recent_media`, `get_media_comments`, `get_media_insights`, `get_account_insights`.
- Comment moderation: `reply_to_comment`, `hide_comment`, `delete_comment`.
- Publishing: `publish_image`, `publish_carousel`, `get_publishing_limit`.
- Every write tool previews by default (`confirm=False`) and makes no API call until confirmed.
- CLI: `check`, `refresh-token`, `exchange-token`.

[Unreleased]: https://github.com/Luffy610/ig-studio-mcp/compare/75f83d5...HEAD
[0.1.0]: https://github.com/Luffy610/ig-studio-mcp/commit/75f83d5
