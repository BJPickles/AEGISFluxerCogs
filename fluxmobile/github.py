"""
Flux Mobile - github.py

GitHub API client for Flux Mobile.

Responsibility:
- Talk to the GitHub REST API.
- Handle retries.
- Convert JSON into Release objects.

ANTI-DRIFT

This module MUST NOT:
- Import Discord.
- Import Red.
- Read/write Config.
- Build embeds.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from .constants import (
    HTTP_TIMEOUT,
    HTTP_USER_AGENT,
    LOGGER_NAME,
    MAX_HTTP_RETRIES,
    RELEASES_API,
)
from .models import Release, release_from_github

log = logging.getLogger(LOGGER_NAME)


class GitHubError(Exception):
    """Base GitHub exception."""


class GitHubAPIError(GitHubError):
    """Raised when GitHub returns an unexpected response."""


class GitHubClient:
    """
    Simple GitHub REST client.

    This class is intentionally responsible ONLY for HTTP communication.
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Create the HTTP session if required."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)

            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={
                    "User-Agent": HTTP_USER_AGENT,
                    "Accept": "application/vnd.github+json",
                },
            )

    async def close(self) -> None:
        """Dispose of the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self) -> list[dict]:
        """
        Request the GitHub releases endpoint.

        Returns
        -------
        list[dict]
            GitHub release JSON.
        """

        if self._session is None:
            raise GitHubError("GitHub client has not been started.")

        last_error = None

        for attempt in range(1, MAX_HTTP_RETRIES + 1):

            try:
                log.debug("Requesting GitHub releases: %s", RELEASES_API)

                async with self._session.get(RELEASES_API) as response:

                    if response.status != 200:
                        raise GitHubAPIError(
                            f"GitHub returned HTTP {response.status}"
                        )

                    payload = await response.json()

                    if not isinstance(payload, list):
                        raise GitHubAPIError(
                            "GitHub returned an unexpected payload."
                        )

                    return payload

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = exc

                log.warning(
                    "GitHub request failed (%s/%s): %s",
                    attempt,
                    MAX_HTTP_RETRIES,
                    exc,
                )

                if attempt < MAX_HTTP_RETRIES:
                    await asyncio.sleep(2)

        raise GitHubError(
            f"Unable to contact GitHub after {MAX_HTTP_RETRIES} attempts."
        ) from last_error

    async def latest_release(self) -> Release:
        """
        Fetch the newest GitHub release.

        Returns
        -------
        Release
        """

        releases = await self._request()

        if not releases:
            raise GitHubError("GitHub returned no releases.")

        #
        # GitHub returns releases newest-first.
        #
        latest = releases[0]

        return release_from_github(latest)
