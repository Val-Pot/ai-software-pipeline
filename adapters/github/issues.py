from __future__ import annotations

import logging

from adapters.github.copilot_login import (
    is_copilot_login,
    normalize_copilot_login,
)
from domain.errors import AssignmentError

logger = logging.getLogger(__name__)


class IssuesClient:
    def __init__(
        self, http, owner: str, repo: str, graphql=None, *, copilot_username: str = ""
    ) -> None:
        self._http = http
        self._owner = owner
        self._repo = repo
        self._graphql = graphql
        self._copilot_username = normalize_copilot_login(copilot_username)

    async def create_issue(self, title: str, body: str) -> dict:
        resp = await self._http.post(
            f"/repos/{self._owner}/{self._repo}/issues",
            json={"title": title, "body": body},
        )
        return resp.json()

    async def get_issue(self, issue_number: int) -> dict:
        resp = await self._http.get(
            f"/repos/{self._owner}/{self._repo}/issues/{issue_number}"
        )
        return resp.json()

    async def assign_copilot(self, issue_number: int):
        graphql_ok = await self._assign_via_graphql(issue_number)
        if graphql_ok:
            return graphql_ok

        resp = await self._http.post(
            f"/repos/{self._owner}/{self._repo}/issues/{issue_number}/assignees",
            json={"assignees": [self._copilot_username]},
        )
        body = resp.json()
        if self._assignees_include_copilot(body.get("assignees") or []):
            return body

        try:
            fresh = await self.get_issue(issue_number)
        except Exception:
            fresh = {}
        if self._assignees_include_copilot(fresh.get("assignees") or []):
            return fresh

        raise AssignmentError(
            f"GitHub принял запрос, но не назначил {self._copilot_username}. "
            "Включите Copilot coding agent и проверьте права токена."
        )

    def _assignees_include_copilot(self, assignees) -> bool:
        for item in assignees:
            login = item.get("login") if isinstance(item, dict) else str(item)
            if is_copilot_login(login, self._copilot_username):
                return True
        return False

    async def _assign_via_graphql(self, issue_number: int):
        if self._graphql is None:
            return None
        try:
            issue_id, actor_id = await self._graphql.resolve_assignable_and_actor(
                owner=self._owner,
                repo=self._repo,
                issue_number=issue_number,
                actor_login=self._copilot_username,
            )
            data = await self._graphql.execute(
                """
                mutation($assignableId: ID!, $actorIds: [ID!]!) {
                  replaceActorsForAssignable(input: {
                    assignableId: $assignableId, actorIds: $actorIds
                  }) {
                    clientMutationId
                  }
                }
                """,
                {"assignableId": issue_id, "actorIds": [actor_id]},
            )
            if data.get("replaceActorsForAssignable") is not None:
                return {"assigned": True}
            return None
        except Exception as exc:
            logger.warning("GraphQL Copilot assign failed: %s", exc)
            return None
