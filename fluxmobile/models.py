"""
===============================================================================
 Flux Mobile
===============================================================================

Developed for the AEGIS Community.

Author:
    Five (Five#6446)

Purpose
-------
Strongly typed data models used throughout the Flux Mobile project.

Rather than passing GitHub JSON dictionaries around the application,
we convert them into Python objects as early as possible.

This keeps the rest of the code clean, readable and type-safe.

===============================================================================

Development Status

✓ Stage 1 - Constants
✓ Stage 2 - Models
□ Stage 3 - GitHub API
□ Stage 4 - Embeds
□ Stage 5 - Commands
□ Stage 6 - Background Monitor
□ Stage 7 - Diagnostics
□ Stage 8 - Production Ready

===============================================================================

ANTI-DRIFT CONTRACT

This module MUST:

• Define data models.
• Validate incoming data.
• Remain immutable.

This module MUST NEVER:

• Perform HTTP requests.
• Import Red.
• Import Discord.
• Read or write configuration.
• Send messages.
• Contain business logic.

===============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# =============================================================================
# GitHub Asset
#
# Represents a downloadable file attached to a GitHub Release.
#
# Progress
# --------
# ✓ Strong typing.
# ✓ Immutable.
# ✓ Simple helper property.
# =============================================================================

@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    """Represents a GitHub Release asset."""

    name: str
    download_url: str
    size: int = 0
    content_type: str | None = None

    @property
    def is_apk(self) -> bool:
        """Return True if this asset appears to be an Android APK."""
        return self.name.lower().endswith(".apk")


# =============================================================================
# GitHub Release
#
# This is the primary object used throughout the project.
#
# Once created, every module should use Release objects rather than raw JSON.
#
# =============================================================================

@dataclass(frozen=True, slots=True)
class Release:
    """Represents the latest GitHub Release."""

    id: int
    tag: str
    title: str
    body: str
    url: str
    published: datetime

    assets: tuple[ReleaseAsset, ...] = field(default_factory=tuple)

    prerelease: bool = False

    draft: bool = False

    # -------------------------------------------------------------------------
    # Helper Properties
    # -------------------------------------------------------------------------

    @property
    def apk(self) -> ReleaseAsset | None:
        """
        Return the first APK asset.

        Returns
        -------
        ReleaseAsset | None
            APK asset if present.
        """
        for asset in self.assets:
            if asset.is_apk:
                return asset

        return None

    @property
    def has_apk(self) -> bool:
        """Convenience property."""
        return self.apk is not None

    @property
    def version(self) -> str:
        """
        Human-readable version.

        Falls back to title if tag is empty.
        """
        return self.tag or self.title

    @property
    def release_notes(self) -> str:
        """
        Return cleaned release notes.

        Future versions may perform Markdown cleanup here.

        Keeping this property centralised prevents formatting logic from
        spreading throughout the project.
        """
        return self.body.strip()


# =============================================================================
# Factory Functions
#
# These functions convert raw GitHub API responses into Release objects.
#
# Keeping conversion logic inside this module means github.py remains focused
# purely on HTTP communication.
# =============================================================================

def release_from_github(data: dict[str, Any]) -> Release:
    """
    Convert GitHub API JSON into a Release object.

    Parameters
    ----------
    data
        JSON response from GitHub.

    Returns
    -------
    Release

    Raises
    ------
    KeyError
        If mandatory GitHub fields are missing.

    ValueError
        If dates cannot be parsed.
    """

    assets: list[ReleaseAsset] = []

    for asset in data.get("assets", []):

        assets.append(
            ReleaseAsset(
                name=asset["name"],
                download_url=asset["browser_download_url"],
                size=asset.get("size", 0),
                content_type=asset.get("content_type"),
            )
        )

    published = datetime.fromisoformat(
        data["published_at"].replace("Z", "+00:00")
    )

    return Release(
        id=data["id"],
        tag=data.get("tag_name", ""),
        title=data.get("name") or data.get("tag_name", ""),
        body=data.get("body", ""),
        url=data["html_url"],
        published=published,
        assets=tuple(assets),
        prerelease=data.get("prerelease", False),
        draft=data.get("draft", False),
    )


# =============================================================================
# End of File
#
# Progress
#
# ✓ Created immutable Release model.
# ✓ Created immutable ReleaseAsset model.
# ✓ Centralised GitHub JSON conversion.
# ✓ Eliminated raw dictionaries from the remainder of the project.
#
# Design Achievement
# ------------------
#
# Every other module now works with Python objects instead of JSON.
#
# This significantly reduces runtime errors and improves readability.
#
# NEXT MODULE
#
# github.py
#
# Responsibilities
#
# • Create aiohttp session.
# • Query GitHub REST API.
# • Return Release objects.
# • Never interact with Discord.
# • Never access Red Config.
#
# =============================================================================
