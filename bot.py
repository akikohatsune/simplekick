import asyncio
import logging
import os

import discord
from discord.ext import commands

from db import Database
from update_checker import check_for_updates


TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

DB_PATH = os.getenv("DB_PATH", "blacklist.db")
SYNC_GUILD_ID = os.getenv("GUILD_ID") or os.getenv("SYNC_GUILD_ID")
OWNER_ID = os.getenv("OWNER_ID")
GITHUB_REPO = os.getenv("GITHUB_REPO")
CURRENT_VERSION = "0.2.0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("simplekick")

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True


class SimpleKickBot(commands.Bot):
    def __init__(self) -> None:
        owner_id = int(OWNER_ID) if OWNER_ID else None
        super().__init__(command_prefix="!", intents=intents, owner_id=owner_id)
        self.db = Database(DB_PATH)
        self.allowed_role_id: int | None = None
        self.setup_locked = False
        self.sync_guild_id = SYNC_GUILD_ID

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.voice_kick")
        await self.load_extension("cogs.admin")
        if GITHUB_REPO:
            self.loop.create_task(self._check_updates())

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")
        await self._sync_commands()

    async def _sync_commands(self) -> None:
        try:
            if self.sync_guild_id:
                guild = discord.Object(id=int(self.sync_guild_id))
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info("Synced %d commands to guild %s", len(synced), self.sync_guild_id)
            else:
                synced = await self.tree.sync()
                logger.info("Synced %d global commands", len(synced))
        except Exception:
            logger.exception("Failed to sync commands")

    async def _check_updates(self) -> None:
        await asyncio.to_thread(check_for_updates, CURRENT_VERSION, GITHUB_REPO, logger)


bot = SimpleKickBot()
bot.run(TOKEN)
