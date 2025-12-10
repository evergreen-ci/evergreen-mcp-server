# Intelligent Project ID Auto-Detection

## Overview

The Evergreen MCP Server now includes intelligent automatic detection of project IDs, eliminating the need for users to manually specify which Evergreen project they're working with.

## How It Works

### The Auto-Detection Process

When a tool is called without a `project_id` parameter:

1. **Check Default Configuration**
   - First checks if a default project ID is configured via environment variables or config file

2. **Intelligent Inference** (if no default)
   - Fetches user's recent patches (up to 50) to discover active projects
   - Analyzes the current workspace directory path
   - Correlates directory names with project identifiers using smart matching:
     * **Score 100**: Exact directory name match (e.g., `/path/to/mms` → `mms`)
     * **Score 80**: Directory contains project ID (e.g., `/path/to/mms-frontend` → `mms`)
     * **Score 70**: Project ID appears in path components
     * **Score 60**: Project ID contains directory name
     * **Score 40+**: Partial substring matches
   - If confidence score ≥ 40, uses the matched project
   - Otherwise, defaults to the most active project (highest patch count)

3. **Transparent Logging**
   - Logs the detected project ID and confidence score
   - Provides clear reasoning for why a project was chosen

## Implementation Details

### Modified Files

#### 1. `server.py`
- Added `workspace_dir` field to `EvergreenContext` dataclass
- Updated `lifespan` manager to capture and pass workspace directory
- Added system prompt explaining auto-detection to AI agents

#### 2. `failed_jobs_tools.py`
- Added `infer_project_id_from_context()` function:
  - Fetches user's recent patches
  - Correlates workspace path with project identifiers
  - Returns best matching project ID with confidence scoring

#### 3. `mcp_tools.py`
- Updated `list_user_recent_patches_evergreen` tool
- Updated `get_patch_failed_jobs_evergreen` tool
- Both tools now automatically invoke intelligent inference when project_id is not provided

### New GraphQL Query

Added `GET_INFERRED_PROJECT_IDS` query in `evergreen_queries.py`:

```graphql
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
        projectIdentifier
      }
    }
  }
}
```

### New MCP Tool

Added `get_inferred_project_ids_evergreen` tool that returns:

```json
{
  "user_id": "user@example.com",
  "projects": [
    {
      "project_identifier": "mms",
      "patch_count": 6,
      "latest_patch_time": "2025-10-28T17:08:11.734Z"
    }
  ],
  "total_projects": 1,
  "patches_scanned": 6,
  "max_patches": 50
}
```

## Usage Examples

### For End Users

```python
# Before: Had to specify project_id
list_user_recent_patches_evergreen(project_id="mms", limit=10)

# Now: Auto-detection handles it
list_user_recent_patches_evergreen(limit=10)
# System automatically detects project from workspace
```

### For AI Agents

AI agents should now simply call tools without project_id when users don't specify it:

```
User: "Show me my recent patches"
AI: *calls list_user_recent_patches_evergreen() without project_id*
System: *auto-detects "mms" from workspace /Users/user/projects/mms*
```

## Matching Algorithm Details

The matching algorithm uses a scoring system:

```python
# Scoring rules (higher is better):
- 100: Exact match (basename == project_id)
- 80:  Directory name contains project_id
- 70:  Project_id in path components
- 60:  Project_id contains directory name
- 40:  Partial substring match (≥3 chars)

# Threshold: score ≥ 40 required for match
# Fallback: Most active project (by patch count)
```

### Example Matches

| Workspace Path | Project IDs | Match | Score | Reasoning |
|----------------|-------------|-------|-------|-----------|
| `/work/mms` | `["mms", "server"]` | `mms` | 100 | Exact basename match |
| `/work/mms-api` | `["mms", "server"]` | `mms` | 80 | Directory contains project |
| `/work/project` | `["server"]` | `server` | 0→fallback | No match, use most active |
| `/mongodb-mongo/src` | `["mongodb-mongo-master"]` | `mongodb-mongo-master` | 60 | Project contains directory |

## Benefits

1. **Reduced Friction**: Users don't need to remember or look up project IDs
2. **Context-Aware**: Understands where the user is working
3. **Intelligent**: Uses actual patch history to make informed decisions
4. **Transparent**: Clear logging explains detection reasoning
5. **Fallback Safety**: Always has a sensible default (most active project)

## Logging Examples

```
INFO: No project_id specified, attempting intelligent auto-detection...
INFO: Fetching inferred project IDs for user shreeven.kommireddy (max 50 patches)
INFO: Retrieved 6 patches for inferring project IDs (page 0)
INFO: Successfully inferred 1 unique project IDs from 6 patches
INFO: Attempting to match workspace '/Users/user/projects/mms' with projects: ['mms']
INFO: Inferred project ID 'mms' from workspace (confidence score: 100)
INFO: Auto-detected project ID: mms
INFO: Using project ID: mms
```

## Performance Considerations

- **Caching**: Project inference results could be cached per session
- **Lazy Evaluation**: Only runs when project_id is actually needed
- **Minimal Overhead**: Single GraphQL query fetches up to 50 patches
- **Smart Limits**: Configurable max_patches parameter (default 50)

## Future Enhancements

Possible improvements:
1. Cache inferred project IDs for the session
2. Add explicit cache invalidation when switching workspaces
3. Support for multi-project workspaces (monorepos)
4. Machine learning-based prediction using historical patterns
5. Integration with Git remote URLs for even smarter detection

## Testing

To test the auto-detection:

```bash
# Change to a project directory
cd ~/projects/mms

# Run tool without project_id
# Should auto-detect "mms" from directory name
list_user_recent_patches_evergreen(limit=5)
```

## Configuration

Auto-detection respects existing configuration:

1. **Explicit parameter** > Auto-detection
2. **Default from config** > Auto-detection  
3. **Auto-detection** > No filtering

Priority order:
```
tool(project_id="explicit")  # Uses "explicit"
  OR
default_project_id from config  # Uses config value
  OR
infer_project_id_from_context()  # Uses auto-detection
  OR
None  # Returns all projects
```

## Troubleshooting

**Issue**: Wrong project detected
- **Solution**: Explicitly pass `project_id` parameter
- **Check**: Review log messages for matching reasoning

**Issue**: No projects found
- **Solution**: Ensure you have recent patches in Evergreen
- **Check**: Call `get_inferred_project_ids_evergreen()` directly

**Issue**: Slow performance
- **Solution**: Reduce `max_patches` parameter (default 50)
- **Note**: Lower values may reduce detection accuracy

## AI Agent Guidelines

When integrating with AI agents, the system prompt explains:

1. **DO NOT** prompt users for project_id unless they explicitly need a different project
2. **DO NOT** call `get_inferred_project_ids_evergreen` separately before other tools
3. **DO** call tools without project_id and let auto-detection work
4. **DO** trust the system to log its detection reasoning

The auto-detection is designed to be **transparent and automatic**, requiring no special handling by AI agents or users.






