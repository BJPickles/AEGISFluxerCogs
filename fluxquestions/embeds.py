"""Embed builders for FluxQuestions.

All public question/answer presentation and human-readable audit-log embeds live
here. The main cog supplies already-resolved display labels (user names,
channel mentions, jump URLs, etc.) so this module performs no network I/O.

A completed Q&A may contain up to 4,000 characters of question text and 4,000
characters of answer text. A single Discord/Fluxer embed cannot safely hold
both at maximum size, so ``build_answer_embeds`` returns one embed when the
combined content fits and a visually matched two-embed card when it does not.
Nothing is silently truncated from the public question or answer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

import discord

from .utils import (
    DOWNVOTE_EMOJI,
    MAX_LOG_TEXT_LENGTH,
    UPVOTE_EMOJI,
    format_timestamp,
    positive_int,
    question_label,
    raw_markdown_codeblock,
    shorten,
    truncate_middle,
)

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

QUESTION_COLOUR = 0x5865F2
ANSWER_COLOUR = 0x57F287
REMOVED_COLOUR = 0xED4245
EDIT_COLOUR = 0xFEE75C
LOG_COLOUR = 0x99AAB5
ERROR_COLOUR = 0xED4245

# Discord-compatible embed limits. Fluxer patches are expected to preserve the
# same practical limits, and staying inside them protects us on either side.
EMBED_DESCRIPTION_LIMIT = 4096
EMBED_FIELD_VALUE_LIMIT = 1024
EMBED_FIELD_NAME_LIMIT = 256
EMBED_FOOTER_LIMIT = 2048
EMBED_TOTAL_LIMIT = 6000

# Leave room for titles, fields and footer when deciding whether a completed
# Q&A can live in one embed. We intentionally use a conservative threshold.
COMBINED_QA_TEXT_LIMIT = 5000


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------


def _record_number(record: Mapping[str, Any]) -> int:
    return positive_int(record.get("number")) or 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _status(record: Mapping[str, Any]) -> str:
    value = _text(record.get("status")).lower()
    return value if value in {"pending", "answered", "removed"} else "pending"


def _safe_field_value(value: Any, *, fallback: str = "Unknown") -> str:
    text = _text(value) or fallback

    if len(text) <= EMBED_FIELD_VALUE_LIMIT:
        return text

    return shorten(text, EMBED_FIELD_VALUE_LIMIT)


def _safe_field_name(value: Any) -> str:
    text = _text(value) or "Information"

    if len(text) <= EMBED_FIELD_NAME_LIMIT:
        return text

    return shorten(text, EMBED_FIELD_NAME_LIMIT)


def _safe_footer(value: Any) -> str:
    text = _text(value)

    if len(text) <= EMBED_FOOTER_LIMIT:
        return text

    return shorten(text, EMBED_FOOTER_LIMIT)


def _answer(record: Mapping[str, Any]) -> Dict[str, Any]:
    value = record.get("answer")
    return dict(value) if isinstance(value, Mapping) else {}


def _votes(record: Mapping[str, Any]) -> Dict[str, Any]:
    value = record.get("votes")
    return dict(value) if isinstance(value, Mapping) else {}


def _revision_count(record: Mapping[str, Any]) -> int:
    revisions = record.get("revisions")
    return len(revisions) if isinstance(revisions, list) else 0


def _answer_revision_count(record: Mapping[str, Any]) -> int:
    answer = _answer(record)
    revisions = answer.get("revisions")
    return len(revisions) if isinstance(revisions, list) else 0


def _vote_line(votes: Mapping[str, Any]) -> str:
    up = max(0, int(votes.get("up") or 0))
    down = max(0, int(votes.get("down") or 0))

    text = f"{UPVOTE_EMOJI} **{up}**   {DOWNVOTE_EMOJI} **{down}**"

    conflicts = max(0, int(votes.get("conflicts") or 0))
    if conflicts:
        text += f"\n⚠️ Dual votes ignored: **{conflicts}**"

    if votes.get("exact") is False:
        text += "\n*Reaction-count fallback was used.*"

    return text


def _set_footer(
    embed: discord.Embed,
    text: str,
    *,
    icon_url: Optional[str] = None,
) -> None:
    text = _safe_footer(text)

    if icon_url:
        embed.set_footer(text=text, icon_url=str(icon_url))
    else:
        embed.set_footer(text=text)


def _add_optional_field(
    embed: discord.Embed,
    *,
    name: str,
    value: Any,
    inline: bool = False,
) -> None:
    text = _text(value)

    if not text:
        return

    embed.add_field(
        name=_safe_field_name(name),
        value=_safe_field_value(text),
        inline=inline,
    )


def _metadata_lines(
    *,
    asked_at: Any,
    answered_at: Any = None,
    edited_at: Any = None,
    answer_edited_at: Any = None,
) -> str:
    lines = [f"**Asked:** {format_timestamp(asked_at)}"]

    if answered_at:
        lines.append(f"**Answered:** {format_timestamp(answered_at)}")

    if edited_at:
        lines.append(f"**Question edited:** {format_timestamp(edited_at)}")

    if answer_edited_at:
        lines.append(f"**Answer edited:** {format_timestamp(answer_edited_at)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public question / answer embeds
# ---------------------------------------------------------------------------


def build_pending_question_embed(
    record: Mapping[str, Any],
    *,
    author_label: Optional[str] = None,
    source_label: Optional[str] = None,
    source_jump_url: Optional[str] = None,
    author_edit_deadline: Optional[int] = None,
    show_author: bool = True,
) -> discord.Embed:
    """Build the live pending-question embed.

    This function never includes revision history or staff-only audit details.
    Editing the existing message in place therefore leaves all native votes
    attached to the same Fluxer message.
    """

    number = _record_number(record)
    content = _text(record.get("content"))

    embed = discord.Embed(
        title=f"❓ {question_label(number)}",
        description=content,
        colour=discord.Colour(QUESTION_COLOUR),
    )

    if show_author and author_label:
        embed.add_field(
            name="Asked by",
            value=_safe_field_value(author_label),
            inline=True,
        )

    embed.add_field(
        name="Submitted",
        value=_safe_field_value(format_timestamp(record.get("created_at"))),
        inline=True,
    )

    if record.get("edited_at"):
        embed.add_field(
            name="Last edited",
            value=_safe_field_value(format_timestamp(record.get("edited_at"))),
            inline=False,
        )

    source_parts: List[str] = []
    if source_label:
        source_parts.append(str(source_label))
    if source_jump_url:
        source_parts.append(f"[View original message]({source_jump_url})")

    if source_parts:
        embed.add_field(
            name="Source",
            value=_safe_field_value(" • ".join(source_parts)),
            inline=False,
        )

    footer = (
        f"Vote with {UPVOTE_EMOJI} or {DOWNVOTE_EMOJI} "
        "to indicate community interest"
    )

    if author_edit_deadline:
        footer += (
            " • Author edits until "
            f"{format_timestamp(author_edit_deadline)}"
        )

    _set_footer(embed, footer)
    return embed


def build_answer_embeds(
    record: Mapping[str, Any],
    *,
    author_label: Optional[str] = None,
    answered_by_label: Optional[str] = None,
    source_jump_url: Optional[str] = None,
) -> List[discord.Embed]:
    """Build the permanent answered Q&A card.

    One embed is returned when the complete Q&A safely fits. If the question
    and answer are too large for one embed, two matched embeds are returned.
    The caller should send them in the same Fluxer message using ``embeds=``.
    """

    number = _record_number(record)
    question = _text(record.get("content"))
    answer = _answer(record)
    answer_text = _text(answer.get("content"))
    votes = _votes(record)

    combined_size = len(question) + len(answer_text)

    if (
        combined_size <= COMBINED_QA_TEXT_LIMIT
        and len(question) <= EMBED_FIELD_VALUE_LIMIT
    ):
        return [
            _build_compact_answer_embed(
                record,
                author_label=author_label,
                answered_by_label=answered_by_label,
                source_jump_url=source_jump_url,
            )
        ]

    return _build_split_answer_embeds(
        record,
        author_label=author_label,
        answered_by_label=answered_by_label,
        source_jump_url=source_jump_url,
        votes=votes,
    )


def _build_compact_answer_embed(
    record: Mapping[str, Any],
    *,
    author_label: Optional[str],
    answered_by_label: Optional[str],
    source_jump_url: Optional[str],
) -> discord.Embed:
    number = _record_number(record)
    question = _text(record.get("content"))
    answer = _answer(record)
    answer_text = _text(answer.get("content"))
    votes = _votes(record)

    # Description keeps the answer Markdown intact while the question is placed
    # in a field. This usually gives the answer the largest available space.
    embed = discord.Embed(
        title=f"✅ {question_label(number)}",
        description=answer_text,
        colour=discord.Colour(ANSWER_COLOUR),
    )

    # A field is limited to 1024 chars, so a compact embed is only valid when
    # the question can fit there. If it cannot, use the split layout.
    if len(question) > EMBED_FIELD_VALUE_LIMIT:
        return _build_split_answer_embeds(
            record,
            author_label=author_label,
            answered_by_label=answered_by_label,
            source_jump_url=source_jump_url,
            votes=votes,
        )[0]

    embed.add_field(
        name="Question",
        value=question,
        inline=False,
    )

    if votes:
        embed.add_field(
            name="Community interest when answered",
            value=_safe_field_value(_vote_line(votes)),
            inline=False,
        )

    people: List[str] = []
    if author_label:
        people.append(f"**Asked by:** {author_label}")
    if answered_by_label:
        people.append(f"**Answered by:** {answered_by_label}")

    if people:
        embed.add_field(
            name="People",
            value=_safe_field_value("\n".join(people)),
            inline=False,
        )

    metadata = _metadata_lines(
        asked_at=record.get("created_at"),
        answered_at=answer.get("created_at"),
        edited_at=record.get("edited_at"),
        answer_edited_at=answer.get("edited_at"),
    )
    embed.add_field(
        name="Timeline",
        value=_safe_field_value(metadata),
        inline=False,
    )

    links: List[str] = []
    if source_jump_url:
        links.append(f"[Original message]({source_jump_url})")

    if links:
        embed.add_field(
            name="Links",
            value=_safe_field_value(" • ".join(links)),
            inline=False,
        )

    _set_footer(
        embed,
        f"{question_label(number)} • Community votes were snapshotted when answered",
    )

    return embed


def _build_split_answer_embeds(
    record: Mapping[str, Any],
    *,
    author_label: Optional[str],
    answered_by_label: Optional[str],
    source_jump_url: Optional[str],
    votes: Optional[Mapping[str, Any]] = None,
) -> List[discord.Embed]:
    number = _record_number(record)
    question = _text(record.get("content"))
    answer = _answer(record)
    answer_text = _text(answer.get("content"))
    vote_data = dict(votes or _votes(record))

    question_embed = discord.Embed(
        title=f"❓ {question_label(number)}",
        description=question,
        colour=discord.Colour(QUESTION_COLOUR),
    )

    if author_label:
        question_embed.add_field(
            name="Asked by",
            value=_safe_field_value(author_label),
            inline=True,
        )

    question_embed.add_field(
        name="Asked",
        value=_safe_field_value(format_timestamp(record.get("created_at"))),
        inline=True,
    )

    if record.get("edited_at"):
        question_embed.add_field(
            name="Question last edited",
            value=_safe_field_value(format_timestamp(record.get("edited_at"))),
            inline=False,
        )

    if source_jump_url:
        question_embed.add_field(
            name="Source",
            value=f"[View original message]({source_jump_url})",
            inline=False,
        )

    _set_footer(
        question_embed,
        f"{question_label(number)} • Answer follows below",
    )

    answer_embed = discord.Embed(
        title="✅ Answer",
        description=answer_text,
        colour=discord.Colour(ANSWER_COLOUR),
    )

    if answered_by_label:
        answer_embed.add_field(
            name="Answered by",
            value=_safe_field_value(answered_by_label),
            inline=True,
        )

    answer_embed.add_field(
        name="Answered",
        value=_safe_field_value(format_timestamp(answer.get("created_at"))),
        inline=True,
    )

    if answer.get("edited_at"):
        answer_embed.add_field(
            name="Answer last edited",
            value=_safe_field_value(format_timestamp(answer.get("edited_at"))),
            inline=False,
        )

    if vote_data:
        answer_embed.add_field(
            name="Community interest when answered",
            value=_safe_field_value(_vote_line(vote_data)),
            inline=False,
        )

    _set_footer(
        answer_embed,
        f"{question_label(number)} • Community votes were snapshotted when answered",
    )

    return [question_embed, answer_embed]


def build_removed_question_embed(
    record: Mapping[str, Any],
    *,
    removed_by_label: Optional[str] = None,
) -> discord.Embed:
    """Build a staff-facing representation of a soft-removed question."""

    number = _record_number(record)
    removal = record.get("removal")
    removal = dict(removal) if isinstance(removal, Mapping) else {}

    embed = discord.Embed(
        title=f"🗑️ Removed {question_label(number)}",
        description=_text(record.get("content")),
        colour=discord.Colour(REMOVED_COLOUR),
    )

    if removed_by_label:
        embed.add_field(
            name="Removed by",
            value=_safe_field_value(removed_by_label),
            inline=True,
        )

    embed.add_field(
        name="Removed",
        value=_safe_field_value(format_timestamp(removal.get("created_at"))),
        inline=True,
    )

    _add_optional_field(
        embed,
        name="Reason",
        value=removal.get("reason"),
        inline=False,
    )

    _set_footer(embed, "Soft-removed record retained in FluxQuestions storage")
    return embed


# ---------------------------------------------------------------------------
# Question lookup / history presentation
# ---------------------------------------------------------------------------


def build_question_info_embed(
    record: Mapping[str, Any],
    *,
    author_label: Optional[str] = None,
    submitted_by_label: Optional[str] = None,
    pending_jump_url: Optional[str] = None,
    answer_jump_url: Optional[str] = None,
    source_jump_url: Optional[str] = None,
) -> discord.Embed:
    """Build a staff/operator lookup card for any question state."""

    number = _record_number(record)
    status = _status(record)

    if status == "answered":
        colour = ANSWER_COLOUR
        status_display = "✅ Answered"
    elif status == "removed":
        colour = REMOVED_COLOUR
        status_display = "🗑️ Removed"
    else:
        colour = QUESTION_COLOUR
        status_display = "⏳ Pending"

    embed = discord.Embed(
        title=question_label(number),
        description=_text(record.get("content")),
        colour=discord.Colour(colour),
    )

    embed.add_field(
        name="Status",
        value=status_display,
        inline=True,
    )
    embed.add_field(
        name="Created",
        value=_safe_field_value(format_timestamp(record.get("created_at"))),
        inline=True,
    )
    embed.add_field(
        name="Question revisions",
        value=str(max(1, _revision_count(record))),
        inline=True,
    )

    if author_label:
        embed.add_field(
            name="Author",
            value=_safe_field_value(author_label),
            inline=True,
        )

    if submitted_by_label:
        embed.add_field(
            name="Submitted by",
            value=_safe_field_value(submitted_by_label),
            inline=True,
        )

    if record.get("edited_at"):
        embed.add_field(
            name="Last question edit",
            value=_safe_field_value(format_timestamp(record.get("edited_at"))),
            inline=False,
        )

    answer = _answer(record)
    if status == "answered" and answer:
        embed.add_field(
            name="Answer revisions",
            value=str(max(1, _answer_revision_count(record))),
            inline=True,
        )
        embed.add_field(
            name="Answered",
            value=_safe_field_value(format_timestamp(answer.get("created_at"))),
            inline=True,
        )

    links: List[str] = []
    if source_jump_url:
        links.append(f"[Source]({source_jump_url})")
    if pending_jump_url:
        links.append(f"[Pending message]({pending_jump_url})")
    if answer_jump_url:
        links.append(f"[Answer]({answer_jump_url})")

    if links:
        embed.add_field(
            name="Messages",
            value=_safe_field_value(" • ".join(links)),
            inline=False,
        )

    operation = record.get("operation")
    if isinstance(operation, Mapping):
        operation_type = _text(operation.get("type")) or "unknown"
        embed.add_field(
            name="⚠️ Incomplete operation",
            value=_safe_field_value(
                f"`{operation_type}` started "
                f"{format_timestamp(operation.get('started_at'))}"
            ),
            inline=False,
        )

    _set_footer(embed, f"Permanent record • {question_label(number)}")
    return embed


def build_revision_history_embed(
    record: Mapping[str, Any],
    *,
    answer_history: bool = False,
    page: int = 1,
    page_size: int = 5,
    editor_labels: Optional[Mapping[int, str]] = None,
) -> discord.Embed:
    """Build a paginated question- or answer-revision history embed."""

    number = _record_number(record)
    editor_labels = editor_labels or {}

    if answer_history:
        answer = _answer(record)
        revisions = answer.get("revisions")
        revisions = revisions if isinstance(revisions, list) else []
        title = f"Answer History — {question_label(number)}"
        colour = ANSWER_COLOUR
    else:
        revisions = record.get("revisions")
        revisions = revisions if isinstance(revisions, list) else []
        title = f"Question History — {question_label(number)}"
        colour = EDIT_COLOUR

    page_size = max(1, min(int(page_size or 5), 10))
    total = len(revisions)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page or 1), total_pages))

    start = (page - 1) * page_size
    selected = revisions[start : start + page_size]

    embed = discord.Embed(
        title=title,
        colour=discord.Colour(colour),
    )

    if not selected:
        embed.description = "No revision history is stored."
    else:
        for revision in selected:
            revision_number = positive_int(revision.get("revision")) or 0
            editor_id = positive_int(revision.get("editor_id"))
            editor = (
                editor_labels.get(editor_id, f"User `{editor_id}`")
                if editor_id
                else "Unknown user"
            )
            kind = _text(revision.get("kind")) or "edit"
            content = _text(revision.get("content"))

            heading = f"Revision {revision_number} • {kind.replace('_', ' ').title()}"

            details = [
                f"**Editor:** {editor}",
                f"**Time:** {format_timestamp(revision.get('created_at'))}",
            ]

            reverted_from = positive_int(revision.get("reverted_from_revision"))
            if reverted_from:
                details.append(
                    f"**Reverted from revision:** {reverted_from}"
                )

            preview = truncate_middle(content, 700)
            value = "\n".join(details) + "\n\n" + raw_markdown_codeblock(preview)

            embed.add_field(
                name=_safe_field_name(heading),
                value=_safe_field_value(value),
                inline=False,
            )

    _set_footer(
        embed,
        f"Page {page}/{total_pages} • {total} revision(s) • {question_label(number)}",
    )
    return embed


# ---------------------------------------------------------------------------
# DM workflow embeds
# ---------------------------------------------------------------------------


def build_author_edit_dm_embeds(
    record: Mapping[str, Any],
    *,
    edit_deadline: int,
) -> List[discord.Embed]:
    """Build the DM cards for the timed author edit workflow.

    The instructions and raw Markdown are deliberately separated. This lets a
    maximum-length question remain fully copyable without pushing instructions
    over the embed description limit. If an extreme backtick sequence makes a
    single fenced block too large, the raw text is split across consecutive
    fenced embeds without discarding content.
    """

    number = _record_number(record)
    content = _text(record.get("content"))

    instructions = discord.Embed(
        title=f"✏️ Editing {question_label(number)}",
        description=(
            "Copy the raw Markdown shown below, make your changes, then send "
            "the corrected version back to me in this DM."
        ),
        colour=discord.Colour(EDIT_COLOUR),
    )
    instructions.add_field(
        name="Edit deadline",
        value=_safe_field_value(format_timestamp(edit_deadline)),
        inline=False,
    )
    _set_footer(
        instructions,
        "Your next DM message will be treated as the replacement question",
    )

    raw_embeds: List[discord.Embed] = []
    remaining = content

    # Most questions fit in one 4,096-character description. The loop exists
    # for pathological content containing unusually long backtick runs.
    while remaining:
        chunk_size = min(len(remaining), 3950)
        chunk = remaining[:chunk_size]
        block = raw_markdown_codeblock(chunk)

        while len(block) > EMBED_DESCRIPTION_LIMIT and chunk_size > 1:
            chunk_size = max(1, chunk_size - 100)
            chunk = remaining[:chunk_size]
            block = raw_markdown_codeblock(chunk)

        if len(block) > EMBED_DESCRIPTION_LIMIT:
            # A sequence made entirely of thousands of backticks cannot be
            # safely fenced within one embed. Preserve it verbatim as plain
            # preformatted-looking content rather than deleting any text.
            block = chunk

        raw_embed = discord.Embed(
            title="Current question — raw Markdown",
            description=block,
            colour=discord.Colour(EDIT_COLOUR),
        )
        raw_embeds.append(raw_embed)
        remaining = remaining[chunk_size:]

    if not raw_embeds:
        raw_embeds.append(
            discord.Embed(
                title="Current question — raw Markdown",
                description=raw_markdown_codeblock(""),
                colour=discord.Colour(EDIT_COLOUR),
            )
        )

    total_parts = len(raw_embeds)
    for index, raw_embed in enumerate(raw_embeds, start=1):
        suffix = f" • Part {index}/{total_parts}" if total_parts > 1 else ""
        _set_footer(
            raw_embed,
            f"Copy this text to edit {question_label(number)}{suffix}",
        )

    return [instructions, *raw_embeds]


def build_author_edit_success_embed(
    record: Mapping[str, Any],
) -> discord.Embed:
    """Build the DM confirmation shown after a successful author edit."""

    number = _record_number(record)

    embed = discord.Embed(
        title=f"✅ {question_label(number)} Updated",
        description=_text(record.get("content")),
        colour=discord.Colour(ANSWER_COLOUR),
    )

    embed.add_field(
        name="Updated",
        value=_safe_field_value(format_timestamp(record.get("edited_at"))),
        inline=False,
    )

    _set_footer(embed, "The pending question was edited in place; votes were preserved")
    return embed


# ---------------------------------------------------------------------------
# Settings / statistics
# ---------------------------------------------------------------------------


def build_settings_embed(
    *,
    questions_channel: str,
    answers_channel: str,
    log_channel: str,
    question_emoji: str,
    submitter_roles: str,
    editor_roles: str,
    operator_roles: str,
    source_channels: str,
    author_edit_window: str,
    submitted: int,
    pending: int,
    answered: int,
    removed: int,
    next_number: int,
    version: str,
) -> discord.Embed:
    """Build the guild's main FluxQuestions settings/status embed."""

    embed = discord.Embed(
        title="Flux Questions Settings",
        colour=discord.Colour(QUESTION_COLOUR),
    )

    embed.add_field(
        name="Questions channel",
        value=_safe_field_value(questions_channel),
        inline=False,
    )
    embed.add_field(
        name="Answers channel",
        value=_safe_field_value(answers_channel),
        inline=False,
    )
    embed.add_field(
        name="Verbose log channel",
        value=_safe_field_value(log_channel),
        inline=False,
    )
    embed.add_field(
        name="Submission emoji",
        value=_safe_field_value(question_emoji),
        inline=True,
    )
    embed.add_field(
        name="Author edit window",
        value=_safe_field_value(author_edit_window),
        inline=True,
    )
    embed.add_field(
        name="Next question",
        value=f"#{max(1, int(next_number))}",
        inline=True,
    )

    embed.add_field(
        name="Submitter roles",
        value=_safe_field_value(submitter_roles or "None"),
        inline=False,
    )
    embed.add_field(
        name="Editor roles",
        value=_safe_field_value(editor_roles or "None"),
        inline=False,
    )
    embed.add_field(
        name="Operator roles",
        value=_safe_field_value(operator_roles or "None"),
        inline=False,
    )
    embed.add_field(
        name="Allowed source channels",
        value=_safe_field_value(
            source_channels or "All channels where the bot can read messages"
        ),
        inline=False,
    )

    embed.add_field(
        name="Records",
        value=(
            f"Submitted: **{max(0, int(submitted))}**\n"
            f"Pending: **{max(0, int(pending))}**\n"
            f"Answered: **{max(0, int(answered))}**\n"
            f"Removed: **{max(0, int(removed))}**"
        ),
        inline=False,
    )

    _set_footer(embed, f"Flux Questions v{version}")
    return embed


# ---------------------------------------------------------------------------
# Human-readable audit logging
# ---------------------------------------------------------------------------


def build_audit_embed(
    *,
    title: str,
    actor_label: Optional[str],
    occurred_at: Any,
    description: Optional[str] = None,
    question_number: Optional[int] = None,
    fields: Optional[Sequence[Mapping[str, Any]]] = None,
    jump_url: Optional[str] = None,
    colour: int = LOG_COLOUR,
) -> discord.Embed:
    """Build a generic structured staff audit entry."""

    embed = discord.Embed(
        title=_text(title) or "Flux Questions Log",
        description=(
            truncate_middle(description, MAX_LOG_TEXT_LENGTH)
            if description
            else None
        ),
        colour=discord.Colour(colour),
    )

    if question_number:
        embed.add_field(
            name="Question",
            value=question_label(question_number),
            inline=True,
        )

    if actor_label:
        embed.add_field(
            name="Actor",
            value=_safe_field_value(actor_label),
            inline=True,
        )

    embed.add_field(
        name="Time",
        value=_safe_field_value(format_timestamp(occurred_at)),
        inline=False,
    )

    for field in fields or []:
        name = _text(field.get("name"))
        value = field.get("value")

        if not name or value is None:
            continue

        embed.add_field(
            name=_safe_field_name(name),
            value=_safe_field_value(value),
            inline=bool(field.get("inline", False)),
        )

    if jump_url:
        embed.add_field(
            name="Message",
            value=f"[Jump to message]({jump_url})",
            inline=False,
        )

    _set_footer(embed, "Flux Questions verbose audit log")
    return embed


def build_submission_log_embed(
    record: Mapping[str, Any],
    *,
    author_label: str,
    submitter_label: str,
    source_label: Optional[str] = None,
    source_jump_url: Optional[str] = None,
) -> discord.Embed:
    """Build the verbose log entry for a newly submitted question."""

    number = _record_number(record)

    fields: List[Dict[str, Any]] = [
        {"name": "Author", "value": author_label, "inline": True},
        {"name": "Submitted by", "value": submitter_label, "inline": True},
    ]

    if source_label:
        fields.append(
            {"name": "Source", "value": source_label, "inline": False}
        )

    fields.append(
        {
            "name": "Question text",
            "value": raw_markdown_codeblock(
                truncate_middle(
                    _text(record.get("content")),
                    760,
                )
            ),
            "inline": False,
        }
    )

    return build_audit_embed(
        title=f"📝 Question Submitted — #{number}",
        actor_label=submitter_label,
        occurred_at=record.get("created_at"),
        question_number=number,
        fields=fields,
        jump_url=source_jump_url,
        colour=QUESTION_COLOUR,
    )


def build_question_edit_log_embed(
    *,
    record: Mapping[str, Any],
    actor_label: str,
    before: str,
    after: str,
    edit_kind: str,
) -> discord.Embed:
    """Build a before/after audit entry for question edits and reverts."""

    number = _record_number(record)

    return build_audit_embed(
        title=f"✏️ Question Edited — #{number}",
        actor_label=actor_label,
        occurred_at=record.get("edited_at"),
        question_number=number,
        fields=[
            {
                "name": "Edit type",
                "value": _text(edit_kind).replace("_", " ").title(),
                "inline": False,
            },
            {
                "name": "Previous question",
                "value": raw_markdown_codeblock(
                    truncate_middle(before, 740)
                ),
                "inline": False,
            },
            {
                "name": "New question",
                "value": raw_markdown_codeblock(
                    truncate_middle(after, 740)
                ),
                "inline": False,
            },
        ],
        colour=EDIT_COLOUR,
    )


def build_answer_log_embed(
    record: Mapping[str, Any],
    *,
    operator_label: str,
    answer_jump_url: Optional[str] = None,
) -> discord.Embed:
    """Build the verbose log entry for a successfully answered question."""

    number = _record_number(record)
    answer = _answer(record)
    votes = _votes(record)

    fields: List[Dict[str, Any]] = [
        {
            "name": "Votes snapshotted",
            "value": _vote_line(votes) if votes else "No vote snapshot",
            "inline": False,
        },
        {
            "name": "Answer",
            "value": raw_markdown_codeblock(
                truncate_middle(
                    _text(answer.get("content")),
                    760,
                )
            ),
            "inline": False,
        },
    ]

    return build_audit_embed(
        title=f"✅ Question Answered — #{number}",
        actor_label=operator_label,
        occurred_at=answer.get("created_at"),
        question_number=number,
        fields=fields,
        jump_url=answer_jump_url,
        colour=ANSWER_COLOUR,
    )


def build_answer_edit_log_embed(
    *,
    record: Mapping[str, Any],
    actor_label: str,
    before: str,
    after: str,
) -> discord.Embed:
    """Build a before/after audit entry for an answer edit."""

    number = _record_number(record)
    answer = _answer(record)

    return build_audit_embed(
        title=f"✏️ Answer Edited — #{number}",
        actor_label=actor_label,
        occurred_at=answer.get("edited_at"),
        question_number=number,
        fields=[
            {
                "name": "Previous answer",
                "value": raw_markdown_codeblock(
                    truncate_middle(before, 740)
                ),
                "inline": False,
            },
            {
                "name": "New answer",
                "value": raw_markdown_codeblock(
                    truncate_middle(after, 740)
                ),
                "inline": False,
            },
        ],
        colour=EDIT_COLOUR,
    )


def build_removal_log_embed(
    record: Mapping[str, Any],
    *,
    actor_label: str,
) -> discord.Embed:
    """Build a verbose log entry for a soft-removed pending question."""

    number = _record_number(record)
    removal = record.get("removal")
    removal = dict(removal) if isinstance(removal, Mapping) else {}

    fields: List[Dict[str, Any]] = [
        {
            "name": "Question text",
            "value": raw_markdown_codeblock(
                truncate_middle(
                    _text(record.get("content")),
                    760,
                )
            ),
            "inline": False,
        }
    ]

    if removal.get("reason"):
        fields.append(
            {
                "name": "Reason",
                "value": removal.get("reason"),
                "inline": False,
            }
        )

    return build_audit_embed(
        title=f"🗑️ Question Removed — #{number}",
        actor_label=actor_label,
        occurred_at=removal.get("created_at"),
        question_number=number,
        fields=fields,
        colour=REMOVED_COLOUR,
    )


def build_recovery_log_embed(
    *,
    title: str,
    question_number: int,
    actor_label: Optional[str],
    occurred_at: Any,
    details: str,
    jump_url: Optional[str] = None,
) -> discord.Embed:
    """Build a log entry for resend/recovery/startup reconciliation."""

    return build_audit_embed(
        title=title,
        actor_label=actor_label,
        occurred_at=occurred_at,
        description=details,
        question_number=question_number,
        jump_url=jump_url,
        colour=LOG_COLOUR,
    )


def build_error_log_embed(
    *,
    title: str,
    occurred_at: Any,
    details: str,
    question_number: Optional[int] = None,
    actor_label: Optional[str] = None,
) -> discord.Embed:
    """Build the readable staff-side counterpart to a Python exception log."""

    return build_audit_embed(
        title=f"⚠️ {title}",
        actor_label=actor_label,
        occurred_at=occurred_at,
        description=details,
        question_number=question_number,
        colour=ERROR_COLOUR,
    )


def build_config_change_log_embed(
    *,
    actor_label: str,
    occurred_at: Any,
    setting: str,
    before: Any,
    after: Any,
) -> discord.Embed:
    """Build a readable audit entry for guild configuration changes."""

    return build_audit_embed(
        title="⚙️ Flux Questions Configuration Changed",
        actor_label=actor_label,
        occurred_at=occurred_at,
        fields=[
            {
                "name": "Setting",
                "value": setting,
                "inline": False,
            },
            {
                "name": "Previous value",
                "value": _text(before) or "Not configured",
                "inline": False,
            },
            {
                "name": "New value",
                "value": _text(after) or "Not configured",
                "inline": False,
            },
        ],
        colour=LOG_COLOUR,
    )
