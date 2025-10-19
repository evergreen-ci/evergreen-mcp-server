#!/usr/bin/env python3
"""
Tests for CLI functionality of Evergreen MCP server

These tests validate CLI installation and basic functionality.
"""

import subprocess
import sys
import unittest
from pathlib import Path

class TestCLI(unittest.TestCase):
    """Test CLI functionality"""

    def setUp(self):
        """Set up test environment"""
        # Get the source directory path
        self.src_dir = Path(__file__).parent.parent / "src"

    def test_cli_help(self):
        """Test that CLI --help works"""
        test_code = """
from evergreen_mcp.server import main
import sys
sys.argv = ['evergreen-mcp', '--help']
try:
    main()
except SystemExit:
    pass
"""
        result = subprocess.run(
            [sys.executable, "-c", test_code],
            env={"PYTHONPATH": str(self.src_dir)},
            capture_output=True,
            text=True
        )
        # argparse --help writes to stderr  
        output = result.stdout + result.stderr
        self.assertIn("Evergreen MCP Server", output)
        self.assertIn("--project-id", output)
        self.assertIn("Examples:", output)

    def test_cli_version(self):
        """Test that CLI --version works"""
        test_code = """
from evergreen_mcp.server import main
import sys
sys.argv = ['evergreen-mcp', '--version']
try:
    main()
except SystemExit:
    pass
"""
        result = subprocess.run(
            [sys.executable, "-c", test_code],
            env={"PYTHONPATH": str(self.src_dir)},
            capture_output=True,
            text=True
        )
        # argparse --version writes to stderr typically  
        output = result.stdout + result.stderr
        self.assertIn("evergreen-mcp-server 0.1.0", output)

    def test_cli_main_import(self):
        """Test that CLI main function can be imported"""
        result = subprocess.run(
            [sys.executable, "-c", 
             "import sys; sys.path.insert(0, '" + str(self.src_dir) + "'); "
             "from evergreen_mcp.server import main; "
             "print('CLI main function imported successfully')"],
            capture_output=True,
            text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("CLI main function imported successfully", result.stdout)

    def test_project_scripts_entry_point(self):
        """Test that the entry point is correctly defined in pyproject.toml"""
        pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
        with open(pyproject_path, 'r') as f:
            content = f.read()
        
        self.assertIn("[project.scripts]", content)
        self.assertIn('evergreen-mcp = "evergreen_mcp.server:main"', content)


if __name__ == "__main__":
    unittest.main()