import logging
import os
import sys
import types
from collections.abc import Sequence


def _ensure_audioop_compat() -> None:
    if sys.version_info < (3, 13):
        return
    try:
        import audioop  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    fallback = types.ModuleType("audioop")

    def _unsupported(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(
            "audioop is not available on Python 3.13. "
            "Install audioop-lts or use Python 3.12 if you need voice audio."
        )

    for name in (
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
        setattr(fallback, name, _unsupported)
    sys.modules["audioop"] = fallback


_ensure_audioop_compat()

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db import Database


load_dotenv()


TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("TOKEN")
if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

DB_PATH = os.getenv("DB_PATH", "blacklist.db")
SYNC_GUILD_ID = os.getenv("GUILD_ID") or os.getenv("SYNC_GUILD_ID")
OWNER_ID = os.getenv("OWNER_ID")
BOT_VERSION = os.getenv("BOT_VERSION", "1.3.4")
GITHUB_REPO = os.getenv("GITHUB_REPO", "akikohatsune/simplekick")
PRESENCE_TEXT = os.getenv("PRESENCE_TEXT", "Auto-disconnect self-deafen")
EXTENSIONS: Sequence[str] = ("cogs.voice_kick", "cogs.admin", "cogs.version")

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
        self.bot_version = BOT_VERSION
        self.github_repo = GITHUB_REPO

    async def setup_hook(self) -> None:
        for extension in EXTENSIONS:
            await self.load_extension(extension)

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

bot = SimpleKickBot()
bot.run(TOKEN)
