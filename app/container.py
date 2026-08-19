from __future__ import annotations

from adapters.coding_agent.adapter import CodingAgentAdapter
from adapters.github.adapter import GitHubAdapter
from adapters.jobs.file_store import FileJobRepository
from adapters.jobs.memory import InMemoryJobRepository
from adapters.telegram.handlers import TelegramHandlers
from adapters.telegram.notifier import TelegramNotifier
from config.settings import Settings
from orchestrator.pipeline_runner import PipelineRunner


class AppContainer:
    def __init__(self, settings: Settings, *, bot=None) -> None:
        self.settings = settings
        self.jobs = (
            FileJobRepository(settings.jobs_store_path)
            if settings.jobs_store_path
            else InMemoryJobRepository()
        )
        self.github = GitHubAdapter(settings)
        self.coding_agent = CodingAgentAdapter(self.github, settings)
        self.notifier = TelegramNotifier(bot) if bot is not None else _NullNotifier()
        self.runner = PipelineRunner(
            settings=settings,
            jobs=self.jobs,
            github=self.github,
            coding_agent=self.coding_agent,
            notifier=self.notifier,
        )
        self.telegram_handlers = TelegramHandlers(self.runner, self.jobs, settings)

    async def aclose(self) -> None:
        await self.github.aclose()


class _NullNotifier:
    async def send_text(self, chat_id: int, text: str) -> None:
        return None

    async def send_document(
        self, chat_id: int, filename: str, content: bytes, caption: str = ""
    ) -> None:
        return None

    async def send_merge_confirmation(
        self, chat_id: int, job_id: str, text: str
    ) -> None:
        return None
