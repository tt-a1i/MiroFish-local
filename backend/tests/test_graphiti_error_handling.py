import pytest

from app.services.graphiti_llm_adapter import ensure_graphiti_json_instruction
from app.services.zep_graphiti_impl import GraphitiClient
from app.utils.neo4j_errors import format_neo4j_auth_error, is_neo4j_auth_error


class DummyMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


class DummyNeo4jAuthError(Exception):
    neo4j_code = "Neo.ClientError.Security.AuthenticationRateLimit"


def test_graphiti_json_instruction_is_appended_when_missing():
    messages = [
        DummyMessage("system", "你是结构化抽取助手。"),
        DummyMessage("user", "抽取实体和关系。"),
    ]

    ensure_graphiti_json_instruction((messages,), {})

    assert "JSON" in messages[0].content
    assert "Markdown" in messages[0].content
    assert messages[1].content == "抽取实体和关系。"


def test_graphiti_json_instruction_keeps_existing_json_prompt():
    messages = [
        DummyMessage("system", "Return a valid json object."),
        DummyMessage("user", "抽取实体和关系。"),
    ]

    ensure_graphiti_json_instruction((messages,), {})

    assert messages[0].content == "Return a valid json object."


def test_neo4j_auth_rate_limit_is_classified_as_auth_error():
    error = DummyNeo4jAuthError("The client has provided incorrect authentication details too many times in a row.")

    assert is_neo4j_auth_error(error)
    assert "NEO4J_PASSWORD" in format_neo4j_auth_error(error)


def test_graphiti_client_preflights_neo4j_connectivity(monkeypatch):
    calls = {}

    class DummyDriver:
        def verify_connectivity(self):
            calls["verified"] = True

        def close(self):
            calls["closed"] = True

    def fake_driver(uri, *, auth, connection_timeout):
        calls["uri"] = uri
        calls["auth"] = auth
        calls["connection_timeout"] = connection_timeout
        return DummyDriver()

    monkeypatch.setattr("neo4j.GraphDatabase.driver", fake_driver)

    client = GraphitiClient("bolt://example:7687", "neo4j", "secret")
    client._verify_neo4j_connectivity()

    assert calls == {
        "uri": "bolt://example:7687",
        "auth": ("neo4j", "secret"),
        "connection_timeout": 5,
        "verified": True,
        "closed": True,
    }


def test_graphiti_client_preflight_raises_auth_error(monkeypatch):
    class DummyDriver:
        def verify_connectivity(self):
            raise DummyNeo4jAuthError("incorrect authentication details")

        def close(self):
            pass

    monkeypatch.setattr(
        "neo4j.GraphDatabase.driver",
        lambda *args, **kwargs: DummyDriver(),
    )

    client = GraphitiClient("bolt://example:7687", "neo4j", "wrong")

    with pytest.raises(DummyNeo4jAuthError):
        client._verify_neo4j_connectivity()
