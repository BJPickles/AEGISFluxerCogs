"""Command converters for FluxQuestions.

The converters here are intentionally small and Fluxer-friendly. They avoid
depending on message components or persistent interaction state and accept the
human-friendly forms used throughout the cog.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Optional

import discord
from redbot.core import commands

from .utils import parse_question_number, positive_int

CUSTOM_EMOJI_RE = re.compile(
    r"^<(?P<animated>a?):(?P<name>[A-Za-z0-9_~]+):(?P<id>[0-9]+)>$"
)
ROLE_MENTION_RE = re.compile(r"^<@&(?P<id>[0-9]+)>$")


class QuestionID(commands.Converter):
    """Convert ``147`` or ``#147`` into the integer question number."""

    async def convert(
        self,
        ctx: commands.Context,
        argument: str,
    ) -> int:
        number = parse_question_number(argument)

        if number is None:
            raise commands.BadArgument(
                "Question IDs must be a positive number such as `147` "
                "or `#147`."
            )

        return number


class GuildRole(commands.Converter):
    """Resolve a guild role without relying solely on Red's role converter.

    Accepted forms:

    - ``@Role`` / ``<@&123456789>`` mention syntax
    - raw numeric role ID
    - exact role name
    - exact role name, case-insensitive

    The explicit ID/name lookup is useful on Fluxer forks where upstream
    Discord converters may not always resolve role input in the same way.
    """

    async def convert(
        self,
        ctx: commands.Context,
        argument: str,
    ) -> discord.Role:
        guild = ctx.guild

        if guild is None:
            raise commands.BadArgument(
                "Roles can only be resolved inside a community."
            )

        raw = str(argument or "").strip()

        if not raw:
            raise commands.BadArgument("Please provide a role.")

        role_id = self._role_id_from_argument(raw)

        if role_id is not None:
            role = guild.get_role(role_id)

            if role is not None:
                return role

            raise commands.BadArgument(
                f"I could not find a role with ID `{role_id}` "
                "in this community."
            )

        roles = list(getattr(guild, "roles", []))

        exact = [role for role in roles if role.name == raw]

        if len(exact) == 1:
            return exact[0]

        folded = raw.casefold()
        insensitive = [
            role for role in roles
            if str(getattr(role, "name", "")).casefold() == folded
        ]

        if len(insensitive) == 1:
            return insensitive[0]

        if len(insensitive) > 1:
            matches = ", ".join(
                f"{role.name} (`{role.id}`)"
                for role in insensitive[:5]
            )

            raise commands.BadArgument(
                "More than one role has that name. "
                f"Use a role ID instead. Matches: {matches}"
            )

        raise commands.BadArgument(
            f"I could not find the role `{raw}` in this community. "
            "Try its exact name, mention, or numeric role ID."
        )

    @staticmethod
    def _role_id_from_argument(argument: str) -> Optional[int]:
        mention = ROLE_MENTION_RE.fullmatch(argument)

        if mention is not None:
            return positive_int(mention.group("id"))

        if argument.isdigit():
            return positive_int(argument)

        return None


class QuestionEmoji(commands.Converter):
    """Resolve the guild-specific emoji used to submit questions.

    Custom emoji can be supplied as:

    - a normal custom emoji mention, e.g. ``<:ask:123456789>``
    - its numeric emoji ID
    - ``:name:``
    - its exact name

    Literal Unicode emoji are returned unchanged.

    The main cog serialises the returned value with
    :func:`fluxquestions.utils.emoji_to_config`.
    """

    async def convert(
        self,
        ctx: commands.Context,
        argument: str,
    ) -> Any:
        raw = str(argument or "").strip()

        if not raw:
            raise commands.BadArgument("Please provide an emoji.")

        guild = ctx.guild

        # Custom emoji mention: <:name:id> or <a:name:id>
        match = CUSTOM_EMOJI_RE.fullmatch(raw)

        if match is not None:
            if guild is None:
                raise commands.BadArgument(
                    "Custom community emoji can only be configured "
                    "inside a community."
                )

            emoji_id = int(match.group("id"))
            emoji = self._get_guild_emoji(guild, emoji_id)

            if emoji is None:
                raise commands.BadArgument(
                    f"I could not find custom emoji `{emoji_id}` "
                    "in this community."
                )

            return emoji

        # Raw custom emoji ID.
        if raw.isdigit():
            if guild is None:
                raise commands.BadArgument(
                    "Custom community emoji can only be configured "
                    "inside a community."
                )

            emoji_id = int(raw)
            emoji = self._get_guild_emoji(guild, emoji_id)

            if emoji is None:
                raise commands.BadArgument(
                    f"I could not find custom emoji `{emoji_id}` "
                    "in this community."
                )

            return emoji

        # :name: convenience form.
        candidate_name = raw

        if (
            len(raw) >= 3
            and raw.startswith(":")
            and raw.endswith(":")
        ):
            candidate_name = raw[1:-1]

        if guild is not None:
            named = self._find_named_emoji(guild, candidate_name)

            if named is not None:
                return named

        if self._looks_like_unicode_emoji(raw):
            return raw

        raise commands.BadArgument(
            "I could not resolve that as a community emoji or Unicode emoji. "
            "Use the emoji itself, a custom emoji mention, its exact name, "
            "or its numeric emoji ID."
        )

    @staticmethod
    def _get_guild_emoji(
        guild: discord.Guild,
        emoji_id: int,
    ) -> Optional[Any]:
        get_emoji = getattr(guild, "get_emoji", None)

        if callable(get_emoji):
            emoji = get_emoji(emoji_id)

            if emoji is not None:
                return emoji

        for emoji in getattr(guild, "emojis", []):
            if positive_int(getattr(emoji, "id", None)) == emoji_id:
                return emoji

        return None

    @staticmethod
    def _find_named_emoji(
        guild: discord.Guild,
        name: str,
    ) -> Optional[Any]:
        if not name:
            return None

        emojis = list(getattr(guild, "emojis", []))

        exact = [
            emoji
            for emoji in emojis
            if str(getattr(emoji, "name", "")) == name
        ]

        if len(exact) == 1:
            return exact[0]

        folded = name.casefold()
        insensitive = [
            emoji
            for emoji in emojis
            if str(getattr(emoji, "name", "")).casefold() == folded
        ]

        if len(insensitive) == 1:
            return insensitive[0]

        # Ambiguous names intentionally fall through. Numeric ID or a rendered
        # custom-emoji mention will always disambiguate them.
        return None

    @staticmethod
    def _looks_like_unicode_emoji(value: str) -> bool:
        """Conservatively recognise literal Unicode emoji sequences.

        This is deliberately permissive around variation selectors, zero-width
        joiners, keycaps and regional indicators while rejecting ordinary ASCII
        words such as ``question``.
        """

        if not value or any(char.isspace() for char in value):
            return False

        has_emojiish_character = False

        for char in value:
            codepoint = ord(char)
            category = unicodedata.category(char)

            if codepoint in {
                0x200D,   # zero-width joiner
                0x20E3,   # combining enclosing keycap
                0xFE0E,   # text variation selector
                0xFE0F,   # emoji variation selector
            }:
                continue

            # Fitzpatrick skin-tone modifiers.
            if 0x1F3FB <= codepoint <= 0x1F3FF:
                has_emojiish_character = True
                continue

            # Regional indicator symbols used for flags.
            if 0x1F1E6 <= codepoint <= 0x1F1FF:
                has_emojiish_character = True
                continue

            # Most pictographic emoji live in these ranges.
            if (
                0x1F000 <= codepoint <= 0x1FAFF
                or 0x2600 <= codepoint <= 0x27BF
                or 0x2300 <= codepoint <= 0x23FF
            ):
                has_emojiish_character = True
                continue

            # Keycap bases and symbols such as ©, ® and ™ can participate in
            # emoji presentation.
            if char in "#*0123456789©®™":
                continue

            # Permit other Unicode symbol characters, but not ordinary
            # letters/numbers/punctuation.
            if category == "So":
                has_emojiish_character = True
                continue

            return False

        return has_emojiish_character
