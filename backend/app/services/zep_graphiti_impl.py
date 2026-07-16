"""
Graphiti 本地客户端实现

使用 graphiti-core + Neo4j 实现本地知识图谱服务。
替代 Zep Cloud，实现 ZepClientAdapter 接口。

MVP 范围：
- 图谱创建/删除（使用 group_id 隔离）
- Episode 添加（单条/批量）
- 节点/边检索
- 语义搜索

Ontology 会在应用层归一化后注入 Graphiti episode ingestion，用于自定义实体/边抽取。
"""

import asyncio
import inspect
import logging
import os
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any, ClassVar, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from ..config import Config
from .graphiti_llm_adapter import (
    ensure_graphiti_json_instruction,
    generate_response_without_response_format,
    get_response_model_arg,
    is_response_format_unsupported_error,
    is_retryable_llm_response_error,
    iter_exception_chain,
    normalize_graphiti_response_model_payload,
)
from .zep_adapter import (
    ZepClientAdapter,
    GraphNode,
    GraphEdge,
    SearchResult,
    EpisodeStatus,
)

logger = logging.getLogger('mirofish.graphiti_client')


def _is_rate_limit_error(exc: Exception) -> bool:
    """识别 OpenAI-compatible 服务的限流错误。"""
    for current in _iter_exception_chain(exc):
        status_code = getattr(current, "status_code", None)
        if status_code == 429:
            return True
        text = str(current).lower()
        if (
            "429" in text
            or ("rate" in text and "limit" in text)
            or "insufficient_quota" in text
            or "quota" in text
        ):
            return True
    return False


_iter_exception_chain = iter_exception_chain


def _is_quota_exhausted_error(exc: Exception) -> bool:
    """识别明确的额度耗尽错误，这类错误重试通常无效。"""
    text = " ".join(str(current).lower() for current in _iter_exception_chain(exc))
    return (
        "insufficient_quota" in text
        or "exceeded your current quota" in text
        or "check your plan and billing" in text
    )


def _is_fatal_error(exc: Exception) -> bool:
    """识别不可恢复的致命错误（进程关闭、事件循环死亡等），这类错误重试无效。"""
    for current in _iter_exception_chain(exc):
        text = str(current)
        # ThreadPoolExecutor 关闭后无法调度新任务
        if "cannot schedule new futures after shutdown" in text:
            return True
        # 事件循环已关闭
        if "event loop is closed" in text.lower():
            return True
        # asyncio 事件循环关闭
        if isinstance(current, RuntimeError) and "shutdown" in text.lower():
            return True
    return False


def _is_connection_error(exc: Exception) -> bool:
    """识别网络连接错误（可通过切换路由重试）。"""
    for current in _iter_exception_chain(exc):
        class_name = current.__class__.__name__
        if class_name in ("APIConnectionError", "ConnectionError", "ConnectTimeout"):
            return True
        text = str(current).lower()
        if "connection" in text and ("error" in text or "refused" in text or "reset" in text or "timeout" in text):
            return True
    return False


def _normalize_reference_time(reference_time: Optional[Any]) -> Optional[datetime]:
    """兼容 API 层传入的 datetime 或 ISO 时间字符串。"""
    if reference_time is None or isinstance(reference_time, datetime):
        return reference_time
    if isinstance(reference_time, str):
        value = reference_time.strip()
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = f"{value[:-1]}+00:00"
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            logger.warning("Graphiti reference_time 解析失败，使用当前时间: value=%s", reference_time)
            return None
    return None


# ============================================================================
# 单后台线程 + 专用事件循环（方案 A）
# ============================================================================
# 所有 Graphiti/Neo4j 异步操作都在这个专用线程的事件循环中执行
# Flask 线程通过 run_coroutine_threadsafe 提交任务并等待结果
# ============================================================================

_async_loop: Optional[asyncio.AbstractEventLoop] = None
_async_thread: Optional[threading.Thread] = None
_async_loop_ready = threading.Event()
_init_lock = threading.Lock()
_embedding_throttle_lock = asyncio.Lock()
_llm_throttle_lock = asyncio.Lock()
_last_embedding_request_at = 0.0
_last_llm_request_at = 0.0


def _start_async_loop():
    """在后台线程中启动事件循环"""
    global _async_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _async_loop = loop
    _async_loop_ready.set()
    logger.info("Graphiti 专用事件循环已启动")
    try:
        loop.run_forever()
    finally:
        if _async_loop is loop:
            _async_loop = None
            _async_loop_ready.clear()
        loop.close()


def _ensure_async_loop() -> asyncio.AbstractEventLoop:
    """确保后台事件循环已启动"""
    global _async_loop, _async_thread
    loop = _async_loop
    if _async_thread is not None and _async_thread.is_alive() and loop is not None and not loop.is_closed():
        return loop

    if _async_thread is None or not _async_thread.is_alive() or (loop is not None and loop.is_closed()):
        with _init_lock:
            loop = _async_loop
            if _async_thread is None or not _async_thread.is_alive() or (loop is not None and loop.is_closed()):
                _async_loop = None
                _async_loop_ready.clear()
                _async_thread = threading.Thread(
                    target=_start_async_loop,
                    daemon=True,
                    name="graphiti-async-loop"
                )
                _async_thread.start()

    # 并发启动时，其他线程可能已经创建了线程但事件循环尚未赋值。
    # 统一等待 ready 事件，避免 run_coroutine_threadsafe 拿到 None。
    if not _async_loop_ready.wait(timeout=10):
        raise RuntimeError("Graphiti 专用事件循环启动超时")

    loop = _async_loop
    if loop is None or loop.is_closed():
        raise RuntimeError("Graphiti 专用事件循环不可用")
    return loop


def _run_async(coro):
    """
    在同步上下文中运行异步协程

    使用专用后台线程的事件循环，通过 run_coroutine_threadsafe 提交任务。
    这样 Neo4j driver 始终绑定到同一个循环，避免跨循环问题。
    """
    loop = _ensure_async_loop()
    if loop.is_closed():
        raise RuntimeError(
            "Graphiti 异步事件循环已关闭，无法执行操作。"
            "可能原因：Flask 热重载触发了进程重启，或服务器正在关闭。"
            "请等待当前图谱构建完成后再修改代码文件，或重启服务后重试。"
        )
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    timeout = max(60, int(Config.GRAPHITI_OPERATION_TIMEOUT_SECONDS or 900))
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"Graphiti 异步操作超过 {timeout} 秒未返回") from exc
    except RuntimeError as exc:
        # 捕获 "cannot schedule new futures after shutdown" 等关闭错误，提供更清晰的提示
        if _is_fatal_error(exc):
            raise RuntimeError(
                "Graphiti 异步操作失败：后台事件循环或线程池已关闭。"
                "这通常是因为 Flask 热重载（检测到文件修改）或服务器关闭触发了进程重启。"
                "请等待当前图谱构建完成后再修改代码文件，或重启服务后重试。"
            ) from exc
        raise


async def _throttle_embedding_request(operation: str, item_count: int) -> None:
    """对 DashScope embedding 请求做全局轻量节流，减少 429 后长时间重试。"""
    min_interval = max(0.0, float(Config.GRAPHITI_EMBEDDING_MIN_INTERVAL_SECONDS or 0))
    if min_interval <= 0:
        return

    global _last_embedding_request_at
    async with _embedding_throttle_lock:
        now = time.monotonic()
        wait_seconds = min_interval - (now - _last_embedding_request_at)
        if wait_seconds > 0:
            logger.debug(
                "Graphiti %s embedding 节流等待 %.2f 秒: items=%s",
                operation,
                wait_seconds,
                item_count,
            )
            await asyncio.sleep(wait_seconds)
        _last_embedding_request_at = time.monotonic()


async def _throttle_llm_request(operation: str, item_count: int) -> None:
    """对 Graphiti LLM 抽取请求做全局节流，避免单批内部并发打爆额度。"""
    min_interval = max(0.0, float(Config.GRAPHITI_LLM_MIN_INTERVAL_SECONDS or 0))
    if min_interval <= 0:
        return

    global _last_llm_request_at
    async with _llm_throttle_lock:
        now = time.monotonic()
        wait_seconds = min_interval - (now - _last_llm_request_at)
        if wait_seconds > 0:
            logger.debug(
                "Graphiti %s LLM 节流等待 %.2f 秒: items=%s",
                operation,
                wait_seconds,
                item_count,
            )
            await asyncio.sleep(wait_seconds)
        _last_llm_request_at = time.monotonic()


def _summarize_graphiti_llm_request(args: tuple, kwargs: Dict[str, Any], llm_client: Any) -> Dict[str, Any]:
    """提取 Graphiti LLM 请求的安全诊断信息，不记录 prompt 正文。"""
    messages = kwargs.get("messages")
    if messages is None and args:
        messages = args[0]
    if messages is None:
        messages = []

    prompt_chars = 0
    try:
        prompt_chars = sum(len(getattr(message, "content", "") or "") for message in messages)
    except TypeError:
        prompt_chars = 0

    return {
        "prompt_name": kwargs.get("prompt_name") or "unknown",
        "model_size": getattr(kwargs.get("model_size"), "value", kwargs.get("model_size") or "medium"),
        "model": getattr(llm_client, "model", None),
        "small_model": getattr(llm_client, "small_model", None),
        "max_tokens": kwargs.get("max_tokens") or getattr(llm_client, "max_tokens", None),
        "prompt_chars": prompt_chars,
    }


async def _call_with_graphiti_rate_limit_retry(
    factory,
    operation: str,
    item_count: int,
    request_detail: Optional[Dict[str, Any]] = None,
):
    """统一处理 Graphiti 对 LLM/Embedding 的限流重试。"""
    max_retries = max(0, int(Config.GRAPHITI_RATE_LIMIT_MAX_RETRIES or 0))
    retry_seconds = max(0.0, float(Config.GRAPHITI_RATE_LIMIT_RETRY_SECONDS or 0))
    if operation.startswith("embedding."):
        request_timeout = max(0.0, float(Config.GRAPHITI_EMBEDDING_REQUEST_TIMEOUT_SECONDS or 0))
    elif operation.startswith("llm."):
        request_timeout = max(0.0, float(Config.GRAPHITI_LLM_REQUEST_TIMEOUT_SECONDS or 0))
    else:
        request_timeout = 0.0

    for attempt in range(max_retries + 1):
        try:
            if operation.startswith("embedding."):
                await _throttle_embedding_request(operation, item_count)
            elif operation.startswith("llm."):
                await _throttle_llm_request(operation, item_count)
            started_at = time.monotonic()
            if request_timeout > 0:
                result = await asyncio.wait_for(factory(), timeout=request_timeout)
            else:
                result = await factory()
            elapsed = time.monotonic() - started_at
            if elapsed >= 10:
                detail = request_detail or {}
                if operation.startswith("llm."):
                    logger.info(
                        "Graphiti %s 请求完成但耗时较长: prompt=%s, model=%s, model_size=%s, "
                        "prompt_chars=%s, max_tokens=%s, elapsed=%.1fs",
                        operation,
                        detail.get("prompt_name"),
                        detail.get("model"),
                        detail.get("model_size"),
                        detail.get("prompt_chars"),
                        detail.get("max_tokens"),
                        elapsed,
                    )
                else:
                    logger.info(
                        "Graphiti %s 请求完成但耗时较长: items=%s, elapsed=%.1fs",
                        operation,
                        item_count,
                        elapsed,
                    )
            return result
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Graphiti {operation} 单次请求超过 {request_timeout:g} 秒未返回"
            ) from exc
        except Exception as exc:
            # 致命错误（进程关闭、事件循环死亡等）不重试，直接抛出
            if _is_fatal_error(exc):
                logger.error(
                    "Graphiti %s 遇到不可恢复的致命错误（进程可能正在关闭），跳过重试: items=%s, error=%s",
                    operation,
                    item_count,
                    exc,
                )
                raise
            retryable_response_error = operation.startswith("llm.") and is_retryable_llm_response_error(exc)
            if (
                not (_is_rate_limit_error(exc) or retryable_response_error)
                or _is_quota_exhausted_error(exc)
                or attempt >= max_retries
            ):
                raise
            delay = retry_seconds * (attempt + 1)
            if retryable_response_error:
                logger.warning(
                    "Graphiti %s 返回不可解析 JSON，%.1f 秒后重试: items=%s, attempt=%s/%s, error=%s",
                    operation,
                    delay,
                    item_count,
                    attempt + 1,
                    max_retries,
                    exc,
                )
            else:
                logger.warning(
                    "Graphiti %s 触发限流，%.1f 秒后重试: items=%s, attempt=%s/%s, error=%s",
                    operation,
                    delay,
                    item_count,
                    attempt + 1,
                    max_retries,
                    exc,
                )
            await asyncio.sleep(delay)


def _create_graphiti_llm_rate_limit_wrapper(base_llm_client: Any) -> Any:
    """
    创建 Graphiti LLM client 包装器。

    这里动态继承 Graphiti 的 LLMClient，避免 GraphitiClients 的 Pydantic
    类型校验拒绝普通代理对象。
    """
    try:
        from graphiti_core.llm_client.client import LLMClient

        class _GraphitiRateLimitedLLMClient(LLMClient):
            """动态生成的 Graphiti LLMClient 子类。"""

            def __init__(self, llm_client: Any):
                self._llm_client = llm_client
                concurrency = max(1, int(Config.GRAPHITI_LLM_CONCURRENCY or 1))
                self._semaphore = asyncio.Semaphore(concurrency)

                for attr in ("config", "model", "small_model", "temperature", "max_tokens", "tracer"):
                    if hasattr(llm_client, attr):
                        setattr(self, attr, getattr(llm_client, attr))

            def __getattr__(self, name: str) -> Any:
                return getattr(self._llm_client, name)

            def set_tracer(self, tracer: Any) -> None:
                if hasattr(self._llm_client, "set_tracer"):
                    self._llm_client.set_tracer(tracer)
                self.tracer = tracer

            async def _generate_response(self, *args, **kwargs) -> Dict[str, Any]:
                return await self._llm_client._generate_response(*args, **kwargs)

            async def generate_response(self, *args, **kwargs) -> Dict[str, Any]:
                ensure_graphiti_json_instruction(args, kwargs)
                request_detail = _summarize_graphiti_llm_request(args, kwargs, self._llm_client)
                response_model = get_response_model_arg(args, kwargs)

                async def call_llm():
                    try:
                        payload = await self._llm_client.generate_response(*args, **kwargs)
                    except Exception as exc:
                        if not is_response_format_unsupported_error(exc):
                            raise
                        logger.warning(
                            "Graphiti LLM 端点不支持 response_format，改用提示词约束 JSON 后重试: model=%s, prompt=%s",
                            request_detail.get("model"),
                            request_detail.get("prompt_name"),
                        )
                        payload = await generate_response_without_response_format(
                            self._llm_client,
                            *args,
                            **kwargs,
                        )
                    return normalize_graphiti_response_model_payload(payload, response_model)

                async with self._semaphore:
                    return await _call_with_graphiti_rate_limit_retry(
                        call_llm,
                        operation="llm.generate_response",
                        item_count=1,
                        request_detail=request_detail,
                    )

        return _GraphitiRateLimitedLLMClient(base_llm_client)
    except ImportError:
        return base_llm_client


class GraphitiEmbeddingBatchWrapper:
    """
    Graphiti Embedder 包装器

    一些 OpenAI-compatible embedding 服务有批次大小或限流约束，
    graphiti-core 的 OpenAIEmbedder 会将所有输入一次性发送。
    此包装器统一做分块、节流和限流重试。

    注意：此类动态继承 EmbedderClient 以满足 Pydantic 类型检查。
    """

    def __init__(self, embedder: Any, max_batch_size: int = 10):
        self._embedder = embedder
        self.max_batch_size = max_batch_size
        # 复制原 embedder 的属性以保持兼容性
        if hasattr(embedder, 'config'):
            self.config = embedder.config

    async def create(self, input_data) -> list[float]:
        """单条 embedding 请求；遇到限流时退避重试。"""
        return await _call_with_graphiti_rate_limit_retry(
            lambda: self._embedder.create(input_data),
            operation="embedding.create",
            item_count=1,
        )

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        """批量 embedding 请求（分块处理）"""
        if len(input_data_list) <= self.max_batch_size:
            return await _call_with_graphiti_rate_limit_retry(
                lambda: self._embedder.create_batch(input_data_list),
                operation="embedding.create_batch",
                item_count=len(input_data_list),
            )

        # 分块处理
        results = []
        for i in range(0, len(input_data_list), self.max_batch_size):
            chunk = input_data_list[i : i + self.max_batch_size]
            chunk_results = await _call_with_graphiti_rate_limit_retry(
                lambda chunk=chunk: self._embedder.create_batch(chunk),
                operation="embedding.create_batch",
                item_count=len(chunk),
            )
            results.extend(chunk_results)
        return results


GRAPHITI_INTERNAL_PROPERTY_KEYS = {
    "uuid",
    "name",
    "summary",
    "created_at",
    "group_id",
    "fact",
    "valid_at",
    "invalid_at",
    "expired_at",
    "episodes",
    "name_embedding",
    "summary_embedding",
    "fact_embedding",
    "embedding",
    "embeddings",
}


def _create_graphiti_embedding_wrapper(base_embedder: Any, max_batch_size: int = 10) -> Any:
    """
    创建 Graphiti Embedder 包装器

    动态继承 EmbedderClient 以满足 graphiti-core 的 Pydantic 类型检查。
    """
    try:
        from graphiti_core.embedder.client import EmbedderClient

        class _GraphitiEmbeddingClient(EmbedderClient):
            """动态生成的 EmbedderClient 子类"""

            def __init__(self, embedder: Any, batch_size: int):
                self._embedder = embedder
                self.max_batch_size = max(1, int(batch_size or 1))
                if hasattr(embedder, 'config'):
                    self.config = embedder.config

            async def create(self, input_data) -> list[float]:
                return await _call_with_graphiti_rate_limit_retry(
                    lambda: self._embedder.create(input_data),
                    operation="embedding.create",
                    item_count=1,
                )

            async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
                if len(input_data_list) <= self.max_batch_size:
                    return await _call_with_graphiti_rate_limit_retry(
                        lambda: self._embedder.create_batch(input_data_list),
                        operation="embedding.create_batch",
                        item_count=len(input_data_list),
                    )

                results = []
                for i in range(0, len(input_data_list), self.max_batch_size):
                    chunk = input_data_list[i : i + self.max_batch_size]
                    chunk_results = await _call_with_graphiti_rate_limit_retry(
                        lambda chunk=chunk: self._embedder.create_batch(chunk),
                        operation="embedding.create_batch",
                        item_count=len(chunk),
                    )
                    results.extend(chunk_results)
                return results

        return _GraphitiEmbeddingClient(base_embedder, max_batch_size)

    except ImportError:
        # fallback: 返回普通包装器
        return GraphitiEmbeddingBatchWrapper(base_embedder, max_batch_size)


class GraphitiClient(ZepClientAdapter):
    """
    Graphiti 本地客户端实现

    使用 graphiti-core 库连接 Neo4j 图数据库。
    通过 group_id 参数实现多图谱隔离（对应 MiroFish 的 graph_id）。
    """

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        llm_client: Optional[Any] = None,
        embedder: Optional[Any] = None,
        use_singleton: bool = True,
        llm_endpoint: Optional[Any] = None,
    ):
        """
        初始化 Graphiti 客户端

        Args:
            neo4j_uri: Neo4j Bolt 连接 URI (如 bolt://localhost:7687)
            neo4j_user: Neo4j 用户名
            neo4j_password: Neo4j 密码
            llm_client: 可选的 LLM 客户端（用于实体抽取）
            embedder: 可选的 Embedder（用于语义搜索）
        """
        self.neo4j_uri = neo4j_uri
        self.neo4j_user = neo4j_user
        self.neo4j_password = neo4j_password
        self._llm_client = llm_client
        self._embedder = embedder
        self._use_singleton = use_singleton
        self._llm_endpoint = llm_endpoint

        # 延迟初始化 Graphiti 实例
        self._graphiti = None
        self._driver = None
        self._initialized = False

        # 记录创建的 graph_id（用于 group_id 映射）
        self._graph_metadata: Dict[str, Dict[str, Any]] = {}

        # 存储 ontology 定义，并在 episode ingestion/search 中作为类型约束使用
        self._ontology_cache: Dict[str, Dict[str, Any]] = {}
        self._instance_init_lock = threading.Lock()

    def _ensure_initialized(self):
        """确保 Graphiti 已初始化"""
        if self._initialized:
            return
        with self._instance_init_lock:
            if self._initialized:
                return
            try:
                from graphiti_core import Graphiti

                # 应用 Neo4j 属性 sanitization patch (Issue #683 workaround)
                from .graphiti_patch import apply_patch
                apply_patch()

                self._verify_neo4j_connectivity()

                llm_client = self._llm_client
                if llm_client is None:
                    llm_client = self._build_default_llm_client()
                llm_client = _create_graphiti_llm_rate_limit_wrapper(llm_client)

                embedder = self._embedder
                if embedder is None:
                    embedder = self._build_default_embedder()

                # 创建 Graphiti 实例。按当前 graphiti-core 版本支持的参数传入，
                # 避免因为 use_singleton 不兼容而 fallback 丢失 max_coroutines。
                max_coroutines = max(1, int(Config.GRAPHITI_LLM_CONCURRENCY or 1))
                graphiti_kwargs = {
                    "llm_client": llm_client,
                    "embedder": embedder,
                    "max_coroutines": max_coroutines,
                }
                supported_params = set(inspect.signature(Graphiti).parameters)
                if "use_singleton" in supported_params:
                    graphiti_kwargs["use_singleton"] = self._use_singleton
                if "max_coroutines" not in supported_params:
                    graphiti_kwargs.pop("max_coroutines", None)
                self._graphiti = Graphiti(
                    self.neo4j_uri,
                    self.neo4j_user,
                    self.neo4j_password,
                    **graphiti_kwargs,
                )

                # 初始化索引和约束
                _run_async(self._graphiti.build_indices_and_constraints())

                # 获取底层 Neo4j driver 用于直接查询
                self._driver = self._graphiti.driver

                self._initialized = True
                logger.info("Graphiti 客户端初始化完成")

            except ImportError as e:
                raise ImportError(
                    "graphiti-core 未安装。请运行: pip install graphiti-core"
                ) from e
            except Exception as e:
                logger.error(f"Graphiti 初始化失败: {e}")
                raise

    def _verify_neo4j_connectivity(self) -> None:
        """初始化 Graphiti 前先验证一次 Neo4j 凭证，避免索引并发初始化刷爆认证限流。"""
        from neo4j import GraphDatabase
        from ..utils.neo4j_errors import format_neo4j_auth_error, is_neo4j_auth_error

        driver = None
        try:
            driver = GraphDatabase.driver(
                self.neo4j_uri,
                auth=(self.neo4j_user, self.neo4j_password),
                connection_timeout=5,
            )
            driver.verify_connectivity()
        except Exception as exc:
            if is_neo4j_auth_error(exc):
                logger.error(
                    "Neo4j 连接预检失败: uri=%s, user=%s, error=%s",
                    self.neo4j_uri,
                    self.neo4j_user,
                    format_neo4j_auth_error(exc),
                )
            raise
        finally:
            if driver is not None:
                driver.close()

    def _build_default_llm_client(self) -> Any:
        """
        构建 Graphiti 默认 LLM client（OpenAI-compatible）

        Graphiti 默认会用 `gpt-4.1-mini`，对 DashScope 这类 OpenAI-compatible 服务通常不适用；
        这里优先使用：
        - GRAPHITI_LLM_MODEL（如有）
        - 否则使用 LLM_MODEL_NAME（与 MiroFish 现有配置保持一致）
        """
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        if self._llm_endpoint:
            api_key = self._llm_endpoint.api_key
            base_url = self._llm_endpoint.base_url
            model = self._llm_endpoint.model
        else:
            api_key = os.environ.get('OPENAI_API_KEY')
            base_url = os.environ.get('OPENAI_BASE_URL')
            model = os.environ.get('GRAPHITI_LLM_MODEL') or os.environ.get('LLM_MODEL_NAME')
        small_model = Config.GRAPHITI_LLM_SMALL_MODEL or None
        temperature = float(Config.GRAPHITI_LLM_TEMPERATURE or 0)
        max_tokens = max(1024, int(Config.GRAPHITI_LLM_MAX_TOKENS or 4096))

        config = LLMConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            small_model=small_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return OpenAIGenericClient(config=config, max_tokens=max_tokens)

    def _build_default_embedder(self) -> Any:
        """
        构建 Graphiti 默认 Embedder（OpenAI-compatible /embeddings）

        未显式配置 GRAPHITI_EMBEDDING_BASE_URL 时，保持现有行为：
        - api_key/base_url 读取 OPENAI_*（Config 会从 LLM_* 自动映射）
        - embedding_model 可用 GRAPHITI_EMBEDDING_MODEL 覆盖

        显式配置 GRAPHITI_EMBEDDING_BASE_URL 时，embedding 会走独立 endpoint，
        不影响 Graphiti 的 LLM 实体/关系抽取。

        注意：graphiti-core 0.25.x 会按 embedding_dim 截断向量，切换到
        Qwen3-Embedding-4B 这类 2560 维模型时需配置 GRAPHITI_EMBEDDING_DIM=2560。
        """
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

        api_key = Config.GRAPHITI_EMBEDDING_API_KEY
        base_url = Config.GRAPHITI_EMBEDDING_BASE_URL
        if not base_url:
            api_key = os.environ.get('OPENAI_API_KEY')
            base_url = os.environ.get('OPENAI_BASE_URL')

        # OpenAI SDK 要求 api_key 非空；内网无鉴权 embedding 服务可用 dummy key。
        if base_url and not api_key:
            api_key = "dummy"

        config_kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
            "embedding_dim": max(1, int(Config.GRAPHITI_EMBEDDING_DIM or 1024)),
        }
        if Config.GRAPHITI_EMBEDDING_MODEL:
            config_kwargs["embedding_model"] = Config.GRAPHITI_EMBEDDING_MODEL

        config = OpenAIEmbedderConfig(**config_kwargs)

        base_embedder = OpenAIEmbedder(config=config)

        batch_size = max(1, int(Config.GRAPHITI_EMBEDDING_BATCH_SIZE or 10))
        logger.info(
            "Graphiti embedding 配置: base_url=%s, model=%s, dim=%s, batch_size=%s, independent_endpoint=%s",
            base_url,
            config.embedding_model,
            config.embedding_dim,
            batch_size,
            bool(Config.GRAPHITI_EMBEDDING_BASE_URL),
        )
        return _create_graphiti_embedding_wrapper(base_embedder, max_batch_size=batch_size)

    # ==================== Graph 操作 ====================

    def create_graph(self, graph_id: str, name: str, description: str) -> None:
        """
        创建图谱（在 Graphiti 中通过 group_id 隔离）

        Graphiti 没有显式的图谱创建 API，数据通过 group_id 自动隔离。
        这里仅记录元数据，实际数据在 add_episode 时创建。
        """
        self._graph_metadata[graph_id] = {
            "name": name,
            "description": description,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.info(f"图谱元数据已记录: graph_id={graph_id}, name={name}")

    def delete_graph(self, graph_id: str) -> None:
        """
        删除图谱（删除 group_id 相关的所有数据）

        使用 Cypher 直接删除 Neo4j 中 group_id 匹配的所有节点和边。
        Graphiti 的所有节点（Entity、Episodic 等）都带 group_id 属性，
        一个通用查询即可覆盖。
        """
        self._ensure_initialized()

        async def _delete():
            # 删除所有带有此 group_id 的节点（级联删除边）
            # Graphiti 的 Entity 和 Episodic 节点都带 group_id，无需分别删除
            result = await self._driver.execute_query(
                """
                MATCH (n {group_id: $group_id})
                DETACH DELETE n
                RETURN count(n) as deleted_count
                """,
                group_id=graph_id,
            )
            records = result.records if hasattr(result, 'records') else result[0]
            deleted = records[0]['deleted_count'] if records else 0
            logger.debug(f"删除了 {deleted} 个节点 (group_id={graph_id})")

        _run_async(_delete())

        # 清理本地缓存
        self._graph_metadata.pop(graph_id, None)
        self._ontology_cache.pop(graph_id, None)
        logger.info(f"图谱已删除: graph_id={graph_id}")

    def set_ontology(
        self,
        graph_ids: List[str],
        entities: Optional[Dict[str, Any]] = None,
        edges: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        设置图谱本体

        Graphiti 不提供与 Zep Cloud 完全等价的图级 ontology 注册接口。
        本体会作为自定义实体/边类型注入 Graphiti episode ingestion，
        为 LLM 抽取提供丰富的类型参考；同时 Graphiti 仍允许 LLM 根据
        文本内容动态创建新的实体类型，不会将类型列表作为硬枚举约束。

        仍未对齐的部分：
        - 图级持久化约束/索引管理
        - 更严格的 schema 校验与冲突检测
        """
        for graph_id in graph_ids:
            normalized_entities = self._normalize_entity_types(entities)
            normalized_edges = self._normalize_edge_types(edges)
            edge_type_map = self._build_edge_type_map(edges)
            self._ontology_cache[graph_id] = {
                "entities": normalized_entities,
                "edges": normalized_edges,
                "edge_type_map": edge_type_map,
                "excluded_entity_types": [],
                "schema_hints": {
                    "entities": entities or [],
                    "edges": edges or [],
                },
            }
            logger.info(
                f"Ontology 已缓存: graph_id={graph_id}, "
                f"entity_types={len(normalized_entities)}, "
                f"edge_types={len(normalized_edges)}, "
                f"edge_type_map_keys={len(edge_type_map)}"
            )

    def set_ontology_from_cache(self, graph_id: str, source_client: Any) -> None:
        """从另一个 Graphiti client 复制已归一化的 ontology 缓存。"""
        source_cache = getattr(source_client, "_ontology_cache", {}) or {}
        if graph_id in source_cache:
            self._ontology_cache[graph_id] = source_cache[graph_id]

    # ==================== Episode 操作 ====================

    def add_episode(
        self,
        graph_id: str,
        data: str,
        episode_type: str = "text",
        reference_time: Optional[datetime] = None,
    ) -> str:
        """添加单条 episode"""
        self._ensure_initialized()

        from graphiti_core.nodes import EpisodeType

        # 映射 episode_type
        source_type = EpisodeType.text
        if episode_type == "message":
            source_type = EpisodeType.message
        elif episode_type == "json":
            source_type = EpisodeType.json

        ontology = self._ontology_cache.get(graph_id, {})
        normalized_reference_time = _normalize_reference_time(reference_time)

        async def _add():
            started_at = time.monotonic()
            previous_episode_uuids = None if Config.GRAPHITI_USE_PREVIOUS_EPISODE_CONTEXT else []
            result = await self._graphiti.add_episode(
                name=f"episode_{graph_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                episode_body=data,
                source=source_type,
                source_description="mirofish_simulation",
                reference_time=normalized_reference_time or datetime.now(timezone.utc),
                group_id=graph_id,
                entity_types=ontology.get("entities") or None,
                excluded_entity_types=ontology.get("excluded_entity_types") or None,
                edge_types=ontology.get("edges") or None,
                edge_type_map=ontology.get("edge_type_map") or None,
                previous_episode_uuids=previous_episode_uuids,
            )
            elapsed = time.monotonic() - started_at
            logger.info(
                "Graphiti 单条 episode 写入完成: graph_id=%s, episode_uuid=%s, elapsed=%.1fs, previous_context=%s",
                graph_id,
                result.episode.uuid if result and result.episode else "",
                elapsed,
                bool(Config.GRAPHITI_USE_PREVIOUS_EPISODE_CONTEXT),
            )
            return result.episode.uuid if result and result.episode else ""

        return _run_async(_add())

    def add_episode_batch(
        self,
        graph_id: str,
        episodes: List[Dict[str, Any]]
    ) -> List[str]:
        """批量添加 episode"""
        self._ensure_initialized()

        if not episodes:
            return []

        # Graphiti bulk ingestion 会强制读取 previous episodes，并执行更重的批量
        # 去重/resolve/Neo4j bulk 写入。默认拆成单条 episode，才能让
        # GRAPHITI_USE_PREVIOUS_EPISODE_CONTEXT=false 真正生效。
        if len(episodes) == 1 or not Config.GRAPHITI_USE_BULK_INGEST:
            started_at = time.monotonic()
            episode_uuids = []
            for episode in episodes:
                episode_uuids.append(
                    self.add_episode(
                        graph_id=graph_id,
                        data=episode.get("data", ""),
                        episode_type=episode.get("type", "text"),
                        reference_time=_normalize_reference_time(episode.get("reference_time")),
                    )
                )
            logger.info(
                "Graphiti episode 批次按单条路径写入完成: graph_id=%s, episodes=%s, elapsed=%.1fs, bulk_ingest=False",
                graph_id,
                len(episode_uuids),
                time.monotonic() - started_at,
            )
            return episode_uuids

        from graphiti_core.nodes import EpisodeType
        from graphiti_core.utils.bulk_utils import RawEpisode
        ontology = self._ontology_cache.get(graph_id, {})

        # 构建 RawEpisode 列表
        raw_episodes = []
        for i, ep in enumerate(episodes):
            ep_type = ep.get("type", "text")
            source_type = EpisodeType.text
            if ep_type == "message":
                source_type = EpisodeType.message
            elif ep_type == "json":
                source_type = EpisodeType.json

            raw_episodes.append(
                RawEpisode(
                    name=f"episode_{graph_id}_{i}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    content=ep.get("data", ""),
                    source=source_type,
                    source_description="mirofish_simulation",
                    reference_time=_normalize_reference_time(ep.get("reference_time")) or datetime.now(timezone.utc),
                )
            )

        async def _add_bulk():
            started_at = time.monotonic()
            result = await self._graphiti.add_episode_bulk(
                bulk_episodes=raw_episodes,
                group_id=graph_id,
                entity_types=ontology.get("entities") or None,
                excluded_entity_types=ontology.get("excluded_entity_types") or None,
                edge_types=ontology.get("edges") or None,
                edge_type_map=ontology.get("edge_type_map") or None,
            )
            logger.info(
                "Graphiti bulk episode 写入完成: graph_id=%s, episodes=%s, elapsed=%.1fs, bulk_ingest=True",
                graph_id,
                len(raw_episodes),
                time.monotonic() - started_at,
            )
            # 返回所有 episode UUID
            return [ep.uuid for ep in result.episodes] if result and result.episodes else []

        return _run_async(_add_bulk())

    def get_episode_status(self, episode_uuid: str) -> EpisodeStatus:
        """
        获取 episode 处理状态

        Graphiti 同步处理 episode，添加完成即为已处理。
        """
        return EpisodeStatus(uuid=episode_uuid, processed=True)

    def wait_for_episode(self, episode_uuid: str, timeout: int = 300) -> bool:
        """
        等待 episode 处理完成

        Graphiti 同步处理，直接返回 True。
        """
        return True

    # ==================== Node 操作 ====================

    def get_all_nodes(self, graph_id: str) -> List[GraphNode]:
        """获取图谱所有节点"""
        self._ensure_initialized()

        async def _get_nodes():
            # 优先读取 Graphiti 标准 Entity 节点。
            records, _, _ = await self._driver.execute_query(
                """
                MATCH (n:Entity {group_id: $group_id})
                RETURN
                    n.uuid AS uuid,
                    n.name AS name,
                    labels(n) AS labels,
                    n.summary AS summary,
                    n {
                        .*,
                        uuid: null,
                        name: null,
                        summary: null,
                        created_at: null,
                        group_id: null,
                        name_embedding: null,
                        summary_embedding: null,
                        embedding: null,
                        embeddings: null
                    } AS props,
                    n.created_at AS created_at
                """,
                group_id=graph_id,
            )
            if records:
                return records

            # fallback: 按 group_id 宽匹配，排除 Episodic 等过程节点。
            records, _, _ = await self._driver.execute_query(
                """
                MATCH (n {group_id: $group_id})
                WHERE NOT 'Episodic' IN labels(n)
                  AND n.uuid IS NOT NULL
                  AND n.name IS NOT NULL
                RETURN DISTINCT
                    n.uuid AS uuid,
                    n.name AS name,
                    labels(n) AS labels,
                    n.summary AS summary,
                    n {
                        .*,
                        uuid: null,
                        name: null,
                        summary: null,
                        created_at: null,
                        group_id: null,
                        name_embedding: null,
                        summary_embedding: null,
                        embedding: null,
                        embeddings: null
                    } AS props,
                    n.created_at AS created_at
                """,
                group_id=graph_id,
            )
            if records:
                logger.info(
                    "get_all_nodes: graph_id=%s 使用宽匹配 schema fallback，返回 %s 个节点",
                    graph_id,
                    len(records),
                )
                return records

            logger.warning(
                f"get_all_nodes: 未找到 group_id={graph_id} 的节点。"
                f"可能的原因：1) 图谱为空 2) Graphiti schema 与读取查询不匹配"
            )
            return []

        records = _run_async(_get_nodes())
        nodes = []
        for record in records:
            props = record.get("props", {})
            # 过滤掉已单独提取的属性
            attributes = {
                k: v for k, v in props.items()
                if str(k).lower() not in GRAPHITI_INTERNAL_PROPERTY_KEYS
            }
            created_at = record.get("created_at")
            if hasattr(created_at, 'to_native'):
                created_at = created_at.to_native().isoformat()
            elif created_at:
                created_at = str(created_at)

            nodes.append(GraphNode(
                uuid=record.get("uuid", ""),
                name=record.get("name", ""),
                labels=record.get("labels", ["Entity"]),
                summary=record.get("summary", ""),
                attributes=attributes,
                created_at=created_at,
            ))
        return nodes

    def get_node(self, graph_id: str, node_uuid: str) -> Optional[GraphNode]:
        """获取单个节点"""
        self._ensure_initialized()

        async def _get_node():
            from graphiti_core.nodes import EntityNode

            node = await EntityNode.get_by_uuid(self._driver, node_uuid)
            if not node or getattr(node, "group_id", None) != graph_id:
                return None
            return node

        node = _run_async(_get_node())
        if not node:
            logger.debug(f"get_node: 未找到 graph_id={graph_id}, uuid={node_uuid} 的节点")
            return None
        return self._graphiti_node_to_graph_node(node)

    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[GraphEdge]:
        """获取节点的所有相关边（双向）"""
        self._ensure_initialized()

        async def _get_edges():
            from graphiti_core.edges import EntityEdge

            edges = await EntityEdge.get_by_node_uuid(self._driver, node_uuid)
            return [
                edge for edge in edges
                if getattr(edge, "group_id", None) == graph_id
            ]

        edges = _run_async(_get_edges())
        if not edges:
            logger.debug(f"get_node_edges: graph_id={graph_id}, 节点 uuid={node_uuid} 没有关联的边")
        return [self._graphiti_edge_to_graph_edge(edge) for edge in edges]

    # graphiti EpisodicNode 基类的保留属性名，不能作为自定义实体类型的字段
    _PROTECTED_ATTRIBUTE_NAMES: ClassVar[Set[str]] = {
        "uuid", "name", "group_id", "created_at", "labels",
        "summary", "source", "source_description",
    }

    def _normalize_entity_types(
        self,
        entities: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, type[BaseModel]]:
        if not entities:
            return {}
        if isinstance(entities, dict):
            return entities

        entity_models: Dict[str, type[BaseModel]] = {}
        for entity_def in entities:
            if not isinstance(entity_def, dict) or not entity_def.get("name"):
                continue
            annotations: Dict[str, Any] = {}
            attrs: Dict[str, Any] = {"__annotations__": annotations}
            for attr in entity_def.get("attributes", []):
                attr_name = attr.get("name")
                if not attr_name:
                    continue
                # 过滤 graphiti 保留属性名，避免 EntityTypeValidationError
                if attr_name in self._PROTECTED_ATTRIBUTE_NAMES:
                    logger.debug(
                        "跳过实体类型 %s 的保留属性: %s",
                        entity_def["name"],
                        attr_name,
                    )
                    continue
                annotations[attr_name] = Optional[str]
                attrs[attr_name] = Field(default=None, description=attr.get("description", attr_name))
            entity_models[entity_def["name"]] = type(entity_def["name"], (BaseModel,), attrs)
        return entity_models

    def _normalize_edge_types(
        self,
        edges: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, type[BaseModel]]:
        if not edges:
            return {}
        if isinstance(edges, dict):
            normalized: Dict[str, type[BaseModel]] = {}
            for edge_name, edge_value in edges.items():
                normalized[edge_name] = edge_value[0] if isinstance(edge_value, tuple) else edge_value
            return normalized

        edge_models: Dict[str, type[BaseModel]] = {}
        for edge_def in edges:
            if not isinstance(edge_def, dict) or not edge_def.get("name"):
                continue
            annotations: Dict[str, Any] = {}
            attrs: Dict[str, Any] = {"__annotations__": annotations}
            for attr in edge_def.get("attributes", []):
                attr_name = attr.get("name")
                if not attr_name:
                    continue
                annotations[attr_name] = Optional[str]
                attrs[attr_name] = Field(default=None, description=attr.get("description", attr_name))
            class_name = ''.join(word.capitalize() for word in edge_def["name"].split('_'))
            edge_models[edge_def["name"]] = type(class_name, (BaseModel,), attrs)
        return edge_models

    def _build_edge_type_map(
        self,
        edges: Optional[Dict[str, Any]] = None,
    ) -> Dict[tuple[str, str], List[str]]:
        if not edges:
            return {}

        edge_map: Dict[tuple[str, str], List[str]] = {}
        edge_items = edges.items() if isinstance(edges, dict) else [
            (edge.get("name"), edge) for edge in edges if isinstance(edge, dict)
        ]
        for edge_name, edge_def in edge_items:
            if not edge_name:
                continue
            source_targets = []
            if isinstance(edge_def, tuple) and len(edge_def) > 1:
                source_targets = edge_def[1]
            elif isinstance(edge_def, dict):
                source_targets = edge_def.get("source_targets", [])

            for source_target in source_targets:
                if hasattr(source_target, "source") and hasattr(source_target, "target"):
                    key = (source_target.source, source_target.target)
                else:
                    key = (source_target.get("source", "Entity"), source_target.get("target", "Entity"))
                edge_map.setdefault(key, []).append(edge_name)
        return edge_map

    # ==================== Edge 操作 ====================

    def get_all_edges(self, graph_id: str) -> List[GraphEdge]:
        """获取图谱所有边（通过节点的 group_id 过滤）"""
        self._ensure_initialized()

        async def _get_edges():
            # 优先读取标准 Entity -> Entity 关系。
            records, _, _ = await self._driver.execute_query(
                """
                MATCH (n:Entity {group_id: $group_id})-[r]-(m:Entity)
                WHERE m.group_id = $group_id
                RETURN DISTINCT
                    r.uuid AS uuid,
                    COALESCE(r.name, type(r)) AS name,
                    r.fact AS fact,
                    startNode(r).uuid AS source_uuid,
                    endNode(r).uuid AS target_uuid,
                    r {
                        .*,
                        uuid: null,
                        name: null,
                        fact: null,
                        created_at: null,
                        valid_at: null,
                        invalid_at: null,
                        expired_at: null,
                        group_id: null,
                        episodes: null,
                        fact_embedding: null,
                        embedding: null,
                        embeddings: null
                    } AS props,
                    r.created_at AS created_at,
                    r.valid_at AS valid_at,
                    r.invalid_at AS invalid_at,
                    r.expired_at AS expired_at
                """,
                group_id=graph_id,
            )
            if records:
                return records

            # fallback: 按 group_id 宽匹配，排除 Episodic 等非实体节点。
            records, _, _ = await self._driver.execute_query(
                """
                MATCH (n {group_id: $group_id})-[r]-(m {group_id: $group_id})
                WHERE NOT 'Episodic' IN labels(n)
                  AND NOT 'Episodic' IN labels(m)
                  AND n.uuid IS NOT NULL
                  AND m.uuid IS NOT NULL
                RETURN DISTINCT
                    r.uuid AS uuid,
                    COALESCE(r.name, type(r)) AS name,
                    r.fact AS fact,
                    startNode(r).uuid AS source_uuid,
                    endNode(r).uuid AS target_uuid,
                    r {
                        .*,
                        uuid: null,
                        name: null,
                        fact: null,
                        created_at: null,
                        valid_at: null,
                        invalid_at: null,
                        expired_at: null,
                        group_id: null,
                        episodes: null,
                        fact_embedding: null,
                        embedding: null,
                        embeddings: null
                    } AS props,
                    r.created_at AS created_at,
                    r.valid_at AS valid_at,
                    r.invalid_at AS invalid_at,
                    r.expired_at AS expired_at
                """,
                group_id=graph_id,
            )
            if records:
                logger.info(
                    "get_all_edges: graph_id=%s 使用宽匹配 schema fallback，返回 %s 条边",
                    graph_id,
                    len(records),
                )
                return records

            logger.warning(
                f"get_all_edges: 未找到 group_id={graph_id} 的边。"
                f"可能的原因：1) 图谱无边 2) Graphiti schema 与读取查询不匹配"
            )
            return []

        records = _run_async(_get_edges())
        return [self._record_to_edge(record) for record in records]

    # ==================== Search 操作 ====================

    def _is_openai_compatible_only(self) -> bool:
        """
        检测是否使用非标准 OpenAI API（如 DashScope、Azure 等）

        这些 API 可能不支持 cross_encoder 需要的 logprobs 功能，
        需要 fallback 到 RRF 重排序。

        可通过 GRAPHITI_FORCE_CROSS_ENCODER=true 强制使用 cross_encoder
        （适用于确认支持 logprobs 的兼容服务）。
        """
        import os

        # 显式覆盖：强制使用 cross_encoder
        if os.environ.get('GRAPHITI_FORCE_CROSS_ENCODER', '').lower() in ('true', '1', 'yes'):
            return False

        base_url = os.environ.get('OPENAI_BASE_URL', '')
        # 标准 OpenAI API
        if not base_url or 'api.openai.com' in base_url:
            return False
        # 非标准 API（DashScope、Azure、本地部署等）
        non_standard_indicators = [
            'dashscope', 'aliyun', 'azure', 'localhost',
            'ollama', 'vllm', 'lmstudio', 'openrouter'
        ]
        return any(indicator in base_url.lower() for indicator in non_standard_indicators)

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: str = "rrf"  # 默认改为 rrf，更安全
    ) -> SearchResult:
        """
        图谱混合搜索

        使用 Graphiti 公开的 search_() API（带 config）进行搜索。
        如果 search_() 不可用，fallback 到简单的 search() API。

        注意：reranker="cross_encoder" 需要 OpenAI API 支持 logprobs，
        非标准 API（如 DashScope）会自动降级为 rrf。
        """
        self._ensure_initialized()

        # 非标准 OpenAI API 不支持 cross_encoder，强制使用 rrf
        if reranker == "cross_encoder" and self._is_openai_compatible_only():
            logger.info("检测到非标准 OpenAI API，cross_encoder 降级为 rrf")
            reranker = "rrf"

        from graphiti_core.search.search_config_recipes import (
            NODE_HYBRID_SEARCH_RRF,
            EDGE_HYBRID_SEARCH_RRF,
            COMBINED_HYBRID_SEARCH_CROSS_ENCODER,
        )

        async def _do_search():
            nodes = []
            edges = []

            # 检查是否有 search_() 方法（公开的高级搜索 API）
            has_search_method = hasattr(self._graphiti, 'search_')

            if not has_search_method:
                # Fallback: 使用简单的 search() API
                logger.info("使用 graphiti.search() 简单 API（search_() 不可用）")
                try:
                    results = await self._graphiti.search(
                        query=query,
                        group_ids=[graph_id],
                        num_results=limit,
                    )
                    # 简单 search 主要返回边
                    if results:
                        edges = list(results) if not isinstance(results, list) else results
                    return nodes, edges
                except Exception as e:
                    logger.warning(f"graphiti.search() 失败: {e}，返回空结果")
                    return [], []

            # 使用 search_() 高级 API
            try:
                if scope == "nodes":
                    config = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
                    config.limit = limit
                    result = await self._graphiti.search_(
                        query=query,
                        config=config,
                        group_ids=[graph_id],
                    )
                    if result and hasattr(result, 'nodes'):
                        nodes = result.nodes or []

                elif scope == "edges":
                    config = EDGE_HYBRID_SEARCH_RRF.model_copy(deep=True)
                    config.limit = limit
                    result = await self._graphiti.search_(
                        query=query,
                        config=config,
                        group_ids=[graph_id],
                    )
                    if result and hasattr(result, 'edges'):
                        edges = result.edges or []

                else:  # both
                    if reranker == "cross_encoder":
                        config = COMBINED_HYBRID_SEARCH_CROSS_ENCODER.model_copy(deep=True)
                        config.limit = limit
                        result = await self._graphiti.search_(
                            query=query,
                            config=config,
                            group_ids=[graph_id],
                        )
                        if result:
                            nodes = result.nodes or [] if hasattr(result, 'nodes') else []
                            edges = result.edges or [] if hasattr(result, 'edges') else []
                    else:
                        # 分别搜索 nodes 和 edges
                        node_config = NODE_HYBRID_SEARCH_RRF.model_copy(deep=True)
                        node_config.limit = limit // 2
                        edge_config = EDGE_HYBRID_SEARCH_RRF.model_copy(deep=True)
                        edge_config.limit = limit // 2

                        node_result = await self._graphiti.search_(
                            query=query, config=node_config, group_ids=[graph_id]
                        )
                        edge_result = await self._graphiti.search_(
                            query=query, config=edge_config, group_ids=[graph_id]
                        )

                        if node_result and hasattr(node_result, 'nodes'):
                            nodes = node_result.nodes or []
                        if edge_result and hasattr(edge_result, 'edges'):
                            edges = edge_result.edges or []

            except Exception as e:
                logger.warning(f"graphiti.search_() 失败: {e}，尝试 fallback")
                # Fallback 到简单搜索
                try:
                    results = await self._graphiti.search(
                        query=query,
                        group_ids=[graph_id],
                        num_results=limit,
                    )
                    if results:
                        edges = list(results) if not isinstance(results, list) else results
                except Exception as fallback_e:
                    logger.error(f"search fallback 也失败: {fallback_e}")

            return nodes, edges

        raw_nodes, raw_edges = _run_async(_do_search())

        if not raw_nodes and not raw_edges:
            logger.debug(f"search: query='{query}' group_id={graph_id} 无结果")

        # 转换为适配器数据结构
        nodes = [self._graphiti_node_to_graph_node(n) for n in raw_nodes]
        edges = [self._graphiti_edge_to_graph_edge(e) for e in raw_edges]

        return SearchResult(nodes=nodes, edges=edges)

    # ==================== 转换辅助方法 ====================

    def _record_to_edge(self, record: Dict[str, Any]) -> GraphEdge:
        """将 Neo4j 查询结果转换为 GraphEdge"""
        props = record.get("props", {})
        attributes = {
            k: v for k, v in props.items()
            if str(k).lower() not in GRAPHITI_INTERNAL_PROPERTY_KEYS
        }

        def _format_time(t):
            if t is None:
                return None
            if hasattr(t, 'to_native'):
                return t.to_native().isoformat()
            return str(t)

        return GraphEdge(
            uuid=record.get("uuid", ""),
            name=record.get("name", ""),
            fact=record.get("fact", ""),
            source_node_uuid=record.get("source_uuid", ""),
            target_node_uuid=record.get("target_uuid", ""),
            attributes=attributes,
            created_at=_format_time(record.get("created_at")),
            valid_at=_format_time(record.get("valid_at")),
            invalid_at=_format_time(record.get("invalid_at")),
            expired_at=_format_time(record.get("expired_at")),
            episodes=[],  # Graphiti 边可能没有 episodes 字段
            fact_type=record.get("name", ""),
        )

    def _graphiti_node_to_graph_node(self, node: Any) -> GraphNode:
        """将 Graphiti 节点对象转换为 GraphNode"""
        created_at = getattr(node, 'created_at', None)
        if hasattr(created_at, 'isoformat'):
            created_at = created_at.isoformat()
        elif created_at:
            created_at = str(created_at)

        return GraphNode(
            uuid=getattr(node, 'uuid', ''),
            name=getattr(node, 'name', ''),
            labels=getattr(node, 'labels', ['Entity']),
            summary=getattr(node, 'summary', ''),
            attributes=getattr(node, 'attributes', {}),
            created_at=created_at,
        )

    def _graphiti_edge_to_graph_edge(self, edge: Any) -> GraphEdge:
        """将 Graphiti 边对象转换为 GraphEdge"""
        def _format_time(t):
            if t is None:
                return None
            if hasattr(t, 'isoformat'):
                return t.isoformat()
            return str(t)

        return GraphEdge(
            uuid=getattr(edge, 'uuid', ''),
            name=getattr(edge, 'name', '') or getattr(edge, 'fact_type', ''),
            fact=getattr(edge, 'fact', ''),
            source_node_uuid=getattr(edge, 'source_node_uuid', ''),
            target_node_uuid=getattr(edge, 'target_node_uuid', ''),
            attributes=getattr(edge, 'attributes', {}),
            created_at=_format_time(getattr(edge, 'created_at', None)),
            valid_at=_format_time(getattr(edge, 'valid_at', None)),
            invalid_at=_format_time(getattr(edge, 'invalid_at', None)),
            expired_at=_format_time(getattr(edge, 'expired_at', None)),
            episodes=getattr(edge, 'episodes', []),
            fact_type=getattr(edge, 'fact_type', '') or getattr(edge, 'name', ''),
        )

    def close(self):
        """关闭连接"""
        graphiti = self._graphiti
        if not graphiti:
            return

        close_coro = None
        try:
            close_result = graphiti.close()
            if inspect.isawaitable(close_result):
                close_coro = close_result
                _run_async(close_coro)
                close_coro = None
        except Exception:
            if close_coro is not None and hasattr(close_coro, "close"):
                try:
                    close_coro.close()
                except RuntimeError:
                    pass
            raise
        finally:
            self._graphiti = None
            self._driver = None
            self._initialized = False
        logger.info("Graphiti 连接已关闭")

    def __del__(self):
        """析构时关闭连接"""
        try:
            loop_alive = (
                _async_thread is not None
                and _async_thread.is_alive()
                and _async_loop is not None
                and not _async_loop.is_closed()
            )
            if loop_alive:
                self.close()
            else:
                self._graphiti = None
                self._driver = None
                self._initialized = False
        except Exception:
            pass
