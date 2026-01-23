<p align="center">
  <img src="no_title.jpg" alt="simplekick" width="360">
</p>
<p align="center"><span style="color:#8a8f98;">"Deafen mode? Cool. Exit mode’s automated."</span></p>

<h1 align="center">simplekick</h1>

Discord bot auto-disconnects members who self-deafen in a voice channel. Includes owner-only blacklist commands and temporary exemption requests.

## Requirements

- Python 3.10+
- A Discord bot with `Move Members` permission
- Enable "Server Members Intent" in the Developer Portal

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Configure environment variables (example in `.env.example`):

```bash
export DISCORD_TOKEN="your_bot_token"
export OWNER_ID="your_user_id" # optional, avoids owner lookup
export GITHUB_REPO="akikohatsune/simplekick" # optional, check updates on startup
export GUILD_ID="your_guild_id" # optional, faster slash command sync
export DB_PATH="blacklist.db"   # optional
export PRESENCE_TEXT="Auto-disconnect self-deafen" # optional, bot status
export AUTO_UPDATE="1" # optional, auto-update from latest release
export UPDATE_INTERVAL_SECONDS="300" # optional, check interval in seconds
```

3. Run the bot:

```bash
python bot.py
```

## Slash Commands

- `/blacklist add user_id reason` - exempt a user from auto-disconnect (owner-only)
- `/blacklist remove user_id` - remove exemption (owner-only)
- `/blacklist list` - list exemptions (owner-only)
- `/exempt request seconds reason` - ask owner for a temporary exemption (DM to owner)
- `/exempt grant user seconds reason` - grant temporary exemption (owner-only, DM user)
- `/exempt deny user reason` - deny exemption request (owner-only, DM user)
- `/sync [guild_id]` - sync global commands (and guild if provided) (owner-only)
- `!sync [guild_id]` - sync global commands (and guild if provided) (owner-only, prefix)
- `!update` - check and apply latest release immediately (owner-only)

## Notes

- If `GUILD_ID` is not set, global slash command sync can take up to 1 hour.
- Set `GITHUB_REPO` to override the default update repository.
- Update check uses the latest GitHub release.
- Auto-update pulls the latest release tag and restarts the bot.
- Update checks repeat every 5 minutes by default.
- Python 3.13 is supported; voice audio features require `audioop-lts` or Python 3.12.
- `.env` is loaded automatically on startup.
- On startup, the bot will auto-disconnect members who are already self-deaf.
- Set `PRESENCE_TEXT` to show a custom Discord presence.
