#!/usr/bin/env python3
"""Validate every GraphQL document in evergreen_queries.py against a schema.

Evergreen removes schema fields with no deprecation window (e.g. DEVPROD-27824
dropped `Waterfall.flattenedVersions`, breaking every waterfall-backed tool at
runtime with a GRAPHQL_VALIDATION_FAILED). Unit tests can't catch that — they
mock the transport — so run this after pulling the evergreen repo, or in CI.

Schema source, in priority order:
  1. --schema PATH               (a merged SDL file)
  2. ./merged-schema.graphql     (from scripts/fetch_graphql_schema.sh)
  3. --evergreen-dir / $EVERGREEN_REPO / ~/evergreen
                                 (concatenates graphql/schema/**/*.graphql)

Note the deployed evergreen.corp schema can lead or lag a local checkout; the
merged-schema.graphql artifact tracks the deployed side more closely.

Exit status: 0 if every document validates, 1 otherwise.
"""

import argparse
import os
import sys
from pathlib import Path

from graphql import build_schema, parse, validate

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def load_sdl(args: argparse.Namespace) -> tuple[str, str]:
    """Return (sdl_text, human_readable_source)."""
    if args.schema:
        path = Path(args.schema).expanduser()
        return path.read_text(), str(path)

    merged = REPO_ROOT / "merged-schema.graphql"
    if merged.exists():
        return merged.read_text(), str(merged)

    evg = Path(
        args.evergreen_dir or os.getenv("EVERGREEN_REPO") or "~/evergreen"
    ).expanduser()
    schema_dir = evg / "graphql" / "schema"
    if not schema_dir.is_dir():
        sys.exit(
            f"No schema found. Looked for --schema, {merged}, and {schema_dir}.\n"
            "Run scripts/fetch_graphql_schema.sh or pass --evergreen-dir."
        )
    parts = [p.read_text() for p in sorted(schema_dir.rglob("*.graphql"))]
    return "\n".join(parts), f"{schema_dir} ({len(parts)} files)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", help="Path to a merged SDL file.")
    parser.add_argument("--evergreen-dir", help="Path to an evergreen checkout.")
    args = parser.parse_args()

    sdl, source = load_sdl(args)
    # assume_valid_sdl: the server's SDL uses custom directives (e.g.
    # @requireProjectAccess) whose definitions we don't need to validate here;
    # we only care that our selection sets match the types.
    schema = build_schema(sdl, assume_valid_sdl=True)

    from evergreen_mcp import evergreen_queries

    documents = {
        name: value
        for name, value in vars(evergreen_queries).items()
        if name.isupper() and isinstance(value, str) and "{" in value
    }
    if not documents:
        sys.exit("No query constants found in evergreen_queries.py.")

    print(f"Schema: {source}")
    print(f"Validating {len(documents)} documents from evergreen_queries.py\n")

    failures = 0
    for name in sorted(documents):
        errors = validate(schema, parse(documents[name]))
        if errors:
            failures += 1
            print(f"FAIL  {name}")
            for err in errors:
                loc = err.locations[0] if err.locations else None
                where = f" (line {loc.line}, col {loc.column})" if loc else ""
                print(f"        {err.message}{where}")
        else:
            print(f"ok    {name}")

    print()
    if failures:
        print(f"{failures}/{len(documents)} documents FAILED validation.")
        return 1
    print(f"All {len(documents)} documents valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
