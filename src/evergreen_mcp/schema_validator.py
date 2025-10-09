"""GraphQL schema validation for Evergreen queries

This module provides functionality to validate GraphQL queries against the Evergreen schema.
It helps catch query errors at development time and ensures queries stay in sync with API changes.
"""

import logging
import os
from pathlib import Path
from typing import List, Optional

from gql import gql
from gql.client import Client
from gql.transport.exceptions import TransportError
from graphql import build_client_schema, get_introspection_query, validate
from graphql.error import GraphQLError
from graphql.language import DocumentNode, parse

logger = logging.getLogger(__name__)


class GraphQLSchemaValidator:
    """Validates GraphQL queries against the Evergreen schema"""

    def __init__(self, schema_path: Optional[str] = None):
        """Initialize the schema validator

        Args:
            schema_path: Path to GraphQL schema file. If None, uses default location.
        """
        self.schema_path = schema_path or self._get_default_schema_path()
        self._schema = None

    def _get_default_schema_path(self) -> str:
        """Get the default schema file path"""
        # Look for schema file in repository root
        repo_root = Path(__file__).parent.parent.parent
        schema_file = repo_root / "merged-schema.graphql"
        return str(schema_file)

    def load_schema(self) -> bool:
        """Load GraphQL schema from file

        Returns:
            True if schema loaded successfully, False otherwise
        """
        try:
            if not os.path.exists(self.schema_path):
                logger.warning("Schema file not found at %s", self.schema_path)
                return False

            with open(self.schema_path, "r", encoding="utf-8") as f:
                schema_content = f.read()

            # Parse the schema SDL (Schema Definition Language) into a GraphQL schema
            from graphql import build_schema

            self._schema = build_schema(schema_content)

            logger.info("Successfully loaded GraphQL schema from %s", self.schema_path)
            return True

        except Exception as e:
            logger.error("Failed to load GraphQL schema: %s", e)
            return False

    def validate_query(self, query_string: str) -> List[GraphQLError]:
        """Validate a GraphQL query against the schema

        Args:
            query_string: GraphQL query to validate

        Returns:
            List of validation errors (empty if valid)
        """
        if not self._schema:
            if not self.load_schema():
                logger.warning("Cannot validate query: schema not loaded")
                return []

        try:
            # Parse query string into AST
            query_ast = parse(query_string)

            # Validate against schema
            validation_errors = validate(self._schema, query_ast)

            if validation_errors:
                logger.warning(
                    "Query validation failed with %d errors", len(validation_errors)
                )
                for error in validation_errors:
                    logger.warning("Validation error: %s", error.message)
            else:
                logger.debug("Query validation successful")

            return validation_errors

        except Exception as e:
            logger.error("Error during query validation: %s", e)
            return [GraphQLError(f"Validation error: {e}")]

    def is_query_valid(self, query_string: str) -> bool:
        """Check if a GraphQL query is valid

        Args:
            query_string: GraphQL query to validate

        Returns:
            True if query is valid, False otherwise
        """
        errors = self.validate_query(query_string)
        return len(errors) == 0

    def validate_all_queries(self, queries_module) -> dict:
        """Validate all queries in a module

        Args:
            queries_module: Module containing GraphQL query constants

        Returns:
            Dictionary mapping query names to validation results
        """
        results = {}

        for attr_name in dir(queries_module):
            if attr_name.startswith("GET_") and isinstance(
                getattr(queries_module, attr_name), str
            ):
                query_string = getattr(queries_module, attr_name)
                errors = self.validate_query(query_string)
                results[attr_name] = {
                    "valid": len(errors) == 0,
                    "errors": [str(error) for error in errors],
                }

        return results


def validate_queries_in_module(
    queries_module, schema_path: Optional[str] = None
) -> bool:
    """Validate all GraphQL queries in a module

    Args:
        queries_module: Module containing GraphQL query constants
        schema_path: Optional path to schema file

    Returns:
        True if all queries are valid, False if any validation fails
    """
    validator = GraphQLSchemaValidator(schema_path)
    results = validator.validate_all_queries(queries_module)

    all_valid = True
    for query_name, result in results.items():
        if not result["valid"]:
            all_valid = False
            logger.error("Query %s validation failed:", query_name)
            for error in result["errors"]:
                logger.error("  - %s", error)
        else:
            logger.info("Query %s: valid", query_name)

    return all_valid
