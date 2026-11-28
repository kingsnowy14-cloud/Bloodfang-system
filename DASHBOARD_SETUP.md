# IronWolf Control Center — Setup

The Control Center runs alongside the Discord bot in the same Railway service. It reads and updates the existing server-specific `config.json`. Existing ticket settings and persistent component IDs are preserved.

## 1. Configure Railway variables

Open your IronWolf project in Railway → Service → Variables and set:

- `DASHBOARD_ENABLED=true`
- `DASHBOARD_PUBLIC_URL=https://YOUR-APP-DOMAIN`
- `DASHBOARD_REDIRECT_URI=https://YOUR-APP-DOMAIN/callback`
- `DASHBOARD_SECRET_KEY=YOUR-LONG-RANDOM-SECRET`
- `DISCORD_CLIENT_ID=YOUR-APPLICATION-ID`
- `DISCORD_CLIENT_SECRET=YOUR-OAUTH2-CLIENT-SECRET`

Use the actual public HTTPS domain assigned to your Railway app. Do not invent a domain; copy it from Railway.

Generate a strong secret locally, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Never commit the secret key or OAuth client secret to GitHub or share them publicly.

## 2. Configure Discord OAuth2

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and select the same Discord application used by your bot.
2. Open **OAuth2** and add the exact value of `DASHBOARD_REDIRECT_URI` under **Redirects**.
3. Copy the Application ID into `DISCORD_CLIENT_ID`.
4. Generate or copy the OAuth2 Client Secret into `DISCORD_CLIENT_SECRET`.
5. Save the settings and restart or redeploy the Railway service.

The dashboard requests only the `identify` and `guilds` OAuth2 scopes. It never requests your bot token. It displays only servers where the signed-in Discord user has **Manage Server** or **Administrator** permissions and the bot is already a member.

## 3. Features

- **New servers start disabled:** When IronWolf is added to a new Discord server, it does not perform server actions, commands, or background tasks there. Open **General Settings** in the Control Center and enable **Enable IronWolf on this server** to activate it.
- **Existing servers remain unchanged:** Servers already present in the configuration keep their previous state. Missing legacy configuration values continue to behave as before.
- Edit server settings using forms, including welcome messages, selected channels and roles, and existing protection controls.
- Enable or disable Cog modules per server.
- Enable or disable prefix and slash commands per server.
- Create a new `discord.py` Cog template and download it as a Python file.
- In Discord, `/module-manager` displays the dashboard link and `/module-status` displays module status.

## 4. New modules

The module generator creates a **template** that you should review and customize before use. It is intentionally not executed automatically. To add an extension to production, review it, add it to the GitHub project, and load it securely. Never execute Python code merely because it was submitted through a form.

## Notes

- The dashboard uses Discord OAuth2 and checks server-specific management permissions on every server page.
- Keep `DASHBOARD_SECRET_KEY` unchanged; otherwise, existing browser sessions become invalid.
- Settings are written to the existing `config.json`. Back up your Railway data volume before major changes.
- The dashboard runs in the same service on the `PORT` provided by Railway.
- Slash commands must be synchronized after deployment. If `/module-manager` and `/module-status` do not appear, check the Railway logs and the `GUILD_ID` variable.

## Website Verification (Discord OAuth2)

To enable the third verification method in the dashboard, set the Railway variable `WEBSITE_VERIFY_REDIRECT_URI` to the public HTTPS callback URL:

```text
https://YOUR-APP-DOMAIN/verify/website/callback
```

Add this exact URL to the Discord Developer Portal under OAuth2 Redirects for the same application used by IronWolf. Keep `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, and `DASHBOARD_SECRET_KEY` configured. The dashboard will then offer **Website Verify** as a third method. Links are single-use and expire after 10 minutes; the Discord account must match the member who requested the link.
