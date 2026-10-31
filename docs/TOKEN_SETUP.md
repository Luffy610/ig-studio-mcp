# Generating an Instagram API access token (Unified Login)

This guide takes you from zero to a working access token for **your own** Instagram Creator or Business account, checked with `curl`, so you can use it with ig-studio-mcp. You don't need App Review or Business Verification.

> Last verified against Meta's docs: **31 October 2026** (Graph API v26.0). Meta changes dashboard labels often. If a label here doesn't match what you see, look for the closest match and check the [References](#references).

## Overview

**What you'll end up with:** a *Business Integration System User access token* (the dashboard calls it a "System-user access token (SUAT)") and your Instagram account ID. The token starts with `EAA…` and is usually 150+ characters long. It belongs to your business portfolio, not to you as a person.

**Why Unified Login:** Meta calls the *Instagram API with Unified Login* "the recommended setup for new integrations". It needs no Facebook Page, and it's the setup ig-studio-mcp is built for.

| Setup | API host | Token | Facebook Page |
|---|---|---|---|
| **Instagram API with Unified Login** (this guide) | `graph.facebook.com` | Business Integration System User access token | Not required |
| Instagram API with Instagram Login | `graph.instagram.com` | Instagram User access token (`IGAA…`) | Not required |
| Instagram API with Facebook Login | `graph.facebook.com` | Facebook User or Page access token | Required |

Instagram Login tokens (`IGAA…`) don't work with Unified Login or with ig-studio-mcp.

### At a glance

- [ ] Instagram account switched to **Creator** or **Business**
- [ ] Meta developer account and a **business portfolio**
- [ ] Instagram account added to the business portfolio, with you having full control
- [ ] App created with the **Manage messaging & content on Instagram** use case
- [ ] App owned by (linked to) the business portfolio
- [ ] Configuration created in **API setup with Unified login**, with `instagram_manage_insights` added
- [ ] Your Instagram account added as an **Instagram Tester**, and the invite accepted
- [ ] Token generated with your Instagram account ticked, and copied straight away
- [ ] `curl` checks pass and the account ID is saved
- [ ] Token in `.env`, which is git-ignored

---

## Part A: Prerequisites

1. **Switch to a professional account.** In the Instagram app: **Settings → Account type and tools → Switch to professional account**, then pick **Creator** or **Business**.
   *Personal accounts have no API access: the Instagram Basic Display API was shut down on 4 December 2024.*
2. **Get a Meta developer account** at [developers.facebook.com](https://developers.facebook.com) (log in with Facebook and accept the developer terms).
3. **Get a business portfolio** at [business.facebook.com](https://business.facebook.com). It's free.
4. **Use Chrome or Edge for Meta's dashboards.** Brave Shields has been seen hiding parts of the dashboard, including **Use cases** (see [Troubleshooting](#troubleshooting)).

## Part B: Add the Instagram account to your business portfolio

5. In [Business settings](https://business.facebook.com/settings), open **Accounts → Instagram accounts** and **add** your Instagram account. Newer layouts show this as **Business assets → Instagram profiles**.
   ✅ Your account appears in the list.
6. Select the account → **People** tab. Make sure **you** have **full control**. If you don't: **Assign people** → choose yourself → **Full control**.
   ✅ You're listed with full control.

## Part C: Create the app and link it to the portfolio

7. On [developers.facebook.com](https://developers.facebook.com/apps), go to **My Apps → Create App**. Choose the use case **Manage messaging & content on Instagram**, and pick **Business** if asked for an app type.
   ✅ The app dashboard opens.
8. **Find the Meta App ID and App Secret.** The App ID is at the top of the dashboard next to the app name, and both are under **App settings → Basic** (click **Show** for the secret).
   ⚠️ Use the **Meta** App ID and secret, *not* the "Instagram app ID/secret" that older setups show. You only need these for optional features; the token alone is enough for ig-studio-mcp.
9. **Make the business portfolio own the app.** Either go to **Business settings → Accounts → Apps → Add → Connect an app ID** and paste the App ID, or go to **App settings → Basic → Business portfolio** in the app dashboard.
   ✅ Meta: *"The API setup with Unified login tab only appears for apps owned by a business portfolio."*

## Part D: Create the login configuration

10. In the app dashboard, go to **Use cases → Manage messaging & content on Instagram → Customize → API setup with Unified login**.
11. Under **Configure your app**, click **Create new configuration**.
    - **Permissions.** The default configuration requests `instagram_basic`, `instagram_manage_messages`, `instagram_manage_comments`, `instagram_content_publish`, `ads_management` and `ads_read`. Click **Edit permissions** and **add `instagram_manage_insights`**: it's *not* included by default, and the insights tools need it. Add `business_management` too if you want to manage tokens through the API ([Refreshing and rotating](#refreshing-and-rotating-the-token)).

      | Permission | ig-studio-mcp uses it for |
      |---|---|
      | `instagram_basic` | Profile and media (required) |
      | `instagram_manage_comments` | Reading, replying to, hiding and deleting comments |
      | `instagram_manage_insights` | Media and account insights (**add by hand**) |
      | `instagram_content_publish` | Publishing images and carousels |

      The old Instagram Login names (`instagram_business_*`) don't apply here. Meta maps them to the names above.
    - **Assets:** make sure the configuration includes an **Instagram account**.
    - **Token expiration:** you choose this in the configuration, and the preview shows it. Meta's Facebook Login for Business docs say these tokens *"default to never expire"*. Pick **Never** if offered; otherwise pick the longest option.
12. Click **Use this configuration**, enter a **Redirect URL** and click **Save**.
    - The redirect URL must be **HTTPS**. ig-studio-mcp never uses it, so any HTTPS URL you control works. A free tunnel (ngrok, Cloudflare Tunnel) also works.
    - **Webhooks** (**Configure webhooks** on the same tab) are optional for ig-studio-mcp. If you do set them up, Meta verifies the endpoint live: it must be HTTPS with a valid (not self-signed) certificate and answer Meta's `hub.challenge` request, so a placeholder URL won't pass.

## Part E: Generate the token

13. On the same tab, go to **Generate access tokens → Add account**. This invites your Instagram account as an **Instagram Tester**. Being a tester is separate from being in the portfolio.
14. **Accept the invite on instagram.com (desktop web):** **Settings → Website permissions → Apps and websites → Tester Invites → Accept**. The mobile app sometimes hides this screen.
15. Back in the dashboard, click **Generate token** → log in → **choose your business portfolio** → **tick your Instagram account** on the asset screen (it's easy to click past!) → skip extra assets such as ad accounts → approve the permissions.
16. **Copy the token immediately: it's shown only once.** Put it straight into a password manager or your `.env`, never into a chat, issue or git.
    ✅ It starts with `EAA…` and is long (150+ characters).

## Part F: Verify

The commands below work in macOS/Linux shells and Windows `cmd`. In **Windows PowerShell 5.1**, type `curl.exe`, because plain `curl` there is an alias for `Invoke-WebRequest`. Keep the **whole URL in double quotes**, and replace `TOKEN` with your token as one unbroken string.

17. **Check the token type:**
    ```bash
    curl "https://graph.facebook.com/v26.0/me?fields=id,name,client_business_id&access_token=TOKEN"
    ```
    ✅ `{"id":"…","name":"<something> System User","client_business_id":"…"}`. A name ending in "System User" confirms the right token type. `/me` is the system user, *not* your Instagram account. Note `client_business_id`: the token management API needs it.
18. **Get your Instagram account ID:**
    ```bash
    curl "https://graph.facebook.com/v26.0/me/assigned_instagram_accounts?fields=id,username&access_token=TOKEN"
    ```
    ✅ `{"data":[{"id":"1784…","username":"your_handle"}],"paging":{…}}`. The `id` is your `IG_ACCOUNT_ID`.
19. **Check that reading works** (replace `IG_ACCOUNT_ID`):
    ```bash
    curl "https://graph.facebook.com/v26.0/IG_ACCOUNT_ID?fields=username,name,biography,followers_count,media_count&access_token=TOKEN"
    curl "https://graph.facebook.com/v26.0/IG_ACCOUNT_ID/media?fields=id,caption,media_type,like_count,comments_count,timestamp,permalink&limit=5&access_token=TOKEN"
    ```
    ✅ Your profile, then your five latest posts.
20. **Check expiry and scopes** in the [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken): **Valid** = True, **Type** = System User, **Expires** = Never (or a future date, which you should write down), and **Scopes** lists your permissions.

<details>
<summary>PowerShell alternative (no curl)</summary>

```powershell
$TOKEN = "PASTE_TOKEN_HERE"
Invoke-RestMethod "https://graph.facebook.com/v26.0/me?fields=id,name,client_business_id&access_token=$TOKEN"
Invoke-RestMethod "https://graph.facebook.com/v26.0/me/assigned_instagram_accounts?fields=id,username&access_token=$TOKEN"
```
</details>

> Commands with the token in them are saved in your shell history. Clear that history afterwards, or skip `curl` and use `uv run ig-studio-mcp check` (next part), which reads the token from `.env` and runs the same checks.

## Part G: Save credentials

In the ig-studio-mcp folder, copy `.env.example` to `.env` and fill it in:

```dotenv
# Required
IG_ACCESS_TOKEN=EAA...

# Optional: only if the token can reach several Instagram accounts
IG_ACCOUNT_ID=

# Optional: Meta App ID / App Secret (App settings -> Basic). Only needed for expiring
# Facebook Login user tokens; if the secret is set, calls also send appsecret_proof.
META_APP_ID=
META_APP_SECRET=
```

`.env` is already in `.gitignore`. In your own projects, add it **before the first commit**, and only ever commit an `.env.example` with empty values. Keep `client_business_id` and the token's expiry date in your password manager next to the token: the server doesn't read them, but you'll want them for rotating the token.

Then check everything end to end:

```bash
uv run ig-studio-mcp check
```

✅ It prints the token type and expiry, any missing permissions, the reachable Instagram accounts, and `OK - using @your_handle …`.

---

## Refreshing and rotating the token

- **If the token never expires**, there's nothing to refresh. `ig-studio-mcp check` shows `never expires`.
- **If it expires**, generate a new one before then: **API setup with Unified login → Generate access tokens → Generate token**, then replace `IG_ACCESS_TOKEN` in `.env`. The server re-reads `.env` on every call, so you don't need to restart it.
- **Through the API:** Meta's *Business integration system user access token management API* is `POST https://graph.facebook.com/v26.0/CLIENT_BUSINESS_ID/system_user_access_tokens`. Pass the current `access_token` and an `appsecret_proof` (SHA-256 HMAC of the token, keyed with your Meta App Secret), and the token needs the `business_management` permission. Optional parameters: `fetch_only=true` returns the existing token, and `set_token_expires_in_60_days=true` makes a new token expire in 60 days. The response is `{"access_token":"…"}`. ig-studio-mcp's `refresh-token` command doesn't call this API yet.

Tokens also stop working if the app is removed from the business portfolio, or the Instagram account moves to another portfolio.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| **Use cases** isn't visible in the dashboard | Brave Shields blocking Meta's scripts; an old-style app that shows "Add product" instead; or a collapsed sidebar | Turn Shields off for developers.facebook.com, business.facebook.com and facebook.com, then hard-refresh. Or use Chrome or Edge. If the app only shows "Products", create a new app. |
| **API setup with Unified login** tab is missing | The app isn't owned by a business portfolio | Link the app to the portfolio (step 9), wait a minute, refresh. |
| Can't find the App ID | Looking for the "Instagram App ID", or the app doesn't exist yet | Use the **Meta** App ID at the top of the dashboard or under **App settings → Basic**. |
| Dashboard asks for Business Verification | A general prompt | Ignore it for your own account and keep the app in **Development** mode. Standard Access covers people with a role on the app. |
| `"An active access token must be used to query information about the current user." … "code":2500` | The token wasn't actually sent: `TOKEN` placeholder left in, a truncated or line-broken paste, or Windows `cmd`/PowerShell mangling single-quoted `curl -d '…'` arguments | Put the whole URL in double quotes with `access_token=` in it (Part F), paste the token as one unbroken string, and check it in the Token Debugger. |
| `/me/assigned_instagram_accounts` returns `{"data":[]}` | The token is valid, but its system user has no Instagram account assigned: it wasn't ticked during **Generate token**, or the configuration lacks the Instagram account asset | **Business settings → Users → System users →** the app's system user (named "… System User") **→ Assign assets → Instagram accounts →** select your account with full control. Run the call again; a new token usually isn't needed. If it's still empty, check that the configuration includes the Instagram account asset, then regenerate the token and tick the account. |
| `/me` has no `client_business_id`, or errors | Wrong token type, such as a personal user token from Graph API Explorer | Generate the token from **API setup with Unified login → Generate access tokens**. |
| `(#10) Access is denied because the instagram account type is not business or creator` | Personal account | Switch to Creator or Business (step 1). |
| Tester invite not visible | The mobile app hides it | Use instagram.com on a desktop: **Settings → Website permissions → Apps and websites → Tester Invites**. |
| Permission error on one specific call (e.g. insights) | That permission isn't in the configuration (`instagram_manage_insights` isn't there by default) | Edit the configuration, add the permission, and generate a new token. |
| `ig-studio-mcp` says the token is an Instagram Login token | The token starts with `IG…`, so it came from "API setup with Instagram login" | Generate the token from **API setup with Unified login** instead. |

## Security notes

- **The token gives full access to your Instagram account.** Treat it like a password.
- Never commit it, or paste it into chats, issues, PRs or screenshots. Keep `.env` in `.gitignore`.
- If it leaks, **generate a new token straight away** and remove the old one. Removing the app from the business portfolio (**Business settings → Integrations → Connected apps**) invalidates its tokens.
- Keep the app in **Development** mode. Advanced Access, App Review and Live mode are only needed if *other people's* accounts will connect.
- ig-studio-mcp never logs the token, scrubs it from error messages, and doesn't return Meta's paging URLs (which contain it).

## References

- [Instagram Platform overview](https://developers.facebook.com/documentation/instagram-platform)
- [Unified Login: Get started](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/get-started)
- [Unified Login: Authentication and access tokens](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/authentication)
- [Unified Login: Permissions](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/permissions)
- [Unified Login: Eligibility and troubleshooting](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/eligibility-and-troubleshooting)
- [Unified Login: Business onboarding](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/business-onboarding)
- [Unified Login: Migration from other setups](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login/migration)
- [Facebook Login for Business (system user token management API)](https://developers.facebook.com/documentation/facebook-login/facebook-login-for-business)
- [Webhooks setup](https://developers.facebook.com/documentation/instagram-platform/webhooks/setup)
- [Content publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing)
- [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken)

---

*Not affiliated with or endorsed by Meta. Instagram is a trademark of Meta Platforms, Inc.*
