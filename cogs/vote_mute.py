import logging
import math
import os
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db import VoiceMutePoll


logger = logging.getLogger("simplekick.vote_mute")
VOTE_THRESHOLD = 0.30
MAX_MUTE_DAYS = 365


def _parse_poll_seconds(raw: str | None) -> int:
    if raw is None:
        return 600
    try:
        value = int(raw.strip())
    except ValueError:
        return 600
    return min(max(value, 60), 86400)


def _required_votes(eligible_voters: int) -> int:
    return max(1, math.ceil(eligible_voters * VOTE_THRESHOLD))


def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        return await interaction.client.is_owner(interaction.user)

    return app_commands.check(predicate)


class VoiceMuteVoteButton(discord.ui.Button):
    def __init__(self, poll_id: int, disabled: bool = False) -> None:
        super().__init__(
            label="Vote to server mute",
            style=discord.ButtonStyle.danger,
            custom_id=f"voice-mute:vote:{poll_id}",
            emoji="🔇",
            disabled=disabled,
        )
        self.poll_id = poll_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, VoiceMuteVoteView):
            await interaction.response.send_message("This vote is unavailable.", ephemeral=True)
            return
        await self.view.cog.handle_vote(interaction, self.poll_id)


class VoiceMuteVoteView(discord.ui.View):
    def __init__(self, cog: "VoteMuteCog", poll_id: int, disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.poll_id = poll_id
        self.add_item(VoiceMuteVoteButton(poll_id, disabled=disabled))

    def disable(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True


class VoteMuteCog(commands.Cog):
    votemute = app_commands.Group(
        name="votemute",
        description="Vote to server-mute a member for a fixed number of days.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.poll_seconds = _parse_poll_seconds(os.getenv("VOTE_MUTE_POLL_SECONDS"))
        self._views: dict[int, VoiceMuteVoteView] = {}

    async def cog_load(self) -> None:
        for poll in self.bot.db.list_open_voice_mute_polls():
            if poll.message_id is None:
                self.bot.db.cancel_voice_mute_poll(poll.id)
                continue
            view = VoiceMuteVoteView(self, poll.id)
            self._views[poll.id] = view
            self.bot.add_view(view, message_id=poll.message_id)
        self.maintenance.start()

    def cog_unload(self) -> None:
        self.maintenance.cancel()
        for view in self._views.values():
            view.stop()
        self._views.clear()

    def _get_bot_member(self, guild: discord.Guild) -> discord.Member | None:
        if guild.me:
            return guild.me
        if self.bot.user:
            return guild.get_member(self.bot.user.id)
        return None

    def _can_moderate(self, guild: discord.Guild, target: discord.Member) -> str | None:
        bot_member = self._get_bot_member(guild)
        if not bot_member or not bot_member.guild_permissions.mute_members:
            return "I need the **Mute Members** permission before a vote can be opened."
        if target == guild.owner:
            return "The server owner cannot be server-muted."
        if bot_member.top_role <= target.top_role:
            return "My highest role must be above the target member's highest role."
        return None

    def _poll_embed(self, poll: VoiceMutePoll) -> discord.Embed:
        colors = {
            "open": discord.Color.orange(),
            "passed": discord.Color.red(),
            "expired": discord.Color.light_grey(),
            "cancelled": discord.Color.light_grey(),
        }
        titles = {
            "open": "Server-mute vote",
            "passed": "Server-mute vote passed",
            "expired": "Server-mute vote expired",
            "cancelled": "Server-mute vote cancelled by owner",
        }
        embed = discord.Embed(
            title=titles.get(poll.status, "Server-mute vote"),
            color=colors.get(poll.status, discord.Color.light_grey()),
        )
        embed.add_field(name="Target", value=f"<@{poll.target_id}>", inline=True)
        embed.add_field(name="Mute duration", value=f"{poll.duration_days} day(s)", inline=True)
        embed.add_field(
            name="Votes",
            value=(
                f"**{poll.vote_count}/{poll.required_votes}** needed to pass\n"
                f"30% of {poll.eligible_voters} eligible member(s)"
            ),
            inline=False,
        )
        embed.add_field(name="Reason", value=poll.reason or "No reason provided", inline=False)
        if poll.status == "open":
            embed.add_field(name="Vote closes", value=f"<t:{poll.ends_at}:R>", inline=True)
            embed.description = "Press the button once to vote. Bots and the target cannot vote."
        elif poll.status == "passed":
            active_mute = self.bot.db.get_active_voice_mute(poll.guild_id, poll.target_id)
            if active_mute:
                embed.add_field(
                    name="Mute expires",
                    value=f"<t:{active_mute.expires_at}:F> (<t:{active_mute.expires_at}:R>)",
                    inline=False,
                )
            embed.description = (
                "The target is server-muted while in voice. The mute is automatically "
                "re-applied if they reconnect before it expires."
            )
        elif poll.status == "expired":
            embed.description = "The vote did not reach the required 30% threshold in time."
        embed.set_footer(text=f"Opened by user {poll.created_by} • Poll #{poll.id}")
        return embed

    async def _set_server_mute(
        self, member: discord.Member, muted: bool, audit_reason: str
    ) -> tuple[bool, str]:
        if not member.voice or not member.voice.channel:
            return False, "The member is not in a voice channel."
        try:
            await member.edit(mute=muted, reason=audit_reason)
            return True, "Server mute updated."
        except discord.Forbidden:
            logger.warning(
                "Forbidden to set server mute=%s for %s/%s",
                muted,
                member.guild.id,
                member.id,
            )
            return False, "Discord denied the action; check Mute Members and role order."
        except discord.HTTPException:
            logger.exception(
                "Failed to set server mute=%s for %s/%s",
                muted,
                member.guild.id,
                member.id,
            )
            return False, "Discord returned an error while updating the server mute."

    def _stop_view(self, poll_id: int) -> VoiceMuteVoteView:
        view = self._views.pop(poll_id, None) or VoiceMuteVoteView(self, poll_id, disabled=True)
        view.disable()
        view.stop()
        return view

    async def _edit_poll_message(self, poll_id: int) -> None:
        poll = self.bot.db.get_voice_mute_poll(poll_id)
        if not poll or poll.message_id is None:
            return
        channel = self.bot.get_channel(poll.channel_id)
        if not channel or not hasattr(channel, "fetch_message"):
            return
        try:
            message = await channel.fetch_message(poll.message_id)
            if poll.status == "open":
                view = self._views.get(poll.id)
                if view is None:
                    view = VoiceMuteVoteView(self, poll.id)
                    self._views[poll.id] = view
                await message.edit(embed=self._poll_embed(poll), view=view)
            else:
                await message.edit(embed=self._poll_embed(poll), view=self._stop_view(poll.id))
        except (discord.Forbidden, discord.NotFound):
            logger.warning("Could not update message for voice-mute poll %s", poll.id)
        except discord.HTTPException:
            logger.exception("Failed to update message for voice-mute poll %s", poll.id)

    async def _enforce_active_mute(self, guild_id: int, user_id: int) -> tuple[bool, str]:
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return False, "The guild is unavailable."
        member = guild.get_member(user_id)
        if not member:
            return False, "The member is no longer in the server."
        if not member.voice or not member.voice.channel:
            return False, "Mute is saved and will be applied when the member joins voice."
        if member.voice.mute:
            return True, "The member is already server-muted."
        return await self._set_server_mute(member, True, "Voice-mute vote reached 30%")

    async def handle_vote(self, interaction: discord.Interaction, poll_id: int) -> None:
        poll = self.bot.db.get_voice_mute_poll(poll_id)
        if not poll or not interaction.guild or interaction.guild.id != poll.guild_id:
            await interaction.response.send_message("This vote is unavailable.", ephemeral=True)
            return
        if poll.status != "open":
            await interaction.response.send_message("This vote is already closed.", ephemeral=True)
            await self._edit_poll_message(poll_id)
            return
        if interaction.user.id == poll.target_id:
            await interaction.response.send_message(
                "The target cannot vote in this poll.", ephemeral=True
            )
            return
        member = interaction.user
        if not isinstance(member, discord.Member) or member.bot:
            await interaction.response.send_message(
                "Only human server members can vote.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        result = self.bot.db.cast_voice_mute_vote(poll_id, interaction.user.id)
        updated_poll = self.bot.db.get_voice_mute_poll(poll_id)
        if updated_poll:
            if updated_poll.status == "open":
                view = self._views.get(poll_id) or VoiceMuteVoteView(self, poll_id)
                self._views[poll_id] = view
                try:
                    await interaction.message.edit(embed=self._poll_embed(updated_poll), view=view)
                except discord.HTTPException:
                    logger.exception("Failed to refresh voice-mute poll %s", poll_id)
            else:
                try:
                    await interaction.message.edit(
                        embed=self._poll_embed(updated_poll), view=self._stop_view(poll_id)
                    )
                except discord.HTTPException:
                    logger.exception("Failed to close voice-mute poll %s", poll_id)

        if result.status == "already_voted":
            await interaction.followup.send("You already voted in this poll.", ephemeral=True)
            return
        if result.status == "accepted":
            await interaction.followup.send(
                f"Vote counted: {result.vote_count}/{result.required_votes}.", ephemeral=True
            )
            return
        if result.status == "passed":
            applied, detail = await self._enforce_active_mute(poll.guild_id, poll.target_id)
            prefix = "The 30% threshold was reached. "
            if applied:
                await interaction.followup.send(prefix + detail, ephemeral=True)
            else:
                await interaction.followup.send(
                    prefix + detail + " The saved mute remains active until it expires.",
                    ephemeral=True,
                )
            return
        if result.status == "expired":
            await interaction.followup.send(
                "The poll expired before this vote was cast.", ephemeral=True
            )
            return
        await interaction.followup.send("This vote is already closed.", ephemeral=True)

    @votemute.command(name="start", description="Open a vote to server-mute a member.")
    @app_commands.guild_only()
    @app_commands.describe(
        user="Member to server-mute",
        days="Mute duration in days (chosen by OWNER_ID)",
        reason="Optional reason shown in the poll",
    )
    @owner_only()
    async def start_vote(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        days: app_commands.Range[int, 1, MAX_MUTE_DAYS],
        reason: str | None = None,
    ) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        if not guild or interaction.channel_id is None:
            await interaction.followup.send(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if user.bot:
            await interaction.followup.send("Bots cannot be targeted by this vote.", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.followup.send(
                "You cannot open a vote against yourself.", ephemeral=True
            )
            return
        if reason and len(reason) > 400:
            await interaction.followup.send(
                "Reason must be 400 characters or fewer.", ephemeral=True
            )
            return
        permission_error = self._can_moderate(guild, user)
        if permission_error:
            await interaction.followup.send(permission_error, ephemeral=True)
            return

        if not guild.chunked:
            try:
                await guild.chunk(cache=True)
            except (discord.HTTPException, discord.ClientException):
                logger.warning("Could not fully load members for guild %s", guild.id)
        eligible_ids = {
            member.id for member in guild.members if not member.bot and member.id != user.id
        }
        eligible_ids.add(interaction.user.id)
        eligible_voters = len(eligible_ids)
        if eligible_voters < 1:
            await interaction.followup.send(
                "There are no eligible voters in this server.", ephemeral=True
            )
            return
        required = _required_votes(eligible_voters)
        now = int(time.time())
        for expired_poll_id in self.bot.db.expire_voice_mute_polls(now):
            await self._edit_poll_message(expired_poll_id)
        poll_id, error = self.bot.db.create_voice_mute_poll(
            guild.id,
            interaction.channel_id,
            user.id,
            interaction.user.id,
            days,
            reason,
            now + self.poll_seconds,
            eligible_voters,
            required,
        )
        if error == "already_muted":
            await interaction.followup.send(
                f"{user.mention} already has an active vote mute.", ephemeral=True
            )
            return
        if error == "poll_open":
            await interaction.followup.send(
                f"An open vote already targets {user.mention}.", ephemeral=True
            )
            return
        if poll_id is None:
            await interaction.followup.send("Could not create the vote.", ephemeral=True)
            return

        poll = self.bot.db.get_voice_mute_poll(poll_id)
        if not poll:
            await interaction.followup.send("Could not load the new vote.", ephemeral=True)
            return
        view = VoiceMuteVoteView(self, poll_id)
        self._views[poll_id] = view
        try:
            message = await interaction.followup.send(
                content=f"Vote opened for {user.mention}",
                embed=self._poll_embed(poll),
                view=view,
                wait=True,
            )
        except discord.HTTPException:
            self.bot.db.cancel_voice_mute_poll(poll_id)
            self._stop_view(poll_id)
            logger.exception("Failed to publish voice-mute poll %s", poll_id)
            await interaction.followup.send("Could not publish the vote message.", ephemeral=True)
            return
        self.bot.db.set_voice_mute_poll_message(poll_id, message.id)

    @votemute.command(
        name="force-unmute",
        description="Immediately remove a vote mute and cancel any open vote for a member.",
    )
    @app_commands.guild_only()
    @app_commands.describe(user="Member to unmute", reason="Optional audit-log reason")
    @owner_only()
    async def force_unmute(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            await interaction.followup.send(
                "This command can only be used in a server.", ephemeral=True
            )
            return
        if reason and len(reason) > 400:
            await interaction.followup.send(
                "Audit-log reason must be 400 characters or fewer.", ephemeral=True
            )
            return

        removed = self.bot.db.remove_voice_mute(guild.id, user.id)
        cancelled_poll_ids = self.bot.db.cancel_voice_mute_polls_for_target(guild.id, user.id)
        for poll_id in cancelled_poll_ids:
            await self._edit_poll_message(poll_id)

        unmuted = False
        detail = "The member is not connected to voice."
        if user.voice and user.voice.channel:
            if user.voice.mute:
                unmuted, detail = await self._set_server_mute(
                    user,
                    False,
                    reason or f"Vote mute force-removed by {interaction.user}",
                )
            else:
                unmuted = True
                detail = "The member was already unmuted."

        if unmuted or not user.voice or not user.voice.channel:
            await interaction.followup.send(
                (
                    f"Force-unmute completed for {user.mention}. "
                    f"Removed active mute: {'yes' if removed else 'no'}; "
                    f"cancelled open vote(s): {len(cancelled_poll_ids)}. {detail}"
                ),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                (
                    f"The saved mute was removed, but Discord could not unmute "
                    f"{user.mention}: {detail}"
                ),
                ephemeral=True,
            )

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot or after.channel is None:
            return
        active_mute = self.bot.db.get_active_voice_mute(member.guild.id, member.id)
        if not active_mute or after.mute:
            return
        await self._set_server_mute(member, True, "Active vote mute re-applied on voice join")

    @tasks.loop(seconds=30)
    async def maintenance(self) -> None:
        for poll_id in self.bot.db.expire_voice_mute_polls():
            await self._edit_poll_message(poll_id)

        for active_mute in self.bot.db.list_active_voice_mutes():
            guild = self.bot.get_guild(active_mute.guild_id)
            member = guild.get_member(active_mute.user_id) if guild else None
            if member and member.voice and member.voice.channel and not member.voice.mute:
                await self._set_server_mute(
                    member,
                    True,
                    "Periodic enforcement of active vote mute",
                )

        for expired_mute in self.bot.db.list_expired_voice_mutes():
            guild = self.bot.get_guild(expired_mute.guild_id)
            member = guild.get_member(expired_mute.user_id) if guild else None
            if member and member.voice and member.voice.channel and member.voice.mute:
                success, _ = await self._set_server_mute(
                    member,
                    False,
                    "Vote-mute duration expired",
                )
                if not success:
                    continue
            self.bot.db.remove_expired_voice_mute(
                expired_mute.guild_id,
                expired_mute.user_id,
                expired_mute.expires_at,
            )

    @maintenance.before_loop
    async def before_maintenance(self) -> None:
        await self.bot.wait_until_ready()

    @maintenance.error
    async def maintenance_error(self, error: BaseException) -> None:
        logger.error(
            "Voice-mute maintenance loop stopped",
            exc_info=(type(error), error, error.__traceback__),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoteMuteCog(bot))
