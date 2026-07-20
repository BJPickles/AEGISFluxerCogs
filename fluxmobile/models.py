"""
Flux Mobile - models.py

Immutable release models and GitHub JSON conversion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
        """Whether this asset appears to be an Android APK."""

        mime_type = (
            (self.content_type or "")
            .partition(";")[0]
            .strip()
            .lower()
        )

        return (
            self.name.lower().endswith(".apk")
            or mime_type == "application/vnd.android.package-archive"
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
    def unix_timestamp(self) -> int:
        return int(self.published.timestamp())

    @property
    def timestamp(self) -> str:
        ts = self.unix_timestamp
        return f"<t:{ts}:F> (<t:{ts}:R>)"

    @property
    def apk(self) -> ReleaseAsset | None:
        """
        Return the preferred APK.

        Preference:
        1. Universal APK
        2. First available APK
        """

        for asset in self.assets:
            if asset.is_apk and "universal" in asset.name.lower():
                return asset

        for asset in self.assets:
            if asset.is_apk:
                return asset

        return None

    @property
    def has_apk(self) -> bool:
        return self.apk is not None

    @property
    def apk_count(self) -> int:
        return sum(1 for asset in self.assets if asset.is_apk)

    @property
    def version(self) -> str:
        return self.tag or self.title

    @property
    def release_type(self) -> str:
        if self.draft and self.prerelease:
            return "Draft prerelease"

        if self.draft:
            return "Draft"

        if self.prerelease:
            return "Prerelease"

        return "Release"

    @property
    def release_notes(self) -> str:
        return (self.body or "").strip()

    def asset_named(self, filename: str) -> ReleaseAsset | None:
        for asset in self.assets:
            if asset.name == filename:
                return asset

        return None


def _parse_github_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Release has no valid publication date.")

    parsed = datetime.fromisoformat(
        value.strip().replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def release_from_github(data: dict[str, Any]) -> Release:
    """Convert GitHub release JSON into a Release object."""

    if not isinstance(data, dict):
        raise ValueError("GitHub release entry was not an object.")

    try:
        release_id = int(data["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("GitHub release has no valid ID.") from exc

    release_url = str(data.get("html_url") or "").strip()

    if not release_url:
        raise ValueError(
            f"GitHub release {release_id} has no HTML URL."
        )

    published_value = (
        data.get("published_at")
        or data.get("created_at")
    )

    published = _parse_github_datetime(published_value)

    assets: list[ReleaseAsset] = []

    raw_assets = data.get("assets") or []

    if isinstance(raw_assets, list):
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, dict):
                continue

            name = str(raw_asset.get("name") or "").strip()
            download_url = str(
                raw_asset.get("browser_download_url") or ""
            ).strip()

            if not name or not download_url:
                continue

            try:
                size = int(raw_asset.get("size") or 0)
            except (TypeError, ValueError):
                size = 0

            content_type = raw_asset.get("content_type")

            if content_type is not None:
                content_type = str(content_type)

            assets.append(
                ReleaseAsset(
                    name=name,
                    download_url=download_url,
                    size=size,
                    content_type=content_type,
                )
            )

    tag = str(data.get("tag_name") or "").strip()
    title = str(data.get("name") or tag).strip()
    body = data.get("body") or ""

    if not isinstance(body, str):
        body = str(body)

    return Release(
        id=release_id,
        tag=tag,
        title=title,
        body=body,
        url=release_url,
        published=published,
        assets=tuple(assets),
        prerelease=bool(data.get("prerelease", False)),
        draft=bool(data.get("draft", False)),
    )
