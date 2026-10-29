from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from agent_control_plane.config import Settings
from agent_control_plane.errors import AuthorizationError, ConflictError
from agent_control_plane.models import TaskStatus
from agent_control_plane.schemas import SubmissionRequest, TaskCreate, TestReportRequest
from agent_control_plane.service import TaskService, utc_now


def task_data() -> TaskCreate:
    return TaskCreate(
        title="Fix authentication race",
        description="Prevent duplicate users during concurrent OAuth login.",
        repository="example/backend",
        acceptance_criteria=["Concurrency test passes", "No duplicate user is created"],
    )


def test_claim_is_atomic_and_token_is_not_stored_plaintext(
    session: Session, settings: Settings
) -> None:
    service = TaskService(session, settings)
    created = service.create_task(task_data(), actor_id="admin")

    claimed, token = service.claim(created.id, agent_id="agent-a", lease_seconds=120)

    assert claimed.status == TaskStatus.CLAIMED
    assert claimed.claimed_by == "agent-a"
    assert claimed.lease_token_hash is not None
    assert claimed.lease_token_hash != token
    with pytest.raises(ConflictError):
        service.claim(created.id, agent_id="agent-b", lease_seconds=120)


def test_wrong_agent_or_token_cannot_update(session: Session, settings: Settings) -> None:
    service = TaskService(session, settings)
    task = service.create_task(task_data(), actor_id="admin")
    _, token = service.claim(task.id, agent_id="agent-a", lease_seconds=120)

    with pytest.raises(AuthorizationError):
        service.start(task.id, agent_id="agent-b", lease_token=token)
    with pytest.raises(AuthorizationError):
        service.start(task.id, agent_id="agent-a", lease_token="x" * 43)


def test_expired_lease_returns_task_to_queue(session: Session, settings: Settings) -> None:
    service = TaskService(session, settings)
    task = service.create_task(task_data(), actor_id="admin")
    claimed, _ = service.claim(task.id, agent_id="agent-a", lease_seconds=120)
    claimed.lease_expires_at = utc_now() - timedelta(seconds=1)
    session.commit()

    available = service.list_available()

    assert [item.id for item in available] == [task.id]
    assert available[0].status == TaskStatus.OPEN
    assert available[0].claimed_by is None


def test_test_report_is_marked_untrusted(session: Session, settings: Settings) -> None:
    service = TaskService(session, settings)
    task = service.create_task(task_data(), actor_id="admin")
    _, token = service.claim(task.id, agent_id="agent-a", lease_seconds=120)
    service.start(task.id, agent_id="agent-a", lease_token=token)

    result = service.report_test(
        TestReportRequest(
            task_id=task.id,
            lease_token=token,
            command="pytest -q",
            status="passed",
            exit_code=0,
            passed=12,
        ),
        agent_id="agent-a",
    )

    event = next(item for item in result.events if item.event_type.value == "test_reported")
    assert event.payload["trusted"] is False


def test_submission_requires_human_review(session: Session, settings: Settings) -> None:
    service = TaskService(session, settings)
    task = service.create_task(task_data(), actor_id="admin")
    _, token = service.claim(task.id, agent_id="agent-a", lease_seconds=120)
    service.start(task.id, agent_id="agent-a", lease_token=token)

    submitted = service.submit(
        SubmissionRequest(
            task_id=task.id,
            lease_token=token,
            summary="Fixed the transaction boundary and added a regression test.",
            commits=["a76f28c"],
        ),
        agent_id="agent-a",
    )
    assert submitted.status == TaskStatus.SUBMITTED

    reviewed = service.review(
        task.id, decision="accept", reason="CI and review passed", actor_id="admin"
    )
    assert reviewed.status == TaskStatus.DONE
