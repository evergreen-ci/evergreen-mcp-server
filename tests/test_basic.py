#!/usr/bin/env python3
"""
Basic unit tests for Evergreen MCP server components

These tests validate individual components without requiring Evergreen credentials.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from evergreen_mcp.mcp_tools import TOOL_HANDLERS, get_tool_definitions


class TestMCPTools(unittest.TestCase):
    """Test MCP tool definitions and handlers"""

    def test_tool_definitions_exist(self):
        """Test that tool definitions are properly defined"""
        tools = get_tool_definitions()
        self.assertGreater(len(tools), 0, "Should have at least one tool defined")

        # Check that all expected tools are present
        tool_names = [tool.name for tool in tools]
        expected_tools = [
            "list_user_recent_patches_evergreen",
            "get_patch_failed_jobs_evergreen",
            "get_task_logs_evergreen",
            "get_task_test_results_evergreen",
        ]

        for expected_tool in expected_tools:
            self.assertIn(
                expected_tool, tool_names, f"Tool {expected_tool} should be defined"
            )

    def test_tool_handlers_exist(self):
        """Test that all tool handlers are properly registered"""
        tools = get_tool_definitions()

        for tool in tools:
            self.assertIn(
                tool.name,
                TOOL_HANDLERS,
                f"Handler for tool {tool.name} should be registered",
            )
            self.assertIsNotNone(
                TOOL_HANDLERS[tool.name],
                f"Handler for tool {tool.name} should not be None",
            )

    def test_tool_definitions_have_required_fields(self):
        """Test that tool definitions have all required fields"""
        tools = get_tool_definitions()

        for tool in tools:
            self.assertIsNotNone(tool.name, "Tool should have a name")
            self.assertIsNotNone(tool.description, "Tool should have a description")
            self.assertGreater(len(tool.name), 0, "Tool name should not be empty")
            self.assertGreater(
                len(tool.description), 0, "Tool description should not be empty"
            )


class TestImports(unittest.TestCase):
    """Test that all modules can be imported successfully"""

    def test_import_server(self):
        """Test that server module can be imported"""
        try:
            from evergreen_mcp import server

            self.assertTrue(hasattr(server, "main"), "Server should have main function")
        except ImportError as e:
            self.fail(f"Failed to import server module: {e}")

    def test_import_graphql_client(self):
        """Test that GraphQL client can be imported"""
        try:
            from evergreen_mcp.evergreen_graphql_client import EvergreenGraphQLClient

            self.assertIsNotNone(
                EvergreenGraphQLClient, "EvergreenGraphQLClient should be importable"
            )
        except ImportError as e:
            self.fail(f"Failed to import EvergreenGraphQLClient: {e}")

    def test_import_queries(self):
        """Test that queries module can be imported"""
        try:
            from evergreen_mcp import evergreen_queries

            # Check that some expected queries exist
            expected_queries = [
                "GET_PROJECTS",
                "GET_PROJECT",
                "GET_USER_RECENT_PATCHES",
                "GET_PATCH_FAILED_TASKS",
                "GET_TASK_LOGS",
                "GET_TASK_TEST_RESULTS",
            ]
            for query in expected_queries:
                self.assertTrue(
                    hasattr(evergreen_queries, query),
                    f"Query {query} should be defined",
                )
        except ImportError as e:
            self.fail(f"Failed to import evergreen_queries: {e}")


class TestVersion(unittest.TestCase):
    """Test version constant"""

    def test_version_exists(self):
        """Test that __version__ constant exists"""
        from evergreen_mcp import __version__

        self.assertIsNotNone(__version__, "Version should be defined")
        self.assertIsInstance(__version__, str, "Version should be a string")
        self.assertGreater(len(__version__), 0, "Version should not be empty")

    def test_version_format(self):
        """Test that version follows expected format"""
        from evergreen_mcp import __version__

        # Version should be in format like "0.1.0"
        self.assertRegex(
            __version__,
            r"^\d+\.\d+\.\d+$",
            "Version should follow semantic versioning format (e.g., 0.1.0)",
        )


class TestUserAgent(unittest.TestCase):
    """Test User-Agent header in GraphQL client"""

    @patch("evergreen_mcp.evergreen_graphql_client.AIOHTTPTransport")
    @patch("evergreen_mcp.evergreen_graphql_client.Client")
    def test_user_agent_header_set(self, mock_client, mock_transport):
        """Test that User-Agent header is set correctly"""
        import asyncio

        from evergreen_mcp import __version__
        from evergreen_mcp.evergreen_graphql_client import EvergreenGraphQLClient

        async def run_test():
            # Create client instance
            client = EvergreenGraphQLClient(
                user="test_user",
                api_key="test_key",
                endpoint="https://test.example.com",
            )

            # Connect (which should set headers)
            await client.connect()

            # Verify that AIOHTTPTransport was called with correct headers
            mock_transport.assert_called_once()
            call_kwargs = mock_transport.call_args.kwargs

            # Check that headers were passed
            self.assertIn("headers", call_kwargs)
            headers = call_kwargs["headers"]

            # Verify User-Agent header exists and has correct format
            self.assertIn("User-Agent", headers)
            expected_user_agent = f"evergreen-mcp-server/{__version__}"
            self.assertEqual(
                headers["User-Agent"],
                expected_user_agent,
                f"User-Agent should be '{expected_user_agent}'",
            )

        # Run the async test
        asyncio.run(run_test())

    def test_user_agent_format(self):
        """Test that User-Agent follows expected format"""
        from evergreen_mcp import __version__

        expected_user_agent = f"evergreen-mcp-server/{__version__}"

        # Should be in format "evergreen-mcp-server/x.y.z"
        self.assertRegex(
            expected_user_agent,
            r"^evergreen-mcp-server/\d+\.\d+\.\d+$",
            "User-Agent should be in format 'evergreen-mcp-server/x.y.z'",
        )


if __name__ == "__main__":
    unittest.main()
