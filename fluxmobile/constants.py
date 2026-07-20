"""
Flux Mobile - constants.py

Project-wide immutable configuration.
"""

from typing import Final

# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

COG_NAME: Final = "FluxMobile"
COG_VERSION: Final = "1.1.0"

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

RELEASES_PAGE: Final = f"{GITHUB_REPOSITORY_URL}/releases"

RELEASES_API: Final = (
    f"{GITHUB_API_BASE}/repos/"
    f"{GITHUB_OWNER}/"
    f"{GITHUB_REPOSITORY}/releases"
)

# The collection endpoint is intentional because /releases/latest excludes
# prereleases.
GITHUB_API_VERSION: Final = "2022-11-28"
RELEASES_PER_PAGE: Final = 100

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

HTTP_TIMEOUT: Final = 15
MAX_HTTP_RETRIES: Final = 3

HTTP_RETRY_BASE_SECONDS: Final = 2
HTTP_RETRY_MAX_SECONDS: Final = 30

RETRYABLE_HTTP_STATUSES: Final = (
    408,
    429,
    500,
    502,
    503,
    504,
)

HTTP_USER_AGENT: Final = f"{COG_NAME}/{COG_VERSION}"

# A short shared cache prevents multiple guilds from making identical API
# requests while still keeping detection timely.
GITHUB_CACHE_TTL_SECONDS: Final = 300

# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

DEFAULT_CHECK_INTERVAL_MINUTES: Final = 30
MIN_CHECK_INTERVAL_MINUTES: Final = 5

# The task is now a scheduler. Each guild's configured interval is checked
# independently.
MONITOR_TICK_SECONDS: Final = 60

# Failed checks and releases waiting for APK uploads are retried sooner than
# the normal configured interval.
ERROR_RETRY_INTERVAL_MINUTES: Final = 5

# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------

DEFAULT_EMBED_COLOUR: Final = 0x5865F2
EMBED_DESCRIPTION_LIMIT: Final = 4000

EMBED_TITLE: Final[str] = "📱 Fluxer Mobile"
EMBED_FOOTER: Final = "Flux Mobile • AEGIS"

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
    # Existing settings -- do not rename these.
    "enabled": False,
    "announcement_channel": None,
    "log_channel": None,
    "role": None,
    "interval": DEFAULT_CHECK_INTERVAL_MINUTES,
    "last_release_id": None,
    "last_release_tag": None,
    "last_check": None,
    "verbose": DEFAULT_VERBOSE_LOGGING,

    # New non-destructive defaults.
    "include_prereleases": True,
    "require_apk": True,
    "last_release_published": None,
    "last_check_result": None,
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
