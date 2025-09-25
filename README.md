# Evergreen MCP Server

A Model Context Protocol (MCP) server that provides access to the Evergreen CI/CD platform API. 
This server enables AI assistants and other MCP clients to interact with Evergreen projects, builds, tasks, and other CI/CD resources.

## Overview

[Evergreen](https://github.com/evergreen-ci/evergreen) is MongoDB's continuous integration platform. This MCP server exposes Evergreen's functionality through the Model Context Protocol, allowing AI assistants to help with CI/CD operations, project management, and build analysis.

## Features

- **Project Resources**: Access and list Evergreen projects and build statuses
- **Failed Jobs Analysis**: Fetch failed jobs and logs for specific commits to help identify CI/CD failures
- **Task Log Retrieval**: Get detailed logs for failed tasks with error filtering
- **Authentication**: Secure API key-based authentication
- **Async Operations**: Built on asyncio for efficient concurrent operations
- **GraphQL Integration**: Uses Evergreen's GraphQL API for efficient data retrieval

## Prerequisites

- Python 3.13.3 (as specified in `.tool-versions`)
- Access to an Evergreen instance
- Valid Evergreen API credentials

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/evergreen-ci/evergreen-mcp-server.git
cd evergreen-mcp-server
```

### 2. Set Up Python Environment

```bash
# Create a virtual environment
python -m venv .venv

# Activate the virtual environment
# On macOS/Linux:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -e .
```

## Configuration

### Evergreen API Configuration

Create a configuration file at `~/.evergreen.yml` with your Evergreen credentials:

```yaml
user: your-evergreen-username
api_key: your-evergreen-api-key
```

**How to get your API credentials:**

1. Log in to your Evergreen instance
2. Go to your user settings/preferences
3. Generate or copy your API key
4. Use your username and the generated API key in the configuration file

### Server Configuration Options

The MCP server supports additional configuration options via command-line arguments:

**Available Arguments:**
- `--project-id <PROJECT_ID>`: Specify the default Evergreen project identifier
- `--help`: Show help information

**Example with project ID:**
```bash
evergreen-mcp-server --project-id mms
```

**Project ID Configuration:**
The server supports a `--project-id` argument to set a default project identifier. If no `--project-id` is specified in the server configuration, the project identifier must be provided explicitly in each tool call. This ensures clear and predictable behavior.

## Available Tools

### `list_user_recent_patches`

Lists recent patches for the authenticated user, enabling AI agents to browse and select patches for analysis.

**Parameters:**
- `limit` (optional): Number of patches to return (default: 10, max: 50)

**Example Usage:**
```json
{
  "tool": "list_user_recent_patches",
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
      "patch_number": 12345,
      "githash": "9e484ce50be1335393eeb056c91ef4a72fe48bfd",
      "description": "Fix authentication bug in user service",
      "author": "developer@example.com",
      "author_display_name": "Jane Developer",
      "status": "failed",
      "create_time": "2025-09-23T10:30:00Z",
      "project_identifier": "mms",
      "has_version": true,
      "version_status": "failed"
    }
  ],
  "total_patches": 10
}
```

### `get_patch_failed_jobs`

Retrieves failed jobs for a specific patch, enabling detailed analysis of CI/CD failures.

**Parameters:**
- `patch_id` (required): Patch identifier from `list_user_recent_patches`
- `max_results` (optional): Maximum number of failed tasks to return (default: 50)

**Example Usage:**
```json
{
  "tool": "get_patch_failed_jobs",
  "arguments": {
    "patch_id": "507f1f77bcf86cd799439011",
    "max_results": 10
  }
}
```

**Response Format:**
```json
{
  "patch_info": {
    "patch_id": "507f1f77bcf86cd799439011",
    "githash": "9e484ce50be1335393eeb056c91ef4a72fe48bfd",
    "description": "Fix authentication bug in user service",
    "author": "developer@example.com",
    "status": "failed"
  },
  "version_info": {
    "version_id": "version_123",
    "status": "failed"
  },
  "failed_tasks": [
    {
      "task_id": "task_456",
      "task_name": "test-unit",
      "build_variant": "ubuntu2004",
      "status": "failed",
      "execution": 0,
      "failure_details": {
        "description": "Test failures in authentication module",
        "timed_out": false,
        "failing_command": "npm test"
      },
      "duration_ms": 120000,
      "finish_time": "2025-09-23T10:32:00Z",
      "logs": {
        "task_log": "https://evergreen.mongodb.com/task_log/...",
        "agent_log": "https://evergreen.mongodb.com/agent_log/...",
        "system_log": "https://evergreen.mongodb.com/system_log/...",
        "all_logs": "https://evergreen.mongodb.com/all_log/..."
      }
    }
  ],
  "summary": {
    "total_failed_tasks": 3,
    "returned_tasks": 3,
    "failed_build_variants": ["ubuntu2004", "windows"],
    "has_timeouts": false
  }
}
```

### `get_task_logs`

Retrieves detailed logs for a specific Evergreen task, with optional error filtering for focused analysis.

**Parameters:**
- `task_id` (required): Task identifier from failed jobs response
- `execution` (optional): Task execution number (default: 0)
- `max_lines` (optional): Maximum number of log lines to return (default: 1000)
- `filter_errors` (optional): Whether to filter for error messages only (default: true)

**Example Usage:**
```json
{
  "tool": "get_task_logs",
  "arguments": {
    "task_id": "task_456",
    "execution": 0,
    "filter_errors": true,
    "max_lines": 500
  }
}
```

**Response Format:**
```json
{
  "task_id": "task_456",
  "execution": 0,
  "task_name": "test-unit",
  "log_type": "task",
  "total_lines": 45,
  "logs": [
    {
      "severity": "error",
      "message": "Test failed: authentication module",
      "timestamp": "2025-09-22T10:35:15Z",
      "type": "test"
    }
  ],
  "truncated": false
}
```

## Running the Server

### Method 1: Direct Execution

```bash
# Make sure your virtual environment is activated
source .venv/bin/activate

# Run the server
evergreen-mcp-server
```

### Method 2: Using Python Module

```bash
python -m src.server
```

### Method 3: Development Mode

```bash
# From the project root
python src/server.py
```

### Method 4: Using Docker

#### Build and Run with Docker

```bash
# Build the Docker image
docker build -t evergreen-mcp-server .

# Run the container with required environment variables
docker run --rm -it \
  -e EVERGREEN_USER=your_username \
  -e EVERGREEN_API_KEY=your_api_key \
  -e EVERGREEN_PROJECT=your_project \
  evergreen-mcp-server

# Run with volume mount for logs
docker run --rm -it \
  -e EVERGREEN_USER=your_username \
  -e EVERGREEN_API_KEY=your_api_key \
  -e EVERGREEN_PROJECT=your_project \
  -v $(pwd)/logs:/app/logs \
  evergreen-mcp-server
```

## MCP Client Configuration

### VS Code with MCP Extension

Add the following to your MCP client configuration (e.g., `.vscode/mcp.json`):

```json
{
    "servers": {
        "evergreen-mcp-server": {
            "type": "stdio",
            "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
            "args": []
        }
    }
}
```

**With Project ID Configuration:**
```json
{
    "servers": {
        "evergreen-mcp-server": {
            "type": "stdio",
            "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
            "args": ["--project-id", "your-evergreen-project-id"]
        }
    }
}
```

### Claude Desktop

Add to your Claude Desktop MCP configuration:

```json
{
    "mcpServers": {
        "evergreen": {
            "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
            "args": []
        }
    }
}
```

**With Project ID Configuration:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "{root}/evergreen-mcp-server/.venv/bin/python",
      "args": ["run_mcp_server.py"],
      "cwd": "{root}/evergreen-mcp-server",
      "env": {}
    }
  }
}
```

**Using Docker:**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-e", "EVERGREEN_USER=your_username",
        "-e", "EVERGREEN_API_KEY=your_api_key",
        "-e", "EVERGREEN_PROJECT=your_project",
        "evergreen-mcp-server"
      ]
    }
  }
}
```

## MCP Inspector Integration

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) is a powerful debugging and testing tool that provides a web-based interface for interacting with MCP servers. It's especially useful for development, testing, and understanding how the Evergreen MCP server works.

### Installing MCP Inspector

```bash
# Install globally via npm
npm install -g @modelcontextprotocol/inspector

# Or install locally in your project
npm install --save-dev @modelcontextprotocol/inspector
```

### Using Inspector with Evergreen MCP Server

#### Method 1: Direct Server Command

```bash
# Start the inspector with the Evergreen MCP server
mcp-inspector python run_server.py
```

#### Method 2: With Project ID Configuration

```bash
# Start with a specific project ID
mcp-inspector python run_server.py --project-id your-evergreen-project-id
```

#### Method 3: Using Virtual Environment Path

```bash
# If you have the server installed in a virtual environment
mcp-inspector /path/to/your/project/.venv/bin/evergreen-mcp-server
```

### Inspector Features for Evergreen MCP

The MCP Inspector provides several useful features when working with the Evergreen MCP server:

1. **Tool Testing**: Interactive forms to test all available tools:
   - `list_user_recent_patches`
   - `get_patch_failed_jobs`
   - `get_task_logs`

2. **Resource Browsing**: View available Evergreen project resources

3. **Real-time Logging**: See server logs and debug information in real-time

4. **Request/Response Inspection**: Examine the exact JSON payloads being sent and received

5. **Schema Validation**: Verify that tool inputs match the expected schemas

### Typical Inspector Workflow

1. **Start Inspector**: Launch the inspector with your Evergreen MCP server
2. **Test Authentication**: Verify your Evergreen credentials are working by listing projects
3. **Explore Tools**: Use the interactive forms to test each tool with sample data
4. **Debug Issues**: Use the logging panel to troubleshoot any authentication or API issues
5. **Validate Responses**: Examine the JSON responses to understand the data structure

### Inspector Configuration Tips

- **Environment Variables**: The inspector will use the same `~/.evergreen.yml` configuration file as the server
- **Logging Level**: Set `PYTHONPATH=src` and enable debug logging for more detailed output
- **Network Issues**: If you encounter connection issues, verify your Evergreen API endpoint and credentials

### Example Inspector Session

1. Open the inspector web interface (typically at `http://localhost:3000`)
2. Navigate to the "Tools" tab
3. Try `list_user_recent_patches` with `limit: 5`
4. Copy a patch ID from the response
5. Use `get_patch_failed_jobs` with the copied patch ID
6. Copy a task ID from the failed jobs response
7. Use `get_task_logs` with the task ID to see detailed error logs

This workflow demonstrates the typical debugging process for CI/CD failures using the Evergreen MCP server.

## Available Resources

The server currently provides the following MCP resources:

### Projects

- **URI Pattern**: `evergreen://project/{project_id}`
- **Description**: Access to Evergreen project information
- **MIME Type**: `application/json`

The server automatically discovers and lists all projects you have access to in your Evergreen instance.

## Usage Examples

### Analyzing Failed Jobs - Two-Step Workflow

#### Step 1: List Recent Patches

```json
{
  "tool": "list_user_recent_patches",
  "arguments": {
    "limit": 10
  }
}
```

This returns your recent patches with status information, allowing you to identify failed patches.

#### Step 2: Analyze Failed Jobs for Selected Patch

```json
{
  "tool": "get_patch_failed_jobs",
  "arguments": {
    "patch_id": "507f1f77bcf86cd799439011",
    "max_results": 20
  }
}
```

### Getting Detailed Logs for a Failed Task

```json
{
  "tool": "get_task_logs",
  "arguments": {
    "task_id": "task_from_failed_jobs_response",
    "filter_errors": true,
    "max_lines": 100
  }
}
```

### Agent Workflow Example

1. **Agent lists user patches**: Calls `list_user_recent_patches` to get recent patches
2. **Agent selects relevant patch**: Chooses patch based on status, description, or user input
3. **Agent analyzes failures**: Calls `get_patch_failed_jobs` to get detailed failure information
4. **Agent gets detailed logs**: Calls `get_task_logs` for specific failed tasks
5. **Agent suggests fixes**: Based on error patterns and log analysis

### Typical Agent Selection Logic

```python
# Agent examines patches and selects based on criteria:
for patch in patches:
    if patch['status'] == 'failed' or patch['version_status'] == 'failed':
        # This patch has failures - good candidate for analysis
        selected_patch = patch
        break
    elif 'fix' in patch['description'].lower():
        # This might be a fix attempt - worth checking
        selected_patch = patch
```

## Development

### Project Structure

```
evergreen-mcp-server/
├── src/
│   ├── server.py                    # Main MCP server implementation
│   ├── run_mcp_server.py            # Server entry point with logging setup
│   ├── mcp_tools.py                 # MCP tool definitions and handlers
│   ├── evergreen_graphql_client.py  # GraphQL client for Evergreen API
│   ├── failed_jobs_tools.py         # Core logic for patch and failed jobs analysis
│   └── evergreen_queries.py         # GraphQL query definitions
├── tests/
│   └── test_mcp_client.py           # MCP integration tests (full end-to-end)
├── scripts/
│   └── fetch_graphql_schema.sh      # Script to update GraphQL schema
├── merged-schema.graphql            # Evergreen GraphQL schema
├── run_server.py                    # Convenience wrapper to start server
├── pyproject.toml                   # Project configuration
└── README.md                        # This file
```

### Key Components

- **Server Lifespan**: Manages Evergreen API client lifecycle
- **Resource Handlers**: Provide access to Evergreen resources
- **Authentication**: Handles API key authentication with Evergreen

### Dependencies

- `mcp`: Model Context Protocol implementation
- `aiohttp`: Async HTTP client for API calls
- `pyyaml`: YAML configuration file parsing
- `pydantic`: Data validation and serialization
- Generated Evergreen API client

### Updating GraphQL Schema

To update the Evergreen GraphQL schema:

```bash
./scripts/fetch_graphql_schema.sh
```

### Cleaning Generated Files

```bash
./scripts/clean
```


### Debug Mode

For debugging, you can run the server with additional logging:

```bash
# Set environment variable for debug logging
export PYTHONPATH=src
python -c "import logging; logging.basicConfig(level=logging.DEBUG); from server import main; main()"
```



## License

This project follows the same license as the main Evergreen project.

## Version

Current version: 0.1.0
