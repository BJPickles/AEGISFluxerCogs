"""Shared, side-effect-free helpers for FluxQuestions.

This module performs no Config I/O and sends no Fluxer messages. It contains
only formatting, validation, parsing and comparison helpers shared by the
storage, embed and cog layers.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

FLUXER_CHANNEL_URL = "https://fluxer.app/channels/{guild_id}/{channel_id}/{message_id}"

DEFAULT_QUESTION_EMOJI = "❓"
UPVOTE_EMOJI = "👍"
DOWNVOTE_EMOJI = "👎"

# Application limits. Keeping them here gives commands, storage validation and
# embed construction a single source of truth.
MAX_QUESTION_LENGTH = 4000
MAX_ANSWER_LENGTH = 4000
MAX_REMOVAL_REASON_LENGTH = 1000
MAX_LOG_TEXT_LENGTH = 1800

QUESTION_ID_RE = re.compile(r"^\s*#?(?P<number>[1-9][0-9]*)\s*$")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def unix_now() -> int:
    """Return the current Unix timestamp as an integer."""

    return int(time.time())


def safe_timestamp(value: Any) -> Optional[int]:
    """Return a positive Unix timestamp, or ``None`` when invalid."""

    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None

    return timestamp if timestamp > 0 else None


def format_timestamp(value: Any) -> str:
    """Render the cog-wide Fluxer timestamp format.

    Example:
        <t:1785181680:F> (<t:1785181680:R>)
    """

    timestamp = safe_timestamp(value)

    if timestamp is None:
        return "Unknown"

    return f"<t:{timestamp}:F> (<t:{timestamp}:R>)"


def edit_deadline(
    created_at: Any,
    window_seconds: Any,
) -> Optional[int]:
    """Return the absolute author-edit deadline for a question."""

    created = safe_timestamp(created_at)

    try:
        window = int(window_seconds)
    except (TypeError, ValueError):
        return None

    if created is None or window < 0:
        return None

    return created + window


def author_edit_open(
    created_at: Any,
    window_seconds: Any,
    *,
    now: Optional[int] = None,
) -> bool:
    """Return whether the normal author's timed edit window is still open."""

    deadline = edit_deadline(created_at, window_seconds)

    if deadline is None:
        return False

    current = safe_timestamp(now) if now is not None else unix_now()

    if current is None:
        return False

    return current <= deadline


# ---------------------------------------------------------------------------
# Integer / text helpers
# ---------------------------------------------------------------------------


def safe_int(
    value: Any,
    default: int = 0,
) -> int:
    """Convert a value to ``int`` without raising."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def positive_int(value: Any) -> Optional[int]:
    """Return a positive integer, or ``None``."""

    result = safe_int(value, 0)
    return result if result > 0 else None


def clean_user_text(value: Any) -> str:
    """Trim only outer whitespace while preserving Markdown and newlines."""

    return str(value or "").strip()


def validate_length(
    text: Any,
    *,
    maximum: int,
    label: str,
) -> str:
    """Validate required user text without altering its internal formatting."""

    cleaned = clean_user_text(text)

    if not cleaned:
        raise ValueError(f"{label} cannot be empty.")

    if len(cleaned) > maximum:
        raise ValueError(
            f"{label} may contain no more than {maximum:,} characters."
        )

    return cleaned


def shorten(
    text: Any,
    limit: int,
    *,
    preserve_newlines: bool = False,
) -> str:
    """Return a display preview without changing the canonical stored text."""

    if limit < 1:
        return ""

    value = str(text or "")

    if not preserve_newlines:
        value = " ".join(value.split())

    if len(value) <= limit:
        return value

    if limit == 1:
        return "…"

    return value[: limit - 1].rstrip() + "…"


def truncate_middle(
    text: Any,
    limit: int,
    *,
    marker: str = "\n…\n",
) -> str:
    """Truncate audit text while retaining both its beginning and end."""

    value = str(text or "")

    if limit <= 0:
        return ""

    if len(value) <= limit:
        return value

    if len(marker) >= limit:
        return value[:limit]

    remaining = limit - len(marker)
    left = (remaining + 1) // 2
    right = remaining // 2

    return f"{value[:left]}{marker}{value[-right:] if right else ''}"


# ---------------------------------------------------------------------------
# Raw Markdown / codeblock helpers
# ---------------------------------------------------------------------------


def _longest_backtick_run(text: str) -> int:
    longest = 0
    current = 0

    for char in text:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0

    return longest


def code_fence_for(text: Any, *, minimum: int = 3) -> str:
    """Return a backtick fence longer than any run inside ``text``."""

    value = str(text or "")
    fence_length = max(minimum, _longest_backtick_run(value) + 1)
    return "`" * fence_length


def raw_markdown_codeblock(
    text: Any,
    *,
    language: str = "text",
) -> str:
    """Wrap arbitrary Markdown in a safely copyable fenced codeblock.

    The fence expands when user content already contains backtick fences, so
    the copied raw Markdown cannot prematurely terminate the outer codeblock.
    """

    value = str(text or "")
    fence = code_fence_for(value)

    language = str(language or "").strip()
    opener = fence + language if language else fence

    return f"{opener}\n{value}\n{fence}"


# ---------------------------------------------------------------------------
# Question-number helpers
# ---------------------------------------------------------------------------


def parse_question_number(value: Any) -> Optional[int]:
    """Parse ``147`` or ``#147`` into integer ``147``."""

    match = QUESTION_ID_RE.fullmatch(str(value or ""))

    if match is None:
        return None

    return int(match.group("number"))


def question_label(number: Any) -> str:
    """Return the standard visible question label."""

    parsed = positive_int(number)
    return f"Question #{parsed}" if parsed is not None else "Question #?"


# ---------------------------------------------------------------------------
# Fluxer URL helpers
# ---------------------------------------------------------------------------


def fluxer_jump_url(
    guild_id: Any,
    channel_id: Any,
    message_id: Any,
) -> Optional[str]:
    """Build a Fluxer message URL when all identifiers are valid."""

    guild = positive_int(guild_id)
    channel = positive_int(channel_id)
    message = positive_int(message_id)

    if guild is None or channel is None or message is None:
        return None

    return FLUXER_CHANNEL_URL.format(
        guild_id=guild,
        channel_id=channel,
        message_id=message,
    )


# ---------------------------------------------------------------------------
# Emoji helpers
# ---------------------------------------------------------------------------


def emoji_to_config(emoji: Any) -> Dict[str, Any]:
    """Serialise a Unicode or custom emoji for persistent configuration.

    Custom emoji identity is the numeric emoji ID. ``value`` preserves its
    printable form for settings and audit logs.
    """

    emoji_id = positive_int(getattr(emoji, "id", None))
    emoji_name = getattr(emoji, "name", None)

    if emoji_id is not None:
        return {
            "type": "custom",
            "value": str(emoji),
            "id": emoji_id,
            "name": str(emoji_name or ""),
        }

    value = str(emoji or "").strip()

    if not value:
        raise ValueError("Emoji cannot be empty.")

    return {
        "type": "unicode",
        "value": value,
        "id": None,
        "name": None,
    }


def normalise_emoji_config(raw: Any) -> Dict[str, Any]:
    """Return a safe configured-emoji dictionary."""

    if not isinstance(raw, Mapping):
        raw = {}

    emoji_type = str(raw.get("type") or "").strip().lower()

    if emoji_type == "custom":
        emoji_id = positive_int(raw.get("id"))

        if emoji_id is not None:
            return {
                "type": "custom",
                "value": str(raw.get("value") or "").strip(),
                "id": emoji_id,
                "name": str(raw.get("name") or "").strip() or None,
            }

    value = str(raw.get("value") or DEFAULT_QUESTION_EMOJI).strip()

    if not value:
        value = DEFAULT_QUESTION_EMOJI

    return {
        "type": "unicode",
        "value": value,
        "id": None,
        "name": None,
    }


def emoji_matches_config(
    emoji: Any,
    configured: Any,
) -> bool:
    """Return whether a reaction emoji matches the configured trigger."""

    conf = normalise_emoji_config(configured)

    if conf["type"] == "custom":
        return positive_int(getattr(emoji, "id", None)) == conf["id"]

    return str(emoji) == conf["value"]


def emoji_display(configured: Any) -> str:
    """Return a readable representation of the configured question emoji."""

    conf = normalise_emoji_config(configured)

    if conf["type"] == "unicode":
        return conf["value"]

    if conf["value"]:
        return conf["value"]

    name = conf.get("name") or "question"
    return f"<:{name}:{conf['id']}>"


# ---------------------------------------------------------------------------
# Role / collection helpers
# ---------------------------------------------------------------------------


def unique_positive_ids(values: Iterable[Any]) -> List[int]:
    """Normalise IDs while preserving first-seen order."""

    output: List[int] = []
    seen = set()

    for value in values:
        item = positive_int(value)

        if item is None or item in seen:
            continue

        seen.add(item)
        output.append(item)

    return output


def object_has_any_id(
    objects: Sequence[Any],
    configured_ids: Sequence[Any],
) -> bool:
    """Return whether any object's ``id`` appears in the configured IDs."""

    wanted = set(unique_positive_ids(configured_ids))

    if not wanted:
        return False

    return any(
        positive_int(getattr(item, "id", None)) in wanted
        for item in objects
    )


def ids_added_removed(
    before: Iterable[Any],
    after: Iterable[Any],
) -> Tuple[List[int], List[int]]:
    """Return ``(added, removed)`` IDs for configuration audit logging."""

    before_ids = set(unique_positive_ids(before))
    after_ids = set(unique_positive_ids(after))

    return sorted(after_ids - before_ids), sorted(before_ids - after_ids)
  
