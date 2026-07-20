"""Tests for configurable endpoint URLs via environment variables.

These tests verify that the OIDC and API key authentication methods
use the correct endpoint URLs based on environment variables, with
proper defaults when those variables are not set.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from evergreen_mcp.oauth_token import get_oauth_token
from evergreen_mcp.server import lifespan


class TestConfigurableEndpointURLs(unittest.IsolatedAsyncioTestCase):
    """Test that endpoint URLs are configurable via environment variables."""

    async def test_oidc_rest_url_override(self):
        """Test that EVERGREEN_OIDC_REST_URL overrides default REST URL for OIDC."""
        mock_config = {
            "user": "test@example.com",
            "auth_method": "oidc",
            "projects_for_directory": {},
        }

        custom_rest_url = "https://custom-evergreen.example.com/rest/v2/"

        with (
            patch(
                "evergreen_mcp.server.load_evergreen_config",
                new_callable=AsyncMock,
                return_value=(mock_config, None),
            ),
            patch("evergreen_mcp.server.EvergreenGraphQLClient") as mock_graphql_client,
            patch("evergreen_mcp.server.EvergreenRestClient") as mock_rest_client,
            patch.dict(
                "os.environ",
                {"EVERGREEN_OIDC_REST_URL": custom_rest_url},
                clear=False,
            ),
        ):
            mock_graphql_instance = AsyncMock()
            mock_graphql_instance.__aenter__ = AsyncMock(
                return_value=mock_graphql_instance
            )
            mock_graphql_instance.__aexit__ = AsyncMock()
            mock_graphql_client.return_value = mock_graphql_instance

            mock_rest_instance = MagicMock()
            mock_rest_instance._close_session = AsyncMock()
            mock_rest_client.return_value = mock_rest_instance

            mock_server = MagicMock()
            async with lifespan(mock_server):
                pass

            mock_rest_client.assert_called_once_with(
                token_getter=get_oauth_token,
                base_url=custom_rest_url,
            )

    async def test_oidc_graphql_url_override(self):
        """Test that EVERGREEN_OIDC_GRAPHQL_URL overrides default GraphQL URL for OIDC."""
        mock_config = {
            "user": "test@example.com",
            "auth_method": "oidc",
            "projects_for_directory": {},
        }

        custom_graphql_url = "https://custom-evergreen.example.com/graphql/query"

        with (
            patch(
                "evergreen_mcp.server.load_evergreen_config",
                new_callable=AsyncMock,
                return_value=(mock_config, None),
            ),
            patch("evergreen_mcp.server.EvergreenGraphQLClient") as mock_graphql_client,
            patch("evergreen_mcp.server.EvergreenRestClient") as mock_rest_client,
            patch.dict(
                "os.environ",
                {"EVERGREEN_OIDC_GRAPHQL_URL": custom_graphql_url},
                clear=False,
            ),
        ):
            mock_graphql_instance = AsyncMock()
            mock_graphql_instance.__aenter__ = AsyncMock(
                return_value=mock_graphql_instance
            )
            mock_graphql_instance.__aexit__ = AsyncMock()
            mock_graphql_client.return_value = mock_graphql_instance

            mock_rest_instance = MagicMock()
            mock_rest_instance._close_session = AsyncMock()
            mock_rest_client.return_value = mock_rest_instance

            mock_server = MagicMock()
            async with lifespan(mock_server):
                pass

            mock_graphql_client.assert_called_once_with(
                token_getter=get_oauth_token,
                endpoint=custom_graphql_url,
            )

    async def test_api_key_rest_url_override(self):
        """Test that EVERGREEN_API_KEY_REST_URL overrides default REST URL for API key."""
        mock_config = {
            "user": "test-user",
            "api_key": "test-api-key",
            "auth_method": "api_key",
            "projects_for_directory": {},
        }

        custom_rest_url = "https://custom-evergreen.example.com/rest/v2/"

        with (
            patch(
                "evergreen_mcp.server.load_evergreen_config",
                new_callable=AsyncMock,
                return_value=(mock_config, None),
            ),
            patch("evergreen_mcp.server.EvergreenGraphQLClient") as mock_graphql_client,
            patch("evergreen_mcp.server.EvergreenRestClient") as mock_rest_client,
            patch.dict(
                "os.environ",
                {"EVERGREEN_API_KEY_REST_URL": custom_rest_url},
                clear=False,
            ),
        ):
            mock_graphql_instance = AsyncMock()
            mock_graphql_instance.__aenter__ = AsyncMock(
                return_value=mock_graphql_instance
            )
            mock_graphql_instance.__aexit__ = AsyncMock()
            mock_graphql_client.return_value = mock_graphql_instance

            mock_rest_instance = MagicMock()
            mock_rest_instance._close_session = AsyncMock()
            mock_rest_client.return_value = mock_rest_instance

            mock_server = MagicMock()
            async with lifespan(mock_server):
                pass

            mock_rest_client.assert_called_once_with(
                user="test-user",
                api_key="test-api-key",
                base_url=custom_rest_url,
            )

    async def test_api_key_graphql_url_override(self):
        """Test that EVERGREEN_API_KEY_GRAPHQL_URL overrides default GraphQL URL for API key."""
        mock_config = {
            "user": "test-user",
            "api_key": "test-api-key",
            "auth_method": "api_key",
            "projects_for_directory": {},
        }

        custom_graphql_url = "https://custom-evergreen.example.com/graphql/query"

        with (
            patch(
                "evergreen_mcp.server.load_evergreen_config",
                new_callable=AsyncMock,
                return_value=(mock_config, None),
            ),
            patch("evergreen_mcp.server.EvergreenGraphQLClient") as mock_graphql_client,
            patch("evergreen_mcp.server.EvergreenRestClient") as mock_rest_client,
            patch.dict(
                "os.environ",
                {"EVERGREEN_API_KEY_GRAPHQL_URL": custom_graphql_url},
                clear=False,
            ),
        ):
            mock_graphql_instance = AsyncMock()
            mock_graphql_instance.__aenter__ = AsyncMock(
                return_value=mock_graphql_instance
            )
            mock_graphql_instance.__aexit__ = AsyncMock()
            mock_graphql_client.return_value = mock_graphql_instance

            mock_rest_instance = MagicMock()
            mock_rest_instance._close_session = AsyncMock()
            mock_rest_client.return_value = mock_rest_instance

            mock_server = MagicMock()
            async with lifespan(mock_server):
                pass

            mock_graphql_client.assert_called_once_with(
                user="test-user",
                api_key="test-api-key",
                endpoint=custom_graphql_url,
            )

    async def test_default_urls_when_no_env_vars_oidc(self):
        """Test that OIDC uses corp defaults when no env vars are set."""
        mock_config = {
            "user": "test@example.com",
            "auth_method": "oidc",
            "projects_for_directory": {},
        }

        with (
            patch(
                "evergreen_mcp.server.load_evergreen_config",
                new_callable=AsyncMock,
                return_value=(mock_config, None),
            ),
            patch("evergreen_mcp.server.EvergreenGraphQLClient") as mock_graphql_client,
            patch("evergreen_mcp.server.EvergreenRestClient") as mock_rest_client,
            patch.dict(
                "os.environ",
                {},
                clear=True,
            ),
        ):
            mock_graphql_instance = AsyncMock()
            mock_graphql_instance.__aenter__ = AsyncMock(
                return_value=mock_graphql_instance
            )
            mock_graphql_instance.__aexit__ = AsyncMock()
            mock_graphql_client.return_value = mock_graphql_instance

            mock_rest_instance = MagicMock()
            mock_rest_instance._close_session = AsyncMock()
            mock_rest_client.return_value = mock_rest_instance

            mock_server = MagicMock()
            async with lifespan(mock_server):
                pass

            mock_graphql_client.assert_called_once_with(
                token_getter=get_oauth_token,
                endpoint="https://evergreen.corp.mongodb.com/graphql/query",
            )
            mock_rest_client.assert_called_once_with(
                token_getter=get_oauth_token,
                base_url="https://evergreen.corp.mongodb.com/rest/v2/",
            )

    async def test_default_urls_when_no_env_vars_api_key(self):
        """Test that API key uses non-corp defaults when no env vars are set."""
        mock_config = {
            "user": "test-user",
            "api_key": "test-api-key",
            "auth_method": "api_key",
            "projects_for_directory": {},
        }

        with (
            patch(
                "evergreen_mcp.server.load_evergreen_config",
                new_callable=AsyncMock,
                return_value=(mock_config, None),
            ),
            patch("evergreen_mcp.server.EvergreenGraphQLClient") as mock_graphql_client,
            patch("evergreen_mcp.server.EvergreenRestClient") as mock_rest_client,
            patch.dict(
                "os.environ",
                {},
                clear=True,
            ),
        ):
            mock_graphql_instance = AsyncMock()
            mock_graphql_instance.__aenter__ = AsyncMock(
                return_value=mock_graphql_instance
            )
            mock_graphql_instance.__aexit__ = AsyncMock()
            mock_graphql_client.return_value = mock_graphql_instance

            mock_rest_instance = MagicMock()
            mock_rest_instance._close_session = AsyncMock()
            mock_rest_client.return_value = mock_rest_instance

            mock_server = MagicMock()
            async with lifespan(mock_server):
                pass

            mock_graphql_client.assert_called_once_with(
                user="test-user",
                api_key="test-api-key",
                endpoint="https://evergreen.mongodb.com/graphql/query",
            )
            mock_rest_client.assert_called_once_with(
                user="test-user",
                api_key="test-api-key",
                base_url="https://evergreen.mongodb.com/rest/v2/",
            )


if __name__ == "__main__":
    unittest.main()
