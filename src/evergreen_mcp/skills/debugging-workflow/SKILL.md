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
Call: list_user_recent_patches_evergreen(project_id="...", limit=10)
```

Present a summary to the user:
- Count by status: "5 patches: 3 succeeded, 1 failed, 1 running"
- Highlight the failed ones with their descriptions
- Ask which one to investigate if there are multiple failures

**Key**: The `patch_id` field from the failed patch is what you need for the next step.

### Step 3: Get Failed Tasks

```
Call: get_patch_failed_jobs_evergreen(patch_id="...")
```

This returns all failed tasks for the patch. Classify each failed task:

| Condition | Failure Type | Next Action |
|-----------|-------------|-------------|
| `failed_test_count > 0` | Test failure | → Step 4a (test results) |
| `failure_details.timed_out == true` | Timeout | → Step 4b (logs) |
| `failed_test_count == 0`, no timeout | Setup/infra failure | → Step 4b (logs) |

**Tip**: If the same task fails on multiple build variants, it's almost certainly a real code issue. If it fails on only one variant, it might be platform-specific.

### Step 4a: Investigate Test Failures

```
Call: get_task_test_results_evergreen(task_id="...", failed_only=True)
```

This gives you the exact test names that failed. Present them to the user:
- "3 tests failed in `auth_unit_tests`: `test_login_expired_token`, `test_session_refresh`, `test_oauth_callback`"
- Look for patterns in test names (all auth-related? all in the same directory?)
- Share Parsley links (`url_parsley`) for the user to explore logs visually

If you need to understand *why* a test failed (not just *which*), proceed to Step 4b with the same task_id.

### Step 4b: Investigate Logs

```
Call: get_task_logs_evergreen(task_id="...", filter_errors=True)
```

Scan the returned error logs for root cause:
- The first `error` or `fatal` severity message is usually the trigger
- Look for stack traces, assertion failures, or error codes

If `filter_errors=True` returns too few or no results (the actual error might not contain "error" in its text), retry:

```
Call: get_task_logs_evergreen(task_id="...", filter_errors=False, max_lines=500)
```

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
2. list_user_recent_patches_evergreen(project_id="...", limit=3)
3. Report: "Your last 3 patches: ✓ SERVER-111, ✓ SERVER-222, ✗ SERVER-333"
```

Only drill deeper if the user asks.

---

## Workflow: Investigating a Specific Task

When the user already has a task_id (e.g., from a Spruce/Parsley URL):

```
1. get_task_test_results_evergreen(task_id="...", failed_only=True)
   → If tests failed, report them
2. get_task_logs_evergreen(task_id="...", filter_errors=True)
   → Report error messages
```

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
5. **When retrying log fetches**: Start with `filter_errors=True` (fast, focused). Fall back to `filter_errors=False` if needed.
6. **Execution numbers**: If a task was retried, `execution=0` is the first run, `execution=1` is the retry. Check both if the failure is intermittent.
