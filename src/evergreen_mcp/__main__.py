#!/usr/bin/env python3
"""
Entry point for running evergreen_mcp as a module.
Allows running with: python -m evergreen_mcp
"""

from .server import main

if __name__ == "__main__":
    main()
