from asyncio import run
import os.path
import sys

import yaml

from evergreen import Configuration, ApiClient, ProjectsApi


async def _main() -> int:
    evergreen_config = yaml.safe_load(open(os.path.expanduser("~/.evergreen.yml"), mode="rb"))

    configuration = Configuration()
    configuration.api_key['Api-User'] = evergreen_config["user"]
    configuration.api_key['Api-Key'] = evergreen_config["api_key"]


    # Enter a context with an instance of the API client
    async with ApiClient(configuration) as api_client:
        # Create an instance of the API class
        api_instance = ProjectsApi(api_client)

        projects = await api_instance.projects_get()
        for project in projects:
            print(project.id)
            print(project.identifier)

    return 0



def main() -> None:
    sys.exit(run(_main()))
