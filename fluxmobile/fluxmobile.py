"""
Flux Mobile

Automatically announces new Fluxer Mobile GitHub releases.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Final

import discord
from discord.ext import tasks

from redbot.core import Config, commands

from .constants import (
    COG_VERSION,
    DEFAULT_CHECK_INTERVAL_MINUTES,
    DEFAULT_GUILD,
    ERROR_RETRY_INTERVAL_MINUTES,
    LOGGER_NAME,
    MIN_CHECK_INTERVAL_MINUTES,
    MONITOR_TICK_SECONDS,
)
from .github import (
    GitHubClient,
    GitHubError,
    ReleaseFeed,
)
from .models import Release
from .utils import (
    build_release_embed,
    resolve_embed_colour,
)

log = logging.getLogger(LOGGER_NAME)


class CheckStatus(str, Enum):
    ANNOUNCED = "announced"
    UP_TO_DATE = "up_to_date"
    NO_ELIGIBLE_RELEASE = "no_eligible_release"
    WAITING_FOR_APK = "waiting_for_apk"
    ANNOUNCEMENT_FAILED = "announcement_failed"
    GITHUB_ERROR = "github_error"
    ERROR = "error"


RETRY_RESULTS: Final[set[str]] = {
    CheckStatus.WAITING_FOR_APK.value,
    CheckStatus.ANNOUNCEMENT_FAILED.value,
    CheckStatus.GITHUB_ERROR.value,
    CheckStatus.ERROR.value,
}


@dataclass(frozen=True, slots=True)
class CheckResult:
    status: CheckStatus
    message: str
    release: Release | None = None


@dataclass(frozen=True, slots=True)
class SendResult:
    success: bool
    detail: str
    channel: discord.TextChannel | None = None


class FluxMobile(commands.Cog):
    """Automatically announce new Fluxer Mobile releases."""

    __author__: Final[list[str]] = ["Five"]
    __version__: Final[str] = COG_VERSION

    def __init__(self, bot):
        self.bot = bot

        # Keep this identifier unchanged. It is the existing Config
        # namespace for deployed guild settings.
        self.config = Config.get_conf(
            self,
            identifier=2407202601,
            force_registration=True,
        )

        self.config.register_guild(**DEFAULT_GUILD)

        self.github = GitHubClient()

        self._ready = False
        self._check_locks: dict[int, asyncio.Lock] = {}

    def format_help_for_context(self, ctx):
        pre = super().format_help_for_context(ctx)

        return (
            f"{pre}\n\n"
            f"Version: {self.__version__}\n"
            f"Author: Five\n"
            f"Community: AEGIS"
        )

    async def cog_load(self):
        """Initialise HTTP resources before starting the monitor."""

        await self.github.start()

        self._ready = True

        if not self.monitor.is_running():
            self.monitor.start()

        log.info(
            "Flux Mobile %s initialised.",
            self.__version__,
        )

    async def cog_unload(self):
        """Stop the monitor and close the HTTP client."""

        self._ready = False

        if self.monitor.is_running():
            self.monitor.cancel()

        await self.github.close()

        log.info("Flux Mobile unloaded.")

    # ------------------------------------------------------------------
    # Date and lock helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_datetime(
        value: str | None,
    ) -> datetime | None:

        if not isinstance(value, str) or not value.strip():
            return None

        try:
            parsed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)

        except ValueError:
            return None

    @staticmethod
    def _display_datetime(
        value: datetime | None,
    ) -> str:

        if value is None:
            return "Never"

        timestamp = int(value.timestamp())

        return (
            f"<t:{timestamp}:F> "
            f"(<t:{timestamp}:R>)"
        )

    def _lock_for(
        self,
        guild: discord.Guild,
    ) -> asyncio.Lock:

        lock = self._check_locks.get(guild.id)

        if lock is None:
            lock = asyncio.Lock()
            self._check_locks[guild.id] = lock

        return lock

    async def _guild_enabled(
        self,
        guild: discord.Guild,
    ) -> bool:

        return await self.config.guild(guild).enabled()

    async def _guild_due(
        self,
        guild: discord.Guild,
        now: datetime,
    ) -> bool:

        conf = await self.config.guild(guild).all()

        if not conf["enabled"]:
            return False

        try:
            configured_interval = int(
                conf.get("interval")
                or DEFAULT_CHECK_INTERVAL_MINUTES
            )
        except (TypeError, ValueError):
            configured_interval = (
                DEFAULT_CHECK_INTERVAL_MINUTES
            )

        configured_interval = max(
            configured_interval,
            MIN_CHECK_INTERVAL_MINUTES,
        )

        last_result = str(
            conf.get("last_check_result") or ""
        )

        if last_result in RETRY_RESULTS:
            effective_interval = min(
                configured_interval,
                ERROR_RETRY_INTERVAL_MINUTES,
            )
        else:
            effective_interval = configured_interval

        last_check = self._parse_datetime(
            conf.get("last_check")
        )

        if last_check is None:
            return True

        # Recover gracefully from a system clock moving backwards or a
        # malformed future timestamp.
        if last_check > now + timedelta(minutes=1):
            return True

        return (
            now - last_check
            >= timedelta(minutes=effective_interval)
        )

    # ------------------------------------------------------------------
    # Channel and logging helpers
    # ------------------------------------------------------------------

    async def _announcement_channel(
        self,
        guild: discord.Guild,
    ) -> tuple[discord.TextChannel | None, str]:

        channel_id = await self.config.guild(
            guild
        ).announcement_channel()

        if not channel_id:
            return None, (
                "No announcement channel is configured."
            )

        channel = guild.get_channel(channel_id)

        if not isinstance(channel, discord.TextChannel):
            return None, (
                "The configured announcement channel "
                "does not exist or is not a text channel."
            )

        member = guild.me

        if member is None:
            return None, (
                "Unable to resolve the bot's guild member."
            )

        permissions = channel.permissions_for(member)

        missing: list[str] = []

        if not permissions.send_messages:
            missing.append("Send Messages")

        if not permissions.embed_links:
            missing.append("Embed Links")

        if missing:
            return None, (
                f"Missing permissions in #{channel}: "
                f"{', '.join(missing)}."
            )

        return channel, "Announcement channel is usable."

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

        if not isinstance(channel, discord.TextChannel):
            return None

        member = guild.me

        if member is None:
            return None

        permissions = channel.permissions_for(member)

        if not permissions.send_messages:
            return None

        return channel

    async def _verbose_log(
        self,
        guild: discord.Guild,
        message: str,
        *,
        level: str = "INFO",
    ):
        """Send a timestamped guild diagnostic message."""

        try:
            if not await self.config.guild(guild).verbose():
                return

            channel = await self._log_channel(guild)

            if channel is None:
                return

            now = int(
                datetime.now(timezone.utc).timestamp()
            )

            if len(message) > 1800:
                message = message[:1799] + "…"

            await channel.send(
                f"<t:{now}:F> (<t:{now}:R>) "
                f"• [{level.upper()}] {message}"
            )

        except discord.HTTPException as exc:
            log.debug(
                "Unable to send verbose log to guild %s: %s",
                guild.id,
                exc,
            )

        except Exception:
            log.debug(
                "Unexpected verbose logging error for guild %s.",
                guild.id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Release helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _release_summary(release: Release) -> str:
        return (
            f"{release.version} "
            f"(id={release.id}, "
            f"type={release.release_type}, "
            f"published={release.timestamp}, "
            f"assets={len(release.assets)}, "
            f"apks={release.apk_count})"
        )

    @staticmethod
    def _feed_summary(feed: ReleaseFeed) -> str:
        source = (
            "local cache"
            if feed.from_cache
            else "GitHub network response"
        )

        rate = "unknown"

        if (
            feed.rate_limit_remaining is not None
            and feed.rate_limit_limit is not None
        ):
            rate = (
                f"{feed.rate_limit_remaining}/"
                f"{feed.rate_limit_limit} remaining"
            )

        fetched = int(feed.fetched_at.timestamp())

        return (
            f"GitHub returned {len(feed.releases)} "
            f"release(s) via {source}. "
            f"Feed fetched <t:{fetched}:R>. "
            f"API rate limit: {rate}."
        )

    @staticmethod
    def _select_candidate(
        feed: ReleaseFeed,
        include_prereleases: bool,
    ) -> tuple[
        Release | None,
        int,
        int,
        int,
    ]:
        """
        Select the newest eligible release.

        Returns:
            candidate
            draft count
            prerelease count
            prereleases excluded by policy
        """

        draft_count = sum(
            1
            for release in feed.releases
            if release.draft
        )

        published = [
            release
            for release in feed.releases
            if not release.draft
        ]

        prerelease_count = sum(
            1
            for release in published
            if release.prerelease
        )

        excluded_prereleases = (
            0
            if include_prereleases
            else prerelease_count
        )

        eligible = [
            release
            for release in published
            if (
                include_prereleases
                or not release.prerelease
            )
        ]

        if not eligible:
            return (
                None,
                draft_count,
                prerelease_count,
                excluded_prereleases,
            )

        candidate = max(
            eligible,
            key=lambda release: (
                release.published,
                release.id,
            ),
        )

        return (
            candidate,
            draft_count,
            prerelease_count,
            excluded_prereleases,
        )

    @staticmethod
    def _find_stored_release(
        feed: ReleaseFeed,
        last_id: int | None,
        last_tag: str | None,
    ) -> Release | None:

        tag_match: Release | None = None

        for release in feed.releases:
            if (
                last_id is not None
                and release.id == last_id
            ):
                return release

            if last_tag and release.version == last_tag:
                tag_match = release

        return tag_match

    async def _save_release(
        self,
        guild: discord.Guild,
        release: Release,
    ):

        scope = self.config.guild(guild)

        await scope.last_release_id.set(release.id)
        await scope.last_release_tag.set(release.version)
        await scope.last_release_published.set(
            release.published.isoformat()
        )

    async def _record_check(
        self,
        guild: discord.Guild,
        status: CheckStatus,
    ):

        scope = self.config.guild(guild)

        await scope.last_check.set(
            datetime.now(timezone.utc).isoformat()
        )

        await scope.last_check_result.set(
            status.value
        )

    async def _send_release(
        self,
        guild: discord.Guild,
        release: Release,
        *,
        colour_source=None,
    ) -> SendResult:

        channel, channel_detail = (
            await self._announcement_channel(guild)
        )

        if channel is None:
            return SendResult(
                success=False,
                detail=channel_detail,
            )

        source = (
            colour_source
            if colour_source is not None
            else self.bot
        )

        try:
            colour = await resolve_embed_colour(
                source,
                guild=guild,
            )

            embed = build_release_embed(
                release,
                colour,
            )

            await channel.send(embed=embed)

            return SendResult(
                success=True,
                detail=(
                    f"Announcement sent to "
                    f"{channel.mention}."
                ),
                channel=channel,
            )

        except discord.Forbidden as exc:
            return SendResult(
                success=False,
                detail=(
                    f"Fluxer/Discord rejected the announcement "
                    f"with Forbidden: {exc}"
                ),
                channel=channel,
            )

        except discord.HTTPException as exc:
            status = getattr(exc, "status", "unknown")

            return SendResult(
                success=False,
                detail=(
                    f"Announcement HTTP failure "
                    f"(status={status}): {exc}"
                ),
                channel=channel,
            )

        except Exception as exc:
            log.exception(
                "Unexpected announcement error in guild %s.",
                guild.id,
            )

            return SendResult(
                success=False,
                detail=(
                    "Unexpected announcement error: "
                    f"{type(exc).__name__}: {exc}"
                ),
                channel=channel,
            )

    # ------------------------------------------------------------------
    # Main release check
    # ------------------------------------------------------------------

    async def _check_guild(
        self,
        guild: discord.Guild,
        *,
        manual: bool = False,
        feed: ReleaseFeed | None = None,
    ) -> CheckResult:

        lock = self._lock_for(guild)

        async with lock:
            result = CheckResult(
                CheckStatus.ERROR,
                "The release check did not complete.",
            )

            try:
                conf = await self.config.guild(
                    guild
                ).all()

                include_prereleases = bool(
                    conf["include_prereleases"]
                )

                require_apk = bool(
                    conf["require_apk"]
                )

                mode = (
                    "manual"
                    if manual
                    else "scheduled"
                )

                await self._verbose_log(
                    guild,
                    (
                        f"Checking GitHub for new releases "
                        f"({mode}). Policy: "
                        f"prereleases="
                        f"{'included' if include_prereleases else 'excluded'}, "
                        f"require_apk="
                        f"{'yes' if require_apk else 'no'}."
                    ),
                )

                log.debug(
                    "Starting %s release check for guild %s.",
                    mode,
                    guild.id,
                )

                if feed is None:
                    feed = await self.github.fetch_releases(
                        force=manual
                    )

                await self._verbose_log(
                    guild,
                    self._feed_summary(feed),
                )

                (
                    candidate,
                    draft_count,
                    prerelease_count,
                    excluded_prereleases,
                ) = self._select_candidate(
                    feed,
                    include_prereleases,
                )

                await self._verbose_log(
                    guild,
                    (
                        "Release classification: "
                        f"drafts={draft_count}, "
                        f"prereleases={prerelease_count}, "
                        f"excluded_by_policy="
                        f"{excluded_prereleases}."
                    ),
                )

                if draft_count:
                    await self._verbose_log(
                        guild,
                        (
                            f"Ignored {draft_count} draft "
                            f"release(s). Drafts are never "
                            f"announced."
                        ),
                    )

                if excluded_prereleases:
                    await self._verbose_log(
                        guild,
                        (
                            f"Ignored {excluded_prereleases} "
                            f"prerelease(s) because prerelease "
                            f"announcements are disabled."
                        ),
                    )

                if candidate is None:
                    if (
                        not include_prereleases
                        and prerelease_count
                    ):
                        message = (
                            "No eligible release was found. "
                            "GitHub returned prereleases, but "
                            "prerelease announcements are disabled."
                        )
                    else:
                        message = (
                            "No eligible published release "
                            "was found."
                        )

                    await self._verbose_log(
                        guild,
                        f"Decision: {message}",
                    )

                    result = CheckResult(
                        CheckStatus.NO_ELIGIBLE_RELEASE,
                        message,
                    )

                    return result

                await self._verbose_log(
                    guild,
                    (
                        "Selected newest eligible candidate: "
                        f"{self._release_summary(candidate)}."
                    ),
                )

                if candidate.prerelease:
                    await self._verbose_log(
                        guild,
                        (
                            "Policy decision: candidate has "
                            "prerelease=True and prerelease "
                            "announcements are enabled, so the "
                            "candidate is accepted."
                        ),
                    )
                else:
                    await self._verbose_log(
                        guild,
                        (
                            "Policy decision: candidate is a "
                            "full release and is accepted."
                        ),
                    )

                last_id = conf.get("last_release_id")
                last_tag = conf.get("last_release_tag")

                stored_release = self._find_stored_release(
                    feed,
                    last_id,
                    last_tag,
                )

                stored_published = self._parse_datetime(
                    conf.get("last_release_published")
                )

                # Backfill the new timestamp field without altering the
                # existing release boundary.
                if (
                    stored_published is None
                    and stored_release is not None
                ):
                    stored_published = (
                        stored_release.published
                    )

                    await self.config.guild(
                        guild
                    ).last_release_published.set(
                        stored_release.published.isoformat()
                    )

                stored_time = self._display_datetime(
                    stored_published
                )

                await self._verbose_log(
                    guild,
                    (
                        "Stored announcement state: "
                        f"id={last_id if last_id is not None else 'none'}, "
                        f"tag={last_tag or 'none'}, "
                        f"published={stored_time}."
                    ),
                )

                same_release = (
                    (
                        last_id is not None
                        and last_id == candidate.id
                    )
                    or (
                        last_id is None
                        and last_tag == candidate.version
                    )
                )

                if same_release:
                    # Complete a tag-only legacy state if necessary.
                    if last_id is None:
                        await self.config.guild(
                            guild
                        ).last_release_id.set(
                            candidate.id
                        )

                    if stored_published is None:
                        await self.config.guild(
                            guild
                        ).last_release_published.set(
                            candidate.published.isoformat()
                        )

                    message = (
                        f"No new release. "
                        f"{candidate.version} "
                        f"(id={candidate.id}) has already "
                        f"been announced."
                    )

                    await self._verbose_log(
                        guild,
                        f"Decision: {message}",
                    )

                    result = CheckResult(
                        CheckStatus.UP_TO_DATE,
                        message,
                        candidate,
                    )

                    return result

                # Do not announce an older stable release simply because
                # prereleases were disabled after a newer beta was saved.
                if stored_release is not None:
                    candidate_key = (
                        candidate.published,
                        candidate.id,
                    )

                    stored_key = (
                        stored_release.published,
                        stored_release.id,
                    )

                    if candidate_key <= stored_key:
                        message = (
                            f"No new eligible release. "
                            f"The selected candidate "
                            f"{candidate.version} is not newer "
                            f"than the stored release "
                            f"{stored_release.version}."
                        )

                        await self._verbose_log(
                            guild,
                            f"Decision: {message}",
                        )

                        result = CheckResult(
                            CheckStatus.UP_TO_DATE,
                            message,
                            candidate,
                        )

                        return result

                elif (
                    stored_published is not None
                    and candidate.published
                    <= stored_published
                ):
                    message = (
                        f"No new eligible release. "
                        f"{candidate.version} was published "
                        f"no later than the stored release "
                        f"boundary."
                    )

                    await self._verbose_log(
                        guild,
                        f"Decision: {message}",
                    )

                    result = CheckResult(
                        CheckStatus.UP_TO_DATE,
                        message,
                        candidate,
                    )

                    return result

                if require_apk and not candidate.has_apk:
                    message = (
                        f"Found new release "
                        f"{candidate.version}, but it currently "
                        f"has no APK asset. It will not be marked "
                        f"as announced and will be retried."
                    )

                    await self._verbose_log(
                        guild,
                        f"Decision: {message}",
                        level="WARNING",
                    )

                    result = CheckResult(
                        CheckStatus.WAITING_FOR_APK,
                        message,
                        candidate,
                    )

                    return result

                if last_id is None and not last_tag:
                    detection_reason = (
                        "no previous release is stored"
                    )
                elif (
                    stored_release is None
                    and stored_published is None
                ):
                    detection_reason = (
                        "the candidate ID differs from the "
                        "stored ID and the previous release is "
                        "outside the fetched release window"
                    )
                else:
                    detection_reason = (
                        "the candidate is newer than the "
                        "stored release boundary"
                    )

                await self._verbose_log(
                    guild,
                    (
                        "New release detected: "
                        f"{self._release_summary(candidate)}; "
                        f"reason={detection_reason}."
                    ),
                )

                log.info(
                    "New release %s detected for guild %s.",
                    candidate.version,
                    guild.id,
                )

                send_result = await self._send_release(
                    guild,
                    candidate,
                )

                if not send_result.success:
                    message = (
                        f"Announcement failed for "
                        f"{candidate.version}: "
                        f"{send_result.detail}"
                    )

                    await self._verbose_log(
                        guild,
                        message,
                        level="ERROR",
                    )

                    log.warning(
                        "Announcement failed for release %s "
                        "in guild %s: %s",
                        candidate.version,
                        guild.id,
                        send_result.detail,
                    )

                    result = CheckResult(
                        CheckStatus.ANNOUNCEMENT_FAILED,
                        message,
                        candidate,
                    )

                    return result

                await self._verbose_log(
                    guild,
                    send_result.detail,
                )

                # Persist only after the message has been sent.
                await self._save_release(
                    guild,
                    candidate,
                )

                await self._verbose_log(
                    guild,
                    (
                        f"Persisted announcement state: "
                        f"id={candidate.id}, "
                        f"tag={candidate.version}, "
                        f"published="
                        f"{candidate.published.isoformat()}."
                    ),
                )

                message = (
                    f"Announced {candidate.version} "
                    f"to {send_result.channel.mention}."
                )

                await self._verbose_log(
                    guild,
                    f"Check complete: {message}",
                )

                log.info(
                    "Announced %s to guild %s.",
                    candidate.version,
                    guild.id,
                )

                result = CheckResult(
                    CheckStatus.ANNOUNCED,
                    message,
                    candidate,
                )

                return result

            except GitHubError as exc:
                message = f"GitHub request failed: {exc}"

                log.warning(
                    "GitHub check failed for guild %s: %s",
                    guild.id,
                    exc,
                )

                await self._verbose_log(
                    guild,
                    message,
                    level="ERROR",
                )

                result = CheckResult(
                    CheckStatus.GITHUB_ERROR,
                    message,
                )

                return result

            except Exception as exc:
                log.exception(
                    "Unexpected release-check error "
                    "for guild %s.",
                    guild.id,
                )

                message = (
                    "Unexpected release-check error: "
                    f"{type(exc).__name__}: {exc}"
                )

                await self._verbose_log(
                    guild,
                    message,
                    level="ERROR",
                )

                result = CheckResult(
                    CheckStatus.ERROR,
                    message,
                )

                return result

            finally:
                try:
                    await self._record_check(
                        guild,
                        result.status,
                    )

                except Exception:
                    log.exception(
                        "Unable to save check state "
                        "for guild %s.",
                        guild.id,
                    )

    async def _latest_release(
        self,
        guild: discord.Guild,
        *,
        force: bool = True,
    ) -> Release:

        conf = await self.config.guild(guild).all()

        feed = await self.github.fetch_releases(
            force=force
        )

        (
            candidate,
            _,
            prerelease_count,
            _,
        ) = self._select_candidate(
            feed,
            bool(conf["include_prereleases"]),
        )

        if candidate is None:
            if (
                not conf["include_prereleases"]
                and prerelease_count
            ):
                raise GitHubError(
                    "No eligible release was found because "
                    "prerelease announcements are disabled."
                )

            raise GitHubError(
                "GitHub returned no eligible release."
            )

        return candidate

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.guild_only()
    @commands.admin_or_permissions(administrator=True)
    @commands.group(
        name="flux",
        invoke_without_command=True,
    )
    async def flux(self, ctx: commands.Context):
        """Flux Mobile configuration."""
        await ctx.send_help()

    @flux.command(name="enable")
    async def flux_enable(self, ctx: commands.Context):
        """Enable Flux Mobile monitoring."""

        scope = self.config.guild(ctx.guild)

        await scope.enabled.set(True)

        # Cause the scheduler to evaluate the guild on its next tick.
        await scope.last_check.set(None)
        await scope.last_check_result.set(None)

        await ctx.tick()

        await self._verbose_log(
            ctx.guild,
            f"{ctx.author} enabled Flux Mobile monitoring.",
        )

    @flux.command(name="disable")
    async def flux_disable(self, ctx: commands.Context):
        """Disable Flux Mobile monitoring."""

        await self.config.guild(
            ctx.guild
        ).enabled.set(False)

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
        """Set the announcement channel."""

        member = ctx.guild.me

        if member is None:
            return await ctx.send(
                "Unable to resolve my guild member."
            )

        permissions = channel.permissions_for(member)

        if not (
            permissions.send_messages
            and permissions.embed_links
        ):
            return await ctx.send(
                "I require Send Messages and Embed Links "
                "in that channel."
            )

        scope = self.config.guild(ctx.guild)

        await scope.announcement_channel.set(channel.id)

        # Re-evaluate immediately if a release previously failed because
        # the channel was missing or invalid.
        await scope.last_check.set(None)

        await ctx.send(
            f"Announcement channel set to "
            f"{channel.mention}."
        )

        await self._verbose_log(
            ctx.guild,
            (
                f"Announcement channel changed to "
                f"#{channel} ({channel.id})."
            ),
        )

    @flux.command(name="logs")
    async def flux_logs(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ):
        """Set the verbose logging channel."""

        member = ctx.guild.me

        if member is None:
            return await ctx.send(
                "Unable to resolve my guild member."
            )

        permissions = channel.permissions_for(member)

        if not permissions.send_messages:
            return await ctx.send(
                "I cannot send messages there."
            )

        await self.config.guild(
            ctx.guild
        ).log_channel.set(channel.id)

        await ctx.send(
            f"Verbose logging channel set to "
            f"{channel.mention}."
        )

        await self._verbose_log(
            ctx.guild,
            (
                f"Verbose logging channel changed to "
                f"#{channel} ({channel.id})."
            ),
        )

    @flux.command(name="verbose")
    async def flux_verbose(
        self,
        ctx: commands.Context,
        enabled: bool,
    ):
        """Enable or disable verbose guild logging."""

        await self.config.guild(
            ctx.guild
        ).verbose.set(enabled)

        await ctx.send(
            f"Verbose logging "
            f"{'enabled' if enabled else 'disabled'}."
        )

        if enabled:
            await self._verbose_log(
                ctx.guild,
                f"{ctx.author} enabled verbose logging.",
            )

    @flux.command(
        name="prereleases",
        aliases=["prerelease"],
    )
    async def flux_prereleases(
        self,
        ctx: commands.Context,
        enabled: bool,
    ):
        """
        Include or exclude GitHub prereleases.

        Fluxer Mobile prereleases are included by default.
        """

        scope = self.config.guild(ctx.guild)

        await scope.include_prereleases.set(enabled)
        await scope.last_check.set(None)

        await ctx.send(
            "Prerelease announcements "
            f"{'enabled' if enabled else 'disabled'}."
        )

        await self._verbose_log(
            ctx.guild,
            (
                f"{ctx.author} set prerelease "
                f"announcements to {enabled}."
            ),
        )

    @flux.command(name="requireapk")
    async def flux_require_apk(
        self,
        ctx: commands.Context,
        enabled: bool,
    ):
        """
        Require an APK asset before announcing a release.

        Enabling this avoids announcing while GitHub release assets are
        still being uploaded.
        """

        scope = self.config.guild(ctx.guild)

        await scope.require_apk.set(enabled)
        await scope.last_check.set(None)

        await ctx.send(
            "APK requirement "
            f"{'enabled' if enabled else 'disabled'}."
        )

        await self._verbose_log(
            ctx.guild,
            (
                f"{ctx.author} set the APK requirement "
                f"to {enabled}."
            ),
        )

    @flux.command(name="interval")
    async def flux_interval(
        self,
        ctx: commands.Context,
        minutes: int,
    ):
        """Set this guild's polling interval."""

        if minutes < MIN_CHECK_INTERVAL_MINUTES:
            return await ctx.send(
                f"The minimum interval is "
                f"{MIN_CHECK_INTERVAL_MINUTES} minutes."
            )

        await self.config.guild(
            ctx.guild
        ).interval.set(minutes)

        await ctx.send(
            f"Check interval set to {minutes} minutes."
        )

        await self._verbose_log(
            ctx.guild,
            (
                f"{ctx.author} changed the polling "
                f"interval to {minutes} minutes."
            ),
        )

    @flux.command(name="status")
    async def flux_status(
        self,
        ctx: commands.Context,
    ):
        """Display the current configuration."""

        conf = await self.config.guild(
            ctx.guild
        ).all()

        embed = discord.Embed(
            title="Flux Mobile Status",
            colour=await resolve_embed_colour(ctx),
        )

        announcement_channel = (
            ctx.guild.get_channel(
                conf["announcement_channel"]
            )
            if conf["announcement_channel"]
            else None
        )

        log_channel = (
            ctx.guild.get_channel(conf["log_channel"])
            if conf["log_channel"]
            else None
        )

        last_check = self._parse_datetime(
            conf.get("last_check")
        )

        last_result = (
            str(conf.get("last_check_result"))
            .replace("_", " ")
            .title()
            if conf.get("last_check_result")
            else "None"
        )

        embed.add_field(
            name="Enabled",
            value="Yes" if conf["enabled"] else "No",
        )

        embed.add_field(
            name="Announcements",
            value=(
                announcement_channel.mention
                if announcement_channel
                else "Not configured"
            ),
            inline=False,
        )

        embed.add_field(
            name="Prereleases",
            value=(
                "Included"
                if conf["include_prereleases"]
                else "Excluded"
            ),
        )

        embed.add_field(
            name="Require APK",
            value=(
                "Yes"
                if conf["require_apk"]
                else "No"
            ),
        )

        embed.add_field(
            name="Verbose Logs",
            value=(
                (
                    f"{log_channel.mention} "
                    f"({'enabled' if conf['verbose'] else 'disabled'})"
                )
                if log_channel
                else "Not configured"
            ),
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
            name="Last Check",
            value=self._display_datetime(last_check),
            inline=False,
        )

        embed.add_field(
            name="Last Result",
            value=last_result,
        )

        embed.add_field(
            name="Background Task",
            value=(
                "Running"
                if self.monitor.is_running()
                else "Stopped"
            ),
        )

        await ctx.send(embed=embed)

    @flux.command(name="latest")
    async def flux_latest(
        self,
        ctx: commands.Context,
    ):
        """Display the newest release allowed by guild policy."""

        async with ctx.typing():
            try:
                release = await self._latest_release(
                    ctx.guild,
                    force=True,
                )

            except GitHubError as exc:
                return await ctx.send(
                    f"GitHub error: `{exc}`"
                )

        embed = build_release_embed(
            release,
            await resolve_embed_colour(ctx),
        )

        await ctx.send(embed=embed)

    @flux.command(name="check")
    async def flux_check(
        self,
        ctx: commands.Context,
    ):
        """Force an immediate release check."""

        async with ctx.typing():
            result = await self._check_guild(
                ctx.guild,
                manual=True,
            )

        if result.status is CheckStatus.ANNOUNCED:
            await ctx.tick()

        await ctx.send(result.message)

    @flux.command(name="test")
    async def flux_test(
        self,
        ctx: commands.Context,
    ):
        """
        Send a test announcement without changing release state.
        """

        async with ctx.typing():
            try:
                release = await self._latest_release(
                    ctx.guild,
                    force=True,
                )

            except GitHubError as exc:
                return await ctx.send(
                    f"GitHub error: `{exc}`"
                )

        send_result = await self._send_release(
            ctx.guild,
            release,
            colour_source=ctx,
        )

        if send_result.success:
            await ctx.tick()
            await ctx.send(send_result.detail)
        else:
            await ctx.send(
                f"Unable to send the announcement: "
                f"{send_result.detail}"
            )

    # ------------------------------------------------------------------
    # Background monitor
    # ------------------------------------------------------------------

    @tasks.loop(seconds=MONITOR_TICK_SECONDS)
    async def monitor(self):
        """
        Run the per-guild scheduler.

        The GitHub feed is fetched once and shared by all guilds due
        during this scheduler tick.
        """

        if not self._ready:
            return

        now = datetime.now(timezone.utc)
        due_guilds: list[discord.Guild] = []

        for guild in self.bot.guilds:
            try:
                if await self._guild_due(guild, now):
                    due_guilds.append(guild)

            except Exception:
                log.exception(
                    "Unable to calculate schedule for guild %s.",
                    guild.id,
                )

        if not due_guilds:
            return

        log.debug(
            "%s guild(s) are due for a Flux Mobile check.",
            len(due_guilds),
        )

        try:
            feed = await self.github.fetch_releases()

        except GitHubError as exc:
            log.warning(
                "Scheduled GitHub request failed for "
                "%s guild(s): %s",
                len(due_guilds),
                exc,
            )

            for guild in due_guilds:
                await self._verbose_log(
                    guild,
                    (
                        "Scheduled GitHub release check "
                        "started."
                    ),
                )

                await self._verbose_log(
                    guild,
                    f"GitHub request failed: {exc}",
                    level="ERROR",
                )

                try:
                    await self._record_check(
                        guild,
                        CheckStatus.GITHUB_ERROR,
                    )
                except Exception:
                    log.exception(
                        "Unable to record failed check "
                        "for guild %s.",
                        guild.id,
                    )

            return

        for guild in due_guilds:
            try:
                await self._check_guild(
                    guild,
                    feed=feed,
                )

            except Exception:
                log.exception(
                    "Unexpected monitor error for guild %s.",
                    guild.id,
                )

                await self._verbose_log(
                    guild,
                    (
                        "Unexpected exception occurred "
                        "during the monitor run."
                    ),
                    level="ERROR",
                )

    @monitor.before_loop
    async def before_monitor(self):
        """Wait until Red is fully ready."""

        await self.bot.wait_until_red_ready()

    @monitor.error
    async def monitor_error(
        self,
        error: BaseException,
    ):
        """Log an exception that escapes the loop body."""

        log.exception(
            "Flux Mobile monitor stopped unexpectedly.",
            exc_info=error,
        )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @commands.is_owner()
    @commands.command(hidden=True)
    async def fluxdiag(
        self,
        ctx: commands.Context,
    ):
        """Owner diagnostics."""

        embed = discord.Embed(
            title="Flux Mobile Diagnostics",
            colour=await resolve_embed_colour(ctx),
        )

        embed.add_field(
            name="Cog Version",
            value=self.__version__,
        )

        embed.add_field(
            name="Monitor",
            value=(
                "Running"
                if self.monitor.is_running()
                else "Stopped"
            ),
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
            value=(
                "Ready"
                if self.github.is_ready
                else "Closed"
            ),
        )

        feed = self.github.last_feed

        if feed is None:
            feed_value = "No request completed yet."
        else:
            source = (
                "Cache"
                if feed.from_cache
                else "Network"
            )

            rate = "Unknown"

            if (
                feed.rate_limit_remaining is not None
                and feed.rate_limit_limit is not None
            ):
                rate = (
                    f"{feed.rate_limit_remaining}/"
                    f"{feed.rate_limit_limit}"
                )

            feed_value = (
                f"Source: {source}\n"
                f"Releases: {len(feed.releases)}\n"
                f"Rate limit: {rate}\n"
                f"Fetched: "
                f"{self._display_datetime(feed.fetched_at)}"
            )

        embed.add_field(
            name="Last GitHub Feed",
            value=feed_value,
            inline=False,
        )

        await ctx.send(embed=embed)
