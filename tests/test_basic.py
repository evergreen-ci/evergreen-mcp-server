#!/usr/bin/env python3
"""
Basic unit tests for Evergreen MCP server components

These tests validate individual components without requiring Evergreen credentials.
"""

import unittest
from unittest.mock import AsyncMock, patch


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
                "GET_USER_RECENT_PATCHES",
                "GET_PATCH_TASKS",
                "GET_PATCH_STATUS_SUMMARY",
                "GET_VERSION_STATUS_SUMMARY",
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
                "fetch_patch_jobs",
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

        # Version should be in format like "0.4.2"
        self.assertRegex(
            __version__,
            r"^\d+\.\d+\.\d+$",
            "Version should follow semantic versioning format (e.g., 0.4.2)",
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
            mock_client.return_value.connect_async = AsyncMock()
            await client.connect()

            # Verify that AIOHTTPTransport was called with correct headers
            mock_transport.assert_called_once()
            call_kwargs = mock_transport.call_args.kwargs

            # Check that headers were passed
            self.assertIn("headers", call_kwargs)
            headers = call_kwargs["headers"]

            # Verify User-Agent header exists and starts with correct base format
            self.assertIn("User-Agent", headers)
            expected_base = f"evergreen-mcp-server/{__version__}"
            self.assertTrue(
                headers["User-Agent"].startswith(expected_base),
                f"User-Agent should start with '{expected_base}', got '{headers['User-Agent']}'",
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


class TestUserAgentConstant(unittest.TestCase):
    """Test the shared USER_AGENT constant"""

    def test_user_agent_constant_exists(self):
        """Test that USER_AGENT constant is exported from the package"""
        from evergreen_mcp import USER_AGENT

        self.assertIsNotNone(USER_AGENT, "USER_AGENT should be defined")
        self.assertIsInstance(USER_AGENT, str, "USER_AGENT should be a string")

    def test_user_agent_constant_value(self):
        """Test that USER_AGENT starts with expected base format with current version"""
        from evergreen_mcp import USER_AGENT, __version__

        expected_base = f"evergreen-mcp-server/{__version__}"
        self.assertTrue(
            USER_AGENT.startswith(expected_base),
            f"USER_AGENT should start with '{expected_base}', got '{USER_AGENT}'",
        )

    def test_user_agent_constant_format(self):
        """Test that USER_AGENT follows the pattern evergreen-mcp-server/x.y.z with optional user_id suffix"""
        from evergreen_mcp import USER_AGENT

        self.assertRegex(
            USER_AGENT,
            r"^evergreen-mcp-server/\d+\.\d+\.\d+(/user_id=\S+)?$",
            "USER_AGENT should be in format 'evergreen-mcp-server/x.y.z' with optional '/user_id=...' suffix",
        )

    def test_graphql_client_uses_shared_constant(self):
        """Test that the GraphQL client imports USER_AGENT from the package"""
        from evergreen_mcp import USER_AGENT, evergreen_graphql_client

        # The module should reference the shared constant
        self.assertTrue(
            hasattr(evergreen_graphql_client, "USER_AGENT"),
            "evergreen_graphql_client should import USER_AGENT",
        )
        self.assertEqual(evergreen_graphql_client.USER_AGENT, USER_AGENT)

    def test_oidc_auth_uses_shared_constant(self):
        """Test that the OIDC auth module imports USER_AGENT from the package"""
        from evergreen_mcp import USER_AGENT, oidc_auth

        self.assertTrue(
            hasattr(oidc_auth, "USER_AGENT"),
            "oidc_auth should import USER_AGENT",
        )
        self.assertEqual(oidc_auth.USER_AGENT, USER_AGENT)


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

    def test_patch_tasks_query_includes_host_metadata(self):
        """Test that GET_PATCH_TASKS includes host metadata fields"""
        from evergreen_mcp.evergreen_queries import GET_PATCH_TASKS

        self.assertIn("ami", GET_PATCH_TASKS, "Query should include 'ami' field")
        self.assertIn("hostId", GET_PATCH_TASKS, "Query should include 'hostId' field")
        self.assertIn(
            "distroId", GET_PATCH_TASKS, "Query should include 'distroId' field"
        )
        self.assertIn(
            "imageId", GET_PATCH_TASKS, "Query should include 'imageId' field"
        )

    def test_version_tasks_query_includes_host_metadata(self):
        """Test that GET_VERSION_TASKS includes host metadata fields"""
        from evergreen_mcp.evergreen_queries import GET_VERSION_TASKS

        self.assertIn("ami", GET_VERSION_TASKS, "Query should include 'ami' field")
        self.assertIn(
            "hostId", GET_VERSION_TASKS, "Query should include 'hostId' field"
        )
        self.assertIn(
            "distroId", GET_VERSION_TASKS, "Query should include 'distroId' field"
        )
        self.assertIn(
            "imageId", GET_VERSION_TASKS, "Query should include 'imageId' field"
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


class TestErrorRegexWordBoundaries(unittest.TestCase):
    """Short numeric keywords must not match inside longer numbers.

    Every false positive here drags an unrelated line into the log summary that
    get_task_log_detailed returns, so this is both a signal and a size problem.
    """

    def test_numeric_keywords_do_not_match_inside_numbers(self):
        from evergreen_mcp.utils import _ERROR_RE

        for line in (
            "line 2403 all quiet",
            "wrote 4401 bytes",
            "elapsed 14036ms",
            "task_id sys_perf_403abc",
        ):
            self.assertEqual(_ERROR_RE.findall(line), [], line)

    def test_real_status_codes_still_match(self):
        from evergreen_mcp.utils import _ERROR_RE

        self.assertTrue(_ERROR_RE.findall("HTTP 403 Forbidden"))
        self.assertTrue(_ERROR_RE.findall("got 401 from the API"))
        self.assertTrue(_ERROR_RE.findall("502 Bad Gateway"))

    def test_word_keywords_still_match(self):
        from evergreen_mcp.utils import _ERROR_RE

        for line in (
            "panic: runtime error",
            "Traceback (most recent call last):",
            "AssertionError: nope",
            "FAIL test_thing",
        ):
            self.assertTrue(_ERROR_RE.findall(line), line)

    def test_plural_and_compound_forms_still_match(self):
        """Alphabetic keywords must stay substring matches.

        Anchoring them on word boundaries silently destroys recall on the forms
        that actually appear in logs. A log whose only signal is "3 errors
        detected" would fall through to the raw-text path with no error summary
        at all — worse than before, for exactly the debugging case this exists
        for.
        """
        from evergreen_mcp.utils import _ERROR_RE

        for line in (
            "errors were found",
            "3 failures: see above",
            "FatalError: boom",
            "UnhandledError occurred",
            "TestFailure detected",
            "ConnectionResetError while dialing",
            "panicked in goroutine",
        ):
            self.assertTrue(_ERROR_RE.findall(line), f"lost recall on: {line}")


class TestAsListCoercion(unittest.TestCase):
    """A bare string must be accepted where a list of filters is expected.

    Rejecting it cost a full round-trip to learn nothing, and it was the most
    repeated tool-call failure in the sampled transcripts. Every parameter whose
    annotation was widened to `list[str] | str | None` must be coerced, or a
    bare string reaches code that iterates it character by character.
    """

    def test_coerces_bare_string(self):
        from evergreen_mcp.mcp_tools import _as_list

        self.assertEqual(_as_list("^(disagg-m8g)"), ["^(disagg-m8g)"])

    def test_passes_through_none_and_lists(self):
        from evergreen_mcp.mcp_tools import _as_list

        self.assertIsNone(_as_list(None))
        self.assertEqual(_as_list(["a", "b"]), ["a", "b"])
        self.assertEqual(_as_list([]), [])

    def test_every_widened_param_is_coerced(self):
        import re
        from pathlib import Path

        import evergreen_mcp.mcp_tools as m

        src = Path(m.__file__).read_text()
        widened = len(re.findall(r"list\[str\] \| str \| None", src))
        # -1 for the _as_list definition itself.
        coerced = len(re.findall(r"_as_list\(", src)) - 1
        self.assertEqual(
            widened,
            coerced,
            "a widened list param is not coerced; a bare string would be "
            "iterated character by character",
        )


class TestDocsReferenceRealTools(unittest.TestCase):
    """Agent-facing docs must only name tools that actually exist.

    server.py's prompts and the skill:// resources are read by the model and
    followed verbatim, so a stale name there becomes a failed tool call and a
    wasted round-trip. `get_task_test_results_evergreen` and
    `get_task_logs_evergreen` were referenced 26 times across these files and
    have never existed.
    """

    @staticmethod
    def _registered_tool_names():
        import re
        from pathlib import Path

        import evergreen_mcp.mcp_tools as m

        src = Path(m.__file__).read_text()
        # Every tool is an `async def` directly beneath an @mcp.tool(...) block.
        return set(re.findall(r"@mcp\.tool\(.*?\n    async def (\w+)\(", src, re.S))

    @staticmethod
    def _doc_files():
        from pathlib import Path

        import evergreen_mcp.server as s

        root = Path(s.__file__).parent
        return [root / "server.py", *sorted(root.glob("skills/*/SKILL.md"))]

    def test_registered_names_are_discoverable(self):
        names = self._registered_tool_names()
        self.assertIn("get_status_summary_evergreen", names)
        self.assertIn("get_task_log_summary", names)
        self.assertGreater(len(names), 20)

    def test_docs_do_not_name_nonexistent_tools(self):
        import re

        real = self._registered_tool_names()
        problems = []
        for path in self._doc_files():
            text = path.read_text()
            for name in set(re.findall(r"\b([a-z][a-z0-9_]{6,})\(", text)):
                looks_like_tool = name.endswith("_evergreen") or name.startswith(
                    ("get_task_log", "get_test_results")
                )
                if looks_like_tool and name not in real:
                    problems.append(f"{path.name}: {name}")
        self.assertEqual(problems, [], f"docs name non-existent tools: {problems}")


class TestGraphQLErrorMessages(unittest.TestCase):
    """A failed query must say what went wrong and whether retrying can help.

    "Failed to execute GraphQL query" discarded the HTTP status entirely; an
    expired OIDC token surfaced as that string with the real 401 buried in a
    traceback, which invites blind retries of a call that cannot succeed.
    """

    def test_401_says_reauthenticate(self):
        from gql.transport.exceptions import TransportServerError

        from evergreen_mcp.evergreen_graphql_client import _describe_transport_error

        msg = _describe_transport_error(TransportServerError("Unauthorized", 401))
        self.assertIn("401", msg)
        self.assertIn("Re-authenticate", msg)
        self.assertIn("will fail the same way", msg)

    def test_403_distinguishes_authz_from_authn(self):
        from gql.transport.exceptions import TransportServerError

        from evergreen_mcp.evergreen_graphql_client import _describe_transport_error

        msg = _describe_transport_error(TransportServerError("Forbidden", 403))
        self.assertIn("not authorized", msg)
        self.assertNotIn("Re-authenticate", msg)

    def test_5xx_marked_transient(self):
        from gql.transport.exceptions import TransportServerError

        from evergreen_mcp.evergreen_graphql_client import _describe_transport_error

        for code in (502, 503, 504):
            msg = _describe_transport_error(TransportServerError("Bad Gateway", code))
            self.assertIn("transient", msg, code)

    def test_unknown_error_still_surfaces_status_and_text(self):
        from gql.transport.exceptions import TransportServerError

        from evergreen_mcp.evergreen_graphql_client import _describe_transport_error

        msg = _describe_transport_error(TransportServerError("kaboom", 500))
        self.assertIn("HTTP 500", msg)
        self.assertIn("kaboom", msg)

    def test_error_without_status_code_does_not_invent_one(self):
        from evergreen_mcp.evergreen_graphql_client import _describe_transport_error

        msg = _describe_transport_error(RuntimeError("connection reset"))
        self.assertNotIn("HTTP", msg)
        self.assertIn("connection reset", msg)
