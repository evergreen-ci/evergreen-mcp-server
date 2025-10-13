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
# Install the package
pip install -e .

# For development (includes testing dependencies)
pip install -e ".[dev]"
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

The Evergreen MCP server is designed to be used with MCP clients (like Claude Desktop, VS Code with MCP extension) or for testing with the MCP Inspector. It communicates via stdio and is not meant to be run as a standalone HTTP server.

### Method 1: With MCP Inspector (Recommended for Testing)

```bash
# Using npx (no installation required)
npx @modelcontextprotocol/inspector .venv/bin/evergreen-mcp-server

# This will:
# - Start the MCP server
# - Launch a web interface for testing
# - Open your browser automatically
```

### Method 2: Direct Execution (for MCP Clients)

```bash
# Make sure your virtual environment is activated
source .venv/bin/activate

# Run the server (will wait for stdio input from an MCP client)
evergreen-mcp-server

# With project ID
evergreen-mcp-server --project-id your-evergreen-project-id
```

**Note**: When run directly, the server expects to communicate via stdio with an MCP client. It will not provide a command-line interface or HTTP endpoint.

### Method 3: Using Docker

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
      "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
      "args": ["--project-id", "your-evergreen-project-id"]
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

### IDE AI Tools Integration

The Evergreen MCP server can be integrated with various IDE-based AI tools that support the Model Context Protocol. This section provides setup instructions for popular IDE AI assistants.

#### Augment Code Assistant

[Augment](https://www.augmentcode.com/) is an AI coding assistant that supports MCP integration for enhanced contextual assistance.

**Setup Steps:**

1. **Install Augment Extension**: Install the Augment extension in your IDE (VS Code, IntelliJ, etc.)

2. **Configure MCP Server**: Add the Evergreen MCP server to Augment's configuration:

   **For VS Code with Augment:**
   ```json
   {
     "augment.mcpServers": {
       "evergreen": {
         "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
         "args": ["--project-id", "your-evergreen-project-id"],
         "env": {}
       }
     }
   }
   ```

   **For JetBrains IDEs with Augment:**
   Add to your Augment settings:
   ```json
   {
     "mcp": {
       "servers": {
         "evergreen": {
           "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
           "args": ["--project-id", "your-evergreen-project-id"]
         }
       }
     }
   }
   ```

3. **Authentication**: Ensure your `~/.evergreen.yml` configuration file is properly set up with your Evergreen credentials.

4. **Usage**: Once configured, you can ask Augment to help with CI/CD debugging:
   - "Show me recent failed patches in Evergreen"
   - "Analyze the failed jobs for patch XYZ"
   - "Get the logs for the failing test task"

#### Claude Code (IDE Integration)

Claude's IDE integration provides direct access to Claude AI within your development environment with MCP support.

**Setup for VS Code:**

1. **Install Claude Extension**: Install the official Claude extension from the VS Code marketplace

2. **Configure MCP in VS Code Settings**: Add to your VS Code `settings.json`:
   ```json
   {
     "claude.mcpServers": {
       "evergreen": {
         "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
         "args": ["--project-id", "your-evergreen-project-id"],
         "type": "stdio"
       }
     }
   }
   ```

3. **Alternative Configuration**: Create a `.claude/mcp.json` file in your project root:
   ```json
   {
     "mcpServers": {
       "evergreen": {
         "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
         "args": ["--project-id", "your-evergreen-project-id"]
       }
     }
   }
   ```

**Setup for JetBrains IDEs:**

1. **Install Claude Plugin**: Install the Claude plugin from JetBrains marketplace

2. **Configure MCP Server**: In Claude plugin settings, add:
   ```json
   {
     "servers": {
       "evergreen": {
         "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
         "args": ["--project-id", "your-evergreen-project-id"]
       }
     }
   }
   ```

#### GitHub Copilot Chat with MCP

GitHub Copilot Chat can be extended with MCP servers through various configuration methods.

**VS Code Configuration:**
```json
{
  "github.copilot.chat.mcp": {
    "servers": {
      "evergreen": {
        "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
        "args": ["--project-id", "your-evergreen-project-id"]
      }
    }
  }
}
```

#### Other IDE AI Tools

For other IDE-based AI assistants that support MCP, the general configuration pattern is:

1. **Locate MCP Configuration**: Find your IDE AI tool's MCP server configuration section
2. **Add Server Entry**: Add an entry for the Evergreen MCP server:
   ```json
   {
     "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
     "args": ["--project-id", "your-evergreen-project-id"],
     "type": "stdio"
   }
   ```
3. **Set Environment**: Ensure your Evergreen credentials are available in `~/.evergreen.yml`

#### Configuration Tips

**Path Resolution:**
- Use absolute paths to the virtual environment binary
- On Windows: Use `.venv\Scripts\evergreen-mcp-server.exe`
- On macOS/Linux: Use `.venv/bin/evergreen-mcp-server`

**Docker Integration:**
Many IDE AI tools also support Docker-based MCP servers:
```json
{
  "command": "docker",
  "args": [
    "run", "--rm", "-i",
    "-e", "EVERGREEN_USER=your_username",
    "-e", "EVERGREEN_API_KEY=your_api_key",
    "-e", "EVERGREEN_PROJECT=your_project",
    "evergreen-mcp-server"
  ]
}
```

**Environment Variables:**
Instead of using `~/.evergreen.yml`, you can set environment variables:
```json
{
  "command": "/path/to/.venv/bin/evergreen-mcp-server",
  "args": ["--project-id", "your-evergreen-project-id"],
  "env": {
    "EVERGREEN_USER": "your_username",
    "EVERGREEN_API_KEY": "your_api_key"
  }
}
```

**Troubleshooting:**
- Verify the MCP server runs correctly: `evergreen-mcp-server --help`
- Test with MCP Inspector first: `npx @modelcontextprotocol/inspector evergreen-mcp-server`
- Check IDE AI tool logs for MCP connection errors
- Ensure proper file permissions on the executable

## MCP Inspector Integration

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) is a powerful debugging and testing tool that provides a web-based interface for interacting with MCP servers. It's especially useful for development, testing, and understanding how the Evergreen MCP server works.

### Installing MCP Inspector

The MCP Inspector can be installed globally or run directly with `npx` (recommended for one-time use):

```bash
# Option 1: Install globally via npm
npm install -g @modelcontextprotocol/inspector

# Option 2: Use npx (no installation required)
# This will be shown in the examples below
```

### Using Inspector with Evergreen MCP Server

#### Method 1: Using npx (Recommended)

```bash
# Start the inspector with the Evergreen MCP server using the full path to the virtual environment
npx @modelcontextprotocol/inspector .venv/bin/evergreen-mcp-server

# Or if your virtual environment is activated:
npx @modelcontextprotocol/inspector evergreen-mcp-server
```

#### Method 2: Using Globally Installed Inspector

```bash
# If you installed mcp-inspector globally
mcp-inspector .venv/bin/evergreen-mcp-server

# With project ID configuration
mcp-inspector .venv/bin/evergreen-mcp-server --project-id your-evergreen-project-id
```

#### Method 3: Using Python Module Directly

```bash
# Using the Python module directly
npx @modelcontextprotocol/inspector python -m evergreen_mcp.server
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

When you start the inspector, it will:
1. Install the inspector package (if using npx)
2. Start the proxy server (typically on port 6277)
3. Open your browser automatically to the inspector interface (typically at `http://localhost:6274`)
4. Display an authentication token in the URL

Then you can:
1. Navigate to the "Tools" tab in the web interface
2. Try `list_user_recent_patches` with `limit: 5`
3. Copy a patch ID from the response
4. Use `get_patch_failed_jobs` with the copied patch ID
5. Copy a task ID from the failed jobs response
6. Use `get_task_logs` with the task ID to see detailed error logs

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

## Testing

The project includes comprehensive tests to ensure functionality and reliability.

### Running Tests

```bash
# Run all tests with pytest (recommended)
python -m pytest tests/ -v

# Run specific test files
python -m pytest tests/test_basic.py -v
python -m pytest tests/test_mcp_client.py -v

# Run tests with unittest
python -m unittest tests.test_basic -v

# Run integration test directly
python tests/test_mcp_client.py
```

### Test Structure

- **`tests/test_basic.py`**: Unit tests for individual components
  - Tool definitions and handlers validation
  - Module import verification
  - Component functionality testing

- **`tests/test_mcp_client.py`**: Full integration test
  - End-to-end MCP protocol testing
  - Real Evergreen API connectivity
  - Tool execution and response validation

### Test Requirements

- **Unit Tests**: No external dependencies, run offline
- **Integration Tests**: Require valid `~/.evergreen.yml` configuration
- **Development Dependencies**: Install with `pip install -e ".[dev]"`

### Code Quality

The project uses automated code formatting and linting:

```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Check for syntax errors and style issues
flake8 src/ tests/ --count --select=E9,F63,F7,F82 --show-source --statistics
```

### Continuous Integration

GitHub Actions workflows automatically:
- **Test Workflow**: Tests code compilation, imports, and unit tests across Python 3.11-3.13
- **Lint Workflow**: Validates code formatting, import sorting, and style guidelines
- **PR Checks**: All workflows run on pull requests to ensure code quality

## Development

### Project Structure

```
evergreen-mcp-server/
├── src/
│   ├── __init__.py                  # Package initialization
│   ├── server.py                    # Main MCP server implementation
│   ├── mcp_tools.py                 # MCP tool definitions and handlers
│   ├── evergreen_graphql_client.py  # GraphQL client for Evergreen API
│   ├── failed_jobs_tools.py         # Core logic for patch and failed jobs analysis
│   └── evergreen_queries.py         # GraphQL query definitions
├── tests/
│   ├── test_basic.py                # Unit tests for components
│   └── test_mcp_client.py           # MCP integration tests (full end-to-end)
├── scripts/
│   └── fetch_graphql_schema.sh      # Script to update GraphQL schema
├── Dockerfile                       # Docker container configuration
├── pyproject.toml                   # Project configuration and dependencies
└── README.md                        # This file
```

### Key Components

- **Server Lifespan**: Manages Evergreen API client lifecycle
- **Resource Handlers**: Provide access to Evergreen resources
- **Authentication**: Handles API key authentication with Evergreen

### Dependencies

**Runtime Dependencies:**
- `mcp`: Model Context Protocol implementation
- `aiohttp`: Async HTTP client for API calls
- `gql[aiohttp]`: GraphQL client with async HTTP transport
- `pyyaml`: YAML configuration file parsing
- `pydantic`: Data validation and serialization
- `python-dateutil`: Date/time parsing utilities

**Development Dependencies:**
- `pytest`: Testing framework
- `pytest-asyncio`: Async test support

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
