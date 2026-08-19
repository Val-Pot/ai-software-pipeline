from __future__ import annotations


class ActionsClient:
    def __init__(self, http, owner: str, repo: str) -> None:
        self._http = http
        self._owner = owner
        self._repo = repo

    async def get_latest_run_for_branch(self, branch: str):
        resp = await self._http.get(
            f"/repos/{self._owner}/{self._repo}/actions/runs",
            params={"branch": branch, "per_page": 10},
        )
        runs = resp.json().get("workflow_runs") or []
        for run in runs:
            if (run.get("status") or "").lower() == "completed":
                return run
        return None

    async def list_runs_for_branch(self, branch: str, per_page: int = 10) -> list[dict]:
        resp = await self._http.get(
            f"/repos/{self._owner}/{self._repo}/actions/runs",
            params={"branch": branch, "per_page": per_page},
        )
        return resp.json().get("workflow_runs") or []

    async def get_run_logs(self, run_id: int) -> str:
        try:
            resp = await self._http.get(
                f"/repos/{self._owner}/{self._repo}/actions/runs/{run_id}/logs"
            )
            return resp.text
        except Exception:
            return f"Failed to fetch logs for workflow run {run_id}"
