import asyncio
import logging
import os

import discord
from discord.ext import commands


logger = logging.getLogger("simplekick.voice")


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _parse_int(raw: str | None, default: int, minimum: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value >= minimum else minimum


def _parse_delays(raw: str | None, default: tuple[float, ...]) -> tuple[float, ...]:
    if not raw:
        return default
    values: list[float] = []
    for chunk in raw.split(","):
        text = chunk.strip()
        if not text:
            continue
        try:
            delay = float(text)
        except ValueError:
            continue
        if delay > 0:
            values.append(delay)
    return tuple(values) if values else default


class VoiceKickCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._enhanced_guard_enabled = _parse_bool(
            os.getenv("VOICE_ENHANCED_GUARD"), default=True
        )
        self._guard_interval_seconds = _parse_int(
            os.getenv("VOICE_GUARD_INTERVAL_SECONDS"), default=45, minimum=10
        )
        self._verify_delays_seconds = _parse_delays(
            os.getenv("VOICE_VERIFY_DELAYS_SECONDS"), default=(2.0, 5.0)
        )
        self._guard_task: asyncio.Task[None] | None = None
        self._verify_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}

    async def cog_load(self) -> None:
        if not self._enhanced_guard_enabled:
            return
        if self._guard_task and not self._guard_task.done():
            return
        self._guard_task = asyncio.create_task(self._guard_loop(), name="simplekick-voice-guard")
        logger.info(
            "Enhanced voice guard enabled (interval=%ss, delays=%s)",
            self._guard_interval_seconds,
            ", ".join(str(x) for x in self._verify_delays_seconds),
        )

    def cog_unload(self) -> None:
        if self._guard_task:
            self._guard_task.cancel()
            self._guard_task = None
        for task in self._verify_tasks.values():
            task.cancel()
        self._verify_tasks.clear()

    def _get_member(self, guild_id: int, member_id: int) -> discord.Member | None:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return None
        return guild.get_member(member_id)

    def _schedule_verify(self, guild_id: int, member_id: int) -> None:
        if not self._enhanced_guard_enabled:
            return
        key = (guild_id, member_id)
        existing = self._verify_tasks.get(key)
        if existing and not existing.done():
            return
        task = asyncio.create_task(
            self._verify_worker(guild_id, member_id), name=f"simplekick-verify-{guild_id}-{member_id}"
        )
        self._verify_tasks[key] = task

        def _cleanup(_task: asyncio.Task[None], task_key: tuple[int, int] = key) -> None:
            self._verify_tasks.pop(task_key, None)

        task.add_done_callback(_cleanup)

    async def _verify_worker(self, guild_id: int, member_id: int) -> None:
        try:
            for delay in self._verify_delays_seconds:
                await asyncio.sleep(delay)
                member = self._get_member(guild_id, member_id)
                if not member:
                    return
                await self._maybe_disconnect(
                    member, "Auto-disconnect: self-deaf verification pass"
                )
                if not member.voice or not member.voice.channel or not member.voice.self_deaf:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Verification task failed for %s/%s", guild_id, member_id)

    async def _guard_loop(self) -> None:
        try:
            await self.bot.wait_until_ready()
            while True:
                await asyncio.sleep(self._guard_interval_seconds)
                await self.scan_voice_states(reason="Auto-disconnect: periodic guard sweep")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Enhanced guard loop stopped unexpectedly")

    async def _notify_user(self, member: discord.Member) -> None:
        try:
            await member.send(
                "You were disconnected because you self-deafened in a voice channel. "
                "Please undeafen before rejoining. If you need time, use /exempt request."
            )
        except discord.Forbidden:
            return
        except discord.HTTPException:
            logger.exception("Failed to DM %s (%s)", member, member.id)

    def _get_guild_me(self, guild: discord.Guild) -> discord.Member | None:
        guild_me = guild.me or (guild.get_member(self.bot.user.id) if self.bot.user else None)
        if not guild_me or not guild_me.guild_permissions.move_members:
            logger.warning("Missing Move Members permission in guild %s", guild.id)
            return None
        return guild_me

    async def _maybe_disconnect(self, member: discord.Member, reason: str) -> None:
        if member.bot:
            return
        if not member.voice or not member.voice.channel:
            return
        if not member.voice.self_deaf:
            return
        if self.bot.db.is_blacklisted(member.guild.id, member.id):
            return
        if self.bot.db.is_temp_exempt(member.guild.id, member.id):
            return
        if not self._get_guild_me(member.guild):
            return

        try:
            await member.move_to(None, reason=reason)
            logger.info("Disconnected %s (%s) for self-deaf", member, member.id)
            await self._notify_user(member)
        except discord.Forbidden:
            logger.warning("Forbidden to disconnect %s (%s)", member, member.id)
        except discord.HTTPException:
            logger.exception("Failed to disconnect %s (%s)", member, member.id)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if after.channel is None:
            return
        if not after.self_deaf:
            return
        if before.self_deaf and before.channel == after.channel:
            return
        await self._maybe_disconnect(member, "Auto-disconnect: self-deaf in voice channel")
        self._schedule_verify(member.guild.id, member.id)

    async def scan_voice_states(self, reason: str = "Auto-disconnect: self-deaf on startup") -> None:
        for guild in self.bot.guilds:
            if not self._get_guild_me(guild):
                continue
            for channel in guild.voice_channels:
                for member in channel.members:
                    if not member.voice or not member.voice.self_deaf:
                        continue
                    await self._maybe_disconnect(member, reason)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceKickCog(bot))
