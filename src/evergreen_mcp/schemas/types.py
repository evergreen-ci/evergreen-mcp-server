"""Type definitions for Evergreen API responses

This module contains TypedDict definitions that describe the structure
of data returned from the Evergreen GraphQL API.
"""

from typing import TypedDict


class ProjectDict(TypedDict, total=False):
    """Type definition for project dictionary structure
    
    Represents a project in the Evergreen CI/CD system.
    
    Attributes:
        id: Internal database ID for the project
        identifier: Project identifier used in API calls (e.g., 'mongodb-mongo-master')
        displayName: Human-readable project name
        enabled: Whether the project is currently enabled
        owner: GitHub organization or user that owns the repository
        repo: Repository name
        branch: Default branch for the project
    """
    id: str
    identifier: str
    displayName: str
    enabled: bool
    owner: str
    repo: str
    branch: str

