"""Force offline providers for the test-suite regardless of the developer's .env."""

import os

os.environ.update(
    {
        "LLM_PROVIDER": "mock",
        "EMBEDDING_PROVIDER": "local",
        "PINECONE_API_KEY": "",
        "LANGSMITH_API_KEY": "",
        "LANGSMITH_TRACING": "false",
        "MCP_SERVER_URL": "",
        "RATE_LIMIT_CAPACITY": "5",
        "RATE_LIMIT_REFILL_PER_SECOND": "0.5",
    }
)

import pytest

from assistant.auth import Role, UserContext


@pytest.fixture
def viewer() -> UserContext:
    return UserContext(
        username="viewer",
        display_name="Vera",
        role=Role.VIEWER,
        department="retail_banking",
        clearance="internal",
    )


@pytest.fixture
def analyst() -> UserContext:
    return UserContext(
        username="analyst",
        display_name="Aiden",
        role=Role.ANALYST,
        department="payments",
        clearance="confidential",
    )


@pytest.fixture
def admin() -> UserContext:
    return UserContext(
        username="admin",
        display_name="Ada",
        role=Role.ADMIN,
        department="platform_engineering",
        clearance="restricted",
    )
