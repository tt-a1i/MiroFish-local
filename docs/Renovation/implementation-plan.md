# MiroFish 改造实施计划

## 一、实施基线

本计划以修订后的 3 条主线为准：

1. **Step1 拆成两段式接口**
   - 文件模式：复用现有上传/分析接口
   - 关键词模式：新增博查联网搜索接口
   - `ontology/generate`

2. **本体 schema 增加 `group` 维度**
   - 解决人物 / 机构 / 群体分类不充分的问题

3. **Step2 引入真实性验证闸门**
   - 默认严格模式
   - 不允许虚构降级

---

## 二、阶段划分

## 阶段 A：Step1 后端基础重构

| ID | 任务 | 文件 | 预估工时 |
|---|---|---|---|
| A1 | 设计并实现 `BochaSearchService`，封装博查 Web Search API 调用、超时、去重与结果标准化 | `backend/app/services/bocha_search_service.py` | 4h |
| A2 | 实现 `SeedAnalysisService`，负责编排博查搜索结果或文件解析结果，生成总结与建议 | `backend/app/services/seed_analysis_service.py` | 5h |
| A3 | `Project` 模型增加 seed 相关字段 | `backend/app/models/project.py` | 1.5h |
| A4 | 新增 `POST /api/graph/seed/web-search`，仅处理关键词联网搜索模式 | `backend/app/api/graph.py` | 3h |
| A5 | 调整 `POST /api/graph/ontology/generate`：multipart 文件模式先返回 summary + suggestions；JSON 模式基于 `project_id` 生成 ontology | `backend/app/api/graph.py` | 4h |
| A6 | 增加互斥输入校验，同时收到 `search_query` 与 `files` 时返回 400 | `backend/app/api/graph.py` | 1.5h |
| A7 | Step1 后端单元测试 | `backend/tests/test_seed_analysis_service.py`、`backend/tests/test_bocha_search_service.py` 等 | 3.5h |
| A8 | 同步博查配置读取与环境模板 | `backend/app/config.py`、`.env.example`、`.env.local.example` | 1h |
| A9 | 回归保护现有文件链路：上传文件、保存 `extracted_text.txt`、生成 ontology、进入 graph build | `backend/app/api/graph.py`、`backend/app/models/project.py` | 1.5h |
| A10 | 生成 `entity_hints` 并标记需要真实性补全的候选主体 | `backend/app/services/seed_analysis_service.py` | 2h |

## 阶段 B：Step1 前端流程迁移

| ID | 任务 | 文件 | 预估工时 |
|---|---|---|---|
| B1 | `Home.vue` 改为进入引擎入口，移除“文件+提示词同时必填”限制 | `frontend/src/views/Home.vue` | 2h |
| B2 | `MainView.vue` 增加 Step1 两段式状态管理 | `frontend/src/views/MainView.vue` | 4h |
| B3 | `Step1GraphBuild.vue` 从展示组件改为交互组件，使用 Tab/分段控件切换关键词搜索与文件上传，并加入过渡动画 | `frontend/src/components/Step1GraphBuild.vue` | 5h |
| B4 | `frontend/src/api/graph.js` 新增 `searchSeedByKeyword()` 并调整 `generateOntology()` | `frontend/src/api/graph.js` | 1h |
| B5 | `pendingUpload.js` 降级为兼容层或移除 | `frontend/src/store/pendingUpload.js` | 1h |
| B6 | Markdown 渲染与 HTML 清洗接入 | Step1 组件相关 | 2h |
| B7 | Step1 手工回归与交互修正 | 前端整体 | 2h |
| B8 | 确认旧 `frontend/src/views/Process.vue` 不在主路由；若不启用，不在该文件实现主交互 | `frontend/src/router/index.js`、`frontend/src/views/Process.vue` | 0.5h |

## 阶段 C：本体分类增强

| ID | 任务 | 文件 | 预估工时 |
|---|---|---|---|
| C1 | `OntologyGenerator` schema 增加 `category` 字段 | `backend/app/services/ontology_generator.py` | 2h |
| C2 | 将兜底类型从 `Person + Organization` 改为 `Person + Organization + Group` | 同上 | 2h |
| C3 | 增加人物/机构/群体校验逻辑与自动补全策略 | 同上 | 2h |
| C4 | 增加本体生成测试样例 | `backend/tests/test_ontology_generator.py` | 2h |

## 阶段 D：Step2 真实性验证链路

| ID | 任务 | 文件 | 预估工时 |
|---|---|---|---|
| D1 | 新增 `RealEntityResolver` | `backend/app/services/real_entity_resolver.py` | 6h |
| D2 | 在 `SimulationManager.prepare_simulation()` 中加入验证闸门 | `backend/app/services/simulation_manager.py` | 4h |
| D3 | `OasisProfileGenerator` 接入真实资料与 provenance | `backend/app/services/oasis_profile_generator.py` | 6h |
| D4 | 删除正式链路中的虚构降级方案 | 同上 | 2h |
| D5 | 扩展 `/api/simulation/prepare` 与 `/prepare/status` 返回结构 | `backend/app/api/simulation.py` | 3h |
| D6 | 扩展 profiles / realtime profiles API 输出 | `backend/app/api/simulation.py` | 2h |
| D7 | 处理 Graphiti fallback 默认 `Entity` 节点，抽象节点必须跳过 | `backend/app/services/real_entity_resolver.py`、`backend/app/services/zep_entity_reader.py` | 2h |
| D8 | Step2 后端测试（verified / ambiguous / unverified / unsupported / zero verified） | `backend/tests/test_real_entity_resolver.py` 等 | 4h |

## 阶段 E：Step2 前端展示与对话约束

| ID | 任务 | 文件 | 预估工时 |
|---|---|---|---|
| E1 | Step2 卡片增加真实性状态与来源数量 | `frontend/src/components/Step2EnvSetup.vue` | 2h |
| E2 | Step2 详情弹窗拆分“真实资料 / 仿真参数” | 同上 | 3h |
| E3 | 增加 skipped entities 面板 | 同上 | 2h |
| E4 | `frontend/src/api/simulation.js` 适配增强响应 | `frontend/src/api/simulation.js` | 1h |
| E5 | Interview 文案与 persona 使用规则联调 | `frontend/src/components/Step5Interaction.vue` 与后端现有接口 | 2h |

## 阶段 F：集成、回归与安全

| ID | 任务 | 预估工时 |
|---|---|---|
| F1 | 搜索词模式端到端测试 | 2h |
| F2 | 文件模式端到端测试 | 1.5h |
| F3 | 互斥输入端到端测试 | 1.5h |
| F4 | 真实性验证端到端测试 | 2h |
| F5 | Step5 对话真实性回归 | 1.5h |
| F6 | XSS / Markdown 清洗验证 | 1h |
| F7 | 博查接口限流与超时测试 | 1h |
| F8 | 文档与 README 同步 | 1.5h |

---

## 三、推荐开发顺序

```text
阶段 A Step1 后端
    ↓
阶段 B Step1 前端
    ↓
阶段 C 本体分类增强
    ↓
阶段 D Step2 真实性验证
    ↓
阶段 E Step2 前端展示
    ↓
阶段 F 集成回归
```

并行建议：

1. 阶段 A 与阶段 B 可以部分并行，但前端以接口契约冻结后再展开。
2. 阶段 C 可以在阶段 A 后期并行推进。
3. 阶段 D 必须在 Step1 `seed_summary_md` 持久化完成后才能稳定实施。

---

## 四、关键技术决策

## 4.1 Step1 为什么要拆成两个接口

原因：

1. 原始需求明确要求先生成建议，再让用户确认模拟提示词。
2. 当前 `simulation_requirement` 在接口 1 中是必填，这与目标顺序冲突。
3. 两段式接口更容易做页面恢复、错误重试、以及用户编辑。

结论：

- 新主流程必须是 `文件上传分析或关键词搜索 -> ontology/generate -> build`
- 文件模式复用现有上传接口，但不再要求用户在上传前先填模拟提示词
- 关键词模式新增独立接口，并固定使用博查 Web Search API
- 旧的“一次上传立刻出 ontology”只保留兼容入口

## 4.2 Step2 为什么不能保留虚构降级

原因：

1. 用户明确三次强调“必须真实”。
2. 虚构降级会污染后续 Step5 对话质量，并误导用户把假信息当真。
3. 与其生成错误人设，不如明确跳过或阻断。

结论：

- 正式模式下不允许“搜索失败则根据图谱生成伪真实 profile”
- 严格模式默认开启

## 4.3 为什么要区分真实事实与仿真参数

原因：

1. OASIS 运行需要一些控制字段，但这些字段不一定能从现实世界公开验证。
2. 如果继续把 `MBTI`、默认年龄等展示为真实事实，会直接违背需求。

结论：

- 前端展示要分栏
- 导出结构可保留运行字段，但语义上必须标清是“仿真参数”

---

## 五、详细任务说明

## 5.1 Step1 后端任务说明

### A1-A2：搜索与事件分析

交付要求：

- 支持 `web_search / file_upload` 两种互斥模式
- 保存 `seed_summary.md`
- 保存 `seed_sources.json`
- 保存 `entity_hints`，标记人物、机构、媒体组织、群体等候选主体
- 生成 2-3 条建议
- 失败时返回明确原因，不生成伪 summary
- 文件模式必须继续保存原始上传文件和 `extracted_text.txt`
- `seed_summary.md` 不能替代原文进入 `/api/graph/build`
- 节点事实不得由 LLM 编造；若建图前做实体补全，补全材料必须带来源并落盘后再进入建图输入

### A4-A5：接口重构

交付要求：

- 关键词搜索接口不要求 `simulation_requirement`
- 文件上传分析阶段不要求 `simulation_requirement`
- `ontology/generate` 改为读取 `project_id` 的已存文本
- `project/<id>` 返回 `seed_summary_md` 和 `simulation_suggestions`

## 5.2 Step1 前端任务说明

### B1-B3：流程迁移

交付要求：

- `Home.vue` 不再卡死在文件必填
- Step1 支持通过 Tab/分段控件在搜索词输入和拖拽文件上传之间切换
- 切换时清空另一种模式的临时输入，前端不得提交混合参数
- 能在同一页面完成“看 summary -> 选建议 -> 生成 ontology”

### B6：Markdown 安全

交付要求：

- 不直接使用未清洗 HTML
- 所有外链使用安全属性

## 5.3 Step2 后端任务说明

### D1：真实性验证服务

交付要求：

- 对重名实体返回 `ambiguous`
- 对无来源实体返回 `unverified`
- 对抽象概念、话题、情绪、Graphiti fallback 的不可验证默认 `Entity` 节点返回 `unsupported`
- 返回 citation 列表

### D2-D4：人设生成主链路

交付要求：

- 只对 `verified` 实体生成 profile
- `bio/persona` 不含编造事实
- 零 verified 时任务失败
- OASIS 必需字段可以保留为运行参数，但不能作为真实资料展示

### D5-D6：API 扩展

交付要求：

- status 接口能看到 verified/skipped 计数
- profiles 与 profiles realtime 接口都能看到 provenance

## 5.4 Step2 前端任务说明

### E1-E3：真实性可视化

交付要求：

- 用户一眼能看出哪些 Agent 资料经过验证
- 用户能看到被跳过的实体及原因
- 真实资料与仿真参数分开展示
- `age/gender/mbti/country` 未验证时不能显示为真实资料

---

## 六、测试计划

## 6.1 单元测试

| 测试对象 | 测试重点 |
|---|---|
| `BochaSearchService` | 博查接口调用、超时、去重、来源标准化 |
| `SeedAnalysisService` | 三种输入模式、summary 生成、建议数量 |
| `OntologyGenerator` | `Group` 兜底、`category` 校验、抽象概念拦截 |
| `RealEntityResolver` | verified / ambiguous / unverified / unsupported |
| `OasisProfileGenerator` | 真实资料写入、禁止虚构降级、provenance 导出 |

## 6.2 集成测试

1. **搜索词模式**
   - 输入“张雪机车事件”
   - 获得 summary 和 suggestions
   - 选择建议生成 ontology
   - 构建图谱成功

2. **文件模式**
   - 上传 1 份 MD / PDF
   - 获得 summary 和 suggestions
   - 项目目录保留原始文件与 `extracted_text.txt`
   - 生成 ontology 成功
   - `/api/graph/build` 可继续基于原文分块建图

3. **互斥输入模式**
   - 关键词 Tab 激活时不得提交文件
   - 文件 Tab 激活时不得提交 `search_query`
   - 后端同时收到 `search_query` 与 `files` 时返回 400

4. **真实性验证模式**
   - 真实人物与真实机构生成 profile
   - 抽象概念与模糊实体被跳过
   - Graphiti fallback 返回的默认 `Entity` 节点不会绕过真实性闸门

5. **零已验证实体**
   - `prepare` 应明确失败

6. **Step5 对话**
   - 对真实 Agent 提问，回答不应出现 dossier 之外的硬事实臆造

## 6.3 安全测试

1. 搜索结果含 HTML / script 片段时，前端不执行
2. Markdown 渲染不出现 XSS
3. 来源链接带安全属性
4. 博查接口限流时后端不会无限重试

---

## 七、里程碑与验收

## 里程碑 M1：Step1 可运行

完成条件：

- 新关键词搜索接口可用
- Step1 页面可完成 summary + suggestions + ontology
- 关键词搜索接口调用博查 Web Search API
- 文件模式复用上传分析接口并返回 summary + suggestions
- 混合输入被前后端同时拒绝

## 里程碑 M2：本体分类达标

完成条件：

- ontology 可明确区分人物 / 机构 / 群体
- `Group` 兜底已加入

## 里程碑 M3：Step2 真实性链路达标

完成条件：

- 只能对 verified 实体生成 profile
- 来源可追溯
- 零 verified 时失败

## 里程碑 M4：端到端闭环

完成条件：

- Step1 搜索或上传进入 Step2
- Step2 真实人设进入 Step5 对话
- 无伪真实资料暴露给用户

---

## 八、工时估算

| 阶段 | 预估工时 |
|---|---|
| 阶段 A | 27h |
| 阶段 B | 17.5h |
| 阶段 C | 8h |
| 阶段 D | 29h |
| 阶段 E | 10h |
| 阶段 F | 10.5h |
| **总计** | **102h** |

建议排期：

- 单人开发：约 10-12 个工作日
- 前后端并行：约 7-8 个工作日

---

## 九、交付物清单

### 代码交付物

- `backend/app/services/bocha_search_service.py`
- `backend/app/services/seed_analysis_service.py`
- `backend/app/services/real_entity_resolver.py`
- `backend/app/api/graph.py`
- `backend/app/api/simulation.py`
- `backend/app/config.py`
- `backend/app/models/project.py`
- `backend/app/services/ontology_generator.py`
- `backend/app/services/oasis_profile_generator.py`
- `backend/app/services/simulation_manager.py`
- `frontend/src/views/Home.vue`
- `frontend/src/views/MainView.vue`
- `frontend/src/components/Step1GraphBuild.vue`
- `frontend/src/components/Step2EnvSetup.vue`
- `frontend/src/api/graph.js`
- `frontend/src/api/simulation.js`
- `.env.example`
- `.env.local.example`

### 测试交付物

- `backend/tests/test_seed_analysis_service.py`
- `backend/tests/test_bocha_search_service.py`
- `backend/tests/test_ontology_generator.py`
- `backend/tests/test_real_entity_resolver.py`
- `backend/tests/test_profile_generator_real_mode.py`
- Step1 / Step2 / Step5 手工回归记录

### 文档交付物

- `docs/overall-architecture-changes.md`
- `docs/step1-graph-build-renovation.md`
- `docs/step2-persona-agent-renovation.md`
- `docs/implementation-plan.md`
