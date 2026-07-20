"""A lightweight reaction-based suggestion board for Fluxer."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone
from math import ceil
from typing import Any, Dict, List, Optional, Set

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

log = logging.getLogger("red.five.fluxsuggestions")

UPVOTE_EMOJI = "👍"
DOWNVOTE_EMOJI = "👎"

PENDING_COLOUR = 0x5865F2
APPROVED_COLOUR = 0x57F287
REJECTED_COLOUR = 0xED4245

MAX_SUGGESTION_LENGTH = 4000
MAX_REASON_LENGTH = 1000
LIST_PAGE_SIZE = 10

PERMISSION_NAMES = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "add_reactions": "Add Reactions",
    "read_message_history": "Read Message History",
    "manage_messages": "Manage Messages",
}


class FluxSuggestions(commands.Cog):
    """A lightweight, reaction-based suggestion board for Fluxer."""

    __author__ = "Five"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot

        # Keep this identifier unchanged after publishing the cog.
        self.config = Config.get_conf(
            self,
            identifier=2007202602,
            force_registration=True,
        )

        self.config.register_guild(
            pending_channel=None,
            approved_channel=None,
            rejected_channel=None,
            dm_results=True,
            state={
                "schema": 1,
                "counter": 0,
                "suggestions": {},
                "submitted": 0,
                "approved": 0,
                "rejected": 0,
            },
        )

        self._guild_locks: Dict[int, asyncio.Lock] = {}

    def format_help_for_context(self, ctx: commands.Context) -> str:
        help_text = super().format_help_for_context(ctx)
        return (
            f"{help_text}\n\n"
            f"Version: {self.__version__}\n"
            f"Author: {self.__author__}\n"
            "Platform: Fluxer"
        )

    # -------------------------------------------------------------------------
    # General helpers
    # -------------------------------------------------------------------------

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        lock = self._guild_locks.get(guild_id)

        if lock is None:
            lock = asyncio.Lock()
            self._guild_locks[guild_id] = lock

        return lock

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _parse_datetime(cls, value: Any) -> datetime:
        if not isinstance(value, str) or not value.strip():
            return cls._utcnow()

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return cls._utcnow()

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        clean = " ".join(text.split())

        if len(clean) <= limit:
            return clean

        return clean[: limit - 1].rstrip() + "…"

    def _normalise_state(self, raw_state: Any) -> Dict[str, Any]:
        if isinstance(raw_state, dict):
            source = dict(raw_state)
        else:
            source = {}

        raw_suggestions = source.get("suggestions", {})
        suggestions: Dict[str, Dict[str, Any]] = {}
        numbers: List[int] = []

        if isinstance(raw_suggestions, dict):
            for key, value in raw_suggestions.items():
                try:
                    number = int(key)
                except (TypeError, ValueError):
                    continue

                if number < 1 or not isinstance(value, dict):
                    continue

                suggestions[str(number)] = dict(value)
                numbers.append(number)

        counter = max(
            self._safe_int(source.get("counter"), 0),
            max(numbers, default=0),
        )

        return {
            "schema": 1,
            "counter": max(0, counter),
            "suggestions": suggestions,
            "submitted": max(0, self._safe_int(source.get("submitted"), 0)),
            "approved": max(0, self._safe_int(source.get("approved"), 0)),
            "rejected": max(0, self._safe_int(source.get("rejected"), 0)),
        }

    def _get_record(
        self,
        state: Dict[str, Any],
        number: int,
    ) -> Optional[Dict[str, Any]]:
        raw = state.get("suggestions", {}).get(str(number))

        if not isinstance(raw, dict):
            return None

        content = str(raw.get("content") or "").strip()

        if not content:
            return None

        author_id = self._safe_int(raw.get("author_id"), 0) or None
        channel_id = self._safe_int(raw.get("channel_id"), 0) or None
        message_id = self._safe_int(raw.get("message_id"), 0) or None

        created = raw.get("created")
        if not isinstance(created, str) or not created.strip():
            created = self._utcnow().isoformat()

        return {
            "number": number,
            "author_id": author_id,
            "channel_id": channel_id,
            "message_id": message_id,
            "content": content[:MAX_SUGGESTION_LENGTH],
            "created": created,
            "status": "pending",
        }

    @staticmethod
    def _text_channel(
        guild: discord.Guild,
        channel_id: Any,
    ) -> Optional[discord.TextChannel]:
        try:
            resolved_id = int(channel_id)
        except (TypeError, ValueError):
            return None

        channel = guild.get_channel(resolved_id)

        if isinstance(channel, discord.TextChannel):
            return channel

        return None

    @staticmethod
    def _channel_label(guild: discord.Guild, channel_id: Any) -> str:
        if not channel_id:
            return "Not configured"

        try:
            resolved_id = int(channel_id)
        except (TypeError, ValueError):
            return "Invalid channel ID"

        channel = guild.get_channel(resolved_id)

        if isinstance(channel, discord.TextChannel):
            return channel.mention

        return f"Missing channel (`{resolved_id}`)"

    @staticmethod
    def _record_jump_url(
        guild_id: int,
        record: Dict[str, Any],
    ) -> Optional[str]:
        channel_id = record.get("channel_id")
        message_id = record.get("message_id")

        if not channel_id or not message_id:
            return None

        return (
            f"https://fluxer.app/channels/"
            f"{guild_id}/{channel_id}/{message_id}"
        )

    @staticmethod
    def _missing_permissions(
        channel: discord.TextChannel,
        required: List[str],
    ) -> List[str]:
        member = channel.guild.me

        if member is None:
            return ["Unable to resolve the bot's community member"]

        permissions = channel.permissions_for(member)
        missing: List[str] = []

        for permission_name in required:
            if not getattr(permissions, permission_name, False):
                missing.append(
                    PERMISSION_NAMES.get(
                        permission_name,
                        permission_name.replace("_", " ").title(),
                    )
                )

        return missing

    async def _delete_invocation(self, ctx: commands.Context) -> bool:
        message = getattr(ctx, "message", None)

        if message is None:
            return True

        try:
            await message.delete()
            return True
        except discord.NotFound:
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    async def _delete_and_respond(
        self,
        ctx: commands.Context,
        text: str,
    ) -> None:
        deleted = await self._delete_invocation(ctx)

        if not deleted:
            text += (
                "\n\nI could not delete your command message. "
                "Please delete it manually to protect your suggestion."
            )

        with suppress(discord.HTTPException):
            await ctx.send(text, delete_after=15)

    async def _notify_failed_submission(
        self,
        ctx: commands.Context,
    ) -> None:
        text = (
            "I could not submit your suggestion. Your command message was "
            "deleted for privacy. Please try again or contact community staff."
        )

        try:
            await ctx.author.send(text)
            return
        except discord.HTTPException:
            pass

        with suppress(discord.HTTPException):
            await ctx.send(text, delete_after=15)

    async def _notify_submission(
        self,
        ctx: commands.Context,
        number: int,
        message: discord.Message,
        channel: discord.TextChannel,
    ) -> None:
        text = f"Your suggestion #{number} has been posted anonymously."

        try:
            if channel.permissions_for(ctx.author).view_channel:
                text += f"\n{message.jump_url}"
        except (AttributeError, TypeError):
            pass

        try:
            await ctx.author.send(text)
            return
        except discord.HTTPException:
            pass

        with suppress(discord.HTTPException):
            await ctx.send(
                f"Suggestion #{number} was submitted successfully.",
                delete_after=10,
            )

    # -------------------------------------------------------------------------
    # Embed helpers
    # -------------------------------------------------------------------------

    def _build_pending_embed(
        self,
        number: int,
        record: Dict[str, Any],
    ) -> discord.Embed:
        embed = discord.Embed(
            title=f"💡 Suggestion #{number}",
            description=record["content"],
            colour=discord.Colour(PENDING_COLOUR),
            timestamp=self._parse_datetime(record.get("created")),
        )

        embed.set_footer(
            text=(
                f"Suggested anonymously • "
                f"Vote with {UPVOTE_EMOJI} or {DOWNVOTE_EMOJI}"
            )
        )

        return embed

    async def _build_result_embed(
        self,
        guild: discord.Guild,
        number: int,
        record: Dict[str, Any],
        approved: bool,
        reason: Optional[str],
        votes: Dict[str, Any],
    ) -> discord.Embed:
        if approved:
            status_text = "✅ Approved"
            title = f"✅ Approved Suggestion #{number}"
            colour = APPROVED_COLOUR
        else:
            status_text = "❌ Rejected"
            title = f"❌ Rejected Suggestion #{number}"
            colour = REJECTED_COLOUR

        embed = discord.Embed(
            title=title,
            description=record["content"],
            colour=discord.Colour(colour),
            timestamp=self._parse_datetime(record.get("created")),
        )

        embed.add_field(
            name="Status",
            value=status_text,
            inline=True,
        )

        vote_lines = [
            f"{UPVOTE_EMOJI} **{votes['up']}**",
            f"{DOWNVOTE_EMOJI} **{votes['down']}**",
        ]

        if votes.get("conflicts", 0):
            vote_lines.append(
                f"⚠️ Dual votes ignored: **{votes['conflicts']}**"
            )

        embed.add_field(
            name="Votes",
            value="\n".join(vote_lines),
            inline=True,
        )

        if reason:
            embed.add_field(
                name="Staff note" if approved else "Reason",
                value=reason,
                inline=False,
            )

        if approved:
            author_id = record.get("author_id")
            user = await self._resolve_user(guild, author_id)

            if user is not None:
                display_name = getattr(
                    user,
                    "display_name",
                    getattr(user, "name", str(user)),
                )
                footer_text = f"Suggested by {display_name} ({user.id})"

                avatar = getattr(user, "display_avatar", None)
                avatar_url = getattr(avatar, "url", None)

                if avatar_url:
                    embed.set_footer(
                        text=footer_text,
                        icon_url=str(avatar_url),
                    )
                else:
                    embed.set_footer(text=footer_text)

            elif author_id:
                embed.set_footer(
                    text=f"Suggested by user ID {author_id}"
                )
            else:
                embed.set_footer(
                    text="Suggested by an unavailable user"
                )
        else:
            embed.set_footer(text="Suggested anonymously")

        return embed

    @staticmethod
    def _build_closed_embed(
        result_embed: discord.Embed,
        result_message: discord.Message,
    ) -> discord.Embed:
        closed_embed = result_embed.copy()

        closed_embed.add_field(
            name="Voting closed",
            value=(
                f"This suggestion has been archived. "
                f"[View the result]({result_message.jump_url})"
            ),
            inline=False,
        )

        return closed_embed

    # -------------------------------------------------------------------------
    # Reaction helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _find_reaction(
        message: discord.Message,
        emoji: str,
    ) -> Optional[discord.Reaction]:
        for reaction in message.reactions:
            if str(reaction.emoji) == emoji:
                return reaction

        return None

    async def _add_seed_reactions(
        self,
        message: discord.Message,
    ) -> None:
        await message.add_reaction(UPVOTE_EMOJI)
        await message.add_reaction(DOWNVOTE_EMOJI)

    async def _repair_seed_reactions(
        self,
        message: discord.Message,
    ) -> List[str]:
        added: List[str] = []

        for emoji in (UPVOTE_EMOJI, DOWNVOTE_EMOJI):
            reaction = self._find_reaction(message, emoji)

            if reaction is None or not getattr(reaction, "me", False):
                await message.add_reaction(emoji)
                added.append(emoji)

        return added

    async def _reaction_user_ids(
        self,
        message: discord.Message,
        emoji: str,
    ) -> Set[int]:
        reaction = self._find_reaction(message, emoji)

        if reaction is None:
            return set()

        users: Set[int] = set()
        bot_user_id = getattr(self.bot.user, "id", None)

        async for user in reaction.users():
            if user.id == bot_user_id:
                continue

            if getattr(user, "bot", False):
                continue

            users.add(user.id)

        return users

    def _fallback_reaction_count(
        self,
        message: discord.Message,
        emoji: str,
    ) -> int:
        reaction = self._find_reaction(message, emoji)

        if reaction is None:
            return 0

        count = max(0, self._safe_int(getattr(reaction, "count", 0), 0))

        if getattr(reaction, "me", False):
            count = max(0, count - 1)

        return count

    async def _read_votes(
        self,
        message: discord.Message,
    ) -> Dict[str, Any]:
        """
        Read current votes directly from Fluxer.

        When reaction user lists are available, bot accounts are excluded and
        users who selected both choices are removed from both totals.

        If reaction-user retrieval fails, the cog falls back to reaction counts
        and subtracts its own seed reactions.
        """

        try:
            upvoters = await self._reaction_user_ids(
                message,
                UPVOTE_EMOJI,
            )
            downvoters = await self._reaction_user_ids(
                message,
                DOWNVOTE_EMOJI,
            )

            conflicts = upvoters.intersection(downvoters)
            upvoters.difference_update(conflicts)
            downvoters.difference_update(conflicts)

            return {
                "up": len(upvoters),
                "down": len(downvoters),
                "conflicts": len(conflicts),
                "exact": True,
            }

        except Exception as exc:
            log.debug(
                "Unable to retrieve exact reaction users for message %s; "
                "falling back to reaction counts: %s",
                message.id,
                exc,
            )

            return {
                "up": self._fallback_reaction_count(
                    message,
                    UPVOTE_EMOJI,
                ),
                "down": self._fallback_reaction_count(
                    message,
                    DOWNVOTE_EMOJI,
                ),
                "conflicts": 0,
                "exact": False,
            }

    # -------------------------------------------------------------------------
    # User helpers
    # -------------------------------------------------------------------------

    async def _resolve_user(
        self,
        guild: discord.Guild,
        user_id: Any,
    ) -> Optional[Any]:
        resolved_id = self._safe_int(user_id, 0)

        if not resolved_id:
            return None

        user = guild.get_member(resolved_id)

        if user is not None:
            return user

        user = self.bot.get_user(resolved_id)

        if user is not None:
            return user

        fetch_user = getattr(self.bot, "fetch_user", None)

        if fetch_user is None:
            return None

        try:
            return await fetch_user(resolved_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _dm_result(
        self,
        guild: discord.Guild,
        record: Dict[str, Any],
        number: int,
        approved: bool,
        reason: Optional[str],
        votes: Dict[str, Any],
        result_message: discord.Message,
        enabled: bool,
    ) -> None:
        if not enabled:
            return

        author_id = record.get("author_id")

        if not author_id:
            return

        user = await self._resolve_user(guild, author_id)

        if user is None:
            return

        result_word = "approved" if approved else "rejected"

        lines = [
            f"Your suggestion #{number} has been {result_word}.",
            (
                f"Votes: {UPVOTE_EMOJI} {votes['up']} • "
                f"{DOWNVOTE_EMOJI} {votes['down']}"
            ),
        ]

        if votes.get("conflicts", 0):
            lines.append(
                f"Dual votes ignored: {votes['conflicts']}"
            )

        if reason:
            lines.append(
                f"{'Staff note' if approved else 'Reason'}: {reason}"
            )

        if isinstance(user, discord.Member):
            try:
                if result_message.channel.permissions_for(user).view_channel:
                    lines.append(result_message.jump_url)
            except (AttributeError, TypeError):
                pass

        try:
            await user.send(
                "\n".join(lines),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.debug(
                "Unable to DM suggestion result to user %s in guild %s.",
                author_id,
                guild.id,
            )

    # -------------------------------------------------------------------------
    # Configuration helpers
    # -------------------------------------------------------------------------

    async def _set_channel(
        self,
        ctx: commands.Context,
        channel_type: str,
        channel: discord.TextChannel,
    ) -> None:
        if channel_type == "pending":
            required = [
                "view_channel",
                "send_messages",
                "embed_links",
                "add_reactions",
                "read_message_history",
            ]
            field_name = "pending_channel"
        elif channel_type == "approved":
            required = [
                "view_channel",
                "send_messages",
                "embed_links",
            ]
            field_name = "approved_channel"
        elif channel_type == "rejected":
            required = [
                "view_channel",
                "send_messages",
                "embed_links",
            ]
            field_name = "rejected_channel"
        else:
            return await ctx.send("Unknown suggestion channel type.")

        missing = self._missing_permissions(channel, required)

        if missing:
            return await ctx.send(
                f"I cannot use {channel.mention} as the "
                f"{channel_type} channel.\n"
                f"Missing permissions: {', '.join(missing)}."
            )

        scope = self.config.guild(ctx.guild)
        await getattr(scope, field_name).set(channel.id)

        text = (
            f"The {channel_type} suggestions channel has been set to "
            f"{channel.mention}."
        )

        if channel_type == "pending":
            state = self._normalise_state(await scope.state())
            pending_count = len(state["suggestions"])

            if pending_count:
                text += (
                    "\nExisting pending suggestions remain in their original "
                    "channels. New suggestions will use the new channel."
                )

        await ctx.send(text)

    async def _send_settings(
        self,
        ctx: commands.Context,
    ) -> None:
        conf = await self.config.guild(ctx.guild).all()
        state = self._normalise_state(conf.get("state"))

        embed = discord.Embed(
            title="Flux Suggestions Settings",
            colour=discord.Colour(PENDING_COLOUR),
        )

        embed.add_field(
            name="Pending channel",
            value=self._channel_label(
                ctx.guild,
                conf.get("pending_channel"),
            ),
            inline=False,
        )
        embed.add_field(
            name="Approved channel",
            value=self._channel_label(
                ctx.guild,
                conf.get("approved_channel"),
            ),
            inline=False,
        )
        embed.add_field(
            name="Rejected channel",
            value=self._channel_label(
                ctx.guild,
                conf.get("rejected_channel"),
            ),
            inline=False,
        )
        embed.add_field(
            name="Result DMs",
            value="Enabled" if conf.get("dm_results", True) else "Disabled",
            inline=True,
        )
        embed.add_field(
            name="Pending suggestions",
            value=str(len(state["suggestions"])),
            inline=True,
        )
        embed.add_field(
            name="Next number",
            value=str(state["counter"] + 1),
            inline=True,
        )
        embed.add_field(
            name="Voting",
            value=(
                f"Native {UPVOTE_EMOJI} and {DOWNVOTE_EMOJI} reactions\n"
                "Bot votes are excluded\n"
                "Users selecting both choices are ignored"
            ),
            inline=False,
        )

        embed.set_footer(text=f"Flux Suggestions v{self.__version__}")

        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            await ctx.send(
                "Flux Suggestions Settings\n"
                f"Pending: {self._channel_label(ctx.guild, conf.get('pending_channel'))}\n"
                f"Approved: {self._channel_label(ctx.guild, conf.get('approved_channel'))}\n"
                f"Rejected: {self._channel_label(ctx.guild, conf.get('rejected_channel'))}\n"
                f"Result DMs: {'Enabled' if conf.get('dm_results', True) else 'Disabled'}\n"
                f"Pending suggestions: {len(state['suggestions'])}\n"
                f"Next number: {state['counter'] + 1}"
            )

    # -------------------------------------------------------------------------
    # User command
    # -------------------------------------------------------------------------

    @commands.command(
        name="idea",
        aliases=["suggest"],
    )
    @commands.guild_only()
    async def idea(
        self,
        ctx: commands.Context,
        *,
        content: Optional[str] = None,
    ) -> None:
        """
        Submit an anonymous suggestion.

        The bot deletes the command message and posts the suggestion in the
        configured pending channel.
        """

        member = ctx.guild.me

        if member is None:
            return await ctx.send(
                "I could not resolve my community member."
            )

        source_permissions = ctx.channel.permissions_for(member)

        if not getattr(source_permissions, "manage_messages", False):
            return await ctx.send(
                "I need Manage Messages in this channel before I can accept "
                "anonymous suggestions. Nothing was submitted. Please delete "
                "your command message manually."
            )

        suggestion_text = (content or "").strip()

        if not suggestion_text:
            return await self._delete_and_respond(
                ctx,
                f"Please include a suggestion.\n"
                f"Example: `{ctx.clean_prefix}idea Add a new feature.`",
            )

        if len(suggestion_text) > MAX_SUGGESTION_LENGTH:
            return await self._delete_and_respond(
                ctx,
                f"Suggestions may contain no more than "
                f"{MAX_SUGGESTION_LENGTH} characters.",
            )

        lock = self._lock_for(ctx.guild.id)

        async with lock:
            scope = self.config.guild(ctx.guild)
            conf = await scope.all()

            pending_channel = self._text_channel(
                ctx.guild,
                conf.get("pending_channel"),
            )

            if pending_channel is None:
                return await self._delete_and_respond(
                    ctx,
                    "The pending suggestions channel has not been configured "
                    "or no longer exists.",
                )

            missing = self._missing_permissions(
                pending_channel,
                [
                    "view_channel",
                    "send_messages",
                    "embed_links",
                    "add_reactions",
                    "read_message_history",
                ],
            )

            if missing:
                return await self._delete_and_respond(
                    ctx,
                    "I cannot post suggestions in the configured pending "
                    f"channel. Missing permissions: {', '.join(missing)}.",
                )

            state = self._normalise_state(conf.get("state"))
            number = state["counter"] + 1

            record: Dict[str, Any] = {
                "number": number,
                "author_id": ctx.author.id,
                "channel_id": pending_channel.id,
                "message_id": None,
                "content": suggestion_text,
                "created": self._utcnow().isoformat(),
                "status": "pending",
            }

            deleted = await self._delete_invocation(ctx)

            if not deleted:
                return await ctx.send(
                    "I could not delete your command message, so the "
                    "suggestion was not submitted. Please delete your message "
                    "manually and try again.",
                    delete_after=15,
                )

            posted_message: Optional[discord.Message] = None

            try:
                embed = self._build_pending_embed(number, record)
                posted_message = await pending_channel.send(embed=embed)
                await self._add_seed_reactions(posted_message)

                record["message_id"] = posted_message.id
                state["suggestions"][str(number)] = record
                state["counter"] = number
                state["submitted"] += 1

                await scope.state.set(state)

            except discord.HTTPException as exc:
                if posted_message is not None:
                    with suppress(discord.HTTPException):
                        await posted_message.delete()

                log.warning(
                    "Unable to post suggestion %s in guild %s: %s",
                    number,
                    ctx.guild.id,
                    exc,
                )

                return await self._notify_failed_submission(ctx)

            except Exception:
                if posted_message is not None:
                    with suppress(discord.HTTPException):
                        await posted_message.delete()

                log.exception(
                    "Unable to save suggestion %s in guild %s.",
                    number,
                    ctx.guild.id,
                )

                return await self._notify_failed_submission(ctx)

        await self._notify_submission(
            ctx,
            number,
            posted_message,
            pending_channel,
        )

    # -------------------------------------------------------------------------
    # Approval and rejection
    # -------------------------------------------------------------------------

    async def _resolve_suggestion(
        self,
        ctx: commands.Context,
        number: int,
        approved: bool,
        reason: Optional[str],
    ) -> None:
        if number < 1:
            return await ctx.send(
                "Suggestion numbers must be greater than zero."
            )

        if reason:
            reason = reason.strip() or None

        if reason and len(reason) > MAX_REASON_LENGTH:
            return await ctx.send(
                f"Staff reasons may contain no more than "
                f"{MAX_REASON_LENGTH} characters."
            )

        lock = self._lock_for(ctx.guild.id)

        final_message: Optional[discord.Message] = None
        record: Optional[Dict[str, Any]] = None
        votes: Optional[Dict[str, Any]] = None
        cleanup_warning: Optional[str] = None
        dm_results = True

        async with lock:
            scope = self.config.guild(ctx.guild)
            conf = await scope.all()
            state = self._normalise_state(conf.get("state"))

            record = self._get_record(state, number)

            if record is None:
                return await ctx.send(
                    f"Pending suggestion #{number} does not exist."
                )

            destination_key = (
                "approved_channel" if approved else "rejected_channel"
            )
            destination_name = "approved" if approved else "rejected"

            destination_channel = self._text_channel(
                ctx.guild,
                conf.get(destination_key),
            )

            if destination_channel is None:
                return await ctx.send(
                    f"The {destination_name} suggestions channel has not "
                    "been configured or no longer exists."
                )

            destination_missing = self._missing_permissions(
                destination_channel,
                [
                    "view_channel",
                    "send_messages",
                    "embed_links",
                ],
            )

            if destination_missing:
                return await ctx.send(
                    f"I cannot send results to "
                    f"{destination_channel.mention}. Missing permissions: "
                    f"{', '.join(destination_missing)}."
                )

            source_channel_id = (
                record.get("channel_id")
                or conf.get("pending_channel")
            )
            source_channel = self._text_channel(
                ctx.guild,
                source_channel_id,
            )

            if source_channel is None:
                return await ctx.send(
                    "The pending suggestion's original channel is "
                    "unavailable. Recreate it with "
                    f"`{ctx.clean_prefix}suggestions resend {number}`."
                )

            source_missing = self._missing_permissions(
                source_channel,
                [
                    "view_channel",
                    "read_message_history",
                ],
            )

            if source_missing:
                return await ctx.send(
                    f"I cannot read the pending suggestion in "
                    f"{source_channel.mention}. Missing permissions: "
                    f"{', '.join(source_missing)}."
                )

            message_id = record.get("message_id")

            if not message_id:
                return await ctx.send(
                    "The pending suggestion has no stored message ID. "
                    f"Use `{ctx.clean_prefix}suggestions resend {number}`."
                )

            try:
                source_message = await source_channel.fetch_message(
                    message_id
                )
            except discord.NotFound:
                return await ctx.send(
                    "The pending suggestion message has been deleted. "
                    f"Use `{ctx.clean_prefix}suggestions resend {number}` "
                    "before resolving it."
                )
            except discord.Forbidden:
                return await ctx.send(
                    "Fluxer refused access to the pending suggestion message."
                )
            except discord.HTTPException as exc:
                return await ctx.send(
                    f"I could not retrieve the pending suggestion: `{exc}`"
                )

            votes = await self._read_votes(source_message)

            result_embed = await self._build_result_embed(
                ctx.guild,
                number,
                record,
                approved,
                reason,
                votes,
            )

            try:
                final_message = await destination_channel.send(
                    embed=result_embed
                )
            except discord.Forbidden:
                return await ctx.send(
                    f"Fluxer refused the result message in "
                    f"{destination_channel.mention}."
                )
            except discord.HTTPException as exc:
                return await ctx.send(
                    f"I could not post the result: `{exc}`\n"
                    "The pending suggestion has not been changed."
                )

            state["suggestions"].pop(str(number), None)

            if approved:
                state["approved"] += 1
            else:
                state["rejected"] += 1

            try:
                await scope.state.set(state)
            except Exception:
                log.exception(
                    "Unable to save the result of suggestion %s in guild %s.",
                    number,
                    ctx.guild.id,
                )

                rollback_succeeded = True

                try:
                    await final_message.delete()
                except discord.HTTPException:
                    rollback_succeeded = False
                    log.exception(
                        "Unable to remove the untracked result message for "
                        "suggestion %s in guild %s.",
                        number,
                        ctx.guild.id,
                    )

                if rollback_succeeded:
                    return await ctx.send(
                        "I posted the result but could not save its state. "
                        "The result message was removed and the pending "
                        "suggestion remains open."
                    )

                return await ctx.send(
                    "I posted the result but could not save its state or "
                    "remove the untracked result message. Please remove the "
                    "new result message manually before trying again."
                )

            try:
                await source_message.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                try:
                    closed_embed = self._build_closed_embed(
                        result_embed,
                        final_message,
                    )
                    await source_message.edit(embed=closed_embed)
                    cleanup_warning = (
                        "The old pending message could not be deleted, "
                        "so it was marked as closed instead."
                    )
                except discord.HTTPException:
                    cleanup_warning = (
                        "The old pending message could not be deleted or "
                        "marked as closed. Please remove it manually."
                    )

                    log.warning(
                        "Unable to close pending message %s for suggestion %s "
                        "in guild %s.",
                        source_message.id,
                        number,
                        ctx.guild.id,
                    )

            dm_results = bool(conf.get("dm_results", True))

        await self._dm_result(
            ctx.guild,
            record,
            number,
            approved,
            reason,
            votes,
            final_message,
            dm_results,
        )

        action = "approved" if approved else "rejected"

        confirmation = (
            f"Suggestion #{number} has been {action}.\n"
            f"{UPVOTE_EMOJI} {votes['up']} • "
            f"{DOWNVOTE_EMOJI} {votes['down']}"
        )

        if votes.get("conflicts", 0):
            confirmation += (
                f" • Dual votes ignored: {votes['conflicts']}"
            )

        if cleanup_warning:
            confirmation += f"\n{cleanup_warning}"

        await ctx.send(confirmation)

    @commands.command(name="approve")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    async def approve(
        self,
        ctx: commands.Context,
        number: int,
        *,
        reason: Optional[str] = None,
    ) -> None:
        """
        Approve a pending suggestion.

        Approved suggestions reveal their author.
        """

        await self._resolve_suggestion(
            ctx,
            number,
            approved=True,
            reason=reason,
        )

    @commands.command(name="reject")
    @commands.guild_only()
    @commands.mod_or_permissions(manage_messages=True)
    async def reject(
        self,
        ctx: commands.Context,
        number: int,
        *,
        reason: Optional[str] = None,
    ) -> None:
        """
        Reject a pending suggestion.

        Rejected suggestions remain anonymous.
        """

        await self._resolve_suggestion(
            ctx,
            number,
            approved=False,
            reason=reason,
        )

    # -------------------------------------------------------------------------
    # Suggestions command group
    # -------------------------------------------------------------------------

    @commands.group(
        name="suggestions",
        aliases=["suggestionset"],
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def suggestions(
        self,
        ctx: commands.Context,
    ) -> None:
        """View or configure Flux Suggestions."""

        if ctx.invoked_subcommand is None:
            await self._send_settings(ctx)

    @suggestions.command(
        name="settings",
        aliases=["status"],
    )
    async def suggestions_settings(
        self,
        ctx: commands.Context,
    ) -> None:
        """Display the current suggestion configuration."""

        await self._send_settings(ctx)

    # -------------------------------------------------------------------------
    # Channel configuration
    # -------------------------------------------------------------------------

    @suggestions.group(
        name="set",
        invoke_without_command=True,
    )
    @commands.admin_or_permissions(manage_guild=True)
    async def suggestions_set(
        self,
        ctx: commands.Context,
    ) -> None:
        """Configure suggestion channels."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @suggestions_set.command(name="pending")
    async def suggestions_set_pending(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel used for pending suggestions."""

        await self._set_channel(ctx, "pending", channel)

    @suggestions_set.command(name="approved")
    async def suggestions_set_approved(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel used for approved suggestions."""

        await self._set_channel(ctx, "approved", channel)

    @suggestions_set.command(name="rejected")
    async def suggestions_set_rejected(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel used for rejected suggestions."""

        await self._set_channel(ctx, "rejected", channel)

    @suggestions.command(name="clear")
    @commands.admin_or_permissions(manage_guild=True)
    async def suggestions_clear(
        self,
        ctx: commands.Context,
        channel_type: str,
    ) -> None:
        """
        Clear a configured suggestion channel.

        Valid channel types are pending, approved, and rejected.
        """

        channel_type = channel_type.lower().strip()

        field_names = {
            "pending": "pending_channel",
            "approved": "approved_channel",
            "rejected": "rejected_channel",
        }

        field_name = field_names.get(channel_type)

        if field_name is None:
            return await ctx.send(
                "Channel type must be `pending`, `approved`, or `rejected`."
            )

        scope = self.config.guild(ctx.guild)
        await getattr(scope, field_name).set(None)

        text = f"The {channel_type} suggestions channel has been cleared."

        if channel_type == "pending":
            state = self._normalise_state(await scope.state())

            if state["suggestions"]:
                text += (
                    "\nExisting pending suggestions remain stored and can "
                    "still be approved or rejected from their original "
                    "channels. New submissions are disabled until another "
                    "pending channel is configured."
                )

        await ctx.send(text)

    @suggestions.command(name="dm")
    @commands.admin_or_permissions(manage_guild=True)
    async def suggestions_dm(
        self,
        ctx: commands.Context,
        enabled: bool,
    ) -> None:
        """Enable or disable result DMs to suggestion authors."""

        await self.config.guild(ctx.guild).dm_results.set(enabled)

        await ctx.send(
            f"Suggestion result DMs are now "
            f"{'enabled' if enabled else 'disabled'}."
        )

    # -------------------------------------------------------------------------
    # Pending suggestion utilities
    # -------------------------------------------------------------------------

    @suggestions.command(name="list")
    @commands.mod_or_permissions(manage_messages=True)
    async def suggestions_list(
        self,
        ctx: commands.Context,
        mode: str = "pending",
        page: int = 1,
    ) -> None:
        """
        List pending suggestions.

        Examples:
        - [p]suggestions list
        - [p]suggestions list pending
        - [p]suggestions list pending 2
        - [p]suggestions list 2
        """

        if mode.isdigit() and page == 1:
            page = int(mode)
            mode = "pending"

        mode = mode.lower().strip()

        if mode not in {"pending", "open"}:
            return await ctx.send(
                "Only pending suggestions can be listed."
            )

        if page < 1:
            return await ctx.send(
                "The page number must be greater than zero."
            )

        state = self._normalise_state(
            await self.config.guild(ctx.guild).state()
        )

        records: List[Dict[str, Any]] = []

        for number_text in sorted(
            state["suggestions"],
            key=lambda value: int(value),
        ):
            record = self._get_record(
                state,
                int(number_text),
            )

            if record is not None:
                records.append(record)

        if not records:
            return await ctx.send(
                "There are no pending suggestions."
            )

        total_pages = max(
            1,
            ceil(len(records) / LIST_PAGE_SIZE),
        )

        if page > total_pages:
            return await ctx.send(
                f"Page {page} does not exist. "
                f"There are {total_pages} page(s)."
            )

        start = (page - 1) * LIST_PAGE_SIZE
        selected = records[start : start + LIST_PAGE_SIZE]

        entries: List[str] = []

        for record in selected:
            number = record["number"]
            preview = self._shorten(record["content"], 140)
            created = self._parse_datetime(
                record.get("created")
            ).strftime("%Y-%m-%d %H:%M UTC")

            jump_url = self._record_jump_url(
                ctx.guild.id,
                record,
            )

            if jump_url:
                heading = f"[Suggestion #{number}]({jump_url})"
            else:
                heading = f"Suggestion #{number}"

            entries.append(
                f"**{heading}** — {created}\n"
                f"> {preview}"
            )

        embed = discord.Embed(
            title="Pending Suggestions",
            description="\n\n".join(entries),
            colour=discord.Colour(PENDING_COLOUR),
        )

        embed.set_footer(
            text=(
                f"Page {page}/{total_pages} • "
                f"{len(records)} pending"
            )
        )

        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            await ctx.send("\n\n".join(entries))

    @suggestions.command(name="resend")
    @commands.mod_or_permissions(manage_messages=True)
    async def suggestions_resend(
        self,
        ctx: commands.Context,
        number: int,
    ) -> None:
        """
        Repair or recreate a pending suggestion.

        If the original message still exists, missing bot reactions are added.
        If it has been deleted, it is recreated in the current pending channel.
        """

        if number < 1:
            return await ctx.send(
                "Suggestion numbers must be greater than zero."
            )

        lock = self._lock_for(ctx.guild.id)

        async with lock:
            scope = self.config.guild(ctx.guild)
            conf = await scope.all()
            state = self._normalise_state(conf.get("state"))
            record = self._get_record(state, number)

            if record is None:
                return await ctx.send(
                    f"Pending suggestion #{number} does not exist."
                )

            old_channel_id = (
                record.get("channel_id")
                or conf.get("pending_channel")
            )
            old_channel = self._text_channel(
                ctx.guild,
                old_channel_id,
            )
            old_message_id = record.get("message_id")

            existing_message: Optional[discord.Message] = None

            if old_channel is not None and old_message_id:
                missing = self._missing_permissions(
                    old_channel,
                    [
                        "view_channel",
                        "read_message_history",
                    ],
                )

                if missing:
                    return await ctx.send(
                        "I cannot verify whether the original suggestion "
                        f"still exists in {old_channel.mention}. Missing "
                        f"permissions: {', '.join(missing)}."
                    )

                try:
                    existing_message = await old_channel.fetch_message(
                        old_message_id
                    )
                except discord.NotFound:
                    existing_message = None
                except discord.Forbidden:
                    return await ctx.send(
                        "Fluxer refused access to the original suggestion "
                        "message. It was not recreated to avoid a duplicate."
                    )
                except discord.HTTPException as exc:
                    return await ctx.send(
                        f"I could not check the original suggestion: `{exc}`"
                    )

            if existing_message is not None:
                try:
                    added = await self._repair_seed_reactions(
                        existing_message
                    )
                except discord.HTTPException as exc:
                    return await ctx.send(
                        f"The suggestion exists, but I could not repair its "
                        f"reactions: `{exc}`"
                    )

                if added:
                    await ctx.send(
                        f"Suggestion #{number} already exists. "
                        f"Restored reactions: {' '.join(added)}\n"
                        f"{existing_message.jump_url}"
                    )
                else:
                    await ctx.send(
                        f"Suggestion #{number} already exists and both "
                        f"voting reactions are present.\n"
                        f"{existing_message.jump_url}"
                    )

                return

            pending_channel = self._text_channel(
                ctx.guild,
                conf.get("pending_channel"),
            )

            if pending_channel is None:
                return await ctx.send(
                    "A current pending suggestions channel must be "
                    "configured before recreating the message."
                )

            missing = self._missing_permissions(
                pending_channel,
                [
                    "view_channel",
                    "send_messages",
                    "embed_links",
                    "add_reactions",
                    "read_message_history",
                ],
            )

            if missing:
                return await ctx.send(
                    f"I cannot recreate the suggestion in "
                    f"{pending_channel.mention}. Missing permissions: "
                    f"{', '.join(missing)}."
                )

            new_message: Optional[discord.Message] = None

            try:
                embed = self._build_pending_embed(number, record)
                new_message = await pending_channel.send(embed=embed)
                await self._add_seed_reactions(new_message)

                record["channel_id"] = pending_channel.id
                record["message_id"] = new_message.id
                record["status"] = "pending"

                state["suggestions"][str(number)] = record
                await scope.state.set(state)

            except discord.HTTPException as exc:
                if new_message is not None:
                    with suppress(discord.HTTPException):
                        await new_message.delete()

                return await ctx.send(
                    f"I could not recreate the suggestion: `{exc}`"
                )

            except Exception:
                if new_message is not None:
                    with suppress(discord.HTTPException):
                        await new_message.delete()

                log.exception(
                    "Unable to save resent suggestion %s in guild %s.",
                    number,
                    ctx.guild.id,
                )

                return await ctx.send(
                    "The suggestion message was created, but its state could "
                    "not be saved. The new message was removed."
                )

        await ctx.send(
            f"Suggestion #{number} has been recreated.\n"
            f"{new_message.jump_url}"
        )

    @suggestions.command(name="stats")
    @commands.mod_or_permissions(manage_messages=True)
    async def suggestions_stats(
        self,
        ctx: commands.Context,
    ) -> None:
        """Display aggregate suggestion statistics."""

        state = self._normalise_state(
            await self.config.guild(ctx.guild).state()
        )

        pending = len(state["suggestions"])
        approved = state["approved"]
        rejected = state["rejected"]
        decided = approved + rejected

        if decided:
            approval_rate = f"{(approved / decided) * 100:.1f}%"
        else:
            approval_rate = "No decisions yet"

        embed = discord.Embed(
            title="Flux Suggestions Statistics",
            colour=discord.Colour(PENDING_COLOUR),
        )

        embed.add_field(
            name="Submitted",
            value=str(state["submitted"]),
            inline=True,
        )
        embed.add_field(
            name="Pending",
            value=str(pending),
            inline=True,
        )
        embed.add_field(
            name="Approved",
            value=str(approved),
            inline=True,
        )
        embed.add_field(
            name="Rejected",
            value=str(rejected),
            inline=True,
        )
        embed.add_field(
            name="Approval rate",
            value=approval_rate,
            inline=True,
        )
        embed.add_field(
            name="Last number",
            value=str(state["counter"]),
            inline=True,
        )

        try:
            await ctx.send(embed=embed)
        except discord.HTTPException:
            await ctx.send(
                "Flux Suggestions Statistics\n"
                f"Submitted: {state['submitted']}\n"
                f"Pending: {pending}\n"
                f"Approved: {approved}\n"
                f"Rejected: {rejected}\n"
                f"Approval rate: {approval_rate}\n"
                f"Last number: {state['counter']}"
            )

    # -------------------------------------------------------------------------
    # Red data deletion
    # -------------------------------------------------------------------------

    async def red_delete_data_for_user(
        self,
        *,
        requester,
        user_id: int,
    ) -> None:
        """
        Remove pending suggestions associated with a user.

        Aggregate statistics and the suggestion counter are not personal data
        and are therefore retained.
        """

        all_guilds = await self.config.all_guilds()

        for guild_id_value in all_guilds:
            guild_id = self._safe_int(guild_id_value, 0)

            if not guild_id:
                continue

            lock = self._lock_for(guild_id)

            async with lock:
                scope = self.config.guild_from_id(guild_id)
                conf = await scope.all()
                state = self._normalise_state(conf.get("state"))

                to_remove: List[str] = []

                for number_text, raw_record in state["suggestions"].items():
                    if not isinstance(raw_record, dict):
                        continue

                    author_id = self._safe_int(
                        raw_record.get("author_id"),
                        0,
                    )

                    if author_id == user_id:
                        to_remove.append(number_text)

                if not to_remove:
                    continue

                guild = self.bot.get_guild(guild_id)

                for number_text in to_remove:
                    raw_record = state["suggestions"].get(number_text, {})

                    if guild is not None and isinstance(raw_record, dict):
                        channel_id = (
                            raw_record.get("channel_id")
                            or conf.get("pending_channel")
                        )
                        message_id = self._safe_int(
                            raw_record.get("message_id"),
                            0,
                        )
                        channel = self._text_channel(
                            guild,
                            channel_id,
                        )

                        if channel is not None and message_id:
                            try:
                                message = await channel.fetch_message(
                                    message_id
                                )
                                await message.delete()
                            except (
                                discord.NotFound,
                                discord.Forbidden,
                                discord.HTTPException,
                            ):
                                pass

                    state["suggestions"].pop(number_text, None)

                try:
                    await scope.state.set(state)
                except Exception:
                    log.exception(
                        "Unable to delete pending suggestion data for user "
                        "%s in guild %s.",
                        user_id,
                        guild_id,
                    )
