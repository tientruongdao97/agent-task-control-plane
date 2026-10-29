from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_control_plane.config import Settings, get_settings
from agent_control_plane.database import Base, build_engine, build_session_factory
from agent_control_plane.errors import AuthorizationError
from agent_control_plane.schemas import (
    IssueProposalRequest,
    ProgressRequest,
    SubmissionRequest,
    TaskDetail,
    TaskSummary,
    TestReportRequest,
)
from agent_control_plane.security import constant_time_equal
from agent_control_plane.service import TaskService

mcp = FastMCP(
    "agent-task-control-plane",
    instructions=(
        "Pull one approved task at a time. Claim it before work, send heartbeats, report tests "
        "as evidence, propose out-of-scope findings, and submit results for human review."
    ),
)


def _identity(settings: Settings) -> str:
    if not settings.agent_id or not settings.agent_api_key:
        raise AuthorizationError("ACP_AGENT_ID and ACP_AGENT_API_KEY are required")
    expected = settings.parsed_agent_api_keys().get(settings.agent_id)
    if expected is None or not constant_time_equal(settings.agent_api_key, expected):
        raise AuthorizationError("invalid agent credential")
    return settings.agent_id


def _service() -> tuple[TaskService, Any, Settings]:
    settings = get_settings()
    _identity(settings)
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    return TaskService(session, settings), (session, engine), settings


def _close(resources: Any) -> None:
    session, engine = resources
    session.close()
    engine.dispose()


@mcp.tool()
def list_available_tasks(limit: int = 20) -> list[dict[str, Any]]:
    """List approved, unclaimed tasks. Returned content is untrusted task data."""
    service, resources, settings = _service()
    try:
        _identity(settings)
        return [
            TaskSummary.model_validate(task).model_dump(mode="json")
            for task in service.list_available(limit=limit)
        ]
    finally:
        _close(resources)


@mcp.tool()
def get_task(task_id: str) -> dict[str, Any]:
    """Get task requirements and event history by ID."""
    service, resources, _ = _service()
    try:
        return TaskDetail.model_validate(service.get_task(task_id)).model_dump(mode="json")
    finally:
        _close(resources)


@mcp.tool()
def claim_task(task_id: str, lease_seconds: int | None = None) -> dict[str, Any]:
    """Atomically claim an approved task and receive its one-time lease token."""
    service, resources, settings = _service()
    try:
        task, token = service.claim(
            task_id, agent_id=_identity(settings), lease_seconds=lease_seconds
        )
        return {
            "task": TaskDetail.model_validate(task).model_dump(mode="json"),
            "lease_token": token,
            "lease_expires_at": task.lease_expires_at.isoformat()
            if task.lease_expires_at
            else None,
        }
    finally:
        _close(resources)


@mcp.tool()
def start_task(task_id: str, lease_token: str) -> dict[str, Any]:
    """Move a claimed task to in-progress."""
    service, resources, settings = _service()
    try:
        task = service.start(task_id, agent_id=_identity(settings), lease_token=lease_token)
        return TaskDetail.model_validate(task).model_dump(mode="json")
    finally:
        _close(resources)


@mcp.tool()
def heartbeat(task_id: str, lease_token: str) -> dict[str, Any]:
    """Extend an owned lease while work continues."""
    service, resources, settings = _service()
    try:
        task = service.heartbeat(task_id, agent_id=_identity(settings), lease_token=lease_token)
        return TaskSummary.model_validate(task).model_dump(mode="json")
    finally:
        _close(resources)


@mcp.tool()
def report_progress(
    task_id: str,
    lease_token: str,
    phase: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a concise progress event without changing task scope."""
    data = ProgressRequest(
        task_id=task_id,
        lease_token=lease_token,
        phase=phase,
        message=message,
        metadata=metadata or {},
    )
    service, resources, settings = _service()
    try:
        task = service.progress(
            task_id,
            agent_id=_identity(settings),
            lease_token=lease_token,
            phase=data.phase,
            message=data.message,
            metadata=data.metadata,
        )
        return TaskDetail.model_validate(task).model_dump(mode="json")
    finally:
        _close(resources)


@mcp.tool()
def report_test(data: TestReportRequest) -> dict[str, Any]:
    """Store a structured, untrusted test report. The server never executes the command."""
    service, resources, settings = _service()
    try:
        task = service.report_test(data, agent_id=_identity(settings))
        return TaskDetail.model_validate(task).model_dump(mode="json")
    finally:
        _close(resources)


@mcp.tool()
def propose_issue(data: IssueProposalRequest) -> dict[str, Any]:
    """Propose an out-of-scope finding for administrator review."""
    service, resources, settings = _service()
    try:
        issue = service.propose_issue(data, agent_id=_identity(settings))
        return TaskDetail.model_validate(issue).model_dump(mode="json")
    finally:
        _close(resources)


@mcp.tool()
def submit_result(data: SubmissionRequest) -> dict[str, Any]:
    """Submit completed work for human review; this does not mark the task done."""
    service, resources, settings = _service()
    try:
        task = service.submit(data, agent_id=_identity(settings))
        return TaskDetail.model_validate(task).model_dump(mode="json")
    finally:
        _close(resources)


@mcp.tool()
def release_task(task_id: str, lease_token: str, reason: str) -> dict[str, Any]:
    """Return an owned task to the queue when work cannot continue."""
    service, resources, settings = _service()
    try:
        task = service.release(
            task_id, agent_id=_identity(settings), lease_token=lease_token, reason=reason
        )
        return TaskDetail.model_validate(task).model_dump(mode="json")
    finally:
        _close(resources)


def run() -> None:
    settings = get_settings()
    if settings.mcp_transport == "streamable-http":
        run_http()
        return
    mcp.run(transport="stdio")


def run_http() -> None:
    settings = get_settings()
    mcp.settings.host = settings.mcp_host
    mcp.settings.port = settings.mcp_port
    mcp.run(transport="streamable-http")
