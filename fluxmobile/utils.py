"""
Flux Mobile - utils.py

Shared helper functions.
"""

from __future__ import annotations

import discord

from .constants import (
    DEFAULT_EMBED_COLOUR,
    EMBED_DESCRIPTION_LIMIT,
    EMBED_FOOTER,
    EMBED_TITLE,
)
from .models import Release


def truncate(text: str, limit: int = EMBED_DESCRIPTION_LIMIT) -> str:
    """
    Trim text to Discord's embed description limit.

    Preserves Markdown while ensuring the embed cannot exceed the
    maximum description length.
    """
    text = (text or "").strip()

    if len(text) <= limit:
        return text

    return text[: limit - 1] + "…"


async def resolve_embed_colour(
    ctx_or_bot,
    configured: int | None = None,
) -> discord.Colour:
    """
    Resolve the embed colour.

    Priority:
        1. Guild-configured override.
        2. Red's default embed colour.
        3. Project fallback colour.
    """

    if configured is not None:
        return discord.Colour(configured)

    if hasattr(ctx_or_bot, "embed_colour"):
        try:
            return await ctx_or_bot.embed_colour()
        except Exception:
            pass

    return discord.Colour(DEFAULT_EMBED_COLOUR)


def build_release_embed(
    release: Release,
    colour: discord.Colour,
) -> discord.Embed:
    """
    Build the announcement embed.

    ANTI-DRIFT

    This function ONLY formats Release objects.

    It MUST NOT:
    - Perform HTTP requests.
    - Read or write Config.
    - Send Discord messages.
    """

    embed = discord.Embed(
        title=f"{EMBED_TITLE} • {release.version}",
        url=release.url,
        colour=colour,
        description=truncate(release.release_notes),
    )

    embed.add_field(
        name="Version",
        value=release.version,
        inline=True,
    )

    embed.add_field(
        name="Type",
        value=release.release_type,
        inline=True,
    )

    embed.add_field(
        name="APK Files",
        value=str(release.apk_count),
        inline=True,
    )

    embed.add_field(
        name="Published",
        value=release.timestamp,
        inline=False,
    )

    #
    # ------------------------------------------------------------------
    # APK Downloads
    #
    # ANTI-DRIFT
    #
    # GitHub releases may contain many APKs.
    #
    # Discord limits:
    #   • 1024 characters per field
    #   • 25 fields per embed
    #
    # Automatically split APK links across multiple fields while
    # preserving every download link.
    # ------------------------------------------------------------------
    #

    apk_assets = [
        asset
        for asset in release.assets
        if asset.is_apk
    ]

    if not apk_assets:

        embed.add_field(
            name="Downloads",
            value="No APK asset was found in this release.",
            inline=False,
        )

    else:

        chunks: list[str] = []
        current = ""

        for asset in apk_assets:

            line = f"• [{asset.name}]({asset.download_url})\n"

            if len(current) + len(line) > 1024:
                chunks.append(current.rstrip())
                current = line
            else:
                current += line

        if current:
            chunks.append(current.rstrip())

        for index, chunk in enumerate(chunks):

            embed.add_field(
                name="Downloads" if index == 0 else "Downloads (continued)",
                value=chunk,
                inline=False,
            )

    embed.set_footer(
        text=EMBED_FOOTER,
    )

    return embed
