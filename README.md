# Evergreen MCP Server

A Model Context Protocol (MCP) server that provides access to the Evergreen CI/CD platform API. This server enables AI assistants and other MCP clients to interact with Evergreen projects, builds, tasks, and other CI/CD resources.

## Overview

[Evergreen](https://github.com/evergreen-ci/evergreen) is MongoDB's continuous integration platform. This MCP server exposes Evergreen's functionality through the Model Context Protocol, allowing AI assistants to help with CI/CD operations, project management, and build analysis.

<p align="center">
<img width="608" height="310" src="https://github.com/user-attachments/assets/c6961ef5-5ab5-450f-a8da-dbaa4253bab7" />
</p>

## Features

- **Project Resources**: Access and list Evergreen projects and build statuses
- **Failed Jobs Analysis**: Fetch failed jobs and logs for specific commits to help identify CI/CD failures
- **Unit Test Failure Analysis**: Detailed analysis of individual unit test failures with test-specific logs and metadata
- **Task Log Retrieval**: Get detailed logs for failed tasks with error filtering
- **Stepback Analysis**: Find failed mainline tasks that have undergone stepback bisection
- **Authentication**: Secure OIDC-based authentication via `evergreen login`
- **Async Operations**: Built on asyncio for efficient concurrent operations
- **GraphQL Integration**: Uses Evergreen's GraphQL API for efficient data retrieval

## Quick Start

### Step 1: Authenticate with Evergreen

First, authenticate with Evergreen using the CLI. This creates the necessary credentials that the MCP server will use:

```bash
evergreen login
```

This will:
- Open your browser for OIDC authentication
- Create `~/.evergreen.yml` with your credentials
- Create `~/.kanopy/token-oidclogin.json` with your OIDC token

> **Note**: If you don't have the Evergreen CLI installed, see [Evergreen CLI Installation](https://github.com/evergreen-ci/evergreen/wiki/Using-the-Command-Line-Tool#installation).

### Step 2: Configure Your MCP Client

Add the Evergreen MCP server to your AI assistant's MCP configuration.

#### Cursor IDE

Add to your Cursor MCP settings (`.cursor/mcp.json` or Settings → MCP):

```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

#### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

#### VS Code with MCP Extension

Add to your VS Code settings (`settings.json`):

```json
{
  "mcp.servers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${userHome}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${userHome}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

### Step 3: Start Using It

Once configured, you can ask your AI assistant questions like:
- "Show me my recent Evergreen patches"
- "What failed in my last patch?"
- "Get the logs for this failing task"
- "Find stepback failures in the mms project"

That's it! The server will use your `evergreen login` credentials automatically.

> **Note:** Telemetry is disabled by default to help improve reliability. To disable it, add `-e SENTRY_ENABLED=true` to the Docker args. See [Telemetry](#telemetry) for details.

---

## Alternative Setup Methods

### Using API Keys (Legacy)

If you can't use OIDC authentication, you can use API keys instead:

1. Get your API key from Evergreen (User Settings → API Key)
2. Configure your MCP client:

```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "EVERGREEN_USER=your_username",
        "-e", "EVERGREEN_API_KEY=your_api_key",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

### Local Development Setup

For development or if you prefer not to use Docker:

1. **Clone and install:**
   ```bash
   git clone https://github.com/evergreen-ci/evergreen-mcp-server.git
   cd evergreen-mcp-server
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e .
   ```

2. **Configure your MCP client to use the local installation:**
   ```json
   {
     "mcpServers": {
       "evergreen": {
         "command": "/path/to/evergreen-mcp-server/.venv/bin/evergreen-mcp-server",
         "args": []
       }
     }
   }
   ```

---

## Running the Server (Detailed)

The Evergreen MCP server is designed to be used with MCP clients and communicates via stdio by default. This section covers all the ways you can run the server.

### Understanding MCP Server Architecture

The MCP server operates as a **subprocess** spawned by your AI assistant (like Cursor, Claude Desktop, etc.). The assistant communicates with the server through standard input/output (stdio), sending JSON-RPC messages back and forth.

**Key concepts:**
- **stdio transport**: The server reads from stdin and writes to stdout (default)
- **HTTP transports**: Alternative transports (SSE, streamable-http) for when stdio isn't available
- **Lifespan management**: The client (your AI assistant) manages starting/stopping the server

### Method 1: Docker with OIDC (Recommended)

This is the most secure and easiest approach for most users.

**Prerequisites:**
- Docker installed and running
- Evergreen CLI installed (`evergreen login` completed)

**Configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

**With project configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "-e", "EVERGREEN_PROJECT=mongodb-mongo-master",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

### Method 2: Docker with API Keys

For environments where OIDC isn't available or when using service accounts.

**When to use:**
- Kubernetes/cloud deployments
- CI/CD pipelines
- Service accounts
- Environments where file mounting is difficult

**Configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "EVERGREEN_USER=your_username",
        "-e", "EVERGREEN_API_KEY=your_api_key",
        "-e", "EVERGREEN_PROJECT=mongodb-mongo-master",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

**⚠️ Security considerations:**
- API keys in environment variables are visible in process lists
- Consider using credential management systems in production
- Rotate API keys regularly

### Method 3: Local Installation (Development)

Running the server directly from source code for development or customization.

**When to use:**
- Developing the MCP server itself
- Testing local changes
- Environments without Docker
- Maximum control over dependencies

**Setup:**
```bash
# Clone and set up
git clone https://github.com/evergreen-ci/evergreen-mcp-server.git
cd evergreen-mcp-server
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .

# Verify installation
evergreen-mcp-server --help
```

**Configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "/absolute/path/to/evergreen-mcp-server/.venv/bin/evergreen-mcp-server",
      "args": []
    }
  }
}
```

**With workspace auto-detection:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "/path/to/.venv/bin/evergreen-mcp-server",
      "args": ["--workspace-dir", "${workspaceFolder}"]
    }
  }
}
```

**Development workflow:**
```bash
# Activate environment
source .venv/bin/activate

# Run tests
pytest tests/ -v

# Test with MCP Inspector
npx @modelcontextprotocol/inspector .venv/bin/evergreen-mcp-server

# Make changes to code
# Changes are immediately available due to editable install (pip install -e .)
```

### Method 4: HTTP/SSE Transport

For scenarios where stdio isn't practical, run the server as a standalone HTTP service.

**When to use:**
- Debugging with network inspection tools
- Shared server instances
- Non-stdio MCP clients
- Browser-based AI assistants

**Start the server:**
```bash
# Using Docker
docker run --rm -p 8000:8000 \
  -e EVERGREEN_MCP_TRANSPORT=sse \
  -e EVERGREEN_MCP_HOST=0.0.0.0 \
  -v ~/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro \
  -v ~/.evergreen.yml:/home/evergreen/.evergreen.yml:ro \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest

# Using local installation
EVERGREEN_MCP_TRANSPORT=sse \
EVERGREEN_MCP_HOST=0.0.0.0 \
EVERGREEN_MCP_PORT=8000 \
evergreen-mcp-server
```

**Client configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

**Transport options:**
- `sse` (Server-Sent Events): Best for most HTTP scenarios
- `streamable-http`: Alternative streaming protocol
- `stdio`: Default, for subprocess communication

### Building Custom Docker Images

If you need to customize the Docker image:

```bash
# Clone the repository
git clone https://github.com/evergreen-ci/evergreen-mcp-server.git
cd evergreen-mcp-server

# Build custom image
docker build -t evergreen-mcp-server:custom .

# Test the custom image
docker run --rm -it \
  -e EVERGREEN_USER=your_username \
  -e EVERGREEN_API_KEY=your_api_key \
  evergreen-mcp-server:custom --help

# Use in MCP configuration
{
  "command": "docker",
  "args": [
    "run", "--rm", "-i",
    "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
    "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
    "evergreen-mcp-server:custom"
  ]
}
```

---

## MCP Client Configuration (Detailed)

Comprehensive setup guides for various MCP clients and AI assistants.

### Cursor IDE

**Location:** `.cursor/mcp.json` in your workspace, or Settings → Features → MCP

**Basic configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

**With environment variables:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ],
      "env": {
        "EVERGREEN_PROJECT": "mongodb-mongo-master"
      }
    }
  }
}
```

**Local installation:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "/Users/yourname/projects/evergreen-mcp-server/.venv/bin/evergreen-mcp-server",
      "args": ["--workspace-dir", "${workspaceFolder}"]
    }
  }
}
```

**Testing the configuration:**
1. Save your `.cursor/mcp.json` file
2. Restart Cursor (or reload the window)
3. Open the MCP panel (View → MCP or Cmd+Shift+P → "MCP")
4. Verify the Evergreen server shows as "Connected"
5. Try a test query: "Show me my recent Evergreen patches"

### Claude Desktop

**Location:**
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

**Configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

**Testing:**
1. Save the config file
2. Quit Claude Desktop completely
3. Restart Claude Desktop
4. Look for the 🔌 icon in the bottom-right corner
5. Click it to see connected MCP servers
6. Test with: "List my recent Evergreen patches"

**Troubleshooting Claude Desktop:**
- **Server not connecting**: Check Docker is running (`docker ps`)
- **No 🔌 icon**: Verify config file syntax (use a JSON validator)
- **Permission errors**: Ensure credential files exist and are readable
- **Logs**: View logs in Settings → Advanced → View Logs

### VS Code with MCP Extension

**Prerequisites:**
- Install the MCP extension from VS Code marketplace

**Location:** VS Code Settings (JSON) - `settings.json`

**Configuration:**
```json
{
  "mcp.servers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${userHome}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${userHome}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ],
      "env": {}
    }
  }
}
```

**Note:** VS Code uses `${userHome}` instead of `${HOME}` for path expansion.

**Per-workspace configuration:**
Create `.vscode/settings.json` in your workspace:
```json
{
  "mcp.servers": {
    "evergreen": {
      "command": "/path/to/.venv/bin/evergreen-mcp-server",
      "args": ["--workspace-dir", "${workspaceFolder}"],
      "env": {
        "EVERGREEN_PROJECT": "mongodb-mongo-master"
      }
    }
  }
}
```

### Augment Code Assistant

**For VS Code:**
```json
{
  "augment.mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ],
      "env": {}
    }
  }
}
```

**For JetBrains IDEs:**
Add to Augment plugin settings:
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

**Using HTTP transport with Augment:**
```json
{
  "augment.mcpServers": {
    "evergreen": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

### GitHub Copilot Chat

**Configuration:**
```json
{
  "github.copilot.chat.mcp": {
    "servers": {
      "evergreen": {
        "command": "docker",
        "args": [
          "run", "--rm", "-i",
          "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
          "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
          "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
        ]
      }
    }
  }
}
```

### Universal Configuration Pattern

For any MCP-compatible client, follow this pattern:

**Docker with OIDC:**
```json
{
  "command": "docker",
  "args": [
    "run", "--rm", "-i",
    "-v", "<path-to-token>:/home/evergreen/.kanopy/token-oidclogin.json:ro",
    "-v", "<path-to-config>:/home/evergreen/.evergreen.yml:ro",
    "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
  ]
}
```

**Local installation:**
```json
{
  "command": "<absolute-path-to-venv>/bin/evergreen-mcp-server",
  "args": []
}
```

**Path variables by platform:**
- macOS/Linux: `${HOME}` or `~`
- Windows: `${USERPROFILE}` or `%USERPROFILE%`
- VS Code: `${userHome}`
- Cursor: `${HOME}`

---

## Tool Reference

### `list_user_recent_patches_evergreen`

Lists recent patches for the authenticated user.

**Parameters:**
- `limit` (optional): Number of patches to return (default: 10, max: 50)
- `project_id` (optional): Filter by project identifier

**Example Usage:**
```json
{
  "tool": "list_user_recent_patches_evergreen",
  "arguments": {
    "limit": 10
  }
}
```

**Response Format:**
```json
{
  "user_id": "developer@example.com",
  "patches": [
    {
      "patch_id": "507f1f77bcf86cd799439011",
      "description": "Fix authentication bug",
      "status": "failed",
      "create_time": "2025-09-23T10:30:00Z",
      "project_identifier": "mms"
    }
  ]
}
```

### `get_patch_failed_jobs_evergreen`

Retrieves failed jobs for a specific patch with test failure counts.

**Parameters:**
- `patch_id` (required): Patch identifier
- `project_id` (optional): Evergreen project identifier
- `max_results` (optional): Maximum failed tasks to return (default: 50)

**Example Usage:**
```json
{
  "tool": "get_patch_failed_jobs_evergreen",
  "arguments": {
    "patch_id": "507f1f77bcf86cd799439011"
  }
}
```

**Response Format:**
```json
{
  "patch_info": { "status": "failed" },
  "failed_tasks": [
    {
      "task_id": "task_456",
      "status": "failed",
      "test_info": {
        "failed_test_count": 5
      }
    }
  ]
}
```

### `get_task_logs_evergreen`

Retrieves detailed logs for a specific task with error filtering.

**Parameters:**
- `task_id` (required): Task identifier
- `execution` (optional): Task execution number (default: 0)
- `max_lines` (optional): Maximum log lines (default: 1000)
- `filter_errors` (optional): Filter for errors only (default: true)

**Example Usage:**
```json
{
  "tool": "get_task_logs_evergreen",
  "arguments": {
    "task_id": "task_456",
    "filter_errors": true
  }
}
```

### `get_task_test_results_evergreen`

Retrieves detailed unit test results for a task.

**Parameters:**
- `task_id` (required): Task identifier
- `execution` (optional): Task execution number (default: 0)
- `failed_only` (optional): Only failed tests (default: true)
- `limit` (optional): Maximum test results (default: 100)

**Example Usage:**
```json
{
  "tool": "get_task_test_results_evergreen",
  "arguments": {
    "task_id": "task_456",
    "failed_only": true
  }
}
```

### `get_stepback_tasks_evergreen`

Finds failed mainline tasks that have undergone stepback bisection.

**Parameters:**
- `project_id` (required): Evergreen project identifier
- `limit` (optional): Versions to analyze (default: 20)
- `requesters` (optional): Filter by requester type (e.g. `['gitter_request']`)
- `variants` (optional): Filter to specific build variants
- `exclude_variants` (optional): Exclude specific build variants

**Example Usage:**
```json
{
  "tool": "get_stepback_tasks_evergreen",
  "arguments": {
    "project_id": "mongodb-mongo-master",
    "limit": 10,
    "variants": ["enterprise-rhel-80-64-bit"]
  }
}
```

### `get_inferred_project_ids_evergreen`

Discovers which Evergreen projects you've been working on based on recent patches.

**Parameters:**
- `max_patches` (optional): Patches to scan (default: 50)

---

## Complete Workflow Examples

### Workflow 1: Debugging a Failed Patch

**Scenario**: Your patch failed in CI, and you want to understand why.

#### Step 1: List Your Recent Patches

Ask your AI assistant: *"Show me my recent Evergreen patches"*

The assistant calls:
```json
{
  "tool": "list_user_recent_patches_evergreen",
  "arguments": { "limit": 10, "project_id": "mms" }
}
```

Response shows:
```json
{
  "patches": [
    {
      "patch_id": "abc123",
      "description": "CLOUDP-12345: Fix auth bug",
      "status": "failed",
      "create_time": "2025-01-12T10:30:00Z"
    }
  ]
}
```

#### Step 2: Analyze Failed Jobs

Ask: *"What failed in patch abc123?"*

The assistant calls:
```json
{
  "tool": "get_patch_failed_jobs_evergreen",
  "arguments": { "patch_id": "abc123" }
}
```

Response shows:
```json
{
  "failed_tasks": [
    {
      "task_id": "task_auth_tests_123",
      "task_name": "auth_unit_tests",
      "build_variant": "ubuntu2004",
      "status": "failed",
      "test_info": {
        "failed_test_count": 3,
        "total_test_count": 150
      }
    }
  ]
}
```

#### Step 3: Get Specific Test Failures

Ask: *"Show me the failing tests in that task"*

The assistant calls:
```json
{
  "tool": "get_task_test_results_evergreen",
  "arguments": {
    "task_id": "task_auth_tests_123",
    "failed_only": true
  }
}
```

Response shows specific test names, files, and log URLs.

#### Step 4: Examine Error Logs

Ask: *"Get the error logs for that task"*

The assistant calls:
```json
{
  "tool": "get_task_logs_evergreen",
  "arguments": {
    "task_id": "task_auth_tests_123",
    "filter_errors": true,
    "max_lines": 100
  }
}
```

#### Step 5: AI Analysis

The assistant synthesizes all this information and provides:
- Root cause analysis
- Suggested fixes
- Links to relevant logs
- Similar past failures

### Workflow 2: Investigating Mainline Failures

**Scenario**: You want to find recent mainline commit failures that have been bisected via stepback.

Ask: *"Find recent stepback failures in the mongodb-mongo-master project for the compile task"*

```json
{
  "tool": "get_stepback_tasks_evergreen",
  "arguments": {
    "project_id": "mongodb-mongo-master",
    "limit": 20,
    "variants": ["enterprise-rhel-80-64-bit-compile"]
  }
}
```

The response shows:
- Versions with failures
- Tasks that failed
- Stepback information (which commits were tested)
- Links to investigate further

### Workflow 3: Monitoring Team's Patch Status

**Scenario**: You're on-call and want to check if team members have failing patches.

Ask: *"Are there any recent failing patches I should know about?"*

The assistant:
1. Calls `list_user_recent_patches_evergreen` to get your patches
2. Checks status of each
3. For failed patches, calls `get_patch_failed_jobs_evergreen`
4. Summarizes failures with severity and urgency

### Workflow 4: Comparative Analysis

**Scenario**: Your test is flaky, and you want to compare multiple failures.

Ask: *"Compare the failures in my last 3 patches"*

The assistant:
1. Lists your recent patches
2. Gets failed jobs for each
3. Analyzes common patterns
4. Identifies if it's the same test failing
5. Suggests if it's a flaky test vs. a real issue

---

## Advanced Configuration

### Understanding Evergreen Configuration File

The `~/.evergreen.yml` file is your central configuration for Evergreen authentication and project settings.

**Basic structure:**
```yaml
user: your.email@example.com
api_key: your_api_key_here
api_server_host: https://evergreen.mongodb.com
ui_server_host: https://spruce.mongodb.com
```

**With OIDC (managed by `evergreen login`):**
```yaml
user: your.email@example.com
api_server_host: https://evergreen.mongodb.com
ui_server_host: https://spruce.mongodb.com
```

The OIDC token is stored separately in `~/.kanopy/token-oidclogin.json`.

### Project Auto-Detection

Configure automatic project detection based on your workspace directory:

```yaml
user: your.email@example.com
api_key: your_api_key
projects_for_directory:
  /Users/yourname/mongodb: mongodb-mongo-master
  /Users/yourname/mms: mms
  /Users/yourname/atlas-proxy: atlasproxy
```

**How it works:**
1. The MCP server checks your current workspace directory
2. Matches it against the configured paths
3. Automatically sets the project context for tool calls
4. The AI assistant receives this as part of its context

**Priority order:**
1. Explicit `project_id` argument in tool calls
2. `EVERGREEN_PROJECT` environment variable
3. Auto-detected from workspace directory
4. Project specified in `~/.evergreen.yml` (if single project)

### Environment Variables Reference

| Variable | Type | Description | Example |
|----------|------|-------------|---------|
| `EVERGREEN_USER` | string | Username for API key auth | `john.doe@example.com` |
| `EVERGREEN_API_KEY` | string | API key for authentication | `abc123def456...` |
| `EVERGREEN_PROJECT` | string | Default project identifier | `mongodb-mongo-master` |
| `EVERGREEN_API_SERVER` | string | API server URL (advanced) | `https://evergreen.mongodb.com` |
| `EVERGREEN_MCP_TRANSPORT` | enum | Transport protocol | `stdio`, `sse`, `streamable-http` |
| `EVERGREEN_MCP_HOST` | string | HTTP host binding | `0.0.0.0`, `127.0.0.1` |
| `EVERGREEN_MCP_PORT` | integer | HTTP port | `8000` |
| `WORKSPACE_PATH` | string | Workspace directory | `/path/to/project` |
| `SENTRY_ENABLED` | boolean | Enable/disable telemetry (default: true) | `true`, `false` |

### Command-Line Arguments

All command-line arguments and their usage:

```bash
evergreen-mcp-server [OPTIONS]
```

**Options:**

`--project-id <PROJECT_ID>`
- Explicitly set the default Evergreen project
- Overrides auto-detection and environment variables
- Example: `--project-id mongodb-mongo-master`

`--workspace-dir <PATH>`
- Specify workspace directory for project auto-detection
- Useful when running outside the actual workspace
- Example: `--workspace-dir /path/to/mongodb`

`--transport <TRANSPORT>`
- Choose transport protocol
- Values: `stdio` (default), `sse`, `streamable-http`
- Example: `--transport sse`

`--host <HOST>`
- Host to bind for HTTP transports
- Default: `127.0.0.1` (localhost only)
- Use `0.0.0.0` to allow external connections
- Example: `--host 0.0.0.0`

`--port <PORT>`
- Port to listen on for HTTP transports
- Default: `8000`
- Example: `--port 9000`

`--help`
- Display help information and exit

**Usage examples:**

```bash
# Basic usage (stdio with auto-detection)
evergreen-mcp-server

# Explicit project
evergreen-mcp-server --project-id mms

# HTTP server mode
evergreen-mcp-server --transport sse --host 0.0.0.0 --port 8080

# With workspace detection
evergreen-mcp-server --workspace-dir ~/projects/mongodb

# Combined
evergreen-mcp-server --project-id mms --workspace-dir ~/projects/mms
```

### Advanced Docker Configuration

#### Custom Networking

Run on a specific Docker network:
```bash
docker network create mcp-network

docker run --rm -i \
  --network mcp-network \
  -v ~/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro \
  -v ~/.evergreen.yml:/home/evergreen/.evergreen.yml:ro \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest
```

#### Resource Limits

Limit CPU and memory:
```bash
docker run --rm -i \
  --cpus="1.0" \
  --memory="512m" \
  -v ~/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro \
  -v ~/.evergreen.yml:/home/evergreen/.evergreen.yml:ro \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest
```

#### Using Docker Compose

Create `docker-compose.yml`:
```yaml
version: '3.8'
services:
  evergreen-mcp:
    image: ghcr.io/evergreen-ci/evergreen-mcp-server:latest
    stdin_open: true
    volumes:
      - ~/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro
      - ~/.evergreen.yml:/home/evergreen/.evergreen.yml:ro
    environment:
      - EVERGREEN_PROJECT=mongodb-mongo-master
      - EVERGREEN_MCP_TRANSPORT=sse
      - EVERGREEN_MCP_HOST=0.0.0.0
      - EVERGREEN_MCP_PORT=8000
    ports:
      - "8000:8000"
```

Start with: `docker-compose up`

---

## MCP Inspector Deep Dive

The MCP Inspector is an essential tool for testing, debugging, and understanding your MCP server.

### Installing MCP Inspector

**Option 1: Use with npx (recommended for occasional use)**
```bash
npx @modelcontextprotocol/inspector <command>
```

**Option 2: Global installation**
```bash
npm install -g @modelcontextprotocol/inspector
mcp-inspector <command>
```

### Basic Inspector Usage

#### Testing Docker-based Server

```bash
npx @modelcontextprotocol/inspector docker run --rm -i \
  -v ~/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro \
  -v ~/.evergreen.yml:/home/evergreen/.evergreen.yml:ro \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest
```

#### Testing Local Installation

```bash
# From the project directory
npx @modelcontextprotocol/inspector .venv/bin/evergreen-mcp-server

# With project configuration
npx @modelcontextprotocol/inspector .venv/bin/evergreen-mcp-server --project-id mms
```

### Inspector Interface Walkthrough

When you start the inspector, it opens a web interface (typically at `http://localhost:6274`).

#### 1. Connection Status Panel

**Top-left corner** shows:
- ✅ **Connected**: Server is running and responding
- 🔄 **Connecting**: Inspector is starting the server
- ❌ **Error**: Connection failed (check logs)

#### 2. Server Info Tab

Shows:
- Server name and version
- Available capabilities
- Server metadata
- Connection details

#### 3. Tools Tab

**This is where you test tool calls.**

**Interface elements:**
- **Tool Selector**: Dropdown of available tools
- **Parameters Panel**: JSON editor for tool arguments
- **Call Tool Button**: Execute the tool call
- **Response Panel**: Shows the result

**Example workflow:**

1. Select `list_user_recent_patches_evergreen`
2. Edit parameters:
   ```json
   {
     "limit": 5,
     "project_id": "mms"
   }
   ```
3. Click "Call Tool"
4. View response in the panel below
5. Copy patch IDs for next calls

#### 4. Resources Tab

Browse available MCP resources:
- List all resources
- View resource URIs
- Read resource contents
- Test resource access

#### 5. Prompts Tab

If the server exposes prompt templates, you can:
- List available prompts
- View prompt templates
- Test prompt execution

#### 6. Logs Panel

**Bottom panel** shows real-time logs:
- Server stdout/stderr
- Request/response messages
- Error traces
- Debug information

**Log filtering:**
- Click icons to filter by severity
- Search logs with Cmd+F
- Copy logs for debugging

### Advanced Inspector Workflows

#### Workflow 1: Complete Failure Investigation

Simulate the AI assistant's workflow manually:

```bash
# Start inspector
npx @modelcontextprotocol/inspector .venv/bin/evergreen-mcp-server
```

1. **List patches** (Tools tab):
   ```json
   {
     "tool": "list_user_recent_patches_evergreen",
     "arguments": { "limit": 10 }
   }
   ```

2. **Copy a patch_id** from the response

3. **Get failed jobs**:
   ```json
   {
     "tool": "get_patch_failed_jobs_evergreen",
     "arguments": { "patch_id": "<copied_id>" }
   }
   ```

4. **Copy a task_id** from the failed_tasks array

5. **Get test results**:
   ```json
   {
     "tool": "get_task_test_results_evergreen",
     "arguments": { "task_id": "<copied_task_id>", "failed_only": true }
   }
   ```

6. **Get logs**:
   ```json
   {
     "tool": "get_task_logs_evergreen",
     "arguments": { "task_id": "<copied_task_id>", "filter_errors": true }
   }
   ```

#### Workflow 2: Performance Testing

Test tool response times and data volume:

1. Start inspector with logs visible
2. Call `list_user_recent_patches_evergreen` with `limit: 50`
3. Note response time in logs
4. Check data size in response panel
5. Test with different limits to find optimal values

#### Workflow 3: Error Reproduction

If users report issues:

1. Start inspector with same configuration as user
2. Reproduce the exact tool calls
3. Check logs for error messages
4. Verify authentication status
5. Test with different parameters to isolate the issue

### Debugging with Inspector

#### Authentication Issues

**Symptoms:**
- 401 errors in logs
- "Unauthorized" in responses

**Debug steps:**
1. Check "Logs" panel for auth errors
2. Verify credential files are mounted (Docker) or exist (local)
3. Test with: `list_user_recent_patches_evergreen` with `limit: 1`
4. Check response for user identification

#### Tool Parameter Issues

**Symptoms:**
- Tool calls fail with validation errors

**Debug steps:**
1. Use the Inspector's parameter editor
2. Check required vs optional parameters
3. Verify parameter types (string vs int vs array)
4. Look at example responses to understand expected formats

#### Network/API Issues

**Symptoms:**
- Timeouts
- Partial responses

**Debug steps:**
1. Check logs for GraphQL errors
2. Monitor response times
3. Test with smaller data requests
4. Verify Evergreen API is accessible

### Inspector Tips and Tricks

**Keyboard shortcuts:**
- `Cmd/Ctrl + F`: Search logs
- `Cmd/Ctrl + K`: Clear logs
- `Cmd/Ctrl + E`: Focus parameter editor

**JSON editing:**
- Use the built-in JSON editor for syntax highlighting
- Format JSON with Cmd+Shift+F
- Validate before calling

**Saving test cases:**
- Copy successful tool calls for documentation
- Save parameter sets for regression testing
- Export responses for test fixtures

---

## IDE Integration (Detailed)

Comprehensive guides for integrating the Evergreen MCP server with various IDEs and AI coding assistants.

### Cursor IDE (Comprehensive)

**Setup locations:**
1. **Workspace-specific**: `.cursor/mcp.json` in your project root
2. **Global**: Settings → Features → MCP

**Basic configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

**With automatic project detection:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "-v", "${workspaceFolder}:${workspaceFolder}:ro",
        "-e", "WORKSPACE_PATH=${workspaceFolder}",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

**Using local installation:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "/Users/yourname/evergreen-mcp-server/.venv/bin/evergreen-mcp-server",
      "args": ["--workspace-dir", "${workspaceFolder}"]
    }
  }
}
```

**Testing in Cursor:**
1. Save `.cursor/mcp.json`
2. Reload window: Cmd+Shift+P → "Developer: Reload Window"
3. Open MCP panel: Cmd+Shift+P → "MCP: Show Panel"
4. Verify "evergreen" server shows ✓ Connected
5. Test by asking: "Show my recent Evergreen patches"

**Cursor-specific tips:**
- Cursor automatically injects workspace context
- Use `${workspaceFolder}` for workspace-relative paths
- Cursor shows MCP status in the status bar
- Click the MCP icon to see connected servers

### Claude Desktop (Comprehensive)

**Configuration file locations:**
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

**Full configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ],
      "env": {
        "EVERGREEN_PROJECT": "mongodb-mongo-master"
      }
    }
  },
  "globalShortcut": "Ctrl+Space"
}
```

**Multiple servers example:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "-v", "...", "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"]
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/yourname/projects"]
    }
  }
}
```

**Setup checklist:**
1. ✅ Create/edit config file
2. ✅ Validate JSON syntax
3. ✅ Quit Claude Desktop completely (not just close window)
4. ✅ Verify Docker is running: `docker ps`
5. ✅ Start Claude Desktop
6. ✅ Look for 🔌 icon (bottom-right)
7. ✅ Click 🔌 to verify "evergreen" is connected
8. ✅ Test with a query

**Troubleshooting Claude Desktop:**

*Problem: No 🔌 icon appears*
- Verify JSON syntax (use `jsonlint` or online validator)
- Check file location is correct
- Ensure file is named exactly `claude_desktop_config.json`

*Problem: Server shows as disconnected*
- Check Docker is running: `docker ps`
- Verify credential files exist: `ls -la ~/.evergreen.yml`
- Check Claude logs: Settings → Advanced → View Logs

*Problem: Server connects but tools don't work*
- Test authentication with: `evergreen --version`
- Verify `evergreen login` was successful
- Check token file exists: `ls -la ~/.kanopy/token-oidclogin.json`

### VS Code MCP Extension (Comprehensive)

**Prerequisites:**
1. Install VS Code MCP extension from marketplace
2. Ensure Docker is installed (for Docker method)

**Configuration location:**
- Open Settings (JSON): Cmd+, → Open Settings (JSON)
- Or edit `.vscode/settings.json` in workspace

**Docker configuration:**
```json
{
  "mcp.servers": {
    "evergreen": {
      "type": "stdio",
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${userHome}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${userHome}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ],
      "env": {}
    }
  }
}
```

**Per-workspace configuration:**
Create `.vscode/settings.json`:
```json
{
  "mcp.servers": {
    "evergreen": {
      "type": "stdio",
      "command": "${workspaceFolder}/.venv/bin/evergreen-mcp-server",
      "args": ["--workspace-dir", "${workspaceFolder}"],
      "env": {
        "EVERGREEN_PROJECT": "mongodb-mongo-master"
      }
    }
  }
}
```

**VS Code variable reference:**
- `${workspaceFolder}`: Current workspace root
- `${userHome}`: User's home directory
- `${env:VAR_NAME}`: Environment variable

**Testing in VS Code:**
1. Save settings.json
2. Reload window: Cmd+Shift+P → "Developer: Reload Window"
3. Open MCP panel (if extension provides one)
4. Check Output panel → MCP for logs

### Augment (Comprehensive)

Augment is an AI coding assistant available for VS Code and JetBrains IDEs.

#### Augment in VS Code

**Configuration in settings.json:**
```json
{
  "augment.mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ],
      "env": {}
    }
  }
}
```

**Using HTTP/SSE transport:**

First, start the server:
```bash
docker run --rm -p 8000:8000 \
  -e EVERGREEN_MCP_TRANSPORT=sse \
  -e EVERGREEN_MCP_HOST=0.0.0.0 \
  -v ~/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro \
  -v ~/.evergreen.yml:/home/evergreen/.evergreen.yml:ro \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest
```

Then configure Augment:
```json
{
  "augment.mcpServers": {
    "evergreen": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

#### Augment in JetBrains IDEs

**Configuration:**
1. Open Augment plugin settings
2. Navigate to MCP Servers section
3. Add new server configuration:

```json
{
  "evergreen": {
    "command": "docker",
    "args": [
      "run", "--rm", "-i",
      "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
      "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
      "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
    ]
  }
}
```

**Testing Augment integration:**
1. Restart IDE/reload Augment
2. Open Augment chat
3. Type: "Can you check my recent Evergreen patches?"
4. Augment should use the MCP server to fetch the data

### GitHub Copilot Chat (Comprehensive)

**Note**: MCP support in GitHub Copilot is experimental and may require specific Copilot versions.

**VS Code configuration:**
```json
{
  "github.copilot.chat.mcp": {
    "servers": {
      "evergreen": {
        "command": "docker",
        "args": [
          "run", "--rm", "-i",
          "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
          "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
          "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
        ]
      }
    }
  }
}
```

**Using with Copilot Workspace:**

If using Copilot in workspace mode:
```json
{
  "github.copilot.chat.mcp": {
    "servers": {
      "evergreen": {
        "command": "${workspaceFolder}/.venv/bin/evergreen-mcp-server",
        "args": ["--workspace-dir", "${workspaceFolder}"]
      }
    }
  }
}
```

### Windsurf (Comprehensive)

Windsurf is Codeium's agentic IDE.

**Configuration location:**
- Settings → Extensions → MCP Servers

**Configuration:**
```json
{
  "mcp.servers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

### Other IDEs and Generic Setup

For any IDE that supports MCP, follow this general pattern:

**Step 1: Identify MCP configuration location**
- Check IDE documentation for MCP settings
- Usually in settings JSON or dedicated MCP panel

**Step 2: Use appropriate configuration format**

Docker-based (most portable):
```json
{
  "command": "docker",
  "args": [
    "run", "--rm", "-i",
    "-v", "<home>/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
    "-v", "<home>/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
    "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
  ]
}
```

Local installation:
```json
{
  "command": "/absolute/path/to/.venv/bin/evergreen-mcp-server",
  "args": []
}
```

**Step 3: Test the configuration**
1. Save configuration
2. Restart IDE or reload settings
3. Verify server appears in MCP panel (if available)
4. Test with a simple query

### Configuration Troubleshooting Guide

#### Problem: Server won't start

**Checklist:**
- ✅ Docker is running: `docker ps`
- ✅ Credentials exist: `ls -la ~/.evergreen.yml ~/.kanopy/token-oidclogin.json`
- ✅ Path is absolute (for local installations)
- ✅ Virtual environment is activated (for local)
- ✅ JSON syntax is valid

#### Problem: Server starts but authentication fails

**Check:**
1. `evergreen login` status
2. Token file permissions
3. Config file format
4. Environment variables

**Test manually:**
```bash
# Docker method
docker run --rm -it \
  -v ~/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro \
  -v ~/.evergreen.yml:/home/evergreen/.evergreen.yml:ro \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest \
  --help

# Local method
.venv/bin/evergreen-mcp-server --help
```

#### Problem: Tools don't appear or aren't working

**Debug steps:**
1. Check IDE logs for MCP errors
2. Use MCP Inspector to verify tool availability
3. Test tool calls directly with Inspector
4. Verify project_id is correct

---

## Troubleshooting

### "Authentication failed" errors

1. Re-run `evergreen login` to refresh your credentials
2. Verify `~/.evergreen.yml` exists and has valid credentials
3. Check that `~/.kanopy/token-oidclogin.json` exists (for OIDC)
4. Test authentication: `evergreen --version`

### "Project not found" errors

1. Use `get_inferred_project_ids_evergreen` to discover available projects
2. Specify `project_id` explicitly in your tool calls
3. Add project mappings to `~/.evergreen.yml`
4. Verify project identifier spelling (case-sensitive)

### Docker permission errors

Ensure Docker can read your credential files:
```bash
ls -la ~/.evergreen.yml ~/.kanopy/token-oidclogin.json
chmod 600 ~/.evergreen.yml ~/.kanopy/token-oidclogin.json
```

### Token refresh issues

OIDC tokens expire. Re-run `evergreen login` if you see authentication errors after some time.

### MCP Server won't connect

1. Check if Docker is running: `docker ps`
2. Test Docker image manually:
   ```bash
   docker run --rm -it ghcr.io/evergreen-ci/evergreen-mcp-server:latest --help
   ```
3. Verify JSON configuration syntax
4. Check IDE/client logs for error messages

### Tools return no data

1. Verify you have access to the Evergreen project
2. Check if patches/tasks exist in the specified time range
3. Test with broader parameters (higher `limit`, no filters)
4. Use MCP Inspector to isolate the issue

---

## Telemetry

The Evergreen MCP Server includes optional telemetry via Sentry to help improve reliability and diagnose issues.

### What's Collected

- Error reports and stack traces when the server encounters failures
- Performance traces for MCP tool calls
- No personally identifiable information (PII) is collected by default

### Disabling Telemetry

Telemetry is **enabled by default**. To disable it, set the `SENTRY_ENABLED` environment variable to `false`:

**Docker:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "SENTRY_ENABLED=false",
        "-v", "${HOME}/.kanopy/token-oidclogin.json:/home/evergreen/.kanopy/token-oidclogin.json:ro",
        "-v", "${HOME}/.evergreen.yml:/home/evergreen/.evergreen.yml:ro",
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
      ]
    }
  }
}
```

**Local installation:**
```bash
SENTRY_ENABLED=false evergreen-mcp-server
```

---

## Development

### Project Structure

```
evergreen-mcp-server/
├── src/evergreen_mcp/
│   ├── server.py                    # Main MCP server
│   ├── mcp_tools.py                 # Tool definitions
│   ├── evergreen_graphql_client.py  # GraphQL client
│   └── evergreen_queries.py         # GraphQL queries
├── tests/
├── Dockerfile
├── pyproject.toml
└── README.md
```

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest --cov=evergreen_mcp tests/
```

### Code Quality

```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Lint
flake8 src/ tests/
```

### Updating GraphQL Schema

```bash
./scripts/fetch_graphql_schema.sh
```

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Ensure all tests pass
5. Submit a pull request

---

## License

This project follows the same license as the main Evergreen project.

## Version

Current version: 0.4.0

