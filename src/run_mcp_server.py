#!/usr/bin/env python3
"""
Wrapper script to run the MCP server with proper logging and error handling
"""

import sys
import os
import logging
from pathlib import Path

# Add current directory to Python path (we're already in src/)
sys.path.insert(0, str(Path(__file__).parent))

# Set up logging to both console and file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr),  # Log to stderr so it doesn't interfere with MCP protocol
        logging.FileHandler('../mcp_server.log')  # Log to project root
    ]
)

logger = logging.getLogger(__name__)

def main():
    """Run the MCP server with error handling"""
    try:
        logger.info("=" * 50)
        logger.info("STARTING EVERGREEN MCP SERVER")
        logger.info("=" * 50)
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Working directory: {os.getcwd()}")
        logger.info(f"Python path: {sys.path[:3]}...")  # Show first 3 entries
        
        # Import and run the server
        from server import main as server_main
        logger.info("Server module imported successfully")
        
        logger.info("Starting MCP server...")
        server_main()
        
    except KeyboardInterrupt:
        logger.info("Server stopped by user (Ctrl+C)")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Server failed with error: {e}")
        import traceback
        logger.error("Full traceback:")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
