from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from agent_control_plane.config import Settings
from agent_control_plane.errors import (
    AuthorizationError,
    ConflictError,
    InvalidTransitionError,
    NotFoundError,
)
from agent_control_plane.models import EventType, Task, TaskEvent, TaskStatus
from agent_control_plane.schemas import (
    IssueProposalRequest,
    SubmissionRequest,
    TaskCreate,
    TestReportRequest,
)
from agent_control_plane.security import constant_time_equal, hash_lease_token, new_lease_token


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TaskService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def create_task(
        self,
        data: TaskCreate,
        *,
        actor_id: str,
        proposed: bool = False,
    ) -> Task:
        if data.parent_task_id is not None:
            self._get_task(data.parent_task_id)
        task = Task(
            **data.model_dump(),
            status=TaskStatus.PROPOSED if proposed else TaskStatus.OPEN,
        )
        self.session.add(task)
        self.session.flush()
        self._event(task, EventType.CREATED, "agent" if proposed else "admin", actor_id, {})
        self.session.commit()
        return self.get_task(task.id)

    def list_tasks(self, *, status: TaskStatus | None = None, limit: int = 100) -> list[Task]:
        self._reclaim_expired()
        query = select(Task).order_by(Task.created_at.desc()).limit(limit)
        if status is not None:
            query = query.where(Task.status == status)
        return list(self.session.scalars(query))

    def list_available(self, *, limit: int = 20) -> list[Task]:
        self._reclaim_expired()
        query = (
            select(Task)
            .where(Task.status == TaskStatus.OPEN)
            .order_by(Task.priority.desc(), Task.created_at.asc())
            .limit(limit)
        )
        return list(self.session.scalars(query))

    def get_task(self, task_id: str) -> Task:
        query = select(Task).where(Task.id == task_id).options(selectinload(Task.events))
        task = self.session.scalar(query)
        if task is None:
            raise NotFoundError("task not found")
        return task

    def claim(self, task_id: str, *, agent_id: str, lease_seconds: int | None) -> tuple[Task, str]:
        self._reclaim_expired()
        task = self._get_task_for_update(task_id)
        if task.status != TaskStatus.OPEN:
            raise ConflictError("task is not available")
        ttl = min(
            lease_seconds or self.settings.lease_ttl_seconds, self.settings.max_lease_ttl_seconds
        )
        token = new_lease_token()
        task.status = TaskStatus.CLAIMED
        task.claimed_by = agent_id
        task.lease_token_hash = hash_lease_token(token)
        task.lease_expires_at = utc_now() + timedelta(seconds=ttl)
        task.version += 1
        self._event(task, EventType.CLAIMED, "agent", agent_id, {"lease_seconds": ttl})
        self.session.commit()
        return self.get_task(task.id), token

    def start(self, task_id: str, *, agent_id: str, lease_token: str) -> Task:
        task = self._authorize_lease(task_id, agent_id, lease_token)
        if task.status != TaskStatus.CLAIMED:
            raise InvalidTransitionError("only claimed tasks can be started")
        task.status = TaskStatus.IN_PROGRESS
        task.version += 1
        self._event(task, EventType.STARTED, "agent", agent_id, {})
        self.session.commit()
        return self.get_task(task.id)

    def heartbeat(self, task_id: str, *, agent_id: str, lease_token: str) -> Task:
        task = self._authorize_lease(task_id, agent_id, lease_token)
        task.lease_expires_at = utc_now() + timedelta(seconds=self.settings.lease_ttl_seconds)
        task.version += 1
        self._event(task, EventType.HEARTBEAT, "agent", agent_id, {})
        self.session.commit()
        return self.get_task(task.id)

    def progress(
        self,
        task_id: str,
        *,
        agent_id: str,
        lease_token: str,
        phase: str,
        message: str,
        metadata: dict[str, Any],
    ) -> Task:
        task = self._authorize_active(task_id, agent_id, lease_token)
        self._event(
            task,
            EventType.PROGRESS,
            "agent",
            agent_id,
            {"phase": phase, "message": message, "metadata": metadata},
        )
        self.session.commit()
        return self.get_task(task.id)

    def report_test(self, data: TestReportRequest, *, agent_id: str) -> Task:
        task = self._authorize_active(data.task_id, agent_id, data.lease_token)
        payload = data.model_dump(exclude={"task_id", "lease_token"}, mode="json")
        payload["trusted"] = False
        self._event(task, EventType.TEST_REPORTED, "agent", agent_id, payload)
        self.session.commit()
        return self.get_task(task.id)

    def propose_issue(self, data: IssueProposalRequest, *, agent_id: str) -> Task:
        parent = self._authorize_active(data.task_id, agent_id, data.lease_token)
        child_data = TaskCreate(
            title=data.title,
            description=data.description,
            repository=parent.repository,
            base_branch=parent.base_branch,
            acceptance_criteria=[],
            labels=["agent-proposed", f"severity:{data.severity}"],
            parent_task_id=parent.id,
        )
        issue = self.create_task(child_data, actor_id=agent_id, proposed=True)
        parent = self._get_task(parent.id)
        self._event(
            parent,
            EventType.ISSUE_PROPOSED,
            "agent",
            agent_id,
            {
                "issue_id": issue.id,
                "evidence": data.evidence,
                "suggested_scope": data.suggested_scope,
            },
        )
        self.session.commit()
        return issue

    def submit(self, data: SubmissionRequest, *, agent_id: str) -> Task:
        task = self._authorize_active(data.task_id, agent_id, data.lease_token)
        result = data.model_dump(exclude={"task_id", "lease_token"}, mode="json")
        task.result = result
        task.status = TaskStatus.SUBMITTED
        self._clear_lease(task)
        task.version += 1
        self._event(task, EventType.SUBMITTED, "agent", agent_id, result)
        self.session.commit()
        return self.get_task(task.id)

    def release(self, task_id: str, *, agent_id: str, lease_token: str, reason: str) -> Task:
        task = self._authorize_lease(task_id, agent_id, lease_token)
        task.status = TaskStatus.OPEN
        self._clear_lease(task)
        task.version += 1
        self._event(task, EventType.RELEASED, "agent", agent_id, {"reason": reason})
        self.session.commit()
        return self.get_task(task.id)

    def review(self, task_id: str, *, decision: str, reason: str, actor_id: str) -> Task:
        task = self._get_task_for_update(task_id)
        if task.status == TaskStatus.PROPOSED:
            task.status = TaskStatus.OPEN if decision == "accept" else TaskStatus.REJECTED
        elif task.status == TaskStatus.SUBMITTED:
            task.status = TaskStatus.DONE if decision == "accept" else TaskStatus.OPEN
            if decision == "reject":
                task.result = None
        else:
            raise InvalidTransitionError("only proposed or submitted tasks can be reviewed")
        task.version += 1
        self._event(
            task,
            EventType.REVIEWED,
            "admin",
            actor_id,
            {"decision": decision, "reason": reason},
        )
        self.session.commit()
        return self.get_task(task.id)

    def _authorize_active(self, task_id: str, agent_id: str, lease_token: str) -> Task:
        task = self._authorize_lease(task_id, agent_id, lease_token)
        if task.status not in {TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED}:
            raise InvalidTransitionError("task does not have an active work state")
        return task

    def _authorize_lease(self, task_id: str, agent_id: str, lease_token: str) -> Task:
        task = self._get_task_for_update(task_id)
        if task.claimed_by != agent_id or task.lease_token_hash is None:
            raise AuthorizationError("agent does not own this task")
        if task.lease_expires_at is None or as_utc(task.lease_expires_at) <= utc_now():
            raise AuthorizationError("lease has expired")
        if not constant_time_equal(hash_lease_token(lease_token), task.lease_token_hash):
            raise AuthorizationError("invalid lease token")
        return task

    def _get_task(self, task_id: str) -> Task:
        task = self.session.get(Task, task_id)
        if task is None:
            raise NotFoundError("task not found")
        return task

    def _get_task_for_update(self, task_id: str) -> Task:
        task = self.session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if task is None:
            raise NotFoundError("task not found")
        return task

    def _reclaim_expired(self) -> None:
        now = utc_now()
        tasks = self.session.scalars(
            select(Task).where(
                Task.lease_expires_at.is_not(None),
                Task.status.in_([TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED]),
            )
        )
        changed = False
        for task in tasks:
            if task.lease_expires_at is not None and as_utc(task.lease_expires_at) <= now:
                previous_agent = task.claimed_by or "unknown"
                task.status = TaskStatus.OPEN
                self._clear_lease(task)
                task.version += 1
                self._event(
                    task,
                    EventType.RELEASED,
                    "system",
                    "lease-reaper",
                    {"reason": "lease expired", "previous_agent": previous_agent},
                )
                changed = True
        if changed:
            self.session.commit()

    @staticmethod
    def _clear_lease(task: Task) -> None:
        task.claimed_by = None
        task.lease_token_hash = None
        task.lease_expires_at = None

    def _event(
        self,
        task: Task,
        event_type: EventType,
        actor_type: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> None:
        self.session.add(
            TaskEvent(
                task=task,
                event_type=event_type,
                actor_type=actor_type,
                actor_id=actor_id,
                payload=payload,
            )
        )
