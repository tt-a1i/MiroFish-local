from app.config import Config
from app.utils.llm_routing import LLMEndpoint, get_graph_build_llm_endpoint_pool, get_preferred_llm_endpoint


def test_preferred_llm_uses_boost_when_complete(monkeypatch):
    monkeypatch.setattr(Config, "LLM_API_KEY", "default-key")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "https://default.example/v1")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "default-model")
    monkeypatch.setattr(Config, "LLM_BOOST_API_KEY", "boost-key")
    monkeypatch.setattr(Config, "LLM_BOOST_BASE_URL", "https://boost.example/v1")
    monkeypatch.setattr(Config, "LLM_BOOST_MODEL_NAME", "boost-model")

    endpoint = get_preferred_llm_endpoint(prefer_boost=True)

    assert endpoint.api_key == "boost-key"
    assert endpoint.base_url == "https://boost.example/v1"
    assert endpoint.model == "boost-model"
    assert endpoint.is_boost is True
    assert endpoint.route_name == "boost"


def test_preferred_llm_falls_back_when_boost_incomplete(monkeypatch):
    monkeypatch.setattr(Config, "LLM_API_KEY", "default-key")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "https://default.example/v1")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "default-model")
    monkeypatch.setattr(Config, "LLM_BOOST_API_KEY", "boost-key")
    monkeypatch.setattr(Config, "LLM_BOOST_BASE_URL", "")
    monkeypatch.setattr(Config, "LLM_BOOST_MODEL_NAME", "boost-model")

    endpoint = get_preferred_llm_endpoint(prefer_boost=True)

    assert endpoint.api_key == "default-key"
    assert endpoint.base_url == "https://default.example/v1"
    assert endpoint.model == "default-model"
    assert endpoint.is_boost is False
    assert endpoint.route_name == "base"


def test_preferred_llm_falls_back_when_boost_key_is_placeholder(monkeypatch):
    monkeypatch.setattr(Config, "LLM_API_KEY", "default-key")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "https://default.example/v1")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "default-model")
    monkeypatch.setattr(Config, "LLM_BOOST_API_KEY", "your_boost_api_key_here")
    monkeypatch.setattr(Config, "LLM_BOOST_BASE_URL", "https://boost.example/v1")
    monkeypatch.setattr(Config, "LLM_BOOST_MODEL_NAME", "boost-model")

    endpoint = get_preferred_llm_endpoint(prefer_boost=True)

    assert endpoint.api_key == "default-key"
    assert endpoint.model == "default-model"
    assert endpoint.is_boost is False


def _patch_base_and_boost(monkeypatch):
    monkeypatch.setattr(Config, "LLM_API_KEY", "default-key")
    monkeypatch.setattr(Config, "LLM_BASE_URL", "https://default.example/v1")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "default-model")
    monkeypatch.setattr(Config, "LLM_BOOST_API_KEY", "boost-key")
    monkeypatch.setattr(Config, "LLM_BOOST_BASE_URL", "https://boost.example/v1")
    monkeypatch.setattr(Config, "LLM_BOOST_MODEL_NAME", "boost-model")
    monkeypatch.setattr(Config, "ZEP_BACKEND", "graphiti")
    monkeypatch.setattr(Config, "GRAPH_BUILD_DUAL_LLM_ENABLED", True)
    monkeypatch.setattr(Config, "GRAPH_BUILD_LLM_BASE_WEIGHT", 1)
    monkeypatch.setattr(Config, "GRAPH_BUILD_LLM_BOOST_WEIGHT", 1)


def test_graph_build_pool_uses_base_and_boost_when_enabled(monkeypatch):
    _patch_base_and_boost(monkeypatch)

    pool = get_graph_build_llm_endpoint_pool(build_mode=True)

    assert pool.dual_enabled is True
    assert pool.route_names == ("base", "boost")
    assert pool.weights == {"base": 1, "boost": 1}
    assert [endpoint.route_name for endpoint in pool.expanded_endpoints] == ["base", "boost"]
    assert pool.endpoint_for_index(0).route_name == "base"
    assert pool.endpoint_for_index(1).route_name == "boost"
    assert pool.endpoint_for_index(2).route_name == "base"


def test_graph_build_pool_falls_back_to_preferred_when_boost_missing(monkeypatch):
    _patch_base_and_boost(monkeypatch)
    monkeypatch.setattr(Config, "LLM_BOOST_BASE_URL", "")

    pool = get_graph_build_llm_endpoint_pool(build_mode=True)

    assert pool.dual_enabled is False
    assert pool.route_names == ("base",)
    assert pool.endpoint_for_index(0).api_key == "default-key"


def test_graph_build_pool_falls_back_when_boost_key_is_placeholder(monkeypatch):
    _patch_base_and_boost(monkeypatch)
    monkeypatch.setattr(Config, "LLM_BOOST_API_KEY", "your_boost_api_key_here")

    pool = get_graph_build_llm_endpoint_pool(build_mode=True)

    assert pool.dual_enabled is False
    assert pool.route_names == ("base",)
    assert pool.endpoint_for_index(0).model == "default-model"


def test_graph_build_pool_falls_back_to_preferred_when_dual_disabled(monkeypatch):
    _patch_base_and_boost(monkeypatch)
    monkeypatch.setattr(Config, "GRAPH_BUILD_DUAL_LLM_ENABLED", False)

    pool = get_graph_build_llm_endpoint_pool(build_mode=True)

    assert pool.dual_enabled is False
    assert pool.route_names == ("boost",)
    assert pool.endpoint_for_index(0).api_key == "boost-key"


def test_graph_build_pool_falls_back_to_preferred_when_not_graphiti_build(monkeypatch):
    _patch_base_and_boost(monkeypatch)
    monkeypatch.setattr(Config, "ZEP_BACKEND", "cloud")

    cloud_pool = get_graph_build_llm_endpoint_pool(build_mode=True)
    non_build_pool = get_graph_build_llm_endpoint_pool(build_mode=False)

    assert cloud_pool.dual_enabled is False
    assert cloud_pool.route_names == ("boost",)
    assert non_build_pool.dual_enabled is False
    assert non_build_pool.route_names == ("boost",)


def test_graph_build_pool_does_not_require_base_when_dual_cannot_enable(monkeypatch):
    _patch_base_and_boost(monkeypatch)
    monkeypatch.setattr(Config, "LLM_API_KEY", "")

    pool = get_graph_build_llm_endpoint_pool(build_mode=True)

    assert pool.dual_enabled is False
    assert pool.route_names == ("boost",)
    assert pool.endpoint_for_index(0).api_key == "boost-key"


def test_graph_build_pool_clamps_and_expands_weights(monkeypatch):
    _patch_base_and_boost(monkeypatch)
    monkeypatch.setattr(Config, "GRAPH_BUILD_LLM_BASE_WEIGHT", 0)
    monkeypatch.setattr(Config, "GRAPH_BUILD_LLM_BOOST_WEIGHT", 99)

    pool = get_graph_build_llm_endpoint_pool(build_mode=True)

    assert pool.weights == {"base": 1, "boost": 8}
    assert [endpoint.route_name for endpoint in pool.expanded_endpoints] == [
        "base",
        "boost",
        "boost",
        "boost",
        "boost",
        "boost",
        "boost",
        "boost",
        "boost",
    ]


def test_llm_endpoint_route_name_keeps_legacy_constructor_compatible():
    endpoint = LLMEndpoint(
        api_key="boost-key",
        base_url="https://boost.example/v1",
        model="boost-model",
        is_boost=True,
    )

    assert endpoint.route_name == "boost"
