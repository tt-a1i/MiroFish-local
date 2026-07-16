import asyncio
import json
import sys
import threading
import time
import types

from app.config import Config
from app import create_app
from app.models.project import ProjectManager, ProjectStatus
from app.models.task import TaskManager
from app.services.graph_builder import GraphBuilderService


class FakeClient:
    def __init__(self):
        self.batches = []

    def add_episode_batch(self, graph_id, episodes):
        self.batches.append((graph_id, episodes))
        return [f"episode_{len(self.batches)}_{index}" for index, _ in enumerate(episodes)]


class OrderedFakeClient:
    def __init__(self):
        self.batches = []

    def add_episode_batch(self, graph_id, episodes):
        self.batches.append((graph_id, episodes))
        return [episodes[0]["data"]]


def test_graph_builder_uses_independent_boost_client_only_in_build_mode(monkeypatch):
    created = []

    class Endpoint:
        model = "boost-model"
        is_boost = True

    def fake_create_zep_client(**kwargs):
        created.append(("create", kwargs))
        return FakeClient()

    def fake_get_zep_client(backend=None):
        created.append(("get", {"backend": backend}))
        return FakeClient()

    monkeypatch.setattr("app.services.graph_builder.get_preferred_llm_endpoint", lambda prefer_boost=True: Endpoint())
    monkeypatch.setattr("app.services.graph_builder.create_zep_client", fake_create_zep_client)
    monkeypatch.setattr("app.services.graph_builder.get_zep_client", fake_get_zep_client)

    GraphBuilderService(backend="graphiti", build_mode=True)
    GraphBuilderService(backend="graphiti", build_mode=False)

    assert created[0][0] == "create"
    assert created[0][1]["use_singleton"] is False
    assert created[0][1]["llm_endpoint"].model == "boost-model"
    assert created[1] == ("get", {"backend": "graphiti"})


def test_ontology_generator_uses_boost_client_by_default(monkeypatch):
    from app.services.ontology_generator import OntologyGenerator

    created = []

    class FakeLLMClient:
        def __init__(self, prefer_boost=False):
            created.append(prefer_boost)

        def chat_json(self, messages, temperature=0.3, max_tokens=4096):
            return {
                "entity_types": [
                    {"name": "Person", "description": "person", "attributes": []},
                    {"name": "Organization", "description": "org", "attributes": []},
                ],
                "edge_types": [],
                "analysis_summary": "测试",
            }

    monkeypatch.setattr("app.services.ontology_generator.LLMClient", FakeLLMClient)

    result = OntologyGenerator().generate(["材料"], "推演方向")

    assert created == [True]
    assert result["entity_types"][-2]["name"] == "Person"
    assert result["entity_types"][-1]["name"] == "Organization"


def test_ontology_prompt_separates_graph_entities_from_agent_candidates():
    from app.services.ontology_generator import ONTOLOGY_SYSTEM_PROMPT, OntologyGenerator

    generator = OntologyGenerator.__new__(OntologyGenerator)
    message = generator._build_user_message(
        ["被告人许国利与受害人来惠利是案件核心当事人，小红书出现相关讨论。"],
        "推演公众对案件事实披露的反应",
        None,
    )

    assert "图谱实体不等于最终人设 Agent" in ONTOLOGY_SYSTEM_PROMPT
    assert "受害人/被害人、嫌疑人/被告人、主配角、亲属" in ONTOLOGY_SYSTEM_PROMPT
    # 核心人物类型应无条件保留，事件角色类型按事件性质条件使用
    assert "核心人物类型（当事人、主角、配角、关键个人）" in ONTOLOGY_SYSTEM_PROMPT
    assert "涉及刑事/法律/争议事件" in message
    assert "小红书、微博、抖音" in message


def test_graphiti_async_loop_waits_for_ready_when_thread_is_alive(monkeypatch):
    from app.services import zep_graphiti_impl

    loop = asyncio.new_event_loop()

    class AliveThread:
        def is_alive(self):
            return True

    ready = threading.Event()
    monkeypatch.setattr(zep_graphiti_impl, "_async_loop", None)
    monkeypatch.setattr(zep_graphiti_impl, "_async_thread", AliveThread())
    monkeypatch.setattr(zep_graphiti_impl, "_async_loop_ready", ready)

    def mark_ready():
        time.sleep(0.02)
        zep_graphiti_impl._async_loop = loop
        ready.set()

    starter = threading.Thread(target=mark_ready)
    starter.start()
    try:
        assert zep_graphiti_impl._ensure_async_loop() is loop
    finally:
        starter.join(timeout=1)
        monkeypatch.setattr(zep_graphiti_impl, "_async_loop", None)
        monkeypatch.setattr(zep_graphiti_impl, "_async_thread", None)
        ready.clear()
        loop.close()


def test_graphiti_client_close_is_idempotent(monkeypatch):
    from app.services.zep_graphiti_impl import GraphitiClient

    closed = []

    class FakeGraphiti:
        async def close(self):
            closed.append("close")

    def fake_run_async(coro):
        return asyncio.run(coro)

    client = GraphitiClient("bolt://unused", "neo4j", "password")
    client._graphiti = FakeGraphiti()
    client._driver = object()
    client._initialized = True

    monkeypatch.setattr("app.services.zep_graphiti_impl._run_async", fake_run_async)

    client.close()
    client.close()

    assert closed == ["close"]
    assert client._graphiti is None
    assert client._driver is None
    assert client._initialized is False


def test_graphiti_ontology_is_not_used_as_hard_entity_type_constraint(monkeypatch):
    from app.services import zep_graphiti_impl
    from app.services.zep_graphiti_impl import GraphitiClient

    captured = {}

    class FakeEpisode:
        uuid = "episode-open-types"

    class FakeAddResult:
        episode = FakeEpisode()

    class FakeGraphiti:
        async def add_episode(self, **kwargs):
            captured.update(kwargs)
            return FakeAddResult()

    fake_graphiti_core = types.ModuleType("graphiti_core")
    fake_graphiti_core.__path__ = []
    fake_nodes = types.ModuleType("graphiti_core.nodes")
    fake_nodes.EpisodeType = type("EpisodeType", (), {"text": "text", "message": "message", "json": "json"})
    monkeypatch.setitem(sys.modules, "graphiti_core", fake_graphiti_core)
    monkeypatch.setitem(sys.modules, "graphiti_core.nodes", fake_nodes)
    monkeypatch.setattr(zep_graphiti_impl, "_run_async", lambda coro: asyncio.run(coro))

    client = GraphitiClient("bolt://unused", "neo4j", "password")
    client._graphiti = FakeGraphiti()
    client._initialized = True
    client.set_ontology(
        ["graph-open"],
        entities=[{"name": "Person", "attributes": []}],
        edges=[{"name": "REPORTS_ON", "source_targets": [{"source": "Person", "target": "Organization"}]}],
    )

    episode_uuid = client.add_episode("graph-open", "材料出现了新的自定义主体类型。")

    assert episode_uuid == "episode-open-types"
    assert client._ontology_cache["graph-open"]["schema_hints"]["entities"][0]["name"] == "Person"
    # 本体类型应作为自定义类型传给 Graphiti（提供丰富类型参考），但不作为硬枚举约束
    assert captured["entity_types"] is not None
    assert "Person" in captured["entity_types"]
    assert captured["edge_types"] is not None
    assert "REPORTS_ON" in captured["edge_types"]
    assert captured["edge_type_map"] is not None


def test_graphiti_embedding_throttle_waits_between_requests(monkeypatch):
    from app.services import zep_graphiti_impl

    sleeps = []
    clock = {"value": 100.0}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["value"] += seconds

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_MIN_INTERVAL_SECONDS", 0.5)
    monkeypatch.setattr("app.services.zep_graphiti_impl.time.monotonic", lambda: clock["value"])
    monkeypatch.setattr("app.services.zep_graphiti_impl.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(zep_graphiti_impl, "_last_embedding_request_at", 0.0)

    async def run_throttle():
        await zep_graphiti_impl._throttle_embedding_request("embedding.create", 1)
        await zep_graphiti_impl._throttle_embedding_request("embedding.create", 1)

    asyncio.run(run_throttle())

    assert sleeps == [0.5]


def test_graphiti_default_embedder_uses_independent_embedding_endpoint(monkeypatch):
    from app.services.zep_graphiti_impl import GraphitiClient

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_API_KEY", "")
    monkeypatch.setattr(
        "app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_BASE_URL",
        "http://10.200.89.13:9997/v1",
    )
    monkeypatch.setattr(
        "app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_MODEL",
        "Qwen3-Embedding-4B",
    )
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_DIM", 2560)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_BATCH_SIZE", 32)
    monkeypatch.setenv("OPENAI_API_KEY", "llm-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    embedder = GraphitiClient("bolt://unused", "neo4j", "password")._build_default_embedder()

    assert embedder.config.api_key == "dummy"
    assert embedder.config.base_url == "http://10.200.89.13:9997/v1"
    assert embedder.config.embedding_model == "Qwen3-Embedding-4B"
    assert embedder.config.embedding_dim == 2560
    assert embedder.max_batch_size == 32


def test_graphiti_embedding_endpoint_does_not_change_llm_client(monkeypatch):
    from app.services.zep_graphiti_impl import GraphitiClient

    monkeypatch.setattr(
        "app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_BASE_URL",
        "http://10.200.89.13:9997/v1",
    )
    monkeypatch.setattr(
        "app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_MODEL",
        "Qwen3-Embedding-4B",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "llm-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("GRAPHITI_LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("LLM_MODEL_NAME", "fallback-model")

    llm_client = GraphitiClient("bolt://unused", "neo4j", "password")._build_default_llm_client()

    assert llm_client.config.api_key == "llm-key"
    assert llm_client.config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert llm_client.config.model == "qwen-plus"


def test_graphiti_llm_client_uses_tuned_generation_config(monkeypatch):
    from app.services.zep_graphiti_impl import GraphitiClient

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_LLM_MAX_TOKENS", 4096)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_LLM_SMALL_MODEL", "qwen-turbo")
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_LLM_TEMPERATURE", 0)
    monkeypatch.setenv("OPENAI_API_KEY", "llm-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("GRAPHITI_LLM_MODEL", "qwen-plus")

    llm_client = GraphitiClient("bolt://unused", "neo4j", "password")._build_default_llm_client()

    assert llm_client.config.max_tokens == 4096
    assert llm_client.max_tokens == 4096
    assert llm_client.config.small_model == "qwen-turbo"
    assert llm_client.config.temperature == 0


def test_graphiti_default_embedder_falls_back_to_openai_env(monkeypatch):
    from app.services.zep_graphiti_impl import GraphitiClient

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_API_KEY", "")
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_BASE_URL", "")
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_DIM", 1024)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_BATCH_SIZE", 10)
    monkeypatch.setenv("OPENAI_API_KEY", "llm-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    embedder = GraphitiClient("bolt://unused", "neo4j", "password")._build_default_embedder()

    assert embedder.config.api_key == "llm-key"
    assert embedder.config.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert embedder.config.embedding_model == "text-embedding-v4"
    assert embedder.config.embedding_dim == 1024
    assert embedder.max_batch_size == 10


def test_graphiti_embedding_wrapper_chunks_batch(monkeypatch):
    from app.services import zep_graphiti_impl

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_MAX_RETRIES", 0)

    class FakeEmbedder:
        def __init__(self):
            self.config = object()
            self.calls = []

        async def create(self, input_data):
            return [float(len(input_data))]

        async def create_batch(self, input_data_list):
            self.calls.append(list(input_data_list))
            return [[float(len(item))] for item in input_data_list]

    fake_embedder = FakeEmbedder()
    wrapped = zep_graphiti_impl._create_graphiti_embedding_wrapper(fake_embedder, max_batch_size=2)

    async def run_create_batch():
        return await wrapped.create_batch(["a", "bb", "ccc", "dddd", "eeeee"])

    result = asyncio.run(run_create_batch())

    assert result == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert fake_embedder.calls == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]


def test_graphiti_llm_rate_limit_wrapper_throttles_and_retries(monkeypatch):
    from app.services import zep_graphiti_impl
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.errors import RateLimitError

    sleeps = []
    clock = {"value": 100.0}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["value"] += seconds

    class FakeLLM:
        config = LLMConfig(model="fake-model", temperature=0, max_tokens=128)
        model = "fake-model"
        small_model = None
        temperature = 0
        max_tokens = 128

        def __init__(self):
            self.calls = 0

        def set_tracer(self, tracer):
            self.tracer = tracer

        async def generate_response(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RateLimitError("Rate limit exceeded. Please try again later.")
            return {"ok": True}

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_LLM_CONCURRENCY", 1)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_LLM_MIN_INTERVAL_SECONDS", 0.5)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_MAX_RETRIES", 2)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_RETRY_SECONDS", 20)
    monkeypatch.setattr("app.services.zep_graphiti_impl.time.monotonic", lambda: clock["value"])
    monkeypatch.setattr("app.services.zep_graphiti_impl.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(zep_graphiti_impl, "_last_llm_request_at", 0.0)

    fake_llm = FakeLLM()
    wrapped = zep_graphiti_impl._create_graphiti_llm_rate_limit_wrapper(fake_llm)

    async def run_calls():
        first = await wrapped.generate_response([])
        second = await wrapped.generate_response([])
        return first, second

    first_result, second_result = asyncio.run(run_calls())

    assert first_result == {"ok": True}
    assert second_result == {"ok": True}
    assert fake_llm.calls == 3
    assert sleeps == [20, 0.5]


def test_graphiti_llm_wrapper_retries_without_response_format(monkeypatch):
    from app.services import zep_graphiti_impl
    from graphiti_core.llm_client.config import LLMConfig

    class FakeMessage:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    class FakeChoice:
        message = type("Message", (), {"content": json.dumps({"ok": True})})()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def __init__(self, calls):
            self.calls = calls

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            assert "response_format" not in kwargs
            return FakeResponse()

    class FakeChat:
        def __init__(self, calls):
            self.completions = FakeCompletions(calls)

    class FakeOpenAIClient:
        def __init__(self, calls):
            self.chat = FakeChat(calls)

    class FakeLLM:
        config = LLMConfig(model="deepseek-v4-flash", temperature=0, max_tokens=128)
        model = "deepseek-v4-flash"
        small_model = None
        temperature = 0
        max_tokens = 128

        def __init__(self):
            self.calls = []
            self.client = FakeOpenAIClient(self.calls)

        def set_tracer(self, tracer):
            self.tracer = tracer

        def _clean_input(self, content):
            return content

        async def generate_response(self, *args, **kwargs):
            error = RuntimeError(
                "Error code: 400 - {'error': {'message': 'This response_format type is unavailable now'}}"
            )
            error.status_code = 400
            raise error

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_LLM_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_MAX_RETRIES", 0)

    fake_llm = FakeLLM()
    wrapped = zep_graphiti_impl._create_graphiti_llm_rate_limit_wrapper(fake_llm)
    messages = [
        FakeMessage("system", "你是结构化抽取助手。"),
        FakeMessage("user", "抽取实体。"),
    ]

    async def run_call():
        return await wrapped.generate_response(messages)

    result = asyncio.run(run_call())

    assert result == {"ok": True}
    assert len(fake_llm.calls) == 1
    assert fake_llm.calls[0]["model"] == "deepseek-v4-flash"
    assert "JSON" in messages[0].content


def test_graphiti_quota_exhausted_error_does_not_retry(monkeypatch):
    from app.services import zep_graphiti_impl
    from graphiti_core.llm_client.errors import RateLimitError

    calls = {"count": 0}

    async def fake_sleep(seconds):
        raise AssertionError("额度耗尽错误不应进入退避等待")

    async def always_quota_error():
        calls["count"] += 1
        upstream = Exception("Error code: 429 - insufficient_quota: You exceeded your current quota")
        raise RateLimitError("Rate limit exceeded. Please try again later.") from upstream

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_MAX_RETRIES", 3)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_RETRY_SECONDS", 20)
    monkeypatch.setattr("app.services.zep_graphiti_impl.asyncio.sleep", fake_sleep)

    try:
        asyncio.run(
            zep_graphiti_impl._call_with_graphiti_rate_limit_retry(
                always_quota_error,
                operation="llm.generate_response",
                item_count=1,
            )
        )
    except Exception as exc:
        assert zep_graphiti_impl._is_quota_exhausted_error(exc)
    else:
        raise AssertionError("应抛出额度耗尽异常")

    assert calls["count"] == 1


def test_graphiti_rate_limit_wrapper_times_out_single_llm_request(monkeypatch):
    from app.services import zep_graphiti_impl

    async def slow_request():
        await asyncio.sleep(0.05)
        return {"ok": True}

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_LLM_REQUEST_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_LLM_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_MAX_RETRIES", 0)

    try:
        asyncio.run(
            zep_graphiti_impl._call_with_graphiti_rate_limit_retry(
                slow_request,
                operation="llm.generate_response",
                item_count=1,
            )
        )
    except TimeoutError as exc:
        assert "单次请求超过 0.01 秒未返回" in str(exc)
    else:
        raise AssertionError("Graphiti LLM 单请求超时应快速抛出")


def test_graphiti_rate_limit_wrapper_times_out_single_embedding_request(monkeypatch):
    from app.services import zep_graphiti_impl

    async def slow_request():
        await asyncio.sleep(0.05)
        return [[0.1]]

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_REQUEST_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_EMBEDDING_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_RATE_LIMIT_MAX_RETRIES", 0)

    try:
        asyncio.run(
            zep_graphiti_impl._call_with_graphiti_rate_limit_retry(
                slow_request,
                operation="embedding.create_batch",
                item_count=1,
            )
        )
    except TimeoutError as exc:
        assert "Graphiti embedding.create_batch 单次请求超过 0.01 秒未返回" in str(exc)
    else:
        raise AssertionError("Graphiti embedding 单请求超时应快速抛出")


def test_graphiti_add_episode_disables_previous_context_by_default(monkeypatch):
    from app.services import zep_graphiti_impl
    from app.services.zep_graphiti_impl import GraphitiClient

    captured = {}

    class FakeEpisode:
        uuid = "episode-light-1"

    class FakeResult:
        episode = FakeEpisode()

    class FakeGraphiti:
        async def add_episode(self, **kwargs):
            captured.update(kwargs)
            return FakeResult()

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_USE_PREVIOUS_EPISODE_CONTEXT", False)
    monkeypatch.setattr(
        zep_graphiti_impl,
        "_run_async",
        lambda coro: asyncio.run(coro),
    )

    client = GraphitiClient("bolt://unused", "neo4j", "password")
    client._initialized = True
    client._graphiti = FakeGraphiti()

    episode_uuid = client.add_episode("graph_1", "测试文本", episode_type="text")

    assert episode_uuid == "episode-light-1"
    assert captured["previous_episode_uuids"] == []
    assert captured["episode_body"] == "测试文本"


def test_graphiti_add_episode_batch_uses_light_single_episode_path(monkeypatch):
    from app.services.zep_graphiti_impl import GraphitiClient

    calls = []

    def fake_add_episode(graph_id, data, episode_type="text", reference_time=None):
        calls.append({
            "graph_id": graph_id,
            "data": data,
            "episode_type": episode_type,
            "reference_time": reference_time,
        })
        return "episode-light-1"

    client = GraphitiClient("bolt://unused", "neo4j", "password")
    client._initialized = True
    client._ensure_initialized = lambda: None
    client.add_episode = fake_add_episode

    episode_uuids = client.add_episode_batch(
        "graph_1",
        [{"data": "单块文本", "type": "text", "reference_time": "2026-06-03T00:00:00Z"}],
    )

    assert episode_uuids == ["episode-light-1"]
    assert calls[0]["reference_time"].isoformat() == "2026-06-03T00:00:00+00:00"
    assert calls == [
        {
            "graph_id": "graph_1",
            "data": "单块文本",
            "episode_type": "text",
            "reference_time": calls[0]["reference_time"],
        }
    ]


def test_graphiti_add_episode_batch_disables_bulk_by_default(monkeypatch):
    from app.services.zep_graphiti_impl import GraphitiClient

    calls = []

    def fake_add_episode(graph_id, data, episode_type="text", reference_time=None):
        calls.append({
            "graph_id": graph_id,
            "data": data,
            "episode_type": episode_type,
            "reference_time": reference_time,
        })
        return f"episode-light-{len(calls)}"

    class FakeGraphiti:
        async def add_episode_bulk(self, **kwargs):
            raise AssertionError("默认禁用 bulk 时不应调用 add_episode_bulk")

    monkeypatch.setattr("app.services.zep_graphiti_impl.Config.GRAPHITI_USE_BULK_INGEST", False)

    client = GraphitiClient("bolt://unused", "neo4j", "password")
    client._initialized = True
    client._ensure_initialized = lambda: None
    client._graphiti = FakeGraphiti()
    client.add_episode = fake_add_episode

    episode_uuids = client.add_episode_batch(
        "graph_1",
        [
            {"data": "第一块文本", "type": "text", "reference_time": "2026-06-03T00:00:00Z"},
            {"data": "第二块文本", "type": "text", "reference_time": "2026-06-03T00:01:00Z"},
        ],
    )

    assert episode_uuids == ["episode-light-1", "episode-light-2"]
    assert [call["data"] for call in calls] == ["第一块文本", "第二块文本"]
    assert calls[0]["reference_time"].isoformat() == "2026-06-03T00:00:00+00:00"
    assert calls[1]["reference_time"].isoformat() == "2026-06-03T00:01:00+00:00"


def test_graph_builder_default_batch_size_uses_config(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = FakeClient()
    builder._backend = "cloud"

    chunks = [f"chunk-{index}" for index in range(Config.GRAPH_BUILD_BATCH_SIZE + 1)]
    episode_uuids = builder.add_text_batches("graph_1", chunks)

    assert [len(batch[1]) for batch in builder.client.batches] == [Config.GRAPH_BUILD_BATCH_SIZE, 1]
    assert len(episode_uuids) == len(chunks)


def test_graph_builder_graphiti_caps_batch_size_for_stable_ingest(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_USE_BULK_INGEST", False)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_EPISODE_BATCH_SIZE", 3)

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = OrderedFakeClient()
    builder._backend = "graphiti"

    episode_uuids = builder.add_text_batches(
        "graph_1",
        ["chunk-0", "chunk-1", "chunk-2"],
        batch_size=3,
        concurrency=1,
    )

    assert [len(batch[1]) for batch in builder.client.batches] == [1, 1, 1]
    assert episode_uuids == ["chunk-0", "chunk-1", "chunk-2"]


def test_graph_builder_graphiti_allows_bulk_when_enabled(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_USE_BULK_INGEST", True)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_EPISODE_BATCH_SIZE", 3)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_INGEST_CONCURRENCY", 1)

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = OrderedFakeClient()
    builder._backend = "graphiti"

    episode_uuids = builder.add_text_batches(
        "graph_1",
        ["chunk-0", "chunk-1", "chunk-2"],
        batch_size=3,
        concurrency=1,
    )

    assert [len(batch[1]) for batch in builder.client.batches] == [3]
    assert episode_uuids == ["chunk-0"]


def test_graph_builder_graphiti_caps_ingest_concurrency(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_USE_BULK_INGEST", False)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_EPISODE_BATCH_SIZE", 1)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_INGEST_CONCURRENCY", 2)
    captured_workers = []

    class InlineFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExecutor:
        def __init__(self, max_workers):
            captured_workers.append(max_workers)
            self.futures = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args):
            future = InlineFuture(fn(*args))
            self.futures.append(future)
            return future

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = OrderedFakeClient()
    builder._backend = "graphiti"

    monkeypatch.setattr("app.services.graph_builder.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(
        "app.services.graph_builder.as_completed",
        lambda futures: reversed(list(futures)),
    )

    episode_uuids = builder.add_text_batches(
        "graph_1",
        ["chunk-0", "chunk-1", "chunk-2", "chunk-3"],
        batch_size=1,
        concurrency=4,
    )

    assert captured_workers == [2]
    assert episode_uuids == ["chunk-0", "chunk-1", "chunk-2", "chunk-3"]


def test_graph_builder_routes_parallel_batches_by_llm_endpoint_pool(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_USE_BULK_INGEST", False)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_EPISODE_BATCH_SIZE", 1)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_INGEST_CONCURRENCY", 6)

    class Endpoint:
        def __init__(self, route_name, model, is_boost=False):
            self.route_name = route_name
            self.model = model
            self.is_boost = is_boost

    base_endpoint = Endpoint("base", "base-model")
    boost_endpoint = Endpoint("boost", "boost-model", is_boost=True)

    class WeightedPool:
        routes = [
            type("Route", (), {"route_name": "base", "endpoint": base_endpoint, "weight": 1})(),
            type("Route", (), {"route_name": "boost", "endpoint": boost_endpoint, "weight": 2})(),
        ]

        def __init__(self):
            self.sequence = [base_endpoint, boost_endpoint, boost_endpoint]

        def select_endpoint(self, batch_index):
            return self.sequence[batch_index % len(self.sequence)]

    class MainClient:
        def set_ontology_from_cache(self, graph_id, source_client):
            raise AssertionError("主 client 不应作为 worker 复制本体")

    created_endpoints = []
    worker_batches = []

    class WorkerClient:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def set_ontology_from_cache(self, graph_id, source_client):
            self.graph_id = graph_id

        def add_episode_batch(self, graph_id, episodes):
            worker_batches.append((self.endpoint.route_name, self.endpoint.model, episodes[0]["data"]))
            return [episodes[0]["data"]]

        def close(self):
            pass

    def fake_create_zep_client(**kwargs):
        created_endpoints.append(kwargs["llm_endpoint"])
        return WorkerClient(kwargs["llm_endpoint"])

    class InlineFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args):
            return InlineFuture(fn(*args))

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = MainClient()
    builder._backend = "graphiti"
    builder._llm_endpoint_pool = WeightedPool()

    monkeypatch.setattr("app.services.graph_builder.create_zep_client", fake_create_zep_client)
    monkeypatch.setattr("app.services.graph_builder.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("app.services.graph_builder.as_completed", lambda futures: list(futures))

    episode_uuids = builder.add_text_batches(
        "graph_1",
        [f"chunk-{index}" for index in range(6)],
        batch_size=1,
        concurrency=6,
    )

    assert [endpoint.route_name for endpoint in created_endpoints] == [
        "base",
        "boost",
        "boost",
        "base",
        "boost",
        "boost",
    ]
    assert [batch[0] for batch in worker_batches] == [
        "base",
        "boost",
        "boost",
        "base",
        "boost",
        "boost",
    ]
    assert episode_uuids == [f"chunk-{index}" for index in range(6)]


def test_graph_builder_supports_llm_endpoint_pool_endpoint_for_index(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_USE_BULK_INGEST", False)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_EPISODE_BATCH_SIZE", 1)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_INGEST_CONCURRENCY", 2)

    class Endpoint:
        def __init__(self, route_name, model, is_boost=False):
            self.route_name = route_name
            self.model = model
            self.is_boost = is_boost

    base_endpoint = Endpoint("base", "base-model")
    boost_endpoint = Endpoint("boost", "boost-model", is_boost=True)

    class EndpointForIndexPool:
        endpoints = (base_endpoint, boost_endpoint)
        weights = {"base": 1, "boost": 1}
        dual_enabled = True

        def endpoint_for_index(self, batch_index):
            return self.endpoints[batch_index % len(self.endpoints)]

    class MainClient:
        def set_ontology_from_cache(self, graph_id, source_client):
            raise AssertionError("主 client 不应作为 worker 复制本体")

    created_routes = []

    class WorkerClient:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def set_ontology_from_cache(self, graph_id, source_client):
            pass

        def add_episode_batch(self, graph_id, episodes):
            created_routes.append((self.endpoint.route_name, self.endpoint.model))
            return [episodes[0]["data"]]

        def close(self):
            pass

    def fake_create_zep_client(**kwargs):
        endpoint = kwargs["llm_endpoint"]
        assert endpoint is not None
        assert hasattr(endpoint, "model")
        return WorkerClient(endpoint)

    class InlineFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExecutor:
        def __init__(self, max_workers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args):
            return InlineFuture(fn(*args))

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = MainClient()
    builder._backend = "graphiti"
    builder._llm_endpoint_pool = EndpointForIndexPool()

    monkeypatch.setattr("app.services.graph_builder.create_zep_client", fake_create_zep_client)
    monkeypatch.setattr("app.services.graph_builder.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("app.services.graph_builder.as_completed", lambda futures: list(futures))

    episode_uuids = builder.add_text_batches(
        "graph_1",
        ["chunk-0", "chunk-1"],
        batch_size=1,
        concurrency=2,
    )

    assert episode_uuids == ["chunk-0", "chunk-1"]
    assert created_routes == [("base", "base-model"), ("boost", "boost-model")]
    assert builder.get_llm_observability()["llm_route_counts"] == {"base": 1, "boost": 1}


def test_graph_builder_does_not_submit_new_batches_after_parallel_failure(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_USE_BULK_INGEST", False)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_EPISODE_BATCH_SIZE", 1)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_INGEST_CONCURRENCY", 2)

    submitted_batches = []

    class FailingClient:
        def set_ontology_from_cache(self, graph_id, source_client):
            pass

        def add_episode_batch(self, graph_id, episodes):
            batch_name = episodes[0]["data"]
            submitted_batches.append(batch_name)
            if batch_name == "chunk-1":
                raise RuntimeError("模拟批次失败")
            return [batch_name]

        def close(self):
            pass

    def fake_create_zep_client(**kwargs):
        return FailingClient()

    class InlineFuture:
        def __init__(self, fn, args):
            self.fn = fn
            self.args = args
            self._done = False
            self._result = None
            self._exc = None

        def run_once(self):
            if self._done:
                return
            try:
                self._result = self.fn(*self.args)
            except Exception as exc:
                self._exc = exc
            self._done = True

        def result(self):
            self.run_once()
            if self._exc:
                raise self._exc
            return self._result

        def cancel(self):
            self._done = True
            return True

    class FakeExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args):
            return InlineFuture(fn, args)

    def fake_as_completed(futures):
        futures = list(futures)
        for future in futures:
            future.run_once()
            yield future

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = type("MainClient", (), {"set_ontology_from_cache": lambda self, graph_id, source_client: None})()
    builder._backend = "graphiti"
    builder._llm_endpoint_pool = None
    builder._llm_endpoint = None

    monkeypatch.setattr("app.services.graph_builder.create_zep_client", fake_create_zep_client)
    monkeypatch.setattr("app.services.graph_builder.ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr("app.services.graph_builder.as_completed", fake_as_completed)

    try:
        builder.add_text_batches(
            "graph_1",
            ["chunk-0", "chunk-1", "chunk-2", "chunk-3"],
            batch_size=1,
            concurrency=2,
        )
    except RuntimeError as exc:
        assert "模拟批次失败" in str(exc)
    else:
        raise AssertionError("并发批次失败时应抛出异常")

    assert submitted_batches == ["chunk-0", "chunk-1"]


def test_graph_builder_retries_failed_batch_with_alternate_llm_route(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_USE_BULK_INGEST", False)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_EPISODE_BATCH_SIZE", 1)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPHITI_INGEST_CONCURRENCY", 2)
    monkeypatch.setattr("app.services.graph_builder.Config.GRAPH_BUILD_LLM_ROUTE_RETRY_ENABLED", True)

    class Endpoint:
        def __init__(self, route_name, model, is_boost=False):
            self.route_name = route_name
            self.model = model
            self.is_boost = is_boost

    base_endpoint = Endpoint("base", "base-model")
    boost_endpoint = Endpoint("boost", "boost-model", is_boost=True)

    class EndpointPool:
        endpoints = (base_endpoint, boost_endpoint)
        weights = {"base": 1, "boost": 1}
        dual_enabled = True

        def endpoint_for_index(self, batch_index):
            return base_endpoint if batch_index == 0 else boost_endpoint

    attempts = []

    class WorkerClient:
        def __init__(self, endpoint):
            self.endpoint = endpoint

        def set_ontology_from_cache(self, graph_id, source_client):
            pass

        def add_episode_batch(self, graph_id, episodes):
            attempts.append(self.endpoint.route_name)
            if self.endpoint.route_name == "base":
                raise TimeoutError("base 路由超时")
            return ["episode-boost"]

        def close(self):
            pass

    def fake_create_zep_client(**kwargs):
        return WorkerClient(kwargs["llm_endpoint"])

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = type("MainClient", (), {"set_ontology_from_cache": lambda self, graph_id, source_client: None})()
    builder._backend = "graphiti"
    builder._llm_endpoint_pool = EndpointPool()
    builder._llm_endpoint = base_endpoint

    monkeypatch.setattr("app.services.graph_builder.create_zep_client", fake_create_zep_client)

    episode_uuids = builder.add_text_batches(
        "graph_1",
        ["chunk-0", "chunk-1"],
        batch_size=1,
        concurrency=2,
    )

    assert episode_uuids == ["episode-boost", "episode-boost"]
    assert attempts.count("base") == 1
    assert attempts.count("boost") == 2
    assert builder.get_llm_observability()["llm_route_counts"] == {"boost": 2}


def test_graph_builder_falls_back_to_single_endpoint_pool_when_route_pool_missing(monkeypatch):
    class Endpoint:
        model = "boost-model"
        is_boost = True

    monkeypatch.delattr(
        "app.services.graph_builder.llm_routing.get_graph_build_llm_endpoint_pool",
        raising=False,
    )

    pool = GraphBuilderService._build_llm_endpoint_pool(Endpoint())

    assert GraphBuilderService._select_llm_endpoint_from_pool(pool, 0).model == "boost-model"
    assert GraphBuilderService._select_llm_endpoint_from_pool(pool, 5).model == "boost-model"
    assert GraphBuilderService._describe_llm_endpoint_pool(pool) == {
        "dual_llm_enabled": False,
        "llm_routes": ["boost"],
        "llm_route_weights": {},
    }


def test_graph_builder_formats_quota_error_message(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)

    class QuotaClient:
        def add_episode_batch(self, graph_id, episodes):
            upstream = Exception("Error code: 429 - insufficient_quota: You exceeded your current quota")
            raise Exception("Rate limit exceeded. Please try again later.") from upstream

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = QuotaClient()
    builder._backend = "graphiti"

    try:
        builder.add_text_batches("graph_1", ["chunk-0"], batch_size=1)
    except RuntimeError as exc:
        assert "LLM 服务额度不足" in str(exc)
    else:
        raise AssertionError("应抛出批次写入失败异常")


def test_graph_builder_keeps_raw_chunk_without_extraction_context(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = FakeClient()

    builder.add_text_batches("graph_1", ["原始文本块"], batch_size=1)

    assert builder.client.batches[0][1][0]["data"] == "原始文本块"


def test_graph_builder_wraps_chunks_with_event_relevance_constraints(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = FakeClient()

    builder.add_text_batches(
        "graph_1",
        ["张雪驾驶820RR-RS参加相关赛事讨论。网易游戏广告出现在页面侧栏。"],
        batch_size=1,
        extraction_context={
            "event_topic": "张雪机车事件",
            "simulation_requirement": "推演赛事争议后续舆情走向",
            "seed_summary": "材料围绕张雪、张雪机车、法国车手瓦伦丁·德比斯、WSBK展开。",
            "entity_hints": ["张雪", "张雪机车", "法国车手瓦伦丁·德比斯", "WSBK", "820RR-RS"],
        },
    )

    wrapped = builder.client.batches[0][1][0]["data"]
    assert "图谱实体抽取约束" in wrapped
    assert "事件主题：张雪机车事件" in wrapped
    assert "推演方向：推演赛事争议后续舆情走向" in wrapped
    assert "张雪、张雪机车、法国车手瓦伦丁·德比斯、WSBK、820RR-RS" in wrapped
    assert "实体数量目标下限为50+" in wrapped
    assert "实体类型完全开放" in wrapped
    assert "仅作为背景噪音或广告推荐出现的无关实体可以忽略" in wrapped
    assert "# 文档文本块（唯一事实来源）" in wrapped
    assert "网易游戏广告出现在页面侧栏" in wrapped


def test_graph_build_worker_runs_complete_flow_with_parallel_batches(monkeypatch):
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)

    class FlowClient:
        def __init__(self):
            self.graph_created = False
            self.ontology_set = False
            self.episodes = []

        def create_graph(self, graph_id, name, description):
            self.graph_created = True

        def set_ontology(self, graph_ids, entities=None, edges=None):
            self.ontology_set = True

        def add_episode_batch(self, graph_id, episodes):
            self.episodes.extend(episodes)
            return [f"episode-{len(self.episodes)}-{index}" for index, _ in enumerate(episodes)]

        def get_all_nodes(self, graph_id):
            from app.services.zep_adapter import GraphNode

            return [
                GraphNode(
                    uuid="node-1",
                    name="张雪",
                    labels=["Entity", "Person"],
                    summary="测试节点",
                    attributes={},
                )
            ]

        def get_all_edges(self, graph_id):
            from app.services.zep_adapter import GraphEdge

            return [
                GraphEdge(
                    uuid="edge-1",
                    name="RELATED_TO",
                    fact="张雪与测试事件相关",
                    source_node_uuid="node-1",
                    target_node_uuid="node-1",
                    attributes={},
                )
            ]

    task_updates = []

    class FakeTaskManager:
        def update_task(self, task_id, **kwargs):
            task_updates.append(("update", kwargs))

        def complete_task(self, task_id, result):
            task_updates.append(("complete", result))

        def fail_task(self, task_id, error):
            task_updates.append(("fail", error))

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder._backend = "graphiti"
    builder.client = FlowClient()
    builder.task_manager = FakeTaskManager()

    builder._build_graph_worker(
        task_id="task-1",
        text="张雪参加赛事讨论。\n\n公众关注事件走向。",
        ontology={"entity_types": [{"name": "Person", "attributes": []}], "edge_types": []},
        graph_name="测试图谱",
        chunk_size=10,
        chunk_overlap=0,
        batch_size=1,
        concurrency=2,
        extraction_context={"event_topic": "张雪机车事件"},
    )

    complete_events = [payload for action, payload in task_updates if action == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["graph_info"]["node_count"] == 1
    assert complete_events[0]["graph_info"]["edge_count"] == 1
    assert builder.client.graph_created is True
    assert builder.client.ontology_set is True
    assert len(builder.client.episodes) >= 2
    assert "图谱实体抽取约束" in builder.client.episodes[0]["data"]


def test_graph_data_coalesces_duplicate_graphiti_entities_by_name_and_type():
    from app.services.zep_adapter import GraphEdge, GraphNode

    class DuplicateEntityClient:
        def get_all_nodes(self, graph_id):
            return [
                GraphNode(
                    uuid="person-a",
                    name="许国利",
                    labels=["Entity", "嫌疑人"],
                    summary="案件相关人员",
                    attributes={"来源": "batch-1"},
                    created_at="2021-01-02",
                ),
                GraphNode(
                    uuid="person-b",
                    name=" 许国利 ",
                    labels=["Entity", "嫌疑人"],
                    summary="杭州市民，案件核心嫌疑人",
                    attributes={"来源": "batch-2", "年龄": "55"},
                    created_at="2021-01-01",
                ),
                GraphNode(
                    uuid="victim-a",
                    name="来惠利",
                    labels=["Entity", "受害人"],
                    summary="案件受害人",
                    attributes={},
                    created_at="2021-01-03",
                ),
            ]

        def get_all_edges(self, graph_id):
            return [
                GraphEdge(
                    uuid="edge-a",
                    name="家庭关系",
                    fact="许国利与来惠利为夫妻关系",
                    source_node_uuid="person-a",
                    target_node_uuid="victim-a",
                    attributes={},
                ),
                GraphEdge(
                    uuid="edge-b",
                    name="家庭关系",
                    fact="许国利与来惠利为夫妻关系",
                    source_node_uuid="person-b",
                    target_node_uuid="victim-a",
                    attributes={},
                ),
                GraphEdge(
                    uuid="edge-c",
                    name="起诉",
                    fact="检方起诉许国利",
                    source_node_uuid="victim-a",
                    target_node_uuid="person-b",
                    attributes={},
                ),
            ]

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = DuplicateEntityClient()

    graph_data = builder.get_graph_data("graph-1")

    assert graph_data["node_count"] == 2
    assert graph_data["edge_count"] == 2

    merged_person = next(node for node in graph_data["nodes"] if node["name"] == "许国利")
    assert merged_person["summary"] == "杭州市民，案件核心嫌疑人"
    assert merged_person["created_at"] == "2021-01-01"
    assert merged_person["attributes"]["年龄"] == "55"
    assert merged_person["attributes"]["merged_duplicate_uuids"] == ["person-a", "person-b"]

    person_edges = [
        edge for edge in graph_data["edges"]
        if edge["source_node_uuid"] == merged_person["uuid"] or edge["target_node_uuid"] == merged_person["uuid"]
    ]
    assert len(person_edges) == 2
    assert {edge["source_node_name"] for edge in person_edges} | {edge["target_node_name"] for edge in person_edges} == {"许国利", "来惠利"}


def test_graph_data_filters_location_entities_and_edges():
    from app.services.zep_adapter import GraphEdge, GraphNode

    class LocationEntityClient:
        def get_all_nodes(self, graph_id):
            return [
                GraphNode("person-1", "张雪", ["Entity", "Person"], "", {}),
                GraphNode("org-1", "杭州市公安局", ["Entity", "GovernmentAgency"], "", {}),
                GraphNode("city-1", "杭州市", ["Entity", "City"], "", {}),
                GraphNode("place-1", "某小区", ["Entity", "Place"], "", {}),
            ]

        def get_all_edges(self, graph_id):
            return [
                GraphEdge("edge-1", "回应", "张雪回应争议", "person-1", "org-1", {}),
                GraphEdge("edge-2", "位于", "事件发生于杭州市", "person-1", "city-1", {}),
                GraphEdge("edge-3", "位于", "某小区位于杭州市", "place-1", "city-1", {}),
            ]

    builder = GraphBuilderService.__new__(GraphBuilderService)
    builder.client = LocationEntityClient()

    graph_data = builder.get_graph_data("graph-1")

    assert graph_data["node_count"] == 2
    assert graph_data["edge_count"] == 1
    assert {node["name"] for node in graph_data["nodes"]} == {"张雪", "杭州市公安局"}
    assert graph_data["edges"][0]["uuid"] == "edge-1"


def test_entity_reader_uses_coalesced_graph_entities():
    from app.services.zep_adapter import GraphEdge, GraphNode
    from app.services.zep_entity_reader import ZepEntityReader

    class DuplicateEntityClient:
        def get_all_nodes(self, graph_id):
            return [
                GraphNode("person-a", "许国利", ["Entity", "嫌疑人"], "", {}),
                GraphNode("person-b", "许国利", ["Entity", "嫌疑人"], "", {}),
                GraphNode("victim-a", "来惠利", ["Entity", "受害人"], "", {}),
            ]

        def get_all_edges(self, graph_id):
            return [
                GraphEdge("edge-a", "家庭关系", "许国利与来惠利为夫妻关系", "person-a", "victim-a", {}),
                GraphEdge("edge-b", "家庭关系", "许国利与来惠利为夫妻关系", "person-b", "victim-a", {}),
            ]

    reader = ZepEntityReader.__new__(ZepEntityReader)
    reader.client = DuplicateEntityClient()

    result = reader.filter_defined_entities("graph-1")

    assert result.total_count == 2
    assert result.filtered_count == 2
    assert [entity.name for entity in result.entities] == ["许国利", "来惠利"]
    assert len(result.entities[0].related_edges) == 1


def test_entity_reader_filters_location_entities_from_agent_candidates():
    from app.services.zep_adapter import GraphEdge, GraphNode
    from app.services.zep_entity_reader import ZepEntityReader

    class LocationEntityClient:
        def get_all_nodes(self, graph_id):
            return [
                GraphNode("person-1", "张雪", ["Entity", "Person"], "", {}),
                GraphNode("city-1", "杭州市", ["Entity", "City"], "", {}),
                GraphNode("location-1", "比赛场地", ["Entity", "Location"], "", {}),
            ]

        def get_all_edges(self, graph_id):
            return [
                GraphEdge("edge-1", "提及", "张雪提及杭州市", "person-1", "city-1", {}),
                GraphEdge("edge-2", "讨论", "张雪讨论赛事", "person-1", "person-1", {}),
            ]

    reader = ZepEntityReader.__new__(ZepEntityReader)
    reader.client = LocationEntityClient()

    result = reader.filter_defined_entities("graph-1")

    assert result.total_count == 1
    assert result.filtered_count == 1
    assert [entity.name for entity in result.entities] == ["张雪"]
    assert result.entities[0].related_edges == [
        {
            "direction": "outgoing",
            "edge_name": "讨论",
            "fact": "张雪讨论赛事",
            "target_node_uuid": "person-1",
        }
    ]


def test_zep_tools_statistics_use_coalesced_graph_entities():
    from app.services.zep_adapter import GraphEdge, GraphNode
    from app.services.zep_tools import ZepToolsService

    class DuplicateEntityClient:
        def get_all_nodes(self, graph_id):
            return [
                GraphNode("person-a", "许国利", ["Entity", "嫌疑人"], "", {}),
                GraphNode("person-b", "许国利", ["Entity", "嫌疑人"], "", {}),
                GraphNode("victim-a", "来惠利", ["Entity", "受害人"], "", {}),
            ]

        def get_all_edges(self, graph_id):
            return [
                GraphEdge("edge-a", "家庭关系", "许国利与来惠利为夫妻关系", "person-a", "victim-a", {}),
                GraphEdge("edge-b", "家庭关系", "许国利与来惠利为夫妻关系", "person-b", "victim-a", {}),
            ]

    tools = ZepToolsService.__new__(ZepToolsService)
    tools.client = DuplicateEntityClient()

    stats = tools.get_graph_statistics("graph-1")

    assert stats["total_nodes"] == 2
    assert stats["total_edges"] == 1
    assert stats["entity_types"] == {"嫌疑人": 1, "受害人": 1}


def test_zep_tools_statistics_filter_location_entities():
    from app.services.zep_adapter import GraphEdge, GraphNode
    from app.services.zep_tools import ZepToolsService

    class LocationEntityClient:
        def get_all_nodes(self, graph_id):
            return [
                GraphNode("person-1", "张雪", ["Entity", "Person"], "", {}),
                GraphNode("city-1", "杭州市", ["Entity", "City"], "", {}),
            ]

        def get_all_edges(self, graph_id):
            return [
                GraphEdge("edge-1", "位于", "事件发生于杭州市", "person-1", "city-1", {}),
            ]

    tools = ZepToolsService.__new__(ZepToolsService)
    tools.client = LocationEntityClient()

    stats = tools.get_graph_statistics("graph-1")

    assert stats["total_nodes"] == 1
    assert stats["total_edges"] == 0
    assert stats["entity_types"] == {"Person": 1}
    assert stats["relation_types"] == {}


def test_ontology_processing_strips_location_entity_types_and_source_targets():
    from app.services.ontology_generator import OntologyGenerator

    generator = OntologyGenerator.__new__(OntologyGenerator)
    ontology = generator._validate_and_process({
        "entity_types": [
            {"name": "Person", "description": "person", "attributes": []},
            {"name": "City", "description": "city", "attributes": []},
            {"name": "Place", "description": "place", "attributes": []},
            {"name": "GovernmentAgency", "description": "agency", "attributes": []},
        ],
        "edge_types": [
            {
                "name": "LOCATED_IN",
                "description": "location relation",
                "source_targets": [{"source": "Person", "target": "City"}],
            },
            {
                "name": "REGULATES",
                "description": "agency relation",
                "source_targets": [{"source": "GovernmentAgency", "target": "Person"}],
            },
        ],
        "analysis_summary": "测试",
    })

    entity_names = [entity["name"] for entity in ontology["entity_types"]]
    edge_names = [edge["name"] for edge in ontology["edge_types"]]

    assert "City" not in entity_names
    assert "Place" not in entity_names
    assert "Person" in entity_names
    assert "Organization" in entity_names
    assert "GovernmentAgency" in entity_names
    assert edge_names == ["REGULATES"]


def test_ontology_processing_does_not_truncate_entity_types_to_ten():
    from app.services.ontology_generator import OntologyGenerator

    generator = OntologyGenerator.__new__(OntologyGenerator)
    entities = [
        {"name": name, "description": name, "attributes": [], "examples": []}
        for name in [
            "GovernmentAgency",
            "RegulatoryAgency",
            "Company",
            "Brand",
            "MediaOutlet",
            "SocialMediaPlatform",
            "Association",
            "Influencer",
            "OnlineCommunity",
            "PublicGroup",
            "Victim",
            "Suspect",
            "Netizen",
            "Person",
            "Organization",
        ]
    ]

    ontology = generator._validate_and_process({
        "entity_types": entities,
        "edge_types": [],
        "analysis_summary": "测试",
    })

    entity_names = [entity["name"] for entity in ontology["entity_types"]]

    assert len(entity_names) == 15
    assert "Netizen" in entity_names
    assert "Person" in entity_names
    assert "Organization" in entity_names


def test_location_filter_keeps_speaking_actor_types_with_location_words():
    from app.services.location_entity_filter import is_location_entity_type, is_location_entity_node
    from app.services.zep_adapter import GraphNode

    assert is_location_entity_type("CityResident") is False
    assert is_location_entity_node(GraphNode("group-1", "杭州市民", ["Entity", "CityResident"], "", {})) is False
    assert is_location_entity_node(GraphNode("agency-1", "杭州市公安局", ["Entity", "GovernmentAgency"], "", {})) is False
    assert is_location_entity_node(GraphNode("city-1", "杭州市", ["Entity", "City"], "", {})) is True


def test_location_filter_blocks_place_names_even_when_mislabeled_as_person_or_org():
    from app.services.location_entity_filter import is_location_entity_node
    from app.services.zep_adapter import GraphNode

    assert is_location_entity_node(
        GraphNode(
            "community-1",
            "三堡北苑",
            ["Entity", "Person"],
            "三堡北苑是杭州市江干区的住宅小区，是案件相关地点。",
            {},
        )
    ) is True
    assert is_location_entity_node(
        GraphNode(
            "mall-1",
            "庆春银泰",
            ["Entity", "Organization"],
            "银泰百货庆春店位于杭州市庆春路与延安路交叉口，是杭州核心商圈重要商业体。",
            {"org_type": "企业/品牌"},
        )
    ) is True
    assert is_location_entity_node(
        GraphNode(
            "media-1",
            "浙江日报",
            ["Entity", "MediaOutlet"],
            "浙江日报是地方权威媒体，报道杭州公共事件。",
            {},
        )
    ) is False


def test_location_filter_keeps_core_person_entities_with_place_context():
    from app.services.location_entity_filter import is_location_entity_node
    from app.services.zep_adapter import GraphNode

    assert is_location_entity_node(
        GraphNode(
            "victim-1",
            "来惠利",
            ["Entity", "受害人"],
            "来惠利是案件受害人，生前居住在三堡北苑小区。",
            {},
        )
    ) is False
    assert is_location_entity_node(
        GraphNode(
            "suspect-1",
            "许国利",
            ["Entity", "嫌疑人"],
            "许国利是案件核心嫌疑人，案发地点涉及杭州市江干区。",
            {},
        )
    ) is False
    assert is_location_entity_node(
        GraphNode(
            "person-1",
            "来女士",
            ["Entity", "Person"],
            "来女士是案件当事人，相关材料提到其居住地和小区。",
            {},
        )
    ) is False


def test_entity_reader_retypes_media_platforms_and_filters_place_agents():
    from app.services.zep_adapter import GraphEdge, GraphNode
    from app.services.zep_entity_reader import ZepEntityReader

    class MixedEntityClient:
        def get_all_nodes(self, graph_id):
            return [
                GraphNode("platform-1", "小红书", ["Entity", "Person"], "小红书平台出现相关讨论。", {}),
                GraphNode("platform-2", "抖音", ["Entity"], "抖音短视频平台传播相关内容。", {}),
                GraphNode("community-1", "三堡北苑", ["Entity", "Person"], "三堡北苑是案件相关小区。", {}),
                GraphNode("person-1", "来惠利", ["Entity", "Person"], "来惠利是案件当事人。", {}),
            ]

        def get_all_edges(self, graph_id):
            return [
                GraphEdge("edge-1", "传播", "小红书传播相关讨论", "platform-1", "person-1", {}),
                GraphEdge("edge-2", "位于", "来惠利居住在三堡北苑", "person-1", "community-1", {}),
            ]

    reader = ZepEntityReader.__new__(ZepEntityReader)
    reader.client = MixedEntityClient()

    result = reader.filter_defined_entities("graph-1")

    assert [entity.name for entity in result.entities] == ["小红书", "抖音", "来惠利"]
    assert result.entity_types == {"SocialMediaPlatform", "Person"}
    assert result.entities[0].get_entity_type() == "SocialMediaPlatform"
    assert result.entities[1].get_entity_type() == "SocialMediaPlatform"

    person_only = reader.filter_defined_entities("graph-1", defined_entity_types=["Person"])
    assert [entity.name for entity in person_only.entities] == ["来惠利"]


def test_graph_build_api_passes_project_event_context_to_episodes(monkeypatch, tmp_path):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.Config.ZEP_BACKEND", "graphiti")
    monkeypatch.setattr("app.api.graph.Config.GRAPHITI_INGEST_CONCURRENCY", 1)
    monkeypatch.setattr("app.api.graph.Config.GRAPHITI_EPISODE_BATCH_SIZE", 1)
    monkeypatch.setattr("app.api.graph.Config.GRAPH_ENTITY_ENRICHMENT_ENABLED", False)
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)

    captured = {}

    class FakeBuilder:
        resolve_batch_plan = staticmethod(GraphBuilderService.resolve_batch_plan)

        def __init__(self, api_key=None, backend=None, build_mode=False):
            self.backend = backend
            self.build_mode = build_mode

        def create_graph(self, name):
            return "mirofish_test_graph"

        def set_ontology(self, graph_id, ontology):
            captured["ontology"] = ontology

        def add_text_batches(self, graph_id, chunks, batch_size, progress_callback=None, extraction_context=None, concurrency=1):
            captured["graph_id"] = graph_id
            captured["chunks"] = chunks
            captured["batch_size"] = batch_size
            captured["concurrency"] = concurrency
            captured["extraction_context"] = extraction_context
            wrapped = GraphBuilderService._wrap_chunk_with_event_constraints(
                chunks[0],
                extraction_context,
            )
            captured["wrapped_episode"] = wrapped
            return ["episode_1"]

        def _wait_for_episodes(self, episode_uuids, progress_callback=None):
            captured["episode_uuids"] = episode_uuids

        def get_graph_data(self, graph_id):
            return {"node_count": 1, "edge_count": 1}

    class InlineThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    project = ProjectManager.create_project(name="张雪机车事件")
    project.status = ProjectStatus.ONTOLOGY_GENERATED
    project.search_query = "张雪机车事件"
    project.simulation_requirement = "推演赛事争议后续舆情走向"
    project.seed_summary_md = "材料围绕张雪、张雪机车、法国车手瓦伦丁·德比斯、WSBK展开。"
    project.entity_hints = ["张雪", "张雪机车", "法国车手瓦伦丁·德比斯", "WSBK", "820RR-RS"]
    project.ontology = {
        "entity_types": [{"name": "Person", "description": "person", "attributes": []}],
        "edge_types": [],
    }
    ProjectManager.save_project(project)
    ProjectManager.save_extracted_text(
        project.project_id,
        "张雪驾驶820RR-RS参加相关赛事讨论。网易游戏广告出现在页面侧栏。",
    )

    monkeypatch.setattr("app.api.graph.GraphBuilderService", FakeBuilder)
    monkeypatch.setattr("app.api.graph.threading.Thread", InlineThread)

    app = create_app()
    response = app.test_client().post(
        "/api/graph/build",
        json={"project_id": project.project_id, "batch_size": 1, "chunk_size": 200, "concurrency": 2},
    )

    assert response.status_code == 200
    assert captured["extraction_context"]["event_topic"] == "张雪机车事件"
    assert captured["extraction_context"]["simulation_requirement"] == "推演赛事争议后续舆情走向"
    assert captured["batch_size"] == 1
    assert captured["concurrency"] == 1
    assert "法国车手瓦伦丁·德比斯" in captured["extraction_context"]["entity_hints"]
    assert "实体数量目标下限为50+" in captured["wrapped_episode"]
    assert "实体类型完全开放" in captured["wrapped_episode"]
    assert "张雪驾驶820RR-RS参加相关赛事讨论" in captured["wrapped_episode"]


def test_graph_build_api_enriches_event_entities_when_below_target(monkeypatch, tmp_path):
    from app.services.bocha_search_service import SearchSource

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.Config.ZEP_BACKEND", "graphiti")
    monkeypatch.setattr("app.api.graph.Config.GRAPHITI_INGEST_CONCURRENCY", 1)
    monkeypatch.setattr("app.api.graph.Config.GRAPHITI_EPISODE_BATCH_SIZE", 1)
    monkeypatch.setattr("app.api.graph.Config.GRAPH_MIN_ENTITY_TARGET", 50)
    monkeypatch.setattr("app.api.graph.Config.GRAPH_ENTITY_ENRICHMENT_ENABLED", True)
    monkeypatch.setattr("app.api.graph.Config.GRAPH_ENTITY_ENRICHMENT_QUERY_LIMIT", 2)
    monkeypatch.setattr("app.api.graph.Config.GRAPH_ENTITY_ENRICHMENT_SEARCH_COUNT", 2)
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)

    captured = {"add_calls": []}

    class FakeSearchService:
        def search(self, query, count=None, freshness=None, summary=True):
            captured.setdefault("queries", []).append(query)
            return [
                SearchSource(
                    title="小女孩呕吐槽事件后续",
                    url=f"https://example.com/{len(captured['queries'])}",
                    snippet="小女孩、家属、武汉地铁、网友、媒体机构、小米YU7车主参与讨论。",
                    summary="材料补充了平台、媒体、当事人家属、监管和公众群体等相关实体。",
                    site_name="示例媒体",
                )
            ]

    class FakeBuilder:
        resolve_batch_plan = staticmethod(GraphBuilderService.resolve_batch_plan)

        def __init__(self, api_key=None, backend=None, build_mode=False):
            self.graph_reads = 0

        def create_graph(self, name):
            return "mirofish_enriched_graph"

        def set_ontology(self, graph_id, ontology):
            pass

        def add_text_batches(self, graph_id, chunks, batch_size, progress_callback=None, extraction_context=None, concurrency=1):
            captured["add_calls"].append({
                "chunks": chunks,
                "extraction_context": extraction_context,
            })
            return [f"episode_{len(captured['add_calls'])}"]

        def _wait_for_episodes(self, episode_uuids, progress_callback=None):
            pass

        def get_graph_data(self, graph_id):
            self.graph_reads += 1
            if self.graph_reads == 1:
                return {"node_count": 33, "edge_count": 93}
            return {"node_count": 120, "edge_count": 220}

    class InlineThread:
        def __init__(self, target, daemon=False):
            self.target = target

        def start(self):
            self.target()

    project = ProjectManager.create_project(name="小女孩呕吐槽视频事件")
    project.status = ProjectStatus.ONTOLOGY_GENERATED
    project.search_query = "小女孩呕吐槽视频二次传播"
    project.simulation_requirement = "追踪地方性偶发片段演变为全国性舆论符号"
    project.seed_summary_md = "材料围绕小女孩、家属、地铁、媒体和小米YU7车主讨论展开。"
    project.entity_hints = ["小女孩", "家属", "武汉地铁", "小米YU7车主"]
    project.ontology = {
        "entity_types": [{"name": "Person", "description": "person", "attributes": []}],
        "edge_types": [],
    }
    ProjectManager.save_project(project)
    ProjectManager.save_extracted_text(project.project_id, "原始文档只包含少量实体。")

    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.get_provider_name", lambda provider=None: "bailian")
    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.create", lambda provider=None: FakeSearchService())
    monkeypatch.setattr("app.api.graph.GraphBuilderService", FakeBuilder)
    monkeypatch.setattr("app.api.graph.threading.Thread", InlineThread)

    app = create_app()
    response = app.test_client().post(
        "/api/graph/build",
        json={"project_id": project.project_id, "batch_size": 1, "chunk_size": 300, "concurrency": 1},
    )

    assert response.status_code == 200
    task = TaskManager().get_task(response.get_json()["data"]["task_id"])
    enrichment = task.result["entity_enrichment"]
    assert enrichment["target_node_count"] == 50
    assert enrichment["initial_node_count"] == 33
    assert enrichment["final_node_count"] == 120
    assert enrichment["performed"] is True
    assert enrichment["source_count"] == 2
    assert len(captured["add_calls"]) == 2
    assert "事件相关联网补充材料" in captured["add_calls"][1]["chunks"][0]
    assert "小女孩呕吐槽事件后续" in captured["add_calls"][1]["chunks"][0]
    assert "武汉地铁" in captured["add_calls"][1]["extraction_context"]["entity_hints"]
    assert task.progress_detail["entity_enrichment"]["final_node_count"] == 120


def test_graph_build_api_fails_when_enrichment_still_below_target(monkeypatch, tmp_path):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.Config.ZEP_BACKEND", "graphiti")
    monkeypatch.setattr("app.api.graph.Config.GRAPHITI_INGEST_CONCURRENCY", 1)
    monkeypatch.setattr("app.api.graph.Config.GRAPHITI_EPISODE_BATCH_SIZE", 1)
    monkeypatch.setattr("app.api.graph.Config.GRAPH_MIN_ENTITY_TARGET", 50)
    monkeypatch.setattr("app.api.graph.Config.GRAPH_ENTITY_ENRICHMENT_ENABLED", True)
    monkeypatch.setattr("app.api.graph.Config.GRAPH_ENTITY_ENRICHMENT_QUERY_LIMIT", 1)
    monkeypatch.setattr("app.services.graph_builder.time.sleep", lambda seconds: None)

    class EmptySearchService:
        def search(self, query, count=None, freshness=None, summary=True):
            return []

    class FakeBuilder:
        resolve_batch_plan = staticmethod(GraphBuilderService.resolve_batch_plan)

        def __init__(self, api_key=None, backend=None, build_mode=False):
            pass

        def create_graph(self, name):
            return "mirofish_under_target_graph"

        def set_ontology(self, graph_id, ontology):
            pass

        def add_text_batches(self, graph_id, chunks, batch_size, progress_callback=None, extraction_context=None, concurrency=1):
            return ["episode_1"]

        def _wait_for_episodes(self, episode_uuids, progress_callback=None):
            pass

        def get_graph_data(self, graph_id):
            return {"node_count": 33, "edge_count": 93}

    class InlineThread:
        def __init__(self, target, daemon=False):
            self.target = target

        def start(self):
            self.target()

    project = ProjectManager.create_project(name="低实体数测试")
    project.status = ProjectStatus.ONTOLOGY_GENERATED
    project.search_query = "低实体数测试事件"
    project.simulation_requirement = "补充事件相关实体"
    project.ontology = {
        "entity_types": [{"name": "Person", "description": "person", "attributes": []}],
        "edge_types": [],
    }
    ProjectManager.save_project(project)
    ProjectManager.save_extracted_text(project.project_id, "原始文档只包含少量实体。")

    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.get_provider_name", lambda provider=None: "bailian")
    monkeypatch.setattr("app.api.graph.WebSearchProviderFactory.create", lambda provider=None: EmptySearchService())
    monkeypatch.setattr("app.api.graph.GraphBuilderService", FakeBuilder)
    monkeypatch.setattr("app.api.graph.threading.Thread", InlineThread)

    app = create_app()
    response = app.test_client().post(
        "/api/graph/build",
        json={"project_id": project.project_id, "batch_size": 1, "chunk_size": 300, "concurrency": 1},
    )

    assert response.status_code == 200
    task = TaskManager().get_task(response.get_json()["data"]["task_id"])
    saved_project = ProjectManager.get_project(project.project_id)
    assert task.status == "failed"
    assert saved_project.status == ProjectStatus.FAILED
    assert "图谱实体数量未达到最低要求" in saved_project.error
    assert task.progress_detail["entity_enrichment"]["initial_node_count"] == 33
    assert task.progress_detail["entity_enrichment"]["source_count"] == 0


def test_graph_extraction_constraints_keep_core_people_and_media_platforms():
    wrapped = GraphBuilderService._wrap_chunk_with_event_constraints(
        "被告人许国利被指控杀害受害人来惠利，小红书和微博出现相关讨论。",
        {
            "event_topic": "杭州杀妻案",
            "simulation_requirement": "推演公众对冷静寻妻和化粪池藏尸强烈反差的情绪演变路径",
            "entity_hints": ["许国利", "来惠利", "小红书", "微博"],
            "seed_summary": "事件围绕被告人许国利、受害人来惠利及媒体报道展开。",
        },
    )

    assert "核心人物必须优先抽取" in wrapped
    assert "受害人/被害人、嫌疑人/犯罪嫌疑人、被告人、当事人" in wrapped
    assert "实体数量目标下限为50+" in wrapped
    assert "实体类型完全开放" in wrapped
    assert "图谱实体不等于最终人设 Agent" in wrapped
    assert "优先使用文本中出现的全名作为实体名称" in wrapped
    assert "必须保留为 MediaPlatform/SocialMediaPlatform/Media" in wrapped
    assert "被告人许国利被指控杀害受害人来惠利" in wrapped


def test_graph_build_api_records_requested_and_effective_batch_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.Config.ZEP_BACKEND", "graphiti")
    monkeypatch.setattr("app.api.graph.Config.GRAPHITI_EPISODE_BATCH_SIZE", 1)
    monkeypatch.setattr("app.api.graph.Config.GRAPHITI_INGEST_CONCURRENCY", 1)
    monkeypatch.setattr("app.api.graph.Config.GRAPHITI_USE_BULK_INGEST", False)
    monkeypatch.setattr("app.api.graph.Config.GRAPH_ENTITY_ENRICHMENT_ENABLED", False)

    class InlineBuilder:
        resolve_batch_plan = staticmethod(GraphBuilderService.resolve_batch_plan)

        def __init__(self, api_key=None, backend=None, build_mode=False):
            self.llm_route_counts = {"base": 0, "boost": 0}

        def get_llm_observability(self):
            return {
                "dual_llm_enabled": True,
                "llm_routes": ["base", "boost"],
                "llm_route_weights": {"base": 1, "boost": 2},
                "llm_route_counts": dict(self.llm_route_counts),
            }

        def create_graph(self, name):
            return "mirofish_test_graph"

        def set_ontology(self, graph_id, ontology):
            pass

        def add_text_batches(self, *args, **kwargs):
            self.llm_route_counts = {"base": 1, "boost": 2}
            return ["episode_1"]

        def _wait_for_episodes(self, episode_uuids, progress_callback=None):
            pass

        def get_graph_data(self, graph_id):
            return {"node_count": 1, "edge_count": 1}

    class InlineThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    project = ProjectManager.create_project(name="批次计划测试")
    project.status = ProjectStatus.ONTOLOGY_GENERATED
    project.ontology = {
        "entity_types": [{"name": "Person", "description": "person", "attributes": []}],
        "edge_types": [],
    }
    ProjectManager.save_project(project)
    ProjectManager.save_extracted_text(project.project_id, "用于测试图谱批次计划的文本。")

    monkeypatch.setattr("app.api.graph.GraphBuilderService", InlineBuilder)
    monkeypatch.setattr("app.api.graph.threading.Thread", InlineThread)

    app = create_app()
    response = app.test_client().post(
        "/api/graph/build",
        json={"project_id": project.project_id, "batch_size": 3, "chunk_size": 200, "concurrency": 4},
    )

    assert response.status_code == 200
    task_id = response.get_json()["data"]["task_id"]
    task = TaskManager().get_task(task_id)
    assert task.result["batch_size"] == 1
    assert task.result["concurrency"] == 1
    assert task.result["bulk_ingest_enabled"] is False
    assert task.result["requested_batch_size"] == 3
    assert task.result["requested_concurrency"] == 4
    assert task.result["dual_llm_enabled"] is True
    assert task.result["llm_routes"] == ["base", "boost"]
    assert task.result["llm_route_weights"] == {"base": 1, "boost": 2}
    assert task.result["llm_route_counts"] == {"base": 1, "boost": 2}
    assert task.progress_detail["batch_size"] == 1
    assert task.progress_detail["concurrency"] == 1
    assert task.progress_detail["bulk_ingest_enabled"] is False
    assert task.progress_detail["graph_id"] == "mirofish_test_graph"
    assert task.progress_detail["total_chunks"] == 1
    assert task.progress_detail["total_batches"] == 1
    assert task.progress_detail["dual_llm_enabled"] is True
    assert task.progress_detail["llm_routes"] == ["base", "boost"]
    assert task.progress_detail["llm_route_weights"] == {"base": 1, "boost": 2}
    assert task.progress_detail["llm_route_counts"] == {"base": 1, "boost": 2}


def test_graph_build_api_records_single_llm_fallback_observability(monkeypatch, tmp_path):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.Config.ZEP_BACKEND", "cloud")
    monkeypatch.setattr("app.api.graph.Config.ZEP_API_KEY", "test-zep-key")
    monkeypatch.setattr("app.api.graph.Config.GRAPH_ENTITY_ENRICHMENT_ENABLED", False)

    class InlineBuilder:
        resolve_batch_plan = staticmethod(GraphBuilderService.resolve_batch_plan)

        def __init__(self, api_key=None, backend=None, build_mode=False):
            pass

        def create_graph(self, name):
            return "mirofish_cloud_graph"

        def set_ontology(self, graph_id, ontology):
            pass

        def add_text_batches(self, *args, **kwargs):
            return ["episode_1"]

        def _wait_for_episodes(self, episode_uuids, progress_callback=None):
            pass

        def get_graph_data(self, graph_id):
            return {"node_count": 1, "edge_count": 1}

    class InlineThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    project = ProjectManager.create_project(name="单模型回退测试")
    project.status = ProjectStatus.ONTOLOGY_GENERATED
    project.ontology = {
        "entity_types": [{"name": "Person", "description": "person", "attributes": []}],
        "edge_types": [],
    }
    ProjectManager.save_project(project)
    ProjectManager.save_extracted_text(project.project_id, "用于测试 Cloud 后端双模型观测降级的文本。")

    monkeypatch.setattr("app.api.graph.GraphBuilderService", InlineBuilder)
    monkeypatch.setattr("app.api.graph.threading.Thread", InlineThread)

    app = create_app()
    response = app.test_client().post(
        "/api/graph/build",
        json={"project_id": project.project_id, "batch_size": 1, "chunk_size": 200},
    )

    assert response.status_code == 200
    task_id = response.get_json()["data"]["task_id"]
    task = TaskManager().get_task(task_id)
    assert task.result["dual_llm_enabled"] is False
    assert task.result["llm_routes"] == []
    assert task.result["llm_route_weights"] == {}
    assert task.result["llm_route_counts"] == {}
    assert task.progress_detail["dual_llm_enabled"] is False
    assert task.progress_detail["llm_routes"] == []
    assert task.progress_detail["llm_route_weights"] == {}
    assert task.progress_detail["llm_route_counts"] == {}


def test_graph_build_api_persists_graph_id_for_build_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr("app.api.graph.Config.ZEP_BACKEND", "graphiti")
    monkeypatch.setattr("app.api.graph.Config.GRAPH_ENTITY_ENRICHMENT_ENABLED", False)

    class FailingBuilder:
        resolve_batch_plan = staticmethod(GraphBuilderService.resolve_batch_plan)

        def __init__(self, api_key=None, backend=None, build_mode=False):
            pass

        def create_graph(self, name):
            return "mirofish_partial_graph"

        def set_ontology(self, graph_id, ontology):
            pass

        def add_text_batches(self, *args, **kwargs):
            raise RuntimeError("批次 1 图谱写入失败: Rate limit exceeded. Please try again later.")

    class InlineThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    project = ProjectManager.create_project(name="限流测试")
    project.status = ProjectStatus.ONTOLOGY_GENERATED
    project.ontology = {
        "entity_types": [{"name": "Person", "description": "person", "attributes": []}],
        "edge_types": [],
    }
    ProjectManager.save_project(project)
    ProjectManager.save_extracted_text(project.project_id, "用于测试图谱构建失败的文本。")

    monkeypatch.setattr("app.api.graph.GraphBuilderService", FailingBuilder)
    monkeypatch.setattr("app.api.graph.threading.Thread", InlineThread)

    app = create_app()
    response = app.test_client().post(
        "/api/graph/build",
        json={"project_id": project.project_id, "batch_size": 1, "chunk_size": 200},
    )

    assert response.status_code == 200
    saved_project = ProjectManager.get_project(project.project_id)
    assert saved_project.status == ProjectStatus.FAILED
    assert saved_project.graph_id == "mirofish_partial_graph"
    assert saved_project.graph_backend == "graphiti"
    assert "Rate limit exceeded" in saved_project.error
    task_id = response.get_json()["data"]["task_id"]
    task = TaskManager().get_task(task_id)
    assert task.progress_detail["graph_id"] == "mirofish_partial_graph"
