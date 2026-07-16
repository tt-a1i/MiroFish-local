# 第一步：图谱构建改造需求文档

## 一、审查结论

本文件已按原始需求与当前仓库现状重写，重点修正了以下问题：

1. **原方案把“搜索/建议/本体生成”混成一个接口，不符合 01 → 02 → 03 的业务顺序。**
2. **联网搜索必须走后端 API 集成博查 Web Search API，不使用开发期搜索工具作为产品能力。**
3. **原方案未覆盖当前真实前端入口：`Home.vue -> pendingUpload -> MainView.vue`。**
4. **原方案对人物/机构/群体的 schema 区分不够严格，缺少 `Group` 兜底类型。**
5. **补充了代码审查边界：当前“现实事件提取”并不是独立产物，而是由文件解析、本体生成和图谱 episode 抽取共同完成。**

---

## 二、当前实现与现状对齐

### 2.1 当前 Step1 实际流程

当前仓库中的 Step1 并不是“在工作台里输入后再分析”，而是：

```text
Home.vue
  ├── 上传文件
  ├── 输入 simulationRequirement
  └── setPendingUpload(...)
          ↓
MainView.vue / handleNewProject()
  └── POST /api/graph/ontology/generate
          ↓
graph.py
  ├── 校验 simulation_requirement 必填
  ├── files 必填
  ├── 提取文本
  ├── 生成 ontology
  └── 自动进入 /api/graph/build
```

路由层需要注意：

- `frontend/src/router/index.js` 中 `/process/:projectId` 的 `name` 是 `Process`，但实际加载组件是 `frontend/src/views/MainView.vue`。
- `frontend/src/views/Process.vue` 是遗留重复页面，仍保留相似旧流程代码，但当前路由不使用它。
- 本次改造主路径应以 `Home.vue -> pendingUpload -> MainView.vue` 为准；除非后续重新启用旧 `Process.vue`，否则不要把主要交互实现写到旧页面里。

### 2.2 当前实现的直接限制

1. **只能文件上传**
   - `backend/app/api/graph.py` 当前要求至少一个文件。

2. **模拟提示词必须先输入**
   - `simulation_requirement` 在当前接口中是前置必填。
   - 这与“先生成建议，再让用户选择/输入”的目标相反。

3. **`Step1GraphBuild.vue` 当前是展示组件**
   - 真正的输入与流程控制在 `MainView.vue` 和 `Home.vue`。

4. **本体分类缺少“群体”兜底**
   - `backend/app/services/ontology_generator.py` 当前强制最后 2 个兜底类型为 `Person` 和 `Organization`。
   - 这无法满足“实体节点、关系边、Schema 类型必须准确区分人物、机构、群体”的要求。

### 2.3 代码审查补充：现有文件链路不可破坏

当前文件上传链路有一组后续步骤强依赖，改造时必须保留：

```text
Home.vue
  → pendingUpload.files / pendingUpload.simulationRequirement
  → MainView.vue handleNewProject()
  → frontend/src/api/graph.js generateOntology(FormData)
  → backend/app/api/graph.py /ontology/generate
  → ProjectManager.create_project()
  → ProjectManager.save_file_to_project()
  → FileParser.extract_text()
  → TextProcessor.preprocess_text()
  → ProjectManager.save_extracted_text(project_id, all_text)
  → OntologyGenerator.generate(...)
  → project.ontology / project.analysis_summary / status=ontology_generated
  → /api/graph/build
  → TextProcessor.split_text()
  → GraphBuilderService.set_ontology()
  → GraphBuilderService.add_text_batches()
```

关键约束：

1. `extracted_text.txt` 是后续 `/api/graph/build` 和 Step2 `prepare` 的基础输入，文件模式新增 summary/suggestions 时不能替代或丢弃它。
2. 当前 `analysis_summary` 只是 ontology 生成结果的一部分，不等同于新增的 `seed_summary_md`。
3. 当前代码没有 `seed_sources`、`simulation_suggestions`、`entity_hints` 的持久化字段，这些都是本次新增能力。
4. 当前“现实事件提取”没有独立服务；它隐含在 LLM 本体分析和后续图谱抽取中。本次新增 `SeedAnalysisService` 时，应作为前置分析产物补充，而不是绕过原本建图链路。
5. Step1 中所谓“个体与群体记忆注入”在当前代码里主要表现为把分块文本作为 episode 写入 Zep/Graphiti 图谱；模拟运行中的动态图谱记忆更新属于后续阶段，不是当前 Step1 的独立接口。

---

## 三、改造目标

### 3.1 业务目标

Step1 改造后必须按以下子步骤运行：

```text
Step1 图谱构建
├── 01 现实事件输入
│   ├── 搜索词输入（联网查询模式）
│   └── 文件上传（本地材料模式）
│       说明：两种模式只能二选一，不能混合提交
├── 02 模拟提示词确认
│   ├── 选择系统建议
│   └── 自然语言自定义
├── 03 本体生成
├── 04 GraphRAG 图谱构建
└── 05 图谱记忆基础写入
    说明：当前主要通过文本分块写入 episode 形成图谱记忆基础；模拟运行阶段的动态记忆更新不在 Step1 内完成
```

### 3.2 需求映射

| 原始需求 | 改造落点 |
|---|---|
| 01 步骤支持输入框与拖拽上传 | Step1-01 使用 Tab/分段控件切换“联网搜索”和“文件上传”，同一时刻只能启用一种 |
| 输入“张雪机车事件”要全网查询并生成总结 MD | 新增关键词联网查询接口，后端通过博查 Web Search API 查询并生成 `seed_summary.md` |
| 文件上传仍走项目已有上传分析能力 | 复用当前 multipart 文件上传、文件解析、`extracted_text.txt`、本体生成前置链路，并补充 summary 与 suggestions 产物 |
| 生成 2-3 条模拟提示词建议 | 无论来自文件上传还是关键词搜索，都必须在 Step1-02 展示 suggestions |
| 支持选建议，也支持自然语言输入 | Step1-02 提供“建议卡片 + 文本框”双模式 |
| 可以用 agent/服务来做搜索总结 | 新增 `SeedAnalysisService`，服务化编排 |
| 后续仍要调用现实事件提取、GraphRAG 构建、记忆注入 | `文件上传分析或关键词搜索 -> ontology/generate -> build` |
| 要能区分联网查询还是拖拽上传 | `seed_input_mode` 只允许记录为 `web_search` 或 `file_upload` |
| 人物/机构/群体区分必须准确 | 改造 `OntologyGenerator` 的 schema 与校验规则 |

---

## 四、推荐技术方案

## 4.1 前端交互重构

### 4.1.1 入口调整

推荐将 `frontend/src/views/Home.vue` 从“收集文件和提示词的表单页”改为“进入引擎入口页”：

```text
Home.vue
  └── 点击“启动引擎”后进入 /process/new
          ↓
MainView.vue
  └── 在 Step1 内完成输入模式选择 / 事件分析 / 建议选择 / 本体生成
```

兼容策略：

- `pendingUpload.js` 可在迁移期保留。
- 但产品主路径应改为“Step1 内部采集输入”，不再依赖首页预先收集。

### 4.1.2 Step1 UI 结构

`frontend/src/components/Step1GraphBuild.vue` 改为交互组件，建议结构如下：

```text
01 / 现实事件输入
  - Tab 或分段控件：联网搜索 / 文件上传
  - 联网搜索 Tab：搜索关键词输入框
  - 文件上传 Tab：文件拖拽上传区
  - Tab 切换时带轻量过渡动画，并清空另一种模式的临时输入
  - “分析现实事件”按钮

02 / 模拟提示词
  - AI 推荐 2-3 条建议
  - 选中建议后自动填入文本框
  - 仍允许用户手动改写

03 / 本体生成
  - “确认提示词并生成本体”按钮
```

### 4.1.3 `MainView.vue` 新增状态

建议增加以下状态而不是继续复用旧的 `pendingUpload` 结构：

```javascript
const seedForm = reactive({
  inputMode: 'web_search', // web_search | file_upload
  searchQuery: '',
  files: [],
  projectName: '',
  additionalContext: ''
})

const seedResult = ref(null)
const selectedSuggestion = ref('')
const customSimulationRequirement = ref('')
const seedAnalyzing = ref(false)
const ontologyGenerating = ref(false)
```

### 4.1.4 前端交互规则

1. `inputMode=web_search` 时只能提交 `search_query`，不能携带 `files`。
2. `inputMode=file_upload` 时只能提交 `files`，不能携带 `search_query`。
3. 用户从一个 Tab 切到另一个 Tab 时，前端必须清空非当前模式的数据并给出轻量提示。
4. Step1-02 未确认模拟提示词前，不允许进入本体生成。
5. 页面刷新后可通过 `GET /api/graph/project/<project_id>` 恢复 summary、sources、suggestions。

---

## 4.2 后端两段式接口设计

### 4.2.1 文件上传模式：复用现有上传分析接口

文件上传模式优先复用当前已有的 multipart 上传、文件解析与现实事件提取链路，降低改造风险。当前入口是：

```text
POST /api/graph/ontology/generate
```

该接口需要调整为“两阶段能力”：

1. 文件上传分析阶段：保存文件、提取文本、生成 `seed_summary_md`、提取实体线索、生成 2-3 条模拟提示词建议。
2. 本体生成阶段：用户在 Step1-02 选择建议或手动输入模拟提示词后，再基于已保存的项目文本生成 ontology。

实现上可以通过 `Content-Type` 或参数区分：

- `multipart/form-data + files`：文件上传分析。
- `application/json + project_id + simulation_requirement`：确认模拟提示词并生成本体。

文件上传分析阶段**不应再强制要求** `simulation_requirement` 前置必填。

请求方式：`multipart/form-data`

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `files` | File[] | 是 | 上传文档 |
| `project_name` | string | 否 | 项目名称 |
| `additional_context` | string | 否 | 额外上下文，用于文件摘要和建议生成 |

校验规则：

- 必须至少上传 1 个有效文件。
- 如果请求中同时出现 `search_query`，直接返回 400，提示用户只能选择一种输入模式。

#### 返回示例

```json
{
  "success": true,
  "data": {
    "project_id": "proj_xxxx",
    "seed_input_mode": "file_upload",
    "seed_summary_md": "# 上传材料总结\n\n## 事件概述\n...",
    "simulation_suggestions": [
      "模拟张雪及相关机构在社交媒体上的舆论博弈演化路径",
      "预测官方回应节奏变化对舆情热度的影响",
      "模拟不同群体对事件关键节点的立场分化与扩散"
    ],
    "seed_sources": [
      {
        "title": "......",
        "url": "https://...",
        "publisher": "......",
        "published_at": "2026-05-10",
        "reliability_level": "high"
      }
    ],
    "seed_sources": [],
    "uploaded_files": [
      {"filename": "seed.md", "size": 10240}
    ],
    "total_text_length": 15832,
    "entity_hints": ["张雪", "第一财经", "浙江省公安厅"]
  }
}
```

### 4.2.2 关键词联网搜索模式：新增独立接口

关键词模式必须新增独立后端接口与独立服务文件，不复用文件上传接口，便于后续扩展更多联网数据源。

推荐接口：

```text
POST /api/graph/seed/web-search
```

请求方式：`application/json`

```json
{
  "search_query": "张雪机车事件",
  "project_name": "张雪机车事件舆情模拟",
  "additional_context": "关注事件时间线、涉事人物、机构回应和媒体报道"
}
```

校验规则：

- `search_query` 必填。
- 不允许携带 `files`。
- 后端必须通过博查 Web Search API 查询，不使用浏览器、开发代理工具或本地临时搜索能力。
- 搜索结果不足以支撑事实总结时，接口应失败并提示用户缩小关键词或补充文件材料。

返回结构与文件上传分析阶段保持一致，但 `seed_input_mode` 为 `web_search`，并返回 `seed_sources`。

### 4.2.3 第二阶段接口：`POST /api/graph/ontology/generate`

该接口改为**基于已保存项目文本**生成本体，而不是重新收文件。

请求方式：`application/json`

```json
{
  "project_id": "proj_xxxx",
  "simulation_requirement": "模拟张雪及相关机构在社交媒体上的舆论博弈演化路径",
  "additional_context": "关注媒体机构与官方机构的互动"
}
```

处理逻辑：

1. 根据 `project_id` 读取 `extracted_text.txt`
2. 保存最终的 `simulation_requirement`
3. 调用 `OntologyGenerator.generate(...)`
4. 返回 ontology 与 analysis summary
5. 之后才允许调用 `/api/graph/build`

### 4.2.4 兼容入口

为兼容当前前端，可保留旧版 multipart `ontology/generate` 文件上传调用若干版本，但内部应转为：

```text
legacy multipart request
  → create project
  → file upload analysis internal flow
  → return seed_summary_md + suggestions
  → wait for user confirmed simulation_requirement
  → ontology/generate internal flow
```

文档、前端新流程、测试用例均应以“输入分析 → Step1-02 确认模拟提示词 → 本体生成”为准。

---

## 4.3 事件分析服务设计

### 4.3.1 服务拆分

推荐新增两个服务：

1. `bocha_search_service.py`
   - 封装博查 Web Search API 调用
   - 统一输出标准化搜索结果
   - 控制超时、重试、去重、可靠性打分
   - 保留清晰的服务边界，后续增加其他数据源时在上层编排，不把前端接口改回混合输入

2. `seed_analysis_service.py`
   - 编排关键词搜索结果或文件解析结果
   - 生成 Markdown 总结
   - 生成 2-3 条模拟提示词建议
   - 保存项目级产物

### 4.3.2 数据结构

```python
@dataclass
class SearchSource:
    title: str
    url: str
    publisher: str
    published_at: Optional[str]
    snippet: str
    reliability_level: str


@dataclass
class SeedAnalysisResult:
    project_id: str
    seed_input_mode: str
    merged_text: str
    seed_summary_md: str
    simulation_suggestions: List[str]
    seed_sources: List[SearchSource]
    entity_hints: List[str]
    topic_tags: List[str]
```

### 4.3.3 输入模式校验

```python
if has_query and has_files:
    raise InvalidSeedInputMode("关键词搜索与文件上传只能二选一")
if has_query:
    input_mode = "web_search"
elif has_files:
    input_mode = "file_upload"
else:
    raise InvalidSeedInputMode("请提供关键词或上传文件")
```

### 4.3.4 关键词联网查询方案

当前阶段固定使用博查 `Web Search API`：

- 文档入口：用户提供的博查 Web Search API 文档与博查开放平台官网
- 服务文件：`backend/app/services/bocha_search_service.py`
- 配置入口：根目录 `.env`，示例同步到 `.env.example` 与 `.env.local.example`
- 调用位置：仅后端持有 API Key，前端只能调用本项目后端接口
- 官方调用形态：`POST https://api.bochaai.com/v1/web-search`
- 鉴权方式：`Authorization: Bearer <BOCHA_API_KEY>`

建议配置：

```env
BOCHA_API_KEY=your_bocha_api_key_here
BOCHA_BASE_URL=https://api.bochaai.com/v1
BOCHA_WEB_SEARCH_ENDPOINT=/web-search
BOCHA_WEB_SEARCH_TIMEOUT=30
BOCHA_WEB_SEARCH_MAX_RESULTS=8
```

请求字段建议：

```json
{
  "query": "张雪机车事件",
  "freshness": "oneYear",
  "summary": true,
  "count": 8
}
```

响应解析要求：

- 从 `webPages.value[]` 读取搜索结果。
- 将 `name` 映射为 `SearchSource.title`。
- 将 `url` 映射为 `SearchSource.url`。
- 将 `siteName` 映射为 `SearchSource.publisher`。
- 将 `datePublished` 映射为 `SearchSource.published_at`。
- 优先使用 `summary`，无 summary 时使用 `snippet`。

后续如需引入其他新闻源，应新增 provider/service 并复用标准化 `SearchSource` 输出，不改变 Step1-01 的二选一交互契约。

### 4.3.5 搜索策略

建议最大只做 3 轮，不做无边界深搜：

```text
第一轮：主查询
  - {query}
  - {query} 最新进展

第二轮：事件上下文
  - {query} 各方回应
  - {query} 时间线

第三轮：实体定向（最多 2 个）
  - {entity_1} {query}
  - {entity_2} {query}
```

约束：

- 返回高置信结果 8-12 条即可
- 去掉短链、跳转链、内容农场
- 优先官方站点、权威媒体、公开数据库

### 4.3.6 Markdown 总结格式

```markdown
# {主题名称}

## 事件概述

## 时间线

## 关键实体
### 人物
### 机构
### 群体

## 主要争议点

## 已公开回应

## 可用于模拟的观察视角

## 信息来源
- [来源标题](URL)
```

要求：

- 总结中只能写来源中出现过的事实。
- 每个重要事实后建议保留来源编号或引用痕迹，便于后续 Step2 消歧。

### 4.3.7 建议生成规则

无论输入来自搜索还是文件上传，都应生成 2-3 条建议，避免用户在 Step1-02 没有可选项。

建议要求：

1. 是完整的模拟/预测需求句子。
2. 不要过泛，必须包含事件主体或互动方向。
3. 不得使用“随便分析一下”之类无约束表述。

### 4.3.8 实体与节点真实性约束

用户对“节点信息”和“Agent 人设”都要求真实，Step1 不能只把真实性留到 Step2 才处理：

1. 文件模式生成图谱时，节点与关系事实只能来自上传材料或已落盘的联网补全材料，不得为了填满属性让 LLM 编造事实。
2. 关键词模式生成图谱时，事实来源必须来自博查搜索结果总结后的 `seed_summary_md` 与来源记录。
3. `entity_hints` 应明确标记需要后续验证的人物、机构、媒体组织和群体主体，例如 `张雪`、`西湖大学`、`西湖公安局`、新闻网或媒体机构。
4. 若某实体在输入材料中只有名称、缺少身份/职能/地区/事件关联等信息，进入 Agent 人设生成前必须由 `RealEntityResolver -> BochaSearchService` 补全并保留来源。
5. 若实施阶段决定在建图前补全节点上下文，则补全结果必须作为有来源的 Markdown/文本材料落盘并进入 `extracted_text.txt` 或等价建图输入，不能只在内存里给图谱写无来源属性。
6. 对补全后仍无可靠来源的字段，节点属性、人设事实和前端展示都应为空、未知或跳过，不得伪造。

---

## 4.4 本体生成改造

### 4.4.1 关键纠偏

当前 `OntologyGenerator` 的“最后 2 个兜底类型必须是 `Person` 和 `Organization`”需要改为：

```text
实体类型不做固定数量限制，不再压缩到 10 个以内。
必须完整覆盖事件中的可发声主体类型，包含但不限于：
- 政府/监管
- 单位、机构
- 企业/品牌
- 媒体、媒体平台
- 组织/协会
- 意见领袖/网红
- 社区、公众
- 主配角、网民/个人

其中仍需包含 2 个兜底类型：
- Person：自然人兜底
- Organization：组织机构兜底

纯地点、地址、省市区、小区、商场等只作为事件背景或属性，不作为实体类型，也不能进入后续 Agent 人设生成。
```

### 4.4.2 schema 扩展

每个 entity type 建议增加 `category` 字段：

```json
{
  "name": "MediaOutlet",
  "category": "organization",
  "description": "News media organization...",
  "attributes": [],
  "examples": ["第一财经", "财新网"]
}
```

可选值固定为：

- `person`
- `organization`
- `group`

### 4.4.3 约束规则

本体生成必须满足：

1. 不允许把抽象概念当实体类型
2. 不允许把“支持方/反对方/舆论”这类立场概念当实体类型
3. 必须至少覆盖 1 个人物类、1 个机构类、1 个群体类
4. `examples` 必须是现实世界主体，而不是概念词

### 4.4.4 关系类型要求

关系类型也必须体现主体差异，至少能表达以下方向：

- 人物 ↔ 人物
- 人物 ↔ 机构
- 人物 ↔ 群体
- 机构 ↔ 机构
- 机构 ↔ 群体
- 群体 ↔ 群体

示例关系：

- `WORKS_FOR`
- `REPRESENTS`
- `RESPONDS_TO`
- `REGULATES`
- `SUPPORTS`
- `OPPOSES`
- `MOBILIZES`
- `BELONGS_TO`

---

## 4.5 项目数据持久化

### 4.5.1 `Project` 模型新增字段

```python
seed_input_mode: Optional[str]
search_query: Optional[str]
seed_summary_md: Optional[str]
seed_sources: List[Dict[str, Any]]
simulation_suggestions: List[str]
entity_hints: List[str]
seed_metadata: Dict[str, Any]
```

### 4.5.2 `ProjectManager` 建议新增方法

```python
save_seed_summary(project_id: str, markdown_text: str) -> None
get_seed_summary(project_id: str) -> Optional[str]
save_seed_sources(project_id: str, sources: List[dict]) -> None
get_seed_sources(project_id: str) -> List[dict]
```

### 4.5.3 项目目录建议

```text
uploads/projects/<project_id>/
├── project.json
├── files/
├── extracted_text.txt
├── seed_summary.md
└── seed_sources.json
```

---

## 五、与现有代码的对应修改点

| 文件 | 必改内容 |
|---|---|
| `frontend/src/views/Home.vue` | 调整为进入引擎入口，移除“文件+提示词同时必填”限制 |
| `frontend/src/store/pendingUpload.js` | 降级为兼容层或删除 |
| `frontend/src/views/MainView.vue` | 增加现实事件分析与 ontology generate 两段状态流 |
| `frontend/src/components/Step1GraphBuild.vue` | 改为交互组件 |
| `frontend/src/api/graph.js` | 新增 `searchSeedByKeyword()`，重写 `generateOntology()` 参数 |
| `backend/app/api/graph.py` | 新增关键词搜索接口，调整 `ontology/generate` 支持文件分析与本体生成两阶段 |
| `backend/app/models/project.py` | 增加 seed 字段 |
| `backend/app/services/seed_analysis_service.py` | 新增 |
| `backend/app/services/bocha_search_service.py` | 新增，封装博查 Web Search API |
| `backend/app/services/ontology_generator.py` | 增加 `Group` 兜底与 `category` 校验 |

---

## 六、安全与异常处理

1. **搜索结果注入**
   - 网页内容进入 LLM 前必须去 HTML、脚本、隐藏指令。

2. **Markdown 展示安全**
   - 前端不能直接把未清洗 Markdown 转为 `v-html`。
   - 必须使用 `markdown-it + DOMPurify` 或等价方案。

3. **无搜索结果**
   - 如果是 `web_search` 模式且没有有效来源，接口返回失败，不生成伪总结。
   - 文件模式只基于上传材料生成初始 summary；若后续实体真实性信息不足，在真实性补全阶段再走联网查询。

4. **文件解析失败**
   - 上传成功但文本提取为空时，直接报错，不进入后续流程。

5. **歧义事件**
   - 对搜索词过于宽泛的情况，summary 中要明确提示“需要进一步缩小范围”。

6. **实现防偏**
   - 文件模式必须继续保存原始上传文件与 `extracted_text.txt`。
   - 文件模式新增的 `seed_summary_md` 只能作为 Step1-02 和 Step2 消歧辅助上下文，不能取代原始全文进入 GraphRAG。
   - `generateOntology()` 改造后需同时支持 `multipart/form-data` 文件分析和 `application/json` 本体生成，前端调用必须按阶段区分。
   - `Process.vue` 当前不在路由主链路上，除非确认重新启用，否则不要把新 Step1 主交互只写到该文件。

---

## 七、验收标准

满足以下条件，Step1 改造才算完成：

1. 搜索词模式可独立完成：输入“张雪机车事件”可生成 `seed_summary_md` 和 2-3 条建议。
2. 文件模式可独立完成：上传 PDF/MD/TXT 后也可生成摘要与建议。
3. 前后端均拒绝混合输入：同一次现实事件分析请求不得同时包含 `search_query` 与 `files`。
4. Step1-01 的关键词输入区与文件拖拽上传区可通过 Tab/分段控件切换，切换过程有过渡动画。
5. Step1-02 允许“选建议 + 改写”，也允许完全手动输入。
6. 本体生成必须在用户确认模拟提示词后才发生。
7. ontology 中必须显式区分 `person / organization / group`。
8. 后续 `/api/graph/build` 与现有 GraphRAG 构建链路保持兼容。
