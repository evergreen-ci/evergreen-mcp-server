"""Tests for GraphQL schema validation functionality"""

from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from evergreen_mcp import evergreen_queries
from evergreen_mcp.schema_validator import (
    GraphQLSchemaValidator,
    validate_queries_in_module,
)


class TestGraphQLSchemaValidator:
    """Test cases for GraphQLSchemaValidator class"""

    def test_default_schema_path(self):
        """Test that default schema path is correctly determined"""
        validator = GraphQLSchemaValidator()
        expected_path = Path(__file__).parent.parent / "merged-schema.graphql"
        assert validator.schema_path == str(expected_path)

    def test_custom_schema_path(self):
        """Test that custom schema path is used when provided"""
        custom_path = "/custom/schema.graphql"
        validator = GraphQLSchemaValidator(custom_path)
        assert validator.schema_path == custom_path

    def test_load_schema_success(self):
        """Test successful schema loading"""
        # Use the actual schema file for testing
        schema_path = Path(__file__).parent.parent / "merged-schema.graphql"
        validator = GraphQLSchemaValidator(str(schema_path))

        result = validator.load_schema()
        assert result is True
        assert validator._schema is not None

    def test_load_schema_file_not_found(self):
        """Test schema loading when file doesn't exist"""
        validator = GraphQLSchemaValidator("/nonexistent/schema.graphql")

        result = validator.load_schema()
        assert result is False
        assert validator._schema is None

    def test_validate_valid_query(self):
        """Test validation of a valid GraphQL query"""
        schema_path = Path(__file__).parent.parent / "merged-schema.graphql"
        validator = GraphQLSchemaValidator(str(schema_path))

        valid_query = """
        query GetProjects {
          projects {
            groupDisplayName
            projects {
              id
              displayName
              identifier
            }
          }
        }
        """

        errors = validator.validate_query(valid_query)
        assert len(errors) == 0

    def test_validate_invalid_query(self):
        """Test validation of an invalid GraphQL query"""
        schema_path = Path(__file__).parent.parent / "merged-schema.graphql"
        validator = GraphQLSchemaValidator(str(schema_path))

        invalid_query = """
        query GetProjects {
          projects {
            nonexistentField
          }
        }
        """

        errors = validator.validate_query(invalid_query)
        assert len(errors) > 0

    def test_is_query_valid(self):
        """Test is_query_valid method"""
        schema_path = Path(__file__).parent.parent / "merged-schema.graphql"
        validator = GraphQLSchemaValidator(str(schema_path))

        valid_query = """
        query GetProjects {
          projects {
            groupDisplayName
          }
        }
        """

        invalid_query = """
        query InvalidQuery {
          nonexistentField
        }
        """

        assert validator.is_query_valid(valid_query) is True
        assert validator.is_query_valid(invalid_query) is False

    def test_validate_all_queries_in_module(self):
        """Test validation of all queries in the evergreen_queries module"""
        schema_path = Path(__file__).parent.parent / "merged-schema.graphql"
        validator = GraphQLSchemaValidator(str(schema_path))

        results = validator.validate_all_queries(evergreen_queries)

        # Should find all GET_* query constants
        assert len(results) > 0

        # Check that results have expected structure
        for query_name, result in results.items():
            assert "valid" in result
            assert "errors" in result
            assert isinstance(result["valid"], bool)
            assert isinstance(result["errors"], list)

    def test_validate_without_schema_loaded(self):
        """Test validation when schema is not loaded"""
        validator = GraphQLSchemaValidator("/nonexistent/schema.graphql")

        query = "query { projects { id } }"
        errors = validator.validate_query(query)

        # Should return empty list when schema can't be loaded
        assert len(errors) == 0


class TestModuleLevelFunctions:
    """Test module-level functions"""

    def test_validate_queries_in_module_success(self):
        """Test validate_queries_in_module with valid queries"""
        schema_path = Path(__file__).parent.parent / "merged-schema.graphql"

        result = validate_queries_in_module(evergreen_queries, str(schema_path))
        assert result is True

    @patch("evergreen_mcp.schema_validator.GraphQLSchemaValidator")
    def test_validate_queries_in_module_failure(self, mock_validator_class):
        """Test validate_queries_in_module with invalid queries"""
        # Mock validator to return invalid results
        mock_validator = mock_validator_class.return_value
        mock_validator.validate_all_queries.return_value = {
            "GET_INVALID": {"valid": False, "errors": ["Test error"]}
        }

        result = validate_queries_in_module(evergreen_queries, "/test/schema.graphql")
        assert result is False


class TestIntegration:
    """Integration tests"""

    def test_all_project_queries_are_valid(self):
        """Test that all queries in evergreen_queries are valid against schema"""
        schema_path = Path(__file__).parent.parent / "merged-schema.graphql"

        # This should pass since we fixed the queries
        result = validate_queries_in_module(evergreen_queries, str(schema_path))
        assert result is True, "All GraphQL queries should be valid against the schema"

    def test_specific_query_validation(self):
        """Test validation of specific important queries"""
        schema_path = Path(__file__).parent.parent / "merged-schema.graphql"
        validator = GraphQLSchemaValidator(str(schema_path))

        # Test the main queries used by the MCP server
        important_queries = [
            evergreen_queries.GET_PROJECTS,
            evergreen_queries.GET_USER_RECENT_PATCHES,
            evergreen_queries.GET_PATCH_FAILED_TASKS,
            evergreen_queries.GET_TASK_LOGS,
        ]

        for query in important_queries:
            errors = validator.validate_query(query)
            assert len(errors) == 0, f"Query should be valid: {query[:50]}..."
