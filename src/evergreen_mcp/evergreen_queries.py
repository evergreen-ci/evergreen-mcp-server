"""GraphQL queries for Evergreen API

This module contains all GraphQL query definitions used by the Evergreen MCP server.
Queries are separated from the client implementation for better maintainability and reusability.
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

# Single project query - retrieves detailed information about a specific project
GET_PROJECT = """
query GetProject($projectId: String!) {
  project(projectIdentifier: $projectId) {
    id
    displayName
    identifier
    enabled
    owner
    repo
    branch
    admins
    banner {
      text
      theme
    }
  }
}
"""

# Project settings query - retrieves configuration settings for a project
GET_PROJECT_SETTINGS = """
query GetProjectSettings($projectId: String!) {
  projectSettings(projectIdentifier: $projectId) {
    projectRef {
      id
      identifier
      displayName
      enabled
      owner
      repo
      branch
    }
    githubWebhooksEnabled
    vars {
      adminOnlyVars {
        key
        value
      }
      privateVars {
        key
        value
      }
      vars {
        key
        value
      }
    }
    aliases {
      alias
      gitTag
      variant
      task
    }
  }
}
"""

# Query to get recent patches for a project
GET_PROJECT_PATCHES = """
query GetProjectPatches($projectId: String!, $limit: Int = 10) {
  project(projectIdentifier: $projectId) {
    patches(patchesInput: {limit: $limit}) {
      patches {
        id
        description
        author
        createTime
        status
        versionFull {
          id
          status
        }
      }
    }
  }
}
"""

# Query to get builds for a project
GET_PROJECT_BUILDS = """
query GetProjectBuilds($projectId: String!) {
  project(projectIdentifier: $projectId) {
    id
    displayName
    # Note: This would need to be adjusted based on actual schema structure
    # The merged-schema.graphql should be consulted for exact field names
  }
}
"""

# Get recent patches for the authenticated user
GET_USER_RECENT_PATCHES = """
query GetUserRecentPatches($userId: String!, $limit: Int = 10) {
  user(userId: $userId) {
    patches(patchesInput: {
      limit: $limit
      page: 0
      patchName: ""
      statuses: []
      includeHidden: false
    }) {
      patches {
        id
        githash
        description
        author
        authorDisplayName
        status
        createTime
        patchNumber
        projectIdentifier
        versionFull {
          id
          status
        }
      }
    }
  }
}
"""

# Get failed tasks for a specific patch
GET_PATCH_FAILED_TASKS = """
query GetPatchFailedTasks($patchId: String!) {
  patch(patchId: $patchId) {
    id
    githash
    description
    author
    authorDisplayName
    status
    createTime
    patchNumber
    projectIdentifier
    versionFull {
      id
      revision
      author
      createTime
      status
      tasks(options: {
        statuses: ["failed", "system-failed", "task-timed-out"]
        limit: 100
      }) {
        count
        data {
          id
          displayName
          buildVariant
          status
          execution
          finishTime
          timeTaken
          details {
            description
            status
            timedOut
            timeoutType
            failingCommand
          }
          logs {
            taskLogLink
            agentLogLink
            systemLogLink
            allLogLink
          }
        }
      }
    }
  }
}
"""

# Get version with failed tasks (simplified)
GET_VERSION_WITH_FAILED_TASKS = """
query GetVersionWithFailedTasks($versionId: String!) {
  version(versionId: $versionId) {
    id
    revision
    author
    createTime
    status
    tasks(options: {
      statuses: ["failed", "system-failed", "task-timed-out"]
      limit: 100
    }) {
      count
      data {
        id
        displayName
        buildVariant
        status
        execution
        finishTime
        timeTaken
        details {
          description
          status
          timedOut
          timeoutType
          failingCommand
        }
        logs {
          taskLogLink
          agentLogLink
          systemLogLink
          allLogLink
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
