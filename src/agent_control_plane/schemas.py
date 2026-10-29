from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from agent_control_plane.models import EventType, TaskPriority, TaskStatus

ShortText = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=20_000)]


class TaskCreate(BaseModel):
    title: ShortText
    description: LongText
    repository: Annotated[str, Field(min_length=1, max_length=500)]
    base_branch: Annotated[str, Field(min_length=1, max_length=200)] = "main"
    acceptance_criteria: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        default_factory=list, max_length=50
    )
    labels: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        default_factory=list, max_length=20
    )
    priority: TaskPriority = TaskPriority.NORMAL
    parent_task_id: str | None = None


class TaskSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    repository: str
    base_branch: str
    priority: TaskPriority
    status: TaskStatus
    claimed_by: str | None
    lease_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: EventType
    actor_type: str
    actor_id: str
    payload: dict[str, Any]
    created_at: datetime


class TaskDetail(TaskSummary):
    description: str
    acceptance_criteria: list[str]
    labels: list[str]
    parent_task_id: str | None
    result: dict[str, Any] | None
    events: list[TaskEventResponse] = Field(default_factory=list)


class ClaimRequest(BaseModel):
    task_id: str
    lease_seconds: int | None = Field(default=None, ge=60, le=86_400)


class ClaimResponse(BaseModel):
    task: TaskDetail
    lease_token: str
    lease_expires_at: datetime


class LeaseRequest(BaseModel):
    task_id: str
    lease_token: Annotated[str, Field(min_length=32, max_length=500)]


class ProgressRequest(LeaseRequest):
    phase: Annotated[str, Field(min_length=1, max_length=100)]
    message: Annotated[str, Field(min_length=1, max_length=4_000)]
    metadata: dict[str, Any] = Field(default_factory=dict)


class TestFailure(BaseModel):
    test: Annotated[str, Field(min_length=1, max_length=500)]
    message: Annotated[str, Field(min_length=1, max_length=4_000)]


class TestReportRequest(LeaseRequest):
    command: Annotated[str, Field(min_length=1, max_length=2_000)]
    status: Literal["passed", "failed", "error"]
    exit_code: int
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    failures: list[TestFailure] = Field(default_factory=list, max_length=100)
    artifact_url: HttpUrl | None = None


class IssueProposalRequest(LeaseRequest):
    title: ShortText
    description: LongText
    severity: Literal["low", "medium", "high", "critical"]
    evidence: list[Annotated[str, Field(min_length=1, max_length=2_000)]] = Field(
        default_factory=list, max_length=50
    )
    suggested_scope: Annotated[str, Field(max_length=4_000)] = ""


class SubmissionRequest(LeaseRequest):
    summary: Annotated[str, Field(min_length=1, max_length=10_000)]
    commits: list[Annotated[str, Field(min_length=7, max_length=64)]] = Field(
        default_factory=list, max_length=100
    )
    pull_request_url: HttpUrl | None = None
    changed_files: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        default_factory=list, max_length=1_000
    )
    remaining_risks: list[Annotated[str, Field(min_length=1, max_length=2_000)]] = Field(
        default_factory=list, max_length=100
    )


class ReviewRequest(BaseModel):
    decision: Literal["accept", "reject"]
    reason: Annotated[str, Field(min_length=1, max_length=4_000)]
