"""
Step1 seed 分析服务
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from ..utils.llm_client import LLMClient
from .bocha_search_service import SearchSource


@dataclass
class SeedAnalysisResult:
    """seed 分析结果"""

    seed_summary_md: str
    simulation_suggestions: List[str] = field(default_factory=list)
    entity_hints: List[str] = field(default_factory=list)
    seed_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SeedAnalysisService:
    """基于搜索结果或文件文本生成事件摘要和后续模拟建议"""

    MAX_MATERIAL_LENGTH = 24000
    MAX_SUGGESTION_LENGTH = 45
    SUGGESTION_FOCUS_KEYWORDS = (
        "吐槽",
        "质疑",
        "争议",
        "投诉",
        "回应",
        "辟谣",
        "道歉",
        "发酵",
        "热议",
        "转发",
        "曝光",
        "评论",
        "围观",
        "不满",
        "担忧",
        "误解",
    )
    STRUCTURAL_SUGGESTION_PATTERNS = (
        r"\*\*[^*]+?\*\*",
        r"(?:^|[-—])(?:时间|地点|来源|URL|站点|发布时间|阶段|参考来源)[:：]",
        r"(?:事件发生阶段|舆情发酵与回应|事件概述|当前事件总结|参考来源)",
        r"^\d{4}年\d{1,2}月\d{1,2}日",
        r"^至\d{1,2}月\d{1,2}日",
    )
    WEB_SEARCH_SUMMARY_TEMPLATE = """联网搜索材料需要整理成一份可直接作为“完整事件内容”的中文 Markdown 文档，风格参考深度新闻全记录：
1. 标题使用“# 事件/主题全记录”，下设“事件概述”先给出时间、地点、核心主体、关键结果。
2. 按时间线拆成若干阶段，优先使用“## 一、...”和“### 阶段/争议/影响”组织。
3. 单独提炼关键人物、组织机构、产品/技术参数、资本/政策/供应链、舆论争议、官方回应等模块；有结构化数据时使用 Markdown 表格。
4. 每个事实尽量保留可追溯的时间、主体、动作、结果，避免空泛评价。
5. 末尾给出“当前事件总结”，从个人/企业、产业、政府/监管、舆论传播等角度归纳规律。
6. 若搜索材料含来源 URL，末尾保留“参考来源”列表；不得编造材料外的新来源。
7. 尽可能完整覆盖政府/监管、单位、机构、企业/品牌、媒体、组织/协会、意见领袖/网红、社区、公众、主配角、网民/个人等关键具体相关实体"""

    FILE_UPLOAD_SUMMARY_TEMPLATE = """上传文件材料只需要生成辅助摘要：提炼核心事实、完整主体、争议变量和后续推演方向即可；完整展示和图谱抽取会使用上传文件解析出的原文。"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client

    def analyze_from_sources(
        self,
        sources: List[SearchSource],
        query: str,
        additional_context: Optional[str] = None,
    ) -> SeedAnalysisResult:
        source_dicts = [source.to_dict() if hasattr(source, "to_dict") else source for source in sources]
        material = self._build_search_material(source_dicts, query)
        return self._analyze(
            material=material,
            input_mode="web_search",
            topic=query,
            source_count=len(source_dicts),
            additional_context=additional_context,
        )

    def analyze_from_text(
        self,
        text: str,
        topic: str = "上传文档",
        additional_context: Optional[str] = None,
    ) -> SeedAnalysisResult:
        material = (text or "").strip()
        return self._analyze(
            material=material,
            input_mode="file_upload",
            topic=topic,
            source_count=1 if material else 0,
            additional_context=additional_context,
        )

    def _analyze(
        self,
        material: str,
        input_mode: str,
        topic: str,
        source_count: int,
        additional_context: Optional[str],
    ) -> SeedAnalysisResult:
        clean_material = (material or "").strip()
        if not clean_material:
            return SeedAnalysisResult(
                seed_summary_md="## Seed 摘要\n\n暂无可分析的材料。",
                simulation_suggestions=[],
                entity_hints=[],
                seed_metadata={
                    "input_mode": input_mode,
                    "source_count": source_count,
                    "analysis_mode": "fallback",
                },
            )

        try:
            result = self._analyze_with_llm(
                material=clean_material[: self.MAX_MATERIAL_LENGTH],
                input_mode=input_mode,
                topic=topic,
                additional_context=additional_context,
            )
            result.seed_metadata.update({
                "input_mode": input_mode,
                "source_count": source_count,
                "analysis_mode": "llm",
            })
            return result
        except Exception as exc:
            fallback = self._fallback_analysis(clean_material, input_mode, topic, source_count)
            fallback.seed_metadata["llm_error"] = str(exc)
            return fallback

    def _analyze_with_llm(
        self,
        material: str,
        input_mode: str,
        topic: str,
        additional_context: Optional[str],
    ) -> SeedAnalysisResult:
        if input_mode == "web_search":
            return self._analyze_web_search_with_llm(
                material=material,
                topic=topic,
                additional_context=additional_context,
            )

        client = self.llm_client or LLMClient()
        style_template = (
            self.WEB_SEARCH_SUMMARY_TEMPLATE
            if input_mode == "web_search"
            else self.FILE_UPLOAD_SUMMARY_TEMPLATE
        )
        prompt = f"""请只基于给定材料生成 Step1 seed 分析，禁止引入材料之外的事实。

输出 JSON，字段：
- seed_summary_md: Markdown 字符串；如果是联网搜索输入，必须整理成可直接展示和后续图谱抽取使用的完整事件文档；如果是上传文件输入，生成辅助摘要即可。材料不足时请明确说明不足。
- simulation_suggestions: 2-3 条可用于后续舆情推演的中文建议。每条必须强关联材料中的具体事件触发点、关键插曲、公众吐槽、争议问题或回应动作，避免跳到背景企业、产品线、行业趋势等泛方向；例如材料核心是“小女孩吐槽”，方向就围绕吐槽内容、二次传播和公众反应，不要转成“小米汽车”方向。文字要通俗、简短，每条建议控制在18-36个中文字符左右，最长不超过45个中文字符；不要使用“模拟”字样，统一使用“推演”“追踪”“研判”等表达。
- entity_hints: 可能进入图谱的关键实体名称列表，必须是材料中原文出现过的人、组织、机构、平台、媒体或关键群体名称。

输入类型：{input_mode}
主题：{topic}
额外说明：{additional_context or "无"}
整理模板：
{style_template}

材料：
{material}
"""
        data = client.chat_json(
            messages=[
                {"role": "system", "content": "你是严谨的中文资料分析助手，只能依据输入材料作答。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1800,
        )

        summary = str(data.get("seed_summary_md") or "").strip()
        hints = self._filter_entity_hints(
            self._clean_string_list(data.get("entity_hints"), limit=120),
            material,
        )
        suggestions = self._repair_suggestions(
            self._clean_suggestion_list(data.get("simulation_suggestions"), limit=3),
            topic=topic,
            entity_hints=hints,
            material=material,
        )

        if not summary:
            raise ValueError("LLM 未返回 seed_summary_md")

        return SeedAnalysisResult(
            seed_summary_md=summary,
            simulation_suggestions=suggestions[:3],
            entity_hints=hints,
            seed_metadata={},
        )

    def _analyze_web_search_with_llm(
        self,
        material: str,
        topic: str,
        additional_context: Optional[str],
    ) -> SeedAnalysisResult:
        """联网搜索场景生成完整 Markdown 文档。

        不把长 Markdown 放进 JSON 字符串，避免模型输出被截断后触发 JSON 解析失败。
        """
        client = self.llm_client or LLMClient()
        summary_prompt = f"""请只基于给定联网搜索材料，整理生成一份可直接作为前端“完整事件内容”展示、也可用于后续图谱实体抽取的中文 Markdown 完整文档。

硬性要求：
- 只输出 Markdown 正文，不要输出 JSON，不要包裹代码块。
- 禁止引入材料之外的事实；材料不足或来源可疑时必须在文档中说明。
- 文档要像深度新闻全记录，而不是简单摘录搜索结果。
- 保留关键时间、主体、动作、结果、争议、官方回应、影响和风险。
- 能表格化的信息使用 Markdown 表格。

主题：{topic}
额外说明：{additional_context or "无"}
整理模板：
{self.WEB_SEARCH_SUMMARY_TEMPLATE}

联网搜索材料：
{material}
"""
        summary = client.chat(
            messages=[
                {"role": "system", "content": "你是严谨的中文资料整理助手，只能依据输入材料生成结构化 Markdown 文档。"},
                {"role": "user", "content": summary_prompt},
            ],
            temperature=0.2,
            max_tokens=5000,
        ).strip()

        if not summary:
            raise ValueError("LLM 未返回联网搜索整理文档")

        suggestions, hints = self._generate_web_search_auxiliary(
            client=client,
            summary=summary,
            material=material,
            topic=topic,
        )

        if not suggestions:
            suggestions = self._fallback_suggestions(topic, hints, f"{summary}\n{material}")
        if not hints:
            hints = self._extract_entity_hints(f"{summary}\n{material}")

        return SeedAnalysisResult(
            seed_summary_md=summary,
            simulation_suggestions=suggestions[:3],
            entity_hints=self._filter_entity_hints(hints, f"{summary}\n{material}")[:120],
            seed_metadata={},
        )

    def _generate_web_search_auxiliary(
        self,
        client: LLMClient,
        summary: str,
        material: str,
        topic: str,
    ) -> tuple[List[str], List[str]]:
        """基于已生成文档提取短建议和实体提示，失败时交给调用方兜底。"""
        try:
            data = client.chat_json(
                messages=[
                    {"role": "system", "content": "你是严谨的信息抽取助手，只返回 JSON。"},
                    {
                        "role": "user",
                        "content": f"""请基于以下整理文档返回 JSON：
{{
  "simulation_suggestions": ["2-3条中文舆情推演建议"],
  "entity_hints": ["材料原文出现过的人、组织、机构、平台、媒体或关键群体名称"]
}}

要求：
- 先判断材料里的“事件焦点”：优先选择引发关注的具体插曲、吐槽/质疑、争议动作、回应或辟谣，而不是名人/品牌/产品的泛业务方向。
- simulation_suggestions 必须围绕这个具体焦点，适合舆论走向、传播路径或公众反应推演。
- 每条建议必须包含材料中出现过的具体锚点（人物、群体、动作、平台、争议词至少一个），让用户一眼看出和事件有关。
- 文字要通俗、短句化，每条18-36个中文字符左右，最长不超过45个中文字符；不要写长句、套话或学术化表达。
- 禁止输出脱离事件触发点的方向：不要把“小女孩吐槽”类事件转写成企业产品、汽车业务、资本市场、行业趋势等泛主题。
- 不要使用“模拟”字样，统一使用“推演”“追踪”“研判”等表达。
- entity_hints 目标不少于50个；如果材料不足50个，则尽最大可能列出所有与事件相关的具体主体，不要人为截断到少量核心实体。
- entity_hints 必须优先覆盖核心人物：受害人/被害人、嫌疑人/犯罪嫌疑人、被告人、当事人、主角、亲属等；这些人物即使不是可发声账号也要保留为图谱实体提示。
- entity_hints 必须继续覆盖政府/监管、学校/单位、企业/品牌、媒体/自媒体、社交平台、社区/社群、公众群体、专家/KOL、赛事/组织等具体相关主体。
- 小红书、微博、抖音、豆瓣、知乎等媒体/社交平台可以作为平台实体提示，但不要当作个人。

主题：{topic}
整理文档：
{summary[:12000]}
""",
                    },
                ],
                temperature=0.2,
                max_tokens=1200,
            )
            hints = self._filter_entity_hints(
                self._clean_string_list(data.get("entity_hints"), limit=120),
                f"{summary}\n{material}",
            )
            suggestions = self._repair_suggestions(
                self._clean_suggestion_list(data.get("simulation_suggestions"), limit=3),
                topic=topic,
                entity_hints=hints,
                material=f"{summary}\n{material}",
            )
            return suggestions, hints
        except Exception:
            return [], []

    def _fallback_analysis(
        self,
        material: str,
        input_mode: str,
        topic: str,
        source_count: int,
    ) -> SeedAnalysisResult:
        excerpts = self._pick_excerpts(material)
        entity_hints = self._extract_entity_hints(material)
        summary_lines = [
            "## Seed 摘要",
            "",
            f"- 输入方式：{input_mode}",
            f"- 主题：{topic or '未提供'}",
            f"- 可用来源数：{source_count}",
            "",
            "## 材料摘录",
        ]
        summary_lines.extend([f"- {excerpt}" for excerpt in excerpts] or ["- 材料内容较少，暂无法提炼稳定摘录。"])

        suggestions = self._fallback_suggestions(topic, entity_hints, material)
        return SeedAnalysisResult(
            seed_summary_md="\n".join(summary_lines),
            simulation_suggestions=suggestions,
            entity_hints=entity_hints,
            seed_metadata={
                "input_mode": input_mode,
                "source_count": source_count,
                "analysis_mode": "fallback",
            },
        )

    @staticmethod
    def _build_search_material(sources: List[Dict[str, Any]], query: str) -> str:
        lines = [f"检索词：{query}", ""]
        for idx, source in enumerate(sources, 1):
            title = source.get("title") or "未命名来源"
            url = source.get("url") or ""
            snippet = source.get("snippet") or ""
            summary = source.get("summary") or ""
            site_name = source.get("site_name") or ""
            date_published = source.get("date_published") or ""
            lines.append(f"### 来源 {idx}: {title}")
            if site_name:
                lines.append(f"站点：{site_name}")
            if date_published:
                lines.append(f"发布时间：{date_published}")
            if url:
                lines.append(f"URL：{url}")
            if snippet:
                lines.append(f"摘要片段：{snippet}")
            if summary:
                lines.append(f"搜索摘要：{summary}")
            lines.append("")
        return "\n".join(lines).strip()

    @staticmethod
    def _clean_string_list(value: Any, limit: int) -> List[str]:
        if not isinstance(value, list):
            return []
        cleaned = []
        for item in value:
            text = str(item).strip()
            if text and text not in cleaned:
                cleaned.append(text)
            if len(cleaned) >= limit:
                break
        return cleaned

    @classmethod
    def _clean_suggestion_list(cls, value: Any, limit: int) -> List[str]:
        suggestions = cls._clean_string_list(value, limit=limit)
        cleaned = []
        for text in suggestions:
            normalized = cls._normalize_suggestion_text(text)
            if normalized and normalized not in cleaned:
                cleaned.append(normalized)
        return cleaned

    @classmethod
    def _normalize_suggestion_text(cls, text: str) -> str:
        normalized = str(text or "").strip()
        normalized = re.sub(r"^\s*[-*•\d一二三四五六七八九十]+[.、）)]\s*", "", normalized)
        normalized = re.sub(r"[*_`]+", "", normalized)
        normalized = (
            normalized
            .replace("可模拟", "可推演")
            .replace("模拟", "推演")
        )
        normalized = re.sub(r"\s+", "", normalized)
        normalized = normalized.strip(" ，。；;:：、（）()[]【】「」“”\"'\n\t")
        return cls._limit_suggestion_length(normalized)

    @classmethod
    def _limit_suggestion_length(cls, text: str) -> str:
        if len(text) <= cls.MAX_SUGGESTION_LENGTH:
            return text
        for separator in ("，", "；", "。", "、", ",", ";", "."):
            index = text.rfind(separator, 0, cls.MAX_SUGGESTION_LENGTH + 1)
            if index >= 18:
                return text[:index].strip(" ，。；;:：、")
        return text[: cls.MAX_SUGGESTION_LENGTH].strip(" ，。；;:：、")

    @classmethod
    def _repair_suggestions(
        cls,
        suggestions: List[str],
        topic: str,
        entity_hints: List[str],
        material: str,
    ) -> List[str]:
        valid = []
        for suggestion in suggestions:
            if cls._is_structural_suggestion(suggestion):
                continue
            if suggestion and suggestion not in valid:
                valid.append(suggestion)
        if valid:
            return valid[:3]
        return cls._fallback_suggestions(topic, entity_hints, material)

    @classmethod
    def _is_structural_suggestion(cls, suggestion: str) -> bool:
        text = str(suggestion or "").strip()
        if not text:
            return True
        return any(re.search(pattern, text) for pattern in cls.STRUCTURAL_SUGGESTION_PATTERNS)

    @staticmethod
    def _filter_entity_hints(hints: Iterable[str], material: str) -> List[str]:
        filtered = []
        for hint in hints:
            if hint and hint in material and hint not in filtered:
                filtered.append(hint)
        return filtered

    @classmethod
    def _extract_entity_hints(cls, material: str) -> List[str]:
        hints: List[str] = []

        for pattern in cls._core_person_hint_patterns():
            for match in re.findall(pattern, material):
                candidate = cls._normalize_person_hint(match)
                if cls._is_valid_person_hint(candidate) and candidate not in hints:
                    hints.append(candidate)
                if len(hints) >= 120:
                    return hints

        patterns = [
            r"[\u4e00-\u9fffA-Za-z0-9·（）()]{2,30}(?:公司|集团|大学|学院|政府|委员会|协会|机构|平台|媒体|日报|时报|新闻|法院|部门|医院|学校)",
            r"(?:小红书|抖音|微博|豆瓣|知乎|快手|微信|哔哩哔哩|B站|b站|TikTok|YouTube|Facebook|Instagram|Twitter|Reddit)",
            r"\b[A-Z][A-Za-z0-9&.\- ]{1,40}(?:Inc|Ltd|LLC|University|College|Agency|Court|Media|News|Platform)\b",
        ]
        for pattern in patterns:
            for match in re.findall(pattern, material):
                candidate = cls._normalize_entity_hint(match)
                if 2 <= len(candidate) <= 40 and candidate not in hints:
                    hints.append(candidate)
                if len(hints) >= 120:
                    return hints
        return hints

    @staticmethod
    def _core_person_hint_patterns() -> List[str]:
        person_name = r"[\u4e00-\u9fff]{2,4}(?:·[\u4e00-\u9fff]{1,4})?"
        name_boundary = r"(?=$|[，,。；;、\s]|与|和|是|为|系|被|涉|因|已|将|获|死|遇|失|案|事)"
        return [
            rf"(?:受害人|被害人|死者|遇害者|嫌疑人|犯罪嫌疑人|被告人|当事人|丈夫|妻子|女儿|儿子|亲属|家属|律师|证人|主角|配角)\s*({person_name}){name_boundary}",
            rf"(?<![\u4e00-\u9fff])({person_name}){name_boundary}\s*(?:是|为|系|被指为|被认定为|涉嫌|因|已被|被判|获刑|伏法|死亡|遇害|失踪)",
            rf"(?<![\u4e00-\u9fff])({person_name})\s*[，,]\s*(?:男|女|丈夫|妻子|受害人|被害人|嫌疑人|犯罪嫌疑人|被告人|当事人)",
        ]

    @staticmethod
    def _normalize_entity_hint(value: Any) -> str:
        if isinstance(value, tuple):
            value = next((item for item in value if item), "")
        return str(value or "").strip(" ，。；;:：、（）()[]【】「」“”\"'\n\t")

    @classmethod
    def _normalize_person_hint(cls, value: Any) -> str:
        candidate = cls._normalize_entity_hint(value)
        while len(candidate) > 2 and candidate[0] in {"人", "者"}:
            candidate = candidate[1:]
        while len(candidate) > 2 and candidate[-1] in {"与", "和", "是", "为", "系", "因", "被", "将", "已", "于", "向", "对"}:
            candidate = candidate[:-1]
        return candidate

    @staticmethod
    def _is_valid_person_hint(candidate: str) -> bool:
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}(?:·[\u4e00-\u9fff]{1,4})?", candidate or ""):
            return False
        if candidate.endswith(("市", "区", "县", "省", "镇", "乡", "村", "路", "街", "苑", "场", "店", "案")):
            return False
        blocked = {
            "嫌疑人",
            "受害人",
            "被害人",
            "当事人",
            "犯罪嫌疑",
            "社交平台",
            "媒体平台",
            "检察机关",
            "公安机关",
            "人民法院",
            "人民检察",
        }
        if candidate in blocked:
            return False
        return not any(keyword in candidate for keyword in ("公司", "集团", "法院", "检察", "公安", "媒体", "平台"))

    @staticmethod
    def _pick_excerpts(material: str) -> List[str]:
        compact = re.sub(r"\s+", " ", material).strip()
        if not compact:
            return []
        sentences = re.split(r"(?<=[。！？.!?])\s+", compact)
        excerpts = []
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 12:
                continue
            excerpts.append(sentence[:220])
            if len(excerpts) >= 5:
                break
        if not excerpts and compact:
            excerpts.append(compact[:220])
        return excerpts

    @classmethod
    def _fallback_suggestions(cls, topic: str, entity_hints: List[str], material: str = "") -> List[str]:
        subject = topic or "当前材料"
        focus = cls._extract_event_focus(material) or subject
        if entity_hints:
            joined = "、".join(entity_hints[:3])
            return [
                cls._normalize_suggestion_text(f"追踪{focus}的二次传播路径"),
                cls._normalize_suggestion_text(f"研判{joined}围绕{focus}的回应"),
                cls._normalize_suggestion_text(f"推演公众对{focus}的态度变化"),
            ]
        return [
            cls._normalize_suggestion_text(f"追踪{focus}的二次传播路径"),
            cls._normalize_suggestion_text(f"推演公众对{focus}的态度变化"),
        ]

    @classmethod
    def _extract_event_focus(cls, material: str) -> str:
        candidates = cls._extract_focus_candidates(material)
        if not candidates:
            return ""
        for keyword in cls.SUGGESTION_FOCUS_KEYWORDS:
            for candidate in candidates:
                if keyword in candidate:
                    return cls._compact_focus_sentence(candidate)
        return cls._compact_focus_sentence(candidates[0])

    @classmethod
    def _extract_focus_candidates(cls, material: str) -> List[str]:
        candidates = []
        for raw_line in str(material or "").splitlines():
            line = cls._clean_focus_line(raw_line)
            if not line:
                continue
            for sentence in re.split(r"(?<=[。！？.!?])\s*", line):
                sentence = sentence.strip()
                if len(sentence) >= 8 and sentence not in candidates:
                    candidates.append(sentence)
        if candidates:
            return candidates

        compact = re.sub(r"\s+", " ", material or "").strip()
        return [sentence.strip() for sentence in re.split(r"(?<=[。！？.!?])\s*", compact) if len(sentence.strip()) >= 8]

    @classmethod
    def _clean_focus_line(cls, line: str) -> str:
        text = re.sub(r"^\s{0,3}#{1,6}\s*", "", line or "").strip()
        text = re.sub(r"^\s*[-*•]\s*", "", text).strip()
        text = re.sub(r"^\s*\d+[.、）)]\s*", "", text).strip()
        text = re.sub(r"[*_`]+", "", text)
        text = re.sub(r"https?://\S+", "", text).strip()
        if not text or re.fullmatch(r"[-|:\s]+", text):
            return ""
        if re.match(r"^(检索词|来源|站点|发布时间|URL|参考来源|时间|地点)[:：]", text):
            return ""
        text = re.sub(r"^(关键结果|关键插曲|现场细节|舆论争议|官方回应|争议萌芽|背景与行程)[:：]\s*", "", text)
        if re.fullmatch(r"[一二三四五六七八九十]+、.+", text):
            return ""
        return text

    @classmethod
    def _compact_focus_sentence(cls, sentence: str) -> str:
        focus = re.sub(r"^[#>\-\s\d、.）)]+", "", sentence or "")
        focus = re.sub(r"https?://\S+", "", focus)
        focus = focus.strip(" ，。；;:：、（）()[]【】「」“”\"'\n\t")
        focus = re.sub(r"^(一|二|两|三|\d+)(段|条|则|篇|个)", "", focus)
        focus = re.sub(r"^(一|二|两|三|\d+)(名|位|个)", "", focus)
        short_focus = cls._extract_short_focus_phrase(focus)
        if short_focus:
            return short_focus
        clauses = [clause.strip(" ，。；;:：、") for clause in re.split(r"[，,；;。]", focus) if clause.strip()]
        focused_clause = next(
            (clause for clause in clauses if any(keyword in clause for keyword in cls.SUGGESTION_FOCUS_KEYWORDS)),
            "",
        )
        if focused_clause:
            focus = focused_clause
        if len(focus) <= 26:
            return focus
        for keyword in cls.SUGGESTION_FOCUS_KEYWORDS:
            index = focus.find(keyword)
            if index >= 0:
                start = max(0, index - 12)
                end = min(len(focus), index + len(keyword) + 12)
                return focus[start:end].strip(" ，。；;:：、")
        for separator in ("，", "；", "、", ",", ";"):
            index = focus.find(separator)
            if 8 <= index <= 26:
                return focus[:index].strip(" ，。；;:：、")
        return focus[:26].strip(" ，。；;:：、")

    @classmethod
    def _extract_short_focus_phrase(cls, focus: str) -> str:
        text = re.sub(r"[“”\"']", "", focus or "")
        patterns = [
            r"([\u4e00-\u9fff]{1,8}(?:女孩|男孩|女子|男子|老人|网友|市民|游客|顾客|员工|用户|车主|消费者|群众).{0,4}吐槽).{0,40}?视频",
            r"([\u4e00-\u9fff]{1,8}(?:女孩|男孩|女子|男子|老人|网友|市民|游客|顾客|员工|用户|车主|消费者|群众).{0,4}(?:质疑|投诉|评论)).{0,40}?视频",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return f"{match.group(1)}视频"
        return ""
