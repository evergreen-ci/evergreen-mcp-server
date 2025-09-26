#!/usr/bin/env python3
"""
Basic unit tests for Evergreen MCP server components

These tests validate individual components without requiring Evergreen credentials.
"""

import sys
import unittest
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mcp_tools import get_tool_definitions, TOOL_HANDLERS


class TestMCPTools(unittest.TestCase):
    """Test MCP tool definitions and handlers"""
    
    def test_tool_definitions_exist(self):
        """Test that tool definitions are properly defined"""
        tools = get_tool_definitions()
        self.assertGreater(len(tools), 0, "Should have at least one tool defined")
        
        # Check that all expected tools are present
        tool_names = [tool.name for tool in tools]
        expected_tools = [
            "list_user_recent_patches",
            "get_patch_failed_jobs",
            "get_task_logs"
        ]
        
        for expected_tool in expected_tools:
            self.assertIn(expected_tool, tool_names, f"Tool {expected_tool} should be defined")
    
    def test_tool_handlers_exist(self):
        """Test that all tool handlers are properly registered"""
        tools = get_tool_definitions()
        
        for tool in tools:
            self.assertIn(tool.name, TOOL_HANDLERS, 
                         f"Handler for tool {tool.name} should be registered")
            self.assertIsNotNone(TOOL_HANDLERS[tool.name], 
                               f"Handler for tool {tool.name} should not be None")
    
    def test_tool_definitions_have_required_fields(self):
        """Test that tool definitions have all required fields"""
        tools = get_tool_definitions()
        
        for tool in tools:
            self.assertIsNotNone(tool.name, "Tool should have a name")
            self.assertIsNotNone(tool.description, "Tool should have a description")
            self.assertGreater(len(tool.name), 0, "Tool name should not be empty")
            self.assertGreater(len(tool.description), 0, "Tool description should not be empty")


class TestImports(unittest.TestCase):
    """Test that all modules can be imported successfully"""
    
    def test_import_server(self):
        """Test that server module can be imported"""
        try:
            from src import server
            self.assertTrue(hasattr(server, 'main'), "Server should have main function")
        except ImportError as e:
            self.fail(f"Failed to import server module: {e}")

    def test_import_graphql_client(self):
        """Test that GraphQL client can be imported"""
        try:
            from src.evergreen_graphql_client import EvergreenGraphQLClient
            self.assertIsNotNone(EvergreenGraphQLClient, "EvergreenGraphQLClient should be importable")
        except ImportError as e:
            self.fail(f"Failed to import EvergreenGraphQLClient: {e}")
    
    def test_import_queries(self):
        """Test that queries module can be imported"""
        try:
            from src import evergreen_queries
            # Check that some expected queries exist
            expected_queries = [
                'GET_PROJECTS', 'GET_PROJECT', 'GET_USER_RECENT_PATCHES',
                'GET_PATCH_FAILED_TASKS', 'GET_TASK_LOGS'
            ]
            for query in expected_queries:
                self.assertTrue(hasattr(evergreen_queries, query),
                              f"Query {query} should be defined")
        except ImportError as e:
            self.fail(f"Failed to import evergreen_queries: {e}")


if __name__ == "__main__":
    unittest.main()
