"""
Zep检索工具服务
封装图谱搜索、节点读取、边查询等工具，供Report Agent使用

核心检索工具（优化后）：
1. InsightForge（深度洞察检索）- 最强大的混合检索，自动生成子问题并多维度检索
2. PanoramaSearch（广度搜索）- 获取全貌，包括过期内容
3. QuickSearch（简单搜索）- 快速检索

支持双后端：
- Zep Cloud (默认)
- Graphiti + Neo4j 本地部署
"""

import time
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from ..config import Config
from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..utils.llm_routing import clamp_concurrency
from .zep_factory import get_zep_client
from .zep_adapter import ZepClientAdapter
from .graph_builder import GraphBuilderService
from .location_entity_filter import filter_location_entities

logger = get_logger('mirofish.zep_tools')


@dataclass
class SearchResult:
    """搜索结果"""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count
        }
    
    def to_text(self) -> str:
        """转换为文本格式，供LLM理解"""
        text_parts = [f"搜索查询: {self.query}", f"找到 {self.total_count} 条相关信息"]
        
        if self.facts:
            text_parts.append("\n### 相关事实:")
            for i, fact in enumerate(self.facts, 1):
                text_parts.append(f"{i}. {fact}")
        
        return "\n".join(text_parts)


@dataclass
class NodeInfo:
    """节点信息"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes
        }
    
    def to_text(self) -> str:
        """转换为文本格式"""
        entity_type = next((l for l in self.labels if l not in ["Entity", "Node"]), "未知类型")
        return f"实体: {self.name} (类型: {entity_type})\n摘要: {self.summary}"


@dataclass
class EdgeInfo:
    """边信息"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    # 时间信息
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
            "created_at": self.created_at,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at
        }
    
    def to_text(self, include_temporal: bool = False) -> str:
        """转换为文本格式"""
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        base_text = f"关系: {source} --[{self.name}]--> {target}\n事实: {self.fact}"
        
        if include_temporal:
            valid_at = self.valid_at or "未知"
            invalid_at = self.invalid_at or "至今"
            base_text += f"\n时效: {valid_at} - {invalid_at}"
            if self.expired_at:
                base_text += f" (已过期: {self.expired_at})"
        
        return base_text
    
    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        return self.expired_at is not None
    
    @property
    def is_invalid(self) -> bool:
        """是否已失效"""
        return self.invalid_at is not None


@dataclass
class InsightForgeResult:
    """
    深度洞察检索结果 (InsightForge)
    包含多个子问题的检索结果，以及综合分析
    """
    query: str
    simulation_requirement: str
    sub_queries: List[str]
    
    # 各维度检索结果
    semantic_facts: List[str] = field(default_factory=list)  # 语义搜索结果
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)  # 实体洞察
    relationship_chains: List[str] = field(default_factory=list)  # 关系链
    
    # 统计信息
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "simulation_requirement": self.simulation_requirement,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships
        }
    
    def to_text(self) -> str:
        """转换为详细的文本格式，供LLM理解"""
        text_parts = [
            f"## 未来预测深度分析",
            f"分析问题: {self.query}",
            f"预测场景: {self.simulation_requirement}",
            f"\n### 预测数据统计",
            f"- 相关预测事实: {self.total_facts}条",
            f"- 涉及实体: {self.total_entities}个",
            f"- 关系链: {self.total_relationships}条"
        ]
        
        # 子问题
        if self.sub_queries:
            text_parts.append(f"\n### 分析的子问题")
            for i, sq in enumerate(self.sub_queries, 1):
                text_parts.append(f"{i}. {sq}")
        
        # 语义搜索结果
        if self.semantic_facts:
            text_parts.append(f"\n### 【关键事实】(请在报告中引用这些原文)")
            for i, fact in enumerate(self.semantic_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # 实体洞察
        if self.entity_insights:
            text_parts.append(f"\n### 【核心实体】")
            for entity in self.entity_insights:
                text_parts.append(f"- **{entity.get('name', '未知')}** ({entity.get('type', '实体')})")
                if entity.get('summary'):
                    text_parts.append(f"  摘要: \"{entity.get('summary')}\"")
                if entity.get('related_facts'):
                    text_parts.append(f"  相关事实: {len(entity.get('related_facts', []))}条")
        
        # 关系链
        if self.relationship_chains:
            text_parts.append(f"\n### 【关系链】")
            for chain in self.relationship_chains:
                text_parts.append(f"- {chain}")
        
        return "\n".join(text_parts)


@dataclass
class PanoramaResult:
    """
    广度搜索结果 (Panorama)
    包含所有相关信息，包括过期内容
    """
    query: str
    
    # 全部节点
    all_nodes: List[NodeInfo] = field(default_factory=list)
    # 全部边（包括过期的）
    all_edges: List[EdgeInfo] = field(default_factory=list)
    # 当前有效的事实
    active_facts: List[str] = field(default_factory=list)
    # 已过期/失效的事实（历史记录）
    historical_facts: List[str] = field(default_factory=list)
    
    # 统计
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "all_nodes": [n.to_dict() for n in self.all_nodes],
            "all_edges": [e.to_dict() for e in self.all_edges],
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count
        }
    
    def to_text(self) -> str:
        """转换为文本格式（完整版本，不截断）"""
        text_parts = [
            f"## 广度搜索结果（未来全景视图）",
            f"查询: {self.query}",
            f"\n### 统计信息",
            f"- 总节点数: {self.total_nodes}",
            f"- 总边数: {self.total_edges}",
            f"- 当前有效事实: {self.active_count}条",
            f"- 历史/过期事实: {self.historical_count}条"
        ]
        
        # 当前有效的事实（完整输出，不截断）
        if self.active_facts:
            text_parts.append(f"\n### 【当前有效事实】(模拟结果原文)")
            for i, fact in enumerate(self.active_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # 历史/过期事实（完整输出，不截断）
        if self.historical_facts:
            text_parts.append(f"\n### 【历史/过期事实】(演变过程记录)")
            for i, fact in enumerate(self.historical_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # 关键实体（完整输出，不截断）
        if self.all_nodes:
            text_parts.append(f"\n### 【涉及实体】")
            for node in self.all_nodes:
                entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "实体")
                text_parts.append(f"- **{node.name}** ({entity_type})")
        
        return "\n".join(text_parts)


@dataclass
class AgentInterview:
    """单个Agent的采访结果"""
    agent_name: str
    agent_role: str  # 角色类型（如：学生、教师、媒体等）
    agent_bio: str  # 简介
    question: str  # 采访问题
    response: str  # 采访回答
    key_quotes: List[str] = field(default_factory=list)  # 关键引言
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "agent_bio": self.agent_bio,
            "question": self.question,
            "response": self.response,
            "key_quotes": self.key_quotes
        }
    
    def to_text(self) -> str:
        text = f"**{self.agent_name}** ({self.agent_role})\n"
        # 显示完整的agent_bio，不截断
        text += f"_简介: {self.agent_bio}_\n\n"
        text += f"**Q:** {self.question}\n\n"
        text += f"**A:** {self.response}\n"
        if self.key_quotes:
            text += "\n**关键引言:**\n"
            for quote in self.key_quotes:
                text += f"> \"{quote}\"\n"
        return text


@dataclass
class InterviewResult:
    """
    采访结果 (Interview)
    包含多个模拟Agent的采访回答
    """
    interview_topic: str  # 采访主题
    interview_questions: List[str]  # 采访问题列表
    
    # 采访选择的Agent
    selected_agents: List[Dict[str, Any]] = field(default_factory=list)
    # 各Agent的采访回答
    interviews: List[AgentInterview] = field(default_factory=list)
    
    # 选择Agent的理由
    selection_reasoning: str = ""
    # 整合后的采访摘要
    summary: str = ""
    
    # 统计
    total_agents: int = 0
    interviewed_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "interview_topic": self.interview_topic,
            "interview_questions": self.interview_questions,
            "selected_agents": self.selected_agents,
            "interviews": [i.to_dict() for i in self.interviews],
            "selection_reasoning": self.selection_reasoning,
            "summary": self.summary,
            "total_agents": self.total_agents,
            "interviewed_count": self.interviewed_count
        }
    
    def to_text(self) -> str:
        """转换为详细的文本格式，供LLM理解和报告引用"""
        text_parts = [
            f"## 🎤 深度采访报告",
            f"**采访主题:** {self.interview_topic}",
            f"**采访人数:** {self.interviewed_count} / {self.total_agents} 位模拟Agent",
            f"\n### 采访对象选择理由",
            f"{self.selection_reasoning}",
            f"\n---"
        ]
        
        # 各Agent的采访内容
        if self.interviews:
            text_parts.append(f"\n### 采访实录")
            for i, interview in enumerate(self.interviews, 1):
                text_parts.append(f"\n#### 采访 #{i}: {interview.agent_name}")
                text_parts.append(interview.to_text())
                text_parts.append("\n---")
        
        # 采访摘要
        if self.summary:
            text_parts.append(f"\n### 采访摘要与核心观点")
            text_parts.append(self.summary)
        
        return "\n".join(text_parts)


class ZepToolsService:
    """
    Zep检索工具服务
    
    【核心检索工具 - 优化后】
    1. insight_forge - 深度洞察检索（最强大，自动生成子问题，多维度检索）
    2. panorama_search - 广度搜索（获取全貌，包括过期内容）
    3. quick_search - 简单搜索（快速检索）
    4. interview_agents - 深度采访（采访模拟Agent，获取多视角观点）
    
    【基础工具】
    - search_graph - 图谱语义搜索
    - get_all_nodes - 获取图谱所有节点
    - get_all_edges - 获取图谱所有边（含时间信息）
    - get_node_detail - 获取节点详细信息
    - get_node_edges - 获取节点相关的边
    - get_entities_by_type - 按类型获取实体
    - get_entity_summary - 获取实体的关系摘要
    """
    
    # 重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        llm_client: Optional[LLMClient] = None,
        backend: Optional[str] = None,
    ):
        """初始化Zep工具服务（使用单例）"""
        # 使用单例获取适配器（避免重复初始化）
        self.client: ZepClientAdapter = get_zep_client(backend=backend)
        # LLM客户端用于InsightForge生成子问题
        self._llm_client = llm_client
        self.tool_concurrency = clamp_concurrency(Config.REPORT_TOOL_CONCURRENCY, 3, maximum=8)
        logger.info("ZepToolsService 初始化完成")
    
    @property
    def llm(self) -> LLMClient:
        """延迟初始化LLM客户端"""
        if self._llm_client is None:
            self._llm_client = LLMClient(prefer_boost=True)
        return self._llm_client
    
    def _call_with_retry(self, func, operation_name: str, max_retries: int = None):
        """带重试机制的API调用"""
        max_retries = max_retries or self.MAX_RETRIES
        last_exception = None
        delay = self.RETRY_DELAY
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name} 第 {attempt + 1} 次尝试失败: {str(e)[:100]}, "
                        f"{delay:.1f}秒后重试..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"Zep {operation_name} 在 {max_retries} 次尝试后仍失败: {str(e)}")
        
        raise last_exception
    
    def search_graph(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        图谱语义搜索
        
        使用混合搜索（语义+BM25）在图谱中搜索相关信息。
        如果Zep Cloud的search API不可用，则降级为本地关键词匹配。
        
        Args:
            graph_id: 图谱ID (Standalone Graph)
            query: 搜索查询
            limit: 返回结果数量
            scope: 搜索范围，"edges" 或 "nodes"
            
        Returns:
            SearchResult: 搜索结果
        """
        logger.info(f"图谱搜索: graph_id={graph_id}, query={query[:50]}...")
        
        # 使用适配器搜索 API
        try:
            search_results = self._call_with_retry(
                func=lambda: self.client.search(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                    scope=scope,
                    reranker="cross_encoder"
                ),
                operation_name=f"图谱搜索(graph={graph_id})"
            )

            facts = []
            edges = []
            nodes = []

            # 解析边搜索结果（适配器返回 GraphEdge 对象列表）
            if search_results.edges:
                for edge in search_results.edges:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges.append({
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    })

            # 解析节点搜索结果（适配器返回 GraphNode 对象列表）
            if search_results.nodes:
                for node in search_results.nodes:
                    nodes.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    })
                    # 节点摘要也算作事实
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"搜索完成: 找到 {len(facts)} 条相关事实")
            
            return SearchResult(
                facts=facts,
                edges=edges,
                nodes=nodes,
                query=query,
                total_count=len(facts)
            )
            
        except Exception as e:
            logger.warning(f"Zep Search API失败，降级为本地搜索: {str(e)}")
            # 降级：使用本地关键词匹配搜索
            return self._local_search(graph_id, query, limit, scope)
    
    def _local_search(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        本地关键词匹配搜索（作为Zep Search API的降级方案）
        
        获取所有边/节点，然后在本地进行关键词匹配
        
        Args:
            graph_id: 图谱ID
            query: 搜索查询
            limit: 返回结果数量
            scope: 搜索范围
            
        Returns:
            SearchResult: 搜索结果
        """
        logger.info(f"使用本地搜索: query={query[:30]}...")
        
        facts = []
        edges_result = []
        nodes_result = []
        
        # 提取查询关键词（简单分词）
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def match_score(text: str) -> int:
            """计算文本与查询的匹配分数"""
            if not text:
                return 0
            text_lower = text.lower()
            # 完全匹配查询
            if query_lower in text_lower:
                return 100
            # 关键词匹配
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score
        
        try:
            if scope in ["edges", "both"]:
                # 获取所有边并匹配
                all_edges = self.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.fact) + match_score(edge.name)
                    if score > 0:
                        scored_edges.append((score, edge))
                
                # 按分数排序
                scored_edges.sort(key=lambda x: x[0], reverse=True)
                
                for score, edge in scored_edges[:limit]:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges_result.append({
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    })
            
            if scope in ["nodes", "both"]:
                # 获取所有节点并匹配
                all_nodes = self.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.name) + match_score(node.summary)
                    if score > 0:
                        scored_nodes.append((score, node))
                
                scored_nodes.sort(key=lambda x: x[0], reverse=True)
                
                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    })
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"本地搜索完成: 找到 {len(facts)} 条相关事实")
            
        except Exception as e:
            logger.error(f"本地搜索失败: {str(e)}")
        
        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )
    
    def get_all_nodes(self, graph_id: str) -> List[NodeInfo]:
        """
        获取图谱的所有节点

        Args:
            graph_id: 图谱ID

        Returns:
            节点列表
        """
        logger.info(f"获取图谱 {graph_id} 的所有节点...")

        nodes = self._call_with_retry(
            func=lambda: self.client.get_all_nodes(graph_id),
            operation_name=f"获取节点(graph={graph_id})"
        )
        nodes, _ = GraphBuilderService._coalesce_duplicate_entities(nodes, [])
        nodes, _ = filter_location_entities(nodes, [])

        result = []
        for node in nodes:
            result.append(NodeInfo(
                uuid=node.uuid,
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            ))

        logger.info(f"获取到 {len(result)} 个节点")
        return result
    
    def get_all_edges(self, graph_id: str, include_temporal: bool = True) -> List[EdgeInfo]:
        """
        获取图谱的所有边（包含时间信息）

        Args:
            graph_id: 图谱ID
            include_temporal: 是否包含时间信息（默认True）

        Returns:
            边列表（包含created_at, valid_at, invalid_at, expired_at）
        """
        logger.info(f"获取图谱 {graph_id} 的所有边...")

        edges = self._call_with_retry(
            func=lambda: self.client.get_all_edges(graph_id),
            operation_name=f"获取边(graph={graph_id})"
        )
        nodes = self._call_with_retry(
            func=lambda: self.client.get_all_nodes(graph_id),
            operation_name=f"获取节点(graph={graph_id})"
        )
        nodes, edges = GraphBuilderService._coalesce_duplicate_entities(nodes, edges)
        nodes, edges = filter_location_entities(nodes, edges)

        result = []
        for edge in edges:
            edge_info = EdgeInfo(
                uuid=edge.uuid,
                name=edge.name or "",
                fact=edge.fact or "",
                source_node_uuid=edge.source_node_uuid or "",
                target_node_uuid=edge.target_node_uuid or ""
            )

            # 添加时间信息（适配器已有这些字段）
            if include_temporal:
                edge_info.created_at = edge.created_at
                edge_info.valid_at = edge.valid_at
                edge_info.invalid_at = edge.invalid_at
                edge_info.expired_at = edge.expired_at

            result.append(edge_info)

        logger.info(f"获取到 {len(result)} 条边")
        return result
    
    def get_node_detail(self, graph_id: str, node_uuid: str) -> Optional[NodeInfo]:
        """
        获取单个节点的详细信息

        Args:
            graph_id: 图谱ID
            node_uuid: 节点UUID

        Returns:
            节点信息或None
        """
        logger.info(f"获取节点详情: {node_uuid[:8]}...")

        try:
            node = self._call_with_retry(
                func=lambda: self.client.get_node(graph_id, node_uuid),
                operation_name=f"获取节点详情(uuid={node_uuid[:8]}...)"
            )

            if not node:
                return None

            return NodeInfo(
                uuid=node.uuid,
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            )
        except Exception as e:
            logger.error(f"获取节点详情失败: {str(e)}")
            return None
    
    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[EdgeInfo]:
        """
        获取节点相关的所有边
        
        通过获取图谱所有边，然后过滤出与指定节点相关的边
        
        Args:
            graph_id: 图谱ID
            node_uuid: 节点UUID
            
        Returns:
            边列表
        """
        logger.info(f"获取节点 {node_uuid[:8]}... 的相关边")
        
        try:
            # 获取图谱所有边，然后过滤
            all_edges = self.get_all_edges(graph_id)
            
            result = []
            for edge in all_edges:
                # 检查边是否与指定节点相关（作为源或目标）
                if edge.source_node_uuid == node_uuid or edge.target_node_uuid == node_uuid:
                    result.append(edge)
            
            logger.info(f"找到 {len(result)} 条与节点相关的边")
            return result
            
        except Exception as e:
            logger.warning(f"获取节点边失败: {str(e)}")
            return []
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str
    ) -> List[NodeInfo]:
        """
        按类型获取实体
        
        Args:
            graph_id: 图谱ID
            entity_type: 实体类型（如 Student, PublicFigure 等）
            
        Returns:
            符合类型的实体列表
        """
        logger.info(f"获取类型为 {entity_type} 的实体...")
        
        all_nodes = self.get_all_nodes(graph_id)
        
        filtered = []
        for node in all_nodes:
            # 检查labels是否包含指定类型
            if entity_type in node.labels:
                filtered.append(node)
        
        logger.info(f"找到 {len(filtered)} 个 {entity_type} 类型的实体")
        return filtered
    
    def get_entity_summary(
        self, 
        graph_id: str, 
        entity_name: str
    ) -> Dict[str, Any]:
        """
        获取指定实体的关系摘要
        
        搜索与该实体相关的所有信息，并生成摘要
        
        Args:
            graph_id: 图谱ID
            entity_name: 实体名称
            
        Returns:
            实体摘要信息
        """
        logger.info(f"获取实体 {entity_name} 的关系摘要...")
        
        # 先搜索该实体相关的信息
        search_result = self.search_graph(
            graph_id=graph_id,
            query=entity_name,
            limit=20
        )
        
        # 尝试在所有节点中找到该实体
        all_nodes = self.get_all_nodes(graph_id)
        entity_node = None
        for node in all_nodes:
            if node.name.lower() == entity_name.lower():
                entity_node = node
                break
        
        related_edges = []
        if entity_node:
            # 传入graph_id参数
            related_edges = self.get_node_edges(graph_id, entity_node.uuid)
        
        return {
            "entity_name": entity_name,
            "entity_info": entity_node.to_dict() if entity_node else None,
            "related_facts": search_result.facts,
            "related_edges": [e.to_dict() for e in related_edges],
            "total_relations": len(related_edges)
        }
    
    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """
        获取图谱的统计信息
        
        Args:
            graph_id: 图谱ID
            
        Returns:
            统计信息
        """
        logger.info(f"获取图谱 {graph_id} 的统计信息...")
        
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)
        
        # 统计实体类型分布
        entity_types = {}
        for node in nodes:
            for label in node.labels:
                if label not in ["Entity", "Node"]:
                    entity_types[label] = entity_types.get(label, 0) + 1
        
        # 统计关系类型分布
        relation_types = {}
        for edge in edges:
            relation_types[edge.name] = relation_types.get(edge.name, 0) + 1
        
        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types
        }
    
    def get_simulation_context(
        self, 
        graph_id: str,
        simulation_requirement: str,
        limit: int = 30
    ) -> Dict[str, Any]:
        """
        获取模拟相关的上下文信息
        
        综合搜索与模拟需求相关的所有信息
        
        Args:
            graph_id: 图谱ID
            simulation_requirement: 模拟需求描述
            limit: 每类信息的数量限制
            
        Returns:
            模拟上下文信息
        """
        logger.info(f"获取模拟上下文: {simulation_requirement[:50]}...")
        
        # 搜索与模拟需求相关的信息
        search_result = self.search_graph(
            graph_id=graph_id,
            query=simulation_requirement,
            limit=limit
        )
        
        # 获取图谱统计
        stats = self.get_graph_statistics(graph_id)
        
        # 获取所有实体节点
        all_nodes = self.get_all_nodes(graph_id)
        
        # 筛选有实际类型的实体（非纯Entity节点）
        entities = []
        for node in all_nodes:
            custom_labels = [l for l in node.labels if l not in ["Entity", "Node"]]
            if custom_labels:
                entities.append({
                    "name": node.name,
                    "type": custom_labels[0],
                    "summary": node.summary
                })
        
        return {
            "simulation_requirement": simulation_requirement,
            "related_facts": search_result.facts,
            "graph_statistics": stats,
            "entities": entities[:limit],  # 限制数量
            "total_entities": len(entities)
        }
    
    # ========== 核心检索工具（优化后） ==========
    
    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_sub_queries: int = 5
    ) -> InsightForgeResult:
        """
        【InsightForge - 深度洞察检索】
        
        最强大的混合检索函数，自动分解问题并多维度检索：
        1. 使用LLM将问题分解为多个子问题
        2. 对每个子问题进行语义搜索
        3. 提取相关实体并获取其详细信息
        4. 追踪关系链
        5. 整合所有结果，生成深度洞察
        
        Args:
            graph_id: 图谱ID
            query: 用户问题
            simulation_requirement: 模拟需求描述
            report_context: 报告上下文（可选，用于更精准的子问题生成）
            max_sub_queries: 最大子问题数量
            
        Returns:
            InsightForgeResult: 深度洞察检索结果
        """
        logger.info(f"InsightForge 深度洞察检索: {query[:50]}...")
        
        result = InsightForgeResult(
            query=query,
            simulation_requirement=simulation_requirement,
            sub_queries=[]
        )
        
        # Step 1: 使用LLM生成子问题
        sub_queries = self._generate_sub_queries(
            query=query,
            simulation_requirement=simulation_requirement,
            report_context=report_context,
            max_queries=max_sub_queries
        )
        result.sub_queries = sub_queries
        logger.info(f"生成 {len(sub_queries)} 个子问题")
        
        # Step 2: 对每个子问题进行语义搜索
        all_facts = []
        all_edges = []
        seen_facts = set()
        
        search_jobs = [(sub_query, 15) for sub_query in sub_queries]
        search_jobs.append((query, 20))
        search_results_by_index: Dict[int, SearchResult] = {}

        def run_search(search_query: str, limit: int) -> SearchResult:
            return self.search_graph(
                graph_id=graph_id,
                query=search_query,
                limit=limit,
                scope="edges",
            )

        max_workers = min(self.tool_concurrency, len(search_jobs))
        if max_workers <= 1:
            for index, (search_query, limit) in enumerate(search_jobs):
                search_results_by_index[index] = run_search(search_query, limit)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_index = {
                    executor.submit(run_search, search_query, limit): index
                    for index, (search_query, limit) in enumerate(search_jobs)
                }
                for future in as_completed(future_to_index):
                    search_results_by_index[future_to_index[future]] = future.result()

        for index in range(len(search_jobs)):
            search_result = search_results_by_index.get(index)
            if not search_result:
                continue
            for fact in search_result.facts:
                if fact not in seen_facts:
                    all_facts.append(fact)
                    seen_facts.add(fact)
            all_edges.extend(search_result.edges)
        
        result.semantic_facts = all_facts
        result.total_facts = len(all_facts)
        
        # Step 3: 从边中提取相关实体UUID，只获取这些实体的信息（不获取全部节点）
        entity_uuids = set()
        for edge_data in all_edges:
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                if source_uuid:
                    entity_uuids.add(source_uuid)
                if target_uuid:
                    entity_uuids.add(target_uuid)
        
        # 获取所有相关实体的详情（不限制数量，完整输出）
        entity_insights = []
        node_map = {}  # 用于后续关系链构建
        
        entity_uuid_list = [uuid for uuid in entity_uuids if uuid]

        def fetch_node(uuid: str) -> Optional[NodeInfo]:
            try:
                return self.get_node_detail(graph_id, uuid)
            except Exception as exc:
                logger.debug(f"获取节点 {uuid} 失败: {exc}")
                return None

        node_results_by_uuid: Dict[str, NodeInfo] = {}
        max_node_workers = min(self.tool_concurrency, len(entity_uuid_list))
        if max_node_workers <= 1:
            for uuid in entity_uuid_list:
                node = fetch_node(uuid)
                if node:
                    node_results_by_uuid[uuid] = node
        else:
            with ThreadPoolExecutor(max_workers=max_node_workers) as executor:
                future_to_uuid = {executor.submit(fetch_node, uuid): uuid for uuid in entity_uuid_list}
                for future in as_completed(future_to_uuid):
                    uuid = future_to_uuid[future]
                    node = future.result()
                    if node:
                        node_results_by_uuid[uuid] = node

        for uuid in entity_uuid_list:
            node = node_results_by_uuid.get(uuid)
            if not node:
                continue
            node_map[uuid] = node
            entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "实体")
            related_facts = [
                f for f in all_facts
                if node.name and node.name.lower() in f.lower()
            ]
            entity_insights.append({
                "uuid": node.uuid,
                "name": node.name,
                "type": entity_type,
                "summary": node.summary,
                "related_facts": related_facts
            })
        
        result.entity_insights = entity_insights
        result.total_entities = len(entity_insights)
        
        # Step 4: 构建所有关系链（不限制数量）
        relationship_chains = []
        for edge_data in all_edges:  # 处理所有边，不截断
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                relation_name = edge_data.get('name', '')
                
                source_name = node_map.get(source_uuid, NodeInfo('', '', [], '', {})).name or source_uuid[:8]
                target_name = node_map.get(target_uuid, NodeInfo('', '', [], '', {})).name or target_uuid[:8]
                
                chain = f"{source_name} --[{relation_name}]--> {target_name}"
                if chain not in relationship_chains:
                    relationship_chains.append(chain)
        
        result.relationship_chains = relationship_chains
        result.total_relationships = len(relationship_chains)
        
        logger.info(f"InsightForge完成: {result.total_facts}条事实, {result.total_entities}个实体, {result.total_relationships}条关系")
        return result
    
    def _generate_sub_queries(
        self,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_queries: int = 5
    ) -> List[str]:
        """
        使用LLM生成子问题
        
        将复杂问题分解为多个可以独立检索的子问题
        """
        system_prompt = """你是一个专业的问题分析专家。你的任务是将一个复杂问题分解为多个可以在模拟世界中独立观察的子问题。

要求：
1. 每个子问题应该足够具体，可以在模拟世界中找到相关的Agent行为或事件
2. 子问题应该覆盖原问题的不同维度（如：谁、什么、为什么、怎么样、何时、何地）
3. 子问题应该与模拟场景相关
4. 返回JSON格式：{"sub_queries": ["子问题1", "子问题2", ...]}"""

        user_prompt = f"""模拟需求背景：
{simulation_requirement}

{f"报告上下文：{report_context[:500]}" if report_context else ""}

请将以下问题分解为{max_queries}个子问题：
{query}

返回JSON格式的子问题列表。"""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            sub_queries = response.get("sub_queries", [])
            # 确保是字符串列表
            return [str(sq) for sq in sub_queries[:max_queries]]
            
        except Exception as e:
            logger.warning(f"生成子问题失败: {str(e)}，使用默认子问题")
            # 降级：返回基于原问题的变体
            return [
                query,
                f"{query} 的主要参与者",
                f"{query} 的原因和影响",
                f"{query} 的发展过程"
            ][:max_queries]
    
    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
        limit: int = 50
    ) -> PanoramaResult:
        """
        【PanoramaSearch - 广度搜索】
        
        获取全貌视图，包括所有相关内容和历史/过期信息：
        1. 获取所有相关节点
        2. 获取所有边（包括已过期/失效的）
        3. 分类整理当前有效和历史信息
        
        这个工具适用于需要了解事件全貌、追踪演变过程的场景。
        
        Args:
            graph_id: 图谱ID
            query: 搜索查询（用于相关性排序）
            include_expired: 是否包含过期内容（默认True）
            limit: 返回结果数量限制
            
        Returns:
            PanoramaResult: 广度搜索结果
        """
        logger.info(f"PanoramaSearch 广度搜索: {query[:50]}...")
        
        result = PanoramaResult(query=query)
        
        # 获取所有节点
        all_nodes = self.get_all_nodes(graph_id)
        node_map = {n.uuid: n for n in all_nodes}
        result.all_nodes = all_nodes
        result.total_nodes = len(all_nodes)
        
        # 获取所有边（包含时间信息）
        all_edges = self.get_all_edges(graph_id, include_temporal=True)
        result.all_edges = all_edges
        result.total_edges = len(all_edges)
        
        # 分类事实
        active_facts = []
        historical_facts = []
        
        for edge in all_edges:
            if not edge.fact:
                continue
            
            # 为事实添加实体名称
            source_name = node_map.get(edge.source_node_uuid, NodeInfo('', '', [], '', {})).name or edge.source_node_uuid[:8]
            target_name = node_map.get(edge.target_node_uuid, NodeInfo('', '', [], '', {})).name or edge.target_node_uuid[:8]
            
            # 判断是否过期/失效
            is_historical = edge.is_expired or edge.is_invalid
            
            if is_historical:
                # 历史/过期事实，添加时间标记
                valid_at = edge.valid_at or "未知"
                invalid_at = edge.invalid_at or edge.expired_at or "未知"
                fact_with_time = f"[{valid_at} - {invalid_at}] {edge.fact}"
                historical_facts.append(fact_with_time)
            else:
                # 当前有效事实
                active_facts.append(edge.fact)
        
        # 基于查询进行相关性排序
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def relevance_score(fact: str) -> int:
            fact_lower = fact.lower()
            score = 0
            if query_lower in fact_lower:
                score += 100
            for kw in keywords:
                if kw in fact_lower:
                    score += 10
            return score
        
        # 排序并限制数量
        active_facts.sort(key=relevance_score, reverse=True)
        historical_facts.sort(key=relevance_score, reverse=True)
        
        result.active_facts = active_facts[:limit]
        result.historical_facts = historical_facts[:limit] if include_expired else []
        result.active_count = len(active_facts)
        result.historical_count = len(historical_facts)
        
        logger.info(f"PanoramaSearch完成: {result.active_count}条有效, {result.historical_count}条历史")
        return result
    
    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10
    ) -> SearchResult:
        """
        【QuickSearch - 简单搜索】
        
        快速、轻量级的检索工具：
        1. 直接调用Zep语义搜索
        2. 返回最相关的结果
        3. 适用于简单、直接的检索需求
        
        Args:
            graph_id: 图谱ID
            query: 搜索查询
            limit: 返回结果数量
            
        Returns:
            SearchResult: 搜索结果
        """
        logger.info(f"QuickSearch 简单搜索: {query[:50]}...")
        
        # 直接调用现有的search_graph方法
        result = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope="edges"
        )
        
        logger.info(f"QuickSearch完成: {result.total_count}条结果")
        return result
    
    def interview_agents(
        self,
        simulation_id: str,
        interview_requirement: str,
        simulation_requirement: str = "",
        max_agents: int = 5,
        custom_questions: List[str] = None
    ) -> InterviewResult:
        """
        【InterviewAgents - 深度采访】
        
        调用真实的OASIS采访API，采访模拟中正在运行的Agent：
        1. 自动读取人设文件，了解所有模拟Agent
        2. 使用LLM分析采访需求，智能选择最相关的Agent
        3. 使用LLM生成采访问题
        4. 调用 /api/simulation/interview/batch 接口进行真实采访（双平台同时采访）
        5. 整合所有采访结果，生成采访报告
        
        【重要】此功能需要模拟环境处于运行状态（OASIS环境未关闭）
        
        【使用场景】
        - 需要从不同角色视角了解事件看法
        - 需要收集多方意见和观点
        - 需要获取模拟Agent的真实回答（非LLM模拟）
        
        Args:
            simulation_id: 模拟ID（用于定位人设文件和调用采访API）
            interview_requirement: 采访需求描述（非结构化，如"了解学生对事件的看法"）
            simulation_requirement: 模拟需求背景（可选）
            max_agents: 最多采访的Agent数量
            custom_questions: 自定义采访问题（可选，若不提供则自动生成）
            
        Returns:
            InterviewResult: 采访结果
        """
        from .simulation_runner import SimulationRunner
        
        logger.info(f"InterviewAgents 深度采访（真实API）: {interview_requirement[:50]}...")
        
        result = InterviewResult(
            interview_topic=interview_requirement,
            interview_questions=custom_questions or []
        )
        
        # Step 1: 读取人设文件
        profiles = self._load_agent_profiles(simulation_id)
        
        if not profiles:
            logger.warning(f"未找到模拟 {simulation_id} 的人设文件")
            result.summary = "未找到可采访的Agent人设文件"
            return result
        
        result.total_agents = len(profiles)
        logger.info(f"加载到 {len(profiles)} 个Agent人设")
        
        # Step 2: 使用LLM选择要采访的Agent（返回agent_id列表）
        selected_agents, selected_indices, selection_reasoning = self._select_agents_for_interview(
            profiles=profiles,
            interview_requirement=interview_requirement,
            simulation_requirement=simulation_requirement,
            max_agents=max_agents
        )
        
        result.selected_agents = selected_agents
        result.selection_reasoning = selection_reasoning
        logger.info(f"选择了 {len(selected_agents)} 个Agent进行采访: {selected_indices}")
        
        # Step 3: 生成采访问题（如果没有提供）
        if not result.interview_questions:
            result.interview_questions = self._generate_interview_questions(
                interview_requirement=interview_requirement,
                simulation_requirement=simulation_requirement,
                selected_agents=selected_agents
            )
            logger.info(f"生成了 {len(result.interview_questions)} 个采访问题")
        
        # 将问题合并为一个采访prompt
        combined_prompt = "\n".join([f"{i+1}. {q}" for i, q in enumerate(result.interview_questions)])
        
        # 添加优化前缀，避免Agent调用工具而直接回复文本
        INTERVIEW_PROMPT_PREFIX = "结合你的人设、所有的过往记忆与行动，不调用任何工具直接用文本回复我："
        optimized_prompt = f"{INTERVIEW_PROMPT_PREFIX}{combined_prompt}"
        
        # Step 4: 优先调用真实的采访API；IPC不可用时降级为基于人设的问卷回答
        interviews_request = [
            {
                "agent_id": agent_idx,
                "prompt": optimized_prompt
            }
            for agent_idx in selected_indices
        ]

        try:
            logger.info(f"调用批量采访API（双平台）: {len(interviews_request)} 个Agent")
            
            # 调用 SimulationRunner 的批量采访方法（不传platform，双平台采访）
            api_result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=interviews_request,
                platform=None,  # 不指定platform，双平台采访
                timeout=180.0   # 双平台需要更长超时
            )
            
            logger.info(f"采访API返回: {api_result.get('interviews_count', 0)} 个结果, success={api_result.get('success')}")
            
            # 检查API调用是否成功
            if not api_result.get("success", False):
                error_msg = api_result.get("error", "未知错误")
                logger.warning(f"采访API返回失败: {error_msg}")
                api_result = self._fallback_interview_agents_from_profiles(
                    simulation_id=simulation_id,
                    interviews_request=interviews_request,
                    fallback_reason=error_msg,
                )

            self._append_api_interviews_to_result(
                result=result,
                api_result=api_result,
                selected_agents=selected_agents,
                selected_indices=selected_indices,
                combined_prompt=combined_prompt,
            )
            
        except (ValueError, TimeoutError) as e:
            logger.warning(f"采访API不可用，切换到profile问卷降级: {e}")
            try:
                api_result = self._fallback_interview_agents_from_profiles(
                    simulation_id=simulation_id,
                    interviews_request=interviews_request,
                    fallback_reason=str(e),
                )
                self._append_api_interviews_to_result(
                    result=result,
                    api_result=api_result,
                    selected_agents=selected_agents,
                    selected_indices=selected_indices,
                    combined_prompt=combined_prompt,
                )
            except Exception as fallback_e:
                logger.error(f"profile问卷降级失败: {fallback_e}")
                import traceback
                logger.error(traceback.format_exc())
                result.summary = f"采访失败：{str(e)}；profile问卷降级也失败：{fallback_e}"
                return result
        except Exception as e:
            logger.error(f"采访API调用异常，切换到profile问卷降级: {e}")
            import traceback
            logger.error(traceback.format_exc())
            try:
                api_result = self._fallback_interview_agents_from_profiles(
                    simulation_id=simulation_id,
                    interviews_request=interviews_request,
                    fallback_reason=str(e),
                )
                self._append_api_interviews_to_result(
                    result=result,
                    api_result=api_result,
                    selected_agents=selected_agents,
                    selected_indices=selected_indices,
                    combined_prompt=combined_prompt,
                )
            except Exception as fallback_e:
                logger.error(f"profile问卷降级失败: {fallback_e}")
                logger.error(traceback.format_exc())
                result.summary = f"采访过程发生错误：{str(e)}；profile问卷降级也失败：{fallback_e}"
                return result
        
        # Step 6: 生成采访摘要
        if result.interviews:
            result.summary = self._generate_interview_summary(
                interviews=result.interviews,
                interview_requirement=interview_requirement
            )
        
        logger.info(f"InterviewAgents完成: 采访了 {result.interviewed_count} 个Agent（双平台）")
        return result

    def _fallback_interview_agents_from_profiles(
        self,
        simulation_id: str,
        interviews_request: List[Dict[str, Any]],
        fallback_reason: str,
    ) -> Dict[str, Any]:
        """当OASIS IPC采访不可用时，复用Step5的人设问卷能力生成可用回答。"""
        from .agent_dialogue_service import AgentDialogueService

        logger.info(
            "使用profile问卷降级生成采访结果: simulation_id=%s, count=%s, reason=%s",
            simulation_id,
            len(interviews_request),
            fallback_reason,
        )
        return AgentDialogueService(llm_client=self.llm).interview_agents_from_profiles(
            simulation_id=simulation_id,
            interviews=interviews_request,
            platform="reddit",
            fallback_reason=fallback_reason,
        )

    def _append_api_interviews_to_result(
        self,
        result: InterviewResult,
        api_result: Dict[str, Any],
        selected_agents: List[Dict[str, Any]],
        selected_indices: List[int],
        combined_prompt: str,
    ) -> None:
        """把真实IPC采访或profile降级采访的API结果转换为报告工具内部结构。"""
        api_data = api_result.get("result", {}) if isinstance(api_result, dict) else {}
        results_dict = api_data.get("results", {}) if isinstance(api_data, dict) else {}
        result_source = api_data.get("source") if isinstance(api_data, dict) else None

        for i, agent_idx in enumerate(selected_indices):
            agent = selected_agents[i]
            agent_name = agent.get("realname", agent.get("username", f"Agent_{agent_idx}"))
            agent_role = agent.get("profession", "未知")
            agent_bio = agent.get("bio", "")

            twitter_result = results_dict.get(f"twitter_{agent_idx}", {})
            reddit_result = results_dict.get(f"reddit_{agent_idx}", {})

            twitter_response = twitter_result.get("response", "")
            reddit_response = reddit_result.get("response", "")

            response_parts = []
            if twitter_response:
                response_parts.append(f"【Twitter平台回答】\n{twitter_response}")
            if reddit_response:
                response_parts.append(f"【Reddit平台回答】\n{reddit_response}")

            if response_parts:
                response_text = "\n\n".join(response_parts)
                if result_source == "profile_llm":
                    response_text = f"【profile问卷降级回答】\n{response_text}"
            else:
                response_text = "[无回复]"

            combined_responses = f"{twitter_response} {reddit_response}"
            key_quotes = re.findall(r'[""「」『』]([^""「」『』]{10,100})[""「」『』]', combined_responses)
            if not key_quotes:
                sentences = combined_responses.split('。')
                key_quotes = [s.strip() + '。' for s in sentences if len(s.strip()) > 20][:3]

            interview = AgentInterview(
                agent_name=agent_name,
                agent_role=agent_role,
                agent_bio=agent_bio[:1000],
                question=combined_prompt,
                response=response_text,
                key_quotes=key_quotes[:5]
            )
            result.interviews.append(interview)

        result.interviewed_count = len(result.interviews)
    
    def _load_agent_profiles(self, simulation_id: str) -> List[Dict[str, Any]]:
        """加载模拟的Agent人设文件"""
        import os
        import csv
        
        # 构建人设文件路径
        sim_dir = os.path.join(
            os.path.dirname(__file__), 
            f'../../uploads/simulations/{simulation_id}'
        )
        
        profiles = []
        
        # 优先尝试读取Reddit JSON格式
        reddit_profile_path = os.path.join(sim_dir, "reddit_profiles.json")
        if os.path.exists(reddit_profile_path):
            try:
                with open(reddit_profile_path, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
                logger.info(f"从 reddit_profiles.json 加载了 {len(profiles)} 个人设")
                return profiles
            except Exception as e:
                logger.warning(f"读取 reddit_profiles.json 失败: {e}")
        
        # 尝试读取Twitter CSV格式
        twitter_profile_path = os.path.join(sim_dir, "twitter_profiles.csv")
        if os.path.exists(twitter_profile_path):
            try:
                with open(twitter_profile_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # CSV格式转换为统一格式
                        profiles.append({
                            "realname": row.get("name", ""),
                            "username": row.get("username", ""),
                            "bio": row.get("description", ""),
                            "persona": row.get("user_char", ""),
                            "profession": "未知"
                        })
                logger.info(f"从 twitter_profiles.csv 加载了 {len(profiles)} 个人设")
                return profiles
            except Exception as e:
                logger.warning(f"读取 twitter_profiles.csv 失败: {e}")
        
        return profiles
    
    def _select_agents_for_interview(
        self,
        profiles: List[Dict[str, Any]],
        interview_requirement: str,
        simulation_requirement: str,
        max_agents: int
    ) -> tuple:
        """
        使用LLM选择要采访的Agent
        
        Returns:
            tuple: (selected_agents, selected_indices, reasoning)
                - selected_agents: 选中Agent的完整信息列表
                - selected_indices: 选中Agent的索引列表（用于API调用）
                - reasoning: 选择理由
        """
        
        # 构建Agent摘要列表
        agent_summaries = []
        for i, profile in enumerate(profiles):
            summary = {
                "index": i,
                "name": profile.get("realname", profile.get("username", f"Agent_{i}")),
                "profession": profile.get("profession", "未知"),
                "bio": profile.get("bio", "")[:200],
                "interested_topics": profile.get("interested_topics", [])
            }
            agent_summaries.append(summary)
        
        system_prompt = """你是一个专业的采访策划专家。你的任务是根据采访需求，从模拟Agent列表中选择最适合采访的对象。

选择标准：
1. Agent的身份/职业与采访主题相关
2. Agent可能持有独特或有价值的观点
3. 选择多样化的视角（如：支持方、反对方、中立方、专业人士等）
4. 优先选择与事件直接相关的角色

返回JSON格式：
{
    "selected_indices": [选中Agent的索引列表],
    "reasoning": "选择理由说明"
}"""

        user_prompt = f"""采访需求：
{interview_requirement}

模拟背景：
{simulation_requirement if simulation_requirement else "未提供"}

可选择的Agent列表（共{len(agent_summaries)}个）：
{json.dumps(agent_summaries, ensure_ascii=False, indent=2)}

请选择最多{max_agents}个最适合采访的Agent，并说明选择理由。"""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            selected_indices = response.get("selected_indices", [])[:max_agents]
            reasoning = response.get("reasoning", "基于相关性自动选择")
            
            # 获取选中的Agent完整信息
            selected_agents = []
            valid_indices = []
            for idx in selected_indices:
                if 0 <= idx < len(profiles):
                    selected_agents.append(profiles[idx])
                    valid_indices.append(idx)
            
            return selected_agents, valid_indices, reasoning
            
        except Exception as e:
            logger.warning(f"LLM选择Agent失败，使用默认选择: {e}")
            # 降级：选择前N个
            selected = profiles[:max_agents]
            indices = list(range(min(max_agents, len(profiles))))
            return selected, indices, "使用默认选择策略"
    
    def _generate_interview_questions(
        self,
        interview_requirement: str,
        simulation_requirement: str,
        selected_agents: List[Dict[str, Any]]
    ) -> List[str]:
        """使用LLM生成采访问题"""
        
        agent_roles = [a.get("profession", "未知") for a in selected_agents]
        
        system_prompt = """你是一个专业的记者/采访者。根据采访需求，生成3-5个深度采访问题。

问题要求：
1. 开放性问题，鼓励详细回答
2. 针对不同角色可能有不同答案
3. 涵盖事实、观点、感受等多个维度
4. 语言自然，像真实采访一样

返回JSON格式：{"questions": ["问题1", "问题2", ...]}"""

        user_prompt = f"""采访需求：{interview_requirement}

模拟背景：{simulation_requirement if simulation_requirement else "未提供"}

采访对象角色：{', '.join(agent_roles)}

请生成3-5个采访问题。"""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5
            )
            
            return response.get("questions", [f"关于{interview_requirement}，您有什么看法？"])
            
        except Exception as e:
            logger.warning(f"生成采访问题失败: {e}")
            return [
                f"关于{interview_requirement}，您的观点是什么？",
                "这件事对您或您所代表的群体有什么影响？",
                "您认为应该如何解决或改进这个问题？"
            ]
    
    def _generate_interview_summary(
        self,
        interviews: List[AgentInterview],
        interview_requirement: str
    ) -> str:
        """生成采访摘要"""
        
        if not interviews:
            return "未完成任何采访"
        
        # 收集所有采访内容
        interview_texts = []
        for interview in interviews:
            interview_texts.append(f"【{interview.agent_name}（{interview.agent_role}）】\n{interview.response[:500]}")
        
        system_prompt = """你是一个专业的新闻编辑。请根据多位受访者的回答，生成一份采访摘要。

摘要要求：
1. 提炼各方主要观点
2. 指出观点的共识和分歧
3. 突出有价值的引言
4. 客观中立，不偏袒任何一方
5. 控制在1000字内"""

        user_prompt = f"""采访主题：{interview_requirement}

采访内容：
{"".join(interview_texts)}

请生成采访摘要。"""

        try:
            summary = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=800
            )
            return summary
            
        except Exception as e:
            logger.warning(f"生成采访摘要失败: {e}")
            # 降级：简单拼接
            return f"共采访了{len(interviews)}位受访者，包括：" + "、".join([i.agent_name for i in interviews])
