import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from agent_control_plane.config import Settings, get_settings
from agent_control_plane.database import Base, build_engine, build_session_factory
from agent_control_plane.errors import (
    AuthorizationError,
    ConflictError,
    ControlPlaneError,
    InvalidTransitionError,
    NotFoundError,
)
from agent_control_plane.models import TaskStatus
from agent_control_plane.schemas import ReviewRequest, TaskCreate, TaskDetail, TaskSummary
from agent_control_plane.security import constant_time_equal
from agent_control_plane.service import TaskService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    logging.basicConfig(level=app_settings.log_level)
    engine = build_engine(app_settings)
    factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Agent Task Control Plane",
        version="0.1.0",
        docs_url="/docs" if app_settings.docs_enabled else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.session_factory = factory

    def get_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    def require_admin(x_api_key: Annotated[str, Header()]) -> str:
        if not constant_time_equal(x_api_key, app_settings.admin_api_key):
            raise AuthorizationError("invalid administrator credential")
        return "admin"

    SessionDep = Annotated[Session, Depends(get_session)]
    AdminDep = Annotated[str, Depends(require_admin)]

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(ControlPlaneError)
    async def domain_error_handler(_: Request, exc: ControlPlaneError) -> JSONResponse:
        status = 400
        if isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, AuthorizationError):
            status = 401
        elif isinstance(exc, (ConflictError, InvalidTransitionError)):
            status = 409
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/admin", status_code=307)

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    def admin_ui() -> str:
        return files("agent_control_plane").joinpath("static/admin.html").read_text()

    @app.post("/v1/tasks", response_model=TaskDetail, status_code=201)
    def create_task(data: TaskCreate, session: SessionDep, actor: AdminDep) -> TaskDetail:
        task = TaskService(session, app_settings).create_task(data, actor_id=actor)
        return TaskDetail.model_validate(task)

    @app.get("/v1/tasks", response_model=list[TaskSummary])
    def list_tasks(
        session: SessionDep,
        _: AdminDep,
        status: TaskStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[TaskSummary]:
        tasks = TaskService(session, app_settings).list_tasks(status=status, limit=limit)
        return [TaskSummary.model_validate(task) for task in tasks]

    @app.get("/v1/tasks/{task_id}", response_model=TaskDetail)
    def get_task(task_id: str, session: SessionDep, _: AdminDep) -> TaskDetail:
        return TaskDetail.model_validate(TaskService(session, app_settings).get_task(task_id))

    @app.post("/v1/tasks/{task_id}/review", response_model=TaskDetail)
    def review_task(
        task_id: str,
        data: ReviewRequest,
        session: SessionDep,
        actor: AdminDep,
    ) -> TaskDetail:
        task = TaskService(session, app_settings).review(
            task_id, decision=data.decision, reason=data.reason, actor_id=actor
        )
        return TaskDetail.model_validate(task)

    return app


def run() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, access_log=False)
