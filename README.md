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
export BOT_VERSION="1.3.4" # optional, local version label used by /ver
export GITHUB_REPO="akikohatsune/simplekick" # optional, repo checked by /ver
export GUILD_ID="your_guild_id" # optional, faster slash command sync
export DB_PATH="blacklist.db"   # optional
export PRESENCE_TEXT="Auto-disconnect self-deafen" # optional, bot status
export VOICE_ENHANCED_GUARD="1" # optional, extra guard algorithm layer
export VOICE_GUARD_INTERVAL_SECONDS="45" # optional, periodic sweep interval
export VOICE_VERIFY_DELAYS_SECONDS="2,5" # optional, delayed verification passes
```

3. Run the bot:

```bash
python main.py
```

## Slash Commands

- `/blacklist add user_id reason` - exempt a user from auto-disconnect (owner-only)
- `/blacklist remove user_id` - remove exemption (owner-only)
- `/blacklist list` - list exemptions (owner-only)
- `/exempt request seconds reason` - ask owner for a temporary exemption (DM to owner)
- `/exempt grant user seconds reason` - grant temporary exemption (owner-only, DM user)
- `/exempt deny user reason` - deny exemption request (owner-only, DM user)
- `/ver` - show local bot version and latest GitHub release
- `/sync [guild_id]` - sync global commands (and guild if provided) (owner-only)
- `!sync [guild_id]` - sync global commands (and guild if provided) (owner-only, prefix)

## Notes

- If `GUILD_ID` is not set, global slash command sync can take up to 1 hour.
- Python 3.13 is supported; voice audio features require `audioop-lts` or Python 3.12.
- `.env` is loaded automatically on startup.
- On startup, the bot will auto-disconnect members who are already self-deaf.
- Set `PRESENCE_TEXT` to show a custom Discord presence.
- Set `GITHUB_REPO` as `owner/repo` to let `/ver` query latest release.
- Enhanced voice guard keeps base algorithm and adds retry/sweep checks.
