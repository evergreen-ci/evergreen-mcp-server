#!/usr/bin/env bash
# Pre-commit hook to validate GraphQL queries
#
# This script can be installed as a git pre-commit hook to validate
# GraphQL queries before each commit. It helps catch query validation
# errors early in the development process.
#
# To install as a pre-commit hook:
#     cp scripts/pre-commit-validate-queries.sh .git/hooks/pre-commit
#     chmod +x .git/hooks/pre-commit
#
# Or use with pre-commit framework by adding to .pre-commit-config.yaml:
#     - repo: local
#       hooks:
#         - id: validate-graphql-queries
#           name: Validate GraphQL Queries
#           entry: ./scripts/pre-commit-validate-queries.sh
#           language: system
#           files: 'src/evergreen_mcp/evergreen_queries\.py$|merged-schema\.graphql$'

set -e

echo "🔍 Validating GraphQL queries..."

# Change to repository root
cd "$(git rev-parse --show-toplevel)"

# Check if schema file exists
if [ ! -f "merged-schema.graphql" ]; then
    echo "⚠️  Schema file 'merged-schema.graphql' not found."
    echo "   Run './scripts/fetch_graphql_schema.sh' to download the schema."
    echo "   Or create a local schema file for validation."
    exit 1
fi

# Check if validation script exists
if [ ! -f "scripts/validate_queries.py" ]; then
    echo "❌ Validation script not found: scripts/validate_queries.py"
    exit 1
fi

# Run validation
if python scripts/validate_queries.py; then
    echo "✅ All GraphQL queries are valid!"
    exit 0
else
    echo "❌ GraphQL query validation failed!"
    echo "   Please fix the invalid queries before committing."
    echo "   Run 'python scripts/validate_queries.py --verbose' for details."
    exit 1
fi