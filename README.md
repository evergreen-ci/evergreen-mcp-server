# Evergreen MCP Server

A Model Context Protocol (MCP) server that provides access to the Evergreen CI/CD platform API. 
This server enables AI assistants and other MCP clients to interact with Evergreen projects, builds, tasks, and other CI/CD resources.

## Overview

[Evergreen](https://github.com/evergreen-ci/evergreen) is MongoDB's continuous integration platform. This MCP server exposes Evergreen's functionality through the Model Context Protocol, allowing AI assistants to help with CI/CD operations, project management, and build analysis.

## Features

- **Project Resources**: Access and list Evergreen projects and build statuses
- **Failed Jobs Analysis**: Fetch failed jobs and logs for specific commits to help identify CI/CD failures
- **Unit Test Failure Analysis**: Detailed analysis of individual unit test failures with test-specific logs and metadata
- **Task Log Retrieval**: Get detailed logs for failed tasks with error filtering
- **Authentication**: Secure API key-based authentication
- **Async Operations**: Built on asyncio for efficient concurrent operations
- **GraphQL Integration**: Uses Evergreen's GraphQL API for efficient data retrieval

## Prerequisites

- Access to an Evergreen instance
- Valid Evergreen API credentials
- Python 3.11+ (for CLI installation) or Docker (for containerized setup)

## Quick Start (CLI - Recommended)

The fastest way to get started is using the CLI tool with `uv`:

```bash
# Install with uv (recommended)
uv tool install evergreen-mcp-server --from git+https://github.com/evergreen-ci/evergreen-mcp-server

# Or install with pip
pip install git+https://github.com/evergreen-ci/evergreen-mcp-server

# Set up your Evergreen credentials
cat > ~/.evergreen.yml << EOF
user: your-evergreen-username
api_key: your-evergreen-api-key
EOF

# Run the MCP server
evergreen-mcp --project-id your-evergreen-project-id
```

For detailed setup instructions and client configuration, see [Installation](#installation) and [MCP Client Configuration](#mcp-client-configuration) sections below.

## Installation

### Method 1: Using uv (Recommended)

[uv](https://github.com/astral-sh/uv) is the fastest and most reliable way to install the Evergreen MCP server:

```bash
# Install the CLI tool
uv tool install evergreen-mcp-server --from git+https://github.com/evergreen-ci/evergreen-mcp-server

# The evergreen-mcp command will be available in your PATH
evergreen-mcp --help
```

### Method 2: Using pip

```bash
# Install directly from Git
pip install git+https://github.com/evergreen-ci/evergreen-mcp-server

# Or install from local clone for development
git clone https://github.com/evergreen-ci/evergreen-mcp-server.git
cd evergreen-mcp-server
pip install -e .

# For development (includes testing dependencies)
pip install -e ".[dev]"
```

### Method 3: Using Docker (Alternative)

If you prefer containerized deployment:

```bash
# Pull the Docker image
docker pull ghcr.io/evergreen-ci/evergreen-mcp-server:latest

# Run with environment variables
docker run --rm -it \
  -e EVERGREEN_USER=your_username \
  -e EVERGREEN_API_KEY=your_api_key \
  -e EVERGREEN_PROJECT=your_project \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest
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

### `list_user_recent_patches_evergreen`

Lists recent patches for the authenticated user, enabling AI agents to browse and select patches for analysis.

**Parameters:**
- `limit` (optional): Number of patches to return (default: 10, max: 50)

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

### `get_patch_failed_jobs_evergreen`

Retrieves failed jobs for a specific patch, enabling detailed analysis of CI/CD failures. Now includes unit test failure counts for each task.

**Parameters:**
- `patch_id` (required): Patch identifier from `list_user_recent_patches_evergreen`
- `max_results` (optional): Maximum number of failed tasks to return (default: 50)

**Example Usage:**
```json
{
  "tool": "get_patch_failed_jobs_evergreen",
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
      "test_info": {
        "has_test_results": true,
        "failed_test_count": 5,
        "total_test_count": 150
      },
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

### `get_task_logs_evergreen`

Retrieves detailed logs for a specific Evergreen task, with optional error filtering for focused analysis.

**Parameters:**
- `task_id` (required): Task identifier from failed jobs response
- `execution` (optional): Task execution number (default: 0)
- `max_lines` (optional): Maximum number of log lines to return (default: 1000)
- `filter_errors` (optional): Whether to filter for error messages only (default: true)

**Example Usage:**
```json
{
  "tool": "get_task_logs_evergreen",
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

### `get_task_test_results_evergreen`

Retrieves detailed unit test results for a specific Evergreen task, including individual test failures. Essential for debugging unit test failures when a task shows `failed_test_count > 0`.

**Parameters:**
- `task_id` (required): Task identifier from failed jobs response
- `execution` (optional): Task execution number (default: 0)
- `failed_only` (optional): Whether to fetch only failed tests (default: true)
- `limit` (optional): Maximum number of test results to return (default: 100)

**Example Usage:**
```json
{
  "tool": "get_task_test_results_evergreen",
  "arguments": {
    "task_id": "task_456",
    "execution": 0,
    "failed_only": true,
    "limit": 50
  }
}
```

**Response Format:**
```json
{
  "task_info": {
    "task_id": "task_456",
    "task_name": "test-unit",
    "build_variant": "ubuntu2004",
    "status": "failed",
    "execution": 0,
    "has_test_results": true,
    "failed_test_count": 5,
    "total_test_count": 150
  },
  "test_results": [
    {
      "test_id": "test_auth_login_failure",
      "test_file": "tests/auth/test_login.py",
      "status": "failed",
      "duration": 2.5,
      "start_time": "2025-09-23T10:30:15Z",
      "end_time": "2025-09-23T10:30:17Z",
      "exit_code": 1,
      "group_id": "auth_tests",
      "logs": {
        "url": "https://evergreen.mongodb.com/test_log/...",
        "url_parsley": "https://parsley.mongodb.com/evergreen/...",
        "url_raw": "https://evergreen.mongodb.com/test_log_raw/...",
        "line_num": 45,
        "rendering_type": "resmoke",
        "version": 1
      }
    }
  ],
  "summary": {
    "total_test_results": 150,
    "filtered_test_count": 5,
    "returned_tests": 5,
    "failed_tests_in_results": 5,
    "filter_applied": "failed tests only"
  }
}
```

## Running the Server

The Evergreen MCP server is designed to be used with MCP clients (like Claude Desktop, VS Code with MCP extension) or for testing with the MCP Inspector. It communicates via stdio and is not meant to be run as a standalone HTTP server.

### Method 1: Using CLI (Recommended)

The easiest way to run the server is using the installed CLI tool:

```bash
# Basic usage (server will wait for stdio input from MCP client)
evergreen-mcp

# With default project ID
evergreen-mcp --project-id your-evergreen-project-id

# Show help and version
evergreen-mcp --help
evergreen-mcp --version
```

**Note**: The server expects to communicate via stdio with an MCP client. It does not provide an interactive command-line interface or HTTP endpoint when run directly.

### Method 2: With MCP Inspector (for Testing and Development)

```bash
# Using npx with installed CLI (no path needed)
npx @modelcontextprotocol/inspector evergreen-mcp

# Using npx with development setup
npx @modelcontextprotocol/inspector .venv/bin/evergreen-mcp

# This will:
# - Start the MCP server  
# - Launch a web interface for testing
# - Open your browser automatically
```

### Method 3: Using Docker (Alternative)

If you prefer containerized deployment:

```bash
# Run with environment variables
docker run --rm -it \
  -e EVERGREEN_USER=your_username \
  -e EVERGREEN_API_KEY=your_api_key \
  -e EVERGREEN_PROJECT=your_project \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest

# With project ID  
docker run --rm -it \
  -e EVERGREEN_USER=your_username \
  -e EVERGREEN_API_KEY=your_api_key \
  -e EVERGREEN_PROJECT=your_project \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest \
  --project-id your-evergreen-project-id
```

## MCP Client Configuration

### VS Code with MCP Extension

**Using CLI (Recommended):**
```json
{
    "servers": {
        "evergreen-mcp-server": {
            "type": "stdio",
            "command": "evergreen-mcp",
            "args": ["--project-id", "your-evergreen-project-id"]
        }
    }
}
```

**Using Docker (Alternative):**
```json
{
    "servers": {
        "evergreen-mcp-server": {
            "type": "stdio",
            "command": "docker",
            "args": [
                "run", "--rm", "-i",
                "-e", "EVERGREEN_USER=your_username",
                "-e", "EVERGREEN_API_KEY=your_api_key",
                "-e", "EVERGREEN_PROJECT=your_project",
                "ghcr.io/evergreen-ci/evergreen-mcp-server:latest",
                "--project-id", "your-evergreen-project-id"
            ]
        }
    }
}
```

### Claude Desktop

**Using CLI (Recommended):**
```json
{
  "mcpServers": {
    "evergreen": {
      "command": "evergreen-mcp",
      "args": ["--project-id", "your-evergreen-project-id"]
    }
  }
}
```

**Using Docker (Alternative):**
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
        "ghcr.io/evergreen-ci/evergreen-mcp-server:latest",
        "--project-id", "your-evergreen-project-id"
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

   **For VS Code with Augment (using Docker - Recommended):**
   ```json
   {
     "augment.mcpServers": {
       "evergreen": {
         "command": "docker",
         "args": [
           "run", "--rm", "-i",
           "-e", "EVERGREEN_USER=your_username",
           "-e", "EVERGREEN_API_KEY=your_api_key",
           "-e", "EVERGREEN_PROJECT=your_project",
           "ghcr.io/evergreen-ci/evergreen-mcp-server:latest",
           "--project-id", "your-evergreen-project-id"
         ],
         "env": {}
       }
     }
   }
   ```

   **For VS Code with Augment (using local installation):**
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

   **For JetBrains IDEs with Augment (using Docker - Recommended):**
   ```json
   {
     "mcp": {
       "servers": {
         "evergreen": {
           "command": "docker",
           "args": [
             "run", "--rm", "-i",
             "-e", "EVERGREEN_USER=your_username",
             "-e", "EVERGREEN_API_KEY=your_api_key",
             "-e", "EVERGREEN_PROJECT=your_project",
             "ghcr.io/evergreen-ci/evergreen-mcp-server:latest",
             "--project-id", "your-evergreen-project-id"
           ]
         }
       }
     }
   }
   ```

   **For JetBrains IDEs with Augment (using local installation):**
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

2. **Configure MCP in VS Code Settings** (using Docker - Recommended):
   ```json
   {
     "claude.mcpServers": {
       "evergreen": {
         "command": "docker",
         "args": [
           "run", "--rm", "-i",
           "-e", "EVERGREEN_USER=your_username",
           "-e", "EVERGREEN_API_KEY=your_api_key",
           "-e", "EVERGREEN_PROJECT=your_project",
           "ghcr.io/evergreen-ci/evergreen-mcp-server:latest",
           "--project-id", "your-evergreen-project-id"
         ],
         "type": "stdio"
       }
     }
   }
   ```

   **Using local installation:**
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

   **Using Docker (Recommended):**
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
           "ghcr.io/evergreen-ci/evergreen-mcp-server:latest",
           "--project-id", "your-evergreen-project-id"
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
         "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
         "args": ["--project-id", "your-evergreen-project-id"]
       }
     }
   }
   ```

**Setup for JetBrains IDEs:**

1. **Install Claude Plugin**: Install the Claude plugin from JetBrains marketplace

2. **Configure MCP Server**: In Claude plugin settings:

   **Using Docker (Recommended):**
   ```json
   {
     "servers": {
       "evergreen": {
         "command": "docker",
         "args": [
           "run", "--rm", "-i",
           "-e", "EVERGREEN_USER=your_username",
           "-e", "EVERGREEN_API_KEY=your_api_key",
           "-e", "EVERGREEN_PROJECT=your_project",
           "ghcr.io/evergreen-ci/evergreen-mcp-server:latest",
           "--project-id", "your-evergreen-project-id"
         ]
       }
     }
   }
   ```

   **Using local installation:**
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

**VS Code Configuration (using Docker - Recommended):**
```json
{
  "github.copilot.chat.mcp": {
    "servers": {
      "evergreen": {
        "command": "docker",
        "args": [
          "run", "--rm", "-i",
          "-e", "EVERGREEN_USER=your_username",
          "-e", "EVERGREEN_API_KEY=your_api_key",
          "-e", "EVERGREEN_PROJECT=your_project",
          "ghcr.io/evergreen-ci/evergreen-mcp-server:latest",
          "--project-id", "your-evergreen-project-id"
        ]
      }
    }
  }
}
```

**VS Code Configuration (using local installation):**
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

   **Using Docker (Recommended):**
   ```json
   {
     "command": "docker",
     "args": [
       "run", "--rm", "-i",
       "-e", "EVERGREEN_USER=your_username",
       "-e", "EVERGREEN_API_KEY=your_api_key",
       "-e", "EVERGREEN_PROJECT=your_project",
       "ghcr.io/evergreen-ci/evergreen-mcp-server:latest",
       "--project-id", "your-evergreen-project-id"
     ],
     "type": "stdio"
   }
   ```

   **Using local installation:**
   ```json
   {
     "command": "/path/to/your/project/.venv/bin/evergreen-mcp-server",
     "args": ["--project-id", "your-evergreen-project-id"],
     "type": "stdio"
   }
   ```

3. **Set Environment**: 
   - For Docker: Set environment variables as shown in the configuration above
   - For local installation: Ensure your Evergreen credentials are available in `~/.evergreen.yml`

#### Configuration Tips

**Path Resolution:**
- Use absolute paths to the virtual environment binary
- On Windows: Use `.venv\Scripts\evergreen-mcp-server.exe`
- On macOS/Linux: Use `.venv/bin/evergreen-mcp-server`

**Docker Integration (Recommended):**
Many IDE AI tools also support Docker-based MCP servers:
```json
{
  "command": "docker",
  "args": [
    "run", "--rm", "-i",
    "-e", "EVERGREEN_USER=your_username",
    "-e", "EVERGREEN_API_KEY=your_api_key",
    "-e", "EVERGREEN_PROJECT=your_project",
    "ghcr.io/evergreen-ci/evergreen-mcp-server:latest"
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
- Test with Docker first: `docker run --rm -it -e EVERGREEN_USER=your_username -e EVERGREEN_API_KEY=your_api_key -e EVERGREEN_PROJECT=your_project ghcr.io/evergreen-ci/evergreen-mcp-server:latest --help`
- Test with MCP Inspector: `npx @modelcontextprotocol/inspector docker run --rm -i -e EVERGREEN_USER=your_username -e EVERGREEN_API_KEY=your_api_key -e EVERGREEN_PROJECT=your_project ghcr.io/evergreen-ci/evergreen-mcp-server:latest`
- For local installation: Verify the MCP server runs correctly: `evergreen-mcp-server --help`
- Check IDE AI tool logs for MCP connection errors
- Ensure Docker is installed and running for Docker-based configurations

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

#### Method 1: Using Docker (Recommended)

```bash
# Start the inspector with the Docker image (requires Docker environment variables)
npx @modelcontextprotocol/inspector docker run --rm -i \
  -e EVERGREEN_USER=your_username \
  -e EVERGREEN_API_KEY=your_api_key \
  -e EVERGREEN_PROJECT=your_project \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest

# With project ID configuration
npx @modelcontextprotocol/inspector docker run --rm -i \
  -e EVERGREEN_USER=your_username \
  -e EVERGREEN_API_KEY=your_api_key \
  -e EVERGREEN_PROJECT=your_project \
  ghcr.io/evergreen-ci/evergreen-mcp-server:latest \
  --project-id your-evergreen-project-id
```

#### Method 2: Using Local Installation with npx

```bash
# Start the inspector with the Evergreen MCP server using the full path to the virtual environment
npx @modelcontextprotocol/inspector .venv/bin/evergreen-mcp-server

# Or if your virtual environment is activated:
npx @modelcontextprotocol/inspector evergreen-mcp-server
```

#### Method 3: Using Globally Installed Inspector

```bash
# If you installed mcp-inspector globally
mcp-inspector .venv/bin/evergreen-mcp-server

# With project ID configuration
mcp-inspector .venv/bin/evergreen-mcp-server --project-id your-evergreen-project-id
```

#### Method 4: Using Python Module Directly

```bash
# Using the Python module directly
npx @modelcontextprotocol/inspector python -m evergreen_mcp.server
```

### Inspector Features for Evergreen MCP

The MCP Inspector provides several useful features when working with the Evergreen MCP server:

1. **Tool Testing**: Interactive forms to test all available tools:
   - `list_user_recent_patches_evergreen`
   - `get_patch_failed_jobs_evergreen`
   - `get_task_logs_evergreen`
   - `get_task_test_results_evergreen`

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
2. Try `list_user_recent_patches_evergreen` with `limit: 5`
3. Copy a patch ID from the response
4. Use `get_patch_failed_jobs_evergreen` with the copied patch ID
5. Look for tasks with `test_info.failed_test_count > 0` in the response
6. For tasks with test failures, use `get_task_test_results_evergreen` with the task ID to see specific unit test failures
7. Use `get_task_logs_evergreen` with the task ID to see detailed error logs
8. Use test-specific log URLs from the test results for focused debugging

This workflow demonstrates the comprehensive debugging process for CI/CD failures, including unit test analysis, using the Evergreen MCP server.

## Available Resources

The server currently provides the following MCP resources:

### Projects

- **URI Pattern**: `evergreen://project/{project_id}`
- **Description**: Access to Evergreen project information
- **MIME Type**: `application/json`

The server automatically discovers and lists all projects you have access to in your Evergreen instance.

## Usage Examples

### Analyzing Failed Jobs - Multi-Step Workflow

#### Step 1: List Recent Patches

```json
{
  "tool": "list_user_recent_patches_evergreen",
  "arguments": {
    "limit": 10
  }
}
```

This returns your recent patches with status information, allowing you to identify failed patches.

#### Step 2: Analyze Failed Jobs for Selected Patch

```json
{
  "tool": "get_patch_failed_jobs_evergreen",
  "arguments": {
    "patch_id": "507f1f77bcf86cd799439011",
    "max_results": 20
  }
}
```

This now includes test failure counts in the response, showing which tasks have unit test failures.

#### Step 3: Get Detailed Unit Test Results (for tasks with test failures)

```json
{
  "tool": "get_task_test_results_evergreen",
  "arguments": {
    "task_id": "task_456",
    "failed_only": true,
    "limit": 50
  }
}
```

This provides detailed information about individual unit test failures, including test files, durations, and log URLs.

#### Step 4: Getting Detailed Logs for a Failed Task

```json
{
  "tool": "get_task_logs_evergreen",
  "arguments": {
    "task_id": "task_from_failed_jobs_response",
    "filter_errors": true,
    "max_lines": 100
  }
}
```

### Agent Workflow Example

1. **Agent lists user patches**: Calls `list_user_recent_patches_evergreen` to get recent patches
2. **Agent selects relevant patch**: Chooses patch based on status, description, or user input
3. **Agent analyzes failures**: Calls `get_patch_failed_jobs_evergreen` to get detailed failure information with test counts
4. **Agent identifies test failures**: Examines `test_info` in failed tasks to find unit test failures
5. **Agent gets unit test details**: Calls `get_task_test_results_evergreen` for tasks with `failed_test_count > 0`
6. **Agent gets detailed logs**: Calls `get_task_logs_evergreen` for specific failed tasks or uses test-specific log URLs
7. **Agent suggests fixes**: Based on error patterns, specific test failures, and log analysis

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

# After getting failed jobs, agent can identify test failures:
failed_jobs = get_patch_failed_jobs_evergreen(selected_patch['patch_id'])
for task in failed_jobs['failed_tasks']:
    test_info = task.get('test_info', {})
    if test_info.get('has_test_results') and test_info.get('failed_test_count', 0) > 0:
        # This task has unit test failures - get detailed test results
        test_results = get_task_test_results_evergreen(task['task_id'])
        # Analyze specific test failures for targeted suggestions
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
