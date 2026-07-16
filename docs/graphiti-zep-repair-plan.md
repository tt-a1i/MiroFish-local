# Graphiti 替代 Zep Cloud 复核、修复与后续建议

## 结论摘要

截至当前代码状态，项目里的 `Graphiti` 已经完成了对 `Zep Cloud` 主链路的大部分替代，能够支撑：

- Step1 建图与 ontology 注入
- Step2 实体读取、按类型过滤、画像生成、模拟准备
- Step3 模拟行为写回图谱
- Step4 报告生成、图谱搜索、节点/边查看、统计分析

但还不能宣称为“100% 完整替代 Zep Cloud 的所有能力”。当前更准确的判断是：

- 工程主链路已经打通，且关键一致性问题已修复
- Graphiti 侧已经形成可运行、可回归、可持续迭代的替代方案
- 仍有一部分能力属于“未完全等价”或“效果 parity 尚未验证完成”

## 本轮最终复核后的能力判断

### 已确认完成的替代能力

1. 图谱构建主链路已完成替代
   - `backend/app/services/graph_builder.py`
   - `backend/app/services/zep_graphiti_impl.py`
   - 文本 chunk 写入、episode 等待、节点/边读取已经可在 Graphiti 路径下完成

2. Ontology 已真正参与 Graphiti ingestion
   - `backend/app/services/zep_graphiti_impl.py`
   - `entity_types`、`edge_types`、`edge_type_map` 已注入 `add_episode()` / `add_episode_batch()`
   - 这意味着 Graphiti 不再只是“存储 ontology”，而是已经在抽取阶段消费类型约束

3. `reference_time` 时序语义已接入 Graphiti 写回链路
   - `backend/app/services/zep_adapter.py`
   - `backend/app/services/zep_graphiti_impl.py`
   - `backend/app/services/zep_graph_memory_updater.py`
   - Step3 现在使用 activity 原始时间戳，而不是写回时刻的 `now()`

4. `graph_id -> group_id` 多图隔离已贯通
   - `backend/app/services/zep_graphiti_impl.py`
   - 单点查询、全图查询、搜索都围绕 `group_id=graph_id` 执行
   - `get_node()` / `get_node_edges()` 已补隔离约束

5. 项目级 / 模拟级 backend 路由已基本补齐
   - `backend/app/models/project.py`
   - `backend/app/api/graph.py`
   - `backend/app/api/report.py`
   - `backend/app/api/simulation.py`
   - `backend/app/services/simulation_manager.py`
   - 已支持 `Project.graph_backend / graph_provider / graph_schema_version`
   - 已支持 `SimulationState.graph_backend`
   - 旧 simulation state 可在访问时自动补全 graph 上下文

6. 图谱展示与工具链已经消费 Graphiti 结果
   - `backend/app/services/zep_tools.py`
   - `frontend/src/components/GraphPanel.vue`
   - `invalid_at` / `expired_at` 已向前端透传
   - 报告工具链已能够使用 Graphiti 节点、边、搜索、统计能力

7. Step3 已具备最小结构化闭环
   - `backend/app/services/zep_graph_memory_updater.py`
   - Graphiti backend 优先写 `json episode`
   - 每条 activity 生成稳定 `activity_id`
   - 本地 outbox：`graph_memory_outbox.json`
   - 已成功 activity 可跳过，避免重放导致重复写入

8. Twitter profile 读取契约已补齐
   - `backend/app/services/simulation_manager.py`
   - 已支持 `twitter_profiles.csv`
   - 兼容历史 `twitter_profiles.json`

9. `graph_id` 作用域 guardrail 已进一步补强
   - `backend/app/api/graph.py`
   - `backend/app/api/report.py`
   - `backend/app/api/simulation.py`
   - 现在调试/读取类接口对孤立 `graph_id` 不再静默回退默认 backend，而是直接返回 404

## 本轮新增收尾修复

### 1. 时间语义标准化

- 新增 `backend/app/utils/time_utils.py`
- 提供：
  - `utc_now()`
  - `utc_now_iso()`
  - `parse_iso_datetime()`
- 目标：
  - 统一输出带时区 UTC 时间
  - 避免 naive datetime 导致 temporal 语义漂移

### 2. 动作日志与 IPC 时间戳统一为 UTC ISO8601

- `backend/scripts/action_logger.py`
- `backend/app/services/simulation_ipc.py`

说明：

- `action_logger.py` 为避免脚本直接依赖 Flask app 包，保留了脚本侧轻量 UTC 实现
- 这是刻意设计，不是遗漏

### 3. 非法 timestamp 不再回退当前时间

- `backend/app/services/zep_graph_memory_updater.py`
- 现在如果 activity timestamp 非法：
  - 不再 fallback `now()`
  - 直接记为 outbox `failed`
  - 避免错误事实带着错误时序进入 Graphiti

### 4. Simulation 侧 graph guardrail 补齐

- `backend/app/api/simulation.py`
- `backend/tests/test_graph_backend_guardrails.py`

修复内容：

- `/api/simulation/entities/<graph_id>`
- `/api/simulation/entities/<graph_id>/<entity_uuid>`
- `/api/simulation/entities/<graph_id>/by-type/<entity_type>`
- `/api/simulation/generate-profiles`

这些接口现在都要求 `graph_id` 必须绑定到项目元数据，避免孤立图谱 ID 在 cloud/graphiti 双后端场景下误走默认 backend。

### 5. 模拟启动链路补强

- `backend/app/services/simulation_runner.py`
- `backend/scripts/run_parallel_simulation.py`
- `backend/scripts/run_twitter_simulation.py`
- `backend/scripts/run_reddit_simulation.py`
- `backend/tests/test_simulation_runner.py`

修复内容：

- 三个模拟脚本继续显式依赖 `python-dotenv`
- `SimulationRunner.start_simulation()` 启动前新增独立环境预检
- 如果 `camel` / `oasis` / `dotenv` 没有安装完成，接口会直接返回明确错误，并提示执行 `cd backend && ./scripts/setup_simulation_env.sh`

这样可以保持本地运行、Docker 部署、服务器部署三种模式的行为一致：都必须先准备完整的模拟独立环境，再允许启动 simulation。

## 仍未完全替代的能力

下面这些项是当前仍不能宣称“完全替代 Zep Cloud”的部分。

### P1

#### 1. Cloud backend 的 Step3 仍不是结构化等价实现

- 文件：
  - `backend/app/services/zep_graph_memory_updater.py`
  - `backend/app/services/zep_cloud_impl.py`
- 当前状态：
  - Graphiti backend：`json episode + reference_time + activity_id + outbox`
  - Cloud backend：仍走 `text episode` 兼容模式
- 影响能力：
  - 无法宣称 cloud 与 graphiti 在 Step3 的写回语义完全等价
  - 结构化重放、字段级提取、后续 schema 演进能力仍偏弱

#### 2. SearchFilters 与更细粒度检索能力未深度接入

- 文件：
  - `backend/app/services/zep_graphiti_impl.py`
  - `backend/app/services/zep_tools.py`
  - `backend/app/api/report.py`
- 当前状态：
  - 已支持 `nodes / edges / both` 搜索
  - 已支持 `group_id` 作用域约束
  - 但未把更细粒度的 filters、属性过滤、时间过滤做成统一可编排能力
- 影响能力：
  - 搜索结果质量和可控性仍低于可定制的“完整图谱检索层”

#### 3. 搜索质量与中文抽取质量 parity 仍未被固定样本证明

- 文件：
  - `backend/app/services/zep_graphiti_impl.py`
  - `backend/app/services/report_agent.py`
  - `backend/app/services/zep_tools.py`
- 当前状态：
  - 工程上能跑通
  - 但“Graphiti 与 Zep Cloud 在中文知识抽取/搜索排序/报告问答质量是否等价”目前没有固定样本回归来证明
- 影响能力：
  - 不能把“代码已实现”直接等同于“效果已完全一致”

### P2

#### 1. Step3 outbox 仍是本地 JSON 文件，不是事务级队列

- 文件：
  - `backend/app/services/zep_graph_memory_updater.py`
- 当前状态：
  - 本地 `graph_memory_outbox.json` 足够支撑最小幂等闭环
  - 但不是数据库事务队列，也没有跨进程锁和更强恢复语义
- 影响能力：
  - 极端并发/异常恢复场景下的鲁棒性仍有限

#### 2. Provider contract tests 还不够完整

- 文件：
  - `backend/tests/`
- 当前状态：
  - 已有路由、状态、Step3、metadata、profile 读取测试
  - 但仍缺：
    - Graphiti / Cloud 行为对照测试
    - 搜索质量固定样本测试
    - 更完整的 provider contract tests
- 影响能力：
  - 后续升级 `graphiti-core` 或调整 provider 实现时，回归保护还不够强

#### 3. Graphiti 原生 schema 演进兼容性仍需持续保护

- 文件：
  - `backend/app/services/zep_graphiti_impl.py`
- 当前状态：
  - 目前对 `graphiti-core` 的搜索 API、Neo4j fallback 查询做了兼容处理
  - 但这类兼容层对上游 schema/API 变动较敏感
- 影响能力：
  - 后续升级依赖时仍需要额外契约测试兜底

## 修复优先级建议

### P0

本轮已完成，没有新的 P0 阻塞项残留。

### P1

1. 补 Cloud backend 的 Step3 结构化等价能力
2. 为 Graphiti 搜索层引入统一 filter 能力
3. 建固定样本集，验证中文抽取、搜索排序、报告工具链效果 parity

### P2

1. 将 outbox 从本地 JSON 升级为更强的持久化队列
2. 建 provider contract tests
3. 建 `graphiti-core` 升级回归验证流程

## 当前最终判断

如果问题是：

“项目中的 Graphiti 是否已经代替掉所有 Zep Cloud 的能力，相关方法是否都实现了，图谱构建/使用/写回能力是否都已经完成？”

那么当前最准确的答案是：

- 代码主链路上，Graphiti 已经基本代替了项目对 Zep Cloud 的核心依赖
- 相关关键方法已经基本补齐，尤其是建图、读图、搜索、模拟准备、报告生成、Step3 写回主链路
- 但还不能宣称“所有能力已经 100% 完整实现并与 Zep Cloud 完全等价”

更具体地说：

- “工程实现是否可用”：可以，且主链路已经打通
- “是否还有遗漏实现”：仍有，但主要集中在 P1/P2 的等价性、质量验证、检索过滤能力和更强持久化能力
- “是否已经完全替代 Zep Cloud”：还不能这么下结论

## 验证清单

已补测试覆盖的方向：

- 项目级 graph metadata 持久化
- report status API 契约
- simulation backend 路由
- graph/report/simulation 的 backend guardrail
- simulation profile 读取契约
- Step3 写回幂等、重试、json episode、非法 timestamp 失败策略

建议每次合并前至少执行：

```bash
python -m py_compile backend/app/utils/time_utils.py \
backend/app/services/zep_graph_memory_updater.py \
backend/app/services/simulation_ipc.py \
backend/scripts/action_logger.py

cd backend && ../backend/.venv/bin/pytest
```
