from __future__ import annotations

import httpx
import pytest

from adapters.github.http import raise_github_status
from domain.errors import GitHubForbiddenError, GitHubUnavailableError


def _response(status: int, *, json=None, headers=None, url: str = "") -> httpx.Response:
    request = httpx.Request(
        "GET",
        url or "https://api.github.com/repos/Val-Pot/ai-pipe/actions/runs",
    )
    kwargs: dict = {"request": request, "headers": headers or {}}
    if json is not None:
        kwargs["json"] = json
    return httpx.Response(status, **kwargs)


def test_403_missing_scope_has_no_url():
    resp = _response(
        403,
        json={"message": "Resource not accessible by personal access token"},
    )
    with pytest.raises(GitHubForbiddenError) as caught:
        raise_github_status(resp)
    text = str(caught.value)
    assert "http" not in text.lower()
    assert "mozilla" not in text.lower()
    assert "actions/runs" not in text


def test_403_rate_limit_is_retryable():
    resp = _response(
        403,
        json={"message": "API rate limit exceeded"},
        headers={"X-RateLimit-Remaining": "0"},
    )
    with pytest.raises(GitHubUnavailableError) as caught:
        raise_github_status(resp)
    assert type(caught.value) is GitHubUnavailableError


def test_401_is_forbidden_without_url():
    resp = _response(401, json={"message": "Bad credentials"})
    with pytest.raises(GitHubForbiddenError) as caught:
        raise_github_status(resp)
    assert "api.github.com" not in str(caught.value)
