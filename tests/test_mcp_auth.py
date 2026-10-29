import pytest

from agent_control_plane.config import Settings
from agent_control_plane.mcp_server import StaticAgentTokenVerifier


@pytest.mark.anyio
async def test_http_token_resolves_distinct_agent_identity(settings: Settings) -> None:
    verifier = StaticAgentTokenVerifier(settings)

    token = await verifier.verify_token("agent-secret-with-at-least-32-characters")

    assert token is not None
    assert token.subject == "agent-a"
    assert token.client_id == "agent-a"
    assert token.scopes == ["tasks:work"]


@pytest.mark.anyio
async def test_http_token_rejects_unknown_agent(settings: Settings) -> None:
    verifier = StaticAgentTokenVerifier(settings)

    assert await verifier.verify_token("unknown-secret-with-at-least-32-characters") is None


def test_duplicate_agent_ids_are_rejected(settings: Settings) -> None:
    duplicate = settings.model_copy(
        update={
            "agent_api_keys": (
                "agent-a:agent-secret-with-at-least-32-characters,"
                "agent-a:another-agent-secret-with-32-characters"
            )
        }
    )

    with pytest.raises(ValueError, match="duplicate agent ID"):
        duplicate.parsed_agent_api_keys()
