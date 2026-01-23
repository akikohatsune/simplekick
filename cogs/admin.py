import logging
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger("simplekick.admin")


def owner_only() -> app_commands.Check:
    async def predicate(interaction: discord.Interaction) -> bool:
        return await interaction.client.is_owner(interaction.user)

    return app_commands.check(predicate)


def access_allowed() -> app_commands.Check:
    async def predicate(interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if await bot.is_owner(interaction.user):
            return True
        allowed_role_id = getattr(bot, "allowed_role_id", None)
        if allowed_role_id is None:
            return True
        if not isinstance(interaction.user, discord.Member):
            return False
        return any(role.id == allowed_role_id for role in interaction.user.roles)

    return app_commands.check(predicate)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_owner_user(self) -> discord.User | None:
        if self.bot.owner_id:
            user = self.bot.get_user(self.bot.owner_id)
            if user:
                return user
            try:
                return await self.bot.fetch_user(self.bot.owner_id)
            except discord.HTTPException:
                return None
        try:
            app = await self.bot.application_info()
        except discord.HTTPException:
            return None
        if app.team:
            return app.team.owner
        return app.owner

    async def _dm_user(self, user: discord.abc.User, message: str) -> bool:
        try:
            await user.send(message)
            return True
        except discord.Forbidden:
            return False
        except discord.HTTPException:
            logger.exception("Failed to DM user %s", user.id)
            return False

    blacklist = app_commands.Group(
        name="blacklist",
        description="Manage auto-disconnect blacklist (exemptions)",
    )

    @blacklist.command(name="add", description="Exempt a user from auto-disconnect.")
    @app_commands.guild_only()
    @app_commands.describe(user="User to exempt", reason="Optional reason")
    @owner_only()
    async def blacklist_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
    ) -> None:
        self.bot.db.add_blacklist(interaction.guild.id, user.id, interaction.user.id, reason)
        await interaction.response.send_message(
            f"Added {user.mention} to the blacklist.",
            ephemeral=True,
        )

    @blacklist.command(name="remove", description="Remove a user from the blacklist.")
    @app_commands.guild_only()
    @app_commands.describe(user="User to remove")
    @owner_only()
    async def blacklist_remove(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        removed = self.bot.db.remove_blacklist(interaction.guild.id, user.id)
        message = (
            f"Removed {user.mention} from the blacklist."
            if removed
            else f"{user.mention} is not in the blacklist."
        )
        await interaction.response.send_message(message, ephemeral=True)

    @blacklist.command(name="list", description="List blacklisted users.")
    @app_commands.guild_only()
    @owner_only()
    async def blacklist_list(self, interaction: discord.Interaction) -> None:
        rows = self.bot.db.list_blacklist(interaction.guild.id, limit=50)
        if not rows:
            await interaction.response.send_message("Blacklist is empty.", ephemeral=True)
            return

        lines = []
        for user_id, reason, added_at, added_by in rows:
            member = interaction.guild.get_member(user_id)
            mention = member.mention if member else f"<@{user_id}>"
            reason_text = reason or "no reason"
            added_date = datetime.utcfromtimestamp(added_at).strftime("%Y-%m-%d")
            added_by_text = f" by <@{added_by}>" if added_by else ""
            lines.append(f"{mention} - {reason_text} (added {added_date}{added_by_text})")

        content = "Blacklist (max 50):\n" + "\n".join(lines)
        await interaction.response.send_message(content, ephemeral=True)

    exempt = app_commands.Group(
        name="exempt",
        description="Request or grant temporary auto-disconnect exemptions.",
    )

    @exempt.command(name="request", description="Request a temporary exemption.")
    @app_commands.guild_only()
    @app_commands.describe(seconds="How long to exempt (seconds)", reason="Optional reason")
    @access_allowed()
    async def exempt_request(
        self,
        interaction: discord.Interaction,
        seconds: app_commands.Range[int, 5, 86400],
        reason: str | None = None,
    ) -> None:
        if self.bot.db.is_temp_exempt(interaction.guild.id, interaction.user.id):
            await interaction.response.send_message(
                "You already have an active exemption.",
                ephemeral=True,
            )
            return

        owner = await self._get_owner_user()
        if not owner:
            await interaction.response.send_message(
                "Could not contact the bot owner.",
                ephemeral=True,
            )
            return

        reason_text = reason or "no reason"
        message = (
            "Exemption request:\n"
            f"- Guild: {interaction.guild.name} ({interaction.guild.id})\n"
            f"- User: {interaction.user} ({interaction.user.id})\n"
            f"- Seconds: {seconds}\n"
            f"- Reason: {reason_text}\n"
            "Use /exempt grant to approve."
        )
        try:
            await owner.send(message)
        except discord.Forbidden:
            logger.warning("Failed to DM owner for request from %s", interaction.user.id)
            await interaction.response.send_message(
                "Could not DM the bot owner.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Request sent to the bot owner.",
            ephemeral=True,
        )

    @exempt.command(name="grant", description="Grant a temporary exemption.")
    @app_commands.guild_only()
    @app_commands.describe(
        user="User to exempt", seconds="How long to exempt (seconds)", reason="Optional reason"
    )
    @owner_only()
    async def exempt_grant(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        seconds: app_commands.Range[int, 5, 86400],
        reason: str | None = None,
    ) -> None:
        expires_at = int(time.time()) + seconds
        self.bot.db.add_temp_exempt(
            interaction.guild.id, user.id, expires_at, interaction.user.id, reason
        )
        reason_text = reason or "no reason"
        dm_message = (
            "Your exemption request was approved.\n"
            f"- Guild: {interaction.guild.name} ({interaction.guild.id})\n"
            f"- Duration: {seconds} seconds\n"
            f"- Reason: {reason_text}"
        )
        await self._dm_user(user, dm_message)
        await interaction.response.send_message(
            f"Granted exemption for {user.mention} ({seconds} seconds).",
            ephemeral=True,
        )

    @exempt.command(name="deny", description="Deny a temporary exemption request.")
    @app_commands.guild_only()
    @app_commands.describe(user="User to deny", reason="Optional reason")
    @owner_only()
    async def exempt_deny(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str | None = None,
    ) -> None:
        reason_text = reason or "no reason"
        dm_message = (
            "Your exemption request was denied.\n"
            f"- Guild: {interaction.guild.name} ({interaction.guild.id})\n"
            f"- Reason: {reason_text}"
        )
        await self._dm_user(user, dm_message)
        await interaction.response.send_message(
            f"Denied exemption for {user.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="sync", description="Sync slash commands.")
    @app_commands.describe(guild_id="Optional guild ID to sync to")
    @owner_only()
    async def sync_commands(
        self, interaction: discord.Interaction, guild_id: str | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            if guild_id:
                guild = discord.Object(id=int(guild_id))
                self.bot.tree.copy_global_to(guild=guild)
                synced = await self.bot.tree.sync(guild=guild)
            else:
                synced = await self.bot.tree.sync()
            await interaction.followup.send(f"Synced {len(synced)} commands.", ephemeral=True)
        except ValueError:
            await interaction.followup.send("Invalid guild_id.", ephemeral=True)
        except Exception:
            logger.exception("Failed to sync commands")
            await interaction.followup.send("Sync failed. Check logs.", ephemeral=True)

    @app_commands.command(name="setup", description="Set the role allowed to use the bot (one-time).")
    @app_commands.guild_only()
    @app_commands.describe(role="Role that can use the bot")
    @owner_only()
    async def setup_access(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        if getattr(self.bot, "setup_locked", False):
            await interaction.response.send_message(
                "Setup already configured. Restart the bot to change it.",
                ephemeral=True,
            )
            return

        self.bot.allowed_role_id = role.id
        self.bot.setup_locked = True
        await interaction.response.send_message(
            f"Access role set to {role.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
