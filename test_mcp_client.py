#!/usr/bin/env python3
"""
Simple MCP client to test the Evergreen MCP server
"""

import asyncio
import json
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

import mcp.client.stdio
from mcp.client.session import ClientSession


async def test_mcp_server():
    """Test the MCP server by connecting and calling tools"""
    print("🔧 Testing Evergreen MCP Server")
    print("=" * 40)
    
    # Start the server process
    server_params = mcp.client.stdio.StdioServerParameters(
        command="python",
        args=["run_mcp_server.py"],
        env=None
    )
    
    async with mcp.client.stdio.stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the session
            await session.initialize()
            print("✅ Connected to MCP server")
            
            # List available tools
            tools_result = await session.list_tools()
            print(f"\n📋 Available tools ({len(tools_result.tools)}):")
            for tool in tools_result.tools:
                print(f"   - {tool.name}: {tool.description}")
            
            # Test 1: List user recent patches
            print(f"\n🧪 Test 1: list_user_recent_patches")
            try:
                result = await session.call_tool("list_user_recent_patches", {"limit": 3})
                print("✅ Tool call successful")
                
                # Parse the response
                if result.content and len(result.content) > 0:
                    response_text = result.content[0].text
                    response_data = json.loads(response_text)
                    
                    if "error" in response_data:
                        print(f"❌ Tool returned error: {response_data['error']}")
                    else:
                        patches = response_data.get('patches', [])
                        print(f"   Retrieved {len(patches)} patches")
                        for i, patch in enumerate(patches[:2]):  # Show first 2
                            print(f"   {i+1}. {patch['githash'][:8]} - {patch['status']}")
                else:
                    print("❌ No content in response")
                    
            except Exception as e:
                print(f"❌ Tool call failed: {e}")
            
            # Test 2: Get patch failed jobs (if we have patches)
            print(f"\n🧪 Test 2: get_patch_failed_jobs")
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
                                print(f"❌ Tool returned error: {response_data['error']}")
                            else:
                                failed_tasks = response_data.get('failed_tasks', [])
                                print(f"✅ Found {len(failed_tasks)} failed tasks")
                        else:
                            print("❌ No content in response")
                    else:
                        print("⚠️  No patches available for testing")
                else:
                    print("⚠️  Could not get patches for testing")
                    
            except Exception as e:
                print(f"❌ Tool call failed: {e}")
            
            # Test 3: Error handling
            print(f"\n🧪 Test 3: Error handling (invalid tool)")
            try:
                result = await session.call_tool("invalid_tool", {})
                if result.content and len(result.content) > 0:
                    response_data = json.loads(result.content[0].text)
                    if "error" in response_data:
                        print("✅ Error handling works correctly")
                        print(f"   Error: {response_data['error']}")
                    else:
                        print("❌ Expected error response")
                else:
                    print("❌ No content in response")
            except Exception as e:
                print(f"✅ Exception handling works: {e}")
    
    print("\n" + "=" * 40)
    print("🎉 MCP server test complete!")


if __name__ == "__main__":
    asyncio.run(test_mcp_server())
