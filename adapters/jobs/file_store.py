from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from adapters.jobs.memory import InMemoryJobRepository
from domain.models import Job

logger = logging.getLogger(__name__)


class FileJobRepository(InMemoryJobRepository):
    """JobRepository that survives process restart via an atomic JSON file."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self.processed_event_ids: set[str] = set()
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("job store unreadable (%s), starting empty: %s", self._path, exc)
            return
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        for raw in jobs or []:
            try:
                job = Job.model_validate(raw)
                job.body = ""
                self._jobs[job.id] = job
            except Exception as exc:
                logger.warning("skip corrupt job record: %s", exc)
        ids = payload.get("processed_event_ids") if isinstance(payload, dict) else []
        self.processed_event_ids = {str(item) for item in (ids or [])}

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "jobs": [self._job_for_disk(job) for job in self._jobs.values()],
            "processed_event_ids": sorted(self.processed_event_ids),
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    def _job_for_disk(self, job: Job) -> dict:
        data = job.model_dump(mode="json")
        data["body"] = ""
        return data

    async def save(self, job: Job) -> None:
        async with self._lock:
            stored = job.model_copy(deep=True)
            stored.body = ""
            await super().save(stored)
            self._flush()

    async def replace_processed_event_ids(self, ids: set[str]) -> None:
        async with self._lock:
            self.processed_event_ids = {str(item) for item in ids}
            self._flush()
