import logging

import discord
from discord.ext import commands


logger = logging.getLogger("simplekick.voice")


class VoiceKickCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return
        if after.channel is None:
            return
        if not after.self_deaf:
            return
        if before.self_deaf and before.channel == after.channel:
            return
        if self.bot.db.is_blacklisted(member.guild.id, member.id):
            return
        if self.bot.db.is_temp_exempt(member.guild.id, member.id):
            return

        guild_me = member.guild.me or (
            member.guild.get_member(self.bot.user.id) if self.bot.user else None
        )
        if not guild_me or not guild_me.guild_permissions.move_members:
            logger.warning("Missing Move Members permission in guild %s", member.guild.id)
            return

        try:
            await member.move_to(None, reason="Auto-disconnect: self-deaf in voice channel")
            logger.info("Disconnected %s (%s) for self-deaf", member, member.id)
        except discord.Forbidden:
            logger.warning("Forbidden to disconnect %s (%s)", member, member.id)
        except discord.HTTPException:
            logger.exception("Failed to disconnect %s (%s)", member, member.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceKickCog(bot))
