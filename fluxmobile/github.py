"""
Flux Mobile - github.py

GitHub REST API client.

Responsibilities:
- HTTP communication
- Retries and API errors
- Rate-limit metadata
- Short response caching
- Conversion of JSON into Release objects
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import aiohttp

from .constants import (
    GITHUB_API_VERSION,
    GITHUB_CACHE_TTL_SECONDS,
    HTTP_RETRY_BASE_SECONDS,
    HTTP_RETRY_MAX_SECONDS,
    HTTP_TIMEOUT,
    HTTP_USER_AGENT,
    LOGGER_NAME,
    MAX_HTTP_RETRIES,
    RELEASES_API,
    RELEASES_PER_PAGE,
    RETRYABLE_HTTP_STATUSES,
)
from .models import Release, release_from_github

log = logging.getLogger(LOGGER_NAME)


class GitHubError(Exception):
    """Base GitHub exception."""


class GitHubAPIError(GitHubError):
    """Raised when GitHub returns an unexpected API response."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class ReleaseFeed:
    """A release collection and associated API metadata."""

    releases: tuple[Release, ...]
    fetched_at: datetime
    from_cache: bool = False
    rate_limit_limit: int | None = None
    rate_limit_remaining: int | None = None
    rate_limit_reset: int | None = None


class GitHubClient:
    """GitHub REST client for the Fluxer Mobile repository."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._request_lock = asyncio.Lock()

        self._cached_feed: ReleaseFeed | None = None
        self._cached_at: float | None = None

        self._last_feed: ReleaseFeed | None = None

    @property
    def is_ready(self) -> bool:
        return bool(
            self._session
            and not self._session.closed
        )

    @property
    def last_feed(self) -> ReleaseFeed | None:
        return self._last_feed

    async def start(self) -> None:
        """Create the HTTP session if required."""

        if self._session is not None and not self._session.closed:
            return

        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)

        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": HTTP_USER_AGENT,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
        )

    async def close(self) -> None:
        """Close the HTTP session."""

        if self._session and not self._session.closed:
            await self._session.close()

    def _cache_is_valid(self) -> bool:
        if self._cached_feed is None or self._cached_at is None:
            return False

        age = time.monotonic() - self._cached_at

        return age < GITHUB_CACHE_TTL_SECONDS

    @staticmethod
    def _header_int(
        headers: aiohttp.typedefs.LooseHeaders,
        name: str,
    ) -> int | None:
        try:
            value = headers.get(name)  # type: ignore[attr-defined]

            if value is None:
                return None

            return int(value)

        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def _retry_delay(
        response: aiohttp.ClientResponse | None,
        attempt: int,
    ) -> int:
        if response is not None:
            retry_after = response.headers.get("Retry-After")

            if retry_after:
                try:
                    return min(
                        max(int(retry_after), 1),
                        HTTP_RETRY_MAX_SECONDS,
                    )
                except ValueError:
                    pass

        exponential = HTTP_RETRY_BASE_SECONDS * (
            2 ** (attempt - 1)
        )

        return min(
            exponential,
            HTTP_RETRY_MAX_SECONDS,
        )

    @staticmethod
    async def _response_message(
        response: aiohttp.ClientResponse,
    ) -> str:
        text = await response.text()

        if not text:
            return "No error body was returned."

        try:
            payload = json.loads(text)

            if isinstance(payload, dict):
                message = payload.get("message")

                if message:
                    return str(message)

        except (TypeError, ValueError):
            pass

        return text[:300]

    async def _request(self) -> ReleaseFeed:
        if self._session is None or self._session.closed:
            raise GitHubError(
                "GitHub client has not been started."
            )

        last_error: Exception | None = None

        for attempt in range(1, MAX_HTTP_RETRIES + 1):
            response: aiohttp.ClientResponse | None = None

            try:
                log.debug(
                    "Requesting GitHub releases: %s "
                    "(attempt %s/%s)",
                    RELEASES_API,
                    attempt,
                    MAX_HTTP_RETRIES,
                )

                async with self._session.get(
                    RELEASES_API,
                    params={
                        "per_page": RELEASES_PER_PAGE,
                        "page": 1,
                    },
                ) as response:

                    limit = self._header_int(
                        response.headers,
                        "X-RateLimit-Limit",
                    )

                    remaining = self._header_int(
                        response.headers,
                        "X-RateLimit-Remaining",
                    )

                    reset = self._header_int(
                        response.headers,
                        "X-RateLimit-Reset",
                    )

                    if response.status == 200:
                        try:
                            payload = await response.json(
                                content_type=None
                            )
                        except (
                            aiohttp.ContentTypeError,
                            json.JSONDecodeError,
                            ValueError,
                        ) as exc:
                            raise GitHubAPIError(
                                "GitHub returned invalid JSON.",
                                status=response.status,
                            ) from exc

                        if not isinstance(payload, list):
                            raise GitHubAPIError(
                                "GitHub returned an unexpected "
                                "release payload.",
                                status=response.status,
                            )

                        releases: list[Release] = []

                        for index, entry in enumerate(payload):
                            if not isinstance(entry, dict):
                                raise GitHubAPIError(
                                    "GitHub release entry "
                                    f"{index} was not an object.",
                                    status=response.status,
                                )

                            try:
                                releases.append(
                                    release_from_github(entry)
                                )
                            except (
                                KeyError,
                                TypeError,
                                ValueError,
                            ) as exc:
                                raise GitHubAPIError(
                                    "Unable to parse GitHub release "
                                    f"entry {index}: {exc}",
                                    status=response.status,
                                ) from exc

                        if not releases:
                            raise GitHubError(
                                "GitHub returned no releases."
                            )

                        return ReleaseFeed(
                            releases=tuple(releases),
                            fetched_at=datetime.now(timezone.utc),
                            from_cache=False,
                            rate_limit_limit=limit,
                            rate_limit_remaining=remaining,
                            rate_limit_reset=reset,
                        )

                    message = await self._response_message(
                        response
                    )

                    if remaining == 0 and reset is not None:
                        reset_time = datetime.fromtimestamp(
                            reset,
                            tz=timezone.utc,
                        ).isoformat()

                        message = (
                            f"{message} Rate limit resets at "
                            f"{reset_time}."
                        )

                    error = GitHubAPIError(
                        f"GitHub returned HTTP "
                        f"{response.status}: {message}",
                        status=response.status,
                    )

                    if (
                        response.status
                        in RETRYABLE_HTTP_STATUSES
                        and attempt < MAX_HTTP_RETRIES
                    ):
                        delay = self._retry_delay(
                            response,
                            attempt,
                        )

                        log.warning(
                            "GitHub returned HTTP %s "
                            "(%s/%s); retrying in %ss.",
                            response.status,
                            attempt,
                            MAX_HTTP_RETRIES,
                            delay,
                        )

                        last_error = error
                        await asyncio.sleep(delay)
                        continue

                    raise error

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc

                log.warning(
                    "GitHub request failed (%s/%s): %s",
                    attempt,
                    MAX_HTTP_RETRIES,
                    exc,
                )

                if attempt < MAX_HTTP_RETRIES:
                    delay = self._retry_delay(
                        response,
                        attempt,
                    )

                    await asyncio.sleep(delay)
                    continue

        raise GitHubError(
            "Unable to contact GitHub after "
            f"{MAX_HTTP_RETRIES} attempts."
        ) from last_error

    async def fetch_releases(
        self,
        *,
        force: bool = False,
    ) -> ReleaseFeed:
        """
        Fetch the current release collection.

        Parameters
        ----------
        force
            Bypass the short local cache.
        """

        if self._session is None or self._session.closed:
            raise GitHubError(
                "GitHub client has not been started."
            )

        if not force and self._cache_is_valid():
            cached = replace(
                self._cached_feed,
                from_cache=True,
            )

            self._last_feed = cached
            return cached

        async with self._request_lock:
            # Recheck after acquiring the lock because another caller may
            # have populated the cache while this caller was waiting.
            if not force and self._cache_is_valid():
                cached = replace(
                    self._cached_feed,
                    from_cache=True,
                )

                self._last_feed = cached
                return cached

            feed = await self._request()

            self._cached_feed = feed
            self._cached_at = time.monotonic()
            self._last_feed = feed

            return feed

    async def latest_release(
        self,
        *,
        include_prereleases: bool = True,
        force: bool = False,
    ) -> Release:
        """
        Return the newest eligible published release.

        Drafts are always excluded. Prereleases are included by default.
        """

        feed = await self.fetch_releases(force=force)

        eligible = [
            release
            for release in feed.releases
            if not release.draft
            and (
                include_prereleases
                or not release.prerelease
            )
        ]

        if not eligible:
            raise GitHubError(
                "GitHub returned no release matching the "
                "configured release policy."
            )

        return max(
            eligible,
            key=lambda release: (
                release.published,
                release.id,
            ),
        )
