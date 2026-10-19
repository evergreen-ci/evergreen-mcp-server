"""GraphQL queries for Evergreen API

This module contains all GraphQL query definitions used by the Evergreen MCP server.
Queries are separated from the client implementation for better maintainability
and reusability.
"""

# Projects query - retrieves all projects grouped by organization
GET_PROJECTS = """
query GetProjects {
  projects {
    groupDisplayName
    projects {
      id
      displayName
      identifier
      enabled
      owner
      repo
      branch
    }
  }
}
"""

# Get recent patches for the authenticated user (with pagination)
GET_USER_RECENT_PATCHES = """
query GetUserRecentPatches($userId: String!, $limit: Int = 10, $page: Int = 0) {
  user(userId: $userId) {
    patches(patchesInput: {
      limit: $limit
      page: $page
      patchName: ""
      statuses: []
      includeHidden: false
    }) {
      patches {
        id
        githash
        description
        user {
          id
          displayName
        }
        githubPatchData {
          author
        }
        status
        createTime
        patchNumber
        projectMetadata {
          identifier
        }
        version {
          id
          status
        }
      }
    }
  }
}
"""

# Compact status-poll query for a patch: server-side task status counts plus
# only the failing tasks' identity and failure description. Deliberately omits
# host metadata, log links and test counts — see status_summary_tools.py.
# Powers get_patch_status_summary_evergreen.
GET_PATCH_STATUS_SUMMARY = """
query GetPatchStatusSummary(
  $patchId: String!
  $failedStatuses: [String!]!
  $failedLimit: Int = 50
) {
  patch(patchId: $patchId) {
    id
    description
    status
    projectMetadata {
      identifier
    }
    version {
      id
      status
      startTime
      finishTime
      taskStatusStats {
        counts {
          status
          count
        }
        eta
      }
      tasks(options: { statuses: $failedStatuses, limit: $failedLimit }) {
        count
        data {
          id
          displayName
          buildVariant
          displayStatus
          execution
          details {
            description
            timedOut
            timeoutType
            failingCommand
          }
        }
      }
    }
  }
}
"""

# Same compact status-poll shape as GET_PATCH_STATUS_SUMMARY, addressed by
# version_id instead — for trigger_request and other non-patch versions.
GET_VERSION_STATUS_SUMMARY = """
query GetVersionStatusSummary(
  $versionId: String!
  $failedStatuses: [String!]!
  $failedLimit: Int = 50
) {
  version(versionId: $versionId) {
    id
    status
    startTime
    finishTime
    projectMetadata {
      identifier
    }
    taskStatusStats {
      counts {
        status
        count
      }
      eta
    }
    tasks(options: { statuses: $failedStatuses, limit: $failedLimit }) {
      count
      data {
        id
        displayName
        buildVariant
        displayStatus
        execution
        details {
          description
          timedOut
          timeoutType
          failingCommand
        }
      }
    }
  }
}
"""

# Get failed tasks for a specific patch
GET_PATCH_TASKS = """
query GetPatchTasks($patchId: String!, $statuses: [String!]!, $limit: Int = 100) {
  patch(patchId: $patchId) {
    id
    githash
    description
    user {
      id
      displayName
    }
    githubPatchData {
      author
    }
    status
    createTime
    patchNumber
    projectMetadata {
      identifier
    }
    version {
      id
      revision
      author
      createTime
      status
      tasks(options: {
        statuses: $statuses
        limit: $limit
      }) {
        count
        data {
          id
          displayName
          buildVariant
          displayStatus
          execution
          finishTime
          timeTaken
          hasTestResults
          failedTestCount
          totalTestCount
          ami
          hostId
          distroId
          imageId
          details {
            description
            status
            timedOut
            timeoutType
            failingCommand
          }
        }
      }
    }
  }
}
"""

# Get version with tasks filtered by status
GET_VERSION_TASKS = """
query GetVersionTasks($versionId: String!, $statuses: [String!]!, $limit: Int = 100) {
  version(versionId: $versionId) {
    id
    revision
    author
    createTime
    status
    tasks(options: {
      statuses: $statuses
      limit: $limit
    }) {
      count
      data {
        id
        displayName
        buildVariant
        displayStatus
        execution
        finishTime
        timeTaken
        hasTestResults
        failedTestCount
        totalTestCount
        ami
        hostId
        distroId
        imageId
        details {
          description
          status
          timedOut
          timeoutType
          failingCommand
        }
      }
    }
  }
}
"""

# Get detailed logs for a specific task
GET_TASK_LOGS = """
query GetTaskLogs($taskId: String!, $execution: Int!) {
  task(taskId: $taskId, execution: $execution) {
    id
    displayName
    execution
    ami
    hostId
    distroId
    imageId
    taskLogs {
      taskId
      execution
      taskLogs {
        severity
        message
        timestamp
        type
      }
    }
  }
}
"""

# Get detailed test results for a specific task
GET_TASK_TEST_RESULTS = """
query GetTaskTestResults(
  $taskId: String!,
  $execution: Int!,
  $testFilterOptions: TestFilterOptions
) {
  task(taskId: $taskId, execution: $execution) {
    id
    displayName
    buildVariant
    displayStatus
    execution
    hasTestResults
    failedTestCount
    totalTestCount
    ami
    hostId
    distroId
    imageId
    tests(opts: $testFilterOptions) {
      totalTestCount
      filteredTestCount
      testResults {
        id
        status
        testFile
        duration
        startTime
        endTime
        exitCode
        groupID
        logs {
          url
          urlParsley
          urlRaw
          lineNum
          renderingType
          version
        }
      }
    }
  }
}
"""

# Get the project waterfall (build variants × versions grid).
# Powers get_waterfall_summary_evergreen, get_waterfall_detailed_evergreen,
# and list_project_build_variants_evergreen.
GET_WATERFALL = """
query GetWaterfall($options: WaterfallOptions!) {
  waterfall(options: $options) {
    versions {
      id
      revision
      user {
        displayName
      }
      message
      createTime
      order
      activated
      requester
      status
      waterfallBuilds {
        id
        activated
        buildVariant
        displayName
        version
        tasks {
          id
          displayName
          displayStatusCache
          execution
        }
      }
    }
    pagination {
      activeVersionIds
      hasNextPage
      hasPrevPage
      mostRecentVersionOrder
      nextPageOrder
      prevPageOrder
    }
  }
}
"""


# Lean version-only query for change-point / regression analysis.
# Powers get_mainline_commits_between_evergreen — drops waterfallBuilds and
# unused pagination cursors so the payload stays small for wide order ranges.
GET_MAINLINE_COMMITS = """
query GetMainlineCommits($options: WaterfallOptions!) {
  waterfall(options: $options) {
    versions {
      id
      revision
      user {
        displayName
      }
      message
      createTime
      order
      activated
      requester
    }
    pagination {
      hasNextPage
      nextPageOrder
      mostRecentVersionOrder
    }
  }
}
"""


# Schedule (activate) previously-unscheduled tasks on a version.
# Used by schedule_tasks_evergreen to flip task_ids found via the waterfall
# (status="unscheduled") into the run queue. Returns the resulting Task
# entities so the caller can confirm what was actually scheduled — Evergreen
# silently drops IDs it can't act on (already finished, wrong version,
# missing TASKS:EDIT permission).
SCHEDULE_TASKS = """
mutation ScheduleTasks($versionId: String!, $taskIds: [String!]!) {
  scheduleTasks(versionId: $versionId, taskIds: $taskIds) {
    id
    displayName
    buildVariant
    status
    execution
    activated
  }
}
"""


# Read a patch's current configuration relevant to reconfiguring it:
# its description, parameters, and the variant/task pairs already selected.
# Used by configure_patch_evergreen to build the full union before calling
# schedulePatch (which replaces, rather than merges, `variantsTasks`).
# NOTE: the output `VariantTask` type exposes only `name` and `tasks` (no
# display tasks), so display-task selections can't be read back or preserved.
GET_PATCH_CONFIGURE = """
query GetPatchConfigure($patchId: String!) {
  patch(patchId: $patchId) {
    id
    description
    status
    activated
    variantsTasks {
      name
      tasks
    }
    parameters {
      key
      value
    }
  }
}
"""


# Reconfigure an existing patch (the mutation Spruce's "Configure Patch" fires).
# On an already-finalized patch this creates any new variant's build/tasks —
# reusing existing compile artifacts — and activates them, without removing
# what's already scheduled. `configure.variantsTasks` REPLACES the patch's
# recorded selection, so callers must pass the full union. Requires
# PATCHES:EDIT permission on the project.
SCHEDULE_PATCH = """
mutation SchedulePatch($patchId: String!, $configure: PatchConfigure!) {
  schedulePatch(patchId: $patchId, configure: $configure) {
    id
    status
    activated
    variantsTasks {
      name
      tasks
    }
  }
}
"""


# Restart a single task. `failedOnly` only affects display tasks (restart
# only failed execution tasks vs all of them). Requires TASKS:EDIT.
RESTART_TASK = """
mutation RestartTask($taskId: String!, $failedOnly: Boolean!) {
  restartTask(taskId: $taskId, failedOnly: $failedOnly) {
    id
    displayName
    buildVariant
    status
    execution
    activated
  }
}
"""


# Restart tasks on one or more versions. Passing an empty `taskIds` list for
# a version restarts all completed tasks on it. `abort=true` additionally
# aborts any in-progress tasks and queues them for reset. Requires TASKS:EDIT.
RESTART_VERSIONS = """
mutation RestartVersions(
  $versionId: String!,
  $abort: Boolean!,
  $versionsToRestart: [VersionToRestart!]!
) {
  restartVersions(
    versionId: $versionId,
    abort: $abort,
    versionsToRestart: $versionsToRestart
  ) {
    id
    status
    activated
  }
}
"""


# Get inferred project identifiers from user's patches
GET_INFERRED_PROJECT_IDS = """
query InferredProjectIds($userId: String!, $limit: Int = 50, $page: Int = 0) {
  user(userId: $userId) {
    patches(
      patchesInput: {
        limit: $limit
        page: $page
        includeHidden: false
        patchName: ""
        statuses: []
      }
    ) {
      patches {
        id
        createTime
        projectMetadata {
          identifier
        }
      }
    }
  }
}
"""


# Get distro event log entries (used to detect recent AMI changes on a distro).
# `before`/`after` are generic Map (serialized distro documents); the AMI lives
# inside the provider settings list. Requires DistroSettingsView permission on
# the distro — the API returns a GraphQL error (not an empty result) otherwise.
GET_DISTRO_EVENTS = """
query DistroEvents($opts: DistroEventsInput!) {
  distroEvents(opts: $opts) {
    count
    eventLogEntries {
      timestamp
      user
      before
      after
      data
    }
  }
}
"""
