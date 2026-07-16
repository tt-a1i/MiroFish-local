import asyncio

from pydantic import BaseModel

from app.services.graphiti_llm_adapter import (
    generate_response_without_response_format,
    normalize_graphiti_response_model_payload,
)
from app.services.zep_graphiti_impl import _call_with_graphiti_rate_limit_retry


class FakeMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = type("Message", (), {"content": content})()


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.responses.pop(0))


class FakeChat:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)


class FakeOpenAIClient:
    def __init__(self, responses):
        self.chat = FakeChat(responses)


class FakeLLM:
    model = "deepseek-v4-flash"
    temperature = 0
    max_tokens = 128

    def __init__(self, responses):
        self.client = FakeOpenAIClient(responses)

    def _clean_input(self, content):
        return content


def test_graphiti_llm_adapter_wraps_top_level_list_for_single_list_response_model():
    class FakeItem(BaseModel):
        name: str

    class FakeItems(BaseModel):
        extracted_entities: list[FakeItem]

    result = normalize_graphiti_response_model_payload([{"name": "雷军"}], FakeItems)

    assert result == {"extracted_entities": [{"name": "雷军"}]}


def test_graphiti_llm_adapter_normalizes_deepseek_node_aliases():
    class FakeItem(BaseModel):
        name: str
        entity_type_id: int

    class FakeItems(BaseModel):
        extracted_entities: list[FakeItem]

    result = normalize_graphiti_response_model_payload(
        {
            "nodes": [
                {"entity_name": "张雪", "entity_type_id": 3},
                {"entity_name": "凯越", "entity_type_id": 4},
            ]
        },
        FakeItems,
    )

    assert result == {
        "extracted_entities": [
            {"entity_name": "张雪", "entity_type_id": 3, "name": "张雪"},
            {"entity_name": "凯越", "entity_type_id": 4, "name": "凯越"},
        ]
    }


def test_graphiti_llm_adapter_normalizes_deepseek_edge_aliases():
    class FakeEdge(BaseModel):
        relation_type: str
        source_entity_id: int
        target_entity_id: int
        fact: str

    class FakeEdges(BaseModel):
        edges: list[FakeEdge]

    result = normalize_graphiti_response_model_payload(
        {
            "relationships": [
                {
                    "relation": "WON_CHAMPIONSHIP",
                    "source_id": 0,
                    "target_id": 1,
                    "description": "张雪赢得赛事冠军",
                }
            ]
        },
        FakeEdges,
    )

    assert result == {
        "edges": [
            {
                "relation": "WON_CHAMPIONSHIP",
                "source_id": 0,
                "target_id": 1,
                "description": "张雪赢得赛事冠军",
                "relation_type": "WON_CHAMPIONSHIP",
                "source_entity_id": 0,
                "target_entity_id": 1,
                "fact": "张雪赢得赛事冠军",
            }
        ]
    }


def test_graphiti_llm_adapter_generates_without_response_format():
    fake_llm = FakeLLM(['{"ok": true}'])
    messages = [
        FakeMessage("system", "你是结构化抽取助手。"),
        FakeMessage("user", "抽取实体。"),
    ]

    async def run_call():
        return await generate_response_without_response_format(fake_llm, messages)

    result = asyncio.run(run_call())

    assert result == {"ok": True}
    assert len(fake_llm.client.chat.completions.calls) == 1
    assert "response_format" not in fake_llm.client.chat.completions.calls[0]
    assert fake_llm.client.chat.completions.calls[0]["model"] == "deepseek-v4-flash"


def test_graphiti_llm_adapter_parses_fenced_json_without_response_format():
    fake_llm = FakeLLM(['```json\n{"ok": true}\n```'])
    messages = [FakeMessage("system", "JSON"), FakeMessage("user", "抽取实体。")]

    async def run_call():
        return await generate_response_without_response_format(fake_llm, messages)

    assert asyncio.run(run_call()) == {"ok": True}


def test_graphiti_llm_adapter_retries_empty_json_fallback_response(monkeypatch):
    fake_llm = FakeLLM(["", '{"ok": true}'])
    messages = [FakeMessage("system", "JSON"), FakeMessage("user", "抽取关系。")]

    async def fake_sleep(seconds):
        return None

    async def call_adapter():
        return await generate_response_without_response_format(fake_llm, messages)

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_LLM_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_MAX_RETRIES", 1)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_RETRY_SECONDS", 0)
    monkeypatch.setattr("app.services.zep_graphiti_impl.asyncio.sleep", fake_sleep)

    result = asyncio.run(
        _call_with_graphiti_rate_limit_retry(
            call_adapter,
            operation="llm.generate_response",
            item_count=1,
        )
    )

    assert result == {"ok": True}
    assert len(fake_llm.client.chat.completions.calls) == 2
