# Acceptance Test Program

A point is closed when the “problem” scenario no longer reproduces and the “criterion” scenario does.

## TEST-001 Webhook off, polling finishes the job (BUG-001)

Disable the repository webhook or break the secret. `/new` still reaches a final Telegram message via `watch_issue`.

## TEST-002 Stale warning (BUG-001)

A job left in `CODING_AGENT_RUNNING` / `WAIT_TESTS` past `CODING_AGENT_STALE_TIMEOUT_SEC` produces a warning and does **not** become `FAILED`.

## TEST-003 Fix command (BUG-002 / BUG-003 / FR-4.2)

On CI failure the issue comment starts with exactly `@copilot Fix the failing tests`. `grep -rn trigger_fix_iteration` shows the orchestrator call. `github.trigger_fix` does not exist.

## TEST-004 Agent login (BUG-004 / BUG-007)

`grep -rn "github-copilot\[bot\]"` is empty except the explicit “erroneous assumption” note. Comments from `copilot-pull-request-reviewer[bot]` do not create `AGENT_STARTED` / `COPILOT_QUESTION` / `AGENT_COMPLETED`.

## TEST-005 Assignment fact (BUG-005)

Repository with Copilot coding agent disabled (or a token without rights) → `ADAPTER_ERROR`, not a fake “agent started”.

## TEST-006 Completion signal (BUG-006)

`draft=false` and/or requested reviewers produce completion even without a magic comment.

## TEST-007 Completed Actions run (BUG-008)

`get_latest_run_for_branch` with an `in_progress` run on top of a previous `failure` returns the completed failure.

## TEST-008 Honest review comment (BUG-009)

Bot text does not claim an AI review that was not performed.

## TEST-015+ PR review control (PIPE-PR-001)

| ID | Criterion |
| --- | --- |
| PR-001 | Open PR, `/diff` → GitHub queried, file sent, PR unchanged |
| PR-002 | New commit, second `/diff` → fresh diff |
| PR-003 | `/merge` with CI PASS → confirmation only, no merge yet |
| PR-004 | Confirm + GitHub allows → merge, job `DONE` |
| PR-005 | CI FAILURE → no merge |
| PR-006 | CI running → no merge |
| PR-007 | Already merged → no second merge |
| PR-008 | No PR → clear text, no GitHub mutation |
| PR-009 | Telegram `send_document` error → PR/job unchanged |
| PR-010 | GitHub merge error → job not `DONE` |
| PR-011 | Closed unmerged PR → `/diff` refused, no file |
| PR-012 | Empty diff → text only, no empty file |
| PR-013 | Oversized diff → not truncated; GitHub `{pr}.diff` link kept |
| PR-014 | GitHub down on `/diff` → user-facing error, no hang |
| PR-015 | Copilot waiting for a reply → `/merge` refused |
| PR-016 | Closed unmerged PR → `/merge` refused |
| PR-017 | CI PASS on `/merge`, FAILURE on confirm → no merge |
| PR-018 | `/merge` after `DONE` → “уже объединён”, no second merge |
| PR-019 | GitHub down on merge check → user-facing error |
| PR-020 | `/merge 123` / `/diff 99` rejected; no GitHub call |
| PR-021 | Empty allowlist → `/diff`, `/merge`, `/status` denied |
| PR-022 | Confirm after HEAD moved → no merge, must `/merge` again (`tests/test_pr_review_control.py`) |
| PR-023 | Process restart reloads jobs and notifies the operator (`tests/test_review_followup.py`) |
| PR-024 | Merge callback without job_id still answers Telegram (`tests/test_review_followup.py`) |
| PR-025 | Unauthorized confirmation → `Нет доступа.`, no merge, job not `DONE` |
| PR-026 | Unauthorized cancellation → pending job unchanged |
| PR-027 | Empty allowlist on confirmation → fail-closed |
| PR-028 | Another allowlisted user can confirm a job they did not create |

Automated coverage: `tests/test_pr_review_control.py`, `tests/test_bug_regressions.py`, `tests/test_review_followup.py`, `tests/test_hygiene.py`, `tests/test_task_contract.py`.

## TEST-016 Task Contract gate

| ID | Criterion |
| --- | --- |
| V1 | Empty `/new` → Issue with template, agent not started, job `TASK_ACCEPTED` |
| V2 | Only Goal filled → Missing lists the other required sections, agent not started |
| V3 | All required sections filled → existing Copilot assign path, `CODING_AGENT_RUNNING` |
| V4 | Only Visual References / screenshot → agent not started |
| V5 | Empty Architecture Constraints → agent not started; `N/A` is enough to start |
| V6 | Visual References empty, other required sections filled → agent starts |
| V7 | After filling the Issue, empty `/new` or `issues.edited` starts the agent. A still-incomplete `issues.edited` sends an updated Missing list |
