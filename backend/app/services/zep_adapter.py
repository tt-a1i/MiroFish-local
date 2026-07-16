"""
Zep 客户端适配器接口

定义统一的 Zep 客户端抽象接口，支持 cloud/graphiti 双实现切换。
MVP 阶段目标：不依赖 Zep Cloud，跑通「建图 → 读实体 → 搜索 → 报告」核心链路。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ============================================================
# 数据结构定义
# ============================================================

@dataclass
class GraphNode:
    """图谱节点（对齐 zep-cloud Node 结构）"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    created_at: Optional[str] = None


@dataclass
class GraphEdge:
    """图谱边（对齐 zep-cloud Edge 结构）"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    attributes: Dict[str, Any]
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    episodes: List[str] = field(default_factory=list)
    fact_type: Optional[str] = None


@dataclass
class SearchResult:
    """搜索结果"""
    nodes: List[GraphNode]
    edges: List[GraphEdge]


@dataclass
class EpisodeStatus:
    """Episode 处理状态"""
    uuid: str
    processed: bool


# ============================================================
# 适配器抽象接口
# ============================================================

class ZepClientAdapter(ABC):
    """
    统一的 Zep 客户端接口

    实现类：
    - ZepCloudClient: 包装现有 zep-cloud SDK
    - GraphitiClient: 本地 Graphiti + Neo4j 实现

    MVP 范围：
    - create_graph: 创建图谱
    - set_ontology: 设置本体（Graphiti 可 no-op）
    - add_episode: 添加单条 episode
    - add_episode_batch: 批量添加
    - search: 语义/混合搜索
    - get_all_nodes: 获取图谱所有节点
    - get_all_edges: 获取图谱所有边
    - get_node: 获取单个节点
    - get_node_edges: 获取节点相关的边
    - delete_graph: 删除图谱
    - wait_for_episode: 等待 episode 处理完成（Graphiti 同步处理，直接返回）
    """

    # ==================== Graph 操作 ====================

    @abstractmethod
    def create_graph(self, graph_id: str, name: str, description: str) -> None:
        """
        创建知识图谱

        Args:
            graph_id: 图谱唯一标识
            name: 图谱名称
            description: 图谱描述
        """
        ...

    @abstractmethod
    def delete_graph(self, graph_id: str) -> None:
        """
        删除图谱

        Args:
            graph_id: 图谱ID
        """
        ...

    @abstractmethod
    def set_ontology(
        self,
        graph_ids: List[str],
        entities: Optional[Dict[str, Any]] = None,
        edges: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        设置图谱本体（实体类型、边类型定义）

        当前约定：
        - Zep Cloud：直接下发到云端图谱
        - Graphiti：缓存后在 episode ingestion 中注入 entity_types / edge_types / edge_type_map

        Args:
            graph_ids: 图谱ID列表
            entities: 实体类型定义 {type_name: EntityModelClass}
            edges: 边类型定义 {edge_name: (EdgeModelClass, [source_targets])}
        """
        ...

    # ==================== Episode 操作 ====================

    @abstractmethod
    def add_episode(
        self,
        graph_id: str,
        data: str,
        episode_type: str = "text",
        reference_time: Optional[datetime] = None,
    ) -> str:
        """
        添加单条 episode 到图谱

        Args:
            graph_id: 图谱ID
            data: episode 内容
            episode_type: 类型，默认 "text"

        Returns:
            episode UUID
        """
        ...

    @abstractmethod
    def add_episode_batch(
        self,
        graph_id: str,
        episodes: List[Dict[str, Any]]
    ) -> List[str]:
        """
        批量添加 episode 到图谱

        Args:
            graph_id: 图谱ID
            episodes: episode 列表，每项包含 {"data": str, "type": str}

        Returns:
            episode UUID 列表
        """
        ...

    @abstractmethod
    def get_episode_status(self, episode_uuid: str) -> EpisodeStatus:
        """
        获取 episode 处理状态

        Args:
            episode_uuid: episode UUID

        Returns:
            EpisodeStatus 包含 uuid 和 processed 状态
        """
        ...

    def wait_for_episode(self, episode_uuid: str, timeout: int = 300) -> bool:
        """
        等待 episode 处理完成

        默认实现：轮询 get_episode_status 直到 processed=True 或超时。
        Graphiti 实现可覆写为直接返回 True（同步处理）。

        Args:
            episode_uuid: episode UUID
            timeout: 超时秒数

        Returns:
            是否处理完成
        """
        import time
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = self.get_episode_status(episode_uuid)
            if status.processed:
                return True
            time.sleep(2)
        return False

    # ==================== Node 操作 ====================

    @abstractmethod
    def get_all_nodes(self, graph_id: str) -> List[GraphNode]:
        """
        获取图谱的所有节点

        Args:
            graph_id: 图谱ID

        Returns:
            节点列表
        """
        ...

    @abstractmethod
    def get_node(self, graph_id: str, node_uuid: str) -> Optional[GraphNode]:
        """
        获取单个节点详情

        Args:
            node_uuid: 节点 UUID

        Returns:
            节点对象，不存在时返回 None
        """
        ...

    @abstractmethod
    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[GraphEdge]:
        """
        获取节点的所有相关边

        Args:
            node_uuid: 节点 UUID

        Returns:
            边列表（包括以该节点为 source 或 target 的边）
        """
        ...

    # ==================== Edge 操作 ====================

    @abstractmethod
    def get_all_edges(self, graph_id: str) -> List[GraphEdge]:
        """
        获取图谱的所有边

        Args:
            graph_id: 图谱ID

        Returns:
            边列表
        """
        ...

    # ==================== Search 操作 ====================

    @abstractmethod
    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
        reranker: str = "cross_encoder"
    ) -> SearchResult:
        """
        图谱混合搜索

        Args:
            graph_id: 图谱ID
            query: 搜索查询
            limit: 返回结果数量限制
            scope: 搜索范围 - "edges" | "nodes" | "both"
            reranker: 重排序策略 - "cross_encoder" | "rrf" | "none"

        Returns:
            SearchResult 包含匹配的 nodes 和 edges
        """
        ...
