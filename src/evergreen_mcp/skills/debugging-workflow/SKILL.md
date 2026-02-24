---
description: Step-by-step workflows for debugging CI/CD failures in Evergreen — from identifying a failing patch to diagnosing root cause.
---

# Debugging CI/CD Failures in Evergreen

## Overview

When a user's CI/CD patch fails in Evergreen, follow this systematic workflow to identify the root cause. The process moves from broad (which patch?) to narrow (which line of code?).

---

## Workflow: Full Failure Investigation

### Step 1: Identify the Project

If you don't already know the user's Evergreen project:

```
Call: get_inferred_project_ids_evergreen(max_patches=50)
```

- **1 project returned** → use it, no need to ask
- **Multiple projects** → ask the user: "I found activity in X (15 patches) and Y (3 patches). Which project should I check?"
- **0 projects** → the user has no recent patches

### Step 2: Find the Failing Patch

```
Call: list_recent_patches(project_id="...", limit=10)
```

Present a summary to the user:
- Count by status: "5 patches: 3 succeeded, 1 failed, 1 running"
- Highlight the failed ones with their descriptions
- Ask which one to investigate if there are multiple failures

**Key**: The `patch_id` field from the failed patch is what you need for the next step.

### Step 3: Get Failed Tasks

```
Call: get_patch_failures(patch_id="...")
```

This returns all failed tasks for the patch. Classify each failed task:

| Condition | Failure Type | Next Action |
|-----------|-------------|-------------|
| `failed_test_count > 0` | Test failure | → Step 4a (test results) |
| `failure_details.timed_out == true` | Timeout | → Step 4b (logs) |
| `failed_test_count == 0`, no timeout | Setup/infra failure | → Step 4b (logs) |

**Tip**: If the same task fails on multiple build variants, it's almost certainly a real code issue. If it fails on only one variant, it might be platform-specific.

### Step 4a: Investigate Test Failures

**Quick triage** — see which tests failed:
```
Call: get_test_results_summary(task_id="...", failed_only=True)
```

This gives you the exact test names that failed. Present them to the user:
- "3 tests failed in `auth_unit_tests`: `test_login_expired_token`, `test_session_refresh`, `test_oauth_callback`"
- Look for patterns in test names (all auth-related? all in the same directory?)
- Share Parsley links (`url_parsley`) for the user to explore logs visually

**Root cause** — get actual error messages from test output:
```
Call: get_test_results_detailed(task_id="...", job_name="Job0")
```

The actual test log content is stored in S3 and is NOT accessible through the GraphQL API. This tool fetches the raw test output via the REST API and scans for error patterns (panic, fatal, exception, stack traces, etc.). Returns categorized error counts with example lines so you can pinpoint the failure.

### Step 4b: Investigate Task Logs

**Note**: The GraphQL task log summary is truncated and mostly shows test log ingestion messages. For most debugging, go directly to the full logs.

**Quick check** (optional) — see if GraphQL has useful error messages:
```
Call: get_task_log_summary(task_id="...", filter_errors=True)
```

**Full raw logs** — get the complete, untruncated task execution log:
```
Call: get_task_log_detailed(task_id="...", execution_retries=0)
```

This fetches the full task log via REST API, including timeout handler output, process dumps, and complete stdout/stderr that the GraphQL summary cannot access. Scan the output for:
- The first `error` or `fatal` message — subsequent errors often cascade from it
- Stack traces, assertion failures, or error codes
- Timeout indicators, OOM messages, or infrastructure errors

### Step 5: Synthesize and Report

Present a clear diagnosis to the user:

**Good example**:
> Your patch "SERVER-12345: Fix auth bug" has 5 failed tasks across 2 build variants.
>
> **Root cause**: 3 test failures in `auth_unit_tests` — all related to token expiration logic:
> - `test_login_expired_token` — expects 401, gets 200
> - `test_session_refresh` — refresh token not being invalidated
> - `test_oauth_callback` — callback handler missing expiry check
>
> These all point to the token expiration changes in your patch. The expiry validation may not be triggering correctly.

**Bad example**:
> Your patch failed. There are 5 failed tasks.

---

## Workflow: Quick CI Health Check

For a fast "is everything green?" check:

```
1. get_inferred_project_ids_evergreen()
2. list_recent_patches(project_id="...", limit=3)
3. Report: "Your last 3 patches: ✓ SERVER-111, ✓ SERVER-222, ✗ SERVER-333"
```

Only drill deeper if the user asks.

---

## Workflow: Investigating a Specific Task

When the user already has a task_id (e.g., from a Spruce/Parsley URL):

```
1. get_test_results_summary(task_id="...", failed_only=True)
   → See which tests failed (names, statuses, Parsley URLs)
2. get_test_results_detailed(task_id="...", job_name="Job0")
   → Get actual error messages from raw test output (stored in S3)
3. get_task_log_detailed(task_id="...")
   → Get full raw task execution log (for non-test failures)
```

Note: `get_task_log_summary` is optional — it returns truncated content. For most debugging, skip directly to `get_task_log_detailed`.

---

## Failure Classification Guide

Use this to categorize failures and advise the user:

### Test Failures (most common)
- **Indicator**: `failed_test_count > 0`
- **Cause**: Code being tested doesn't produce expected results
- **User action**: Fix the code. Look at test names for clues about what's wrong.
- **Pattern**: If tests pass locally but fail in CI, check for environment differences (different OS, compiler flags, timing-sensitive tests).

### Timeout Failures
- **Indicator**: `failure_details.timed_out == true`
- **Cause**: Task or test exceeded allocated time
- **User action**: Optimize slow tests/code, or check for infinite loops/deadlocks
- **Pattern**: If timeout is new to this patch, the code change likely introduced a performance regression or hang.

### System Failures
- **Indicator**: No test failures, status is "system-failed"
- **Cause**: Infrastructure problems — network outages, host crashes, EC2 issues
- **User action**: Usually just retry. Not a code problem.
- **Pattern**: Sporadic, not reproducible. If it persists across retries, escalate.

### Setup Failures
- **Indicator**: Status is "setup-failed", task failed before any tests ran
- **Cause**: Environment preparation failed — dependency install, config error
- **User action**: Check setup commands. May be a transient issue (retry first).
- **Pattern**: If consistent, something changed in the build environment or dependencies.

### Compilation Errors
- **Indicator**: Compile task failed, no test results
- **Cause**: Syntax errors, missing includes, type errors
- **User action**: Fix the compilation error shown in logs.
- **Pattern**: Check logs for the first error — subsequent errors are often cascading.

---

## Tips for Effective Diagnosis

1. **Start broad, narrow down**: Patch → Tasks → Tests → Logs. Don't jump to logs first.
2. **Look for patterns across variants**: Same failure everywhere = code bug. One variant only = platform issue.
3. **Check the failure count**: 1-3 failed tests is usually a focused bug. 50+ failures often means a build/setup issue that cascaded.
4. **Use the right tool for the failure type**: Test results for test failures, logs for everything else.
5. **Summary for triage, detailed for root cause**: Use `get_test_results_summary` to see which tests failed (names, statuses). Use `get_test_results_detailed` to see WHY they failed (actual error messages from S3 logs). For task logs, prefer `get_task_log_detailed` directly — the GraphQL summary is truncated and often insufficient.
6. **Execution numbers**: If a task was retried, `execution=0` is the first run, `execution=1` is the retry. Check both if the failure is intermittent.
