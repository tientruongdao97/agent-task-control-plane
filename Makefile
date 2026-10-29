.PHONY: install format lint typecheck test check api mcp mcp-http

install:
	uv sync --all-extras

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy

test:
	uv run pytest

check: lint typecheck test

api:
	uv run agent-control-api

mcp:
	uv run agent-control-mcp

mcp-http:
	ACP_MCP_TRANSPORT=streamable-http uv run agent-control-mcp
