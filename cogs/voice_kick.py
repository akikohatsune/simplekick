import logging

import discord
from discord.ext import commands


logger = logging.getLogger("simplekick.voice")


class VoiceKickCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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

    async def scan_voice_states(self) -> None:
        for guild in self.bot.guilds:
            if not self._get_guild_me(guild):
                continue
            for channel in guild.voice_channels:
                for member in channel.members:
                    if not member.voice or not member.voice.self_deaf:
                        continue
                    await self._maybe_disconnect(
                        member, "Auto-disconnect: self-deaf on startup"
                    )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceKickCog(bot))
