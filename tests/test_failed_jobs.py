#!/usr/bin/env python3
"""
Integration tests for patch-based failed jobs functionality
Tests the new patch-based tools with real Evergreen API calls
"""

import asyncio
import yaml
import logging
import sys
import os
from pathlib import Path
from typing import Dict, Any

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from evergreen_graphql_client import EvergreenGraphQLClient
from failed_jobs_tools import fetch_user_recent_patches, fetch_patch_failed_jobs, fetch_task_logs

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TestPatchFailedJobsIntegration:
    """Integration tests for patch-based failed jobs functionality"""

    def __init__(self):
        self.client = None
        self.user_id = None

    async def setup(self):
        """Set up test environment"""
        # Load config same way as MCP server
        config_path = Path.home() / ".evergreen.yml"

        if not config_path.exists():
            logger.error(f"Configuration file not found: {config_path}")
            logger.error("Please create ~/.evergreen.yml with your Evergreen credentials:")
            logger.error("user: your-evergreen-username")
            logger.error("api_key: your-evergreen-api-key")
            return False

        try:
            with open(config_path, 'r') as f:
                evergreen_config = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return False

        if not evergreen_config.get("user") or not evergreen_config.get("api_key"):
            logger.error("Configuration file missing 'user' or 'api_key' fields")
            return False

        # Initialize client
        self.client = EvergreenGraphQLClient(
            user=evergreen_config["user"],
            api_key=evergreen_config["api_key"]
        )

        # Store user ID for patch queries
        self.user_id = evergreen_config["user"]

        await self.client.connect()
        logger.info("Test setup completed successfully")
        return True

    async def cleanup(self):
        """Clean up test environment"""
        if self.client:
            await self.client.close()

    async def test_user_recent_patches(self):
        """Test user recent patches functionality"""
        logger.info("Testing user recent patches...")

        try:
            result = await fetch_user_recent_patches(self.client, self.user_id, 5)

            # Validate response structure
            assert 'user_id' in result, "Missing user_id in response"
            assert 'patches' in result, "Missing patches in response"
            assert 'total_patches' in result, "Missing total_patches in response"

            patches = result['patches']
            logger.info(f"✅ Retrieved {len(patches)} recent patches for user {self.user_id}")

            # Display patch information
            for i, patch in enumerate(patches[:3]):  # Show first 3
                logger.info(f"   {i+1}. {patch['githash'][:8]} - {patch['description'][:50]}...")
                logger.info(f"      Status: {patch['status']}, Project: {patch['project_identifier']}")

            return patches

        except Exception as e:
            logger.error(f"❌ User patches test failed: {e}")
            raise
            
    async def test_patch_failed_jobs(self, patches):
        """Test patch failed jobs functionality"""
        logger.info("Testing patch failed jobs...")

        if not patches:
            logger.warning("⚠️  No patches available, skipping patch failed jobs test")
            return

        # Find a patch with failed status or use the first one
        test_patch = None
        for patch in patches:
            if patch['status'] == 'failed' or patch['version_status'] == 'failed':
                test_patch = patch
                break

        if not test_patch:
            test_patch = patches[0]  # Use first patch if none failed

        patch_id = test_patch['patch_id']
        logger.info(f"Testing with patch {patch_id} ({test_patch['githash'][:8]})")

        try:
            result = await fetch_patch_failed_jobs(self.client, patch_id, 10)

            # Validate response structure
            assert 'patch_info' in result, "Missing patch_info in response"
            assert 'failed_tasks' in result, "Missing failed_tasks in response"
            assert 'summary' in result, "Missing summary in response"

            patch_info = result['patch_info']
            assert patch_info['patch_id'] == patch_id, "Patch ID mismatch"

            failed_tasks = result['failed_tasks']
            summary = result['summary']

            logger.info(f"✅ Patch failed jobs returned {len(failed_tasks)} failed tasks")
            logger.info(f"   Build variants: {summary['failed_build_variants']}")
            logger.info(f"   Has timeouts: {summary['has_timeouts']}")

            # Test task logs tool if there are failed tasks
            if failed_tasks:
                task_id = failed_tasks[0]['task_id']
                execution = failed_tasks[0]['execution']

                log_arguments = {
                    'task_id': task_id,
                    'execution': execution,
                    'max_lines': 50,
                    'filter_errors': True
                }

                log_result = await fetch_task_logs(self.client, log_arguments)

                # Validate log response structure
                assert 'task_id' in log_result, "Missing task_id in log response"
                assert 'logs' in log_result, "Missing logs in log response"
                assert log_result['task_id'] == task_id, "Task ID mismatch"

                logger.info(f"✅ Task logs tool returned {log_result['total_lines']} log entries")

        except Exception as e:
            logger.error(f"❌ Patch failed jobs test failed: {e}")
            raise
            
    async def test_error_handling(self):
        """Test error handling scenarios"""
        logger.info("Testing error handling...")

        # Test with invalid patch ID
        try:
            result = await fetch_patch_failed_jobs(self.client, 'invalid-patch-id', 10)
            # Should return error response, not raise exception
            assert 'error' in result, "Expected error response for invalid patch ID"
            logger.info("✅ Invalid patch ID handled correctly")
        except Exception as e:
            logger.error(f"❌ Error handling test failed: {e}")
            raise

        # Test with missing task ID
        try:
            await fetch_task_logs(self.client, {})
            assert False, "Expected ValueError for missing task_id"
        except ValueError as e:
            assert "task_id parameter is required" in str(e)
            logger.info("✅ Missing task ID handled correctly")

    async def run_all_tests(self):
        """Run all integration tests"""
        logger.info("=" * 60)
        logger.info("PATCH-BASED FAILED JOBS INTEGRATION TESTS")
        logger.info("=" * 60)

        if not await self.setup():
            return False

        try:
            patches = await self.test_user_recent_patches()
            await self.test_patch_failed_jobs(patches)
            await self.test_error_handling()

            logger.info("=" * 60)
            logger.info("✅ ALL TESTS PASSED!")
            logger.info("Patch-based failed jobs functionality is ready for use")
            logger.info("=" * 60)
            return True

        except Exception as e:
            logger.error("=" * 60)
            logger.error("❌ TESTS FAILED!")
            logger.error(f"Error: {e}")
            logger.error("=" * 60)
            return False

        finally:
            await self.cleanup()


async def main():
    """Main test runner"""
    test_runner = TestPatchFailedJobsIntegration()
    success = await test_runner.run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
