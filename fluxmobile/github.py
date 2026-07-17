"""
Flux Mobile - github.py

GitHub API client for Flux Mobile.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from .constants import (
    HTTP_TIMEOUT,
    HTTP_USER_AGENT,
    LATEST_RELEASE_API,
    LOGGER_NAME,
    MAX_HTTP_RETRIES,
)
from .models import Release, release_from_github

log = logging.getLogger(LOGGER_NAME)


class GitHubError(Exception):
    """Base GitHub exception."""


class GitHubAPIError(GitHubError):
    """Raised when GitHub returns an unexpected response."""


class GitHubClient:
    """
    ANTI-DRIFT CONTRACT

    This class ONLY communicates with GitHub.

    It MUST NOT:
    - Import Discord.
    - Import Red.
    - Read/write Config.
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": HTTP_USER_AGENT},
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self) -> dict:
        if self._session is None:
            raise GitHubError("Client not started.")

        last_error = None

        for attempt in range(1, MAX_HTTP_RETRIES + 1):
            try:
                async with self._session.get(LATEST_RELEASE_API) as resp:
                    if resp.status != 200:
                        raise GitHubAPIError(
                            f"GitHub returned HTTP {resp.status}"
                        )
                    return await resp.json()

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
        Fetch the latest GitHub release.

        Returns:
            Release
        """
        payload = await self._request()
        return rele
ase_from_github(payload)
