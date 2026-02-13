import asyncio
import json
import logging
import re
import urllib.error
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands


logger = logging.getLogger("simplekick.version")


def _parse_repo(repo: str) -> tuple[str, str] | None:
    value = (repo or "").strip()
    if "/" not in value:
        return None
    owner, name = value.split("/", 1)
    if not owner or not name:
        return None
    return owner, name


def _normalize_version(version: str) -> str:
    value = (version or "").strip()
    if value.startswith("v"):
        value = value[1:]
    return value


def _parse_version(version: str) -> tuple[int, ...] | None:
    cleaned = _normalize_version(version)
    if not cleaned:
        return None
    parts = re.split(r"[.-]", cleaned)
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        numbers.append(int(part))
    return tuple(numbers)


def _compare_versions(current: str, latest: str) -> int | None:
    current_parsed = _parse_version(current)
    latest_parsed = _parse_version(latest)
    if current_parsed is None or latest_parsed is None:
        return None
    max_len = max(len(current_parsed), len(latest_parsed))
    current_pad = current_parsed + (0,) * (max_len - len(current_parsed))
    latest_pad = latest_parsed + (0,) * (max_len - len(latest_parsed))
    if current_pad == latest_pad:
        return 0
    return -1 if current_pad < latest_pad else 1


def _request_json(url: str, timeout: int = 5) -> dict | list | None:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "simplekick-version-check",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_latest_release(repo: str) -> tuple[str, str] | None:
    parsed = _parse_repo(repo)
    if not parsed:
        return None
    owner, name = parsed
    url = f"https://api.github.com/repos/{owner}/{name}/releases/latest"
    try:
        data = _request_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name")
    html_url = data.get("html_url", "")
    if not tag:
        return None
    return tag, html_url


class VersionCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ver", description="Show current bot version and GitHub release.")
    async def ver(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        bot_version = str(getattr(self.bot, "bot_version", "unknown"))
        repo = str(getattr(self.bot, "github_repo", "")).strip()
        embed = discord.Embed(title="Version", color=discord.Color.blurple())
        embed.add_field(name="Bot Version", value=f"`{bot_version}`", inline=True)

        if not repo:
            embed.add_field(name="GitHub Repo", value="Not configured", inline=True)
            embed.add_field(name="Status", value="Cannot check latest release.", inline=False)
            embed.color = discord.Color.light_grey()
            await interaction.followup.send(embed=embed)
            return

        if not _parse_repo(repo):
            embed.add_field(name="GitHub Repo", value=f"`{repo}`", inline=True)
            embed.add_field(name="Status", value="Invalid repo format. Use `owner/repo`.", inline=False)
            embed.color = discord.Color.red()
            await interaction.followup.send(embed=embed)
            return

        try:
            latest_info = await asyncio.to_thread(_fetch_latest_release, repo)
        except Exception:
            logger.exception("Failed to fetch release for %s", repo)
            embed.add_field(name="GitHub Repo", value=f"`{repo}`", inline=True)
            embed.add_field(name="Status", value="Failed to fetch latest release.", inline=False)
            embed.color = discord.Color.red()
            await interaction.followup.send(embed=embed)
            return

        if not latest_info:
            embed.add_field(name="GitHub Repo", value=f"`{repo}`", inline=True)
            embed.add_field(name="Status", value="No release found.", inline=False)
            embed.color = discord.Color.light_grey()
            await interaction.followup.send(embed=embed)
            return

        latest_tag, release_url = latest_info
        embed.add_field(name="GitHub Repo", value=f"`{repo}`", inline=True)
        embed.add_field(name="Latest Release", value=f"`{latest_tag}`", inline=True)
        if release_url:
            embed.add_field(name="Release URL", value=f"[Open release]({release_url})", inline=False)

        comparison = _compare_versions(bot_version, latest_tag)
        if comparison is None:
            status = "Cannot compare version format."
            embed.color = discord.Color.light_grey()
        elif comparison < 0:
            status = "Update available."
            embed.color = discord.Color.orange()
        elif comparison == 0:
            status = "Up to date."
            embed.color = discord.Color.green()
        else:
            status = "Local version is newer than latest release."
            embed.color = discord.Color.blue()

        embed.add_field(name="Status", value=status, inline=False)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VersionCog(bot))
