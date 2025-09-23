#!/usr/bin/env python3
"""
Manual MCP protocol test - sends raw JSON-RPC messages to test the server
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path


async def test_mcp_protocol():
    """Test MCP server using raw JSON-RPC protocol"""
    print("🔧 Testing MCP Server with Raw Protocol")
    print("=" * 45)
    
    # Start the server process
    process = subprocess.Popen(
        ["python", "run_mcp_server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd="/Users/w.trocki/Projects/evergreen/evergreen-mcp-server"
    )
    
    def send_message(message):
        """Send a JSON-RPC message to the server"""
        json_msg = json.dumps(message)
        print(f"📤 Sending: {json_msg}")
        process.stdin.write(json_msg + "\n")
        process.stdin.flush()
        
        # Read response
        try:
            response = process.stdout.readline()
            if response:
                print(f"📥 Received: {response.strip()}")
                return json.loads(response.strip())
            else:
                print("❌ No response received")
                return None
        except json.JSONDecodeError as e:
            print(f"❌ Invalid JSON response: {e}")
            return None
    
    try:
        # 1. Initialize the connection
        print("\n1️⃣ Initializing connection...")
        init_message = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {
                        "listChanged": True
                    },
                    "sampling": {}
                },
                "clientInfo": {
                    "name": "test-client",
                    "version": "1.0.0"
                }
            }
        }
        
        response = send_message(init_message)
        if response and response.get("result"):
            print("✅ Server initialized successfully")
            server_info = response["result"]
            print(f"   Server: {server_info.get('serverInfo', {}).get('name', 'unknown')}")
            print(f"   Version: {server_info.get('serverInfo', {}).get('version', 'unknown')}")
        else:
            print("❌ Initialization failed")
            return
        
        # 2. List tools
        print("\n2️⃣ Listing available tools...")
        list_tools_message = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        
        response = send_message(list_tools_message)
        if response and response.get("result"):
            tools = response["result"].get("tools", [])
            print(f"✅ Found {len(tools)} tools:")
            for tool in tools:
                print(f"   - {tool['name']}: {tool['description']}")
        else:
            print("❌ Failed to list tools")
        
        # 3. Call a tool
        print("\n3️⃣ Calling list_user_recent_patches tool...")
        call_tool_message = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "list_user_recent_patches",
                "arguments": {
                    "limit": 2
                }
            }
        }
        
        response = send_message(call_tool_message)
        if response and response.get("result"):
            content = response["result"].get("content", [])
            if content:
                tool_response = json.loads(content[0]["text"])
                if "error" in tool_response:
                    print(f"❌ Tool error: {tool_response['error']}")
                else:
                    patches = tool_response.get("patches", [])
                    print(f"✅ Tool returned {len(patches)} patches")
                    for patch in patches[:2]:
                        print(f"   - {patch['githash'][:8]}: {patch['status']}")
            else:
                print("❌ No content in tool response")
        else:
            print("❌ Tool call failed")
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Clean up
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        
        print("\n" + "=" * 45)
        print("🎉 Manual protocol test complete!")


if __name__ == "__main__":
    asyncio.run(test_mcp_protocol())
