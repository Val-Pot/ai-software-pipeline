"""
FastAPI application lifespan context manager.

Responsibilities (startup order matters — dependencies listed top-to-bottom):

  1.  Configure logging.
  2.  Validate and load Settings.
  3.  Construct GitHub HTTP client.
  4.  Construct GitHubAdapter (issues, PRs, Actions).
  5.  Construct CodingAgentAdapter.
  6.  Construct InMemoryPersistenceAdapter.
  7.  Construct PipelineRunner (Orchestrator) — wires GitHubAdapter.
  8.  Construct Aiogram Bot instance.
  9.  Construct TelegramBotClient wrapper.
  10. Construct TelegramNotifier — wires to PipelineRunner.notifier.
  11. Construct Aiogram Dispatcher with middlewares and handlers.
  12. Store all singletons on ``app.state`` for DI retrieval.
  13. Recover any persisted active pipeline jobs.
  14. Start Telegram long-polling task OR register Telegram webhook.
  15. Yield (application serves requests).
  16. Graceful shutdown: cancel polling task, deregister webhook, close sessions.

No business logic lives here — only wiring.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from adapters.coding_agent.adapter import CodingAgentAdapter
from adapters.github.adapter import GitHubAdapter
from adapters.github.client import GitHubHTTPClient
from adapters.github.webhooks import REQUIRED_WEBHOOK_EVENTS
from adapters.telegram.client import TelegramBotClient
from adapters.telegram.notifier import TelegramNotifier
from config.logging_setup import configure_logging
from config.settings import get_settings
from orchestrator.persistence import InMemoryPersistenceAdapter
from orchestrator.pipeline_runner import PipelineRunner
from telegram_bot.bot import create_bot
from telegram_bot.dispatcher import create_dispatcher

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Async context manager managing the full application lifecycle.

    Everything constructed here is stored on ``app.state`` so that
    FastAPI dependency functions can retrieve it via ``Request``.
    """

    # ------------------------------------------------------------------
    # 1. Logging
    # ------------------------------------------------------------------
    # Configure logging first so all subsequent init messages are captured.
    configure_logging()
    logger.info("=== AI Software Pipeline starting up ===")

    # ------------------------------------------------------------------
    # 2. Settings
    # ------------------------------------------------------------------
    settings = get_settings()
    logger.info(
        "Settings loaded: env=%s log_level=%s webhook_mode=%s",
        settings.environment,
        settings.log_level,
        settings.telegram_use_webhook,
    )

    # ------------------------------------------------------------------
    # 3–4. GitHub HTTP client + GitHubAdapter
    # ------------------------------------------------------------------
    github_client: GitHubHTTPClient | None = None
    github_adapter: GitHubAdapter | None = None

    if settings.github_token and settings.github_owner and settings.github_repo:
        github_client = GitHubHTTPClient(
            token=settings.github_token,
            owner=settings.github_owner,
            repo=settings.github_repo,
            timeout=settings.coding_agent_request_timeout,
            max_retries=settings.coding_agent_max_retries,
        )
        github_adapter = GitHubAdapter(
            token=settings.github_token,
            owner=settings.github_owner,
            repo=settings.github_repo,
            timeout=settings.coding_agent_request_timeout,
            copilot_username=settings.copilot_username,
        )
        logger.info(
            "GitHubAdapter initialised: owner=%s repo=%s",
            settings.github_owner,
            settings.github_repo,
        )
        logger.info(
            "Configure the GitHub repo webhook for %s/%s → POST /webhooks/github "
            "(application/json) with events: %s",
            settings.github_owner,
            settings.github_repo,
            ", ".join(REQUIRED_WEBHOOK_EVENTS),
        )
        if settings.ci_workflow_names:
            logger.info(
                "CI workflow filter enabled: %s",
                ", ".join(sorted(settings.ci_workflow_names)),
            )
    else:
        logger.warning(
            "GitHub credentials not fully configured — GitHubAdapter disabled. "
            "Set GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO in .env."
        )

    # ------------------------------------------------------------------
    # 5. CodingAgentAdapter
    # ------------------------------------------------------------------
    coding_agent_adapter: CodingAgentAdapter | None = None

    if github_client is not None:
        coding_agent_adapter = CodingAgentAdapter(
            client=github_client,
            copilot_username=settings.copilot_username,
            max_retries=settings.coding_agent_max_retries,
            poll_interval=settings.coding_agent_poll_interval,
            request_timeout=settings.coding_agent_request_timeout,
        )
        logger.info(
            "CodingAgentAdapter initialised: agent=%s poll_interval=%.1fs",
            settings.copilot_username,
            settings.coding_agent_poll_interval,
        )
    else:
        logger.warning("CodingAgentAdapter disabled — GitHub client unavailable.")

    # ------------------------------------------------------------------
    # 6. Persistence
    # ------------------------------------------------------------------
    persistence = InMemoryPersistenceAdapter()
    logger.info("InMemoryPersistenceAdapter initialised.")

    # ------------------------------------------------------------------
    # 7. PipelineRunner (Orchestrator)
    # ------------------------------------------------------------------
    orchestrator_runner = PipelineRunner(
        persistence=persistence,
        github=github_adapter,         # may be None in dev without GitHub creds
        notifier=None,                 # injected below after Telegram is ready
        max_retries=settings.coding_agent_max_retries,
        enable_ai_review=True,
    )
    logger.info("PipelineRunner (Orchestrator) initialised.")

    # ------------------------------------------------------------------
    # 8–9. Telegram Bot + BotClient
    # ------------------------------------------------------------------
    bot = create_bot(token=settings.telegram_bot_token)
    bot_client = TelegramBotClient(bot=bot)
    logger.info("Aiogram Bot instance created.")

    # ------------------------------------------------------------------
    # 10. TelegramNotifier
    # ------------------------------------------------------------------
    # Dispatcher is needed by the notifier to set FSM states, so it must
    # be created before the notifier but after the bot.
    dp = create_dispatcher(settings=settings, orchestrator=orchestrator_runner)
    notifier = TelegramNotifier(client=bot_client, dispatcher=dp)
    logger.info("TelegramNotifier initialised.")

    # Wire notifier back into the Orchestrator (late-bind pattern).
    orchestrator_runner.notifier = notifier

    # ------------------------------------------------------------------
    # 12. Store all singletons on app.state for DI access
    # ------------------------------------------------------------------
    app.state.settings = settings
    app.state.persistence = persistence
    app.state.orchestrator_runner = orchestrator_runner
    app.state.orchestrator = orchestrator_runner   # alias for legacy DI keys
    app.state.github_adapter = github_adapter
    app.state.github_client = github_client
    app.state.coding_agent_adapter = coding_agent_adapter
    app.state.bot = bot
    app.state.bot_client = bot_client
    app.state.dp = dp
    app.state.notifier = notifier

    # ------------------------------------------------------------------
    # 13. Recover active pipeline jobs
    # ------------------------------------------------------------------
    logger.info("Recovering active pipeline jobs...")
    await orchestrator_runner.recover_active_jobs()

    # ------------------------------------------------------------------
    # 14. Start Telegram: long-polling or webhook
    # ------------------------------------------------------------------
    polling_task: asyncio.Task | None = None

    if settings.telegram_use_webhook:
        webhook_url = settings.telegram_webhook_full_url
        logger.info("Registering Telegram webhook: %s", webhook_url)
        await bot.set_webhook(
            url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )
    else:
        logger.info("Starting Telegram Bot long-polling background task...")
        polling_task = asyncio.create_task(
            dp.start_polling(bot, handle_signals=False),
            name="telegram-polling",
        )

    logger.info("=== AI Software Pipeline startup complete ===")

    # ------------------------------------------------------------------
    # 15. Application serves requests
    # ------------------------------------------------------------------
    yield

    # ------------------------------------------------------------------
    # 16. Graceful shutdown
    # ------------------------------------------------------------------
    logger.info("=== AI Software Pipeline shutting down ===")

    # Cancel long-polling task
    if polling_task and not polling_task.done():
        logger.info("Cancelling Telegram polling task...")
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            logger.info("Telegram polling task cancelled cleanly.")

    # Remove webhook registration
    if settings.telegram_use_webhook:
        logger.info("Deleting Telegram webhook...")
        try:
            await bot.delete_webhook()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to delete Telegram webhook: %s", exc)

    # Close the Telegram Bot HTTP session
    try:
        await bot.session.close()
        logger.info("Telegram Bot session closed.")
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Error closing Telegram Bot session: %s", exc)

    logger.info("=== AI Software Pipeline shutdown complete ===")
