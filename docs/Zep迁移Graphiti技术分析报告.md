# MiroFish：从 Zep Cloud 迁移至 Graphiti 自托管技术分析报告

> 分析日期：2026-04-23  
> 修订日期：2026-04-24
> 当前 Zep SDK：`zep-cloud==3.13.0`

---

## 目录

1. [执行摘要](#1-执行摘要)
2. [国内替代方案对比分析](#2-国内替代方案对比分析)
3. [为何选择 Graphiti 自托管](#3-为何选择-graphiti-自托管)
4. [MiroFish 对 Zep 的完整依赖分析](#4-mirofish-对-zep-的完整依赖分析)
5. [架构设计：GraphMemoryProvider 抽象层](#5-架构设计-graphmemoryprovider-抽象层)
6. [需要修改的文件详细分析（10 个文件）](#6-需要修改的文件详细分析10-个文件)
7. [需要新建的文件（建议 4 个文件）](#7-需要新建的文件建议-4-个文件)
8. [基础设施变更](#8-基础设施变更)
9. [工作量估算](#9-工作量估算)
10. [五步迁移策略](#10-五步迁移策略)
11. [POC 验收标准与风险规避](#11-poc-验收标准与风险规避)
12. [服务器部署注意事项](#12-服务器部署注意事项)

---

## 1. 执行摘要

**背景**：MiroFish 当前使用 Zep Cloud（托管服务，`zep-cloud==3.13.0`）作为知识图谱构建与检索的核心基础设施。Zep Cloud 为境外服务，在国内访问速度慢、成本高，需要寻找可替代方案。

**结论**：
- **首选方向**：Graphiti 自托管 + Neo4j 首选后端 + 国产/本地 OpenAI-compatible LLM/Embedding；FalkorDB 作为第二阶段资源优化备选，不建议第一阶段同时落地两个图数据库。
- **方案状态**：方向正确，但不能直接全量生产替换。必须先完成 Graphiti + Neo4j POC、中文抽取质量对比、报告链路验收和回滚策略设计。
- **国内云厂商均不适合直接替换**：腾讯云 GraphRAG、华为云 KG 等偏平台型，无法提供 MiroFish 所需的节点/边级别底层 API、项目内图谱数据接口和可控检索工具链。
- **迁移策略**：先引入 `GraphMemoryProvider` 抽象层收敛 Zep 调用，保留 `ZepProvider`，再实现 `GraphitiProvider`，通过项目元数据和 `GRAPH_PROVIDER` 灰度切换。
- **工作量**：约 10–12 人天（含 POC、Provider contract tests、配置和部署），不含中文抽取调优和生产数据迁移。

**核心挑战**：
1. Graphiti 是异步库（`async/await`），当前 Flask 同步后端需要专用 asyncio 事件循环桥接，并处理生命周期关闭。
2. Graphiti 没有 Zep `set_ontology` 的图谱级等价 API，本体需要在应用侧持久化，并在写入 episode 时转为 Graphiti 自定义实体/边类型。
3. Zep 的 `node.get_by_graph_id` / `edge.get_by_graph_id` 分页接口在 Graphiti SDK 中没有直接等价，需要基于真实 Neo4j/FalkorDB schema 做原生查询适配，不能写死单一关系类型。
4. Zep `graph.search(scope="edges")` 与 Graphiti 搜索 recipe/filter 不是一一映射，需要做结果归一化和报告质量回归。
5. `add_episode_bulk()` 只适合初始离线批量导入或重建，不应简单替代所有 Zep `add_batch()`/`graph.add()` 场景；Step3 动态模拟写回应优先使用有序 `add_episode()` 或小批顺序写入。
6. 历史 Zep Cloud 图谱只在项目元数据中保留 `graph_id`，迁移时必须定义旧项目继续走 Zep、基于原始文档重建或导出归档的策略。

---

## 2. 国内替代方案对比分析

### 2.1 综合评分表

| 方案 | 功能完整度 | 国内访问速度 | 部署/维护 | 成本 | 社区/文档 | 综合建议 |
|---|:---:|:---:|:---:|:---:|:---:|---|
| **Graphiti + Neo4j 自托管（FalkorDB 二阶段备选）** | ★★★★☆ (4.2) | ★★★★★ (5) | ★★★☆☆ (3) | ★★★★☆ (4) | ★★★★☆ (4.5) | **首选方向，需先 POC** |
| Cognee 自托管 | ★★★☆☆ (3.4) | ★★★★★ (5) | ★★★☆☆ (3.5) | ★★★★☆ (4) | ★★★★☆ (4.3) | 备选 |
| Mem0 自托管 | ★★★☆☆ (2.8) | ★★★★★ (5) | ★★★★☆ (4) | ★★★★☆ (4) | ★★★★★ (5) | 仅适合 Agent 记忆 |
| 腾讯云 GraphRAG | ★★★☆☆ (3.3) | ★★★★★ (5) | ★★★★☆ (4) | ★★★☆☆ (3) | ★★★☆☆ (3.5) | 平台型，非 SDK |
| 华为云 KG | ★★★☆☆ (3.5) | ★★★★★ (5) | ★★★☆☆ (3) | ★★☆☆☆ (2.5) | ★★★☆☆ (3) | 企业定制型 |
| 华为云 GES | ★★☆☆☆ (2.0) | ★★★★★ (5) | ★★★★☆ (4) | ★★★☆☆ (3) | ★★★★☆ (4) | 图数据库，不含抽取 |
| TuGraph 自托管 | ★★☆☆☆ (2.0) | ★★★★★ (5) | ★★★☆☆ (3) | ★★★★★ (5) | ★★★☆☆ (3.2) | 图数据库，不含抽取 |
| 阿里云 OpenSearch LLM | ★★☆☆☆ (2.4) | ★★★★★ (5) | ★★★★☆ (4.5) | ★★★☆☆ (3) | ★★★★☆ (4) | RAG 平台，非图谱 |
| 百度知识理解/NLP | ★★☆☆☆ (1.8) | ★★★★★ (5) | ★★★★☆ (4) | ★★★☆☆ (3.5) | ★★★☆☆ (3) | 只能作为组件使用 |

*评分说明：5 分最好；"部署/维护难度"按易部署 = 高分计算*

### 2.2 各方案关键差异分析

**腾讯云 GraphRAG**：是国内最接近"文本→图谱→问答"的托管产品，支持自定义 GraphSchema、实体关系抽取、图谱更新与知识图谱检索问答。但它是**平台型能力**，不是底层 SDK，缺乏 MiroFish 需要的节点/边分页读取 API 和前端图谱数据接口。

**阿里云 OpenSearch LLM 智能问答版**：支持文本摄入、向量化、RAG 检索，但无等价 GraphSchema/ontology 图谱 API，不适合替换 MiroFish 当前图谱底座。

**TuGraph / 华为云 GES**：均为高性能图数据库，不负责 LLM 实体关系抽取和语义搜索，需要自行搭建抽取层，工程量等同于从头实现 Graphiti，不建议采用。

**Mem0**：擅长 Agent 对话记忆管理，对文档知识图谱构建和自定义 ontology 支持弱，更适合作为 Step 5（交互对话记忆）的补充，而非 Step 1 图谱构建的替代。

---

## 3. 为何选择 Graphiti 自托管

### 3.1 Graphiti 与 Zep Cloud 的关系

Graphiti 由 Zep 团队开发，是 Zep Cloud 的底层时序知识图谱引擎（temporal context graph engine）。Zep Cloud 是基于 Graphiti 构建的托管平台，Graphiti 是可自托管的开源引擎。

官方文档明确区分：Zep Cloud 提供 API 即服务，Graphiti 提供引擎本身，需要自行构建周边系统。

GitHub 活跃度（截至 2026-04-23）：
- Graphiti：~25,300 stars，活跃维护
- Cognee：~16,600 stars
- Mem0：~53,800 stars

### 3.2 能力映射表

| Zep Cloud 功能 | Graphiti 替代实现 | 替代难度 | 迁移注意事项 |
|---|---|:---:|
| `graph.create(graph_id)` | `group_id=graph_id` 做项目隔离 | 低 | `graph_id` 可继续沿用 `mirofish_xxx`，但项目元数据需记录 provider/backend |
| `graph.set_ontology(graph_ids, entities, edges)` | 应用层持久化 ontology，写入 episode 时转为 Graphiti 自定义实体/边类型 | 中 | 不能只放内存缓存；需支持后端重启和多进程恢复 |
| 初始 `graph.add_batch(graph_id, episodes)` | 可评估 `graphiti.add_episode_bulk()` 或有序小批 `add_episode()` | 中 | bulk 适合离线导入/重建；需要验证时间语义和边失效行为 |
| 动态 `graph.add(graph_id, data)` | 优先有序 `graphiti.add_episode()` 或小批顺序写入 | 中 | Step3 模拟活动具有时间线，不建议无脑 bulk |
| `graph.episode.get(uuid_)` 轮询处理状态 | Graphiti 调用完成后返回；Provider 层保留 `wait_until_indexed()` 兼容接口 | 中 | 对 Flask 任务进度仍需保留“写入/索引/读取统计”阶段 |
| `graph.node.get_by_graph_id(graph_id)` 分页 | 基于 Neo4j/FalkorDB 原生查询按 `group_id` 列举 | 中 | 查询必须基于 POC 后真实 schema，不能假设固定关系/标签 |
| `graph.edge.get_by_graph_id(graph_id)` 分页 | 原生图查询列举业务边 | 中 | 不应写死 `RELATES_TO`，需排除 Graphiti 内部系统关系 |
| `graph.node.get(uuid_)` + `node.get_entity_edges` | 原生图查询按 uuid 查节点和邻接边 | 中 | 方法签名建议带 `graph_id`，避免跨项目 uuid 冲突 |
| `graph.search(graph_id, query, scope)` | `graphiti.search()` + search recipe/filter + 结果归一化 | 中 | 需分别验证报告工具的 edge/fact 搜索与 node 搜索质量 |
| `graph.delete(graph_id)` | 原生查询按 `group_id` 删除项目图谱 | 低 | 删除前建议记录 provider/backend 并保护历史 Zep 项目 |

### 3.3 基础设施要求

| 组件 | 推荐选项 |
|---|---|
| 图数据库 | 第一阶段推荐 Neo4j 5.26+；FalkorDB 作为第二阶段 POC 备选；Kuzu 仅适合本地实验，不建议作为当前生产路径 |
| Python 服务 | 当前 Flask 后端，新增 asyncio 后台事件循环、Graphiti 单例、driver 生命周期管理 |
| LLM | 国产 OpenAI-compatible：通义千问、DeepSeek、智谱 GLM、火山方舟等；必须验证 JSON/schema 输出稳定性 |
| Embedding | Qwen Embedding、BGE-M3 或同等中文向量模型；需明确维度和 Graphiti embedder 配置 |
| Reranker | 初期可关闭；报告质量不足时再接 bge-reranker 或 vLLM cross-encoder |

**选型边界**：本报告推荐先完成 `Graphiti + Neo4j` 的最小闭环和质量验收。FalkorDB 不应与 Neo4j 在第一阶段并行生产化，否则会同时引入 Graphiti 适配、图数据库差异、运维差异三类变量，放大排障成本。

---

## 4. MiroFish 对 Zep 的完整依赖分析

### 4.1 调用点汇总

涉及 Zep SDK 调用的文件共 **5 个**：

| 文件 | 主要 Zep 调用 |
|---|---|
| `backend/app/services/graph_builder.py` | `create`、`set_ontology`、`add_batch`、`episode.get`、`graph.delete` |
| `backend/app/utils/zep_paging.py` | `node.get_by_graph_id`、`edge.get_by_graph_id` |
| `backend/app/services/zep_entity_reader.py` | `node.get_by_graph_id`、`node.get`、`node.get_entity_edges` |
| `backend/app/services/zep_tools.py` | `graph.search` (scope="edges"/"nodes") |
| `backend/app/services/zep_graph_memory_updater.py` | `graph.add` (将模拟活动写回图谱) |

### 4.2 关键设计约束

1. **同步/异步桥接**：Flask 是 WSGI 同步框架，Graphiti 是 async 库。所有 `await graphiti.xxx()` 调用应通过专用后台 asyncio 事件循环线程的 `run_coroutine_threadsafe()` 来同步执行，并提供 shutdown/teardown，避免事件循环和 driver 泄漏。

2. **本体管理模式差异**：Zep 通过 `set_ontology` 一次性设置图谱级本体；Graphiti 需要在写入 episode 时提供自定义 `entity_types`/`edge_types`/`edge_type_map`。本体注册表不能只在内存保存，必须能从 `project.json` 或独立 registry 持久化恢复。

3. **节点/边列举 API 缺失**：Graphiti Python SDK 无 `list_nodes_by_group` 等 API。必须直接通过 Neo4j/FalkorDB driver 执行原生查询，但查询语句要基于真实 Graphiti schema 校准，不能预设所有关系都是 `RELATES_TO`。

4. **搜索结果结构差异**：Zep `graph.search(scope="edges")` 返回边对象列表；Graphiti search 更偏 recipe/filter 模式，返回对象可能包含 edges/nodes/communities，需要按 MiroFish 的 `GraphSearchResult` 做归一化，并对 ReportAgent 做质量回归。

5. **历史图谱兼容差异**：旧项目只保存 `graph_id`，真实节点/边在 Zep Cloud。迁移后必须通过项目元数据区分 `zep` 与 `graphiti` 图谱，不能默认所有旧 `graph_id` 都可在 Neo4j 中读取。

---

## 5. 架构设计：GraphMemoryProvider 抽象层

### 5.1 抽象层接口设计

```python
# backend/app/services/graph_provider.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

@dataclass
class GraphNode:
    uuid: str
    name: str
    labels: list[str]
    summary: str
    attributes: dict[str, Any]
    created_at: str | None = None

@dataclass
class GraphEdge:
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: str = ""
    target_node_name: str = ""
    attributes: dict[str, Any] = None
    created_at: str | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    expired_at: str | None = None
    episodes: list[str] = None

@dataclass
class GraphSearchResult:
    edges: list[GraphEdge]
    nodes: list[GraphNode]

class GraphMemoryProvider(ABC):
    """知识图谱存储与检索的抽象接口"""

    @abstractmethod
    def create_graph(self, name: str) -> str:
        """创建图谱，返回 graph_id"""

    @abstractmethod
    def register_ontology(self, graph_id: str, ontology: dict) -> None:
        """注册/持久化本体，ontology 格式与现有一致"""

    @abstractmethod
    def add_texts(
        self, graph_id: str, chunks: list[str],
        batch_size: int = 3,
        progress_callback=None
    ) -> list[str]:
        """写入初始文档文本，返回 episode 引用列表"""

    @abstractmethod
    def append_activity_texts(
        self, graph_id: str,
        texts: list[str],
        platform: str = "",
        progress_callback=None
    ) -> list[str]:
        """追加模拟/新闻等增量文本，要求保持时间顺序"""

    @abstractmethod
    def wait_until_indexed(
        self, episode_ids: list[str],
        progress_callback=None,
        timeout: int = 600
    ) -> None:
        """等待 episode 处理/索引完成；Graphiti 可实现为空或轻量校验"""

    @abstractmethod
    def list_nodes(self, graph_id: str) -> list[GraphNode]:
        """列出图谱所有节点"""

    @abstractmethod
    def list_edges(self, graph_id: str) -> list[GraphEdge]:
        """列出图谱所有边"""

    @abstractmethod
    def get_node(self, graph_id: str, node_uuid: str) -> GraphNode | None:
        """按 UUID 获取单个节点"""

    @abstractmethod
    def get_node_edges(self, graph_id: str, node_uuid: str) -> list[GraphEdge]:
        """获取节点的关联边"""

    @abstractmethod
    def search(
        self, graph_id: str, query: str,
        limit: int = 10,
        scope: str = "edges"
    ) -> GraphSearchResult:
        """语义/混合检索"""

    @abstractmethod
    def delete_graph(self, graph_id: str) -> None:
        """删除图谱所有数据"""

    @abstractmethod
    def healthcheck(self) -> dict:
        """返回 provider、后端连接、索引状态等健康信息"""
```

**接口设计原则**：

- 业务层禁止直接导入 `zep_cloud` 或 `graphiti_core`，所有图谱能力必须经由 `GraphMemoryProvider`。
- `GraphBuilderService` 应降级为任务编排层：负责线程、进度、文本分块、项目状态写回；真实图谱读写交给 Provider。
- Provider 负责把 Zep/Graphiti 的结果归一化为 `GraphNode`、`GraphEdge`、`GraphSearchResult`，保证前端和报告工具不感知底层差异。
- `get_node()`、`get_node_edges()` 必须带 `graph_id`，避免未来跨项目 uuid 或多后端混用时出现误读。
- `append_activity_texts()` 与 `add_texts()` 分开，避免把初始离线批量导入和 Step3 动态时序写回混为同一语义。

### 5.2 双 Provider 部署架构

```
MiroFish Backend
  ↓
GraphMemoryProvider（抽象层）
  ├── ZepProvider（保留，现有代码封装）
  └── GraphitiProvider（新增自托管实现）
        ↓
      Graphiti Python SDK
        ↓
      Neo4j 5.26+ / FalkorDB
        ↓
      国产/本地 LLM + Embedding（通义千问、DeepSeek 等）
```

通过 `Config.GRAPH_PROVIDER = "zep" | "graphiti"` 环境变量控制使用哪个 Provider，实现 A/B 切换。

### 5.3 项目元数据与本体注册表

当前 `backend/uploads/projects/<project_id>/project.json` 只保存 `graph_id` 和原始 `ontology`。迁移 Graphiti 后建议增加以下字段：

```json
{
  "graph_id": "mirofish_xxx",
  "graph_provider": "graphiti",
  "graph_backend": "neo4j",
  "graph_schema_version": 1,
  "ontology_registry_version": 1
}
```

兼容策略：

- 旧项目未包含 `graph_provider` 时，默认视为 `zep`，继续走 `ZepProvider`，避免旧 `graph_id` 被错误拿到 Neo4j 查询。
- 新项目使用 Graphiti 时，`graph_provider=graphiti`，`graph_backend=neo4j`，`graph_id` 映射为 Graphiti `group_id`。
- 本体注册表不能只用 `_ontology_cache` 内存变量；内存缓存只能作为加速层，真实来源必须是项目 `ontology` 或独立持久化 registry。
- 每次新闻接入、模拟活动写回前，如果内存中没有编译后的 ontology，应能从项目元数据恢复并重新编译。

### 5.4 历史数据迁移策略

旧 Zep Cloud 图谱有三种处理方式：

| 策略 | 优点 | 缺点 | 建议 |
|---|---|---|---|
| 旧项目继续走 Zep | 风险最低，不破坏历史项目 | 仍依赖 Zep Cloud | 短期默认策略 |
| 用原始上传文档重建 Graphiti 图 | 图谱语义由 Graphiti 重新生成，后续可完全自托管 | 节点/边 uuid 与旧图不一致 | 推荐用于活跃项目 |
| 从 Zep 导出节点/边导入 Neo4j | 可保留部分旧展示结构 | 失去 Graphiti episode/embedding/时序抽取语义 | 仅适合归档展示 |

因此生产迁移不应以“一键转换所有旧 graph_id”为目标，而应先保证新项目可用，再逐步重建活跃项目。

---

## 6. 需要修改的文件详细分析（10 个文件）

### 6.1 `backend/app/config.py`

**改动**：新增 Graph Provider、Graphiti 后端、LLM、Embedding 与 Reranker 配置项。配置命名不应只绑定 Neo4j，便于后续评估 FalkorDB。

```python
# 图谱 Provider 选择（zep 或 graphiti）
GRAPH_PROVIDER = os.environ.get('GRAPH_PROVIDER', 'zep')

# Graphiti 图数据库后端
GRAPHITI_BACKEND = os.environ.get('GRAPHITI_BACKEND', 'neo4j')  # neo4j | falkordb | kuzu
GRAPHITI_DB_URI = os.environ.get('GRAPHITI_DB_URI', 'bolt://localhost:7687')
GRAPHITI_DB_USER = os.environ.get('GRAPHITI_DB_USER', 'neo4j')
GRAPHITI_DB_PASSWORD = os.environ.get('GRAPHITI_DB_PASSWORD', '')

# Graphiti LLM（可复用 LLM_* 配置，也可单独设置）
GRAPHITI_LLM_API_KEY = os.environ.get('GRAPHITI_LLM_API_KEY') or os.environ.get('LLM_API_KEY')
GRAPHITI_LLM_BASE_URL = os.environ.get('GRAPHITI_LLM_BASE_URL') or os.environ.get('LLM_BASE_URL')
GRAPHITI_LLM_MODEL = os.environ.get('GRAPHITI_LLM_MODEL') or os.environ.get('LLM_MODEL_NAME')
GRAPHITI_LLM_TEMPERATURE = float(os.environ.get('GRAPHITI_LLM_TEMPERATURE', '0'))

# Graphiti Embedding
GRAPHITI_EMBEDDING_API_KEY = os.environ.get('GRAPHITI_EMBEDDING_API_KEY') or os.environ.get('LLM_API_KEY')
GRAPHITI_EMBEDDING_BASE_URL = os.environ.get('GRAPHITI_EMBEDDING_BASE_URL') or os.environ.get('LLM_BASE_URL')
GRAPHITI_EMBEDDING_MODEL = os.environ.get('GRAPHITI_EMBEDDING_MODEL', 'text-embedding-v3')
GRAPHITI_EMBEDDING_DIM = int(os.environ.get('GRAPHITI_EMBEDDING_DIM', '1024'))

# 可选 reranker
GRAPHITI_RERANKER_ENABLED = os.environ.get('GRAPHITI_RERANKER_ENABLED', 'false').lower() == 'true'
GRAPHITI_RERANKER_MODEL = os.environ.get('GRAPHITI_RERANKER_MODEL', '')
```

**`Config.validate()` 调整**：当 `GRAPH_PROVIDER == 'zep'` 时验证 `ZEP_API_KEY`；当 `GRAPH_PROVIDER == 'graphiti'` 时验证 `GRAPHITI_DB_*`、Graphiti LLM 与 Embedding 配置，不再强制要求 `ZEP_API_KEY`。

---

### 6.2 `backend/app/services/graph_builder.py`

**改动**：将直接 Zep SDK 调用替换为通过 `GraphMemoryProvider` 接口调用

```python
# 修改前
from zep_cloud.client import Zep
from zep_cloud import EpisodeData, EntityEdgeSourceTarget
from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel

class GraphBuilderService:
    def __init__(self, api_key=None):
        self.client = Zep(api_key=self.api_key)

# 修改后
from .graph_provider import GraphMemoryProvider
from ..config import Config

class GraphBuilderService:
    def __init__(self):
        self.provider: GraphMemoryProvider = _build_provider()
```

`_build_provider()` 工厂函数根据 `Config.GRAPH_PROVIDER` 返回对应实例：

```python
def _build_provider() -> GraphMemoryProvider:
    if Config.GRAPH_PROVIDER == 'graphiti':
        from .graphiti_provider import GraphitiProvider
        return GraphitiProvider()
    else:
        from .zep_provider import ZepProvider
        return ZepProvider(api_key=Config.ZEP_API_KEY)
```

所有业务方法均改为调用 `self.provider.xxx()`，但方法语义需要按新接口调整：

- `set_ontology()` → `register_ontology()`，并把 ontology 持久化在项目元数据中。
- `add_text_batches()` → `add_texts()`，仅用于初始文档导入。
- `_wait_for_episodes()` → `wait_until_indexed()`，Zep 需要轮询，Graphiti 可做空实现或轻量校验。
- 模拟/新闻增量写入使用 `append_activity_texts()`，避免误用初始批量导入语义。
- `get_graph_data()`、`delete_graph()` 继续作为业务编排入口，内部委托 provider。

---

### 6.3 `backend/app/utils/zep_paging.py`

**改动**：此文件逻辑移入 `GraphitiProvider`（原生图查询）和 `ZepProvider`（保留现有翻页逻辑），原文件仅作为短期兼容薄包装。中长期建议删除该工具，避免继续传播 `zep_*` 命名。

```python
# 保留向后兼容，实际实现已移入各 Provider
def fetch_all_nodes(client, graph_id):
    from ..services.graph_builder import _get_provider
    return _get_provider().list_nodes(graph_id)

def fetch_all_edges(client, graph_id):
    from ..services.graph_builder import _get_provider
    return _get_provider().list_edges(graph_id)
```

---

### 6.4 `backend/app/services/zep_entity_reader.py`

**改动**：将 Zep 特有调用替换为 `GraphMemoryProvider` 接口

关键变化：
- `client.graph.node.get_by_graph_id(graph_id, ...)` → `provider.list_nodes(graph_id)`
- `client.graph.node.get(uuid_=uuid)` → `provider.get_node(graph_id, uuid)`
- `client.graph.node.get_entity_edges(uuid_=uuid)` → `provider.get_node_edges(graph_id, uuid)`

节点和边的数据结构从 Zep SDK 对象改为 `GraphNode` / `GraphEdge` dataclass，访问字段方式相同（均为属性访问）。

---

### 6.5 `backend/app/services/zep_tools.py`（ReportAgent 工具）

**改动**：`search_graph` 函数

```python
# 修改前
result = client.graph.search(
    graph_id=graph_id,
    query=query,
    scope="edges",
    limit=limit
)
edges = result.edges

# 修改后
result = provider.search(graph_id, query, limit=limit, scope="edges")
edges = result.edges  # GraphEdge 列表，字段兼容
```

`get_panorama`（全量节点/边读取）：
```python
# 修改前
nodes = fetch_all_nodes(client, graph_id)
edges = fetch_all_edges(client, graph_id)

# 修改后
nodes = provider.list_nodes(graph_id)
edges = provider.list_edges(graph_id)
```

---

### 6.6 `backend/app/services/zep_graph_memory_updater.py`

**改动**：将模拟活动回写图谱的调用替换为 `provider.append_activity_texts()`，并保持平台内事件顺序。

```python
# 修改前
self.client.graph.add(graph_id=graph_id, type="text", data=text)

# 修改后
self.provider.append_activity_texts(
    graph_id=graph_id,
    texts=[text],
    platform=platform,
)
```

注意：Step3 模拟行为具有时间线，不建议直接使用 `add_episode_bulk()` 作为默认实现；GraphitiProvider 内部应优先调用有序 `add_episode()` 或小批顺序写入。

---

### 6.7 `backend/app/__init__.py`

**改动**：在应用工厂中注册新蓝图（`news`），并确保 Graphiti 在应用启动时完成初始化

```python
# 新增 Graphiti 预热（仅在使用 Graphiti 时）
if Config.GRAPH_PROVIDER == 'graphiti':
    from app.utils.graphiti_runtime import get_graphiti
    get_graphiti()  # 触发单例初始化，建立 Neo4j 连接和索引
```

---

### 6.8 `backend/app/api/graph.py`

**改动**：构造 `GraphBuilderService` 时不再需要传入 `api_key` 参数

```python
# 修改前
service = GraphBuilderService(api_key=Config.ZEP_API_KEY)

# 修改后
service = GraphBuilderService()
```

---

### 6.9 `backend/pyproject.toml`

**改动**：迁移期依赖共存，不应第一阶段删除 `zep-cloud`

```toml
# 过渡期：两个依赖同时存在
"zep-cloud==3.13.0",
"graphiti-core>=0.3.0",
"neo4j>=5.0.0",
```

---

### 6.10 `.env.example` / `.env`

**改动**：新增 Graphiti 相关配置项

```env
# === 图谱 Provider 选择 ===
# zep（当前默认）或 graphiti（自托管）
GRAPH_PROVIDER=zep

# === Zep Cloud 配置（GRAPH_PROVIDER=zep 时必填）===
ZEP_API_KEY=...

# === Graphiti 图数据库配置（GRAPH_PROVIDER=graphiti 时必填）===
GRAPHITI_BACKEND=neo4j
GRAPHITI_DB_URI=bolt://neo4j:7687
GRAPHITI_DB_USER=neo4j
GRAPHITI_DB_PASSWORD=your_graphiti_db_password

# Graphiti LLM（留空则复用 LLM_* 配置）
GRAPHITI_LLM_API_KEY=
GRAPHITI_LLM_BASE_URL=
GRAPHITI_LLM_MODEL=

# Graphiti Embedding
GRAPHITI_EMBEDDING_API_KEY=
GRAPHITI_EMBEDDING_BASE_URL=
GRAPHITI_EMBEDDING_MODEL=text-embedding-v3
GRAPHITI_EMBEDDING_DIM=1024

# Graphiti Reranker（可选，初期建议关闭）
GRAPHITI_RERANKER_ENABLED=false
GRAPHITI_RERANKER_MODEL=
```

---

## 7. 需要新建的文件（建议 4 个文件）

> 本章不再给出可直接复制的完整实现代码，而给出实现职责、关键约束和伪代码。Graphiti、Neo4j/FalkorDB 的内部 schema 可能随版本变化，正式代码必须以 POC 写入后的真实 schema 为准。

### 7.1 `backend/app/services/graph_provider.py`

职责：定义 `GraphMemoryProvider` 抽象接口与统一数据模型，业务层只依赖该文件。

建议模型：

```python
@dataclass
class GraphNode:
    uuid: str
    name: str
    labels: list[str]
    summary: str
    attributes: dict[str, Any]
    created_at: str | None = None

@dataclass
class GraphEdge:
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: str = ""
    target_node_name: str = ""
    attributes: dict[str, Any] | None = None
    created_at: str | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    expired_at: str | None = None
    episodes: list[str] | None = None
```

关键要求：

- 字段必须兼容当前前端图谱展示、Step2 实体读取和 ReportAgent 工具链。
- Provider 内部做字段归一化，不把 Zep SDK 对象或 Graphiti 内部对象直接暴露给业务层。
- `graph_id` 是业务层统一概念；GraphitiProvider 内部把它映射为 `group_id`。

### 7.2 `backend/app/services/zep_provider.py`

职责：把当前 Zep Cloud 调用封装进 `GraphMemoryProvider`，第一阶段不改变行为。

保留能力：

- `client.graph.create()`
- `client.graph.set_ontology()`
- `client.graph.add_batch()` / `client.graph.add()`
- `client.graph.episode.get()`
- `node.get_by_graph_id()` / `edge.get_by_graph_id()`
- `graph.search()`
- `graph.delete()`

设计要求：

- 该 Provider 是迁移的安全网，生产切换失败时可以回滚。
- 旧项目未包含 `graph_provider` 字段时，默认由 `ZepProvider` 处理。
- 对外返回统一 `GraphNode` / `GraphEdge` / `GraphSearchResult`。

### 7.3 `backend/app/utils/graphiti_runtime.py`

职责：管理 Graphiti 单例、asyncio 后台事件循环、图数据库 driver 生命周期和索引初始化。

关键伪代码：

```python
def _ensure_bg_loop() -> asyncio.AbstractEventLoop:
    """创建并复用后台事件循环。"""


def run_async(coro, timeout: float = 120.0):
    """通过 run_coroutine_threadsafe() 让同步 Flask 代码调用 async Graphiti。"""


def get_graphiti() -> Graphiti:
    """懒加载 Graphiti 单例，初始化 LLM、Embedding、可选 Reranker。"""


async def _init_graphiti() -> Graphiti:
    graphiti = Graphiti(
        uri=Config.GRAPHITI_DB_URI,
        user=Config.GRAPHITI_DB_USER,
        password=Config.GRAPHITI_DB_PASSWORD,
        llm_client=...,      # OpenAI-compatible LLM
        embedder=...,        # 中文 embedding
        cross_encoder=...,   # 可选 reranker
    )
    await graphiti.build_indices_and_constraints()
    return graphiti


def shutdown_graphiti() -> None:
    """Flask 退出或测试 teardown 时关闭 driver 和事件循环。"""
```

注意事项：

- 不要在每个请求里 `asyncio.run()`，否则容易创建大量事件循环并泄漏连接。
- Graphiti 初始化时必须执行索引/约束初始化。
- LLM、Embedding、Reranker 建议使用独立 `GRAPHITI_*` 配置，不能假设与报告生成模型完全一致。
- 多进程部署时每个 worker 都会有自己的 Graphiti 单例和事件循环；内存缓存不能作为唯一状态源。

### 7.4 `backend/app/services/graphiti_provider.py`

职责：实现 `GraphMemoryProvider`，把 MiroFish 业务语义适配到 Graphiti + Neo4j/FalkorDB。

关键职责：

1. `create_graph(name)`：生成 `mirofish_xxx`，作为 Graphiti `group_id` 使用，不需要在 Graphiti 中显式创建空图。
2. `register_ontology(graph_id, ontology)`：从项目元数据读取/保存 ontology，并编译为 Graphiti 自定义实体/边类型；内存缓存仅作为加速层。
3. `add_texts(graph_id, chunks)`：用于初始文档导入。可在 POC 后决定使用 `add_episode_bulk()` 还是有序小批 `add_episode()`。
4. `append_activity_texts(graph_id, texts, platform)`：用于新闻和模拟行为增量写入，默认使用有序 `add_episode()` 或小批顺序写入。
5. `wait_until_indexed()`：兼容 Zep 轮询阶段；Graphiti 可做轻量健康检查或读取统计。
6. `list_nodes()` / `list_edges()` / `get_node()` / `get_node_edges()`：通过原生图查询实现，但查询必须基于真实 Graphiti schema 校准。
7. `search()`：封装 Graphiti search recipe/filter，并归一化为 `GraphSearchResult`。
8. `delete_graph()`：按 `group_id` 删除图谱数据，避免误删其它项目。
9. `healthcheck()`：返回 Graphiti、图数据库、索引和模型配置状态。

### 7.5 `backend/app/utils/graphiti_queries.py`

职责：集中维护 Neo4j/FalkorDB 原生查询，避免查询散落在业务代码中。

查询原则：

- 所有查询必须带 `group_id` 过滤。
- 节点查询应兼容 Graphiti 默认实体标签与自定义实体标签。
- 边查询不应写死为 `RELATES_TO`，应在 POC 后根据真实 schema 过滤业务边并排除内部关系。
- 所有全量查询必须有 `limit`、分页或最大返回数，避免大图拖垮接口。
- 返回字段只保留 MiroFish 需要的统一模型字段，不直接暴露 Graphiti 内部字段。

示意查询：

```cypher
// 示例：以真实 schema 校准后再固化，不可未经验证直接生产使用
MATCH (n)
WHERE n.group_id = $group_id
RETURN n
LIMIT $limit
```

```cypher
// 示例：边类型和内部关系过滤条件需 POC 后确认
MATCH (s)-[e]->(t)
WHERE s.group_id = $group_id AND t.group_id = $group_id
RETURN s, e, t
LIMIT $limit
```

### 7.6 本体编译策略

MiroFish 当前 ontology 结构为：

```json
{
  "entity_types": [...],
  "edge_types": [...]
}
```

GraphitiProvider 需要把它编译为 Graphiti 支持的自定义实体和边类型。建议：

- entity 名称转为合法 Python/Pydantic class 名称，保留原始名称到属性映射。
- 避免 `uuid`、`name`、`group_id` 等保留字段冲突。
- edge type 与 edge type map 先在 POC 中验证格式，再写入生产方案。
- 编译失败时应降级为无自定义 schema 的通用抽取，并记录日志，而不是让整个任务静默失败。

---

## 8. 基础设施变更

### 8.1 第一阶段推荐：Neo4j 服务

第一阶段推荐只落地 Neo4j，先验证 Graphiti 抽取、检索和报告链路质量。FalkorDB 保留为第二阶段备选，不与 Neo4j 同时生产化。

```yaml
services:
  neo4j:
    image: neo4j:5.26
    restart: unless-stopped
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-mirofish_neo4j}
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_dbms_memory_heap_initial__size: 512m
      NEO4J_dbms_memory_heap_max__size: 1G
      NEO4J_dbms_memory_pagecache_size: 512m
    ports:
      - "127.0.0.1:7474:7474"   # Neo4j Browser，仅本机访问
      - "127.0.0.1:7687:7687"   # Bolt，仅本机访问；容器间使用服务名 neo4j
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs
    healthcheck:
      test: ["CMD-SHELL", "cypher-shell -u neo4j -p ${NEO4J_PASSWORD:-mirofish_neo4j} 'RETURN 1'"]
      interval: 10s
      timeout: 5s
      retries: 10

volumes:
  neo4j_data:
  neo4j_logs:
```

`.env` 示例：

```env
GRAPH_PROVIDER=graphiti
GRAPHITI_BACKEND=neo4j
GRAPHITI_DB_URI=bolt://neo4j:7687
GRAPHITI_DB_USER=neo4j
GRAPHITI_DB_PASSWORD=mirofish_neo4j
```

生产注意事项：

- `7474/7687` 不应直接暴露公网。
- Neo4j 密码必须来自 `.env` 或部署平台密钥，不写入代码仓库。
- `neo4j_data` 必须定期备份；图谱数据不在 `backend/uploads` 中。
- 全量节点/边接口必须设置分页或最大返回数，避免大图查询拖垮服务。

### 8.2 FalkorDB 二阶段评估

FalkorDB 可能更轻量，但不建议第一阶段直接作为生产默认后端。原因：

- 当前首要风险是 Graphiti + 中文 LLM 抽取质量，而不是图数据库资源占用。
- MiroFish 需要自定义节点/边读取、统计、删除和搜索归一化；同时适配 Neo4j/FalkorDB 会扩大变量面。
- 团队排障时需要同时理解 Graphiti、图数据库 schema、Cypher/查询差异和部署差异，迁移初期成本过高。

建议在 Neo4j POC 通过后，用同一套 Provider contract tests 单独评估 FalkorDB 的内存、写入吞吐、查询延迟和运维复杂度。

### 8.3 `backend/pyproject.toml` 依赖更新

迁移期依赖共存：

```toml
dependencies = [
    "flask>=3.0.0",
    "flask-cors>=6.0.0",
    "openai>=1.0.0",
    # 过渡期保留，旧项目和 fallback 继续使用
    "zep-cloud==3.13.0",
    # 新增 Graphiti 自托管
    "graphiti-core>=0.3.0",
    "neo4j>=5.0.0",
    # ... 其余不变
]
```

不要在第一阶段删除 `zep-cloud`，否则无法保证旧项目兼容和生产回滚。

---

## 9. 工作量估算

| 任务 | 工作量 | 说明 |
|---|:---:|---|
| 创建抽象层 `graph_provider.py` | 0.5 天 | dataclass 定义 + Provider 接口 |
| 封装 `ZepProvider` | 0.5–1 天 | 包装现有 SDK 调用，行为不变 |
| 修改现有 Zep 调用点 | 1–1.5 天 | `graph_builder`、实体读取、报告工具、人设增强、模拟写回 |
| `graphiti_runtime.py` | 0.5–1 天 | 单例、后台事件循环、索引初始化、shutdown |
| `graphiti_queries.py` | 1–1.5 天 | 基于真实 schema 校准节点/边/删除/统计查询 |
| `graphiti_provider.py` | 2–3 天 | 构建、增量写入、搜索、查询、删除、健康检查 |
| 配置和基础设施变更 | 0.5–1 天 | config、.env、docker-compose、健康检查 |
| Provider contract tests | 1 天 | Zep mock 与 Graphiti 实现共用契约测试 |
| 中文 POC 与质量对比 | 1–2 天 | 固定样本，对比 Zep/Graphiti 抽取与搜索 |
| 完整 5 步集成测试 | 1 天 | Step1 → Step5，含报告和模拟写回 |
| **合计** | **10–12 天** | 不含模型 prompt 调优、历史项目批量重建 |

---

## 10. 五步迁移策略

### 第一步：收敛 Zep 调用（无 breaking change）

- 创建 `graph_provider.py` 和 `ZepProvider`。
- 修改 `GraphBuilderService`、`ZepEntityReader`、`ZepToolsService`、`OasisProfileGenerator`、`ZepGraphMemoryUpdater`，让它们通过 Provider 调用图谱能力。
- 默认 `GRAPH_PROVIDER=zep`，保证现有行为不变。
- 验证当前 Zep 路径可完整跑通 Step1–Step5。

### 第二步：搭建 Graphiti + Neo4j 最小环境

- 本地或测试机运行 Neo4j 5.26 Docker。
- 新增 `GRAPHITI_*` 配置。
- 创建 `graphiti_runtime.py`，验证 Graphiti 初始化、Neo4j 连接、索引/约束初始化成功。
- 用一个最小中文文档验证 `add_episode()` 写入和 `search()` 可用。

### 第三步：实现 GraphitiProvider POC

- 实现 `create_graph()`、`register_ontology()`、`add_texts()`、`list_nodes()`、`list_edges()`、`search()`、`delete_graph()` 的最小闭环。
- 固定 3–5 份中文样本，对比 Zep 与 Graphiti 的节点数、边数、核心实体覆盖、核心关系覆盖、搜索 Top-K。
- 根据真实 Neo4j schema 固化 `graphiti_queries.py`。
- 选择稳定的 LLM 和 Embedding 配置。

### 第四步：覆盖完整业务链路

- 改造 Step2 实体读取与人设生成。
- 改造 ReportAgent 的图谱搜索、全景读取、统计工具。
- 改造 Step3 动态模拟活动写回，优先使用有序 `add_episode()` 或小批顺序写入。
- 加入 Provider contract tests 和端到端集成测试。

### 第五步：灰度生产切换与回滚

- 项目元数据记录 `graph_provider`、`graph_backend`、`graph_schema_version`。
- 旧项目默认继续走 Zep；新项目可选择 Graphiti。
- 生产前完成完整 Step1–Step5 验收。
- 保留 `ZepProvider` fallback，一旦报告质量、搜索质量或构建稳定性不达标，可通过 `.env` 回滚。
- Neo4j 稳定运行后，再单独评估 FalkorDB 是否作为轻量后端。

---

## 11. POC 验收标准与风险规避

### 11.1 POC 数据集

至少选择：

1. 一个短中文 Markdown 文档。
2. 一个长中文 PDF。
3. 一个包含人物、组织、事件、地点、时间的复杂案例。
4. 一个新闻增量接入样本。
5. 一个完整 Step3 模拟运行日志样本。

### 11.2 量化验收指标

| 指标 | 目标 |
|---|---|
| 构建成功率 | ≥ 95% |
| 核心实体覆盖率 | ≥ Zep 结果的 80% |
| 核心关系覆盖率 | ≥ Zep 结果的 70% |
| 搜索 Top-5 可用率 | ≥ 80% |
| Step2 人设生成可用率 | 不低于当前 Zep 路径 |
| Step4 报告引用质量 | 人工评分不低于 Zep 路径 |
| 单项目构建耗时 | 不超过 Zep 路径 2 倍，或业务可接受 |
| 失败可恢复性 | 支持重试、错误可见、不会污染项目状态 |

### 11.3 必测接口

- `/api/graph/build`
- `/api/graph/task/<task_id>`
- `/api/graph/data/<graph_id>`
- `/api/simulation/entities/<graph_id>`
- `/api/simulation/prepare`
- `/api/simulation/start` with `enable_graph_memory_update=true`
- `/api/report/generate`
- `/api/report/chat`

### 11.4 风险与规避措施

| 风险 | 等级 | 规避措施 |
|---|:---:|---|
| 国产 LLM 结构化输出不稳定 | 高 | 选支持 JSON/schema 输出的模型；Graphiti 抽取加重试、错误日志和样本回归 |
| 中文实体关系抽取质量低于 Zep Cloud | 高 | POC 对比核心实体/关系覆盖率；必要时调整 ontology、prompt、chunk 策略和模型 |
| `add_episode_bulk()` 误用于动态时序写回 | 中 | 初始导入和动态写回接口分离；Step3 优先有序 `add_episode()` |
| 本体只保存在内存导致重启丢失 | 高 | ontology 以 `project.json`/registry 为事实源，内存只做缓存 |
| Graphiti schema 与示例 Cypher 不一致 | 中 | POC 后基于真实 Neo4j schema 固化查询，不写死单一关系类型 |
| asyncio 桥接线程安全问题 | 中 | 后台事件循环单例、锁保护、超时、shutdown/teardown、driver 生命周期管理 |
| Flask/Gunicorn 多 worker 状态不一致 | 中 | 不依赖进程内缓存保存关键状态；项目元数据持久化 provider/backend/ontology 信息 |
| 旧 Zep 项目无法在 Graphiti 中读取 | 高 | 旧项目默认 `graph_provider=zep`；活跃项目基于原始文档重建；导出导入只用于归档展示 |
| Neo4j 冷启动或资源不足 | 中 | healthcheck、启动预热、内存限制、分页查询、磁盘监控和备份 |
| 搜索结果结构与 ReportAgent 期望不一致 | 中 | Provider 归一化 `GraphSearchResult`；报告工具做回归测试 |
| Graphiti API 版本变更 | 低 | 固定 `graphiti-core` 版本，关注 changelog，Provider contract tests 兜底 |

---

## 12. 服务器部署注意事项

> 适用于当前生产服务器：IP 122.51.3.250，CentOS 7，Docker 部署模式

### 12.1 Docker 镜像优化（已完成的修改）

服务器端 Dockerfile 已完成以下关键修改，**无需重复操作**：
- `ENV UV_TORCH_BACKEND=cpu`：跳过 CUDA 包（nvidia-nccl-cu12 566MB、cudnn 674MB），大幅缩短构建时间
- `ENV UV_HTTP_TIMEOUT=600`：避免大包下载超时
- `.dockerignore` 排除 `backend/uv.lock`：防止 CUDA 锁文件污染 CPU 环境

### 12.2 Axios baseURL 修复（已完成）

`frontend/src/api/index.js` 中已将 `baseURL: 'http://localhost:5001'` 改为 `baseURL: ''`（相对路径），使远程访问时请求能正确经由 Vite proxy 转发到后端。

### 12.3 新增 Neo4j 后的资源评估

| 组件 | 内存占用 | 磁盘 |
|---|---|---|
| 原有后端容器 | ~300MB | ~500MB |
| 原有前端容器 | ~50MB | ~150MB |
| Neo4j 新增 | ~1.5GB（含页面缓存）| 10GB+ |
| **合计** | **~1.8GB** | **~10.7GB** |

建议确认服务器内存：若总内存 < 4GB，需调低 Neo4j 配置：
```yaml
NEO4J_dbms_memory_heap_max__size: 512m
NEO4J_dbms_memory_pagecache_size: 256m
```

### 12.4 CentOS 7 注意事项

CentOS 7 glibc 版本为 2.17，**源码部署不可行**，只能使用 Docker 方式。这与当前部署方式一致，无需变更。

### 12.5 查看 Neo4j + 后端实时日志

```bash
# 查看所有容器日志
docker compose logs -f

# 单独查看 Neo4j 日志
docker compose logs -f neo4j

# 单独查看后端日志
docker compose logs -f backend
```

---

## 参考资料

**Graphiti 官方**
- [Graphiti GitHub](https://github.com/getzep/graphiti)
- [Graphiti Quick Start](https://help.getzep.com/graphiti/graphiti/quick-start)
- [Graphiti Graph Namespacing](https://help.getzep.com/graphiti/core-concepts/graph-namespacing)
- [Graphiti 搜索文档](https://help.getzep.com/graphiti/working-with-data/searching)
- [Graphiti Neo4j Configuration](https://help.getzep.com/graphiti/configuration/neo-4-j-configuration)
- [Graphiti Custom Entity and Edge Types](https://help.getzep.com/graphiti/core-concepts/custom-entity-and-edge-types)

**Zep Cloud SDK 参考（当前使用）**
- [graph.create](https://help.getzep.com/sdk-reference/graph/create-graph)
- [graph.set_ontology](https://help.getzep.com/sdk-reference/graph/set-ontology)
- [graph.add_batch](https://help.getzep.com/sdk-reference/graph/add-data-in-batch-mode)
- [graph.search](https://help.getzep.com/sdk-reference/graph/search)

**国内云厂商**
- [腾讯云 GraphRAG](https://cloud.tencent.com/document/product/1759/128202)
- [阿里云 OpenSearch LLM 智能问答版](https://help.aliyun.com/zh/open-search/introduction-to-llm-intelligent-q-a-edition)
- [华为云知识图谱 KG](https://www.huaweicloud.com/product/nlpkg.html)
- [TuGraph GitHub](https://github.com/TuGraph-family/tugraph-db)

**开源替代方案**
- [Cognee GitHub](https://github.com/topoteretes/cognee)
- [Mem0 GitHub](https://github.com/mem0ai/mem0)
