# 9adam bot

A Discord moderation bot with AI-powered message filtering, moderation commands,
appeals, warnings, and anti-nuke protection.

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Create a `.env` file with your tokens:

   ```env
   DISCORD_TOKEN=your-bot-token
   OPENAI_API_KEY=your-openai-key
   ```

4. Start the bot with `python 9adam.py`.

## Deploy to Railway (24/7 hosting)

The bot runs as a long-lived worker, so hosting it on Railway keeps it online
around the clock without you running anything locally. The repo already includes
`railway.json`, a `Procfile`, and `runtime.txt` — no Dockerfile needed.

### Step 1 — Push the code

Make sure the latest code is on GitHub (the repo already exists):

```bash
git add -A
git commit -m "Update bot"
git push origin master
```

### Step 2 — Create the project on Railway

1. Go to [railway.app](https://railway.app) and sign in.
2. Click **New Project → Deploy from GitHub repo**.
3. Select your `9ADAMBOT` repository.
4. Railway auto-detects the Python app and runs `python 9adam.py` via Nixpacks.

### Step 3 — Set environment variables

In the project's **Variables** tab, add:

| Variable | Value |
| --- | --- |
| `DISCORD_TOKEN` | Your bot token from the Discord Developer Portal |
| `OPENAI_API_KEY` | Your OpenAI API key (optional — filter still works on the local slur list without it) |
| `DATA_DIR` | `/data` (required so data survives redeploys) |

### Step 4 — Add a persistent volume

Railway's filesystem is **wiped on every redeploy**, so the SQLite databases
(`moderation.db` for warnings, `filter.db` for filter settings and exclusions,
`anti_nuke.db` for anti-nuke thresholds) must live on a persistent volume:

1. Open your service → **Volumes** tab.
2. Click **New Volume**, mount it at `/data`, and give it a reasonable size (1 GB is plenty).
3. Restart the service.

The code reads the `DATA_DIR` env variable and stores all databases there.

### Step 5 — Invite the bot to your server

Use the Discord Developer Portal to generate an invite URL with these permissions:

- Manage Messages, Read Message History
- Kick Members, Ban Members, Moderate Members
- Manage Channels, Manage Roles
- View Audit Log (required for anti-nuke to attribute actions)
- Send Messages, Embed Links

### Step 6 — Verify

Check the **Deployments → Logs** tab. You should see:

```
[startup] OpenAI moderation client enabled.
Logged in as <bot name> (ID: ...)
Slash commands synced for <guild name>.
```

The bot is now online 24/7. Restarts and redeploys preserve warnings, custom
filter words, and filter settings thanks to the volume.

### Notes

- **Restart policy**: `railway.json` sets `restartPolicyType: ON_FAILURE`, so the
  bot automatically restarts if it ever crashes.
- **Slash commands**: they sync automatically on startup and when the bot joins a
  new guild.
- **Logs**: all bot prints go to stdout and appear in Railway's logs tab.
