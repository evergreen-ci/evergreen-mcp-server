---
description: Complete reference for every Evergreen MCP tool — what it does, when to use it, parameters, return values, and how results chain into other tools.
---

# Evergreen MCP Tools — Complete Reference

## Tool Decision Tree

Use this to pick the right tool:

```
User wants to...
│
├─ See their recent CI patches/commits
│   └─ list_user_recent_patches_evergreen
│
├─ Understand why a patch is failing
│   └─ get_patch_failed_jobs_evergreen (needs patch_id)
│
├─ See which specific tests failed in a task
│   └─ get_task_test_results_evergreen (needs task_id)
│
├─ Read error logs from a failed task
│   └─ get_task_logs_evergreen (needs task_id)
│
├─ Know which Evergreen projects they work on
│   └─ get_inferred_project_ids_evergreen
│
└─ Don't know their project_id
    └─ get_inferred_project_ids_evergreen FIRST
        then use the project_id in subsequent calls
```

---

## Tool 1: get_inferred_project_ids_evergreen

**Purpose**: Discover which Evergreen projects the user is active in by scanning recent patch history. Returns project identifiers sorted by activity.

**When to Use**:
- Always call this first if you don't know the user's project_id
- When the user says "check my CI" without specifying a project
- When you need to present the user with their active projects

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| max_patches | int | 50 | Number of recent patches to scan. 20-50 for quick discovery. |

**Return shape**:
```json
{
  "user_id": "user@mongodb.com",
  "projects": [
    { "project_identifier": "mongodb-mongo-master", "patch_count": 15, "latest_patch_time": "2025-01-15T10:30:00Z" }
  ],
  "total_projects": 2,
  "patches_scanned": 50
}
```

**What to do with results**:
- **1 project** → Use that project_identifier for all subsequent calls. No need to ask the user.
- **Multiple projects** → Tell the user which projects you found and ask which one. Example: "I found activity in mongodb-mongo-master (15 patches) and mms (3 patches). Which project?"
- **0 projects** → The user has no recent patches. Let them know.

---

## Tool 2: list_user_recent_patches_evergreen

**Purpose**: List the authenticated user's recent patches with their CI/CD status. This is the starting point for most workflows.

**When to Use**:
- User asks "what are my recent patches?" or "is my CI passing?"
- User asks "check my latest build"
- You need a patch_id to investigate failures
- You want an overview of CI activity

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| project_id | str | auto-detect | Evergreen project identifier (e.g., "mongodb-mongo-master", "mms"). Required — pass explicitly or let auto-detection handle it. |
| limit | int | 10 | Number of patches. 3-5 for quick check, 10-20 for full overview. Max 50. |

**Return shape**:
```json
{
  "user_id": "user@mongodb.com",
  "project_id": "mongodb-mongo-master",
  "patches": [
    {
      "patch_id": "65a1b2c3d4e5f6...",
      "description": "SERVER-12345: Fix authentication bug",
      "status": "failed",
      "version_status": "failed",
      "create_time": "2025-01-15T10:30:00Z",
      "project_identifier": "mongodb-mongo-master"
    }
  ],
  "count": 5
}
```

**Key fields**:
- `status` — Overall patch status: created, started, succeeded, failed
- `version_status` — CI version status (may differ from patch status)
- `patch_id` — Use this to drill into failures with get_patch_failed_jobs_evergreen

**What to do with results**:
1. Summarize: "You have 5 recent patches. 3 succeeded, 1 failed, 1 is running."
2. If any patch has status "failed", offer to investigate: "Your patch SERVER-12345 is failing. Want me to check what's wrong?"
3. Use the patch_id from failed patches to call get_patch_failed_jobs_evergreen

**Auto-detection behavior** (when project_id omitted):
- High confidence (1 project): Uses it automatically
- Low confidence (multiple projects): Returns patches but includes a warning — relay alternatives to the user
- No match: Returns `user_selection_required` with available projects — ask the user to pick

---

## Tool 3: get_patch_failed_jobs_evergreen

**Purpose**: Analyze why a patch is failing. Returns all failed tasks with failure details, build variants, timeout info, log links, and test failure counts.

**When to Use**:
- After finding a failed patch via list_user_recent_patches_evergreen
- User asks "why is my patch failing?" or "what's broken?"
- You need task_id values to dig deeper

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| patch_id | str | **required** | Patch ID from list_user_recent_patches results |
| project_id | str or None | auto-detect | Optional project identifier for validation |
| max_results | int | 50 | Max failed tasks. 10-20 focused, 50+ comprehensive. |

**Return shape**:
```json
{
  "patch_info": { "description": "SERVER-12345: Fix auth", "status": "failed" },
  "failed_tasks": [
    {
      "task_id": "task_abc123",
      "task_name": "auth_unit_tests",
      "build_variant": "enterprise-rhel-80-64-bit",
      "status": "failed",
      "execution": 0,
      "failure_details": {
        "description": "test failures",
        "timed_out": false,
        "failing_command": "subprocess.exec"
      },
      "test_info": {
        "has_test_results": true,
        "failed_test_count": 3,
        "total_test_count": 150
      },
      "logs": { "task_log": "https://...", "all_logs": "https://..." }
    }
  ],
  "summary": {
    "total_failed_tasks": 5,
    "has_timeouts": false,
    "failed_build_variants": ["enterprise-rhel-80-64-bit", "ubuntu2204"]
  }
}
```

**Key fields**:
- `summary.total_failed_tasks` — How many tasks failed overall
- `summary.has_timeouts` — Whether any failures are timeout-related
- `failed_tasks[].test_info.failed_test_count` — If > 0, there are specific test failures
- `failed_tasks[].failure_details.timed_out` — Whether this task timed out
- `failed_tasks[].task_id` — Use this to drill into logs or test results

**What to do with results**:
1. Summarize: "Your patch has 5 failed tasks across 2 build variants."
2. Categorize failures:
   - `failed_test_count > 0` → Test failures (most common, most actionable)
   - `timed_out: true` → Timeout issues
   - `failed_test_count == 0` and no timeout → Infrastructure/setup failure
3. For test failures → call get_task_test_results_evergreen(task_id)
4. For other failures → call get_task_logs_evergreen(task_id)
5. Look for patterns: same task failing across multiple variants = likely real code issue. Fails on one variant only = possibly platform-specific.

---

## Tool 4: get_task_test_results_evergreen

**Purpose**: Get the specific test cases that failed within a task. Shows individual test names, statuses, durations, and links to test-specific logs.

**When to Use**:
- After get_patch_failed_jobs_evergreen shows a task with failed_test_count > 0
- User asks "which tests are failing?"
- You need specific test names to help the user fix their code

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| task_id | str | **required** | Task ID from get_patch_failed_jobs results |
| execution | int | 0 | Execution number (0 = first run, 1+ = retries) |
| failed_only | bool | True | Only show failed tests (recommended) |
| limit | int | 100 | Max test results to return |

**Return shape**:
```json
{
  "task_info": {
    "task_name": "auth_unit_tests",
    "build_variant": "enterprise-rhel-80-64-bit",
    "failed_test_count": 3,
    "total_test_count": 150
  },
  "test_results": [
    {
      "test_file": "jstests/auth/test_login_expired_token.js",
      "status": "fail",
      "duration": 5.2,
      "logs": { "url": "https://...", "url_parsley": "https://..." }
    }
  ],
  "summary": { "returned_tests": 3, "failed_tests_in_results": 3 }
}
```

**Key fields**:
- `test_results[].test_file` — The failing test name. This tells the user exactly what to look at.
- `test_results[].logs.url_parsley` — Link to AI-powered log viewer for this specific test.

**What to do with results**:
1. List the failing tests: "3 tests failed: test_login_expired_token, test_session_refresh, test_oauth_callback"
2. If test names suggest a pattern, point it out: "All 3 failures are auth-related — likely connected to your changes."
3. Share Parsley links if available for deeper analysis
4. If you need more detail, call get_task_logs_evergreen on the same task_id

---

## Tool 5: get_task_logs_evergreen

**Purpose**: Fetch raw log output from a task execution. By default filters for error/failure messages to surface what went wrong.

**When to Use**:
- Tasks without test results (setup failures, timeouts, compilation errors)
- When test results don't explain why a test failed
- User asks "show me the error logs"
- Investigating timeout or infrastructure failures

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| task_id | str | **required** | Task ID from get_patch_failed_jobs results |
| execution | int | 0 | Execution number (0 = first run, 1+ = retries) |
| max_lines | int | 1000 | Max log lines. 100-500 for quick scan, 1000+ for comprehensive. |
| filter_errors | bool | True | Only error/failure lines. Set False for full output. |

**Return shape**:
```json
{
  "task_id": "task_abc123",
  "task_name": "compile_all",
  "total_lines": 45,
  "logs": [
    { "timestamp": "2025-01-15T10:35:00Z", "severity": "error", "message": "fatal error: cannot find header file 'auth.h'" }
  ],
  "truncated": false
}
```

**Key fields**:
- `logs[].severity` — "error" and "fatal" are most important
- `logs[].message` — The actual error text for diagnosis
- `truncated` — If true, increase max_lines

**What to do with results**:
1. Find the root cause: usually the first error/fatal message
2. Summarize: "Compilation failed — header file 'auth.h' not found."
3. Categorize:
   - Compilation errors → missing files, syntax, dependencies
   - "OOM" / memory → resource constraints
   - Network/connection → infrastructure, retry may help
   - "timeout" / "heartbeat" → task took too long
   - Setup/install → environment configuration
4. If filter_errors=True gives too few results, retry with filter_errors=False

---

## Common Tool Chains

### "Check my CI status" (Quick Overview)
```
get_inferred_project_ids_evergreen
→ list_user_recent_patches_evergreen(project_id=..., limit=5)
→ Present summary
```

### "Why is my patch failing?" (Full Investigation)
```
list_user_recent_patches_evergreen(project_id=..., limit=5)
→ Find failed patch → get patch_id
→ get_patch_failed_jobs_evergreen(patch_id=...)
→ For tasks with failed_test_count > 0:
    → get_task_test_results_evergreen(task_id=...)
→ For tasks without test results:
    → get_task_logs_evergreen(task_id=...)
→ Synthesize findings and present root cause
```

### "Which tests are failing?" (Test Focus)
```
get_patch_failed_jobs_evergreen(patch_id=...)
→ Find tasks with failed_test_count > 0
→ get_task_test_results_evergreen(task_id=...)
→ List failing test names and patterns
```

### "Show me the error logs" (Direct Log Access)
```
get_task_logs_evergreen(task_id=..., filter_errors=True)
→ If not enough context: retry with filter_errors=False
→ Present error summary
```
