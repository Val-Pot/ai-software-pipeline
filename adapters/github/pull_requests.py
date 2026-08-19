from __future__ import annotations

import httpx

from adapters.github.models import GitHubPullRequest, GitHubUser
from domain.errors import MergeError


class PullRequestsClient:
    def __init__(self, http, owner: str, repo: str) -> None:
        self._http = http
        self._owner = owner
        self._repo = repo

    async def get_pull_request(self, pull_request_number: int) -> GitHubPullRequest:
        resp = await self._http.get(
            f"/repos/{self._owner}/{self._repo}/pulls/{pull_request_number}"
        )
        data = resp.json()
        reviewers = [
            GitHubUser(login=item["login"], id=item.get("id"))
            for item in (data.get("requested_reviewers") or [])
            if item.get("login")
        ]
        head = data.get("head") or {}
        return GitHubPullRequest(
            number=data["number"],
            html_url=data.get("html_url") or "",
            state=data.get("state") or "open",
            draft=bool(data.get("draft")),
            requested_reviewers=reviewers,
            merged=bool(data.get("merged")),
            mergeable=data.get("mergeable"),
            mergeable_state=data.get("mergeable_state"),
            head_sha=(head.get("sha") or ""),
            head_ref=(head.get("ref") or ""),
        )

    async def list_pulls_for_issue(self, issue_number: int) -> list[dict]:
        resp = await self._http.get(
            f"/repos/{self._owner}/{self._repo}/issues/{issue_number}/timeline",
            params={"per_page": 100},
        )
        items = resp.json() if isinstance(resp.json(), list) else []
        pulls: list[dict] = []
        for item in items:
            if item.get("event") == "cross-referenced":
                source = (item.get("source") or {}).get("issue") or {}
                if source.get("pull_request"):
                    pulls.append(source)
        if pulls:
            return pulls
        resp = await self._http.get(
            f"/repos/{self._owner}/{self._repo}/pulls",
            params={"state": "all", "per_page": 30},
        )
        marker = f"#{issue_number}"
        issue_token = str(issue_number)
        return [
            pr
            for pr in (resp.json() or [])
            if marker in (pr.get("body") or "")
            or marker in (pr.get("title") or "")
            or issue_token in ((pr.get("head") or {}).get("ref") or "")
        ]

    async def get_pull_request_diff(self, pull_request_number: int) -> str:
        resp = await self._http.get(
            f"/repos/{self._owner}/{self._repo}/pulls/{pull_request_number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        return resp.text

    async def merge_pull_request(
        self, pull_request_number: int, *, sha: str | None = None
    ) -> dict:
        payload: dict = {"merge_method": "merge"}
        if sha:
            payload["sha"] = sha
        try:
            resp = await self._http.put(
                f"/repos/{self._owner}/{self._repo}/pulls/{pull_request_number}/merge",
                json=payload,
            )
        except httpx.HTTPStatusError as exc:
            reason = exc.response.text
            try:
                reason = exc.response.json().get("message") or reason
            except Exception:
                pass
            raise MergeError(reason) from exc
        except Exception as exc:
            raise MergeError(str(exc)) from exc
        return resp.json()
