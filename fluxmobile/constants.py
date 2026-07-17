"""
Flux Mobile - constants.py

Project-wide constants.

This module contains immutable configuration only.
"""

from typing import Final

# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

COG_NAME: Final = "FluxMobile"
COG_VERSION: Final = "1.0.0"

AUTHOR: Final = "Five"
AUTHOR_TAG: Final = "Five#6446"

COMMUNITY: Final = "AEGIS"

# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

GITHUB_OWNER: Final = "fluxerapp"
GITHUB_REPOSITORY: Final = "flutter_client"

GITHUB_BASE_URL: Final = "https://github.com"
GITHUB_API_BASE: Final = "https://api.github.com"

GITHUB_REPOSITORY_URL: Final = (
    f"{GITHUB_BASE_URL}/{GITHUB_OWNER}/{GITHUB_REPOSITORY}"
)

#
# Human-readable releases page
#
RELEASES_PAGE: Final = (
    f"{GITHUB_REPOSITORY_URL}/releases"
)

#
# GitHub REST API
#
# NOTE:
# Do NOT use /releases/latest.
# Fluxer currently exposes releases through the collection endpoint,
# where the newest entry is payload[0].
#
RELEASES_API: Final = (
    f"{GITHUB_API_BASE}/repos/"
    f"{GITHUB_OWNER}/"
    f"{GITHUB_REPOSITORY}/releases"
)

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

HTTP_TIMEOUT: Final = 15
MAX_HTTP_RETRIES: Final = 3

HTTP_USER_AGENT: Final = (
    f"{COG_NAME}/{COG_VERSION}"
)

# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

DEFAULT_CHECK_INTERVAL_MINUTES: Final = 30

# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------

DEFAULT_EMBED_COLOUR: Final = 0x5865F2

EMBED_DESCRIPTION_LIMIT: Final = 4000

EMBED_TITLE: Final[str] = "📱 Fluxer Mobile"

EMBED_FOOTER: Final = (
    "Flux Mobile • AEGIS"
)

# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

APK_EXTENSIONS: Final = (
    ".apk",
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGER_NAME: Final = "red.aegis.fluxmobile"

DEFAULT_VERBOSE_LOGGING: Final = False

# ---------------------------------------------------------------------------
# Config Defaults
# ---------------------------------------------------------------------------

DEFAULT_GUILD = {
    "enabled": False,
    "announcement_channel": None,
    "log_channel": None,
    "role": None,
    "interval": DEFAULT_CHECK_INTERVAL_MINUTES,
    "last_release_id": None,
    "last_release_tag": None,
    "last_check": None,
    "verbose": DEFAULT_VERBOSE_LOGGING,
}

# ---------------------------------------------------------------------------
# GitHub JSON
# ---------------------------------------------------------------------------

JSON_ID: Final = "id"
JSON_TAG: Final = "tag_name"
JSON_NAME: Final = "name"
JSON_BODY: Final = "body"
JSON_URL: Final = "html_url"
JSON_ASSETS: Final = "assets"
JSON_PUBLISHED: Final = "published_at"
JSON_BROWSER_DOWNLOAD: Final = "browser_download_url"
JSON_FILENAME: Final = "name"

# ---------------------------------------------------------------------------
# Logging Messages
# ---------------------------------------------------------------------------

LOG_CHECKING: Final = "Checking GitHub..."
LOG_NO_RELEASE: Final = "No new release."
LOG_NEW_RELEASE: Final = "New release detected."
LOG_ANNOUNCED: Final = "Release announced."
LOG_FAILED: Final = "Announcement failed."
