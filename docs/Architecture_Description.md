# Architecture Description

Hexagonal layout. Telegram is a thin control/review channel. GitHub is the source of truth.

```
Telegram → Adapter → Orchestrator → GitHubPort → GitHub Adapter → API
                 ↘ CodingAgentPort ↗
webhook / watch_issue  →  process_event()  →  Job FSM
```

`watch_issue` is a reserve for the webhook. Both sources share `process_event` and `processed_event_ids`. There is no second Telegram FSM. Job state is the only process model.

On process start `recover_active_jobs()` reloads jobs from disk, restores processed event IDs, restarts watchers, and tells the operator the service came back. That is separate from BUG-001 (webhook drop inside a live process).

## Layers

| Layer | Package | Responsibility |
| --- | --- | --- |
| Application | `orchestrator/` | Job FSM, `/diff`, `/merge`, recover, watchers |
| Ports | `ports/` | `GitHubPort`, `NotifierPort`, `CodingAgentPort`, `JobRepository` |
| GitHub adapter | `adapters/github/` | Issues, PRs, comments, Actions, GraphQL assign |
| Coding agent | `adapters/coding_agent/` | assign, `@copilot` fix, webhook parse, poll |
| Telegram adapter | `adapters/telegram/` | commands and notifications only |
| Jobs | `adapters/jobs/` | `FileJobRepository` (`JOBS_STORE_PATH`, default `data/jobs.json`); tests use in-memory |
| HTTP | `app/`, `webhooks/` | FastAPI lifespan, GitHub webhook router |

## Job states

```
TASK_ACCEPTED
    → CODING_AGENT_RUNNING
        → WAIT_TESTS
            → TEST_PASSED
                → MERGE_CONFIRMATION_PENDING
                    → DONE
ADAPTER_ERROR / FAILED   (terminal)
```

`CODING_AGENT_RUNNING` and `WAIT_TESTS` do not advance inside `_process_state`. They wait for `process_event`.

A job stays in `TASK_ACCEPTED` until the GitHub Issue Task Contract has every required section filled. Then the existing `coding_agent.trigger` path runs.

## Task Contract

`/new` always creates a GitHub Issue with the Task Contract template. The contract lives only in that Issue. Pipeline checks **structure**, not quality.

Required sections: Goal, Scope, Expected Behavior, Architecture Constraints, Acceptance Criteria, Verification. `N/A` is a valid Architecture Constraints value. Visual References / Out of Scope / Additional Context are optional; a screenshot does not replace required text.

Incomplete: Coding Agent is not assigned; Product gets `Task Contract is incomplete` with the missing section names. Retry: empty `/new` re-reads the Issue, or `issues.edited` while still `TASK_ACCEPTED`. A later edit that is still incomplete sends an updated Missing list (unchanged lists are not repeated). Job metadata in `jobs.json` does not include the Issue body.

## Coding agent login

One login: `copilot-swe-agent[bot]`. The older assumption of a separate `github-copilot[bot]` comment account is wrong and is not used.

## Webhooks

Single parse path: `webhooks/router.py` → `coding_agent.parse_webhook_event` → `process_event`. Dead `handle_*` helpers are not present. `issues.edited` becomes `ISSUE_UPDATED` and re-runs the Task Contract gate only while the job is `TASK_ACCEPTED`.

## Review / merge control (PIPE-PR-001)

Telegram is a thin control channel, not a GitHub client.

- `/diff` → Orchestrator.`request_diff` → GitHubPort.`get_pull_request_diff` (live unified diff) → NotifierPort.`send_document` (`PR-{n}.diff`)
- `/merge` → Orchestrator.`request_merge` (status + buttons) → `confirm_merge(job_id, confirmed, operator_id=)` → GitHubPort.`merge_pull_request`
- Merge confirmation is allowlist-only (`operator_id ∈ TELEGRAM_ALLOWED_USER_IDS`), not bound to `job.user_id`. The check runs at confirm entry and again before GitHub as defense-in-depth, not as an atomic lock.
- Job FSM is the only process model (`MERGE_CONFIRMATION_PENDING` is a job state, not a Telegram FSM)
- `/merge 123` and `/diff 123` are rejected with an explicit message; only the PR bound to the current job is used

## Review comment

MVP does not run an LLM review. After CI pass the bot posts:

`Pipeline check: CI passed, no automated review configured.`
