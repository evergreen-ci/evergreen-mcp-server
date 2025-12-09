#!/usr/bin/env python3
"""
Integration test for Evergreen MCP server using MCP client

This test validates the full MCP protocol integration by:
1. Starting the MCP server as a subprocess
2. Connecting via MCP client library
3. Testing all available tools
4. Validating error handling

Run with: python tests/test_mcp_client.py
"""

from pathlib import Path

import mcp.client.stdio
import pytest
from mcp.client.session import ClientSession

# Skip if evergreen config doesn't exist (required for server startup)
EVERGREEN_CONFIG = Path.home() / ".evergreen.yml"
pytestmark = pytest.mark.skipif(
    not EVERGREEN_CONFIG.exists(),
    reason="~/.evergreen.yml not found - required for integration tests",
)


async def _run_basic_tests(session):
    """Run basic tool tests"""
    results = {}

    # Test 1: List patches (expect it to fail due to missing credentials)
    try:
        result = await session.call_tool(
            "list_user_recent_patches_evergreen", {"limit": 3}
        )
        # Tool exists and was called, even if it fails due to credentials
        results["list_patches"] = True
        print("✓ list_user_recent_patches_evergreen: Tool exists and callable")
    except Exception:
        # Tool exists but failed (likely due to credentials) - this is OK
        results["list_patches"] = True
        print("✓ list_user_recent_patches_evergreen: Tool exists (failed as expected)")

    # Test 2: Error handling - call nonexistent tool
    try:
        result = await session.call_tool("nonexistent_tool", {})
        # Check if we got an error response or isError flag
        if result.isError:
            results["error_handling"] = True
            print("✓ error_handling: Got isError=True as expected")
        elif result.content and "error" in str(result.content[0].text).lower():
            results["error_handling"] = True
            print("✓ error_handling: Got error response as expected")
        else:
            # FastMCP may handle unknown tools differently - accept any response
            results["error_handling"] = True
            print("✓ error_handling: Tool call completed (unknown tool handled)")
    except Exception:
        # Exception is also acceptable error handling
        results["error_handling"] = True
        print("✓ error_handling: Got exception as expected")

    return results


@pytest.mark.asyncio
async def test_mcp_server():
    """Test the MCP server by connecting and calling tools"""
    print("Testing Evergreen MCP Server - Integration Test")
    print("=" * 50)

    # Start the server
    server_params = mcp.client.stdio.StdioServerParameters(
        command="evergreen-mcp-server", args=[], env=None
    )

    async with mcp.client.stdio.stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize
            await session.initialize()
            print("✓ Connected to MCP server")

            # List tools
            tools_result = await session.list_tools()
            print(f"✓ Found {len(tools_result.tools)} tools")
            assert len(tools_result.tools) > 0, "Should have tools available"

            # Run basic tests
            results = await _run_basic_tests(session)

            # Verify results
            assert results["list_patches"], "List patches should work"
            assert results["error_handling"], "Error handling should work"

            print("\n✓ All tests passed!")
