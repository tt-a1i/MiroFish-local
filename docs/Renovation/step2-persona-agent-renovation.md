# 第二步：人设 Agent 生成改造需求文档

## 一、审查结论

本文件已按“必须真实”这一硬约束重写，重点修正以下问题：

1. **删除“搜索失败则回退到图谱摘要虚构人设”的错误方案。**
2. **明确把“真实事实”和“仿真参数”拆开，避免把 MBTI、随机年龄等伪装成真实资料。**
3. **补上“真实性验证闸门”设计，防止所有实体不经验证直接生成 Agent。**
4. **补上 `profiles API / realtime profiles / Step2 UI` 的来源透出设计，确保前端能看到真实来源。**

---

## 二、当前实现与问题定位

### 2.1 当前 Step2 实际链路

```text
POST /api/simulation/prepare
        ↓
SimulationManager.prepare_simulation(...)
        ↓
ZepEntityReader.filter_defined_entities(...)
        ↓
OasisProfileGenerator.generate_profiles_from_entities(...)
        ↓
generate_profile_from_entity(...)
        ↓
_generate_profile_with_llm() / _generate_profile_rule_based()
        ↓
保存 reddit_profiles.json / twitter_profiles.csv
```

### 2.2 当前实现中的关键问题

1. **当前允许虚构**
   - `OasisProfileGenerator` 在 `use_llm=False` 或 LLM 失败时，会生成规则化、随机化的人设。
   - 这与“人设必须是真实的人物或机构”直接冲突。

2. **当前默认会补随机字段**
   - 年龄、性别、MBTI、国家、职业等字段经常来自随机值或推断值。

3. **当前没有真实性验证闸门**
   - 图谱里只要是实体，就会尝试生成 Agent。
   - 抽象实体、模糊实体、重名实体、不可验证群体都可能被错误生成为人设。

4. **当前导出文件会丢失来源信息**
   - `reddit_profiles.json` 和 `twitter_profiles.csv` 目前主要面向 OASIS 运行，未保留足够的 provenance。

5. **当前 Step2 UI 把部分仿真字段当成“人设事实”展示**
   - `MBTI`、`年龄`、`国家/地区` 等若非真实可验证，就不能继续按“事实”展示给用户。

### 2.3 代码审查补充：当前真实填充链路

当前 Step2 的实际数据流如下：

```text
Step2EnvSetup.vue
  → prepareSimulation({ simulation_id, use_llm_for_profiles: true, parallel_profile_count: 5 })
  → POST /api/simulation/prepare
  → SimulationManager.prepare_simulation(...)
  → ZepEntityReader.filter_defined_entities(...)
  → OasisProfileGenerator.generate_profiles_from_entities(...)
  → reddit_profiles.json / twitter_profiles.csv
  → SimulationConfigGenerator.generate_config(...)
  → simulation_config.json
```

需要特别注意的实现细节：

1. `ZepEntityReader.filter_defined_entities()` 会优先保留带业务 label 的节点；但在 Graphiti 后端未产生业务 label 时，会 fallback 返回所有节点并统一标记为 `Entity`。
2. 因为存在 fallback，真实性验证闸门必须放在实体读取之后、profile 生成之前，不能只依赖 label 判断实体是否可生成 Agent。
3. `OasisProfileGenerator._generate_profile_with_llm()` 失败后会调用 `_generate_profile_rule_based()`，单个实体异常时也会创建 fallback profile。
4. `_generate_profile_rule_based()` 会随机填充年龄、性别、MBTI、国家等字段；保存 Reddit JSON 时还会用 `age=30`、`mbti=ISTJ`、`country=中国` 等默认值补齐 OASIS 必需字段。
5. 前端 `Step2EnvSetup.vue` 当前会把 `age/gender/country/mbti` 直接显示在人设详情里，因此必须在 UI 上拆分“真实资料”和“仿真运行参数”。
6. `/profiles/realtime` 当前直接读取 `reddit_profiles.json` 或 `twitter_profiles.csv`，后续 provenance 字段必须同时写入实时文件和普通 profiles API，否则前端生成过程中看不到来源。

---

## 三、改造目标

### 3.1 不可妥协的业务目标

1. **人设必须对应真实人物、真实机构，或明确可验证的真实群体。**
2. **人设事实必须来自联网检索的公开来源。**
3. **来源不足、重名冲突、消歧失败时，不得生成伪真实人设。**
4. **Agent 在深度互动中仍复用现有 LLM 能力，但 system prompt 必须基于真实 dossier。**
5. **后续推荐算法、热点编排、初始激活序列、双平台模拟仍沿用现有项目实现。**

### 3.2 实体准入规则

默认准入策略如下：

| 实体类别 | 默认策略 | 说明 |
|---|---|---|
| 真实人物 | 允许 | 需完成搜索验证与事件上下文消歧 |
| 真实机构 | 允许 | 如媒体、企业、政府部门、学校、协会 |
| 真实群体 | 默认不启用 | 只有在存在可验证公开身份且配置允许时才可生成 Agent |
| 抽象概念 | 禁止 | 如舆论、风向、情绪、正义 |
| 模糊实体/重名未消歧 | 禁止 | 标记 `ambiguous` |
| 搜索无可靠来源 | 禁止 | 标记 `unverified` |

---

## 四、推荐技术方案

## 4.1 增加“真实性验证闸门”

### 4.1.1 闸门位置

真实性验证必须放在 `SimulationManager.prepare_simulation()` 的“读取实体”与“生成 profile”之间：

```text
读取实体
    ↓
真实性验证与消歧
    ↓
只将 verified 实体送入 profile generator
    ↓
生成 profile 与模拟配置
```

### 4.1.2 验证结果状态

建议统一为 4 种：

- `verified`
- `ambiguous`
- `unverified`
- `unsupported`

解释：

- `verified`：来源充分、候选唯一、上下文匹配
- `ambiguous`：存在多个候选，无法确认是哪一个
- `unverified`：检索不到足够可信资料
- `unsupported`：抽象实体、私密实体、不可公开验证群体

### 4.1.3 严格模式

默认配置必须为：

```python
REAL_PROFILE_STRICT_MODE = True
```

严格模式下：

- `verified` 才能进入 Agent 生成
- 其他状态一律跳过
- 若最终 `verified_profiles_count == 0`，`/prepare` 必须失败

---

## 4.2 新增 `real_entity_resolver.py`

### 4.2.1 服务职责

新增 `backend/app/services/real_entity_resolver.py`，负责：

1. 使用 Step1 的博查搜索能力联网补全候选实体资料
2. 结合事件上下文做消歧
3. 选取可信来源
4. 抽取真实事实
5. 形成结构化 dossier
6. 给出验证状态与可信度

### 4.2.2 核心数据结构

```python
@dataclass
class SourceCitation:
    title: str
    url: str
    publisher: str
    published_at: Optional[str]
    snippet: str
    reliability_level: str


@dataclass
class ResolvedRealEntity:
    canonical_name: str
    requested_name: str
    category: str                       # person | organization | group
    verification_status: str            # verified | ambiguous | unverified | unsupported
    confidence: str                     # high | medium | low
    real_identity_summary: str
    structured_facts: Dict[str, Any]
    source_citations: List[SourceCitation]
    disambiguation_notes: str
```

### 4.2.3 输入参数

`resolve(...)` 建议接收：

```python
resolve(
    entity_name: str,
    entity_type: str,
    graph_context: str,
    event_summary_md: str,
    min_source_count: int = 2
) -> ResolvedRealEntity
```

需要同时使用：

- 图谱里的摘要与关系上下文
- Step1 生成的 `seed_summary_md`
- 通过 `BochaSearchService` 获得的联网搜索结果

这样才能在“张雪”“第一财经”“浙江公安局”这类实体上做正确消歧。

### 4.2.4 信息不足时的补全规则

现实事件提取出的节点、关系与 Agent 人设信息必须建立在真实资料上。对图谱中只有名称、摘要过短或上下文不足的实体，`RealEntityResolver` 必须先联网补全，再决定是否生成 Agent：

1. 人物示例：`张雪` 需要结合事件摘要查询其真实身份、公开背景和与事件的关系。
2. 机构示例：`西湖大学`、`西湖公安局`、新闻媒体或新闻网组织，需要查询真实名称、职能、地区或公开介绍。
3. 联网补全后仍无可靠公开来源，字段保持 `None`/“未公开”，实体状态返回 `unverified` 或 `ambiguous`，不得以 LLM 猜测填满。
4. LLM 可以在已检索来源上做摘要、归一化和有限推断；没有来源支撑的硬事实不能写入真实 dossier。

### 4.2.5 验证规则

建议规则如下：

1. **可信来源优先级**
   - 官方网站 / 官方公众号 / 官方通报
   - 主流媒体
   - 权威数据库 / 百科类公开资料
   - 其他公开来源

2. **人物验证**
   - 至少 2 个可信来源，或 1 个官方/强权威来源
   - 候选人与事件上下文匹配

3. **机构验证**
   - 至少 1 个官方来源，或 2 个主流来源
   - 名称、职能、隶属或地区与上下文匹配

4. **群体验证**
   - 默认不启用
   - 若启用，必须存在公开可验证的组织性或代表性

5. **消歧失败**
   - 如果两个候选实体都合理但无法区分，必须返回 `ambiguous`

6. **Graphiti fallback 兜底**
   - 若实体类型为 `Entity` 或 labels 只有默认标签，必须依赖 `entity.name + entity.summary + related_edges + seed_summary_md` 做真实性验证。
   - 不能因为 Graphiti fallback 返回了节点，就默认该节点可生成 Agent。
   - 抽象节点、话题节点、情绪节点即使出现在图谱中，也必须返回 `unsupported`。

---

## 4.3 `OasisProfileGenerator` 改造

### 4.3.1 去掉错误降级路径

以下行为必须删除或仅保留为本地调试隐藏开关，不得在正式链路启用：

- 搜索失败后使用图谱摘要“脑补”人设
- 用随机年龄、随机性别、随机职业填补空白
- 把默认 MBTI 当成真实人格

### 4.3.2 生成逻辑改造

`generate_profile_from_entity(...)` 建议改为：

```python
def generate_profile_from_entity(
    self,
    entity: EntityNode,
    user_id: int,
    use_llm: bool = True,
    resolved_real_entity: Optional[ResolvedRealEntity] = None,
    strict_real_mode: bool = True
) -> OasisAgentProfile:
```

处理规则：

1. `resolved_real_entity.verification_status != "verified"` 时：
   - 严格模式：直接抛出 `UnverifiedEntityError`
   - 非严格模式：仅用于调试，不作为正式功能说明

2. 真实人设字段只能从 `resolved_real_entity.structured_facts` 填充
3. 图谱或上传材料信息不足时，先通过 `RealEntityResolver -> BochaSearchService` 联网补全
4. 补全后仍缺失的信息填 `None` 或“未公开”，不得猜测
5. OASIS 运行所需的默认字段可以保留，但必须放入 `runtime_traits` 或运行配置区域，不得再作为真实资料展示

### 4.3.3 Persona 输出模板

建议 persona 改为结构化 dossier：

```markdown
# {canonical_name} 真实资料档案

## 真实身份

## 公开背景

## 与当前事件的关系

## 公开言论/公开行动特征

## 可能的社交媒体表达方式
说明：本节只能是基于公开事实的有限推断，且需显式标记“推断”。

## 信息来源
- [标题](URL)
```

### 4.3.4 Prompt 约束

LLM Prompt 必须显式要求：

1. 只基于提供的事实与引用写作
2. 不能生成来源中不存在的经历
3. 不能擅自补全年龄、MBTI、性格史
4. 不确定项必须写“未知/未公开”
5. 推断性描述必须显式标注“推断”

---

## 4.4 真实事实与仿真参数分栏

### 4.4.1 必须当成真实事实的字段

- `name`
- `bio`
- `persona`
- `profession`（仅当来源明确）
- `country/region`（仅当来源明确）
- `source citations`
- `verification status`

### 4.4.2 只能当成仿真参数的字段

以下字段即使保留在 OASIS 运行结构中，也不能再作为“真实信息”向用户展示：

- `karma`
- `friend_count`
- `follower_count`
- `statuses_count`
- `mbti`
- 平台活跃度、响应延迟、情感偏向等行为参数

### 4.4.3 年龄与性别的处理

- 年龄、性别只有在公开来源明确时才写入真实资料。
- 未公开时为 `null`，前端显示“未公开/未验证”。
- 不得再用默认值 30 岁、male/female 来充当真实事实。

---

## 4.5 `SimulationManager.prepare_simulation()` 改造

### 4.5.1 内部流程

建议改成如下步骤：

```text
1. 读取图谱实体
2. 读取项目的 seed_summary_md
3. 对每个实体做真实性验证
4. 过滤出 verified 实体
5. 为 verified 实体生成 profile
6. 统计 skipped entities
7. 保存 profile 文件与状态
8. 继续生成 simulation_config
```

### 4.5.2 返回增强

`/api/simulation/prepare` 与 `/prepare/status` 应增加：

```json
{
  "candidate_entities_count": 28,
  "verified_profiles_count": 16,
  "skipped_entities_count": 12,
  "skipped_entities_preview": [
    {
      "name": "某网友",
      "reason": "unverified"
    },
    {
      "name": "支持方",
      "reason": "unsupported"
    }
  ]
}
```

### 4.5.3 失败条件

任一条件满足时，`prepare` 应失败：

1. 没有任何 verified entity
2. 搜索服务不可用且无法验证任何实体
3. 项目缺少 `seed_summary_md` 或图谱未构建完成

---

## 4.6 API 改造

### 4.6.1 `POST /api/simulation/prepare`

建议新增可选参数：

```json
{
  "simulation_id": "sim_xxxx",
  "entity_types": ["PublicFigure", "MediaOutlet"],
  "use_llm_for_profiles": true,
  "use_real_profiles": true,
  "strict_real_mode": true,
  "allow_group_agents": false,
  "min_source_count": 2,
  "parallel_profile_count": 3,
  "force_regenerate": false
}
```

说明：

- `use_real_profiles` 默认 `true`
- `strict_real_mode` 默认 `true`
- `allow_group_agents` 默认 `false`

### 4.6.2 Profiles 返回结构

`/api/simulation/<simulation_id>/profiles` 与 realtime 接口建议返回：

```json
{
  "user_id": 0,
  "username": "zhang_xue_001",
  "name": "张雪",
  "bio": "......",
  "persona": "......",
  "profession": "职业赛车手",
  "verification_status": "verified",
  "info_confidence": "high",
  "info_sources": [
    "https://...",
    "https://..."
  ],
  "source_citations": [
    {
      "title": "......",
      "url": "https://...",
      "publisher": "......",
      "published_at": "2026-05-01"
    }
  ],
  "real_identity_summary": "中国赛车手，与当前事件直接相关",
  "runtime_traits": {
    "mbti_proxy": "ISTJ",
    "platform_activity_level": 0.7
  }
}
```

其中 `runtime_traits` 仅用于仿真，不属于真实事实。

---

## 4.7 前端 Step2 展示改造

### 4.7.1 卡片层展示

`frontend/src/components/Step2EnvSetup.vue` 中每个 profile 卡片需展示：

- 验证状态徽标
- 可信度
- 来源数量
- 真实简介

### 4.7.2 详情弹窗展示

详情弹窗应拆成两个分区：

1. **真实资料**
   - 真实身份
   - 公开背景
   - 事件关联
   - 来源列表

2. **仿真运行参数**
   - 平台影响力
   - 活跃节奏
   - 仅供模拟使用的行为控制参数

禁止继续把 `MBTI`、默认年龄等放在“真实资料”区域。

### 4.7.3 跳过实体面板

Step2 页面应增加“未进入模拟的实体”摘要：

```text
跳过实体
- 某网友：unverified
- 支持方：unsupported
- 张雪：ambiguous（存在多个候选）
```

这样用户才能知道为什么某些实体没有生成 Agent。

---

## 4.8 对话能力保持现有架构

当前 `/api/simulation/interview` 与 `/api/simulation/interview/batch` 机制可继续复用，不需要重写。

但需要补两点：

1. `persona` 改成真实 dossier 后，Agent 在 Step5 中应基于真实资料回答。
2. Interview Prompt 中应增加约束：
   - 如资料中没有该事实，回答“在已验证资料中没有该信息”。
   - 不得为迎合提问自行脑补经历或观点。

使用的模型仍沿用：

```python
Config.LLM_MODEL_NAME
Config.LLM_BASE_URL
Config.LLM_API_KEY
```

---

## 五、与现有代码的对应修改点

| 文件 | 必改内容 |
|---|---|
| `backend/app/services/real_entity_resolver.py` | 新增真实性验证与资料归一化服务 |
| `backend/app/services/oasis_profile_generator.py` | 移除虚构降级，接入 resolver 结果，导出 provenance |
| `backend/app/services/simulation_manager.py` | 增加真实性验证闸门与 skipped entity 统计 |
| `backend/app/api/simulation.py` | 扩展 prepare / status / profiles / profiles realtime 响应结构 |
| `frontend/src/components/Step2EnvSetup.vue` | 增加验证状态、来源列表、跳过实体、真实信息与仿真参数分栏 |
| `frontend/src/api/simulation.js` | 适配新增参数和增强响应 |

---

## 六、安全、性能与缓存

1. **搜索内容提示词注入**
   - 与 Step1 相同，网页文本进入 LLM 前必须清洗。

2. **来源缓存**
   - 对同一实体名 + 事件摘要可做 24 小时缓存，减少重复博查查询。

3. **并发控制**
   - `parallel_profile_count` 建议默认 3，不建议高于 5，避免博查接口限流。

4. **来源最小要求**
   - 默认 `min_source_count=2`
   - 对政府机构等强官方主体，可接受 1 个高可信官方来源

5. **数据展示安全**
   - 前端来源链接须加 `rel="noopener noreferrer"`。

6. **OASIS 兼容边界**
   - `reddit_profiles.json` 与 `twitter_profiles.csv` 仍需满足 OASIS 运行字段要求。
   - 对 OASIS 必填但无法真实验证的字段，应以 `runtime_traits` 或等价结构保存为仿真参数。
   - 前端不得把这些运行字段展示成真实年龄、真实性别、真实 MBTI 或真实地区。

---

## 七、验收标准

以下条件全部满足，Step2 改造才算通过：

1. 生成的人设不再出现随机职业、随机年龄、随机经历。
2. 搜索失败或消歧失败的实体不会被伪造为 Agent。
3. Step2 界面能看到验证状态、来源、可信度。
4. 至少 1 个实体验证成功后，模拟仍能继续生成配置。
5. 若 0 个实体验证成功，`prepare` 明确失败并给出原因。
6. Step5 对话基于真实 dossier，而不是基于虚构背景。
7. Graphiti fallback 返回的默认 `Entity` 节点必须经过真实性验证；抽象节点不得进入 profile 生成。
8. `/profiles/realtime` 在生成过程中也能返回验证状态、来源数量和来源列表。
