#!/usr/bin/env python3
"""
Convenience wrapper to run the Evergreen MCP server
"""

import sys
from pathlib import Path

# Add src to Python path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

if __name__ == "__main__":
    from src.run_mcp_server import main
    main()
