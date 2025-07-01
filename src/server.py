"""MCP server for Evergreen"""

from asyncio import run
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Sequence
import os.path
import sys

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
import yaml

from evergreen import Configuration, ApiClient, ProjectsApi


@asynccontextmanager
async def _server_lifespan(_) -> AsyncIterator[dict]:
    with open(os.path.expanduser("~/.evergreen.yml"), mode="rb") as f:
        evergreen_config = yaml.safe_load(f)

    configuration = Configuration()
    configuration.api_key['Api-User'] = evergreen_config["user"]
    configuration.api_key['Api-Key'] = evergreen_config["api_key"]

    async with ApiClient(configuration) as evergreen_api:
        yield {"evergreen_api": evergreen_api}

server: Server = Server("evergreen-mcp-server", lifespan=_server_lifespan)

@server.list_resources()
async def _handle_project_resources() -> Sequence[types.Resource]:
    api_client = server.request_context.lifespan_context["evergreen_api"]

    api_instance = ProjectsApi(api_client)

    projects = await api_instance.projects_get()
    
    return list(map(lambda project: types.Resource(
        uri=f"evergreen://project/{project.id}",
        name=project.display_name,
        mimeType="application/json",
    ), projects))

@server.list_resource_templates()
def _handle_resource_templates() -> Sequence[types.ResourceTemplate]:
    return [
        types.ResourceTemplate(
            uriTemplate="evergreen://project/{project_id}",
            name="Project",
            mimeType="application/json",
            description="Evergreen project",
        ),
    ]

async def _main() -> int:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="evergreen-mcp-server",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )
    return 0



def main() -> None:
    """Main entry point for the MCP server"""
    sys.exit(run(_main()))
