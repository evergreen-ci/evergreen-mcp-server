#!/usr/bin/env python3
"""Validate all GraphQL queries against the schema

This script validates all GraphQL queries in the evergreen_queries module
against the Evergreen GraphQL schema. It can be used standalone or as part
of CI/CD pipelines to catch query errors early.

Usage:
    python scripts/validate_queries.py [--schema PATH] [--verbose]
"""

import argparse
import logging
import sys
from pathlib import Path

# Add src to path so we can import evergreen_mcp modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from evergreen_mcp import evergreen_queries
from evergreen_mcp.schema_validator import validate_queries_in_module


def main():
    parser = argparse.ArgumentParser(description="Validate GraphQL queries against schema")
    parser.add_argument(
        "--schema", 
        help="Path to GraphQL schema file (default: merged-schema.graphql in repo root)"
    )
    parser.add_argument(
        "--verbose", 
        action="store_true",
        help="Enable verbose logging"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format='%(levelname)s: %(message)s')
    
    logger = logging.getLogger(__name__)
    
    # Determine schema path
    schema_path = args.schema
    if not schema_path:
        repo_root = Path(__file__).parent.parent
        schema_path = str(repo_root / "merged-schema.graphql")
    
    logger.info("Validating GraphQL queries against schema: %s", schema_path)
    
    # Validate all queries
    try:
        all_valid = validate_queries_in_module(evergreen_queries, schema_path)
        
        if all_valid:
            logger.info("✅ All GraphQL queries are valid!")
            sys.exit(0)
        else:
            logger.error("❌ Some GraphQL queries failed validation")
            sys.exit(1)
            
    except Exception as e:
        logger.error("Error during validation: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()