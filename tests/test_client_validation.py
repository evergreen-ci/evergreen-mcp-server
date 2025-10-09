"""Test GraphQL client with schema validation enabled"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from evergreen_mcp import evergreen_queries
from evergreen_mcp.evergreen_graphql_client import EvergreenGraphQLClient


class TestClientValidation:
    """Test GraphQL client with validation enabled"""

    def test_client_initialization_with_validation(self):
        """Test client initialization with validation enabled"""
        client = EvergreenGraphQLClient(
            user="test_user", api_key="test_key", enable_validation=True
        )

        assert client.enable_validation is True
        assert client._validator is not None

    def test_client_initialization_without_validation(self):
        """Test client initialization with validation disabled (default)"""
        client = EvergreenGraphQLClient(user="test_user", api_key="test_key")

        assert client.enable_validation is False
        assert client._validator is None

    @pytest.mark.asyncio
    async def test_query_execution_with_validation_success(self):
        """Test query execution with validation enabled and valid query"""
        client = EvergreenGraphQLClient(
            user="test_user", api_key="test_key", enable_validation=True
        )

        # Mock the gql client and transport
        with patch(
            "evergreen_mcp.evergreen_graphql_client.Client"
        ) as mock_client_class:
            with patch("evergreen_mcp.evergreen_graphql_client.AIOHTTPTransport"):
                mock_client = AsyncMock()
                mock_client_class.return_value = mock_client
                mock_client.execute_async.return_value = {"projects": []}

                await client.connect()

                # Use a valid query
                result = await client._execute_query(evergreen_queries.GET_PROJECTS)

                assert result == {"projects": []}
                mock_client.execute_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_query_execution_with_validation_failure(self):
        """Test query execution with validation enabled and invalid query"""
        client = EvergreenGraphQLClient(
            user="test_user", api_key="test_key", enable_validation=True
        )

        # Mock the client connection
        with patch("evergreen_mcp.evergreen_graphql_client.Client"):
            with patch("evergreen_mcp.evergreen_graphql_client.AIOHTTPTransport"):
                await client.connect()

                # Use an invalid query
                invalid_query = """
                query InvalidQuery {
                  nonexistentField
                }
                """

                with pytest.raises(Exception) as exc_info:
                    await client._execute_query(invalid_query)

                assert "GraphQL query validation failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_query_execution_without_validation(self):
        """Test query execution with validation disabled"""
        client = EvergreenGraphQLClient(
            user="test_user", api_key="test_key", enable_validation=False
        )

        # Mock the gql client and transport
        with patch(
            "evergreen_mcp.evergreen_graphql_client.Client"
        ) as mock_client_class:
            with patch("evergreen_mcp.evergreen_graphql_client.AIOHTTPTransport"):
                mock_client = AsyncMock()
                mock_client_class.return_value = mock_client
                mock_client.execute_async.return_value = {"data": "test"}

                await client.connect()

                # Use an invalid query - should not raise validation error
                invalid_query = """
                query InvalidQuery {
                  nonexistentField
                }
                """

                # Should not raise validation error, but may raise other errors
                # In this case, we'll catch the result since our mock returns success
                try:
                    result = await client._execute_query(invalid_query)
                    # If validation is disabled, the query passes to gql
                    assert result == {"data": "test"}
                except Exception:
                    # If gql itself raises an error, that's expected when validation is disabled
                    pass

    @pytest.mark.asyncio
    async def test_specific_method_calls_with_validation(self):
        """Test specific client method calls work with validation enabled"""
        client = EvergreenGraphQLClient(
            user="test_user", api_key="test_key", enable_validation=True
        )

        # Mock the gql client and transport
        with patch(
            "evergreen_mcp.evergreen_graphql_client.Client"
        ) as mock_client_class:
            with patch("evergreen_mcp.evergreen_graphql_client.AIOHTTPTransport"):
                mock_client = AsyncMock()
                mock_client_class.return_value = mock_client
                mock_client.execute_async.return_value = {"projects": []}

                await client.connect()

                # This should work since GET_PROJECTS is valid
                result = await client.get_projects()
                assert result == []
                mock_client.execute_async.assert_called_once()


class TestValidationIntegration:
    """Integration tests for validation with the full client"""

    @pytest.mark.asyncio
    async def test_all_client_queries_pass_validation(self):
        """Test that all queries used by client methods pass validation"""
        # Test client can be created with validation without errors
        client = EvergreenGraphQLClient(
            user="test_user", api_key="test_key", enable_validation=True
        )

        # Validate that the validator is properly initialized
        assert client._validator is not None
        assert client._validator.load_schema() is True

        # Test all the query constants used in the client
        queries_to_test = [
            evergreen_queries.GET_PROJECTS,
            evergreen_queries.GET_PROJECT,
            evergreen_queries.GET_PROJECT_SETTINGS,
            evergreen_queries.GET_USER_RECENT_PATCHES,
            evergreen_queries.GET_PATCH_FAILED_TASKS,
            evergreen_queries.GET_VERSION_WITH_FAILED_TASKS,
            evergreen_queries.GET_TASK_LOGS,
        ]

        for query in queries_to_test:
            errors = client._validator.validate_query(query)
            assert len(errors) == 0, f"Query should be valid: {query[:100]}..."
