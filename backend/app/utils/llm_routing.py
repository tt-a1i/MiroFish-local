"""
LLM 路由工具。

统一管理默认 LLM 与加速 LLM 的选择，避免各业务模块重复散落环境变量判断。
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from openai import OpenAI

from ..config import Config


@dataclass(frozen=True)
class LLMEndpoint:
    """OpenAI-compatible LLM 端点配置。"""

    api_key: str
    base_url: str
    model: str
    is_boost: bool = False
    route_name: Optional[str] = None

    def __post_init__(self):
        """兼容旧调用：未显式传 route_name 时按 is_boost 推导。"""
        if self.route_name is None:
            object.__setattr__(self, "route_name", "boost" if self.is_boost else "base")


@dataclass(frozen=True)
class LLMEndpointPool:
    """图谱构建 LLM 端点池，支持按权重确定性轮询。"""

    endpoints: Tuple[LLMEndpoint, ...]
    weights: Dict[str, int]
    expanded_endpoints: Tuple[LLMEndpoint, ...]
    dual_enabled: bool = False

    def endpoint_for_index(self, index: int) -> LLMEndpoint:
        """按序号返回本次应使用的端点。"""
        if not self.expanded_endpoints:
            raise ValueError("LLM endpoint pool 为空")
        return self.expanded_endpoints[index % len(self.expanded_endpoints)]

    @property
    def route_names(self) -> Tuple[str, ...]:
        """返回端点池中的路由名称。"""
        return tuple(endpoint.route_name for endpoint in self.endpoints)


def get_default_llm_endpoint() -> LLMEndpoint:
    """返回默认 LLM 端点。"""
    if not Config.LLM_API_KEY:
        raise ValueError("LLM_API_KEY 未配置")
    return LLMEndpoint(
        api_key=Config.LLM_API_KEY,
        base_url=Config.LLM_BASE_URL,
        model=Config.LLM_MODEL_NAME,
        is_boost=False,
        route_name="base",
    )


def get_base_llm_endpoint() -> LLMEndpoint:
    """返回图谱构建默认 LLM 端点，语义上显式标记为 base。"""
    return get_default_llm_endpoint()


def get_boost_llm_endpoint() -> Optional[LLMEndpoint]:
    """返回加速 LLM 端点；未完整配置时返回 None。"""
    if not all([Config.LLM_BOOST_API_KEY, Config.LLM_BOOST_BASE_URL, Config.LLM_BOOST_MODEL_NAME]):
        return None
    if _is_placeholder(Config.LLM_BOOST_API_KEY):
        return None
    return LLMEndpoint(
        api_key=Config.LLM_BOOST_API_KEY,
        base_url=Config.LLM_BOOST_BASE_URL,
        model=Config.LLM_BOOST_MODEL_NAME,
        is_boost=True,
        route_name="boost",
    )


def get_preferred_llm_endpoint(prefer_boost: bool = True) -> LLMEndpoint:
    """优先返回加速 LLM；未配置加速时回退默认 LLM。"""
    if prefer_boost:
        boost = get_boost_llm_endpoint()
        if boost:
            return boost
    return get_default_llm_endpoint()


def get_graph_build_llm_endpoint_pool(build_mode: bool = True) -> LLMEndpointPool:
    """
    返回 02 图谱构建使用的 LLM 端点池。

    只有 Graphiti build 模式、双模型开关开启、base 可用且 boost 完整时，才返回 base+boost。
    其他场景保持现有优先 boost 的单端点行为。
    """
    if (
        build_mode
        and str(Config.ZEP_BACKEND or "").strip().lower() == "graphiti"
        and bool(Config.GRAPH_BUILD_DUAL_LLM_ENABLED)
        and _has_base_llm_endpoint()
    ):
        base = get_base_llm_endpoint()
        boost = get_boost_llm_endpoint()
        if boost:
            weights = {
                "base": clamp_weight(Config.GRAPH_BUILD_LLM_BASE_WEIGHT),
                "boost": clamp_weight(Config.GRAPH_BUILD_LLM_BOOST_WEIGHT),
            }
            return _build_endpoint_pool((base, boost), weights, dual_enabled=True)

    preferred = get_preferred_llm_endpoint(prefer_boost=True)
    weights = {preferred.route_name: 1}
    return _build_endpoint_pool((preferred,), weights, dual_enabled=False)


def create_openai_client(endpoint: Optional[LLMEndpoint] = None, prefer_boost: bool = True) -> OpenAI:
    """根据端点创建 OpenAI-compatible 客户端。"""
    endpoint = endpoint or get_preferred_llm_endpoint(prefer_boost=prefer_boost)
    return OpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url)


def clamp_concurrency(value: Optional[int], default: int, minimum: int = 1, maximum: int = 16) -> int:
    """把并发数限制在安全范围内。"""
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def clamp_weight(value: Optional[int], default: int = 1, minimum: int = 1, maximum: int = 8) -> int:
    """把 LLM 分摊权重限制在安全范围内。"""
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _build_endpoint_pool(
    endpoints: Sequence[LLMEndpoint],
    weights: Dict[str, int],
    dual_enabled: bool,
) -> LLMEndpointPool:
    """根据端点和权重创建确定性轮询池。"""
    expanded: List[LLMEndpoint] = []
    for endpoint in endpoints:
        weight = clamp_weight(weights.get(endpoint.route_name, 1))
        expanded.extend([endpoint] * weight)
    return LLMEndpointPool(
        endpoints=tuple(endpoints),
        weights={endpoint.route_name: clamp_weight(weights.get(endpoint.route_name, 1)) for endpoint in endpoints},
        expanded_endpoints=tuple(expanded),
        dual_enabled=dual_enabled and len(endpoints) > 1,
    )


def _is_placeholder(value: str) -> bool:
    """识别示例配置中的占位符，避免误用无效加速 Key。"""
    normalized = str(value or "").strip().lower()
    return not normalized or normalized.startswith("your_") or normalized in {
        "your_boost_api_key",
        "your_boost_api_key_here",
        "your_api_key",
        "your_api_key_here",
    }


def _has_base_llm_endpoint() -> bool:
    """判断默认 LLM 是否可作为 base 路由参与双模型分摊。"""
    return bool(Config.LLM_API_KEY) and not _is_placeholder(Config.LLM_API_KEY)


@contextmanager
def temporary_graphiti_llm_env(prefer_boost: bool = True) -> Iterator[LLMEndpoint]:
    """
    临时切换 Graphiti 使用的 LLM 环境变量。

    Graphiti 的 OpenAIGenericClient 从 OPENAI_* / GRAPHITI_LLM_MODEL 读取配置。
    该上下文只在调用方创建独立 Graphiti client 前使用，并在退出时恢复原值。
    """
    endpoint = get_preferred_llm_endpoint(prefer_boost=prefer_boost)
    keys = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "GRAPHITI_LLM_MODEL")
    old_values = {key: os.environ.get(key) for key in keys}
    try:
        os.environ["OPENAI_API_KEY"] = endpoint.api_key
        os.environ["OPENAI_BASE_URL"] = endpoint.base_url
        os.environ["GRAPHITI_LLM_MODEL"] = endpoint.model
        yield endpoint
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
