# Security Policy

## Supported versions

Security fixes go into the latest release on the `master` branch.

## Reporting a vulnerability

**Please don't open a public issue for security problems.**

Report them privately through GitHub: go to the repository's **Security** tab and choose **Report a vulnerability**, or open [this link](https://github.com/Luffy610/ig-studio-mcp/security/advisories/new). Include what you found, how to reproduce it, and the impact.

You can expect an acknowledgement within a few days. Fixed issues will be disclosed in a GitHub security advisory and the changelog, with credit if you want it.

## Handling your own credentials

ig-studio-mcp runs locally and talks only to `graph.facebook.com`. Your access token gives full control over your Instagram account, so:

- Keep it in `.env`, which is git-ignored, and never commit it or paste it into issues, PRs or chats.
- If a token leaks, regenerate it right away: open your app → **API setup with Unified login** → **Generate access tokens**. To cut off the old token, remove the app's access in your business portfolio. See [docs/TOKEN_SETUP.md](docs/TOKEN_SETUP.md#security-notes).
- The server never logs the token, keeps it out of error messages, and never returns Meta's paging URLs (which contain it).

If you find a way the token could leak through logs, errors or tool output, that is a vulnerability: please report it as described above.
