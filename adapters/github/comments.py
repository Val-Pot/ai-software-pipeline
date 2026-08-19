from __future__ import annotations


class CommentsClient:
    def __init__(self, http, owner: str, repo: str) -> None:
        self._http = http
        self._owner = owner
        self._repo = repo

    async def create_issue_comment(self, issue_number: int, body: str) -> dict:
        resp = await self._http.post(
            f"/repos/{self._owner}/{self._repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        return resp.json()

    async def list_issue_comments(self, issue_number: int) -> list[dict]:
        resp = await self._http.get(
            f"/repos/{self._owner}/{self._repo}/issues/{issue_number}/comments",
            params={"per_page": 100},
        )
        return resp.json()

    async def create_pr_comment(self, pull_request_number: int, body: str) -> dict:
        return await self.create_issue_comment(pull_request_number, body)
