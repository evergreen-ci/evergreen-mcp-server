---
description: Essential Evergreen CI/CD domain knowledge — hierarchy, terminology, statuses, and how the system works. Read this to understand the data returned by Evergreen tools.
---

# Evergreen CI/CD — Essential Concepts

## What is Evergreen?

Evergreen is MongoDB's distributed Continuous Integration (CI) system. It automates building, testing, and validating code changes across many platforms, operating systems, and configurations. It powers CI/CD for MongoDB Server, Atlas, WiredTiger, and dozens of other internal projects.

---

## Core Hierarchy

Evergreen organizes work in a strict hierarchy. Understanding this is key to interpreting tool results:

```
Project
└── Version (a snapshot at a specific commit)
     └── Build Variant (a platform/config, e.g. "ubuntu2204-64-bit")
          └── Task (a unit of work, e.g. "run_unit_tests")
               └── Test (an individual test case within a task)
```

| Level | What It Is | Example |
|-------|-----------|---------|
| **Project** | A repository or service | mongodb-mongo-master, mms |
| **Version** | A snapshot at a specific commit | Contains all build variants |
| **Build Variant** | Platform + config combination | enterprise-rhel-80-64-bit, ubuntu2204-debug |
| **Task** | A single job: compile, test suite, lint | auth_unit_tests, compile_all |
| **Test** | One test case within a task | TestUserLoginWithExpiredToken |
| **Patch** | Uncommitted changes submitted for CI | Created from PRs, CLI, or UI |

---

## Tasks vs Tests — Critical Distinction

This is the most important distinction to understand:

- A **Task** is a coarse unit of work (like "run the auth test suite"). It has its own host, logs, and status.
- A **Test** is a fine-grained individual test case (like "TestLoginExpired") that runs *within* a task.

A single task can contain hundreds or thousands of tests. When a task "fails", it usually means one or more tests within it failed — but a task can also fail due to compilation errors, timeouts, or setup issues with zero test failures.

| Aspect | Task | Test |
|--------|------|------|
| Granularity | Coarse — a complete job | Fine — a single test case |
| Status values | succeeded, failed, system-failed, timed-out, setup-failed | pass, fail, skip, silentfail |
| Has logs | Yes — task-level logs | Yes — test-specific log lines |
| Identified by | task_id | test_file name |

---

## Task Statuses

Every task in Evergreen has one of these statuses:

| Status | Meaning | Is it a code bug? |
|--------|---------|-------------------|
| **succeeded** | Completed successfully | No |
| **failed** | Code-related failure (test failures, compilation errors) | Usually yes |
| **system-failed** | Infrastructure failure (host died, network issue) | No — retry |
| **setup-failed** | Environment setup failed before tests ran | Maybe — check config |
| **timed-out** | Exceeded configured timeout | Maybe — perf regression or hang |
| **test-timed-out** | A specific test exceeded its timeout | Likely yes |
| **system-timed-out** | Timed out due to system issues | No — retry |

---

## Build Variants

A build variant defines which platform and configuration tasks run under. Examples:
- `enterprise-rhel-80-64-bit` — Enterprise build on RHEL 8, 64-bit
- `ubuntu2204-debug-suggested` — Debug build on Ubuntu 22.04
- `macos-arm64` — macOS on Apple Silicon
- `windows-64-2022` — Windows Server 2022

When a task fails on **one** build variant but passes on others, it's likely a platform-specific issue. When it fails on **all** variants, it's almost certainly a code bug.

---

## Patches

A patch is a set of code changes submitted for CI validation **before** merging. This is what developers interact with most.

**Patch statuses**:
- `created` — Just submitted, scheduling in progress
- `started` — Tasks are running
- `succeeded` — All tasks passed
- `failed` — One or more tasks failed

**Patch types**:
- **CLI Patch** — Submitted via `evergreen patch` command from local machine
- **PR Patch** — Auto-created when a GitHub Pull Request is opened/updated
- **Manual Patch** — Created through the Spruce web UI

**Patch identifiers**: Each patch has a unique `patch_id` (a hex string like `65a1b2c3d4e5f6...`). This ID is used to query failed jobs.

---

## The Waterfall

The waterfall is Evergreen's visualization of mainline (post-merge) commits and their CI status. It shows:
- Each commit as a column
- Each build variant as a row
- Task status as colored cells (green = pass, red = fail, purple = system failure)

When a task fails on the waterfall, Evergreen can perform **stepback** — automatic bisection to find exactly which commit introduced the failure.

---

## Logs

Evergreen has several types of logs:

| Log Type | What It Contains |
|----------|-----------------|
| **Task Logs** | stdout/stderr from task command execution |
| **Test Logs** | Output specific to individual test cases |
| **System Logs** | Agent-level operational logs |
| **Agent Logs** | Evergreen agent process logs |

**Viewing logs**:
- **Spruce** (spruce.mongodb.com) — Shows log tail (~100 lines) in the task view
- **Parsley** — Full log viewer with search, filtering, and AI-powered analysis
- **API / MCP tools** — get_task_logs_evergreen fetches logs programmatically

---

## UI Tools

- **Spruce** (spruce.mongodb.com) — Main CI/CD dashboard. View patches, tasks, test results, waterfall.
- **Parsley** — Dedicated log viewer with AI chat. Can ask "why did this test fail?" in natural language.

Log links returned by get_patch_failed_jobs_evergreen point to these UIs.

---

## Common Project Identifiers

Projects are identified by string IDs like:
- `mongodb-mongo-master` — MongoDB Server (master branch)
- `mms` — MongoDB Atlas / Cloud Manager
- `wiredtiger` — WiredTiger storage engine

Users may work across multiple projects. The get_inferred_project_ids_evergreen tool discovers which projects a user is active in.

---

## Glossary of Terms in API Responses

| Field | Meaning |
|-------|---------|
| `patch_id` | Unique identifier for a patch submission |
| `patch_number` | Sequential number within the project |
| `githash` | Git commit hash the patch is based on |
| `task_id` | Unique identifier for a task execution |
| `build_variant` | Platform/config the task ran on |
| `execution` | Retry count (0 = first run, 1 = first retry) |
| `timeTaken` / `duration_ms` | How long the task took in milliseconds |
| `failed_test_count` | Number of tests that failed within a task |
| `total_test_count` | Total tests executed within a task |
| `test_file` | Name/path of a specific test case |
| `url_parsley` | Link to the Parsley AI log viewer for a test |
| `timed_out` | Whether the failure was due to timeout |
| `failing_command` | Which command in the task definition caused the failure |
