# AI Software Pipeline

Telegram control channel for GitHub Copilot coding agent.

```
/new
  → GitHub Issue (Task Contract template)
  → проверка полноты контракта
  → Copilot (только если обязательные разделы заполнены)
  → PR
  → Telegram: PR создан
  → /diff → PR-N.diff → внешнее ревью
  → при необходимости Copilot fixes → новый commit
  → /diff → повторное ревью
  → /merge → подтверждение
  → GitHub merge
  → Telegram: задача завершена
```

AI-review в MVP нет: комментарий «Pipeline check: CI passed, no automated review configured.»

Целевой репозиторий должен содержать `.github/workflows/` до делегирования агенту, либо агента нужно явно попросить создать минимальный CI (ISSUE-001, не баг пайплайна).

## What it does

1. Authorized user sends `/new <task>` in Telegram.
2. Pipeline opens a GitHub Issue with a Task Contract template. Copilot is assigned only if required sections are non-empty.
3. GitHub webhooks **and** `watch_issue` polling feed the same `process_event()` path. `issues.edited` re-checks the contract while the job is still `TASK_ACCEPTED`.
4. When a PR exists, `/diff` sends the live unified diff as `PR-{n}.diff`.
5. `/merge` shows CI/PR status and merges only after an explicit button press.

Telegram never calls the GitHub API. GitHub remains the source of truth.

## Requirements

- Python 3.11+
- Telegram bot token
- GitHub token that can create issues, assign Copilot, read PRs, merge, and (for backup poll) read Actions
- Copilot coding agent enabled on the **target** repository
- CI workflows in the target repository (or ask the agent to add them)

Fine-grained PAT: Repository permissions **Actions: Read**, **Contents: Read**, **Issues: Write**, **Pull requests: Write**. Without Actions: Read the bot still works via webhook; it will not poll `/actions/runs`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env
```

Fill `.env`:

| Variable | Meaning |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated Telegram user IDs. Empty list denies everyone. |
| `GITHUB_TOKEN` | Fine-grained or classic PAT |
| `GITHUB_OWNER` / `GITHUB_REPO` | Target repository |
| `GITHUB_WEBHOOK_SECRET` | Secret for `POST /webhooks/github` |
| `COPILOT_USERNAME` | `copilot-swe-agent[bot]` |
| `JOBS_STORE_PATH` | JSON file for jobs + processed event IDs. Empty = in-memory only. |

Point a GitHub webhook at `https://<host>/webhooks/github` for `issues`, `issue_comment`, `pull_request`, `workflow_run`. Polling still works if the webhook is missing. `issues.edited` re-checks a Task Contract that is still waiting to start.

```bash
python -m app.main
```

## Docker

Secrets stay in `.env` (not baked into the image). Job state survives container restarts via the `pipeline-data` volume.

```bash
copy .env.example .env
docker compose up -d --build
```

Webhook URL: `https://<host>/webhooks/github`. Health: `GET /health`.

```bash
docker compose logs -f
docker compose down
```

To run the image without Compose:

```bash
docker build -t ai-software-pipeline .
docker run --rm -p 8080:8080 --env-file .env -v pipeline-data:/app/data ai-software-pipeline
```

## Commands

| Command | Effect |
| --- | --- |
| `/new <text>` | Create Issue with Task Contract; assign Copilot if required sections are filled |
| `/new` | Empty: create an empty-template Issue, or re-check the current `TASK_ACCEPTED` Issue |
| `/diff` | Live unified diff of the current job PR as `PR-{n}.diff`. A PR number is rejected. |
| `/merge` | Status + Merge/Cancel for the current job PR. `/merge 123` is rejected. Confirm re-checks the allowlist. |
| `/status` | Current job state |

## Tests

```bash
pytest
```

Covers BUG-001…009, PIPE-PR-001 (`TEST-015+`, PR-001…PR-028), and Task Contract gate (V1–V7).

## Layout

```
adapters/     GitHub, Telegram, coding agent, file/in-memory jobs
orchestrator/ Job FSM, watchers, /diff, /merge
ports/        Protocols — Telegram does not import GitHub
webhooks/     Signature check → parse_webhook_event
docs/         TZ, architecture, acceptance program
```

## Spec sources

`AI-PIPELINE-PR-FIXES.txt` and `AI-PIPELINE-PR-FIXES.diff` in the repo root are the defect TZ (BUG-001…011) and PIPE-PR-001 (`/diff`, `/merge`) this tree implements.
