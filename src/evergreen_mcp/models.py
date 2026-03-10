from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class LogLinks(BaseModel):
    """Model for log links"""

    all_log: Optional[str] = Field(
        default=None, description="URL to the complete task log"
    )
    task_log: Optional[str] = Field(default=None, description="URL to the task log")
    event_log: Optional[str] = Field(default=None, description="URL to the event log")
    system_log: Optional[str] = Field(default=None, description="URL to the system log")
    agent_log: Optional[str] = Field(default=None, description="URL to the agent log")


class EndDetails(BaseModel):
    """Model for task end details"""

    status: str = Field(description="Status of the task after completion")
    type: Optional[str] = Field(default=None, description="Type of task completion")
    description: Optional[str] = Field(
        default=None, description="Description of task completion"
    )
    timed_out: Optional[bool] = Field(
        default=None, description="Whether the task timed out"
    )
    oom_killed: Optional[bool] = Field(
        default=None, description="Whether the task was killed due to OOM"
    )


class Artifact(BaseModel):
    """Model for task artifacts"""

    name: str = Field(description="Name of the artifact")
    url: str = Field(description="URL to download the artifact")
    url_parsley: Optional[str] = Field(
        default=None, description="Parsley URL for the artifact"
    )
    visibility: str = Field(description="Visibility of the artifact (e.g., 'signed')")
    ignore_for_fetch: bool = Field(
        description="Whether to ignore this artifact for fetching"
    )
    content_type: str = Field(description="MIME type of the artifact")


class TaskExecution(BaseModel):
    """Model for a task execution"""

    execution: int = Field(description="Execution number")
    status: str = Field(description="Status of this execution")
    start_time: Optional[str] = Field(
        default=None, description="Start time of execution"
    )
    finish_time: Optional[str] = Field(
        default=None, description="Finish time of execution"
    )


class TaskResponse(BaseModel):
    """Model for task details from the Evergreen API"""

    task_id: str = Field(description="Unique identifier of the task")
    execution: Optional[int] = Field(
        default=0, description="Execution number of this task"
    )
    display_name: str = Field(description="Display name of the task")
    status: str = Field(description="Current status of the task")
    status_details: Optional[EndDetails] = Field(
        default=None, description="Details about the task status"
    )
    logs: Optional[LogLinks] = Field(
        default=None, description="Links to the various logs for this task"
    )
    activated: bool = Field(description="Whether the task is activated")
    activated_by: Optional[str] = Field(
        default=None, description="User who activated the task"
    )
    build_id: str = Field(description="ID of the build this task belongs to")
    build_variant: str = Field(description="Build variant this task runs on")
    version_id: str = Field(description="Version ID this task is a part of")
    project_id: Optional[str] = Field(
        default=None, description="Project ID this task belongs to"
    )
    project: Optional[str] = Field(
        default=None, description="Project this task belongs to"
    )
    revision: Optional[str] = Field(
        default=None, description="Git revision this task is testing"
    )
    priority: Optional[int] = Field(default=None, description="Priority of this task")
    create_time: Optional[str] = Field(
        default=None, description="Time when this task was created"
    )
    start_time: Optional[str] = Field(
        default=None, description="Time when this task started"
    )
    finish_time: Optional[str] = Field(
        default=None, description="Time when this task finished"
    )
    depends_on: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Tasks this task depends on"
    )
    time_taken_ms: Optional[int] = Field(
        default=None, description="Time taken to complete in milliseconds"
    )
    expected_duration_ms: Optional[int] = Field(
        default=None, description="Expected duration in milliseconds"
    )
    previous_executions: Optional[List[TaskExecution]] = Field(
        default=None, description="Previous executions of this task"
    )
    artifacts: Optional[List[Artifact]] = Field(
        default=None, description="List of artifacts associated with this task"
    )
    host_id: Optional[str] = Field(
        default=None, description="ID of the host running this task"
    )
    distro_id: Optional[str] = Field(
        default=None, description="Distribution/OS identifier for this task"
    )
