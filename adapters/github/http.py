from __future__ import annotations

import httpx

from domain.errors import GitHubForbiddenError, GitHubUnavailableError


def _response_message(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        return resp.text or ""
    if isinstance(payload, dict):
        return str(payload.get("message") or "")
    return resp.text or ""


def _is_github_rate_limit(resp: httpx.Response) -> bool:
    if resp.headers.get("Retry-After"):
        return True
    if resp.headers.get("X-RateLimit-Remaining") == "0":
        return True
    text = _response_message(resp).lower()
    return "rate limit" in text or "abuse detection" in text


def raise_github_status(resp: httpx.Response) -> None:
    """Map GitHub HTTP errors without leaking URLs into exception text."""
    code = resp.status_code
    if code < 400:
        return
    if code == 403 and _is_github_rate_limit(resp):
        raise GitHubUnavailableError("GitHub API rate limited")
    if code == 401:
        raise GitHubForbiddenError("GitHub API 401 Unauthorized")
    if code == 403:
        raise GitHubForbiddenError("GitHub API 403 Forbidden")
    if code == 404:
        raise GitHubUnavailableError("GitHub API 404 Not Found")
    if code == 429 or code >= 500:
        raise GitHubUnavailableError(f"GitHub API {code}")
    resp.raise_for_status()


class GitHubHttp:
    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-software-pipeline",
            },
            timeout=timeout,
        )

    def _raise_for_status(self, resp: httpx.Response) -> None:
        raise_github_status(resp)

    async def get(self, path: str, **kwargs) -> httpx.Response:
        resp = await self._client.get(path, **kwargs)
        self._raise_for_status(resp)
        return resp

    async def post(self, path: str, **kwargs) -> httpx.Response:
        resp = await self._client.post(path, **kwargs)
        self._raise_for_status(resp)
        return resp

    async def put(self, path: str, **kwargs) -> httpx.Response:
        resp = await self._client.put(path, **kwargs)
        self._raise_for_status(resp)
        return resp

    async def aclose(self) -> None:
        await self._client.aclose()
