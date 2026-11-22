# Connecting YouTube and Instagram: options (for the owner to decide)

Status 22 Nov 2026: posting works through **Zernio** with the user's own Zernio key. Nothing below is
built yet. No real account was posted to while writing this.

Wanted flow: **Connect YouTube / Connect Instagram → sign in → approve → review clip → publish or schedule.**

## What the platforms require (checked 22 Nov 2026)

| | YouTube (Data API v3) | Instagram (API with Instagram Login) |
|---|---|---|
| Accounts | Any channel | Professional (Business or Creator) accounts only |
| Sign-in | Google OAuth. Desktop apps may use the "installed app" flow with PKCE; its client secret isn't truly secret | Meta Business Login. The app secret must stay on a server |
| Approval | **Uploads from unverified API projects are forced to private** until Google audits the project (Audit and Quota Extension form). The upload scope also needs OAuth app verification | **App Review** for `instagram_business_content_publish` (Advanced Access), which can take weeks |
| Limits | Default 10,000 quota units a day **per project**, shared by every user. Docs disagree on whether an upload costs 1,600 or 100 units (check the Console). With 1,600 that's about 6 uploads a day for the whole app until Google raises the quota | 100 API posts per account per 24 h (some pages still say 50; read `content_publishing_limit`) |
| Video | Upload the file directly | Meta must fetch the video from a **public URL**, so a desktop app needs somewhere to host the file temporarily |

## Options

| | A. Keep Zernio | B. Our managed connection | C. Direct, in the app only |
|---|---|---|---|
| User experience | Make a Zernio account, paste a key (first 2 accounts free) | Click Connect, sign in, done | Click Connect, sign in |
| Works today | **Yes** | No | No |
| Our cost | None | A small server (token exchange + temporary video hosting for Instagram) plus upkeep. Cloudflare Workers + R2 is cheap at small scale | None |
| Approvals we need | None (Zernio has them) | Google audit + verification, Meta App Review | Same as B |
| Secrets in the EXE | None | None (they live on our server) | YouTube: OK with PKCE. **Instagram: impossible** without a server (app secret + public video URL) |
| Risk | Depends on Zernio's prices and uptime | We run a service; per-project YouTube quota limits everyone until raised | Instagram can't work this way |

## Recommendation

1. **Ship with A now** and make its setup clearer (one screen, a link, what it costs after 2 accounts).
2. **Add YouTube directly next** (installed-app OAuth with PKCE, token in Windows Credential
   Manager): no server needed. Start the Google audit early, because until it passes, uploads land as
   **private**.
3. **Only do Instagram directly with a server (B).** Do it if many users ask, after Meta App Review.

What it costs us: A is free. YouTube direct is free but takes Google's audit time. B costs a small
monthly server bill and needs both reviews.

Sources: [YouTube quota costs](https://developers.google.com/youtube/v3/determine_quota_cost),
[videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert),
[YouTube audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits),
[Instagram content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/),
[Instagram App Review](https://developers.facebook.com/docs/instagram-platform/app-review),
[publishing limit](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/content_publishing_limit).
