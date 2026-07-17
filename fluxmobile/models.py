"""
Flux Mobile - models.py

Strongly typed models used throughout the Flux Mobile project.

ANTI-DRIFT

This module MUST:
- Define immutable data models.
- Convert GitHub JSON into Python objects.

This module MUST NEVER:
- Perform HTTP requests.
- Import Discord.
- Import Red.
- Access Config.
- Send messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    """Represents a downloadable GitHub Release asset."""

    name: str
    download_url: str
    size: int = 0
    content_type: str | None = None

    @property
    def is_apk(self) -> bool:
        """
        True if this asset appears to be an Android APK.

        Detection uses both filename and MIME type for resilience.
        """

        return (
            self.name.lower().endswith(".apk")
            or self.content_type == "application/vnd.android.package-archive"
        )


@dataclass(frozen=True, slots=True)
class Release:
    """Represents a GitHub Release."""

    id: int
    tag: str
    title: str
    body: str
    url: str
    published: datetime

    assets: tuple[ReleaseAsset, ...] = field(default_factory=tuple)

    prerelease: bool = False
    draft: bool = False

    @property
    def apk(self) -> ReleaseAsset | None:
        """
        Return the preferred APK.

        Preference order:

        1. Universal APK
        2. First APK
        """

        for asset in self.assets:
            if (
                asset.is_apk
                and "universal" in asset.name.lower()
            ):
                return asset

        for asset in self.assets:
            if asset.is_apk:
                return asset

        return None

    @property
    def has_apk(self) -> bool:
        """True if at least one APK exists."""
        return self.apk is not None

    @property
    def apk_count(self) -> int:
        """Return the number of APK assets."""
        return sum(asset.is_apk for asset in self.assets)

    @property
    def version(self) -> str:
        """
        Human-readable version.

        Prefer the Git tag, otherwise fall back to the release title.
        """
        return self.tag or self.title

    @property
    def release_type(self) -> str:
        """Return a user-friendly release type."""
        return "Prerelease" if self.prerelease else "Release"

    @property
    def release_notes(self) -> str:
        """
        Return release notes exactly as GitHub provides them.

        Fluxer supports GitHub-flavoured Markdown so formatting should
        be preserved exactly.
        """
        return self.body.strip()

    def asset_named(self, filename: str) -> ReleaseAsset | None:
        """
        Return an asset by filename.

        Returns None if not found.
        """

        for asset in self.assets:
            if asset.name == filename:
                return asset

        return None


def release_from_github(data: dict[str, Any]) -> Release:
    """
    Convert GitHub API JSON into a strongly typed Release object.
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
        data.get("published_at", data["created_at"]).replace("Z", "+00:00")
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
