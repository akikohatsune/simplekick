import asyncio
import logging
import os
import sys
import types

if sys.version_info >= (3, 13):
    try:
        import audioop  # noqa: F401
    except ModuleNotFoundError:
        audioop = types.ModuleType("audioop")

        def _unsupported(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError(
                "audioop is not available on Python 3.13. "
                "Install audioop-lts or use Python 3.12 if you need voice audio."
            )

        for _name in (
            "ratecv",
            "tomono",
            "tostereo",
            "add",
            "bias",
            "mul",
            "max",
            "minmax",
            "avg",
            "rms",
            "findfit",
            "findfactor",
            "reverse",
            "lin2lin",
            "adpcm2lin",
            "lin2adpcm",
            "getsample",
        ):
            setattr(audioop, _name, _unsupported)
        sys.modules["audioop"] = audioop

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db import Database
from update_checker import check_for_updates
from update_manager import perform_update


load_dotenv()


TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

DB_PATH = os.getenv("DB_PATH", "blacklist.db")
SYNC_GUILD_ID = os.getenv("GUILD_ID") or os.getenv("SYNC_GUILD_ID")
OWNER_ID = os.getenv("OWNER_ID")
GITHUB_REPO = os.getenv("GITHUB_REPO", "akikohatsune/simplekick")
CURRENT_VERSION = "1"
AUTO_UPDATE = os.getenv("AUTO_UPDATE", "1").lower() not in {"0", "false", "no"}
UPDATE_INTERVAL_SECONDS = int(os.getenv("UPDATE_INTERVAL_SECONDS", "300"))
PRESENCE_TEXT = os.getenv("PRESENCE_TEXT", "Auto-disconnect self-deafen")

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
        self.sync_guild_id = SYNC_GUILD_ID

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.voice_kick")
        await self.load_extension("cogs.admin")
        if GITHUB_REPO:
            self.loop.create_task(self._update_loop())

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")
        if PRESENCE_TEXT:
            await self.change_presence(activity=discord.Game(name=PRESENCE_TEXT))
        await self._sync_commands()
        cog = self.get_cog("VoiceKickCog")
        if cog and hasattr(cog, "scan_voice_states"):
            await cog.scan_voice_states()

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

    async def _check_updates(self, force: bool = False) -> str:
        update_info = await asyncio.to_thread(
            check_for_updates, CURRENT_VERSION, GITHUB_REPO, logger
        )
        if not update_info:
            return "no_update"
        if not AUTO_UPDATE and not force:
            logger.info("AUTO_UPDATE disabled; skipping update.")
            return "auto_disabled"
        latest_tag, _url = update_info
        updated = await asyncio.to_thread(perform_update, latest_tag, logger)
        if updated:
            logger.info("Update applied; restarting.")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        return "update_failed"

    async def _update_loop(self) -> None:
        while True:
            await self._check_updates()
            await asyncio.sleep(max(60, UPDATE_INTERVAL_SECONDS))


bot = SimpleKickBot()
bot.run(TOKEN)
