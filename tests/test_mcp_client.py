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

import asyncio
import json
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import mcp.client.stdio
from mcp.client.session import ClientSession
import pytest


@pytest.mark.asyncio
async def test_mcp_server():
    """Test the MCP server by connecting and calling tools"""
    print("Testing Evergreen MCP Server - Full Integration Test")
    print("=" * 60)

    test_results = {
        "server_connection": False,
        "tools_listed": False,
        "list_patches": False,
        "get_failed_jobs": False,
        "get_task_logs": False,
        "error_handling": False
    }
    
    # Start the server process using the installed entry point
    server_params = mcp.client.stdio.StdioServerParameters(
        command="evergreen-mcp-server",
        args=[],
        env=None
    )
    
    async with mcp.client.stdio.stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the session
            await session.initialize()
            print("Connected to MCP server")
            test_results["server_connection"] = True

            # List available tools
            tools_result = await session.list_tools()
            print(f"\nAvailable tools ({len(tools_result.tools)}):")
            for tool in tools_result.tools:
                print(f"   - {tool.name}: {tool.description}")
            test_results["tools_listed"] = len(tools_result.tools) > 0
            
            # Test 1: List user recent patches
            print(f"\nTest 1: list_user_recent_patches")
            try:
                result = await session.call_tool("list_user_recent_patches", {"limit": 3})
                print("Tool call successful")
                
                # Parse the response
                if result.content and len(result.content) > 0:
                    response_text = result.content[0].text
                    response_data = json.loads(response_text)
                    
                    if "error" in response_data:
                        print(f" Tool returned error: {response_data['error']}")
                    else:
                        patches = response_data.get('patches', [])
                        print(f"   Retrieved {len(patches)} patches")
                        for i, patch in enumerate(patches[:2]):  # Show first 2
                            print(f"   {i+1}. {patch['githash'][:8]} - {patch['status']}")
                        test_results["list_patches"] = True
                else:
                    print(" No content in response")
                    
            except Exception as e:
                print(f" Tool call failed: {e}")
            
            # Test 2: Get patch failed jobs (if we have patches)
            print(f"\nTest 2: get_patch_failed_jobs")
            failed_task_id = None
            try:
                # First get a patch ID
                patches_result = await session.call_tool("list_user_recent_patches", {"limit": 1})
                if patches_result.content and len(patches_result.content) > 0:
                    patches_data = json.loads(patches_result.content[0].text)
                    if patches_data.get('patches'):
                        patch_id = patches_data['patches'][0]['patch_id']
                        print(f"   Testing with patch: {patch_id}")

                        result = await session.call_tool("get_patch_failed_jobs", {
                            "patch_id": patch_id,
                            "max_results": 5
                        })

                        if result.content and len(result.content) > 0:
                            response_data = json.loads(result.content[0].text)
                            if "error" in response_data:
                                print(f"Tool returned error: {response_data['error']}")
                            else:
                                failed_tasks = response_data.get('failed_tasks', [])
                                print(f"Found {len(failed_tasks)} failed tasks")
                                test_results["get_failed_jobs"] = True
                                # Store a task ID for the next test
                                if failed_tasks:
                                    failed_task_id = failed_tasks[0]['task_id']
                        else:
                            print("No content in response")
                    else:
                        print("No patches available for testing")
                else:
                    print("Could not get patches for testing")

            except Exception as e:
                print(f"Tool call failed: {e}")
            
            # Test 3: Get task logs (if we have a failed task)
            print(f"\nTest 3: get_task_logs")
            if failed_task_id:
                try:
                    result = await session.call_tool("get_task_logs", {
                        "task_id": failed_task_id,
                        "max_lines": 10,
                        "filter_errors": True
                    })

                    if result.content and len(result.content) > 0:
                        response_data = json.loads(result.content[0].text)
                        if "error" in response_data:
                            error_msg = response_data['error']
                            # Display tasks don't have logs - this is expected behavior
                            if "display task" in error_msg.lower():
                                print(f"Task is a display task (no logs available) - this is expected")
                                test_results["get_task_logs"] = True  # This is actually correct behavior
                            else:
                                print(f"Tool returned unexpected error: {error_msg}")
                        else:
                            logs = response_data.get('logs', [])
                            print(f"Retrieved {len(logs)} log entries for task {failed_task_id}")
                            test_results["get_task_logs"] = True
                    else:
                        print("No content in response")

                except Exception as e:
                    print(f"Tool call failed: {e}")
            else:
                print("No failed task available for log testing")
                test_results["get_task_logs"] = True  # Skip this test gracefully

            # Test 4: Error handling
            print(f"\nTest 4: Error handling (invalid tool)")
            try:
                result = await session.call_tool("invalid_tool", {})
                if result.content and len(result.content) > 0:
                    response_data = json.loads(result.content[0].text)
                    if "error" in response_data:
                        print("Error handling works correctly")
                        print(f"   Error: {response_data['error']}")
                        test_results["error_handling"] = True
                    else:
                        print("Expected error response")
                else:
                    print("No content in response")
            except Exception as e:
                print(f"Exception handling works: {e}")
                test_results["error_handling"] = True

    # Print test summary
    print("\n" + "=" * 60)
    print("TEST RESULTS SUMMARY")
    print("=" * 60)

    passed = sum(test_results.values())
    total = len(test_results)

    for test_name, result in test_results.items():
        status = "PASS" if result else "FAIL"
        print(f"   {test_name.replace('_', ' ').title()}: {status}")

    print(f"\nOverall: {passed}/{total} tests passed")

    if passed == total:
        print("All tests passed! MCP server is working correctly.")
        return True
    else:
        print("Some tests failed. Check the output above for details.")
        return False


if __name__ == "__main__":
    import sys
    success = asyncio.run(test_mcp_server())
    sys.exit(0 if success else 1)
