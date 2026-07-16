# 传播推演决策引擎升级计划

## 计划目标

将 MiroFish-local 从“单次传播推演 + 预测报告”升级为“结构化决策问题 + 多情景传播推演 + 指标对比 + 外部信号追踪 + 决策型报告”的传播推演决策引擎。

## 阶段 0：准备与边界确认

周期：1 周

### 任务

1. 确认新增能力默认不破坏现有五步流程。
2. 明确 MVP 只做传播/舆情领域，不扩展到金融、地缘、企业战略等泛化场景。
3. 确认数据存储策略：MVP 优先 JSON 文件，后续再独立数据库模型。
4. 确认外部信号追踪是否允许联网；无联网时仅支持用户手动补充信号。

### 产出

- 最终需求确认文档
- 技术方案评审记录
- MVP 范围表

### 验收标准

- 开发范围不影响现有上传、建图、模拟、报告、互动流程。
- 明确哪些能力第一版不做。

## 阶段 1：决策问题结构化 MVP

周期：1 到 2 周

### 后端任务

1. 新增 `backend/app/services/decision_scenario_service.py`。
2. 定义 `DecisionContext` schema。
3. 基于 `seed_summary_md`、`simulation_requirement`、`entity_hints` 生成结构化决策问题。
4. 给 Project 增加 `decision_context` 保存能力。
5. 新增 API：`POST /api/decision/context/generate`。

### 前端任务

1. 在 Step1 推演方向区域新增“结构化推演问题”展示。
2. 支持用户编辑决策目标、时间范围、关键变量、干预动作、目标指标。
3. 在进入 Step2 前保存结构化结果。

### 测试

1. 单元测试：LLM JSON 解析、兜底、字段校验。
2. 手动测试：上传文件和联网搜索两种入口。

### 验收标准

- 给定任意事件材料，能生成结构化决策问题。
- 用户可编辑并保存。
- 原有 Step1 不启用该能力时仍可继续。

## 阶段 2：情景配置与快照

周期：2 到 3 周

### 后端任务

1. 新增 `backend/app/services/scenario_manager.py`。
2. 新增 `Scenario` JSON 存储。
3. 支持从 baseline simulation config 复制情景。
4. 支持保存 `changed_variables` 和 `interventions`。
5. 支持情景绑定 simulation_id。
6. 新增 API：`GET/POST/PUT /api/scenarios`。

### 前端任务

1. 在 Step2 新增情景页签。
2. 支持创建 baseline、复制情景、编辑情景名称。
3. 支持展示 config snapshot。
4. 把“重新推演”改造成“新建对照情景”的入口之一。

### 测试

1. 创建情景、复制情景、更新情景。
2. 确认多个情景不会覆盖彼此日志和报告。

### 验收标准

- 同一 project 下可存在多个 scenario。
- 每个 scenario 可独立关联 simulation。
- 情景配置可追溯。

## 阶段 3：基础指标评估

周期：1 到 2 周

### 后端任务

1. 新增 `backend/app/services/simulation_metrics_service.py`。
2. 读取 twitter/reddit actions.jsonl。
3. 计算基础动作指标、峰值指标、平台分布、Top Agent。
4. 保存 metrics JSON。
5. 新增 API：`GET /api/simulation/{simulation_id}/metrics`。

### 前端任务

1. Step3 增加指标卡片。
2. Step4 报告生成前展示指标摘要。
3. 报告中可引用指标。

### 测试

1. 使用已有 actions.jsonl 样本生成指标。
2. 测试空日志、单平台日志、异常 JSON 行。

### 验收标准

- 模拟结束后可生成 metrics。
- 指标生成不会阻塞报告流程。
- 指标结果可被对比服务使用。

## 阶段 4：干预机制 MVP

周期：2 到 4 周

### 后端任务

1. 新增 `backend/app/services/intervention_planner.py`。
2. 支持内容干预、平台干预、主体干预三类 MVP。
3. 将干预应用到 `SimulationParameters`。
4. 增加干预校验和错误提示。

### 前端任务

1. Step2 增加干预编辑器。
2. 提供常用模板：
   - 快速官方回应
   - 延迟回应
   - 媒体介入
   - KOL 背书
   - 平台降权
3. 展示干预对配置字段的影响。

### 测试

1. 干预动作可正确写入 config。
2. 模拟脚本能读取并应用干预。
3. 干预配置非法时前后端均有提示。

### 验收标准

- 用户可以创建至少 3 种传播干预情景。
- 干预能影响模拟结果或至少明确进入初始/定时事件配置。

## 阶段 5：多情景对比报告

周期：2 到 3 周

### 后端任务

1. 新增 `backend/app/services/scenario_comparison_service.py`。
2. 汇总多个 scenario 的 metrics。
3. 新增 `scenario_compare` 报告工具。
4. ReportAgent 新增 `comparison_report` 模式。
5. 新增 API：`POST /api/scenarios/compare`。

### 前端任务

1. 新增 `ScenarioComparePanel.vue`。
2. 支持选择多个情景。
3. 展示指标表、路径差异、推荐方案。
4. 支持生成对比报告。

### 测试

1. 两情景对比。
2. 三情景对比。
3. 缺少 metrics 时的提示。

### 验收标准

- 用户能看到 baseline 与干预情景的差异。
- 对比报告能给出推荐情景和原因。

## 阶段 6：外部信号追踪 MVP

周期：3 到 5 周

### 后端任务

1. 新增 `backend/app/services/signal_tracking_service.py`。
2. 复用 `web_search_provider` 做手动刷新。
3. 定义 `SignalObservation` schema。
4. 实现信号去重、摘要、相关变量匹配。
5. 将信号写入图谱 episode。
6. 在 ZepToolsService 增加 `signal_search` 和 `evidence_matrix`。

### 前端任务

1. Step3 或 GraphPanel 增加信号追踪面板。
2. 支持手动刷新。
3. 展示信号来源、摘要、影响等级、关联变量。
4. 信号命中关键变量时提示“建议再推演”。

### 测试

1. 手动刷新信号。
2. 重复信号去重。
3. 信号写入图谱后可被报告工具检索。

### 验收标准

- 项目可保存外部信号。
- 报告可引用信号。
- 信号变化能驱动再推演建议。

## 阶段 7：概率与校准雏形

周期：3 到 6 周

### 后端任务

1. 支持同一情景多次运行。
2. 基于多次 metrics 统计经验概率。
3. 新增 `probability_assess` 工具。
4. 支持 Step5 访谈/问卷结果回写为校准记录。

### 前端任务

1. 情景中心展示运行次数。
2. 对比报告展示经验概率。
3. Step5 增加“回写校准”按钮。

### 验收标准

- 可以展示“基于 N 次仿真的经验概率”。
- 报告明确区分经验概率与真实世界概率。

## 阶段 8：长期能力建设

周期：3 到 12 个月

### 任务

1. 建立历史案例库。
2. 建立回测流程。
3. 引入贝叶斯更新或其他概率信念模型。
4. 抽象 `SimulationEnvironment`，降低对 OASIS 社交环境的绑定。
5. 构建策略推荐器。

### 验收标准

- 至少 20 个历史案例可用于回测。
- 系统能记录预测、真实结果和误差。
- 可支持传播推演之外的一个实验性环境。

## 风险清单

| 风险 | 等级 | 应对 |
| --- | --- | --- |
| 多情景运行成本过高 | 高 | 默认短轮次，限制并发，展示 token 成本提示 |
| LLM 结构化输出失败 | 中 | JSON schema 校验，规则兜底 |
| 干预无法影响 OASIS 行为 | 高 | 先用初始/定时事件和配置参数实现可控影响 |
| 概率被用户误解 | 高 | 明确标注经验概率，不作为真实统计概率 |
| 外部信号噪声大 | 中 | 来源去重、置信度、人工确认 |
| 破坏原流程 | 中 | 默认单情景兼容模式，新增功能可关闭 |

## 首个 MVP 推荐组合

建议第一轮只做：

1. 决策问题结构化
2. 情景配置快照
3. 基础指标评估
4. 决策型报告模板

这四项不依赖外部信号、不需要复杂概率模型，能最快把产品从“推演展示”提升到“决策支持”。

