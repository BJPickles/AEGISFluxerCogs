"""
===============================================================================
 Flux Mobile
===============================================================================

Developed for the AEGIS Community.

Author:
    Five (Five#6446)

Purpose
-------
Central location for immutable project constants.

This module intentionally contains NO executable code.

Every other module imports values from here to avoid duplicated strings,
magic numbers and inconsistent configuration.

===============================================================================

Development Status

✓ Stage 1 - Constants
□ Stage 2 - Models
□ Stage 3 - GitHub API
□ Stage 4 - Embeds
□ Stage 5 - Commands
□ Stage 6 - Background Monitor
□ Stage 7 - Diagnostics
□ Stage 8 - Production Ready

===============================================================================

ANTI-DRIFT CONTRACT

This module MUST ONLY contain constants.

This module MUST NEVER:

• Perform HTTP requests.
• Import Red.
• Import Discord.
• Import aiohttp.
• Read or write Config.
• Execute business logic.

If executable code is required,
it belongs in another module.

===============================================================================
"""

from typing import Final

# =============================================================================
# Project Information
# =============================================================================

COG_NAME: Final[str] = "FluxMobile"

COG_DESCRIPTION: Final[str] = (
    "Automatically announces new Fluxer Mobile releases."
)

COG_VERSION: Final[str] = "1.0.0"

AUTHOR: Final[str] = "Five"

AUTHOR_TAG: Final[str] = "Five#6446"

COMMUNITY: Final[str] = "AEGIS"

# =============================================================================
# GitHub Repository
#
# This cog intentionally monitors ONE repository.
#
# If support for multiple repositories is ever required,
# this module should be expanded rather than hardcoding elsewhere.
# =============================================================================

GITHUB_OWNER: Final[str] = "fluxerapp"

GITHUB_REPOSITORY: Final[str] = "flutter_client"

# =============================================================================
# GitHub URLs
# =============================================================================

GITHUB_BASE_URL: Final[str] = "https://github.com"

GITHUB_API_BASE: Final[str] = "https://api.github.com"

GITHUB_REPOSITORY_URL: Final[str] = (
    f"{GITHUB_BASE_URL}/{GITHUB_OWNER}/{GITHUB_REPOSITORY}"
)

LATEST_RELEASE_URL: Final[str] = (
    f"{GITHUB_REPOSITORY_URL}/releases/latest"
)

LATEST_RELEASE_API: Final[str] = (
    f"{GITHUB_API_BASE}/repos/"
    f"{GITHUB_OWNER}/"
    f"{GITHUB_REPOSITORY}/releases/latest"
)

# =============================================================================
# HTTP Configuration
# =============================================================================

HTTP_TIMEOUT: Final[int] = 15

HTTP_USER_AGENT: Final[str] = (
    f"{COG_NAME}/{COG_VERSION} "
    "(https://github.com/AEGIS)"
)

# =============================================================================
# Background Monitor
# =============================================================================

DEFAULT_CHECK_INTERVAL_MINUTES: Final[int] = 30

MAX_HTTP_RETRIES: Final[int] = 3

# =============================================================================
# Embed Configuration
#
# The embed colour follows this priority:
#
# 1. Guild override (future support)
# 2. Bot default embed colour
# 3. DEFAULT_EMBED_COLOUR
#
# =============================================================================

DEFAULT_EMBED_COLOUR: Final[int] = 0x5865F2

EMBED_DESCRIPTION_LIMIT: Final[int] = 4000

EMBED_TITLE: Final[str] = "📱 Fluxer Mobile Updated"

EMBED_FOOTER: Final[str] = (
    "Flux Mobile • Developed for the AEGIS Community"
)

# =============================================================================
# Asset Detection
#
# These file extensions are considered downloadable Android packages.
# =============================================================================

APK_EXTENSIONS: Final[tuple[str, ...]] = (
    ".apk",
)

# =============================================================================
# Logging
# =============================================================================

LOGGER_NAME: Final[str] = "red.aegis.fluxmobile"

LOG_PREFIX: Final[str] = "[FluxMobile]"

DEFAULT_VERBOSE_LOGGING: Final[bool] = False

# =============================================================================
# Config Defaults
#
# These values are used when registering Red Config.
#
# Keeping them here makes future migrations easier.
# =============================================================================

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

# =============================================================================
# GitHub JSON Keys
#
# Using constants instead of hardcoded strings prevents accidental typos.
# =============================================================================

JSON_ID: Final[str] = "id"

JSON_TAG: Final[str] = "tag_name"

JSON_NAME: Final[str] = "name"

JSON_BODY: Final[str] = "body"

JSON_URL: Final[str] = "html_url"

JSON_ASSETS: Final[str] = "assets"

JSON_PUBLISHED: Final[str] = "published_at"

JSON_BROWSER_DOWNLOAD: Final[str] = "browser_download_url"

JSON_FILENAME: Final[str] = "name"

# =============================================================================
# Diagnostic Messages
#
# Common log messages used throughout the project.
# =============================================================================

LOG_MONITOR_STARTED: Final[str] = "Background monitor started."

LOG_MONITOR_STOPPED: Final[str] = "Background monitor stopped."

LOG_CHECKING: Final[str] = "Checking GitHub for a new release..."

LOG_NO_RELEASE: Final[str] = "No new release detected."

LOG_NEW_RELEASE: Final[str] = "New release detected."

LOG_ANNOUNCED: Final[str] = "Release announcement sent."

LOG_FAILED: Final[str] = "Failed to announce release."

# =============================================================================
# End of File
#
# Progress
#
# ✓ Centralised all configuration.
# ✓ Removed magic values.
# ✓ Defined project-wide defaults.
# ✓ Prepared the project for strongly typed models.
#
# NEXT MODULE
#
# models.py
#
# Responsibility:
#
# Convert raw GitHub JSON into a strongly typed Release object.
#
# This allows the remainder of the project to work with Python objects
# instead of raw dictionaries.
#
# =============================================================================
