"""Reaction-driven persistent Q&A system for Fluxer communities.

FluxQuestions turns ordinary community messages into numbered questions using a
guild-configured reaction emoji. Pending questions receive native 👍 / 👎
reactions, authors receive a timed DM editing window, privileged roles can
continue editing pending questions after that soft-lock, and operators publish
permanent answers with a final vote snapshot.

The storage layer is canonical. Fluxer messages are projections of that stored
state and are repaired after restarts/crashes whenever practical.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timezone
from math import ceil
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

from .converters import GuildRole, QuestionEmoji, QuestionID
from .embeds import (
    ANSWER_COLOUR,
    EDIT_COLOUR,
    ERROR_COLOUR,
    LOG_COLOUR,
    QUESTION_COLOUR,
    build_answer_edit_log_embed,
    build_answer_embeds,
    build_answer_log_embed,
    build_audit_embed,
    build_author_edit_dm_embed,
    build_author_edit_success_embed,
    build_config_change_log_embed,
    build_error_log_embed,
    build_pending_question_embed,
    build_question_edit_log_embed,
    build_question_info_embed,
    build_recovery_log_embed,
    build_removal_log_embed,
    build_revision_history_embed,
    build_settings_embed,
    build_submission_log_embed,
)
from .storage import (
    QUESTION_GROUP,
    SOURCE_GROUP,
    QuestionNotFound,
    QuestionStorage,
    SourceAlreadySubmitted,
    StorageConflict,
)
from .utils import (
    DEFAULT_QUESTION_EMOJI,
    DOWNVOTE_EMOJI,
    MAX_ANSWER_LENGTH,
    MAX_QUESTION_LENGTH,
    MAX_REMOVAL_REASON_LENGTH,
    UPVOTE_EMOJI,
    author_edit_open,
    edit_deadline,
    emoji_display,
    emoji_matches_config,
    emoji_to_config,
    fluxer_jump_url,
    format_timestamp,
    normalise_emoji_config,
    positive_int,
    raw_markdown_codeblock,
    shorten,
    unix_now,
    unique_positive_ids,
    validate_length,
)

# ``member_has_any_role`` existed in an early utils draft. Keep this module
# independent from that draft by using the local helper below.
# The odd import expression above is replaced immediately after source
# generation; it is present only to make accidental stale imports obvious.

log = logging.getLogger("red.five.fluxquestions")

PERMISSION_NAMES = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "add_reactions": "Add Reactions",
    "read_message_history": "Read Message History",
    "manage_messages": "Manage Messages",
}

LIST_PAGE_SIZE = 10
EDIT_SESSION_GRACE_SECONDS = 5 * 60
RECOVERY_SEARCH_SLOP_SECONDS = 5 * 60

PENDING_REQUIRED = [
    "view_channel",
    "send_messages",
    "embed_links",
    "add_reactions",
    "read_message_history",
]

ANSWER_REQUIRED = [
    "view_channel",
    "send_messages",
    "embed_links",
    "read_message_history",
]

LOG_REQUIRED = [
    "view_channel",
    "send_messages",
    "embed_links",
]


class FluxQuestions(commands.Cog):
    """Persistent reaction-driven Q&A for Fluxer."""

    __author__ = "Five"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot

        # Do not change this identifier after publishing the cog.
        self.config = Config.get_conf(
            self,
            identifier=2707202601,
            force_registration=True,
        )
        self.storage = QuestionStorage(self.config)

        # DM edit sessions are intentionally ephemeral. The *right* to edit is
        # durable (created_at + edit window); after a restart the author simply
        # invokes qedit again.
        self._edit_sessions: Dict[int, Dict[str, int]] = {}
        self._startup_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Red lifecycle
    # ------------------------------------------------------------------

    async def cog_load(self) -> None:
        self._startup_task = asyncio.create_task(
            self._startup_reconcile(),
            name="fluxquestions-startup-reconcile",
        )

    def cog_unload(self) -> None:
        if self._startup_task is not None:
            self._startup_task.cancel()

        self._edit_sessions.clear()

    def format_help_for_context(self, ctx: commands.Context) -> str:
        help_text = super().format_help_for_context(ctx)
        return (
            f"{help_text}\n\n"
            f"Version: {self.__version__}\n"
            f"Author: {self.__author__}\n"
            "Platform: Fluxer"
        )

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _member_has_any_role(
        member: discord.Member,
        role_ids: Sequence[Any],
    ) -> bool:
        wanted = set(unique_positive_ids(role_ids))

        if not wanted:
            return False

        return any(
            positive_int(getattr(role, "id", None)) in wanted
            for role in getattr(member, "roles", [])
        )

    @staticmethod
    def _text_channel(
        guild: discord.Guild,
        channel_id: Any,
    ) -> Optional[discord.TextChannel]:
        resolved = positive_int(channel_id)

        if resolved is None:
            return None

        channel = guild.get_channel(resolved)

        if isinstance(channel, discord.TextChannel):
            return channel

        return None

    @staticmethod
    def _channel_label(
        guild: discord.Guild,
        channel_id: Any,
    ) -> str:
        resolved = positive_int(channel_id)

        if resolved is None:
            return "Not configured"

        channel = guild.get_channel(resolved)

        if isinstance(channel, discord.TextChannel):
            return channel.mention

        return f"Missing channel (`{resolved}`)"

    @staticmethod
    def _roles_label(
        guild: discord.Guild,
        role_ids: Iterable[Any],
    ) -> str:
        labels: List[str] = []

        for role_id in unique_positive_ids(role_ids):
            role = guild.get_role(role_id)

            if role is None:
                labels.append(f"Missing role (`{role_id}`)")
            else:
                labels.append(role.mention)

        return ", ".join(labels) if labels else "None"

    @staticmethod
    def _sources_label(
        guild: discord.Guild,
        channel_ids: Iterable[Any],
    ) -> str:
        ids = unique_positive_ids(channel_ids)

        if not ids:
            return "All eligible channels"

        labels: List[str] = []

        for channel_id in ids:
            channel = guild.get_channel(channel_id)
            labels.append(
                channel.mention
                if isinstance(channel, discord.TextChannel)
                else f"Missing channel (`{channel_id}`)"
            )

        return ", ".join(labels)

    @staticmethod
    def _missing_permissions(
        channel: discord.TextChannel,
        required: Sequence[str],
    ) -> List[str]:
        member = channel.guild.me

        if member is None:
            return ["Unable to resolve the bot's community member"]

        permissions = channel.permissions_for(member)
        missing: List[str] = []

        for name in required:
            if not getattr(permissions, name, False):
                missing.append(
                    PERMISSION_NAMES.get(
                        name,
                        name.replace("_", " ").title(),
                    )
                )

        return missing

    @staticmethod
    def _display_name(user: Any) -> str:
        if user is None:
            return "Unknown user"

        name = getattr(
            user,
            "display_name",
            getattr(user, "name", None),
        )
        user_id = positive_int(getattr(user, "id", None))

        if name and user_id:
            return f"{name} (`{user_id}`)"
        if name:
            return str(name)
        if user_id:
            return f"User `{user_id}`"

        return str(user)

    async def _resolve_user(
        self,
        guild: discord.Guild,
        user_id: Any,
    ) -> Optional[Any]:
        resolved = positive_int(user_id)

        if resolved is None:
            return None

        member = guild.get_member(resolved)
        if member is not None:
            return member

        cached = self.bot.get_user(resolved)
        if cached is not None:
            return cached

        fetch_user = getattr(self.bot, "fetch_user", None)
        if fetch_user is None:
            return None

        try:
            return await fetch_user(resolved)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return None

    async def _is_bot_owner(self, member: discord.Member) -> bool:
        try:
            return bool(await self.bot.is_owner(member))
        except Exception:
            return False

    async def _is_red_mod(self, member: discord.Member) -> bool:
        # Red forks have historically exposed slightly different helper
        # signatures, so keep permission fallback checks as well.
        is_mod = getattr(self.bot, "is_mod", None)

        if callable(is_mod):
            try:
                result = is_mod(member)
                if asyncio.iscoroutine(result):
                    result = await result
                if result:
                    return True
            except Exception:
                pass

        permissions = getattr(member, "guild_permissions", None)

        return bool(
            permissions
            and (
                getattr(permissions, "administrator", False)
                or getattr(permissions, "manage_guild", False)
                or getattr(permissions, "manage_messages", False)
            )
        )

    async def _can_submit_other(
        self,
        member: discord.Member,
        conf: Mapping[str, Any],
    ) -> bool:
        if await self._is_bot_owner(member) or await self._is_red_mod(member):
            return True

        return self._member_has_any_role(
            member,
            conf.get("submitter_role_ids", []),
        )

    async def _can_edit_question(
        self,
        member: discord.Member,
        conf: Mapping[str, Any],
    ) -> bool:
        if await self._is_bot_owner(member) or await self._is_red_mod(member):
            return True

        return (
            self._member_has_any_role(
                member,
                conf.get("editor_role_ids", []),
            )
            or self._member_has_any_role(
                member,
                conf.get("operator_role_ids", []),
            )
        )

    async def _can_operate(
        self,
        member: discord.Member,
        conf: Mapping[str, Any],
    ) -> bool:
        if await self._is_bot_owner(member) or await self._is_red_mod(member):
            return True

        return self._member_has_any_role(
            member,
            conf.get("operator_role_ids", []),
        )

    async def _require_editor(self, ctx: commands.Context) -> bool:
        conf = await self.config.guild(ctx.guild).all()

        if await self._can_edit_question(ctx.author, conf):
            return True

        await ctx.send(
            "You do not have permission to edit community questions."
        )
        return False

    async def _require_operator(self, ctx: commands.Context) -> bool:
        conf = await self.config.guild(ctx.guild).all()

        if await self._can_operate(ctx.author, conf):
            return True

        await ctx.send(
            "You do not have permission to operate Flux Questions."
        )
        return False

    # ------------------------------------------------------------------
    # Native reaction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_reaction(
        message: discord.Message,
        emoji: str,
    ) -> Optional[discord.Reaction]:
        for reaction in getattr(message, "reactions", []):
            if str(reaction.emoji) == emoji:
                return reaction

        return None

    async def _add_vote_reactions(
        self,
        message: discord.Message,
    ) -> None:
        await message.add_reaction(UPVOTE_EMOJI)
        await message.add_reaction(DOWNVOTE_EMOJI)

    async def _repair_vote_reactions(
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

        ids: Set[int] = set()
        own_id = positive_int(getattr(self.bot.user, "id", None))

        async for user in reaction.users():
            if own_id is not None and user.id == own_id:
                continue
            if getattr(user, "bot", False):
                continue

            ids.add(user.id)

        return ids

    def _fallback_reaction_count(
        self,
        message: discord.Message,
        emoji: str,
    ) -> int:
        reaction = self._find_reaction(message, emoji)

        if reaction is None:
            return 0

        try:
            count = max(0, int(getattr(reaction, "count", 0)))
        except (TypeError, ValueError):
            count = 0

        if getattr(reaction, "me", False):
            count = max(0, count - 1)

        return count

    async def _read_votes(
        self,
        message: discord.Message,
    ) -> Dict[str, Any]:
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
                "Unable to enumerate reaction users for message %s; "
                "falling back to displayed counts: %s",
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

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def _send_audit(
        self,
        guild: discord.Guild,
        embed: discord.Embed,
    ) -> bool:
        conf = await self.config.guild(guild).all()
        channel = self._text_channel(guild, conf.get("log_channel"))

        if channel is None:
            return False

        missing = self._missing_permissions(channel, LOG_REQUIRED)

        if missing:
            log.warning(
                "FluxQuestions log channel %s in guild %s is missing: %s",
                channel.id,
                guild.id,
                ", ".join(missing),
            )
            return False

        try:
            await channel.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except discord.HTTPException:
            log.exception(
                "Unable to send FluxQuestions audit embed in guild %s.",
                guild.id,
            )
            return False

    async def _audit_error(
        self,
        guild: discord.Guild,
        *,
        title: str,
        details: str,
        question_number: Optional[int] = None,
        actor_label: Optional[str] = None,
    ) -> None:
        embed = build_error_log_embed(
            title=title,
            occurred_at=unix_now(),
            details=details,
            question_number=question_number,
            actor_label=actor_label,
        )
        await self._send_audit(guild, embed)

    # ------------------------------------------------------------------
    # Embed context helpers
    # ------------------------------------------------------------------

    async def _pending_embed(
        self,
        guild: discord.Guild,
        record: Mapping[str, Any],
        conf: Mapping[str, Any],
    ) -> discord.Embed:
        author = await self._resolve_user(
            guild,
            record.get("author_id"),
        )
        source_channel = self._text_channel(
            guild,
            record.get("source_channel_id"),
        )

        source_url = fluxer_jump_url(
            guild.id,
            record.get("source_channel_id"),
            record.get("source_message_id"),
        )

        deadline = edit_deadline(
            record.get("created_at"),
            conf.get("author_edit_window_seconds", 1800),
        )

        return build_pending_question_embed(
            record,
            author_label=self._display_name(author),
            source_label=(
                source_channel.mention
                if source_channel is not None
                else None
            ),
            source_jump_url=source_url,
            author_edit_deadline=deadline,
            show_author=True,
        )

    async def _answer_embeds(
        self,
        guild: discord.Guild,
        record: Mapping[str, Any],
    ) -> List[discord.Embed]:
        author = await self._resolve_user(
            guild,
            record.get("author_id"),
        )

        answer = record.get("answer")
        answer = dict(answer) if isinstance(answer, Mapping) else {}

        operator = await self._resolve_user(
            guild,
            answer.get("author_id"),
        )

        source_url = fluxer_jump_url(
            guild.id,
            record.get("source_channel_id"),
            record.get("source_message_id"),
        )

        return build_answer_embeds(
            record,
            author_label=self._display_name(author),
            answered_by_label=self._display_name(operator),
            source_jump_url=source_url,
        )

    # ------------------------------------------------------------------
    # Message lookup / recovery
    # ------------------------------------------------------------------

    async def _fetch_message(
        self,
        guild: discord.Guild,
        channel_id: Any,
        message_id: Any,
    ) -> Optional[discord.Message]:
        channel = self._text_channel(guild, channel_id)
        resolved_message = positive_int(message_id)

        if channel is None or resolved_message is None:
            return None

        try:
            return await channel.fetch_message(resolved_message)
        except discord.NotFound:
            return None
        except (discord.Forbidden, discord.HTTPException):
            raise

    @staticmethod
    def _message_has_question_title(
        message: discord.Message,
        number: int,
    ) -> bool:
        wanted = {
            f"❓ Question #{number}",
            f"✅ Question #{number}",
        }

        return any(
            str(getattr(embed, "title", "") or "") in wanted
            for embed in getattr(message, "embeds", [])
        )

    async def _search_bot_question_message(
        self,
        channel: discord.TextChannel,
        number: int,
        *,
        after_timestamp: Any,
    ) -> Optional[discord.Message]:
        own_id = positive_int(getattr(self.bot.user, "id", None))
        if own_id is None:
            return None

        try:
            timestamp = int(after_timestamp or 0)
        except (TypeError, ValueError):
            timestamp = 0

        after = None
        if timestamp > 0:
            after = datetime.fromtimestamp(
                max(1, timestamp - RECOVERY_SEARCH_SLOP_SECONDS),
                tz=timezone.utc,
            )

        try:
            history = channel.history(
                limit=None,
                after=after,
                oldest_first=True,
            )

            async for message in history:
                if positive_int(getattr(message.author, "id", None)) != own_id:
                    continue

                if self._message_has_question_title(message, number):
                    return message

        except (discord.Forbidden, discord.HTTPException):
            return None

        return None

    async def _clear_pending_reference(
        self,
        guild_id: int,
        number: int,
    ) -> None:
        """Clear a pending-message pointer after successful cleanup.

        This uses the storage scope directly because the storage file's initial
        API intentionally kept message cleanup separate from durable state
        transitions. A later storage schema can expose this as a public method
        without changing the on-disk record format.
        """

        scope = self.storage._question_scope(guild_id, number)
        await scope.pending_message_id.set(None)

    async def _ensure_pending_message(
        self,
        guild: discord.Guild,
        record: Mapping[str, Any],
        conf: Optional[Mapping[str, Any]] = None,
    ) -> discord.Message:
        """Return a live pending message matching canonical storage.

        The stored/original channel is checked first so existing votes remain
        attached to their message. If that message is genuinely gone, the
        question is recreated in the community's *current* questions channel.
        """

        if str(record.get("status")) != "pending":
            raise StorageConflict("Only pending questions have pending messages.")

        conf = conf or await self.config.guild(guild).all()
        number = int(record["number"])

        stored_channel = self._text_channel(
            guild,
            record.get("pending_channel_id"),
        )
        current_channel = self._text_channel(
            guild,
            conf.get("questions_channel"),
        )

        if stored_channel is None and current_channel is None:
            raise StorageConflict(
                "The questions channel is not configured or no longer exists."
            )

        message: Optional[discord.Message] = None
        message_id = positive_int(record.get("pending_message_id"))

        # First preserve an existing message in its historical channel.
        if stored_channel is not None and message_id is not None:
            stored_missing = self._missing_permissions(
                stored_channel,
                ["view_channel", "read_message_history"],
            )
            if stored_missing:
                raise StorageConflict(
                    "I cannot verify the stored pending message because its "
                    "channel is missing permissions: "
                    + ", ".join(stored_missing)
                )

            try:
                message = await stored_channel.fetch_message(message_id)
            except discord.NotFound:
                message = None
            except discord.Forbidden as exc:
                raise StorageConflict(
                    "Fluxer refused access to the stored pending message."
                ) from exc
            except discord.HTTPException as exc:
                raise StorageConflict(
                    f"I could not retrieve the stored pending message: {exc}"
                ) from exc

        # Crash case: the message may have been sent immediately before the
        # process stopped, leaving no attached message ID.
        if message is None and stored_channel is not None:
            message = await self._search_bot_question_message(
                stored_channel,
                number,
                after_timestamp=record.get("created_at"),
            )

            if message is not None:
                record = await self.storage.attach_pending_message(
                    guild.id,
                    number,
                    channel_id=stored_channel.id,
                    message_id=message.id,
                )

        if message is not None:
            embed = await self._pending_embed(guild, record, conf)

            try:
                await message.edit(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except TypeError:
                await message.edit(embed=embed)

            await self._repair_vote_reactions(message)
            return message

        # The historical message is truly gone. Recreate it in the current
        # configured questions channel, matching the documented channel-change
        # behaviour and avoiding resurrection into an obsolete channel.
        target = current_channel or stored_channel
        if target is None:
            raise StorageConflict(
                "No usable questions channel is available for recreation."
            )

        missing = self._missing_permissions(target, PENDING_REQUIRED)
        if missing:
            raise StorageConflict(
                "The questions channel is missing permissions: "
                + ", ".join(missing)
            )

        # Search the current target once as well: if a prior crash happened
        # during migration/recreation, this can recover that orphan instead of
        # producing a duplicate.
        if target is not stored_channel:
            message = await self._search_bot_question_message(
                target,
                number,
                after_timestamp=record.get("created_at"),
            )

        if message is None:
            embed = await self._pending_embed(guild, record, conf)
            message = await target.send(
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self._add_vote_reactions(message)
        else:
            embed = await self._pending_embed(guild, record, conf)
            try:
                await message.edit(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except TypeError:
                await message.edit(embed=embed)
            await self._repair_vote_reactions(message)

        await self.storage.attach_pending_message(
            guild.id,
            number,
            channel_id=target.id,
            message_id=message.id,
        )
        return message

    async def _ensure_answer_message(
        self,
        guild: discord.Guild,
        record: Mapping[str, Any],
        *,
        search_if_missing: bool = True,
    ) -> discord.Message:
        """Return a permanent answer message matching canonical storage.

        Existing answers remain where they were originally posted. If an answer
        was deleted, recovery recreates it in the guild's current answers
        channel and updates the stored channel/message pointers.
        """

        if str(record.get("status")) != "answered":
            raise StorageConflict("That question is not answered.")

        conf = await self.config.guild(guild).all()
        answer = record.get("answer")
        answer = dict(answer) if isinstance(answer, Mapping) else {}

        stored_channel = self._text_channel(
            guild,
            answer.get("channel_id"),
        )
        current_channel = self._text_channel(
            guild,
            conf.get("answers_channel"),
        )

        if stored_channel is None and current_channel is None:
            raise StorageConflict(
                "The answers channel is not configured or no longer exists."
            )

        message: Optional[discord.Message] = None
        message_id = positive_int(answer.get("message_id"))

        if stored_channel is not None and message_id is not None:
            stored_missing = self._missing_permissions(
                stored_channel,
                ["view_channel", "read_message_history", "send_messages", "embed_links"],
            )
            if stored_missing:
                raise StorageConflict(
                    "I cannot verify/edit the stored answer because its "
                    "channel is missing permissions: "
                    + ", ".join(stored_missing)
                )

            try:
                message = await stored_channel.fetch_message(message_id)
            except discord.NotFound:
                message = None
            except discord.Forbidden as exc:
                raise StorageConflict(
                    "Fluxer refused access to the stored answer message."
                ) from exc
            except discord.HTTPException as exc:
                raise StorageConflict(
                    f"I could not retrieve the stored answer message: {exc}"
                ) from exc

        if message is None and search_if_missing and stored_channel is not None:
            message = await self._search_bot_question_message(
                stored_channel,
                int(record["number"]),
                after_timestamp=answer.get("created_at"),
            )

            if message is not None:
                record = await self.storage.attach_answer_message(
                    guild.id,
                    int(record["number"]),
                    channel_id=stored_channel.id,
                    message_id=message.id,
                )

        embeds = await self._answer_embeds(guild, record)

        if message is not None:
            try:
                await message.edit(
                    embeds=embeds,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except TypeError:
                await message.edit(embeds=embeds)

            return message

        target = current_channel or stored_channel
        if target is None:
            raise StorageConflict(
                "No usable answers channel is available for recreation."
            )

        missing = self._missing_permissions(target, ANSWER_REQUIRED)
        if missing:
            raise StorageConflict(
                "The answers channel is missing permissions: "
                + ", ".join(missing)
            )

        if search_if_missing and target is not stored_channel:
            message = await self._search_bot_question_message(
                target,
                int(record["number"]),
                after_timestamp=answer.get("created_at"),
            )

        if message is None:
            message = await target.send(
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            try:
                await message.edit(
                    embeds=embeds,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except TypeError:
                await message.edit(embeds=embeds)

        await self.storage.attach_answer_message(
            guild.id,
            int(record["number"]),
            channel_id=target.id,
            message_id=message.id,
        )
        return message

    async def _cleanup_pending_after_resolution(
        self,
        guild: discord.Guild,
        record: Mapping[str, Any],
    ) -> bool:
        number = int(record["number"])
        message = None

        try:
            message = await self._fetch_message(
                guild,
                record.get("pending_channel_id"),
                record.get("pending_message_id"),
            )
        except (discord.Forbidden, discord.HTTPException):
            # Keep the pointer so startup/manual reconciliation can retry.
            return False

        if message is None:
            await self._clear_pending_reference(guild.id, number)
            return True

        try:
            await message.delete()
            await self._clear_pending_reference(guild.id, number)
            return True
        except discord.NotFound:
            await self._clear_pending_reference(guild.id, number)
            return True
        except discord.HTTPException:
            return False

    # ------------------------------------------------------------------
    # Startup reconciliation
    # ------------------------------------------------------------------

    async def _startup_reconcile(self) -> None:
        try:
            waiter = getattr(self.bot, "wait_until_red_ready", None)
            if not callable(waiter):
                waiter = getattr(self.bot, "wait_until_ready", None)

            if callable(waiter):
                await waiter()

            for guild in list(getattr(self.bot, "guilds", [])):
                try:
                    await self._reconcile_guild_messages(guild)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception(
                        "FluxQuestions startup reconciliation failed "
                        "for guild %s.",
                        guild.id,
                    )
        except asyncio.CancelledError:
            return

    async def _reconcile_guild_messages(
        self,
        guild: discord.Guild,
    ) -> None:
        report = await self.storage.reconcile_guild(guild.id)
        conf = await self.config.guild(guild).all()

        for conflict in report.get("source_conflicts", []):
            await self._audit_error(
                guild,
                title="Duplicate source index detected",
                details=(
                    f"Source message `{conflict['source_message_id']}` "
                    f"is referenced by Question #{conflict['first_question']} "
                    f"and Question #{conflict['duplicate_question']}. "
                    "The first record remains canonical."
                ),
            )

        # First resolve staged answers. A crash can occur after the answer is
        # sent but before complete_answer persists its message ID.
        incomplete_numbers: Set[int] = set()

        for staged in report.get("incomplete_operations", []):
            number = int(staged["number"])
            incomplete_numbers.add(number)

            operation = staged.get("operation") or {}

            if operation.get("type") != "answer":
                await self._audit_error(
                    guild,
                    title="Unknown incomplete operation",
                    details=f"Stored operation: `{operation}`",
                    question_number=number,
                )
                continue

            answers_channel = self._text_channel(
                guild,
                conf.get("answers_channel"),
            )

            found = None
            if answers_channel is not None:
                found = await self._search_bot_question_message(
                    answers_channel,
                    number,
                    after_timestamp=operation.get("started_at"),
                )

            if found is not None:
                completed = await self.storage.complete_answer(
                    guild.id,
                    number,
                    channel_id=found.channel.id,
                    message_id=found.id,
                )

                cleaned = await self._cleanup_pending_after_resolution(
                    guild,
                    completed,
                )

                await self._send_audit(
                    guild,
                    build_recovery_log_embed(
                        title="♻️ Recovered Interrupted Answer",
                        question_number=number,
                        actor_label="Automatic startup recovery",
                        occurred_at=unix_now(),
                        details=(
                            "Found the answer message posted before the previous "
                            "process stopped and attached it to permanent storage."
                            + (
                                ""
                                if cleaned
                                else " The old pending message still needs cleanup."
                            )
                        ),
                        jump_url=getattr(found, "jump_url", None),
                    ),
                )
                continue

            # No answer message was found, so the safest state is to make the
            # question pending again. The staged answer was never confirmed.
            restored = await self.storage.abort_answer(
                guild.id,
                number,
            )

            try:
                pending = await self._ensure_pending_message(
                    guild,
                    restored,
                    conf,
                )
                jump = getattr(pending, "jump_url", None)
            except Exception as exc:
                jump = None
                await self._audit_error(
                    guild,
                    title="Pending recovery failed",
                    details=str(exc),
                    question_number=number,
                )

            await self._send_audit(
                guild,
                build_recovery_log_embed(
                    title="♻️ Restored Interrupted Question",
                    question_number=number,
                    actor_label="Automatic startup recovery",
                    occurred_at=unix_now(),
                    details=(
                        "No completed answer message was found. The staged "
                        "answer was cleared and the question was restored to "
                        "the pending queue."
                    ),
                    jump_url=jump,
                ),
            )

        # Make every pending question reflect canonical storage. Pending queues
        # are normally modest, and validating all of them gives robust restart
        # behaviour after edits, partial submissions and manual deletions.
        for record in report.get("pending", []):
            number = int(record["number"])

            if number in incomplete_numbers:
                continue

            try:
                await self._ensure_pending_message(
                    guild,
                    record,
                    conf,
                )
            except Exception as exc:
                log.exception(
                    "Unable to reconcile pending Question #%s in guild %s.",
                    number,
                    guild.id,
                )
                await self._audit_error(
                    guild,
                    title="Pending question reconciliation failed",
                    details=str(exc),
                    question_number=number,
                )

        # Normally successful resolution clears the pending message pointer.
        # A non-null pointer on an answered record therefore means the previous
        # process stopped before cleanup, or deletion failed.
        answered = await self.storage.list_questions(
            guild.id,
            status="answered",
        )

        for record in answered:
            answer = record.get("answer")
            answer = dict(answer) if isinstance(answer, Mapping) else {}

            if record.get("pending_message_id"):
                cleaned = await self._cleanup_pending_after_resolution(
                    guild,
                    record,
                )

                if not cleaned:
                    await self._audit_error(
                        guild,
                        title="Answered question still has pending message",
                        details=(
                            "Automatic startup cleanup could not delete the "
                            "old pending message."
                        ),
                        question_number=int(record["number"]),
                    )

            # Only edited answers need routine content resynchronisation.
            # This catches a crash between the Config write and Message.edit
            # without fetching every historical answer on every restart.
            if answer.get("edited_at"):
                try:
                    await self._ensure_answer_message(
                        guild,
                        record,
                        search_if_missing=True,
                    )
                except Exception as exc:
                    await self._audit_error(
                        guild,
                        title="Edited answer reconciliation failed",
                        details=str(exc),
                        question_number=int(record["number"]),
                    )

    # ------------------------------------------------------------------
    # Reaction submission listener
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        guild_id = positive_int(getattr(payload, "guild_id", None))
        channel_id = positive_int(getattr(payload, "channel_id", None))
        message_id = positive_int(getattr(payload, "message_id", None))
        user_id = positive_int(getattr(payload, "user_id", None))

        if not all((guild_id, channel_id, message_id, user_id)):
            return

        own_id = positive_int(getattr(self.bot.user, "id", None))
        if own_id is not None and user_id == own_id:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        conf = await self.config.guild(guild).all()

        if not emoji_matches_config(
            getattr(payload, "emoji", None),
            conf.get("question_emoji"),
        ):
            return

        # Never allow Q&A infrastructure messages to recursively submit
        # themselves as new questions.
        protected_channels = {
            positive_int(conf.get("questions_channel")),
            positive_int(conf.get("answers_channel")),
            positive_int(conf.get("log_channel")),
        }
        protected_channels.discard(None)

        if channel_id in protected_channels:
            return

        allowed_sources = set(
            unique_positive_ids(conf.get("source_channel_ids", []))
        )
        if allowed_sources and channel_id not in allowed_sources:
            return

        channel = self._text_channel(guild, channel_id)
        if channel is None:
            return

        try:
            message = await channel.fetch_message(message_id)
        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException,
        ):
            return

        if getattr(message.author, "bot", False):
            return

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
                AttributeError,
            ):
                return

        author_id = positive_int(getattr(message.author, "id", None))
        if author_id is None:
            return

        own_submission = author_id == user_id

        if not own_submission and not await self._can_submit_other(member, conf):
            with suppress(
                discord.Forbidden,
                discord.NotFound,
                discord.HTTPException,
            ):
                await message.remove_reaction(payload.emoji, member)

            with suppress(discord.HTTPException):
                await member.send(
                    "Only the author, community moderators, or configured "
                    "question submitters can turn that message into a question."
                )
            return

        questions_channel = self._text_channel(
            guild,
            conf.get("questions_channel"),
        )

        if questions_channel is None:
            with suppress(discord.HTTPException):
                await member.send(
                    "I could not submit that question because this community "
                    "has not configured its Flux Questions channel."
                )
            return

        missing = self._missing_permissions(
            questions_channel,
            PENDING_REQUIRED,
        )
        if missing:
            await self._audit_error(
                guild,
                title="Question submission failed",
                details=(
                    f"The questions channel is missing permissions: "
                    f"{', '.join(missing)}."
                ),
                actor_label=self._display_name(member),
            )
            return

        content = str(getattr(message, "content", "") or "").strip()

        try:
            content = validate_length(
                content,
                maximum=MAX_QUESTION_LENGTH,
                label="Questions",
            )
        except ValueError as exc:
            with suppress(discord.HTTPException):
                await member.send(str(exc))
            return

        try:
            record = await self.storage.create_question(
                guild.id,
                author_id=author_id,
                submitted_by_id=user_id,
                source_channel_id=channel.id,
                source_message_id=message.id,
                pending_channel_id=questions_channel.id,
                content=content,
                created_at=unix_now(),
            )
        except SourceAlreadySubmitted as exc:
            with suppress(discord.HTTPException):
                await member.send(str(exc))
            return
        except Exception:
            log.exception(
                "Unable to create a question record from source message %s "
                "in guild %s.",
                message.id,
                guild.id,
            )
            await self._audit_error(
                guild,
                title="Question storage failed",
                details=(
                    "The source reaction was received, but the permanent "
                    "question record could not be created."
                ),
                actor_label=self._display_name(member),
            )
            return

        try:
            pending_message = await self._ensure_pending_message(
                guild,
                record,
                conf,
            )
        except Exception as exc:
            # The record deliberately remains pending. Startup reconciliation
            # or a staff resend can finish the projection later.
            log.exception(
                "Question #%s was stored but its pending message could not "
                "be projected in guild %s.",
                record["number"],
                guild.id,
            )
            await self._audit_error(
                guild,
                title="Question stored but not posted",
                details=str(exc),
                question_number=int(record["number"]),
                actor_label=self._display_name(member),
            )
            with suppress(discord.HTTPException):
                await member.send(
                    f"Question #{record['number']} was saved, but I could not "
                    "post its pending card. Staff have been notified."
                )
            return

        author = await self._resolve_user(
            guild,
            record.get("author_id"),
        )
        submitter = await self._resolve_user(
            guild,
            record.get("submitted_by_id"),
        )

        await self._send_audit(
            guild,
            build_submission_log_embed(
                record,
                author_label=self._display_name(author),
                submitter_label=self._display_name(submitter),
                source_label=channel.mention,
                source_jump_url=getattr(message, "jump_url", None),
            ),
        )

        with suppress(discord.HTTPException):
            await member.send(
                f"Question #{record['number']} has been submitted.\n"
                f"{getattr(pending_message, 'jump_url', '')}".rstrip()
            )

    # ------------------------------------------------------------------
    # Author DM editing
    # ------------------------------------------------------------------

    @commands.command(name="qedit")
    @commands.guild_only()
    async def qedit(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
    ) -> None:
        """Start the timed private editing flow for your own pending question."""

        try:
            record = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )
        except QuestionNotFound as exc:
            return await ctx.send(str(exc))

        if record.get("status") != "pending":
            return await ctx.send(
                f"Question #{question_id} is no longer pending."
            )

        if positive_int(record.get("author_id")) != ctx.author.id:
            return await ctx.send(
                "Only the original question author can use the private "
                "self-edit workflow."
            )

        conf = await self.config.guild(ctx.guild).all()
        window = int(conf.get("author_edit_window_seconds") or 0)

        if not author_edit_open(
            record.get("created_at"),
            window,
        ):
            deadline = edit_deadline(record.get("created_at"), window)
            return await ctx.send(
                "Your self-edit window has closed."
                + (
                    f" It ended {format_timestamp(deadline)}."
                    if deadline
                    else ""
                )
                + " Configured editors can still clarify or revert a pending "
                "question."
            )

        deadline = edit_deadline(
            record.get("created_at"),
            window,
        )
        if deadline is None:
            return await ctx.send(
                "This question has an invalid edit deadline. "
                "Please contact community staff."
            )

        now = unix_now()
        session_expiry = max(
            deadline,
            now + EDIT_SESSION_GRACE_SECONDS,
        )

        try:
            await ctx.author.send(
                embed=build_author_edit_dm_embed(
                    record,
                    edit_deadline=deadline,
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            return await ctx.send(
                "I couldn't DM you. Please allow direct messages from members "
                "of this community and run the edit command again."
            )

        self._edit_sessions[ctx.author.id] = {
            "guild_id": ctx.guild.id,
            "question_id": question_id,
            "expires_at": session_expiry,
        }

        await ctx.send(
            f"I've sent you a DM to edit Question #{question_id}.",
            delete_after=15,
        )

        await self._send_audit(
            ctx.guild,
            build_audit_embed(
                title=f"✏️ Edit Session Started — #{question_id}",
                actor_label=self._display_name(ctx.author),
                occurred_at=now,
                question_number=question_id,
                fields=[
                    {
                        "name": "Author edit deadline",
                        "value": format_timestamp(deadline),
                        "inline": False,
                    },
                    {
                        "name": "DM session expiry",
                        "value": format_timestamp(session_expiry),
                        "inline": False,
                    },
                ],
                colour=EDIT_COLOUR,
            ),
        )

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message,
    ) -> None:
        if message.guild is not None:
            return

        if getattr(message.author, "bot", False):
            return

        user_id = positive_int(getattr(message.author, "id", None))
        if user_id is None:
            return

        session = self._edit_sessions.get(user_id)
        if session is None:
            return

        now = unix_now()

        if now > int(session["expires_at"]):
            self._edit_sessions.pop(user_id, None)
            with suppress(discord.HTTPException):
                await message.channel.send(
                    "That question edit session has expired. "
                    "Run the edit command again from the community if your "
                    "author edit window is still open."
                )
            return

        raw = str(message.content or "")

        if raw.strip().casefold() in {"cancel", "cancel edit"}:
            self._edit_sessions.pop(user_id, None)
            with suppress(discord.HTTPException):
                await message.channel.send("Question edit cancelled.")
            return

        try:
            new_content = validate_length(
                raw,
                maximum=MAX_QUESTION_LENGTH,
                label="Questions",
            )
        except ValueError as exc:
            with suppress(discord.HTTPException):
                await message.channel.send(
                    f"{exc}\nYour edit session is still active."
                )
            return

        guild = self.bot.get_guild(int(session["guild_id"]))
        if guild is None:
            self._edit_sessions.pop(user_id, None)
            return

        number = int(session["question_id"])

        try:
            before = await self.storage.require_question(
                guild.id,
                number,
            )

            if before.get("status") != "pending":
                raise StorageConflict(
                    "The question was answered or removed while you were editing it."
                )

            if positive_int(before.get("author_id")) != user_id:
                raise StorageConflict(
                    "You are no longer recorded as this question's author."
                )

            updated = await self.storage.edit_question(
                guild.id,
                number,
                new_content=new_content,
                editor_id=user_id,
                kind="author_edit",
            )

            conf = await self.config.guild(guild).all()
            await self._ensure_pending_message(
                guild,
                updated,
                conf,
            )

        except (QuestionNotFound, StorageConflict, ValueError) as exc:
            self._edit_sessions.pop(user_id, None)
            with suppress(discord.HTTPException):
                await message.channel.send(str(exc))
            return

        except Exception as exc:
            # Canonical storage may already have accepted the edit. Preserve it
            # and let startup reconciliation repair the projection.
            log.exception(
                "Unable to finish DM edit for Question #%s in guild %s.",
                number,
                guild.id,
            )
            await self._audit_error(
                guild,
                title="Author edit projection failed",
                details=str(exc),
                question_number=number,
                actor_label=self._display_name(message.author),
            )
            with suppress(discord.HTTPException):
                await message.channel.send(
                    "Your edit was saved, but I could not fully refresh the "
                    "pending Fluxer message. Staff have been notified."
                )
            self._edit_sessions.pop(user_id, None)
            return

        self._edit_sessions.pop(user_id, None)

        await self._send_audit(
            guild,
            build_question_edit_log_embed(
                record=updated,
                actor_label=self._display_name(message.author),
                before=str(before.get("content") or ""),
                after=str(updated.get("content") or ""),
                edit_kind="Author self-edit",
            ),
        )

        with suppress(discord.HTTPException):
            await message.channel.send(
                embed=build_author_edit_success_embed(updated),
                allowed_mentions=discord.AllowedMentions.none(),
            )

    # ------------------------------------------------------------------
    # Staff question editing
    # ------------------------------------------------------------------

    @commands.command(name="questionedit")
    @commands.guild_only()
    async def questionedit(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
        *,
        content: str,
    ) -> None:
        """Edit a pending question as a configured editor/operator."""

        if not await self._require_editor(ctx):
            return

        try:
            content = validate_length(
                content,
                maximum=MAX_QUESTION_LENGTH,
                label="Questions",
            )
            before = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )
            updated = await self.storage.edit_question(
                ctx.guild.id,
                question_id,
                new_content=content,
                editor_id=ctx.author.id,
                kind="staff_edit",
            )
            conf = await self.config.guild(ctx.guild).all()
            pending = await self._ensure_pending_message(
                ctx.guild,
                updated,
                conf,
            )
        except (QuestionNotFound, StorageConflict, ValueError) as exc:
            return await ctx.send(str(exc))
        except Exception as exc:
            log.exception(
                "Staff edit failed for Question #%s in guild %s.",
                question_id,
                ctx.guild.id,
            )
            await self._audit_error(
                ctx.guild,
                title="Staff question edit failed",
                details=str(exc),
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
            )
            return await ctx.send(
                "The stored question may have changed, but I could not fully "
                "synchronise its pending message. Check the verbose log."
            )

        await self._send_audit(
            ctx.guild,
            build_question_edit_log_embed(
                record=updated,
                actor_label=self._display_name(ctx.author),
                before=str(before.get("content") or ""),
                after=str(updated.get("content") or ""),
                edit_kind="Staff edit",
            ),
        )

        await ctx.send(
            f"Question #{question_id} has been edited in place.\n"
            f"{pending.jump_url}"
        )

    @commands.command(name="questionrevert")
    @commands.guild_only()
    async def questionrevert(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
        revision: int,
    ) -> None:
        """Revert a pending question to a stored historical revision."""

        if not await self._require_editor(ctx):
            return

        try:
            before = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )
            updated = await self.storage.revert_question(
                ctx.guild.id,
                question_id,
                revision_number=revision,
                editor_id=ctx.author.id,
            )
            conf = await self.config.guild(ctx.guild).all()
            pending = await self._ensure_pending_message(
                ctx.guild,
                updated,
                conf,
            )
        except (QuestionNotFound, StorageConflict, ValueError) as exc:
            return await ctx.send(str(exc))

        await self._send_audit(
            ctx.guild,
            build_question_edit_log_embed(
                record=updated,
                actor_label=self._display_name(ctx.author),
                before=str(before.get("content") or ""),
                after=str(updated.get("content") or ""),
                edit_kind=f"Revert to revision {revision}",
            ),
        )

        await ctx.send(
            f"Question #{question_id} has been reverted to revision "
            f"{revision}.\n{pending.jump_url}"
        )

    # ------------------------------------------------------------------
    # Answering
    # ------------------------------------------------------------------

    @commands.command(name="answer")
    @commands.guild_only()
    async def answer(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
        *,
        content: str,
    ) -> None:
        """Answer a pending question while preserving multiline Markdown."""

        if not await self._require_operator(ctx):
            return

        try:
            content = validate_length(
                content,
                maximum=MAX_ANSWER_LENGTH,
                label="Answers",
            )
        except ValueError as exc:
            return await ctx.send(str(exc))

        conf = await self.config.guild(ctx.guild).all()
        answers_channel = self._text_channel(
            ctx.guild,
            conf.get("answers_channel"),
        )

        if answers_channel is None:
            return await ctx.send(
                "The answers channel has not been configured or no longer exists."
            )

        missing = self._missing_permissions(
            answers_channel,
            ANSWER_REQUIRED,
        )
        if missing:
            return await ctx.send(
                "I cannot use the configured answers channel. Missing "
                f"permissions: {', '.join(missing)}."
            )

        try:
            record = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )
            pending = await self._ensure_pending_message(
                ctx.guild,
                record,
                conf,
            )
            votes = await self._read_votes(pending)

            staged = await self.storage.begin_answer(
                ctx.guild.id,
                question_id,
                answer_content=content,
                operator_id=ctx.author.id,
                votes=votes,
            )
        except (QuestionNotFound, StorageConflict, ValueError) as exc:
            return await ctx.send(str(exc))
        except Exception as exc:
            log.exception(
                "Unable to stage answer for Question #%s in guild %s.",
                question_id,
                ctx.guild.id,
            )
            await self._audit_error(
                ctx.guild,
                title="Answer staging failed",
                details=str(exc),
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
            )
            return await ctx.send(
                "I could not prepare that answer. The question remains pending."
            )

        try:
            embeds = await self._answer_embeds(
                ctx.guild,
                staged,
            )

            final_message = await answers_channel.send(
                embeds=embeds,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            with suppress(Exception):
                await self.storage.abort_answer(
                    ctx.guild.id,
                    question_id,
                )

            log.exception(
                "Unable to post answer for Question #%s in guild %s.",
                question_id,
                ctx.guild.id,
            )
            await self._audit_error(
                ctx.guild,
                title="Answer send failed",
                details=str(exc),
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
            )
            return await ctx.send(
                "I could not post the answer. The staged answer was cleared "
                "and the question remains pending."
            )

        try:
            completed = await self.storage.complete_answer(
                ctx.guild.id,
                question_id,
                channel_id=answers_channel.id,
                message_id=final_message.id,
            )
        except Exception as exc:
            # Deliberately do NOT delete the answer here. Storage still contains
            # the staged answer and startup recovery can locate this exact bot
            # message by question number and operation timestamp.
            log.exception(
                "Answer message %s was posted for Question #%s but completion "
                "could not be persisted.",
                final_message.id,
                question_id,
            )
            await self._audit_error(
                ctx.guild,
                title="Answer posted but completion is pending",
                details=(
                    f"Answer message `{final_message.id}` exists, but the final "
                    f"storage transition failed: {exc}. Startup recovery will "
                    "attempt to attach the existing message."
                ),
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
            )
            return await ctx.send(
                "The answer was posted, but I could not complete the storage "
                "transition. **Do not answer the question again.** Startup "
                "recovery will attach the existing answer."
            )

        cleaned = await self._cleanup_pending_after_resolution(
            ctx.guild,
            completed,
        )

        await self._send_audit(
            ctx.guild,
            build_answer_log_embed(
                completed,
                operator_label=self._display_name(ctx.author),
                answer_jump_url=final_message.jump_url,
            ),
        )

        confirmation = (
            f"Question #{question_id} has been answered.\n"
            f"{UPVOTE_EMOJI} {votes['up']} • "
            f"{DOWNVOTE_EMOJI} {votes['down']}\n"
            f"{final_message.jump_url}"
        )

        if votes.get("conflicts"):
            confirmation += (
                f"\nDual votes ignored: {votes['conflicts']}"
            )

        if not cleaned:
            confirmation += (
                "\n⚠️ I could not delete the old pending message. "
                "The permanent answer is safe; the verbose log records the "
                "cleanup issue."
            )
            await self._audit_error(
                ctx.guild,
                title="Pending cleanup failed after answer",
                details=(
                    "The answer was safely persisted, but the old pending "
                    "question message could not be deleted."
                ),
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
            )

        await ctx.send(confirmation)

    @commands.command(name="answeredit")
    @commands.guild_only()
    async def answeredit(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
        *,
        content: str,
    ) -> None:
        """Edit a previously published answer in place."""

        if not await self._require_operator(ctx):
            return

        try:
            content = validate_length(
                content,
                maximum=MAX_ANSWER_LENGTH,
                label="Answers",
            )
            before = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )
            previous_answer = before.get("answer")
            previous_answer = (
                dict(previous_answer)
                if isinstance(previous_answer, Mapping)
                else {}
            )

            updated = await self.storage.edit_answer(
                ctx.guild.id,
                question_id,
                new_content=content,
                editor_id=ctx.author.id,
            )

            message = await self._ensure_answer_message(
                ctx.guild,
                updated,
                search_if_missing=True,
            )
        except (QuestionNotFound, StorageConflict, ValueError) as exc:
            return await ctx.send(str(exc))
        except Exception as exc:
            # The new answer is canonical even if Message.edit failed. Edited
            # answers are automatically resynchronised at startup.
            log.exception(
                "Unable to project answer edit for Question #%s in guild %s.",
                question_id,
                ctx.guild.id,
            )
            await self._audit_error(
                ctx.guild,
                title="Answer edit projection failed",
                details=str(exc),
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
            )
            return await ctx.send(
                "The new answer was saved, but I could not fully synchronise "
                "the published message. Startup recovery or `questions "
                "resendanswer` can repair it."
            )

        await self._send_audit(
            ctx.guild,
            build_answer_edit_log_embed(
                record=updated,
                actor_label=self._display_name(ctx.author),
                before=str(previous_answer.get("content") or ""),
                after=str((updated.get("answer") or {}).get("content") or ""),
            ),
        )

        await ctx.send(
            f"Answer for Question #{question_id} has been edited in place.\n"
            f"{message.jump_url}"
        )

    # ------------------------------------------------------------------
    # Soft removal
    # ------------------------------------------------------------------

    @commands.command(name="questionremove")
    @commands.guild_only()
    async def questionremove(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
        *,
        reason: Optional[str] = None,
    ) -> None:
        """Soft-remove a pending question while preserving its audit record."""

        if not await self._require_operator(ctx):
            return

        if reason and len(reason.strip()) > MAX_REMOVAL_REASON_LENGTH:
            return await ctx.send(
                f"Removal reasons may contain no more than "
                f"{MAX_REMOVAL_REASON_LENGTH:,} characters."
            )

        try:
            before = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )
            removed = await self.storage.remove_question(
                ctx.guild.id,
                question_id,
                actor_id=ctx.author.id,
                reason=reason,
            )
        except (QuestionNotFound, StorageConflict, ValueError) as exc:
            return await ctx.send(str(exc))

        cleaned = await self._cleanup_pending_after_resolution(
            ctx.guild,
            before,
        )

        await self._send_audit(
            ctx.guild,
            build_removal_log_embed(
                removed,
                actor_label=self._display_name(ctx.author),
            ),
        )

        text = f"Question #{question_id} has been soft-removed."

        if not cleaned:
            text += (
                "\n⚠️ Its pending message could not be deleted and may need "
                "manual cleanup."
            )

        await ctx.send(text)

    # ------------------------------------------------------------------
    # Main command group
    # ------------------------------------------------------------------

    @commands.group(
        name="questions",
        aliases=["questionset"],
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def questions(
        self,
        ctx: commands.Context,
    ) -> None:
        """View or configure Flux Questions."""

        if ctx.invoked_subcommand is None:
            await self._send_settings(ctx)

    @questions.command(
        name="settings",
        aliases=["status"],
    )
    async def questions_settings(
        self,
        ctx: commands.Context,
    ) -> None:
        """Display the current configuration."""

        await self._send_settings(ctx)

    async def _send_settings(
        self,
        ctx: commands.Context,
    ) -> None:
        conf = await self.config.guild(ctx.guild).all()
        stats = await self.storage.guild_statistics(ctx.guild.id)

        window_seconds = max(
            0,
            int(conf.get("author_edit_window_seconds") or 0),
        )

        if window_seconds % 60 == 0:
            window_label = f"{window_seconds // 60} minutes"
        else:
            window_label = f"{window_seconds} seconds"

        embed = build_settings_embed(
            questions_channel=self._channel_label(
                ctx.guild,
                conf.get("questions_channel"),
            ),
            answers_channel=self._channel_label(
                ctx.guild,
                conf.get("answers_channel"),
            ),
            log_channel=self._channel_label(
                ctx.guild,
                conf.get("log_channel"),
            ),
            question_emoji=emoji_display(
                conf.get("question_emoji")
            ),
            submitter_roles=self._roles_label(
                ctx.guild,
                conf.get("submitter_role_ids", []),
            ),
            editor_roles=self._roles_label(
                ctx.guild,
                conf.get("editor_role_ids", []),
            ),
            operator_roles=self._roles_label(
                ctx.guild,
                conf.get("operator_role_ids", []),
            ),
            source_channels=self._sources_label(
                ctx.guild,
                conf.get("source_channel_ids", []),
            ),
            author_edit_window=window_label,
            submitted=stats["submitted"],
            pending=stats["pending"],
            answered=stats["answered"],
            removed=stats["removed"],
            next_number=stats["counter"] + 1,
            version=self.__version__,
        )

        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    @questions.group(
        name="set",
        invoke_without_command=True,
    )
    @commands.admin_or_permissions(manage_guild=True)
    async def questions_set(
        self,
        ctx: commands.Context,
    ) -> None:
        """Configure Flux Questions."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    async def _set_channel_setting(
        self,
        ctx: commands.Context,
        *,
        key: str,
        channel: discord.TextChannel,
        required: Sequence[str],
        display_name: str,
    ) -> None:
        missing = self._missing_permissions(channel, required)

        if missing:
            return await ctx.send(
                f"I cannot use {channel.mention} as the {display_name}. "
                f"Missing permissions: {', '.join(missing)}."
            )

        scope = self.config.guild(ctx.guild)
        before = await getattr(scope, key)()
        await getattr(scope, key).set(channel.id)

        await ctx.send(
            f"The {display_name} has been set to {channel.mention}."
        )

        await self._send_audit(
            ctx.guild,
            build_config_change_log_embed(
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                setting=display_name,
                before=self._channel_label(ctx.guild, before),
                after=channel.mention,
            ),
        )

    @questions_set.command(
        name="questions",
        aliases=["pending"],
    )
    async def questions_set_questions(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the pending questions channel."""

        await self._set_channel_setting(
            ctx,
            key="questions_channel",
            channel=channel,
            required=PENDING_REQUIRED,
            display_name="questions channel",
        )

    @questions_set.command(name="answers")
    async def questions_set_answers(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the permanent answers channel."""

        await self._set_channel_setting(
            ctx,
            key="answers_channel",
            channel=channel,
            required=ANSWER_REQUIRED,
            display_name="answers channel",
        )

    @questions_set.command(
        name="log",
        aliases=["logging"],
    )
    async def questions_set_log(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        """Set the verbose audit-log channel."""

        await self._set_channel_setting(
            ctx,
            key="log_channel",
            channel=channel,
            required=LOG_REQUIRED,
            display_name="verbose log channel",
        )

        # The configuration-change entry should appear in the *new* log.
        await self._send_audit(
            ctx.guild,
            build_audit_embed(
                title="🧾 Verbose Logging Enabled",
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                description=(
                    "Flux Questions will write human-readable state changes, "
                    "edits, answers, recovery events and configuration changes "
                    "to this channel."
                ),
                colour=LOG_COLOUR,
            ),
        )

    @questions_set.command(name="emoji")
    async def questions_set_emoji(
        self,
        ctx: commands.Context,
        emoji: QuestionEmoji,
    ) -> None:
        """Set the reaction emoji used to submit a question."""

        scope = self.config.guild(ctx.guild)
        before = normalise_emoji_config(await scope.question_emoji())
        after = emoji_to_config(emoji)

        await scope.question_emoji.set(after)

        await ctx.send(
            f"The question submission emoji is now {emoji_display(after)}."
        )

        await self._send_audit(
            ctx.guild,
            build_config_change_log_embed(
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                setting="Question submission emoji",
                before=emoji_display(before),
                after=emoji_display(after),
            ),
        )

    @questions_set.command(
        name="editwindow",
        aliases=["editminutes"],
    )
    async def questions_set_editwindow(
        self,
        ctx: commands.Context,
        minutes: int,
    ) -> None:
        """Set the normal author's self-edit window in minutes."""

        if minutes < 1 or minutes > 1440:
            return await ctx.send(
                "The author edit window must be between 1 and 1,440 minutes."
            )

        scope = self.config.guild(ctx.guild)
        before = int(await scope.author_edit_window_seconds())
        after = minutes * 60

        await scope.author_edit_window_seconds.set(after)

        await ctx.send(
            f"Authors may now self-edit pending questions for "
            f"{minutes} minute(s). Configured editors remain able to edit "
            "after that soft-lock."
        )

        await self._send_audit(
            ctx.guild,
            build_config_change_log_embed(
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                setting="Author self-edit window",
                before=f"{before} seconds",
                after=f"{after} seconds ({minutes} minutes)",
            ),
        )

    # ------------------------------------------------------------------
    # Role configuration
    # ------------------------------------------------------------------

    @questions.group(
        name="role",
        aliases=["roles"],
        invoke_without_command=True,
    )
    @commands.admin_or_permissions(manage_guild=True)
    async def questions_role(
        self,
        ctx: commands.Context,
    ) -> None:
        """Configure submitter, editor and operator roles."""

        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @staticmethod
    def _role_key(kind: str) -> Optional[str]:
        mapping = {
            "submitter": "submitter_role_ids",
            "submit": "submitter_role_ids",
            "editor": "editor_role_ids",
            "edit": "editor_role_ids",
            "operator": "operator_role_ids",
            "op": "operator_role_ids",
        }
        return mapping.get(str(kind or "").casefold())

    @questions_role.command(name="add")
    async def questions_role_add(
        self,
        ctx: commands.Context,
        kind: str,
        role: GuildRole,
    ) -> None:
        """Add a configured Q&A role."""

        key = self._role_key(kind)

        if key is None:
            return await ctx.send(
                "Role type must be `submitter`, `editor`, or `operator`."
            )

        scope = self.config.guild(ctx.guild)
        before = unique_positive_ids(await getattr(scope, key)())
        after = list(before)

        if role.id not in after:
            after.append(role.id)

        await getattr(scope, key).set(after)

        await ctx.send(
            f"{role.mention} has been added as a "
            f"{kind.casefold()} role."
        )

        await self._send_audit(
            ctx.guild,
            build_config_change_log_embed(
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                setting=f"{kind.casefold()} roles",
                before=self._roles_label(ctx.guild, before),
                after=self._roles_label(ctx.guild, after),
            ),
        )

    @questions_role.command(name="remove")
    async def questions_role_remove(
        self,
        ctx: commands.Context,
        kind: str,
        role: GuildRole,
    ) -> None:
        """Remove a configured Q&A role."""

        key = self._role_key(kind)

        if key is None:
            return await ctx.send(
                "Role type must be `submitter`, `editor`, or `operator`."
            )

        scope = self.config.guild(ctx.guild)
        before = unique_positive_ids(await getattr(scope, key)())
        after = [item for item in before if item != role.id]

        await getattr(scope, key).set(after)

        await ctx.send(
            f"{role.mention} has been removed from the "
            f"{kind.casefold()} roles."
        )

        await self._send_audit(
            ctx.guild,
            build_config_change_log_embed(
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                setting=f"{kind.casefold()} roles",
                before=self._roles_label(ctx.guild, before),
                after=self._roles_label(ctx.guild, after),
            ),
        )

    # ------------------------------------------------------------------
    # Source-channel restrictions
    # ------------------------------------------------------------------

    @questions.group(
        name="source",
        aliases=["sources"],
        invoke_without_command=True,
    )
    @commands.admin_or_permissions(manage_guild=True)
    async def questions_source(
        self,
        ctx: commands.Context,
    ) -> None:
        """Restrict reaction submission to selected source channels."""

        if ctx.invoked_subcommand is None:
            conf = await self.config.guild(ctx.guild).all()
            await ctx.send(
                "Allowed question source channels: "
                + self._sources_label(
                    ctx.guild,
                    conf.get("source_channel_ids", []),
                )
            )

    @questions_source.command(name="add")
    async def questions_source_add(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        scope = self.config.guild(ctx.guild)
        before = unique_positive_ids(await scope.source_channel_ids())
        after = list(before)

        if channel.id not in after:
            after.append(channel.id)

        await scope.source_channel_ids.set(after)

        await ctx.send(
            f"{channel.mention} is now an allowed question source channel."
        )

        await self._send_audit(
            ctx.guild,
            build_config_change_log_embed(
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                setting="Allowed source channels",
                before=self._sources_label(ctx.guild, before),
                after=self._sources_label(ctx.guild, after),
            ),
        )

    @questions_source.command(name="remove")
    async def questions_source_remove(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
    ) -> None:
        scope = self.config.guild(ctx.guild)
        before = unique_positive_ids(await scope.source_channel_ids())
        after = [item for item in before if item != channel.id]

        await scope.source_channel_ids.set(after)

        await ctx.send(
            f"{channel.mention} is no longer an allowed question source."
        )

        await self._send_audit(
            ctx.guild,
            build_config_change_log_embed(
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                setting="Allowed source channels",
                before=self._sources_label(ctx.guild, before),
                after=self._sources_label(ctx.guild, after),
            ),
        )

    @questions_source.command(name="clear")
    async def questions_source_clear(
        self,
        ctx: commands.Context,
    ) -> None:
        scope = self.config.guild(ctx.guild)
        before = unique_positive_ids(await scope.source_channel_ids())
        await scope.source_channel_ids.set([])

        await ctx.send(
            "Source-channel restrictions have been cleared. The configured "
            "question emoji may now submit eligible messages from any channel."
        )

        await self._send_audit(
            ctx.guild,
            build_config_change_log_embed(
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                setting="Allowed source channels",
                before=self._sources_label(ctx.guild, before),
                after="All eligible channels",
            ),
        )

    # ------------------------------------------------------------------
    # Lookup / list / history / statistics
    # ------------------------------------------------------------------

    @questions.command(name="show")
    async def questions_show(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
    ) -> None:
        """Display a question's stored state."""

        if not await self._require_editor(ctx):
            return

        try:
            record = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )
        except QuestionNotFound as exc:
            return await ctx.send(str(exc))

        author = await self._resolve_user(
            ctx.guild,
            record.get("author_id"),
        )
        submitter = await self._resolve_user(
            ctx.guild,
            record.get("submitted_by_id"),
        )
        answer = record.get("answer")
        answer = dict(answer) if isinstance(answer, Mapping) else {}

        embed = build_question_info_embed(
            record,
            author_label=self._display_name(author),
            submitted_by_label=self._display_name(submitter),
            pending_jump_url=fluxer_jump_url(
                ctx.guild.id,
                record.get("pending_channel_id"),
                record.get("pending_message_id"),
            ),
            answer_jump_url=fluxer_jump_url(
                ctx.guild.id,
                answer.get("channel_id"),
                answer.get("message_id"),
            ),
            source_jump_url=fluxer_jump_url(
                ctx.guild.id,
                record.get("source_channel_id"),
                record.get("source_message_id"),
            ),
        )

        await ctx.send(embed=embed)

    @questions.command(name="list")
    async def questions_list(
        self,
        ctx: commands.Context,
        status: str = "pending",
        page: int = 1,
    ) -> None:
        """List stored questions by state."""

        if not await self._require_editor(ctx):
            return

        if status.isdigit() and page == 1:
            page = int(status)
            status = "pending"

        status = status.casefold().strip()

        if status not in {"pending", "answered", "removed"}:
            return await ctx.send(
                "Status must be `pending`, `answered`, or `removed`."
            )

        records = await self.storage.list_questions(
            ctx.guild.id,
            status=status,
        )

        if not records:
            return await ctx.send(
                f"There are no {status} questions."
            )

        total_pages = max(1, ceil(len(records) / LIST_PAGE_SIZE))
        page = max(1, page)

        if page > total_pages:
            return await ctx.send(
                f"Page {page} does not exist. There are "
                f"{total_pages} page(s)."
            )

        start = (page - 1) * LIST_PAGE_SIZE
        selected = records[start : start + LIST_PAGE_SIZE]
        lines: List[str] = []

        for record in selected:
            number = int(record["number"])
            preview = shorten(record.get("content"), 180)
            lines.append(
                f"**#{number}** — {format_timestamp(record.get('created_at'))}\n"
                f"> {preview}"
            )

        embed = discord.Embed(
            title=f"{status.title()} Questions",
            description="\n\n".join(lines),
            colour=discord.Colour(
                QUESTION_COLOUR
                if status == "pending"
                else ANSWER_COLOUR
                if status == "answered"
                else ERROR_COLOUR
            ),
        )
        embed.set_footer(
            text=f"Page {page}/{total_pages} • {len(records)} record(s)"
        )

        await ctx.send(embed=embed)

    @questions.command(name="history")
    async def questions_history(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
        kind: str = "question",
        page: int = 1,
    ) -> None:
        """Display stored question or answer revisions."""

        if not await self._require_editor(ctx):
            return

        try:
            record = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )
        except QuestionNotFound as exc:
            return await ctx.send(str(exc))

        answer_history = kind.casefold() in {"answer", "answers", "a"}

        if not answer_history and kind.casefold() not in {
            "question",
            "questions",
            "q",
        }:
            return await ctx.send(
                "History type must be `question` or `answer`."
            )

        revisions = (
            (record.get("answer") or {}).get("revisions", [])
            if answer_history
            else record.get("revisions", [])
        )

        editor_ids = {
            positive_int(item.get("editor_id"))
            for item in revisions
            if isinstance(item, Mapping)
        }
        editor_ids.discard(None)

        labels: Dict[int, str] = {}
        for editor_id in editor_ids:
            user = await self._resolve_user(ctx.guild, editor_id)
            labels[int(editor_id)] = self._display_name(user)

        embed = build_revision_history_embed(
            record,
            answer_history=answer_history,
            page=page,
            editor_labels=labels,
        )
        await ctx.send(embed=embed)

    @questions.command(name="stats")
    async def questions_stats(
        self,
        ctx: commands.Context,
    ) -> None:
        """Display aggregate permanent-record statistics."""

        if not await self._require_editor(ctx):
            return

        stats = await self.storage.guild_statistics(ctx.guild.id)

        embed = discord.Embed(
            title="Flux Questions Statistics",
            colour=discord.Colour(QUESTION_COLOUR),
        )
        embed.add_field(
            name="Submitted",
            value=str(stats["submitted"]),
            inline=True,
        )
        embed.add_field(
            name="Pending",
            value=str(stats["pending"]),
            inline=True,
        )
        embed.add_field(
            name="Answered",
            value=str(stats["answered"]),
            inline=True,
        )
        embed.add_field(
            name="Removed",
            value=str(stats["removed"]),
            inline=True,
        )
        embed.add_field(
            name="Last number",
            value=f"#{stats['counter']}",
            inline=True,
        )
        embed.add_field(
            name="Next number",
            value=f"#{stats['counter'] + 1}",
            inline=True,
        )

        await ctx.send(embed=embed)

    # ------------------------------------------------------------------
    # Repair / resend
    # ------------------------------------------------------------------

    @questions.command(name="resend")
    async def questions_resend(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
    ) -> None:
        """Repair or recreate a pending question without changing its ID."""

        if not await self._require_operator(ctx):
            return

        try:
            record = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )

            if record.get("status") != "pending":
                return await ctx.send(
                    f"Question #{question_id} is not pending."
                )

            conf = await self.config.guild(ctx.guild).all()
            message = await self._ensure_pending_message(
                ctx.guild,
                record,
                conf,
            )
        except (QuestionNotFound, StorageConflict, ValueError) as exc:
            return await ctx.send(str(exc))
        except Exception as exc:
            await self._audit_error(
                ctx.guild,
                title="Pending question repair failed",
                details=str(exc),
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
            )
            return await ctx.send(
                "I could not repair that pending question. "
                "Check the verbose log."
            )

        await self._send_audit(
            ctx.guild,
            build_recovery_log_embed(
                title="🛠️ Pending Question Repaired",
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                details=(
                    "The canonical pending record was projected back onto "
                    "Fluxer. Existing votes were preserved when the old "
                    "message still existed."
                ),
                jump_url=message.jump_url,
            ),
        )

        await ctx.send(
            f"Question #{question_id} is synchronised.\n"
            f"{message.jump_url}"
        )

    @questions.command(name="resendanswer")
    async def questions_resendanswer(
        self,
        ctx: commands.Context,
        question_id: QuestionID,
    ) -> None:
        """Repair or recreate an answered question without changing its ID."""

        if not await self._require_operator(ctx):
            return

        try:
            record = await self.storage.require_question(
                ctx.guild.id,
                question_id,
            )

            if record.get("status") != "answered":
                return await ctx.send(
                    f"Question #{question_id} has not been answered."
                )

            message = await self._ensure_answer_message(
                ctx.guild,
                record,
                search_if_missing=True,
            )
        except (QuestionNotFound, StorageConflict, ValueError) as exc:
            return await ctx.send(str(exc))
        except Exception as exc:
            await self._audit_error(
                ctx.guild,
                title="Answer repair failed",
                details=str(exc),
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
            )
            return await ctx.send(
                "I could not repair that answer. Check the verbose log."
            )

        await self._send_audit(
            ctx.guild,
            build_recovery_log_embed(
                title="🛠️ Answer Repaired",
                question_number=question_id,
                actor_label=self._display_name(ctx.author),
                occurred_at=unix_now(),
                details=(
                    "The permanent answer was synchronised from canonical "
                    "storage without changing its Question ID."
                ),
                jump_url=message.jump_url,
            ),
        )

        await ctx.send(
            f"Answer for Question #{question_id} is synchronised.\n"
            f"{message.jump_url}"
        )

    # ------------------------------------------------------------------
    # Manual reconciliation
    # ------------------------------------------------------------------

    @questions.command(name="reconcile")
    @commands.admin_or_permissions(manage_guild=True)
    async def questions_reconcile(
        self,
        ctx: commands.Context,
    ) -> None:
        """Run the same recovery pass used automatically after startup."""

        await ctx.send(
            "Running Flux Questions reconciliation for this community…"
        )

        try:
            await self._reconcile_guild_messages(ctx.guild)
        except Exception as exc:
            log.exception(
                "Manual FluxQuestions reconciliation failed in guild %s.",
                ctx.guild.id,
            )
            await self._audit_error(
                ctx.guild,
                title="Manual reconciliation failed",
                details=str(exc),
                actor_label=self._display_name(ctx.author),
            )
            return await ctx.send(
                "Reconciliation failed. Check the verbose log and Python log."
            )

        await ctx.send("Flux Questions reconciliation completed.")

    # ------------------------------------------------------------------
    # Red data deletion
    # ------------------------------------------------------------------

    async def red_delete_data_for_user(
        self,
        *,
        requester,
        user_id: int,
    ) -> None:
        """Best-effort removal of stored identifiers for a Red deletion request.

        Permanent question numbering and non-personal aggregate statistics are
        retained. When the requesting user authored a question, its question
        text and question revision contents are replaced so the cog does not
        continue retaining their submitted text as part of its archive.
        """

        target_id = positive_int(user_id)

        if target_id is None:
            return

        all_guilds = await self.config.all_guilds()

        for guild_id_value in all_guilds:
            guild_id = positive_int(guild_id_value)

            if guild_id is None:
                continue

            lock = self.storage.lock_for(guild_id)

            changed_any = False

            async with lock:
                records = await self.storage.list_questions(guild_id)

                for record in records:
                    changed = False
                    authored = positive_int(record.get("author_id")) == target_id

                    if authored:
                        record["author_id"] = None
                        record["content"] = (
                            "[Question content removed following a user "
                            "data-deletion request.]"
                        )
                        record["source_channel_id"] = None
                        record["source_message_id"] = None
                        changed = True

                        for revision in record.get("revisions", []):
                            if isinstance(revision, dict):
                                revision["content"] = (
                                    "[Question revision removed following a "
                                    "user data-deletion request.]"
                                )

                    if positive_int(record.get("submitted_by_id")) == target_id:
                        record["submitted_by_id"] = None
                        changed = True

                    for revision in record.get("revisions", []):
                        if (
                            isinstance(revision, dict)
                            and positive_int(revision.get("editor_id")) == target_id
                        ):
                            revision["editor_id"] = None
                            changed = True

                    answer = record.get("answer")
                    if isinstance(answer, dict):
                        if positive_int(answer.get("author_id")) == target_id:
                            answer["author_id"] = None
                            changed = True

                        for revision in answer.get("revisions", []):
                            if (
                                isinstance(revision, dict)
                                and positive_int(revision.get("editor_id")) == target_id
                            ):
                                revision["editor_id"] = None
                                changed = True

                    removal = record.get("removal")
                    if (
                        isinstance(removal, dict)
                        and positive_int(removal.get("actor_id")) == target_id
                    ):
                        removal["actor_id"] = None
                        changed = True

                    operation = record.get("operation")
                    if (
                        isinstance(operation, dict)
                        and positive_int(operation.get("actor_id")) == target_id
                    ):
                        operation["actor_id"] = None
                        changed = True

                    if not changed:
                        continue

                    await self.storage._question_scope(
                        guild_id,
                        int(record["number"]),
                    ).set(record)
                    changed_any = True

            # Reconcile outside the per-guild lock because reconcile_guild()
            # acquires that same lock internally.
            if changed_any:
                await self.storage.reconcile_guild(guild_id)
