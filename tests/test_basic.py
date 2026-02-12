#!/usr/bin/env python3
"""
Basic unit tests for Evergreen MCP server components

These tests validate individual components without requiring Evergreen credentials.
"""

import unittest
from unittest.mock import patch


class TestImports(unittest.TestCase):
    """Test that all modules can be imported successfully"""

    def test_import_server(self):
        """Test that server module can be imported"""
        try:
            from evergreen_mcp import server

            self.assertTrue(hasattr(server, "main"), "Server should have main function")
            self.assertTrue(hasattr(server, "mcp"), "Server should have mcp instance")
        except ImportError as e:
            self.fail(f"Failed to import server module: {e}")

    def test_import_tools(self):
        """Test that tools module can be imported"""
        try:
            from evergreen_mcp import mcp_tools

            self.assertTrue(
                hasattr(mcp_tools, "register_tools"),
                "Tools module should have register_tools function",
            )
        except ImportError as e:
            self.fail(f"Failed to import tools module: {e}")

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

    def test_import_failed_jobs_tools(self):
        """Test that failed_jobs_tools module can be imported"""
        try:
            from evergreen_mcp import failed_jobs_tools

            # Check that expected functions exist
            expected_functions = [
                "fetch_user_recent_patches",
                "fetch_patch_failed_jobs",
                "fetch_task_logs",
                "fetch_task_test_results",
            ]
            for func in expected_functions:
                self.assertTrue(
                    hasattr(failed_jobs_tools, func),
                    f"Function {func} should be defined",
                )
        except ImportError as e:
            self.fail(f"Failed to import failed_jobs_tools: {e}")

    def test_import_oidc_auth(self):
        """Test that OIDC auth module can be imported"""
        try:
            from evergreen_mcp.oidc_auth import OIDCAuthManager

            self.assertIsNotNone(
                OIDCAuthManager, "OIDCAuthManager should be importable"
            )
        except ImportError as e:
            self.fail(f"Failed to import OIDCAuthManager: {e}")


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

        # Version should be in format like "0.4.0"
        self.assertRegex(
            __version__,
            r"^\d+\.\d+\.\d+$",
            "Version should follow semantic versioning format (e.g., 0.4.0)",
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


class TestServerComponents(unittest.TestCase):
    """Test server components are properly configured"""

    def test_fastmcp_server_created(self):
        """Test that FastMCP server instance is created"""
        from evergreen_mcp.server import mcp

        self.assertIsNotNone(mcp, "MCP server instance should exist")

    def test_server_has_lifespan(self):
        """Test that server has lifespan configured"""
        from evergreen_mcp.server import lifespan

        self.assertIsNotNone(lifespan, "Lifespan function should be defined")


class TestGraphQLQueriesHostMetadata(unittest.TestCase):
    """Test that GraphQL queries include host metadata fields"""

    def test_patch_failed_tasks_query_includes_host_metadata(self):
        """Test that GET_PATCH_FAILED_TASKS includes host metadata fields"""
        from evergreen_mcp.evergreen_queries import GET_PATCH_FAILED_TASKS

        # Verify host metadata fields are in the query
        self.assertIn("ami", GET_PATCH_FAILED_TASKS, "Query should include 'ami' field")
        self.assertIn(
            "hostId", GET_PATCH_FAILED_TASKS, "Query should include 'hostId' field"
        )
        self.assertIn(
            "distroId", GET_PATCH_FAILED_TASKS, "Query should include 'distroId' field"
        )
        self.assertIn(
            "imageId", GET_PATCH_FAILED_TASKS, "Query should include 'imageId' field"
        )

    def test_version_failed_tasks_query_includes_host_metadata(self):
        """Test that GET_VERSION_WITH_FAILED_TASKS includes host metadata fields"""
        from evergreen_mcp.evergreen_queries import GET_VERSION_WITH_FAILED_TASKS

        # Verify host metadata fields are in the query
        self.assertIn(
            "ami", GET_VERSION_WITH_FAILED_TASKS, "Query should include 'ami' field"
        )
        self.assertIn(
            "hostId",
            GET_VERSION_WITH_FAILED_TASKS,
            "Query should include 'hostId' field",
        )
        self.assertIn(
            "distroId",
            GET_VERSION_WITH_FAILED_TASKS,
            "Query should include 'distroId' field",
        )
        self.assertIn(
            "imageId",
            GET_VERSION_WITH_FAILED_TASKS,
            "Query should include 'imageId' field",
        )

    def test_task_logs_query_includes_host_metadata(self):
        """Test that GET_TASK_LOGS includes host metadata fields"""
        from evergreen_mcp.evergreen_queries import GET_TASK_LOGS

        # Verify host metadata fields are in the query
        self.assertIn("ami", GET_TASK_LOGS, "Query should include 'ami' field")
        self.assertIn("hostId", GET_TASK_LOGS, "Query should include 'hostId' field")
        self.assertIn(
            "distroId", GET_TASK_LOGS, "Query should include 'distroId' field"
        )
        self.assertIn("imageId", GET_TASK_LOGS, "Query should include 'imageId' field")

    def test_task_test_results_query_includes_host_metadata(self):
        """Test that GET_TASK_TEST_RESULTS includes host metadata fields"""
        from evergreen_mcp.evergreen_queries import GET_TASK_TEST_RESULTS

        # Verify host metadata fields are in the query
        self.assertIn("ami", GET_TASK_TEST_RESULTS, "Query should include 'ami' field")
        self.assertIn(
            "hostId", GET_TASK_TEST_RESULTS, "Query should include 'hostId' field"
        )
        self.assertIn(
            "distroId", GET_TASK_TEST_RESULTS, "Query should include 'distroId' field"
        )
        self.assertIn(
            "imageId", GET_TASK_TEST_RESULTS, "Query should include 'imageId' field"
        )


if __name__ == "__main__":
    unittest.main()
