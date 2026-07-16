"""
本体生成服务
接口1：分析文本内容，生成适合社会模拟的实体和关系类型定义
"""

import json
import logging
import time
from typing import Dict, Any, List, Optional
from ..config import Config
from ..utils.llm_client import LLMClient, LLMRequestError
from ..utils.llm_routing import get_boost_llm_endpoint, get_default_llm_endpoint
from .location_entity_filter import strip_location_entity_types_from_ontology


logger = logging.getLogger("mirofish.ontology_generator")


# 本体生成的系统提示词
ONTOLOGY_SYSTEM_PROMPT = """你是一个专业的知识图谱本体设计专家。你的任务是分析给定的文本内容和模拟需求，设计适合**社交媒体舆论模拟**的实体类型和关系类型。

**重要：你必须输出有效的JSON格式数据，不要输出任何其他内容。**

## 核心任务背景

我们正在构建一个**社交媒体舆论模拟系统**。在这个系统中：
- 图谱实体用于完整记录舆论事件中的关键人物、组织、平台、群体和其他具体相关主体
- 后续生成 Agent 时会再筛选或重标可发声主体；**图谱实体不等于最终人设 Agent**
- 本体生成只提供建议类型，不是实体抽取上限；后续 Graphiti 抽取阶段允许根据文本产生任意新的实体类型
- 实体之间会相互影响、转发、评论、回应，也会承载事实关系、报道关系、法律关系和亲属关系
- 我们需要模拟舆论事件中各方的反应和信息传播路径，也需要保留事件事实的核心当事人

因此，**实体类型必须是现实中真实存在或材料中明确出现的具体相关主体类型**：

**可以是**：
- 具体的个人（公众人物、当事人、受害人/被害人、嫌疑人/被告人、主配角、亲属、意见领袖、专家学者、普通人）
- 公司、企业（包括其官方账号）
- 组织机构（大学、协会、NGO、工会等）
- 政府部门、监管机构
- 媒体机构（报纸、电视台、自媒体、网站）
- 社交媒体平台本身
- 特定群体代表（如校友会、粉丝团、维权群体等）
- 事件角色主体（受害人/被害人、嫌疑人/肇事者、目击者/证人、家属/亲属、警方/执法者、医护人员、律师/法律代理人等）

**不可以是**：
- 抽象概念（如"舆论"、"情绪"、"趋势"）
- 主题/话题（如"学术诚信"、"教育改革"）
- 观点/态度（如"支持方"、"反对方"）
- 纯地点/位置/地址/城市/地区/场所（如 `Location`, `Place`, `City`, `Region`, `Address`）。地点不会构建成人设 Agent；如果地点名称属于政府机构、学校、医院、公司等可发声组织，应归入对应组织类型，而不是地点类型。

## 输出格式

请输出JSON格式，包含以下结构：

```json
{
    "entity_types": [
        {
            "name": "实体类型名称（英文，PascalCase）",
            "description": "简短描述（英文，不超过100字符）",
            "attributes": [
                {
                    "name": "属性名（英文，snake_case）",
                    "type": "text",
                    "description": "属性描述"
                }
            ],
            "examples": ["示例实体1", "示例实体2"]
        }
    ],
    "edge_types": [
        {
            "name": "关系类型名称（英文，UPPER_SNAKE_CASE）",
            "description": "简短描述（英文，不超过100字符）",
            "source_targets": [
                {"source": "源实体类型", "target": "目标实体类型"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "对文本内容的简要分析说明（中文）"
}
```

## 设计指南（极其重要！）

### 1. 实体类型设计 - 必须严格遵守

**数量要求：不要为了凑数或压缩而固定为10个或14个类型。按文本真实主体需要输出，宁可多覆盖关键可发声主体类型，也不要把不同主体强行合并。类型数量没有最高限制。**

**覆盖要求（包含但不限于）**：
- 政府/监管部门
- 单位、机构
- 企业/品牌
- 媒体、新闻机构、自媒体平台
- 组织/协会
- 意见领袖/网红/KOL
- 社区/社群
- 公众/群体
- 主配角、当事人、关键个人
- 网民/个人

**层次结构要求（必须同时包含具体类型和兜底类型）**：

A. **兜底类型（必须包含，放在列表最后2个）**：
   - `Person`: 任何自然人个体的兜底类型。当一个人不属于其他更具体的人物类型时，归入此类。
   - `Organization`: 任何组织机构的兜底类型。当一个组织不属于其他更具体的组织类型时，归入此类。

B. **具体类型（根据文本内容设计）**：
   - 针对文本中出现的主要角色，设计更具体的类型
   - 例如：如果文本涉及学术事件，可以有 `Student`, `Professor`, `University`
   - 例如：如果文本涉及商业事件，可以有 `Company`, `CEO`, `Employee`

**为什么需要兜底类型**：
- 文本中会出现各种人物，如"中小学教师"、"路人甲"、"某位网友"
- 如果没有专门的类型匹配，他们应该被归入 `Person`
- 同理，小型组织、临时团体等应该归入 `Organization`

**具体类型的设计原则**：
- 从文本中识别出高频出现或关键的角色类型
- 核心人物类型（当事人、主角、配角、关键个人）只要在文档中出现，不论事件类型如何，都应作为图谱实体类型保留；事件角色类型（受害人、嫌疑人、证人、家属等）仅在文档涉及刑事/法律/争议事件且文本中确实出现时才设计，不要强行加入无关类型
- 如果文档内容涉及刑事/法律/争议事件，且文本中确实出现了受害人、嫌疑人、证人、家属、警方、律师等角色，才应设计对应的事件角色类型；如果文档内容与刑事/法律事件无关（如商业活动、产品发布、日常社交等），则不应强行加入 Victim、Suspect、Police 等无关类型
- 每个具体类型应该有明确的边界，避免重叠
- description 必须清晰说明这个类型和兜底类型的区别

### 2. 关系类型设计

- 数量不设上限，按事件事实、传播路径和舆论互动的真实需要设计
- 关系应该反映社媒互动中的真实联系，也要覆盖事实、法律、报道、回应、亲属、隶属、参与、影响等事件关系
- 确保关系的 source_targets 涵盖你定义的实体类型

### 3. 属性设计

- 每个实体类型1-3个关键属性
- **注意**：属性名不能使用 `name`、`uuid`、`group_id`、`created_at`、`summary`（这些是系统保留字）
- 推荐使用：`full_name`, `title`, `role`, `position`, `location`, `description` 等

## 实体类型参考

**事件角色类（按需，仅当文本涉及刑事/法律/争议事件时才使用）**：
- Victim: 受害人/被害人，事件中的直接受害者
- Suspect: 嫌疑人/肇事者/加害方，被指控或追责的主体
- Witness: 目击者/证人，事件直接目击者或知情人
- FamilyMember: 家属/亲属，当事人的家庭成员
- Police: 警方/执法机构，负责案件调查的公安机关
- LegalExpert: 法律专家/律师个人

**个人类（具体）**：
- Student: 学生
- Professor: 教授/学者
- Journalist: 记者
- Celebrity: 明星/网红/公众人物
- Executive: 高管
- Official: 政府官员
- Doctor: 医生

**个人类（兜底）**：
- Person: 任何自然人（不属于上述具体类型时使用）

**组织类（具体）**：
- University: 高校
- Company: 公司企业
- GovernmentAgency: 政府机构
- MediaOutlet: 媒体机构
- Hospital: 医院
- School: 中小学
- LawFirm: 律师事务所
- NGO: 非政府组织
- SocialMediaPlatform: 社交媒体平台本身

**组织类（兜底）**：
- Organization: 任何组织机构（不属于上述具体类型时使用）

## 关系类型参考

- WORKS_FOR: 工作于
- STUDIES_AT: 就读于
- AFFILIATED_WITH: 隶属于
- REPRESENTS: 代表
- REGULATES: 监管
- REPORTS_ON: 报道
- COMMENTS_ON: 评论
- RESPONDS_TO: 回应
- SUPPORTS: 支持
- OPPOSES: 反对
- COLLABORATES_WITH: 合作
- COMPETES_WITH: 竞争
"""


class OntologyGenerator:
    """
    本体生成器
    分析文本内容，生成实体和关系类型定义
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        # 本体生成是 Step1 的同步长请求，优先走 boost 端点降低等待时间。
        self.llm_client = llm_client
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        生成本体定义
        
        Args:
            document_texts: 文档文本列表
            simulation_requirement: 模拟需求描述
            additional_context: 额外上下文
            
        Returns:
            本体定义（entity_types, edge_types等）
        """
        started_at = time.monotonic()

        # 构建用户消息
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context
        )
        
        messages = [
            {"role": "system", "content": ONTOLOGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
        
        prompt_chars = sum(len(message.get("content", "")) for message in messages)
        logger.info(
            "开始生成本体: documents=%s, source_chars=%s, prompt_chars=%s, max_text_chars=%s",
            len(document_texts),
            sum(len(text or "") for text in document_texts),
            prompt_chars,
            self.MAX_TEXT_LENGTH_FOR_LLM,
        )

        # 调用LLM
        llm_started_at = time.monotonic()
        result = self._chat_json_with_fallback(
            messages=messages,
            temperature=0.3,
            max_tokens=6000
        )
        llm_elapsed = time.monotonic() - llm_started_at
        
        # 验证和后处理
        result = self._validate_and_process(result)
        elapsed = time.monotonic() - started_at

        logger.info(
            "本体生成完成: entity_types=%s, edge_types=%s, llm_elapsed=%.1fs, total_elapsed=%.1fs",
            len(result.get("entity_types") or []),
            len(result.get("edge_types") or []),
            llm_elapsed,
            elapsed,
        )
        
        return result

    def _chat_json_with_fallback(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> Dict[str, Any]:
        """优先使用 boost LLM，失败时回退默认 LLM。"""
        if self.llm_client:
            return self.llm_client.chat_json(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        clients = []
        boost_endpoint = get_boost_llm_endpoint()
        base_endpoint = get_default_llm_endpoint()
        if boost_endpoint:
            clients.append(LLMClient(
                api_key=boost_endpoint.api_key,
                base_url=boost_endpoint.base_url,
                model=boost_endpoint.model,
            ))
            clients[-1].route_name = boost_endpoint.route_name
        clients.append(LLMClient(
            api_key=base_endpoint.api_key,
            base_url=base_endpoint.base_url,
            model=base_endpoint.model,
        ))
        clients[-1].route_name = base_endpoint.route_name

        last_error: Optional[LLMRequestError] = None
        for index, client in enumerate(clients):
            try:
                logger.info("调用 LLM 生成本体: route=%s, model=%s", client.route_name, client.model)
                return client.chat_json(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except LLMRequestError as exc:
                last_error = exc
                has_next = index < len(clients) - 1
                if has_next:
                    logger.warning(
                        "本体生成 LLM 路由失败，尝试回退: route=%s, retryable=%s, error=%s",
                        exc.route_name or client.route_name,
                        exc.retryable,
                        exc,
                    )
                    continue
                logger.error(
                    "本体生成 LLM 路由全部失败: route=%s, retryable=%s, error=%s",
                    exc.route_name or client.route_name,
                    exc.retryable,
                    exc,
                )
                raise

        if last_error:
            raise last_error
        raise LLMRequestError("LLM 路由不可用，请检查模型配置", status_code=502)
    
    # 传给 LLM 的文本最大长度；只影响本体分析，不影响图谱构建原文。
    MAX_TEXT_LENGTH_FOR_LLM = max(1000, int(Config.ONTOLOGY_MAX_TEXT_LENGTH_FOR_LLM or 30000))
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """构建用户消息"""
        
        # 合并文本
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)
        
        # 如果文本过长，截断（仅影响传给 LLM 的内容，不影响图谱构建）。
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += f"\n\n...(原文共{original_length}字，已截取前{self.MAX_TEXT_LENGTH_FOR_LLM}字用于本体分析)..."
        
        message = f"""## 模拟需求

{simulation_requirement}

## 文档内容

{combined_text}
"""
        
        if additional_context:
            message += f"""
## 额外说明

{additional_context}
"""
        
        message += """
请根据以上内容，设计适合社会舆论模拟的实体类型和关系类型。

**必须遵守的规则**：
1. 不要固定为10个、14个或任何有限实体类型；请按文本真实主体需要输出，完整覆盖关键事件主体和可发声主体类型，类型数量没有最高限制
2. 最后2个必须是兜底类型：Person（个人兜底）和 Organization（组织兜底）
3. 具体类型包含但不限于：政府/监管、单位、机构、企业/品牌、媒体、组织/协会、意见领袖/网红、社区、公众、主配角、网民/个人等
4. 如果文档涉及刑事/法律/争议事件，且文本中确实出现了受害人、嫌疑人、证人等角色，才应包含对应的事件角色类型；商业、产品、社交等非法律类事件不要强行加入 Victim、Suspect、Police 等无关类型
5. 图谱实体不等于最终人设 Agent；Agent 生成阶段会再过滤地点、重标媒体平台，不能在本体阶段牺牲核心人物覆盖
6. 不要设计纯地点/位置/地址/城市/地区/场所类实体类型；地点只可作为主体属性或事件背景，不要作为节点类型
7. 小红书、微博、抖音、豆瓣、知乎等属于媒体/社交平台类型，不要归为 Person
8. 属性名不能使用 name、uuid、group_id 等保留字，用 full_name、org_name 等替代
9. 本体只是建议类型，不是后续抽取上限；若文档出现新的事件相关主体类型，应允许后续抽取阶段自由创建
"""
        
        return message
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """验证和后处理结果"""
        
        # 确保必要字段存在
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""
        
        # 验证实体类型
        for entity in result["entity_types"]:
            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []
            # 确保description不超过100字符
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."
        
        # 验证关系类型
        for edge in result["edge_types"]:
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []
            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."

        result = strip_location_entity_types_from_ontology(result)
        
        # 兜底类型定义
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # 检查是否已有兜底类型
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # 需要添加的兜底类型
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            # 添加兜底类型
            result["entity_types"].extend(fallbacks_to_add)

        result = strip_location_entity_types_from_ontology(result)
        
        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        将本体定义转换为Python代码（类似ontology.py）
        
        Args:
            ontology: 本体定义
            
        Returns:
            Python代码字符串
        """
        code_lines = [
            '"""',
            '自定义实体类型定义',
            '由MiroFish自动生成，用于社会舆论模拟',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== 实体类型定义 ==============',
            '',
        ]
        
        # 生成实体类型
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== 关系类型定义 ==============')
        code_lines.append('')
        
        # 生成关系类型
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # 转换为PascalCase类名
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # 生成类型字典
        code_lines.append('# ============== 类型配置 ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # 生成边的source_targets映射
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)
