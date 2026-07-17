"""
===============================================================================
 Flux Mobile
===============================================================================

Main Cog

Stage 5/8

ANTI-DRIFT CONTRACT

This module owns:


• Red Config
• Commands
• Background monitoring
• Release announcements

It MUST NOT:

• Contain GitHub API implementation.
• Build embeds directly.
• Parse GitHub JSON.

Those responsibilities belong in github.py, utils.py and models.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Final

import discord
from discord.ext import tasks

from redbot.core import Config, commands

from .constants import (
    COG_VERSION,
    DEFAULT_CHECK_INTERVAL_MINUTES,
    DEFAULT_GUILD,
    LOGGER_NAME,
)
from .github import GitHubClient, GitHubError
from .models import Release
from .utils import (
    build_release_embed,
    resolve_embed_colour,
)

log = logging.getLogger(LOGGER_NAME)


class FluxMobile(commands.Cog):
    """
    Automatically announce new Fluxer Mobile releases.
    """

    __author__: Final[list[str]] = ["Five"]
    __version__: Final[str] = COG_VERSION

    def __init__(self, bot):
        self.bot = bot

        self.config = Config.get_conf(
            self,
            identifier=2407202601,
            force_registration=True,
        )

        self.config.register_guild(**DEFAULT_GUILD)

        self.github = GitHubClient()

        self._ready = False

        self.monitor.start()

    def format_help_for_context(self, ctx):
        pre = super().format_help_for_context(ctx)

        return (
            f"{pre}\n\n"
            f"Version: {self.__version__}\n"
            f"Author: Five\n"
            f"Community: AEGIS"
        )

    async def cog_load(self):
        """
        Initialise resources.

        Called automatically when the cog loads.
        """

        await self.github.start()

        self._ready = True

        log.info("Flux Mobile initialised.")

    async def cog_unload(self):
        """
        Gracefully stop the background task and
        close the GitHub client.
        """

        self.monitor.cancel()

        await self.github.close()

        log.info("Flux Mobile unloaded.")

    #
    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------
    #

    async def _guild_enabled(self, guild: discord.Guild) -> bool:
        return await self.config.guild(guild).enabled()

    async def _announcement_channel(
        self,
        guild: discord.Guild,
    ) -> discord.TextChannel | None:

        channel_id = await self.config.guild(
            guild
        ).announcement_channel()

        if not channel_id:
            return None

        channel = guild.get_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            return None

        permissions = channel.permissions_for(guild.me)

        if not (
            permissions.send_messages
            and permissions.embed_links
        ):
            return None

        return channel

    async def _log_channel(
        self,
        guild: discord.Guild,
    ) -> discord.TextChannel | None:

        channel_id = await self.config.guild(
            guild
        ).log_channel()

        if not channel_id:
            return None

        channel = guild.get_channel(channel_id)

        if isinstance(channel, discord.TextChannel):
            return channel

        return None

    async def _verbose_log(
        self,
        guild: discord.Guild,
        message: str,
    ):
        """
        Send a verbose diagnostic log.

        Uses Discord/Fluxer Unix timestamps so every log displays the
        absolute time and relative time consistently.
        """

        if not await self.config.guild(guild).verbose():
            return

        channel = await self._log_channel(guild)

        if channel is None:
            return

        try:
            now = int(datetime.now(timezone.utc).timestamp())

            await channel.send(
                f"<t:{now}:F> (<t:{now}:R>) • {message}"
            )

        except discord.HTTPException:
            pass


    async def _latest_release(self) -> Release:
        """
        Fetch the latest GitHub release.

        Wrapper around GitHubClient so future
        enhancements remain isolated.
        """

        return await self.github.latest_release()

    async def _release_already_announced(
        self,
        guild: discord.Guild,
        release: Release,
    ) -> bool:

        last_id = await self.config.guild(
            guild
        ).last_release_id()

        return last_id == release.id

    async def _save_release(
        self,
        guild: discord.Guild,
        release: Release,
    ):

        await self.config.guild(guild).last_release_id.set(
            release.id
        )

        await self.config.guild(guild).last_release_tag.set(
            release.version
        )

        await self.config.guild(guild).last_check.set(
            datetime.now(timezone.utc).isoformat()
        )

    async def _send_release(
        self,
        guild: discord.Guild,
        release: Release,
    ) -> bool:

        channel = await self._announcement_channel(
            guild
        )

        if channel is None:
            return False

        colour = await resolve_embed_colour(
            self.bot
        )

        embed = build_release_embed(
            release,
            colour,
        )

        try:

            await channel.send(
                embed=embed
            )

            return True

        except discord.HTTPException:

            return False

    #
    # -------------------------------------------------------------------------
    # Commands
    # -------------------------------------------------------------------------
    #

    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    @commands.group(name="flux", invoke_without_command=True)
    async def flux(self, ctx: commands.Context):
        """
        Flux Mobile configuration.
        """
        await ctx.send_help()

    @flux.command(name="enable")
    async def flux_enable(self, ctx: commands.Context):
        """Enable Flux Mobile monitoring."""

        await self.config.guild(ctx.guild).enabled.set(True)

        await ctx.tick()

        await self._verbose_log(
            ctx.guild,
            f"{ctx.author} enabled Flux Mobile monitoring.",
        )

    @flux.command(name="disable")
    async def flux_disable(self, ctx: commands.Context):
        """Disable Flux Mobile monitoring."""

        await self.config.guild(ctx.guild).enabled.set(False)

        await ctx.tick()

        await self._verbose_log(
            ctx.guild,
            f"{ctx.author} disabled Flux Mobile monitoring.",
        )

    @flux.command(name="channel")
    async def flux_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ):
        """
        Set the announcement channel.
        """

        perms = channel.permissions_for(ctx.guild.me)

        if not (perms.send_messages and perms.embed_links):
            return await ctx.send(
                "I require Send Messages and Embed Links in that channel."
            )

        await self.config.guild(ctx.guild).announcement_channel.set(
            channel.id
        )

        await ctx.send(
            f"Announcement channel set to {channel.mention}."
        )

        await self._verbose_log(
            ctx.guild,
            f"Announcement channel changed to #{channel}.",
        )

    @flux.command(name="logs")
    async def flux_logs(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ):
        """
        Set the verbose log channel.
        """

        perms = channel.permissions_for(ctx.guild.me)

        if not perms.send_messages:
            return await ctx.send(
                "I cannot send messages there."
            )

        await self.config.guild(ctx.guild).log_channel.set(
            channel.id
        )

        await ctx.send(
            f"Verbose logging channel set to {channel.mention}."
        )

    @flux.command(name="verbose")
    async def flux_verbose(
        self,
        ctx: commands.Context,
        enabled: bool,
    ):
        """
        Enable or disable verbose logging.
        """

        await self.config.guild(ctx.guild).verbose.set(enabled)

        await ctx.send(
            f"Verbose logging {'enabled' if enabled else 'disabled'}."
        )

    @flux.command(name="interval")
    async def flux_interval(
        self,
        ctx: commands.Context,
        minutes: int,
    ):
        """
        Set the polling interval.
        """

        if minutes < 5:
            return await ctx.send(
                "The minimum interval is 5 minutes."
            )

        await self.config.guild(ctx.guild).interval.set(
            minutes
        )

        #
        # REVIEW NOTE
        #
        # The monitor currently runs on a global interval.
        # During Phase 2 we should decide whether this command
        # changes the global task interval or becomes a per-guild
        # scheduling mechanism.
        #

        await ctx.send(
            f"Check interval set to {minutes} minutes."
        )

    @flux.command(name="status")
    async def flux_status(
        self,
        ctx: commands.Context,
    ):
        """
        Display the current configuration.
        """

        conf = await self.config.guild(ctx.guild).all()

        embed = discord.Embed(
            title="Flux Mobile Status",
            colour=await resolve_embed_colour(ctx),
        )

        embed.add_field(
            name="Enabled",
            value="Yes" if conf["enabled"] else "No",
        )

        channel = (
            ctx.guild.get_channel(conf["announcement_channel"])
            if conf["announcement_channel"]
            else None
        )

        embed.add_field(
            name="Announcements",
            value=channel.mention if channel else "Not configured",
            inline=False,
        )

        log_channel = (
            ctx.guild.get_channel(conf["log_channel"])
            if conf["log_channel"]
            else None
        )

        embed.add_field(
            name="Verbose Logs",
            value=log_channel.mention if log_channel else "Disabled",
            inline=False,
        )

        embed.add_field(
            name="Interval",
            value=f"{conf['interval']} minutes",
        )

        embed.add_field(
            name="Last Release",
            value=conf["last_release_tag"] or "None",
            inline=False,
        )

        embed.add_field(
            name="Background Task",
            value="Running" if self.monitor.is_running() else "Stopped",
        )

        await ctx.send(embed=embed)

    @flux.command(name="latest")
    async def flux_latest(
        self,
        ctx: commands.Context,
    ):
        """
        Show the latest GitHub release without announcing it.
        """

        async with ctx.typing():

            try:
                release = await self._latest_release()

            except GitHubError as exc:
                return await ctx.send(
                    f"GitHub error: `{exc}`"
                )

        colour = await resolve_embed_colour(ctx)

        embed = build_release_embed(
            release,
            colour,
        )

        await ctx.send(embed=embed)

    @flux.command(name="check")
    async def flux_check(
        self,
        ctx: commands.Context,
    ):
        """
        Force an immediate release check.
        """

        async with ctx.typing():

            await self._check_guild(
                ctx.guild,
                manual=True,
            )

        await ctx.tick()

    @flux.command(name="test")
    async def flux_test(
        self,
        ctx: commands.Context,
    ):
        """
        Send a test announcement using the latest release.
        """

        async with ctx.typing():

            try:
                release = await self._latest_release()

            except GitHubError as exc:
                return await ctx.send(
                    f"GitHub error: `{exc}`"
                )

        success = await self._send_release(
            ctx.guild,
            release,
        )

        if success:
            await ctx.tick()
        else:
            await ctx.send(
                "Unable to send the announcement."
            )

    #
    # -------------------------------------------------------------------------
    # Background Monitor
    # -------------------------------------------------------------------------
    #

    @tasks.loop(minutes=DEFAULT_CHECK_INTERVAL_MINUTES)
    async def monitor(self):
        """
        Periodically check GitHub for new Fluxer Mobile releases.

        ANTI-DRIFT

        This task coordinates the workflow only.

        GitHub communication -> github.py
        Embed creation      -> utils.py
        Release model       -> models.py
        """

        if not self._ready:
            return

        await self.bot.wait_until_red_ready()

        for guild in self.bot.guilds:

            try:

                if not await self._guild_enabled(guild):
                    continue

                await self._check_guild(guild)

            except Exception:

                log.exception(
                    "Unexpected error while checking guild %s",
                    guild.id,
                )

                await self._verbose_log(
                    guild,
                    "Unexpected exception occurred during monitor run.",
                )

    @monitor.before_loop
    async def before_monitor(self):
        """Wait until Red is fully ready."""

        await self.bot.wait_until_red_ready()

    #
    # -------------------------------------------------------------------------
    # Internal Monitor
    # -------------------------------------------------------------------------
    #

    async def _check_guild(
        self,
        guild: discord.Guild,
        *,
        manual: bool = False,
    ):
        """
        Check GitHub and announce a release if required.
        """

        await self._verbose_log(
            guild,
            "Checking GitHub for new releases...",
        )

        try:

            release = await self._latest_release()

        except GitHubError as exc:

            log.warning(
                "GitHub request failed: %s",
                exc,
            )

            await self._verbose_log(
                guild,
                f"GitHub request failed: {exc}",
            )

            return

        #
        # Ignore draft releases.
        #

        if release.draft:

            await self._verbose_log(
                guild,
                "Ignoring draft release.",
            )

            return

        #
        # Ignore prereleases.
        #
        # REVIEW NOTE
        #
        # Future enhancement:
        # configurable prerelease support.
        #

        if release.prerelease:

            await self._verbose_log(
                guild,
                "Ignoring prerelease.",
            )

            return

        #
        # Duplicate protection.
        #

        if await self._release_already_announced(
            guild,
            release,
        ):

            await self._verbose_log(
                guild,
                f"No new release ({release.version}).",
            )

            if manual:
                log.debug(
                    "Manual check completed with no new release."
                )

            return

        #
        # Announce.
        #

        success = await self._send_release(
            guild,
            release,
        )

        if not success:

            await self._verbose_log(
                guild,
                "Announcement failed.",
            )

            return

        #
        # Persist state only AFTER
        # a successful announcement.
        #

        await self._save_release(
            guild,
            release,
        )

        await self._verbose_log(
            guild,
            f"Announced {release.version}.",
        )

        log.info(
            "Announced %s to guild %s",
            release.version,
            guild.id,
        )

    #
    # -------------------------------------------------------------------------
    # Diagnostics
    # -------------------------------------------------------------------------
    #

    @commands.is_owner()
    @commands.command(hidden=True)
    async def fluxdiag(
        self,
        ctx: commands.Context,
    ):
        """
        Owner diagnostics.
        """

        embed = discord.Embed(
            title="Flux Mobile Diagnostics",
            colour=await resolve_embed_colour(ctx),
        )

        embed.add_field(
            name="Monitor",
            value="Running"
            if self.monitor.is_running()
            else "Stopped",
        )

        embed.add_field(
            name="Ready",
            value=str(self._ready),
        )

        embed.add_field(
            name="Guilds",
            value=str(len(self.bot.guilds)),
        )

        embed.add_field(
            name="GitHub Client",
            value="Ready"
            if self.github._session
            and not self.github._session.closed
            else "Closed",
        )

        await ctx.send(embed=embed)

    #
    # -------------------------------------------------------------------------
    # End of File
    # -------------------------------------------------------------------------
