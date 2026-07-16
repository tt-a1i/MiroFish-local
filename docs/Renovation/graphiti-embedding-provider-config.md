# Graphiti Embedding 能力独立配置改造方案

## 背景

当前 Step1 图谱构建在 `ZEP_BACKEND=graphiti` 时使用 Graphiti + Neo4j 本地图谱后端。Graphiti 在 `add_episode` / `add_episode_bulk` 中会先调用 LLM 抽取实体和关系，再对实体名称与关系事实生成 embedding，最后将节点、边和向量属性写入 Neo4j。

现有实现中，Graphiti embedding 默认复用 `OPENAI_API_KEY` / `OPENAI_BASE_URL`，而这两个变量又会从项目级 `LLM_API_KEY` / `LLM_BASE_URL` 自动映射。因此当项目默认 LLM 使用阿里百炼时，Graphiti embedding 也会走百炼 `text-embedding-v4`。在百炼 embedding 限流较紧时，Step1 会在图谱写入阶段触发 429，导致 Neo4j 中无法稳定落入完整图谱。

## 使用面核查

代码层面实际执行 embedding 的位置集中在 Graphiti 适配器与 graphiti-core 内部：

- `backend/app/services/zep_graphiti_impl.py`：创建 Graphiti embedder、处理 embedding 限速重试和批量分块。
- `graphiti_core` 内部：在 `add_episode` / `add_episode_bulk` 期间生成 `name_embedding` 和 `fact_embedding`。

以下模块当前主要使用 chat completions 或 OASIS 模型工厂，没有直接调用 embeddings API：

- 双平台模拟脚本：`backend/scripts/run_parallel_simulation.py`、`run_reddit_simulation.py`、`run_twitter_simulation.py`。
- 人设生成：`backend/app/services/oasis_profile_generator.py`。
- 模拟配置生成：`backend/app/services/simulation_config_generator.py`。
- Agent 对话：`backend/app/services/agent_dialogue_service.py`。
- 报告生成：`backend/app/services/report_agent.py`。

因此本次改造只需要收敛 Graphiti embedding 通道，不需要改模拟、人设、报告或 Agent 对话的 LLM 配置。

## 目标

1. Graphiti embedding 可以独立配置 OpenAI-compatible embeddings endpoint。
2. 未配置独立 endpoint 时，保持现有行为：继续复用 `OPENAI_*` / `LLM_*` 和 `GRAPHITI_EMBEDDING_MODEL=text-embedding-v4`。
3. 独立 embedding 配置不能影响 Graphiti 的 LLM 实体/关系抽取。
4. 独立 embedding 配置不能改变 Graphiti 写入 Neo4j 的调用方式，只替换向量生成来源。
5. embedding 维度和 batch size 必须可配置，避免模型切换后向量截断或批量请求失败。

## 新增配置

```env
# 可选：Graphiti embedding 独立 OpenAI-compatible endpoint。
# 为空时回退 OPENAI_BASE_URL；OPENAI_BASE_URL 又会按现有逻辑从 LLM_BASE_URL 映射。
GRAPHITI_EMBEDDING_BASE_URL=

# 可选：embedding 服务 API Key。无鉴权内网服务可留空，后端会使用 dummy key 创建 OpenAI SDK client。
GRAPHITI_EMBEDDING_API_KEY=

# Graphiti embedding 模型。默认继续使用百炼 text-embedding-v4。
GRAPHITI_EMBEDDING_MODEL=text-embedding-v4

# Graphiti 写入 Neo4j 的向量维度。graphiti-core 默认是 1024；Qwen3-Embedding-4B 实测为 2560。
GRAPHITI_EMBEDDING_DIM=1024

# Graphiti create_batch 分块大小。百炼建议 10；内网服务可按吞吐能力调大。
GRAPHITI_EMBEDDING_BATCH_SIZE=10
```

接入用户提供的第三方服务时建议配置：

```env
GRAPHITI_EMBEDDING_BASE_URL=http://10.200.89.13:9997/v1
GRAPHITI_EMBEDDING_API_KEY=
GRAPHITI_EMBEDDING_MODEL=Qwen3-Embedding-4B
GRAPHITI_EMBEDDING_DIM=2560
GRAPHITI_EMBEDDING_BATCH_SIZE=32
```

## 影响边界

### 不影响 LLM 抽取

Graphiti LLM client 仍由 `GRAPHITI_LLM_MODEL`、`OPENAI_API_KEY`、`OPENAI_BASE_URL` 或 Step1 build mode 中的 boost LLM endpoint 决定。新增的 `GRAPHITI_EMBEDDING_BASE_URL` 不会写回 `OPENAI_BASE_URL`，因此不会把实体/关系抽取请求错误发送到 embedding-only 服务。

### 不影响 Neo4j 写入机制

Graphiti 写入 Neo4j 的流程仍是：

1. 保存 episode。
2. 调 LLM 抽取节点和边。
3. 调 embedder 生成 `name_embedding` / `fact_embedding`。
4. 调 graphiti-core 的 Neo4j driver 写入节点、边和向量属性。

本次改造只替换第 3 步的 endpoint/model/dim/batch 参数，不改变第 1、2、4 步。

### 维度迁移注意

graphiti-core 0.25.5 的 `OpenAIEmbedder` 会按 `embedding_dim` 截断返回向量。Qwen3-Embedding-4B 接口实测返回 2560 维，如果不配置 `GRAPHITI_EMBEDDING_DIM=2560`，最终写入 Neo4j 的只有前 1024 维。

同一个 `group_id` 内不建议混用不同维度的向量。切换 embedding 维度后，建议重新构建图谱或清理旧图谱数据后重建。

## 验收标准

1. 未配置 `GRAPHITI_EMBEDDING_BASE_URL` 时，Graphiti embedding 继续使用现有百炼配置。
2. 配置 `GRAPHITI_EMBEDDING_BASE_URL=http://10.200.89.13:9997/v1` 和 `GRAPHITI_EMBEDDING_MODEL=Qwen3-Embedding-4B` 后，Graphiti embedding 使用第三方服务。
3. `GRAPHITI_EMBEDDING_DIM=2560` 能传入 `OpenAIEmbedderConfig`。
4. `GRAPHITI_EMBEDDING_BATCH_SIZE` 能控制 Graphiti batch embedding 分块。
5. 单元测试覆盖 fallback、独立 endpoint、维度、分块和限流重试包装逻辑。
