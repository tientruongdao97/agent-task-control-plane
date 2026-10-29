from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from agent_control_plane.config import Settings
from agent_control_plane.database import Base, build_session_factory


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite:///:memory:",
        admin_api_key="admin-secret-with-at-least-32-characters",
        agent_api_keys="agent-a:agent-secret-with-at-least-32-characters",
        lease_ttl_seconds=300,
        max_lease_ttl_seconds=600,
        docs_enabled=False,
    )


@pytest.fixture
def engine() -> Iterator[Engine]:
    value = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(value)
    yield value
    value.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return build_session_factory(engine)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as value:
        yield value
