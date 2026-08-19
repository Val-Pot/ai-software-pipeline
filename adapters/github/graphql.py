from __future__ import annotations

import httpx

from adapters.github.copilot_login import copilot_login_aliases
from adapters.github.http import raise_github_status


class GitHubGraphQL:
    def __init__(self, token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "ai-software-pipeline",
            },
            timeout=30.0,
        )

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        resp = await self._client.post(
            "/graphql",
            json={"query": query, "variables": variables or {}},
        )
        raise_github_status(resp)
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(payload["errors"])
        return payload.get("data") or {}

    async def resolve_assignable_and_actor(
        self,
        *,
        owner: str,
        repo: str,
        issue_number: int,
        actor_login: str,
    ) -> tuple[str, str]:
        data = await self.execute(
            """
            query($owner: String!, $repo: String!, $number: Int!, $login: String!) {
              repository(owner: $owner, name: $repo) {
                issue(number: $number) { id }
                assignableUsers(query: $login, first: 10) {
                  nodes { id login }
                }
              }
            }
            """,
            {
                "owner": owner,
                "repo": repo,
                "number": issue_number,
                "login": actor_login,
            },
        )
        issue = (data.get("repository") or {}).get("issue") or {}
        issue_id = issue.get("id")
        aliases = copilot_login_aliases(actor_login)
        actor_id = self._match_actor(
            (data.get("repository") or {}).get("assignableUsers", {}).get("nodes") or [],
            aliases,
        )
        if not actor_id:
            actor_id = await self._resolve_suggested_actor(
                owner, repo, actor_login, aliases
            )
        if not issue_id or not actor_id:
            raise RuntimeError(
                f"Cannot resolve GraphQL ids for issue #{issue_number} / {actor_login}"
            )
        return issue_id, actor_id

    def _match_actor(self, nodes: list, aliases: frozenset[str]) -> str | None:
        for node in nodes:
            if (node.get("login") or "").lower() in aliases:
                return node.get("id")
        return None

    async def _resolve_suggested_actor(
        self, owner: str, repo: str, actor_login: str, aliases: frozenset[str]
    ) -> str | None:
        queries = [
            """
            query($owner: String!, $repo: String!) {
              repository(owner: $owner, name: $repo) {
                suggestedActors(capabilities: [CAN_BE_ASSIGNED], first: 50) {
                  nodes {
                    ... on Bot { id login }
                    ... on User { id login }
                  }
                }
              }
            }
            """,
            """
            query($owner: String!, $repo: String!, $login: String!) {
              repository(owner: $owner, name: $repo) {
                suggestedActors(query: $login, first: 20) {
                  nodes {
                    ... on Bot { id login }
                    ... on User { id login }
                  }
                }
              }
            }
            """,
        ]
        variables_list = [
            {"owner": owner, "repo": repo},
            {"owner": owner, "repo": repo, "login": actor_login},
        ]
        for query, variables in zip(queries, variables_list):
            try:
                suggested = await self.execute(query, variables)
            except Exception:
                continue
            actor_id = self._match_actor(
                (suggested.get("repository") or {})
                .get("suggestedActors", {})
                .get("nodes")
                or [],
                aliases,
            )
            if actor_id:
                return actor_id
            nodes = (
                (suggested.get("repository") or {})
                .get("suggestedActors", {})
                .get("nodes")
                or []
            )
            swe = [
                node
                for node in nodes
                if "swe-agent" in (node.get("login") or "").lower()
                and "reviewer" not in (node.get("login") or "").lower()
            ]
            if len(swe) == 1 and swe[0].get("id"):
                return swe[0]["id"]
        return None

    async def aclose(self) -> None:
        await self._client.aclose()
