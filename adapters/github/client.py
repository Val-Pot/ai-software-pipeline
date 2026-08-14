"""
Async HTTPX client wrapper for GitHub REST API with retry & timeout handling.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class GitHubClientError(Exception):
    """Base exception for GitHub API failures."""
    pass


def _format_http_error(exc: Exception) -> str:
    """Include GitHub response body so 422 validation errors are diagnosable."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        body = exc.response.text[:500]
        return f"{exc} | body={body}"
    return str(exc)


def _is_retryable(exc: Exception) -> bool:
    """Retry network errors and transient HTTP statuses; never retry 422/401/403."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return isinstance(exc, httpx.RequestError)


class GitHubHTTPClient:
    """Async HTTP client for GitHub API with auth headers and timeout configuration."""

    BASE_URL = "https://api.github.com"

    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.timeout = timeout
        self.max_retries = max_retries
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AI-Software-Pipeline/1.0",
        }

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=self._headers,
            timeout=self.timeout,
        )

    async def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any] | List[Dict[str, Any]]:
        """Perform GET request with retries."""
        url = f"/repos/{self.owner}/{self.repo}{endpoint}"
        async with self._get_client() as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    detail = _format_http_error(e)
                    logger.warning("GET %s failed (attempt %d/%d): %s", url, attempt, self.max_retries, detail)
                    if not _is_retryable(e) or attempt == self.max_retries:
                        raise GitHubClientError(f"GET {url} failed: {detail}") from e

    async def post(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Perform POST request with retries."""
        url = f"/repos/{self.owner}/{self.repo}{endpoint}"
        async with self._get_client() as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = await client.post(url, json=data)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    detail = _format_http_error(e)
                    logger.warning("POST %s failed (attempt %d/%d): %s", url, attempt, self.max_retries, detail)
                    if not _is_retryable(e) or attempt == self.max_retries:
                        raise GitHubClientError(f"POST {url} failed: {detail}") from e

    async def put(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Perform PUT request with retries."""
        url = f"/repos/{self.owner}/{self.repo}{endpoint}"
        async with self._get_client() as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = await client.put(url, json=data)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    detail = _format_http_error(e)
                    logger.warning("PUT %s failed (attempt %d/%d): %s", url, attempt, self.max_retries, detail)
                    if not _is_retryable(e) or attempt == self.max_retries:
                        raise GitHubClientError(f"PUT {url} failed: {detail}") from e

    async def patch(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Perform PATCH request with retries."""
        url = f"/repos/{self.owner}/{self.repo}{endpoint}"
        async with self._get_client() as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = await client.patch(url, json=data)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    detail = _format_http_error(e)
                    logger.warning("PATCH %s failed (attempt %d/%d): %s", url, attempt, self.max_retries, detail)
                    if not _is_retryable(e) or attempt == self.max_retries:
                        raise GitHubClientError(f"PATCH {url} failed: {detail}") from e

