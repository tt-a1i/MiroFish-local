# 02 图谱构建双模型并发分摊设计

## 背景与目标

MiroFish 的 Step 1 会先生成本体，再进入 02 图谱构建。当前 Graphiti 本地图谱构建会把原文切成多个 chunk，每个 chunk 作为 episode 写入 Graphiti，由 Graphiti 调用 LLM 抽取实体、关系，再执行 embedding 与 Neo4j 写入。真实使用中，02 阶段可能等待十几到二十分钟，用户会长期停留在“构建中”，体验成本过高。

本次目标是在不破坏现有图谱质量、Neo4j 写入稳定性和 embedding 逻辑的前提下，实现“LLM_BASE 与 LLM_BOOST 同时分摊图谱抽取任务”。这里的“同时分摊”不是对同一个 chunk 重复抽取后合并，而是把不同 chunk/episode 分配给不同 LLM endpoint，让两个模型并行处理同一轮图谱构建中的不同文本块，从而缩短总等待时间。

实现版本同时加入批次级备用路由重试：当某个 batch 的首选模型路由在 Graphiti 写入过程中超时或失败，且双模型池里存在另一路由时，该 batch 会切换到备用模型再试一次。这个能力默认开启，但仍受应用层并发和 Graphiti 超时配置保护。

## 当前业务流程

1. 前端在 Step1GraphBuild 的 02 图谱构建阶段调用 `/api/graph/build`。
2. 后端 `build_graph` 读取项目文本、本体、推演需求和 seed 摘要，构建 `extraction_context`。
3. `GraphBuilderService(build_mode=True)` 创建 Graphiti client。当前实现会优先选择 `LLM_BOOST_*`，若未配置则回退 `LLM_*`。
4. 文本通过 `TextProcessor.split_text` 切块。
5. `add_text_batches` 根据 `GRAPH_BUILD_BATCH_SIZE`、`GRAPH_BUILD_CONCURRENCY`、`GRAPHITI_EPISODE_BATCH_SIZE`、`GRAPHITI_INGEST_CONCURRENCY` 计算实际批次计划。
6. Graphiti 后端默认禁用 bulk ingestion，实际通常按单条 episode 写入。
7. 每个 episode 写入时，Graphiti 内部调用 LLM 抽取实体/关系，调用 embedding 生成向量，并写入 Neo4j。
8. 后端读取 Graphiti 节点和边，做重复实体合并、地点过滤，更新任务结果和项目状态。

当前并发已经存在两层：

- 应用层 episode/批次并发：`GRAPH_BUILD_CONCURRENCY` 与 `GRAPHITI_INGEST_CONCURRENCY` 控制。
- Graphiti 内部 LLM 并发：`GRAPHITI_LLM_CONCURRENCY` 控制单个 Graphiti client 内部的 LLM 请求并发。

但当前 LLM endpoint 只有一个：配置了 boost 时，02 阶段只走 boost；未配置时只走 base。

## 主要瓶颈与约束

### LLM 抽取是主要耗时

每个 episode 的 Graphiti ingestion 会触发多次 LLM 请求，例如抽取节点、抽取边、去重/resolve 等。模型延迟和限流会直接决定 02 阶段等待时间。

### Neo4j 与 embedding 不能无限并发

把两个模型并行起来之后，整体 episode 完成速度会变快，但 embedding 与 Neo4j 写入也会被更频繁触发。如果只盲目提高线程数，可能出现：

- embedding endpoint 429 或超时；
- Neo4j driver 写入排队；
- Graphiti resolve 阶段冲突增多；
- 大量 worker client 反复初始化索引与连接，反而拖慢。

### Graphiti 写入天然具备 group_id 隔离

所有 episode 都写入同一个 `graph_id/group_id`。不同模型处理不同 chunk 后，Graphiti 仍会在同一个图谱空间中进行实体和关系写入，最终读取阶段已有重复实体合并与地点过滤能力。

### Zep Cloud 不适合本次双模型分摊

Zep Cloud 的 LLM 抽取由云端封装，应用层无法传入不同 LLM endpoint。本次能力只对 `ZEP_BACKEND=graphiti` 生效；cloud 后端保持串行/原有能力。

## 方案选择

### 方案 A：同 chunk 双模型重复抽取后合并

两个模型同时处理同一个 chunk，再在应用层合并实体/关系。

优点：可能提升召回率。

缺点：成本翻倍，时间未必下降；需要实现 Graphiti 抽取结果中间态合并，风险大；同一事实重复写入更多，Neo4j 压力更高。

结论：不适合“加速 02 等待”的当前目标。

### 方案 B：chunk 级分摊到不同模型

把 chunk/episode 按权重分配给 base 与 boost 两个 endpoint，并发写入同一个 graph_id。

优点：实现风险低，真正减少总耗时；复用 Graphiti 原有 ontology、embedding、Neo4j 写入；保留已有去重与过滤逻辑。

缺点：不同模型抽取风格可能略有差异；需要控制调度权重和并发上限。

结论：推荐实现。

### 方案 C：base 与 boost 作为故障转移

默认走 boost，boost 失败后将失败 chunk 交给 base 重试。

优点：稳定性强。

缺点：不能显著缩短正常路径耗时。

结论：作为 B 的补充，不作为主目标。

## 推荐设计

### 核心思路

在 `GraphBuilderService` 中引入“图谱构建 LLM 端点池”：

- base endpoint：`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_NAME`。
- boost endpoint：`LLM_BOOST_API_KEY`、`LLM_BOOST_BASE_URL`、`LLM_BOOST_MODEL_NAME`。

当满足以下条件时启用双模型分摊：

- `ZEP_BACKEND=graphiti`；
- `build_mode=True`；
- base endpoint 可用；
- boost endpoint 完整且不是示例占位 key；
- 配置项 `GRAPH_BUILD_DUAL_LLM_ENABLED=true`。

不满足条件时保持现有行为：优先 boost，缺失则 base。

### 调度策略

新增 `LLMEndpointPool`：

- 包含一个或多个 `LLMEndpoint`；
- 每个 endpoint 有 `route_name`，取值 `base` 或 `boost`；
- 支持权重轮询，例如默认 `base:1, boost:1`；
- 支持只在 Graphiti build_mode 中启用。

默认权重：

- `GRAPH_BUILD_LLM_BASE_WEIGHT=1`
- `GRAPH_BUILD_LLM_BOOST_WEIGHT=1`

如果 boost 模型更快，可以配置 `base:1, boost:2` 或 `base:1, boost:3`，让更多 chunk 交给 boost。调度以 batch/chunk 顺序做确定性轮询，避免同一轮运行中随机分配导致问题难复现。

### worker client 策略

当前 `add_text_batches` 在并发时为每个 batch 临时创建 worker client，并把同一个 `_llm_endpoint` 传入。改造后：

1. `GraphBuilderService` 持有 `_llm_endpoint_pool`。
2. `submit_batch` 根据 `batch_index` 从 endpoint pool 中选择一个 endpoint。
3. `create_worker_client(endpoint)` 创建独立 Graphiti client，并传入该 endpoint。
4. `set_ontology_from_cache` 复制主 client 的 ontology 缓存。
5. 每个 worker client 处理完当前 batch 后关闭。

这样不同 batch 可以同时使用不同模型处理不同 chunk，且仍写入同一个 graph_id。

### 主 client 角色

主 client 继续负责：

- 创建图谱元数据；
- 缓存 ontology；
- 构建完成后读取节点和边；
- 删除图谱。

主 client 不必参与所有 episode 写入。并发 worker 负责实际 Graphiti ingestion。

当 `concurrency <= 1` 或只有一个 batch 时，仍可按 endpoint pool 分配，但运行效果接近串行；这保证配置保守时不会扩大压力。

### embedding 与 Neo4j 保护

双模型分摊只改变 Graphiti LLM client，不改变 embedder。embedding 仍由 `GRAPHITI_EMBEDDING_*` 控制，继续复用：

- `GRAPHITI_EMBEDDING_BATCH_SIZE`
- `GRAPHITI_EMBEDDING_MIN_INTERVAL_SECONDS`
- `GRAPHITI_EMBEDDING_REQUEST_TIMEOUT_SECONDS`
- 限流重试包装器

Neo4j 写入保护依赖：

- `GRAPHITI_INGEST_CONCURRENCY` 控制应用层并发 worker 数；
- Graphiti 专用 async loop 保持现有同步桥接；
- `GRAPHITI_LLM_CONCURRENCY` 控制单个 Graphiti client 内部 LLM 请求并发。

推荐默认值保持保守：

- `GRAPH_BUILD_CONCURRENCY=2`
- `GRAPHITI_INGEST_CONCURRENCY=2`
- `GRAPHITI_LLM_CONCURRENCY=1 或 2`
- `GRAPHITI_EMBEDDING_MIN_INTERVAL_SECONDS=0.5`

这意味着“双模型”默认不会把整体并发放大到不可控，而是把两个并发 worker 分别路由到不同模型。

### 失败与降级

本轮实现采用“批次级备用路由重试 + 整体 fail-fast”：

- 每个 batch 先按权重轮询选择首选 route；
- 若首选 route 失败，且 `GRAPH_BUILD_LLM_ROUTE_RETRY_ENABLED=true`，会从 endpoint pool 里选择另一个 route 对同一 batch 重试一次；
- 重试成功后，`llm_route_counts` 只统计最终成功写入的 route；
- 重试仍失败时，任务整体失败，并继续复用现有 429、quota、timeout 友好提示；
- 并发调度使用滑动窗口，某个 batch 失败后不再提交新的未开始 batch，并尽量取消已排队但未执行的 future。

幂等风险说明：Graphiti 单条 episode 写入是同步的，失败可能发生在 LLM 抽取、embedding 或 Neo4j 写入的不同阶段。备用路由重试不能绝对保证上游没有留下部分写入，因此仍保留既有的实体合并、边去重和地点过滤读取逻辑，并建议真实生产默认维持 `GRAPHITI_EPISODE_BATCH_SIZE=1`、`GRAPHITI_INGEST_CONCURRENCY=2` 的保守配置。

### 任务进度与可观测性

`progress_detail` 增加：

- `dual_llm_enabled`
- `llm_routes`
- `llm_route_weights`
- `llm_route_counts`

日志增加：

- 图谱写入计划中打印 routes 与权重；
- 每个 batch 开始时打印 route/model；
- 全部完成时打印每个 route 处理的 batch 数。

前端可先不新增 UI，只继续显示当前百分比。后续可以在 02 卡片中显示“双模型加速中”，但本轮优先保证后端能力。

### 配置项

新增配置：

```bash
# 是否在 Graphiti 图谱构建中让 LLM_BASE 与 LLM_BOOST 同时分摊 episode 抽取任务。
GRAPH_BUILD_DUAL_LLM_ENABLED=true

# 双模型分摊权重；数值越大，该模型分到的 batch 越多。
GRAPH_BUILD_LLM_BASE_WEIGHT=1
GRAPH_BUILD_LLM_BOOST_WEIGHT=1

# 某个模型路由失败时，是否切换另一路由重试该批次一次。
GRAPH_BUILD_LLM_ROUTE_RETRY_ENABLED=true
```

兼容原则：

- 不配置 `GRAPH_BUILD_DUAL_LLM_ENABLED` 时默认开启双模型分摊，但只有 boost 三项完整才真正生效。
- boost 不完整或为占位 key 时，自动退回默认单模型。
- cloud 后端无效，保持原行为。

## 实现范围

1. `backend/app/config.py`
   - 增加双模型开关和权重配置。

2. `backend/app/utils/llm_routing.py`
   - 增加 base endpoint 显式获取；
   - 增加 endpoint route 元数据；
   - 增加构建 graph build endpoint pool 的函数。

3. `backend/app/services/graph_builder.py`
   - 构造阶段保存 endpoint pool；
   - 创建 worker client 时按 batch 选择 endpoint；
   - 进度和日志输出双模型路由信息；
   - 首选 route 失败后可切换备用 route 重试当前 batch；
   - 并发调度采用滑动窗口，失败后不再继续提交新 batch；
   - 保持非 Graphiti、非 build_mode 兼容。

4. `.env.example` 与 `.env.local.example`
   - 增加配置说明。

5. 测试
   - LLM 路由测试：boost 完整时 pool 包含 base+boost，boost 缺失时 pool 只有 preferred endpoint。
   - GraphBuilder 测试：多个 batch 会按权重分配不同 endpoint，worker client 收到对应 endpoint。
   - API 测试：`progress_detail` 记录 dual LLM 信息。

## 测试策略

### 自动化测试

运行：

```bash
cd backend && uv run pytest backend/tests/test_llm_routing.py backend/tests/test_graph_build_parameters.py
```

覆盖点：

- 单模型兼容；
- 双模型 pool 构建；
- 权重轮询；
- Graphiti 并发写入时不同 batch 使用不同 endpoint；
- API 任务结果记录路由配置。

### 真实本地测试

选择 `backend/uploads/projects` 或当前已有项目元数据中的一个已完成本体项目，使用 `/api/graph/build` 强制重建小样本图谱，观察：

- 任务是否完成；
- Neo4j 中节点和边可读取；
- 日志中是否出现 base 与 boost 两条 route；
- `ingest_elapsed_seconds` 是否可记录；
- 未出现 embedding 或 Neo4j 连接异常。

若本地 Neo4j 或 LLM 网络不可用，需明确记录失败原因和风险。

## 风险与后续优化

1. 两个模型抽取风格不同，可能导致实体命名差异增加。现有读取阶段已有重复实体合并，但后续可加强同义实体归并。
2. 如果 base 明显慢于 boost，`1:1` 可能仍受慢模型尾部拖累。可调成 `base:1, boost:2` 或 `base:1, boost:3`。
3. 如果 embedding 成为新瓶颈，需要单独提升 embedding endpoint 或调大 batch size，而不是继续提高 LLM 并发。
4. 后续可实现失败 batch 换路由重试，但需要 episode 幂等 key 或写入前去重策略配合。
