"""
图谱构建服务
接口2：使用Zep API构建Standalone Graph

支持双后端：
- Zep Cloud (默认)
- Graphiti + Neo4j 本地部署
"""

import os
import uuid
import time
import atexit
import threading
import logging
import re
import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from .text_processor import TextProcessor
from .zep_factory import create_zep_client, get_zep_client
from .zep_adapter import ZepClientAdapter
from .location_entity_filter import (
    filter_location_entities,
    strip_location_entity_types_from_ontology,
)
from ..utils import llm_routing
from ..utils.llm_routing import clamp_concurrency, get_preferred_llm_endpoint
from ..utils.neo4j_errors import format_neo4j_auth_error, is_neo4j_auth_error

logger = logging.getLogger("mirofish.graph_builder")

# 跟踪当前活跃的图谱构建操作数量，用于检测进程关闭时是否需要快速失败
_active_graph_operations = 0
_active_graph_operations_lock = threading.Lock()


def _is_graph_builder_shutting_down() -> bool:
    """检查图谱构建器是否正在关闭（例如 Flask 热重载触发）。"""
    with _active_graph_operations_lock:
        return _active_graph_operations < 0


def _enter_graph_operation() -> None:
    """标记一个图谱操作开始。"""
    global _active_graph_operations
    with _active_graph_operations_lock:
        if _active_graph_operations >= 0:
            _active_graph_operations += 1


def _leave_graph_operation() -> None:
    """标记一个图谱操作结束。"""
    global _active_graph_operations
    with _active_graph_operations_lock:
        if _active_graph_operations > 0:
            _active_graph_operations -= 1


def _shutdown_graph_operations() -> None:
    """强制标记所有图谱操作为关闭状态（由 atexit 或进程退出时调用）。"""
    global _active_graph_operations
    with _active_graph_operations_lock:
        if _active_graph_operations > 0:
            logger.warning(
                "进程正在关闭，仍有 %s 个活跃图谱操作将被中断。"
                "这通常由 Flask 热重载（检测到文件修改）或服务器关闭触发。",
                _active_graph_operations,
            )
        _active_graph_operations = -1  # 负数表示正在关闭


atexit.register(_shutdown_graph_operations)

SIMULATION_MEMORY_LABEL = "未来推演记忆"
SIMULATION_MEMORY_LABEL_KEY = "FutureSimulationMemory"


@dataclass
class GraphInfo:
    """图谱信息"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


@dataclass(frozen=True)
class GraphBatchPlan:
    """图谱写入批次计划。"""
    batch_size: int
    concurrency: int
    use_bulk_ingest: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_size": self.batch_size,
            "concurrency": self.concurrency,
            "use_bulk_ingest": self.use_bulk_ingest,
        }


@dataclass(frozen=True)
class _SingleLLMEndpointPool:
    """路由工具尚未提供 pool 时使用的单模型兼容池。"""

    endpoint: Any

    def select_endpoint(self, batch_index: int) -> Any:
        return self.endpoint


class GraphBuilderService:
    """
    图谱构建服务
    负责调用Zep API构建知识图谱

    支持双后端：
    - Zep Cloud: 使用 zep-cloud SDK
    - Graphiti: 使用 graphiti-core + Neo4j
    """

    INTERNAL_ATTRIBUTE_KEYS = {
        "name_embedding",
        "embedding",
        "embeddings",
    }
    GENERIC_NODE_LABELS = {"Entity", "Node"}

    def __init__(
        self,
        api_key: Optional[str] = None,
        backend: Optional[str] = None,
        build_mode: bool = False,
    ):
        """
        初始化图谱构建服务

        Args:
            api_key: Zep API Key（仅 cloud 模式需要，可选）
        """
        self._backend = backend or Config.ZEP_BACKEND
        if self._backend == 'graphiti' and build_mode:
            self._llm_endpoint = get_preferred_llm_endpoint(prefer_boost=True)
            self._llm_endpoint_pool = self._build_llm_endpoint_pool(self._llm_endpoint)
            self.client: ZepClientAdapter = create_zep_client(
                backend=self._backend,
                use_singleton=False,
                llm_endpoint=self._llm_endpoint,
            )
        else:
            self._llm_endpoint = None
            self._llm_endpoint_pool = None
            self.client: ZepClientAdapter = get_zep_client(backend=self._backend)
        self._llm_route_counts: Dict[str, int] = {}
        self.task_manager = TaskManager()

    @staticmethod
    def _build_llm_endpoint_pool(preferred_endpoint: Any = None) -> Optional[Any]:
        """获取图谱构建 LLM endpoint pool；路由层未就绪时退回单端点。"""
        pool_factory = getattr(llm_routing, "get_graph_build_llm_endpoint_pool", None)
        if callable(pool_factory):
            try:
                pool = pool_factory()
                if pool:
                    return pool
            except Exception:
                logger.exception("图谱构建 LLM endpoint pool 初始化失败，回退到 preferred endpoint")

        if preferred_endpoint is not None:
            return _SingleLLMEndpointPool(preferred_endpoint)
        return None

    @staticmethod
    def _select_llm_endpoint_from_pool(endpoint_pool: Any, batch_index: int) -> Any:
        """兼容不同 endpoint pool 接口，按批次选择端点。"""
        if endpoint_pool is None:
            return None
        for method_name in ("endpoint_for_index", "select_endpoint", "get_endpoint", "endpoint_for_batch", "select"):
            selector = getattr(endpoint_pool, method_name, None)
            if callable(selector):
                return selector(batch_index)
        if isinstance(endpoint_pool, (list, tuple)) and endpoint_pool:
            return endpoint_pool[batch_index % len(endpoint_pool)]
        return getattr(endpoint_pool, "endpoint", endpoint_pool)

    @staticmethod
    def _llm_route_name(endpoint: Any) -> str:
        route_name = getattr(endpoint, "route_name", None)
        if route_name:
            return str(route_name)
        if getattr(endpoint, "is_boost", False):
            return "boost"
        if endpoint is not None:
            return "base"
        return "default"

    @staticmethod
    def _llm_model_name(endpoint: Any) -> Optional[str]:
        return getattr(endpoint, "model", None) if endpoint is not None else None

    @classmethod
    def _alternate_llm_endpoint_from_pool(cls, endpoint_pool: Any, current_endpoint: Any) -> Any:
        """从 endpoint pool 中选择一个不同路由的备用端点，用于批次级失败重试。"""
        if endpoint_pool is None or current_endpoint is None:
            return None

        candidates = getattr(endpoint_pool, "endpoints", None) or []
        current_route = cls._llm_route_name(current_endpoint)
        for candidate in candidates:
            endpoint = getattr(candidate, "endpoint", candidate)
            if cls._llm_route_name(endpoint) != current_route:
                return endpoint
        return None

    def get_llm_observability(self) -> Dict[str, Any]:
        """返回图谱构建 LLM 路由观测信息。"""
        pool_info = self._describe_llm_endpoint_pool(getattr(self, "_llm_endpoint_pool", None))
        pool_info["llm_route_counts"] = dict(getattr(self, "_llm_route_counts", {}))
        return pool_info

    @classmethod
    def _describe_llm_endpoint_pool(cls, endpoint_pool: Any) -> Dict[str, Any]:
        """输出 endpoint pool 摘要，供日志和任务元数据观测。"""
        if endpoint_pool is None:
            return {
                "dual_llm_enabled": False,
                "llm_routes": [],
                "llm_route_weights": {},
            }

        routes = getattr(endpoint_pool, "routes", None) or getattr(endpoint_pool, "endpoints", None)
        route_names: List[str] = []
        route_weights: Dict[str, int] = {}
        if routes:
            for item in routes:
                endpoint = getattr(item, "endpoint", item)
                route = getattr(item, "route_name", None) or cls._llm_route_name(endpoint)
                route_names.append(route)
                weight = getattr(item, "weight", None)
                if weight is None:
                    weights = getattr(endpoint_pool, "weights", None)
                    if isinstance(weights, dict):
                        weight = weights.get(route)
                if weight is not None:
                    route_weights[route] = int(weight)

        if not route_names:
            endpoint = getattr(endpoint_pool, "endpoint", None)
            if endpoint is not None:
                route_names = [cls._llm_route_name(endpoint)]

        route_names = list(dict.fromkeys(route_names))
        return {
            "dual_llm_enabled": len(route_names) > 1,
            "llm_routes": route_names,
            "llm_route_weights": route_weights,
        }
    
    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = Config.DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = Config.DEFAULT_CHUNK_OVERLAP,
        batch_size: int = Config.GRAPH_BUILD_BATCH_SIZE,
        concurrency: int = Config.GRAPH_BUILD_CONCURRENCY,
        extraction_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        异步构建图谱
        
        Args:
            text: 输入文本
            ontology: 本体定义（来自接口1的输出）
            graph_name: 图谱名称
            chunk_size: 文本块大小
            chunk_overlap: 块重叠大小
            batch_size: 每批发送的块数量
            
        Returns:
            任务ID
        """
        llm_pool_info = self._describe_llm_endpoint_pool(getattr(self, "_llm_endpoint_pool", None))
        # 创建任务
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
                "batch_size": batch_size,
                "concurrency": concurrency,
                "llm_model": self._llm_endpoint.model if self._llm_endpoint else None,
                "llm_boost_enabled": bool(self._llm_endpoint and self._llm_endpoint.is_boost),
                **llm_pool_info,
            }
        )
        
        # 在后台线程中执行构建
        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size, concurrency, extraction_context)
        )
        thread.daemon = True
        thread.start()
        
        return task_id
    
    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int,
        concurrency: int,
        extraction_context: Optional[Dict[str, Any]] = None,
    ):
        """图谱构建工作线程"""
        _enter_graph_operation()
        try:
            if _is_graph_builder_shutting_down():
                raise RuntimeError(
                    "图谱构建已取消：服务器正在关闭（可能是 Flask 热重载触发了进程重启）。"
                    "请等待当前图谱构建完成后再修改代码文件，或重启服务后重试。"
                )

            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message="开始构建图谱..."
            )

            # 1. 创建图谱
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"图谱已创建: {graph_id}"
            )

            # 2. 设置本体
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message="本体已设置"
            )

            # 3. 文本分块
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=f"文本已分割为 {total_chunks} 个块"
            )

            # 4. 分批发送数据
            episode_uuids = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.4),  # 20-60%
                    message=msg
                ),
                extraction_context=extraction_context or {"event_topic": graph_name},
                concurrency=concurrency,
            )

            # 5. 等待Zep处理完成
            self.task_manager.update_task(
                task_id,
                progress=60,
                message="等待图谱服务处理数据..."
            )

            self._wait_for_episodes(
                episode_uuids,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=60 + int(prog * 0.3),  # 60-90%
                    message=msg
                )
            )

            # 6. 获取图谱信息
            self.task_manager.update_task(
                task_id,
                progress=90,
                message="获取图谱信息..."
            )

            graph_info = self._get_graph_info(graph_id)

            # 完成
            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)
        finally:
            _leave_graph_operation()
    
    def create_graph(self, name: str) -> str:
        """创建图谱（公开方法）"""
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"

        self.client.create_graph(
            graph_id=graph_id,
            name=name,
            description="MiroFish Social Simulation Graph"
        )

        return graph_id
    
    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """
        设置图谱本体（公开方法）

        根据后端类型选择处理方式：
        - Zep Cloud: 动态创建 Pydantic 模型，调用 Zep API
        - Graphiti: 归一化并缓存 ontology，供 episode 抽取时注入自定义实体/边类型
        """
        ontology = strip_location_entity_types_from_ontology(ontology)

        if self._backend == 'graphiti':
            # Graphiti 后端：直接传递原始 ontology，适配器会归一化并在写入时注入
            self.client.set_ontology(
                graph_ids=[graph_id],
                entities=ontology.get("entity_types", []),
                edges=ontology.get("edge_types", []),
            )
            return

        # Zep Cloud 后端：需要动态创建 Pydantic 模型
        import warnings
        from typing import Optional
        from pydantic import Field
        from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel
        from zep_cloud import EntityEdgeSourceTarget

        # 抑制 Pydantic v2 关于 Field(default=None) 的警告
        # 这是 Zep SDK 要求的用法，警告来自动态类创建，可以安全忽略
        warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')

        # Zep 保留名称，不能作为属性名
        RESERVED_NAMES = {'uuid', 'name', 'group_id', 'name_embedding', 'summary', 'created_at'}

        def safe_attr_name(attr_name: str) -> str:
            """将保留名称转换为安全名称"""
            if attr_name.lower() in RESERVED_NAMES:
                return f"entity_{attr_name}"
            return attr_name

        # 动态创建实体类型
        entity_types = {}
        for entity_def in ontology.get("entity_types", []):
            name = entity_def["name"]
            description = entity_def.get("description", f"A {name} entity.")

            # 创建属性字典和类型注解（Pydantic v2 需要）
            attrs = {"__doc__": description}
            annotations = {}

            for attr_def in entity_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])  # 使用安全名称
                attr_desc = attr_def.get("description", attr_name)
                # Zep API 需要 Field 的 description，这是必需的
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[EntityText]  # 类型注解

            attrs["__annotations__"] = annotations

            # 动态创建类
            entity_class = type(name, (EntityModel,), attrs)
            entity_class.__doc__ = description
            entity_types[name] = entity_class

        # 动态创建边类型
        edge_definitions = {}
        for edge_def in ontology.get("edge_types", []):
            name = edge_def["name"]
            description = edge_def.get("description", f"A {name} relationship.")

            # 创建属性字典和类型注解
            attrs = {"__doc__": description}
            annotations = {}

            for attr_def in edge_def.get("attributes", []):
                attr_name = safe_attr_name(attr_def["name"])  # 使用安全名称
                attr_desc = attr_def.get("description", attr_name)
                # Zep API 需要 Field 的 description，这是必需的
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[str]  # 边属性用str类型

            attrs["__annotations__"] = annotations

            # 动态创建类
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            edge_class = type(class_name, (EdgeModel,), attrs)
            edge_class.__doc__ = description

            # 构建source_targets
            source_targets = []
            for st in edge_def.get("source_targets", []):
                source_targets.append(
                    EntityEdgeSourceTarget(
                        source=st.get("source", "Entity"),
                        target=st.get("target", "Entity")
                    )
                )

            if source_targets:
                edge_definitions[name] = (edge_class, source_targets)

        # 调用适配器设置本体
        if entity_types or edge_definitions:
            self.client.set_ontology(
                graph_ids=[graph_id],
                entities=entity_types if entity_types else None,
                edges=edge_definitions if edge_definitions else None,
            )
    
    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = Config.GRAPH_BUILD_BATCH_SIZE,
        progress_callback: Optional[Callable] = None,
        extraction_context: Optional[Dict[str, Any]] = None,
        concurrency: int = Config.GRAPH_BUILD_CONCURRENCY,
    ) -> List[str]:
        """分批添加文本到图谱，返回所有 episode 的 uuid 列表"""
        total_chunks = len(chunks)
        if total_chunks == 0:
            return []

        backend = getattr(self, "_backend", Config.ZEP_BACKEND)
        plan_started_at = time.monotonic()
        batch_plan = self.resolve_batch_plan(
            batch_size=batch_size,
            concurrency=concurrency,
            backend=backend,
        )
        batch_size = batch_plan.batch_size
        concurrency = batch_plan.concurrency

        batch_specs = []
        total_batches = (total_chunks + batch_size - 1) // batch_size
        for start in range(0, total_chunks, batch_size):
            batch_chunks = chunks[start:start + batch_size]
            batch_specs.append((len(batch_specs), start, batch_chunks))

        llm_pool_info = self._describe_llm_endpoint_pool(getattr(self, "_llm_endpoint_pool", None))
        logger.info(
            "图谱写入计划: graph_id=%s, backend=%s, chunks=%s, batch_size=%s, concurrency=%s, use_bulk_ingest=%s, llm_routes=%s, llm_route_weights=%s",
            graph_id,
            backend,
            total_chunks,
            batch_size,
            concurrency,
            batch_plan.use_bulk_ingest,
            llm_pool_info["llm_routes"],
            llm_pool_info["llm_route_weights"],
        )

        # Cloud add_batch 自身是批量异步处理，保守串行提交；Graphiti 本地抽取才启用并发写入。
        use_worker_clients = (
            backend == 'graphiti'
            and concurrency > 1
            and hasattr(self.client, "set_ontology_from_cache")
        )

        completed_batches = 0
        episode_uuids_by_batch: Dict[int, List[str]] = {}
        llm_route_counts: Dict[str, int] = {}
        self._llm_route_counts = {}
        progress_lock = threading.Lock()
        failure_event = threading.Event()
        max_reported_ratio = 0.0

        def build_episodes(batch_chunks: List[str]) -> List[Dict[str, Any]]:
            return [
                {
                    "data": self._wrap_chunk_with_event_constraints(chunk, extraction_context),
                    "type": "text",
                    "reference_time": None,
                }
                for chunk in batch_chunks
            ]

        def report_progress(message: str, ratio: float) -> None:
            """并发批次完成顺序不固定，确保对外进度只向前推进。"""
            nonlocal max_reported_ratio
            if not progress_callback:
                return
            safe_ratio = max(0.0, min(1.0, float(ratio or 0)))
            with progress_lock:
                max_reported_ratio = max(max_reported_ratio, safe_ratio)
                reported_ratio = max_reported_ratio
            progress_callback(message, reported_ratio)

        def iter_exception_messages(exc: Exception) -> List[str]:
            messages: List[str] = []
            seen = set()
            current = exc
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                messages.append(str(current) or current.__class__.__name__)
                current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
            return messages

        def raise_batch_error(batch_num: int, exc: Exception) -> None:
            if isinstance(exc, TimeoutError) or isinstance(exc, FutureTimeoutError):
                timeout_text = " ".join(iter_exception_messages(exc))
                if "单次请求超过" in timeout_text:
                    message = timeout_text
                else:
                    message = f"批次 {batch_num} 写入超过 Graphiti 超时限制"
            elif is_neo4j_auth_error(exc):
                message = format_neo4j_auth_error(exc)
            else:
                exception_text = " ".join(iter_exception_messages(exc)).lower()
                if (
                    "insufficient_quota" in exception_text
                    or "exceeded your current quota" in exception_text
                    or "check your plan and billing" in exception_text
                ):
                    message = "LLM 服务额度不足或账单配额已耗尽，请检查 API Key、套餐/余额，或切换 Graphiti 使用的模型后重试"
                elif "429" in exception_text or ("rate" in exception_text and "limit" in exception_text):
                    message = "LLM 服务触发限流，请稍后重试；若频繁出现，请降低 GRAPHITI_LLM_CONCURRENCY 或调大 GRAPHITI_LLM_MIN_INTERVAL_SECONDS"
                else:
                    message = str(exc) or exc.__class__.__name__
            report_progress(f"批次 {batch_num} 发送失败: {message}", 0)
            raise RuntimeError(f"批次 {batch_num} 图谱写入失败: {message}") from exc

        def create_worker_client(endpoint: Any = None) -> ZepClientAdapter:
            worker_client = create_zep_client(
                backend=backend,
                use_singleton=False,
                llm_endpoint=endpoint,
            )
            if hasattr(worker_client, "set_ontology_from_cache"):
                worker_client.set_ontology_from_cache(graph_id, self.client)
            return worker_client

        def submit_batch(batch_index: int, start_index: int, batch_chunks: List[str]) -> List[str]:
            batch_num = batch_index + 1
            # 检查进程是否正在关闭（如 Flask 热重载触发）
            if _is_graph_builder_shutting_down():
                raise RuntimeError(
                    "图谱批次提交已取消：服务器正在关闭（可能是 Flask 热重载触发了进程重启）。"
                    "请等待当前图谱构建完成后再修改代码文件。"
                )
            batch_started_at = time.monotonic()
            if use_worker_clients:
                endpoint = self._select_llm_endpoint_from_pool(
                    getattr(self, "_llm_endpoint_pool", None),
                    batch_index,
                )
                if endpoint is None:
                    endpoint = getattr(self, "_llm_endpoint", None)
            else:
                endpoint = getattr(self, "_llm_endpoint", None)
            route_name = self._llm_route_name(endpoint)
            model_name = self._llm_model_name(endpoint)
            report_progress(
                f"发送第 {batch_num}/{total_batches} 批数据 ({len(batch_chunks)} 块)...",
                min(start_index / total_chunks, completed_batches / total_batches),
            )

            def run_with_endpoint(current_endpoint: Any, is_retry: bool = False) -> tuple[List[str], str, Optional[str], float]:
                current_route = self._llm_route_name(current_endpoint)
                current_model = self._llm_model_name(current_endpoint)
                attempt_started_at = time.monotonic()
                worker_client = create_worker_client(current_endpoint) if use_worker_clients else self.client
                try:
                    logger.info(
                        "图谱批次写入开始: graph_id=%s, batch=%s/%s, chunks=%s, concurrency=%s, worker_client=%s, llm_route=%s, llm_model=%s, retry=%s",
                        graph_id,
                        batch_num,
                        total_batches,
                        len(batch_chunks),
                        concurrency,
                        worker_client is not self.client,
                        current_route,
                        current_model,
                        is_retry,
                    )
                    batch_uuids = worker_client.add_episode_batch(
                        graph_id=graph_id,
                        episodes=build_episodes(batch_chunks),
                    )
                    return batch_uuids, current_route, current_model, time.monotonic() - attempt_started_at
                finally:
                    if worker_client is not self.client and hasattr(worker_client, "close"):
                        try:
                            worker_client.close()
                        except Exception:
                            pass

            try:
                try:
                    batch_uuids, route_name, model_name, batch_elapsed = run_with_endpoint(endpoint)
                except Exception as first_exc:
                    # 致命错误（进程关闭、事件循环死亡等）跳过路由切换重试，直接抛出
                    _is_shutdown = False
                    try:
                        from .zep_graphiti_impl import _is_fatal_error
                        _is_shutdown = _is_fatal_error(first_exc)
                    except Exception:
                        pass
                    if _is_shutdown or is_neo4j_auth_error(first_exc):
                        logger.error(
                            "图谱批次遇到不可通过 LLM 路由切换恢复的错误，跳过路由切换: graph_id=%s, batch=%s/%s, route=%s, model=%s, error=%s",
                            graph_id,
                            batch_num,
                            total_batches,
                            route_name,
                            model_name,
                            first_exc,
                        )
                        raise

                    alternate_endpoint = None
                    if (
                        use_worker_clients
                        and Config.GRAPH_BUILD_LLM_ROUTE_RETRY_ENABLED
                        and not failure_event.is_set()
                    ):
                        alternate_endpoint = self._alternate_llm_endpoint_from_pool(
                            getattr(self, "_llm_endpoint_pool", None),
                            endpoint,
                        )

                    if alternate_endpoint is None:
                        raise

                    alternate_route = self._llm_route_name(alternate_endpoint)
                    alternate_model = self._llm_model_name(alternate_endpoint)
                    logger.warning(
                        "图谱批次首选 LLM 路由失败，切换备用路由重试: graph_id=%s, batch=%s/%s, failed_route=%s, failed_model=%s, retry_route=%s, retry_model=%s, error=%s",
                        graph_id,
                        batch_num,
                        total_batches,
                        route_name,
                        model_name,
                        alternate_route,
                        alternate_model,
                        first_exc,
                    )
                    report_progress(
                        f"第 {batch_num}/{total_batches} 批首选 {route_name} 路由失败，切换 {alternate_route} 重试...",
                        min(start_index / total_chunks, completed_batches / total_batches),
                    )
                    try:
                        batch_uuids, route_name, model_name, batch_elapsed = run_with_endpoint(
                            alternate_endpoint,
                            is_retry=True,
                        )
                    except Exception as retry_exc:
                        raise RuntimeError(
                            f"首选路由 {self._llm_route_name(endpoint)} 失败后，备用路由 {alternate_route} 重试仍失败"
                        ) from retry_exc

                with progress_lock:
                    llm_route_counts[route_name] = llm_route_counts.get(route_name, 0) + 1
                    self._llm_route_counts = dict(llm_route_counts)
                if failure_event.is_set():
                    logger.info(
                        "图谱批次写入完成但任务已失败，结果丢弃: graph_id=%s, batch=%s/%s, episodes=%s, llm_route=%s, llm_model=%s, elapsed=%.1fs",
                        graph_id,
                        batch_num,
                        total_batches,
                        len(batch_uuids),
                        route_name,
                        model_name,
                        batch_elapsed,
                    )
                else:
                    logger.info(
                        "图谱批次写入完成: graph_id=%s, batch=%s/%s, episodes=%s, llm_route=%s, llm_model=%s, elapsed=%.1fs",
                        graph_id,
                        batch_num,
                        total_batches,
                        len(batch_uuids),
                        route_name,
                        model_name,
                        batch_elapsed,
                    )
                delay = max(0.0, float(Config.GRAPH_BUILD_BATCH_DELAY_SECONDS or 0))
                if delay:
                    time.sleep(delay)
                return batch_uuids
            except Exception:
                failure_event.set()
                logger.exception(
                    "图谱批次写入异常: graph_id=%s, batch=%s/%s, llm_route=%s, llm_model=%s",
                    graph_id,
                    batch_num,
                    total_batches,
                    route_name,
                    model_name,
                )
                raise

        if concurrency <= 1 or total_batches == 1:
            for batch_index, start_index, batch_chunks in batch_specs:
                try:
                    episode_uuids_by_batch[batch_index] = submit_batch(batch_index, start_index, batch_chunks)
                    completed_batches += 1
                    report_progress(
                        f"已完成第 {completed_batches}/{total_batches} 批数据写入",
                        completed_batches / total_batches,
                    )
                except Exception as e:
                    raise_batch_error(batch_index + 1, e)
        else:
            worker_count = min(concurrency, total_batches)
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_batch: Dict[Any, int] = {}
                next_batch_index = 0

                def submit_next_batch() -> None:
                    nonlocal next_batch_index
                    if failure_event.is_set() or next_batch_index >= len(batch_specs):
                        return
                    batch_index, start_index, batch_chunks = batch_specs[next_batch_index]
                    future = executor.submit(submit_batch, batch_index, start_index, batch_chunks)
                    future_to_batch[future] = batch_index
                    next_batch_index += 1

                for _ in range(worker_count):
                    submit_next_batch()

                while future_to_batch:
                    completed_future = None
                    for future in as_completed(list(future_to_batch)):
                        completed_future = future
                        break
                    if completed_future is None:
                        break

                    batch_index = future_to_batch.pop(completed_future)
                    try:
                        episode_uuids_by_batch[batch_index] = completed_future.result()
                    except Exception as e:
                        failure_event.set()
                        for pending_future in future_to_batch:
                            pending_future.cancel()
                        raise_batch_error(batch_index + 1, e)

                    with progress_lock:
                        completed_batches += 1
                        current_completed = completed_batches

                    report_progress(
                        f"已完成第 {current_completed}/{total_batches} 批数据写入",
                        current_completed / total_batches,
                    )
                    submit_next_batch()

        episode_uuids: List[str] = []
        for batch_index in range(total_batches):
            episode_uuids.extend(episode_uuids_by_batch.get(batch_index, []))
        logger.info(
            "图谱文本批次全部写入完成: graph_id=%s, chunks=%s, batches=%s, episodes=%s, llm_route_counts=%s, elapsed=%.1fs",
            graph_id,
            total_chunks,
            total_batches,
            len(episode_uuids),
            dict(sorted(llm_route_counts.items())),
            time.monotonic() - plan_started_at,
        )
        return episode_uuids

    @staticmethod
    def resolve_batch_plan(
        batch_size: int = Config.GRAPH_BUILD_BATCH_SIZE,
        concurrency: int = Config.GRAPH_BUILD_CONCURRENCY,
        backend: Optional[str] = None,
    ) -> GraphBatchPlan:
        """计算图谱写入实际生效的批次大小和并发数。"""
        resolved_batch_size = max(1, int(batch_size or 1))
        resolved_concurrency = clamp_concurrency(
            concurrency,
            Config.GRAPH_BUILD_CONCURRENCY,
            maximum=8,
        )

        use_bulk_ingest = False
        if (backend or Config.ZEP_BACKEND) == 'graphiti':
            use_bulk_ingest = bool(Config.GRAPHITI_USE_BULK_INGEST)
            graphiti_batch_size = max(1, int(Config.GRAPHITI_EPISODE_BATCH_SIZE or 1)) if use_bulk_ingest else 1
            graphiti_ingest_concurrency = max(1, int(Config.GRAPHITI_INGEST_CONCURRENCY or 1))
            resolved_batch_size = min(resolved_batch_size, graphiti_batch_size)
            resolved_concurrency = min(resolved_concurrency, graphiti_ingest_concurrency)
        else:
            resolved_concurrency = 1
            use_bulk_ingest = resolved_batch_size > 1

        return GraphBatchPlan(
            batch_size=resolved_batch_size,
            concurrency=resolved_concurrency,
            use_bulk_ingest=use_bulk_ingest,
        )

    @classmethod
    def _wrap_chunk_with_event_constraints(
        cls,
        chunk: str,
        extraction_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """为每个文本块补充事件锚点，约束图谱抽取保留事件关键实体。"""
        if not extraction_context:
            return chunk

        event_topic = cls._compact_context_value(extraction_context.get("event_topic"), 200)
        simulation_requirement = cls._compact_context_value(
            extraction_context.get("simulation_requirement"),
            300,
        )
        seed_summary = cls._compact_context_value(
            extraction_context.get("seed_summary"),
            max(0, int(Config.GRAPH_EXTRACTION_CONTEXT_MAX_SUMMARY_CHARS or 0)),
        )
        entity_hints = cls._compact_context_list(
            extraction_context.get("entity_hints"),
            max(0, int(Config.GRAPH_EXTRACTION_CONTEXT_MAX_ENTITY_HINTS or 0)),
            max(0, int(Config.GRAPH_EXTRACTION_CONTEXT_MAX_HINT_CHARS or 0)),
        )

        context_lines = []
        if event_topic:
            context_lines.append(f"- 事件主题：{event_topic}")
        if simulation_requirement:
            context_lines.append(f"- 推演方向：{simulation_requirement}")
        if entity_hints:
            context_lines.append(f"- 已知关键实体提示：{entity_hints}")
        if seed_summary:
            context_lines.append(f"- 事件摘要：{seed_summary}")

        context_block = "\n".join(context_lines) or "- 事件主题：未提供"
        return f"""# 图谱实体抽取约束（仅用于判断相关性，不是事实来源）
{context_block}

抽取指导规则（建议性，非强制性）：
1. 优先抽取“文档文本块”中明确出现，且与事件主题、事件事实或推演方向存在关联的实体节点。不确定关联性的实体也建议保留，由后续阶段进一步筛选。
2. 图谱实体不等于最终人设 Agent；抽取阶段必须优先保留事件关键实体，Agent 生成阶段会另行过滤地点、重标媒体平台。
3. 实体数量目标下限为50+；不是只抽核心节点，而是尽最大可能抽取与事件相关的所有具体主体，没有最高数量限制。
4. 实体类型完全开放，不受本体中已列类型限制；如果文本里出现新的主体类型，请按语义创建更具体的新类型，不要强行塞进少数预设类型。
5. 尽量完整覆盖政府/监管、单位、机构、企业/品牌、媒体、组织/协会、意见领袖/网红、社区、公众、主配角、网民/个人、以及事件角色（受害人、嫌疑人、目击者、家属等）等关键具体相关主体。
6. 核心人物必须优先抽取：受害人/被害人、嫌疑人/犯罪嫌疑人、被告人、当事人、主角/配角、死者、伤者、亲属、证人、律师等，只要文档文本块出现并与事件有关，就不能因为其不一定发声而忽略。
7. 若同一人物同时出现全名和匿名/代称（如“许国利/许某”“来惠利/来某/来女士/被害人”），优先使用文本中出现的全名作为实体名称，匿名称谓放入属性或摘要。
8. 实体关系应从文档文本块中的事实出发。对于背景、广告、推荐或相似案例段落中的实体，需判断其是否与事件主线存在实质性关联；如果有关联也应抽取。
9. 如果某个名称出现在文本块中但关系不明确，仍建议抽取并保留，避免遗漏潜在关键实体。
10. 不要把本约束中的类别词、规则文本或示例当作实体；实体事实只能来自“文档文本块”。
11. 纯地点/位置/地址（如省市区、街道、小区）不要作为 Person 类型实体节点；若地点对事件链条关键，可作为 Location/Place 或事件背景属性保留，但后续 Agent 阶段会过滤。
12. 若名称指向组织（如政府机构、学校、医院、公司、媒体等），按组织主体抽取；若名称只是商场/小区/地址，即使与事件有关也不要抽为 Agent 候选实体。
13. 小红书、微博、抖音、豆瓣、知乎等是媒体/社交平台，必须保留为 MediaPlatform/SocialMediaPlatform/Media 等平台或媒体主体，不能归为 Person。
14. 与事件主题明确相关的实体应全部入图；仅作为背景噪音或广告推荐出现的无关实体可以忽略，但需以当前文档文本块的实际内容为准，不要机械套用例。

# 文档文本块（唯一事实来源）
{chunk}"""

    @staticmethod
    def _compact_context_value(value: Any, limit: int) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = " ".join(text.split())
        return text[:limit]

    @classmethod
    def _compact_context_list(cls, values: Any, max_items: int, limit: int) -> str:
        if not values:
            return ""
        if isinstance(values, str):
            items = [values]
        else:
            items = [str(item).strip() for item in values if str(item).strip()]
        text = "、".join(items[:max_items])
        return cls._compact_context_value(text, limit)
    
    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600
    ):
        """等待所有 episode 处理完成（通过查询每个 episode 的 processed 状态）"""
        if not episode_uuids:
            if progress_callback:
                progress_callback("无需等待（没有 episode）", 1.0)
            return

        # Graphiti 同步处理，直接返回
        if self._backend == 'graphiti':
            if progress_callback:
                progress_callback(f"处理完成: {len(episode_uuids)}/{len(episode_uuids)}", 1.0)
            return

        start_time = time.time()
        pending_episodes = set(episode_uuids)
        completed_count = 0
        total_episodes = len(episode_uuids)

        if progress_callback:
            progress_callback(f"开始等待 {total_episodes} 个文本块处理...", 0)

        while pending_episodes:
            if time.time() - start_time > timeout:
                if progress_callback:
                    progress_callback(
                        f"部分文本块超时，已完成 {completed_count}/{total_episodes}",
                        completed_count / total_episodes
                    )
                break

            # 检查每个 episode 的处理状态
            for ep_uuid in list(pending_episodes):
                try:
                    status = self.client.get_episode_status(ep_uuid)
                    if status.processed:
                        pending_episodes.remove(ep_uuid)
                        completed_count += 1

                except Exception as e:
                    # 忽略单个查询错误，继续
                    pass

            elapsed = int(time.time() - start_time)
            if progress_callback:
                progress_callback(
                    f"处理中... {completed_count}/{total_episodes} 完成, {len(pending_episodes)} 待处理 ({elapsed}秒)",
                    completed_count / total_episodes if total_episodes > 0 else 0
                )

            if pending_episodes:
                time.sleep(3)  # 每3秒检查一次

        if progress_callback:
            progress_callback(f"处理完成: {completed_count}/{total_episodes}", 1.0)
    
    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """获取图谱信息"""
        # 使用适配器获取节点和边
        nodes = self.client.get_all_nodes(graph_id)
        edges = self.client.get_all_edges(graph_id)
        nodes, edges = self._coalesce_duplicate_entities(nodes, edges)
        nodes, edges = filter_location_entities(nodes, edges)

        # 统计实体类型
        entity_types = set()
        for node in nodes:
            if node.labels:
                for label in node.labels:
                    if label not in self.GENERIC_NODE_LABELS:
                        entity_types.add(label)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types)
        )
    
    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """
        获取完整图谱数据（包含详细信息）

        Args:
            graph_id: 图谱ID

        Returns:
            包含nodes和edges的字典，包括时间信息、属性等详细数据
        """
        # 使用适配器获取节点和边
        nodes = self.client.get_all_nodes(graph_id)
        edges = self.client.get_all_edges(graph_id)
        nodes, edges = self._coalesce_duplicate_entities(nodes, edges)
        nodes, edges = filter_location_entities(nodes, edges)
        simulation_memory_index = self._load_simulation_memory_index(graph_id)

        # 创建节点映射用于获取节点名称
        node_map = {}
        for node in nodes:
            node_map[node.uuid] = node.name or ""

        simulation_node_sources: Dict[str, List[Dict[str, Any]]] = {}
        edge_memory_sources: Dict[str, List[Dict[str, Any]]] = {}
        for edge in edges:
            memory_sources = self._get_simulation_memory_sources_for_edge(
                edge, simulation_memory_index
            )
            edge_uuid = getattr(edge, "uuid", "") or ""
            if edge_uuid:
                edge_memory_sources[edge_uuid] = memory_sources
            if memory_sources:
                source_uuid = getattr(edge, "source_node_uuid", "") or ""
                target_uuid = getattr(edge, "target_node_uuid", "") or ""
                if source_uuid:
                    simulation_node_sources.setdefault(source_uuid, []).extend(memory_sources)
                if target_uuid:
                    simulation_node_sources.setdefault(target_uuid, []).extend(memory_sources)

        nodes_data = []
        for node in nodes:
            memory_sources = self._unique_simulation_memory_sources(
                simulation_node_sources.get(node.uuid, [])
                + self._get_simulation_memory_sources_for_node(
                    node, simulation_memory_index
                )
            )
            is_simulation_memory = bool(memory_sources)
            is_new_simulation_memory = self._is_new_simulation_memory_node(
                node, memory_sources
            )
            labels = node.labels or []
            display_type = (
                SIMULATION_MEMORY_LABEL_KEY
                if is_new_simulation_memory
                else self._get_node_display_type(labels)
            )
            nodes_data.append({
                "uuid": node.uuid,
                "name": node.name,
                "labels": labels,
                "display_labels": self._build_display_labels(labels, is_simulation_memory),
                "display_type": display_type,
                "is_simulation_memory": is_simulation_memory,
                "is_new_simulation_memory": is_new_simulation_memory,
                "summary": node.summary or "",
                "attributes": self._sanitize_display_attributes(node.attributes),
                "created_at": node.created_at,
            })

        edges_data = []
        for edge in edges:
            is_simulation_memory = bool(edge_memory_sources.get(edge.uuid, []))
            edges_data.append({
                "uuid": edge.uuid,
                "name": edge.name or "",
                "fact": edge.fact or "",
                "fact_type": edge.name or "",
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "source_node_name": node_map.get(edge.source_node_uuid, ""),
                "target_node_name": node_map.get(edge.target_node_uuid, ""),
                "attributes": self._sanitize_display_attributes(edge.attributes),
                "created_at": edge.created_at,
                "valid_at": edge.valid_at,
                "invalid_at": edge.invalid_at,
                "expired_at": edge.expired_at,
                "episodes": edge.episodes or [],
                "display_labels": self._build_display_labels([], is_simulation_memory),
                "display_type": SIMULATION_MEMORY_LABEL_KEY if is_simulation_memory else None,
                "is_simulation_memory": is_simulation_memory,
            })

        # 统计实体类型分布
        entity_type_counts: Dict[str, int] = {}
        for nd in nodes_data:
            _dt = nd.get("display_type")
            if not _dt:
                _labels = nd.get("labels")
                _dt = _labels[0] if _labels else "Entity"
            entity_type_counts[_dt] = entity_type_counts.get(_dt, 0) + 1

        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
            "entity_types": entity_type_counts,
        }

    @classmethod
    def _build_display_labels(cls, labels: List[str], is_simulation_memory: bool) -> List[str]:
        """构建仅供前端展示的标签，避免覆盖底层实体类型。"""
        display_labels = list(labels or [])
        if is_simulation_memory and SIMULATION_MEMORY_LABEL_KEY not in display_labels:
            display_labels.append(SIMULATION_MEMORY_LABEL_KEY)
        return display_labels

    @classmethod
    def _get_node_display_type(cls, labels: List[str]) -> str:
        """保留节点的原始实体类型，推演标签不能替代原有类型。"""
        for label in labels or []:
            if label not in cls.GENERIC_NODE_LABELS:
                return label
        return "Entity"

    @classmethod
    def _load_simulation_memory_index(cls, graph_id: str) -> Dict[str, Any]:
        """
        从推演写回 outbox 构建展示标记索引。

        outbox 是图谱记忆写回的幂等账本，不改变图谱实体语义；这里仅用于在
        /api/graph/data 响应中标记哪些节点/边与双平台推演 episode 相关。
        """
        index = {"sources": []}
        sim_root = Config.OASIS_SIMULATION_DATA_DIR
        if not graph_id or not sim_root or not os.path.isdir(sim_root):
            return index

        for entry in os.scandir(sim_root):
            if not entry.is_dir():
                continue
            source = {
                "episode_uuids": set(),
                "terms": set(),
                "first_sent_at": None,
                "baseline_node_uuids": cls._load_simulation_memory_baseline(
                    entry.path, graph_id
                ),
            }
            outbox_path = os.path.join(entry.path, "graph_memory_outbox.json")
            if not os.path.exists(outbox_path):
                continue
            try:
                with open(outbox_path, "r", encoding="utf-8") as f:
                    outbox = json.load(f)
            except Exception as exc:
                logger.warning("读取图谱记忆 outbox 失败: path=%s, error=%s", outbox_path, exc)
                continue
            if not isinstance(outbox, dict):
                continue

            for record in outbox.values():
                if not isinstance(record, dict):
                    continue
                if record.get("graph_id") != graph_id or record.get("status") != "sent":
                    continue

                episode_uuid = record.get("episode_uuid")
                if episode_uuid:
                    source["episode_uuids"].add(str(episode_uuid))

                sent_at = cls._parse_simulation_memory_time(record.get("sent_at"))
                if sent_at and (
                    source["first_sent_at"] is None
                    or sent_at < source["first_sent_at"]
                ):
                    source["first_sent_at"] = sent_at

                activity = record.get("activity") if isinstance(record.get("activity"), dict) else {}
                for value in (
                    record.get("agent_name"),
                    record.get("payload_preview"),
                    activity.get("agent_name"),
                    activity.get("episode_text"),
                ):
                    cls._add_simulation_memory_term(source["terms"], value)
                cls._collect_simulation_memory_terms(
                    source["terms"], activity.get("action_args")
                )

            if source["episode_uuids"] or source["terms"]:
                index["sources"].append(source)

        return index

    @classmethod
    def _load_simulation_memory_baseline(
        cls, simulation_dir: str, graph_id: str
    ) -> Optional[set]:
        """读取 Step 3 启动前的节点快照；缺失时由时间戳逻辑兼容历史记录。"""
        baseline_path = os.path.join(simulation_dir, "graph_memory_baseline.json")
        if not os.path.exists(baseline_path):
            return None
        try:
            with open(baseline_path, "r", encoding="utf-8") as f:
                baseline = json.load(f)
        except Exception as exc:
            logger.warning("读取图谱记忆基线失败: path=%s, error=%s", baseline_path, exc)
            return None
        if not isinstance(baseline, dict) or baseline.get("graph_id") != graph_id:
            return None
        node_uuids = baseline.get("node_uuids")
        if not isinstance(node_uuids, list):
            return None
        return {str(node_uuid) for node_uuid in node_uuids if node_uuid}

    @staticmethod
    def _parse_simulation_memory_time(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    @classmethod
    def _collect_simulation_memory_terms(cls, terms: set, value: Any) -> None:
        """递归收集推演动作中的可匹配文本片段。"""
        if isinstance(value, dict):
            for item in value.values():
                cls._collect_simulation_memory_terms(terms, item)
            return
        if isinstance(value, list):
            for item in value:
                cls._collect_simulation_memory_terms(terms, item)
            return
        cls._add_simulation_memory_term(terms, value)

    @classmethod
    def _add_simulation_memory_term(cls, terms: set, value: Any) -> None:
        if value is None:
            return
        text = cls._normalize_simulation_memory_text(value)
        if not text:
            return
        # 过短词容易误伤普通实体；保留中文/英文实体名和内容片段。
        if len(text) >= 2:
            terms.add(text[:120])

    @staticmethod
    def _normalize_simulation_memory_text(value: Any) -> str:
        text = str(value).strip()
        if not text:
            return ""
        text = re.sub(r"\s+", "", text)
        return text

    @classmethod
    def _get_simulation_memory_sources_for_edge(
        cls, edge: Any, index: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """找出与边匹配的推演写回来源。"""
        edge_episodes = {
            str(episode)
            for episode in (getattr(edge, "episodes", []) or [])
            if episode
        }
        edge_created_at = cls._parse_simulation_memory_time(
            getattr(edge, "created_at", None)
        )
        haystack = cls._normalize_simulation_memory_text(
            " ".join([
                getattr(edge, "name", "") or "",
                getattr(edge, "fact", "") or "",
                str(getattr(edge, "attributes", {}) or ""),
            ])
        )
        matched_sources = []
        for source in index.get("sources", []):
            if edge_episodes & (source.get("episode_uuids") or set()):
                matched_sources.append(source)
                continue
            if cls._matches_simulation_memory_terms(
                haystack, edge_created_at, source
            ):
                matched_sources.append(source)
        return cls._unique_simulation_memory_sources(matched_sources)

    @classmethod
    def _get_simulation_memory_sources_for_node(
        cls, node: Any, index: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """找出仅通过内容匹配的推演记忆节点来源。"""
        node_created_at = cls._parse_simulation_memory_time(
            getattr(node, "created_at", None)
        )
        haystack = cls._normalize_simulation_memory_text(
            " ".join([
                getattr(node, "name", "") or "",
                getattr(node, "summary", "") or "",
                str(getattr(node, "attributes", {}) or ""),
            ])
        )
        return cls._unique_simulation_memory_sources([
            source
            for source in index.get("sources", [])
            if cls._matches_simulation_memory_terms(haystack, node_created_at, source)
        ])

    @classmethod
    def _matches_simulation_memory_terms(
        cls,
        haystack: str,
        item_created_at: Optional[datetime],
        source: Dict[str, Any],
    ) -> bool:
        terms = source.get("terms") or set()
        if not terms:
            return False
        first_sent_at = source.get("first_sent_at")
        if (
            first_sent_at
            and item_created_at
            and item_created_at < first_sent_at - timedelta(minutes=5)
        ):
            return False
        return cls._contains_simulation_memory_term(haystack, terms)

    @staticmethod
    def _unique_simulation_memory_sources(
        sources: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """按来源对象去重，避免同一轮推演被节点和边重复计入。"""
        unique_sources = []
        seen_source_ids = set()
        for source in sources:
            source_id = id(source)
            if source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            unique_sources.append(source)
        return unique_sources

    @classmethod
    def _is_new_simulation_memory_node(
        cls, node: Any, sources: List[Dict[str, Any]]
    ) -> bool:
        """
        判断节点是否由推演新增。

        新推演使用启动前快照精确判断；旧推演没有快照时，退化为节点创建时间与
        首次写回时间的比较，避免将明确早于推演的节点置灰。
        """
        node_uuid = str(getattr(node, "uuid", "") or "")
        node_created_at = cls._parse_simulation_memory_time(
            getattr(node, "created_at", None)
        )
        for source in sources:
            baseline_node_uuids = source.get("baseline_node_uuids")
            if baseline_node_uuids is not None:
                if node_uuid not in baseline_node_uuids:
                    return True
                continue
            first_sent_at = source.get("first_sent_at")
            if first_sent_at and node_created_at and node_created_at >= first_sent_at:
                return True
        return False

    @staticmethod
    def _contains_simulation_memory_term(haystack: str, terms: set) -> bool:
        if not haystack:
            return False
        for term in terms:
            if term and (term in haystack or haystack in term):
                return True
        return False

    @classmethod
    def _sanitize_display_attributes(cls, attributes: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """过滤不适合前端展示的内部属性。"""
        if not attributes:
            return {}
        return {
            key: value for key, value in attributes.items()
            if str(key).lower() not in cls.INTERNAL_ATTRIBUTE_KEYS
        }

    @classmethod
    def _coalesce_duplicate_entities(
        cls,
        nodes: List[Any],
        edges: List[Any],
    ) -> tuple[List[Any], List[Any]]:
        """按实体名称和主类型归并 Graphiti 重复抽取出的同名节点。"""
        if not nodes:
            return nodes, edges

        canonical_by_key: Dict[tuple[str, str], Any] = {}
        uuid_to_canonical: Dict[str, str] = {}
        merged_sources_by_uuid: Dict[str, set[str]] = {}
        canonical_nodes: List[Any] = []

        for node in nodes:
            node_uuid = getattr(node, "uuid", "") or ""
            node_name = getattr(node, "name", "") or ""
            merge_key = (cls._normalize_entity_name(node_name), cls._primary_entity_label(getattr(node, "labels", []) or []))
            if not merge_key[0]:
                if node_uuid:
                    uuid_to_canonical[node_uuid] = node_uuid
                    merged_sources_by_uuid.setdefault(node_uuid, set()).add(node_uuid)
                canonical_nodes.append(node)
                continue

            canonical = canonical_by_key.get(merge_key)
            if not canonical:
                canonical_by_key[merge_key] = node
                canonical_nodes.append(node)
                if node_uuid:
                    uuid_to_canonical[node_uuid] = node_uuid
                    merged_sources_by_uuid.setdefault(node_uuid, set()).add(node_uuid)
                continue

            canonical_uuid = getattr(canonical, "uuid", "") or node_uuid
            if node_uuid:
                uuid_to_canonical[node_uuid] = canonical_uuid
                merged_sources_by_uuid.setdefault(canonical_uuid, set()).add(node_uuid)
            if canonical_uuid:
                merged_sources_by_uuid.setdefault(canonical_uuid, set()).add(canonical_uuid)
            cls._merge_node_into_canonical(canonical, node)

        for node in canonical_nodes:
            node_uuid = getattr(node, "uuid", "") or ""
            if not node_uuid:
                continue
            source_uuids = sorted(merged_sources_by_uuid.get(node_uuid, {node_uuid}))
            if len(source_uuids) <= 1:
                continue
            attributes = dict(getattr(node, "attributes", {}) or {})
            attributes["merged_duplicate_uuids"] = source_uuids
            setattr(node, "attributes", attributes)

        redirected_edges = cls._redirect_edges_to_canonical(edges, uuid_to_canonical)
        return canonical_nodes, redirected_edges

    @classmethod
    def _normalize_entity_name(cls, name: str) -> str:
        """实体名称归一化，用于识别 Graphiti 跨批次重复实体。"""
        normalized = str(name or "").strip().casefold()
        normalized = re.sub(r"\s+", "", normalized)
        normalized = re.sub(r"[《》“”\"'‘’`·•・,，.。:：;；!！?？\\-—_()（）\\[\\]【】{}<>]+", "", normalized)
        return normalized

    @classmethod
    def _primary_entity_label(cls, labels: List[str]) -> str:
        """提取实体主类型；没有主类型时回退到 Entity。"""
        for label in labels or []:
            if label not in cls.GENERIC_NODE_LABELS:
                return str(label)
        return "Entity"

    @classmethod
    def _merge_node_into_canonical(cls, canonical: Any, duplicate: Any) -> None:
        """把重复节点的信息合并到 canonical 节点。"""
        canonical.labels = cls._merge_unique_values(getattr(canonical, "labels", []) or [], getattr(duplicate, "labels", []) or [])

        canonical_summary = getattr(canonical, "summary", "") or ""
        duplicate_summary = getattr(duplicate, "summary", "") or ""
        if len(duplicate_summary) > len(canonical_summary):
            canonical.summary = duplicate_summary

        canonical.attributes = cls._merge_attribute_dicts(
            getattr(canonical, "attributes", {}) or {},
            getattr(duplicate, "attributes", {}) or {},
        )

        canonical_created_at = getattr(canonical, "created_at", None)
        duplicate_created_at = getattr(duplicate, "created_at", None)
        if duplicate_created_at and (not canonical_created_at or str(duplicate_created_at) < str(canonical_created_at)):
            canonical.created_at = duplicate_created_at

    @classmethod
    def _merge_attribute_dicts(cls, first: Dict[str, Any], second: Dict[str, Any]) -> Dict[str, Any]:
        """合并节点属性，保留非空信息并对冲突值做列表化。"""
        merged = dict(first or {})
        for key, value in (second or {}).items():
            if value in (None, "", [], {}):
                continue
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
                continue
            if merged[key] == value:
                continue
            merged[key] = cls._merge_unique_values(
                merged[key] if isinstance(merged[key], list) else [merged[key]],
                value if isinstance(value, list) else [value],
            )
        return merged

    @classmethod
    def _merge_unique_values(cls, first: List[Any], second: List[Any]) -> List[Any]:
        """按字符串表示保持顺序去重。"""
        values = []
        seen = set()
        for value in [*(first or []), *(second or [])]:
            marker = str(value)
            if marker in seen:
                continue
            seen.add(marker)
            values.append(value)
        return values

    @classmethod
    def _redirect_edges_to_canonical(cls, edges: List[Any], uuid_to_canonical: Dict[str, str]) -> List[Any]:
        """把边端点改写到 canonical 节点，并去掉完全重复边。"""
        redirected = []
        seen = set()
        for edge in edges or []:
            source_uuid = uuid_to_canonical.get(getattr(edge, "source_node_uuid", ""), getattr(edge, "source_node_uuid", ""))
            target_uuid = uuid_to_canonical.get(getattr(edge, "target_node_uuid", ""), getattr(edge, "target_node_uuid", ""))
            if not source_uuid or not target_uuid:
                continue

            edge.source_node_uuid = source_uuid
            edge.target_node_uuid = target_uuid
            edge_key = (
                source_uuid,
                target_uuid,
                getattr(edge, "name", "") or getattr(edge, "fact_type", ""),
                getattr(edge, "fact", "") or "",
            )
            if edge_key in seen:
                continue
            seen.add(edge_key)
            redirected.append(edge)
        return redirected
    
    def delete_graph(self, graph_id: str):
        """删除图谱"""
        self.client.delete_graph(graph_id)
