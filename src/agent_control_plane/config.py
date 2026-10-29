from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACP_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./data/control-plane.db"
    admin_api_key: str = Field(min_length=32)
    agent_api_keys: str = ""
    agent_id: str | None = None
    agent_api_key: str | None = None
    lease_ttl_seconds: int = Field(default=900, ge=60, le=86_400)
    max_lease_ttl_seconds: int = Field(default=3_600, ge=60, le=86_400)
    log_level: str = "INFO"
    docs_enabled: bool = True
    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8001, ge=1, le=65_535)
    mcp_public_url: str | None = None

    def effective_mcp_public_url(self) -> str:
        return self.mcp_public_url or f"http://{self.mcp_host}:{self.mcp_port}"

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        allowed = {"development", "test", "production"}
        if value not in allowed:
            raise ValueError(f"environment must be one of {sorted(allowed)}")
        return value

    @field_validator("mcp_transport")
    @classmethod
    def validate_mcp_transport(cls, value: str) -> str:
        if value not in {"stdio", "streamable-http"}:
            raise ValueError("mcp_transport must be stdio or streamable-http")
        return value

    def parsed_agent_api_keys(self) -> dict[str, str]:
        result: dict[str, str] = {}
        if not self.agent_api_keys.strip():
            return result
        for pair in self.agent_api_keys.split(","):
            agent_id, separator, secret = pair.strip().partition(":")
            if not separator or not agent_id or len(secret) < 32:
                raise ValueError(
                    "ACP_AGENT_API_KEYS entries must be agent-id:key with 32+ char keys"
                )
            if agent_id in result:
                raise ValueError(f"duplicate agent ID in ACP_AGENT_API_KEYS: {agent_id}")
            result[agent_id] = secret
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
