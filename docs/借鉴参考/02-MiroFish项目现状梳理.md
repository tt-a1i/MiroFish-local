# MiroFish-local 项目现状梳理

## 当前定位

MiroFish-local 当前是一个“传播推演专用流水线”，不是完整的通用决策机。它通过图谱构建、Agent 人设生成、OASIS 双平台社交仿真、报告 Agent 和深度互动，实现对事件传播、舆情走向、群体反应的模拟和分析。

项目 README 已将主流程定义为：

```text
种子输入 -> 图谱构建 -> 环境搭建 -> 并行模拟 -> 报告生成 -> 深度交互
```

对应文件：

- `README.md:52` 定义系统架构。
- `README.md:74` 到 `README.md:82` 描述图谱构建、环境搭建、开始模拟、报告生成和深度互动。
- `README.md:88` 将舆情预测与危机公关预演列为核心场景。

## 技术栈

| 层级 | 技术 |
| --- | --- |
| 前端 | Vue 3、Vite、Vue Router、Axios、D3、marked |
| 后端 | Flask、OpenAI SDK、LangChain 风格 ReACT 报告逻辑 |
| 图谱/记忆 | Zep Cloud 或 Graphiti + Neo4j 本地模式 |
| 仿真引擎 | OASIS / CAMEL，独立 `.venv-simulation` 子进程运行 |
| 文件与状态 | 项目状态、模拟状态、报告文件、actions.jsonl、agent_log.jsonl |

关键文件：

- `frontend/package.json:11` 记录前端依赖。
- `backend/pyproject.toml:11` 记录 Flask、OpenAI、Zep 等后端依赖。
- `backend/pyproject.toml:40` 记录 OASIS 依赖。
- `backend/app/services/simulation_runner.py:37` 说明 OASIS 与 Graphiti 的 Neo4j 依赖冲突，因此使用独立模拟环境。

## 当前用户流程

### Step1：事件输入与图谱构建

用户输入事件文本或上传 PDF/MD/TXT，前端会展示“输入任意事件，即刻推演未来”。对应：

- `frontend/src/components/Step1GraphBuild.vue:4`
- `frontend/src/components/Step1GraphBuild.vue:165`

Step1 主要做三件事：

1. 对用户输入或联网搜索材料进行摘要。
2. 生成 2 到 3 条推演方向建议。
3. 生成实体提示，供后续图谱抽取使用。

后端服务：

- `backend/app/services/seed_analysis_service.py:26` 定义 `SeedAnalysisService`。
- `backend/app/services/seed_analysis_service.py:132` 生成 seed 分析 JSON。
- `backend/app/api/graph.py:340` 提供 Web Search seed 创建。
- `backend/app/api/graph.py:434` 提供流式搜索处理。

### Step2：环境搭建与智能体人设生成

Step2 会创建模拟实例、生成智能体人设、生成双平台模拟配置。前端位置：

- `frontend/src/components/Step2EnvSetup.vue:66` 生成智能体人设。
- `frontend/src/components/Step2EnvSetup.vue:138` 生成双平台模拟配置。
- `frontend/src/components/Step2EnvSetup.vue:158` 展示时间配置、Agent 配置等。

后端服务：

- `backend/app/services/simulation_manager.py:258` 开始准备模拟。
- `backend/app/services/simulation_manager.py:309` 读取并过滤图谱实体。
- `backend/app/services/simulation_manager.py:433` 生成 OASIS Agent profile。
- `backend/app/services/simulation_manager.py:519` 生成模拟配置。

### Step3：双平台传播推演

Step3 使用 Twitter / Reddit 风格的双平台并行模拟。前端会展示轮次、模拟时间、动作数和动作流：

- `frontend/src/components/Step3Simulation.vue:1`
- `frontend/src/components/Step3Simulation.vue:113`
- `frontend/src/components/Step3Simulation.vue:138`

启动参数包含 `platform: parallel`，并可开启图谱记忆更新：

- `frontend/src/components/Step3Simulation.vue:488`

后端运行器：

- `backend/app/services/simulation_runner.py:443` 启动模拟子进程。
- `backend/app/services/simulation_runner.py:642` 监控动作日志。
- `backend/app/services/simulation_runner.py:1148` 提供按轮次汇总的 timeline。

### Step4：报告生成

报告 Agent 采用 ReACT 模式，要求先调用工具观察模拟世界，再生成报告。关键文件：

- `backend/app/services/report_agent.py:621` 定义 `ReportAgent`。
- `backend/app/services/report_agent.py:682` 定义工具。
- `backend/app/services/report_agent.py:957` 规划报告大纲。
- `backend/app/services/report_agent.py:1102` 逐章节生成报告。

工具包括：

- `insight_forge`
- `panorama_search`
- `quick_search`
- `interview_agents`

这些工具定义和执行逻辑位于：

- `backend/app/services/zep_tools.py:385`
- `backend/app/services/report_agent.py:682`

### Step5：深度互动

Step5 支持与报告智能体对话、与单个 Agent 对话、批量问卷。前端关键入口：

- `frontend/src/components/Step5Interaction.vue:80`
- `frontend/src/components/Step5Interaction.vue:749`
- `frontend/src/components/Step5Interaction.vue:779`
- `frontend/src/components/Step5Interaction.vue:910`

后端 `AgentDialogueService` 会将 `simulation_requirement` 作为本次推演背景注入人设对话：

- `backend/app/services/agent_dialogue_service.py:337`

## 已有的决策推演底座能力

### 1. 场景上下文

`simulation_requirement` 贯穿图谱、本体、模拟配置、人设对话和报告生成。它可以升级为“决策问题定义”的核心字段。

已有位置：

- `backend/app/models/project.py:39`
- `backend/app/api/graph.py:565`
- `backend/app/services/agent_dialogue_service.py:337`

### 2. 主体与行为参数

`AgentActivityConfig` 已包含活跃度、发言频率、响应延迟、情感倾向、立场和影响力权重：

- `backend/app/services/simulation_config_generator.py:52`

这些参数天然适合作为传播干预和情景比较的变量。

### 3. 平台传播机制

`PlatformConfig` 已包含推荐权重、病毒传播阈值和回声室强度：

- `backend/app/services/simulation_config_generator.py:130`

这些参数可以被改造成“平台策略变量”，例如平台降权、推荐放大、热度阈值调整。

### 4. 多主体社交仿真

OASIS 双平台运行支持发帖、评论、点赞、转发、搜索、关注等动作。当前行动空间与社交平台绑定，但已经具备“多主体策略互动”的雏形。

相关位置：

- `backend/scripts/run_parallel_simulation.py:176`
- `frontend/src/components/Step3Simulation.vue:33`
- `frontend/src/components/Step3Simulation.vue:74`

### 5. 模拟内事件流

模拟动作会写入 `actions.jsonl`，可用于状态监控、时间线、报告和图谱记忆更新。动作日志结构可以扩展为指标评估数据源。

相关位置：

- `backend/scripts/action_logger.py:56`
- `backend/app/api/simulation.py:2016`

### 6. 图谱记忆写回

模拟动作可作为 episode 写回图谱，形成动态记忆：

- `backend/app/services/zep_graph_memory_updater.py:254`
- `backend/app/services/zep_graph_memory_updater.py:462`
- `backend/app/services/zep_graph_memory_updater.py:810`

这为“外部信号写回”和“推演更新闭环”提供了现成骨架。

### 7. 报告工具化

报告 Agent 已具备工具调用、日志记录、章节生成和报告持久化能力：

- `backend/app/services/report_agent.py:621`
- `backend/app/services/report_agent.py:682`
- `backend/app/services/report_agent.py:456`

这适合扩展 `signal_search`、`evidence_matrix`、`scenario_compare`、`probability_assess` 等工具。

## 当前主要缺口

| 缺口 | 说明 |
| --- | --- |
| 决策抽象缺失 | 缺少 `DecisionScenario`、`Intervention`、`OutcomeMetric`、`EvaluationResult` 等统一模型 |
| 干预机制缺失 | 目前主要是重新推演，缺少第 N 轮发布声明、调整平台权重、改变 Agent 立场等干预 |
| 多情景对照弱 | 没有同一项目下的情景分支、快照、横向对比报告 |
| 指标评估弱 | 有动作数和时间线，但没有传播峰值、负面情绪、关键节点扩散等指标服务 |
| 外部信号持续追踪缺失 | 只有一次性 Web Search，没有定时追踪、信号去重、信号状态机和告警 |
| 概率校准缺失 | `info_confidence` 用于真实实体核验，不是预测概率或情景置信度 |
| 报告偏叙述 | 报告能解释趋势，但缺少固定的条件化判断、概率区间、反证和待观察信号 |

## 最适合的扩展切入点

1. 在 `Project` 和 `SimulationState` 上扩展决策元数据：场景、干预、目标指标、分支 ID。
2. 在 `SimulationConfigGenerator` 旁新增 `DecisionScenarioGenerator`，把推演需求转为结构化决策场景。
3. 在 `SimulationRunner` 外层新增环境适配器接口，把 OASIS 作为第一个 `SocialPropagationEnvironment`。
4. 基于 `actions.jsonl` 新增指标评估服务，输出传播速度、峰值、情绪、关键节点等指标。
5. 在 `ZepGraphMemoryUpdater` / `ZepAdapter` 旁新增外部信号 episode 写入规范。
6. 在 `ZepToolsService` 和 `ReportAgent` 新增信号检索、证据矩阵、情景对比和概率评估工具。
7. 在前端 Step1/Step2/Step3/Step4/Step5 分别新增结构化问题、情景设计、信号追踪、情景对比报告、互动验证回写入口。

