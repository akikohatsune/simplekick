import logging
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger("simplekick.admin")


def _parse_user_id(raw: str) -> int | None:
    value = raw.strip()
    if value.startswith("<@") and value.endswith(">"):
        value = value[2:-1]
        if value.startswith("!"):
            value = value[1:]
    if not value.isdigit():
        return None
    return int(value)


def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        return await interaction.client.is_owner(interaction.user)

    return app_commands.check(predicate)


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _sync_tree(self, guild_id: str | None) -> str:
        synced_global = await self.bot.tree.sync()
        if not guild_id:
            return f"Synced {len(synced_global)} global commands."
        guild = discord.Object(id=int(guild_id))
        self.bot.tree.copy_global_to(guild=guild)
        synced_guild = await self.bot.tree.sync(guild=guild)
        return f"Synced {len(synced_global)} global and {len(synced_guild)} guild commands."

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
    @app_commands.describe(user_id="User ID or mention", reason="Optional reason")
    @owner_only()
    async def blacklist_add(
        self,
        interaction: discord.Interaction,
        user_id: str,
        reason: str | None = None,
    ) -> None:
        target_id = _parse_user_id(user_id)
        if not target_id:
            await interaction.response.send_message(
                "Invalid user ID. Provide a numeric ID or mention.",
                ephemeral=True,
            )
            return
        self.bot.db.add_blacklist(interaction.guild.id, target_id, interaction.user.id, reason)
        await interaction.response.send_message(
            f"Added <@{target_id}> to the blacklist.",
            ephemeral=True,
        )

    @blacklist.command(name="remove", description="Remove a user from the blacklist.")
    @app_commands.guild_only()
    @app_commands.describe(user_id="User ID or mention")
    @owner_only()
    async def blacklist_remove(
        self, interaction: discord.Interaction, user_id: str
    ) -> None:
        target_id = _parse_user_id(user_id)
        if not target_id:
            await interaction.response.send_message(
                "Invalid user ID. Provide a numeric ID or mention.",
                ephemeral=True,
            )
            return
        removed = self.bot.db.remove_blacklist(interaction.guild.id, target_id)
        mention = f"<@{target_id}>"
        message = (
            f"Removed {mention} from the blacklist."
            if removed
            else f"{mention} is not in the blacklist."
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
            message = await self._sync_tree(guild_id)
            await interaction.followup.send(message, ephemeral=True)
        except ValueError:
            await interaction.followup.send("Invalid guild_id.", ephemeral=True)
        except Exception:
            logger.exception("Failed to sync commands")
            await interaction.followup.send("Sync failed. Check logs.", ephemeral=True)

    @commands.command(name="sync")
    @commands.is_owner()
    async def sync_prefix(self, ctx: commands.Context, guild_id: str | None = None) -> None:
        try:
            message = await self._sync_tree(guild_id)
            await ctx.reply(message)
        except ValueError:
            await ctx.reply("Invalid guild_id.")
        except Exception:
            logger.exception("Failed to sync commands")
            await ctx.reply("Sync failed. Check logs.")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
