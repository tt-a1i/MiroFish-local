"""
真实实体解析与验证服务。

该服务负责把图谱中的 EntityNode 映射到可引用的现实资料，并给出
verified / ambiguous / unverified / unsupported 四类状态。没有足够上下文的
抽象节点不会进入正式真实画像生成流程。
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from openai import OpenAI

from ..config import Config
from ..utils.llm_routing import clamp_concurrency
from ..utils.logger import get_logger
from .zep_entity_reader import EntityNode


logger = get_logger("mirofish.real_entity_resolver")


VERIFIED = "verified"
AMBIGUOUS = "ambiguous"
UNVERIFIED = "unverified"
UNSUPPORTED = "unsupported"


@dataclass
class RealEntitySource:
    """真实资料来源。"""

    title: str
    url: str
    snippet: str = ""
    site_name: str = ""
    published_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "site_name": self.site_name,
            "published_at": self.published_at,
        }


@dataclass
class ResolvedRealEntity:
    """实体真实身份解析结果。"""

    entity_uuid: str
    entity_name: str
    entity_type: str
    verification_status: str
    info_confidence: float = 0.0
    info_sources: List[Dict[str, Any]] = field(default_factory=list)
    source_citations: List[Dict[str, Any]] = field(default_factory=list)
    real_identity_summary: str = ""
    verified_facts: List[str] = field(default_factory=list)
    skip_reason: str = ""
    raw_query: str = ""

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VERIFIED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_uuid": self.entity_uuid,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type,
            "verification_status": self.verification_status,
            "info_confidence": self.info_confidence,
            "info_sources": self.info_sources,
            "source_citations": self.source_citations,
            "real_identity_summary": self.real_identity_summary,
            "verified_facts": self.verified_facts,
            "skip_reason": self.skip_reason,
            "raw_query": self.raw_query,
        }


class RealEntityResolver:
    """基于 LLM 联网搜索对图谱实体进行真实性验证。"""

    ABSTRACT_TYPES = {
        "entity",
        "node",
        "topic",
        "theme",
        "concept",
        "event",
        "issue",
        "keyword",
        "unknown",
    }
    GROUP_AGENT_TYPES = {
        "group",
        "community",
        "publicgroup",
        "socialgroup",
        "citizengroup",
        "netizencommunity",
        "audiencegroup",
    }
    ORGANIZATION_TYPES = {
        "university",
        "governmentagency",
        "organization",
        "ngo",
        "mediaoutlet",
        "company",
        "institution",
        "government",
        "agency",
        "regulatoryagency",
        "distributionplatform",
        "socialmediaplatform",
        "platform",
    }
    PERSON_TYPES = {
        "person",
        "publicfigure",
        "expert",
        "faculty",
        "official",
        "journalist",
        "activist",
        "celebrity",
        "star",
        "actor",
        "actress",
        "singer",
        "artist",
        "influencer",
        "kol",
        "keyopinionleader",
        "entrepreneur",
        "executive",
        "ceo",
        "founder",
        "engineer",
        "scientist",
        "researcher",
        "technologist",
        "techperson",
        "farmer",
        "student",
        "alumni",
        "professor",
    }
    ORG_NAME_KEYWORDS = {
        "公安",
        "公安局",
        "分局",
        "派出所",
        "法院",
        "检察院",
        "政府",
        "委员会",
        "大学",
        "学院",
        "学校",
        "公司",
        "集团",
        "机构",
        "组织",
        "协会",
        "媒体",
        "日报",
        "新闻网",
        "平台",
        "中心",
        "部门",
        "支队",
        "大队",
        "办公室",
    }
    ORG_IDENTITY_KEYWORDS = {
        "公安机关",
        "公安局",
        "分局",
        "派出所",
        "检察院",
        "法院",
        "政府部门",
        "官方账号",
        "官方发布",
        "机构正式名称",
        "机构性质",
        "主要职能",
        "辖区",
        "办案程序",
        "编辑团队",
        "新闻发布会",
    }

    def __init__(
        self,
        search_service: Optional[Any] = None,
        min_source_count: int = 1,
        allow_group_agents: bool = True,
        llm_web_search_client: Optional[Any] = None,
        batch_size: Optional[int] = None,
        concurrency: Optional[int] = None,
    ):
        self.min_source_count = max(1, int(min_source_count or 1))
        self.allow_group_agents = allow_group_agents
        self.batch_size = max(1, int(batch_size or Config.REAL_ENTITY_BATCH_SIZE or 1))
        self.concurrency = clamp_concurrency(
            concurrency,
            Config.REAL_ENTITY_RESOLVE_CONCURRENCY,
            maximum=8,
        )
        # 真实资料验证默认只使用 LLM 联网；search_service 仅保留给测试或显式离线注入。
        self.search_service = search_service
        self.llm_web_search_client = llm_web_search_client

    def resolve_entities(self, entities: Sequence[EntityNode]) -> List[ResolvedRealEntity]:
        """批量解析实体，保持输入顺序。"""
        if not entities:
            return []

        resolved: List[Optional[ResolvedRealEntity]] = [None] * len(entities)
        searchable_items: List[Tuple[int, EntityNode, str, str]] = []

        for index, entity in enumerate(entities):
            entity_type = entity.get_entity_type() or "Entity"
            raw_query = self._build_search_query(entity, entity_type)
            unsupported_reason = self._unsupported_reason(entity, entity_type)
            if unsupported_reason:
                resolved[index] = self._result(
                    entity,
                    entity_type,
                    UNSUPPORTED,
                    skip_reason=unsupported_reason,
                    raw_query=raw_query,
                )
                continue
            searchable_items.append((index, entity, entity_type, raw_query))

        if not searchable_items:
            return [item for item in resolved if item is not None]

        if len(searchable_items) == 1:
            for index, entity, entity_type, raw_query in searchable_items:
                sources = self._search_with_llm_web(entity, entity_type, raw_query)
                resolved[index] = self._resolve_from_sources(entity, entity_type, raw_query, sources)
            return [item for item in resolved if item is not None]

        batches = [
            searchable_items[start:start + self.batch_size]
            for start in range(0, len(searchable_items), self.batch_size)
        ]

        def resolve_batch(batch: Sequence[Tuple[int, EntityNode, str, str]]) -> List[Tuple[int, ResolvedRealEntity]]:
            batch_sources = self._search_with_llm_web_batch(batch)
            batch_results = []
            for index, entity, entity_type, raw_query in batch:
                sources = batch_sources.get(entity.uuid)
                if sources is None:
                    logger.info("LLM批量联网查询缺少实体结果，回退单实体查询: entity=%s", entity.name)
                    sources = self._search_with_llm_web(entity, entity_type, raw_query)
                batch_results.append(
                    (index, self._resolve_from_sources(entity, entity_type, raw_query, sources))
                )
            return batch_results

        if self.concurrency <= 1 or len(batches) == 1 or self.llm_web_search_client is not None:
            for batch in batches:
                for index, result in resolve_batch(batch):
                    resolved[index] = result
        else:
            with ThreadPoolExecutor(max_workers=min(self.concurrency, len(batches))) as executor:
                future_to_batch = {executor.submit(resolve_batch, batch): batch for batch in batches}
                for future in as_completed(future_to_batch):
                    for index, result in future.result():
                        resolved[index] = result

        return [item for item in resolved if item is not None]

    def resolve_entity(self, entity: EntityNode) -> ResolvedRealEntity:
        """解析单个实体。"""
        entity_type = entity.get_entity_type() or "Entity"
        raw_query = self._build_search_query(entity, entity_type)

        unsupported_reason = self._unsupported_reason(entity, entity_type)
        if unsupported_reason:
            return self._result(entity, entity_type, UNSUPPORTED, skip_reason=unsupported_reason, raw_query=raw_query)

        sources = self._search_with_llm_web(entity, entity_type, raw_query)
        return self._resolve_from_sources(entity, entity_type, raw_query, sources)

    def _resolve_from_sources(
        self,
        entity: EntityNode,
        entity_type: str,
        raw_query: str,
        sources: Sequence[RealEntitySource],
    ) -> ResolvedRealEntity:
        """根据联网来源判定实体真实性状态。"""
        search_error = ""
        sources = list(sources or [])
        matched_sources = self._filter_matching_sources(entity, entity_type, sources)
        if len(matched_sources) < self.min_source_count:
            try:
                if self.search_service:
                    raw_results = self._call_search_service(raw_query)
                    injected_sources = self._normalize_sources(raw_results)
                    if injected_sources:
                        sources = self._merge_sources(sources, injected_sources)
                        matched_sources = self._filter_matching_sources(entity, entity_type, sources)
            except Exception as exc:
                logger.warning(f"真实实体显式搜索服务调用失败: entity={entity.name}, error={exc}")
                search_error = f"显式搜索服务调用失败: {exc}"

        citations = [source.to_dict() for source in matched_sources]

        if len(matched_sources) < self.min_source_count:
            status = UNVERIFIED if sources else UNSUPPORTED
            if sources:
                reason = f"可引用来源不足，需要{self.min_source_count}条，实际{len(matched_sources)}条"
            elif search_error:
                reason = f"LLM联网查询未返回可引用来源，且{search_error}"
            else:
                reason = "LLM联网查询未返回可引用来源"
            return self._result(
                entity,
                entity_type,
                status,
                info_sources=[source.to_dict() for source in sources],
                source_citations=citations,
                skip_reason=reason,
                raw_query=raw_query,
            )

        if self._looks_ambiguous(entity.name, matched_sources):
            return self._result(
                entity,
                entity_type,
                AMBIGUOUS,
                info_confidence=0.45,
                info_sources=[source.to_dict() for source in sources],
                source_citations=citations,
                real_identity_summary=self._build_summary(entity, matched_sources),
                verified_facts=self._build_verified_facts(matched_sources),
                skip_reason="搜索结果可能指向多个同名对象",
                raw_query=raw_query,
            )

        confidence = min(0.95, 0.55 + 0.15 * len(matched_sources))
        return self._result(
            entity,
            entity_type,
            VERIFIED,
            info_confidence=confidence,
            info_sources=[source.to_dict() for source in sources],
            source_citations=citations,
            real_identity_summary=self._build_summary(entity, matched_sources),
            verified_facts=self._build_verified_facts(matched_sources),
            raw_query=raw_query,
        )

    def _unsupported_reason(self, entity: EntityNode, entity_type: str) -> str:
        entity_type_lower = entity_type.lower()
        labels = [label.lower() for label in entity.labels or []]

        if entity_type_lower in self.GROUP_AGENT_TYPES and not self.allow_group_agents:
            return "群体实体未启用 allow_group_agents"

        is_named_organization = entity_type_lower in self.ORGANIZATION_TYPES and bool((entity.name or "").strip())
        has_specific_label = any(label not in {"entity", "node"} for label in labels)
        has_context = self._has_sufficient_context(entity)

        if is_named_organization:
            return ""

        if not has_specific_label and not has_context:
            return "默认 Entity 节点缺少足够上下文"

        if entity_type_lower in self.ABSTRACT_TYPES and not has_context:
            return "抽象实体缺少可验证上下文"

        if not entity.name or not entity.name.strip():
            return "实体名称为空，无法检索"

        return ""

    def _has_sufficient_context(self, entity: EntityNode) -> bool:
        summary = (entity.summary or "").strip()
        attrs = entity.attributes or {}
        edges = entity.related_edges or []
        nodes = entity.related_nodes or []

        meaningful_attrs = [value for value in attrs.values() if value and str(value).strip()]
        meaningful_edges = [edge for edge in edges if (edge.get("fact") or edge.get("name") or edge.get("edge_name"))]

        return len(summary) >= 24 or len(meaningful_attrs) >= 2 or len(meaningful_edges) >= 1 or len(nodes) >= 1

    def _build_search_query(self, entity: EntityNode, entity_type: str) -> str:
        parts = [entity.name.strip(), entity_type]
        if entity.summary:
            parts.append(entity.summary[:120])
        return " ".join(part for part in parts if part).strip()

    def _call_search_service(self, query: str) -> Any:
        limit = max(self.min_source_count * 4, 5)
        for method_name in ("search", "web_search", "search_web", "run"):
            method = getattr(self.search_service, method_name, None)
            if not callable(method):
                continue
            try:
                return method(query=query, count=limit)
            except TypeError:
                try:
                    return method(query, limit=limit)
                except TypeError:
                    return method(query)
        raise AttributeError("搜索服务未提供 search/web_search/search_web/run 方法")

    def _search_with_llm_web(
        self,
        entity: EntityNode,
        entity_type: str,
        query: str,
    ) -> List[RealEntitySource]:
        """使用 LLM 联网搜索获取可引用真实来源。"""
        if not Config.LLM_WEB_SEARCH_API_KEY:
            logger.warning("LLM_WEB_SEARCH_API_KEY/LLM_API_KEY 未配置，跳过LLM联网查询")
            return []

        try:
            client = self.llm_web_search_client or OpenAI(
                api_key=Config.LLM_WEB_SEARCH_API_KEY,
                base_url=Config.LLM_WEB_SEARCH_BASE_URL,
            )
            prompt = self._build_llm_web_search_prompt(entity, entity_type, query)
            response = client.chat.completions.create(
                model=Config.LLM_WEB_SEARCH_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是严谨的真实资料检索助手。必须基于联网搜索结果返回可引用来源；"
                            "没有可靠来源时返回空 sources 数组，禁止编造事实或链接。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                extra_body={
                    "enable_search": True,
                    "search_options": {
                        "forced_search": True,
                        "search_strategy": Config.LLM_WEB_SEARCH_STRATEGY,
                    },
                },
            )
            raw_text = self._extract_response_text(response)
            sources = self._normalize_sources({"results": self._parse_llm_sources(raw_text)})
            sources = self._validate_llm_sources(sources)
            if sources:
                logger.info("LLM联网查询完成: entity=%s, sources=%s", entity.name, len(sources))
            return sources
        except Exception as exc:
            logger.warning("LLM联网查询失败: entity=%s, error=%s", entity.name, exc)
            return []

    def _search_with_llm_web_batch(
        self,
        batch_items: Sequence[Tuple[int, EntityNode, str, str]],
    ) -> Dict[str, List[RealEntitySource]]:
        """批量使用 LLM 联网搜索获取可引用真实来源。"""
        if not batch_items:
            return {}
        if not Config.LLM_WEB_SEARCH_API_KEY:
            logger.warning("LLM_WEB_SEARCH_API_KEY/LLM_API_KEY 未配置，跳过LLM批量联网查询")
            return {entity.uuid: [] for _, entity, _, _ in batch_items}

        try:
            client = self.llm_web_search_client or OpenAI(
                api_key=Config.LLM_WEB_SEARCH_API_KEY,
                base_url=Config.LLM_WEB_SEARCH_BASE_URL,
            )
            prompt = self._build_llm_web_search_batch_prompt(batch_items)
            response = client.chat.completions.create(
                model=Config.LLM_WEB_SEARCH_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是严谨的真实资料检索助手。必须基于联网搜索结果逐个验证实体，"
                            "每个实体只返回可公开引用的来源；没有可靠来源时返回空 sources 数组，"
                            "禁止编造事实或链接。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                extra_body={
                    "enable_search": True,
                    "search_options": {
                        "forced_search": True,
                        "search_strategy": Config.LLM_WEB_SEARCH_STRATEGY,
                    },
                },
            )
            raw_text = self._extract_response_text(response)
            parsed_items = self._parse_llm_batch_sources(raw_text)
            requested_uuids = {entity.uuid for _, entity, _, _ in batch_items}
            uuid_by_name = {
                entity.name.strip(): entity.uuid
                for _, entity, _, _ in batch_items
                if entity.name and entity.name.strip()
            }
            uuid_by_lower_name = {name.lower(): uuid for name, uuid in uuid_by_name.items()}

            sources_by_uuid: Dict[str, List[RealEntitySource]] = {}
            for item in parsed_items:
                entity_uuid = str(item.get("entity_uuid") or "").strip()
                entity_name = str(item.get("entity_name") or "").strip()
                if entity_uuid not in requested_uuids:
                    entity_uuid = uuid_by_name.get(entity_name) or uuid_by_lower_name.get(entity_name.lower(), "")
                if not entity_uuid or entity_uuid not in requested_uuids:
                    continue
                sources = self._normalize_sources({"results": item.get("sources") or []})
                sources_by_uuid[entity_uuid] = self._validate_llm_sources(sources)

            total_sources = sum(len(sources) for sources in sources_by_uuid.values())
            logger.info(
                "LLM批量联网查询完成: requested=%s, returned=%s, sources=%s",
                len(batch_items),
                len(sources_by_uuid),
                total_sources,
            )
            return sources_by_uuid
        except Exception as exc:
            logger.warning("LLM批量联网查询失败: requested=%s, error=%s", len(batch_items), exc)
            return {}

    def _build_llm_web_search_prompt(self, entity: EntityNode, entity_type: str, query: str) -> str:
        context_parts = []
        if entity.summary:
            context_parts.append(f"图谱摘要: {entity.summary[:500]}")
        if entity.attributes:
            context_parts.append(f"图谱属性: {entity.attributes}")
        if entity.related_edges:
            facts = [edge.get("fact") or edge.get("edge_name") for edge in entity.related_edges[:8]]
            facts = [fact for fact in facts if fact]
            if facts:
                context_parts.append("图谱关系: " + "；".join(facts))
        context = "\n".join(context_parts) or "无额外图谱上下文"
        return (
            "请联网查询并验证下面图谱实体是否对应真实人物、机构或可验证群体。"
            "只返回可以公开引用的真实来源，不要编造来源。"
            "如果无法找到可靠来源，返回空 sources 数组。\n\n"
            f"实体名称: {entity.name}\n"
            f"实体类型: {entity_type}\n"
            f"检索关键词: {query}\n"
            f"{context}\n\n"
            "返回 JSON：{\"sources\":[{\"title\":\"...\",\"url\":\"https://...\","
            "\"snippet\":\"能证明实体身份或事件关联的简短事实\",\"site_name\":\"...\","
            "\"published_at\":\"...\"}]}"
        )

    def _build_llm_web_search_batch_prompt(
        self,
        batch_items: Sequence[Tuple[int, EntityNode, str, str]],
    ) -> str:
        entity_lines = []
        for _, entity, entity_type, query in batch_items:
            context_parts = []
            if entity.summary:
                context_parts.append(f"摘要: {entity.summary[:240]}")
            if entity.attributes:
                context_parts.append(f"属性: {str(entity.attributes)[:240]}")
            if entity.related_edges:
                facts = [edge.get("fact") or edge.get("edge_name") for edge in entity.related_edges[:4]]
                facts = [fact for fact in facts if fact]
                if facts:
                    context_parts.append("关系: " + "；".join(facts)[:240])
            context = " | ".join(context_parts) or "无额外图谱上下文"
            entity_lines.append(
                (
                    f"- entity_uuid: {entity.uuid}\n"
                    f"  entity_name: {entity.name}\n"
                    f"  entity_type: {entity_type}\n"
                    f"  query: {query}\n"
                    f"  context: {context}"
                )
            )

        return (
            "请对下面多个图谱实体逐个联网查询并验证其是否对应真实人物、机构或可验证群体。"
            "每个实体都必须出现在返回 JSON 的 entities 数组中，保持原 entity_uuid。"
            "只返回可以公开引用的真实来源，不要编造来源；如果某个实体无法找到可靠来源，"
            "该实体返回空 sources 数组。\n\n"
            "待验证实体:\n"
            + "\n".join(entity_lines)
            + "\n\n返回 JSON：{\"entities\":[{\"entity_uuid\":\"...\",\"entity_name\":\"...\","
            "\"sources\":[{\"title\":\"...\",\"url\":\"https://...\","
            "\"snippet\":\"能证明实体身份或事件关联的简短事实\",\"site_name\":\"...\","
            "\"published_at\":\"...\"}]}]}"
        )

    def _extract_response_text(self, response: Any) -> str:
        choices = getattr(response, "choices", []) or []
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
        return getattr(response, "output_text", "") or ""

    def _parse_llm_sources(self, raw_text: str) -> List[Dict[str, Any]]:
        data = self._load_json_object(raw_text)
        sources = data.get("sources") if isinstance(data, dict) else None
        return sources if isinstance(sources, list) else []

    def _parse_llm_batch_sources(self, raw_text: str) -> List[Dict[str, Any]]:
        data = self._load_json_object(raw_text)
        if not isinstance(data, dict):
            return []

        entities = data.get("entities") or data.get("results") or data.get("entity_results")
        if isinstance(entities, dict):
            return [
                {
                    "entity_uuid": str(entity_uuid),
                    "entity_name": "",
                    "sources": sources if isinstance(sources, list) else [],
                }
                for entity_uuid, sources in entities.items()
            ]
        if not isinstance(entities, list):
            return []

        parsed_items = []
        for item in entities:
            if not isinstance(item, dict):
                continue
            sources = item.get("sources") if isinstance(item.get("sources"), list) else []
            parsed_items.append(
                {
                    "entity_uuid": item.get("entity_uuid") or item.get("uuid") or item.get("entity_id") or item.get("id") or "",
                    "entity_name": item.get("entity_name") or item.get("name") or "",
                    "sources": sources,
                }
            )
        return parsed_items

    def _load_json_object(self, raw_text: str) -> Dict[str, Any]:
        import json

        if not raw_text:
            return {}
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", raw_text)
            if not match:
                return {}
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return {}
        return data if isinstance(data, dict) else {}

    def _merge_sources(
        self,
        primary: Sequence[RealEntitySource],
        fallback: Sequence[RealEntitySource],
    ) -> List[RealEntitySource]:
        merged = []
        seen_urls = set()
        for source in list(primary) + list(fallback):
            key = source.url or f"{source.title}:{source.snippet}"
            if key in seen_urls:
                continue
            seen_urls.add(key)
            merged.append(source)
        return merged

    def _validate_llm_sources(self, sources: List[RealEntitySource]) -> List[RealEntitySource]:
        if not sources or not Config.LLM_WEB_SEARCH_VALIDATE_LINKS:
            return sources
        validated = []
        for source in sources:
            parsed_url = urlparse(source.url)
            if parsed_url.scheme in {"http", "https"} and parsed_url.netloc:
                validated.append(source)
            else:
                logger.info("过滤LLM联网来源：URL 为空或非法 title=%s", source.title)
        return validated

    def _normalize_sources(self, raw_results: Any) -> List[RealEntitySource]:
        items = self._extract_result_items(raw_results)
        sources: List[RealEntitySource] = []
        seen_urls = set()

        for item in items:
            if hasattr(item, "to_dict") and callable(item.to_dict):
                item = item.to_dict()
            elif not isinstance(item, dict):
                item = {
                    "title": getattr(item, "title", ""),
                    "url": getattr(item, "url", ""),
                    "snippet": getattr(item, "snippet", ""),
                    "summary": getattr(item, "summary", ""),
                    "site_name": getattr(item, "site_name", ""),
                    "published_at": getattr(item, "published_at", "") or getattr(item, "date_published", ""),
                }
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            url = str(item.get("url") or item.get("link") or item.get("displayLink") or "").strip()
            snippet = str(item.get("snippet") or item.get("summary") or item.get("content") or "").strip()
            site_name = str(item.get("site_name") or item.get("siteName") or item.get("source") or "").strip()
            published_at = str(
                item.get("published_at")
                or item.get("date")
                or item.get("publishedTime")
                or item.get("date_published")
                or item.get("datePublished")
                or ""
            ).strip()

            if not title and not snippet:
                continue
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            sources.append(
                RealEntitySource(
                    title=title,
                    url=url,
                    snippet=snippet,
                    site_name=site_name,
                    published_at=published_at,
                )
            )

        return sources

    def _extract_result_items(self, raw_results: Any) -> List[Dict[str, Any]]:
        if raw_results is None:
            return []
        if isinstance(raw_results, list):
            return raw_results
        if not isinstance(raw_results, dict):
            return []

        candidates = [
            raw_results.get("results"),
            raw_results.get("items"),
            raw_results.get("webPages", {}).get("value") if isinstance(raw_results.get("webPages"), dict) else None,
            raw_results.get("data", {}).get("webPages", {}).get("value") if isinstance(raw_results.get("data"), dict) else None,
            raw_results.get("data", {}).get("results") if isinstance(raw_results.get("data"), dict) else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, list):
                return candidate
        return []

    def _filter_matching_sources(
        self,
        entity: EntityNode,
        entity_type: str,
        sources: Sequence[RealEntitySource],
    ) -> List[RealEntitySource]:
        entity_name = entity.name
        name_tokens = self._tokens(entity_name)
        if not name_tokens:
            return []

        matched = []
        for source in sources:
            haystack = f"{source.title} {source.snippet}".lower()
            normalized_name = entity_name.lower().strip()
            if normalized_name and normalized_name in haystack:
                if self._source_matches_expected_identity(entity, entity_type, source):
                    matched.append(source)
            elif all(token in haystack for token in name_tokens):
                if self._source_matches_expected_identity(entity, entity_type, source):
                    matched.append(source)
            elif len(name_tokens) >= 2 and sum(1 for token in name_tokens if token in haystack) >= 2:
                if self._source_matches_expected_identity(entity, entity_type, source):
                    matched.append(source)
        return matched

    def _source_matches_expected_identity(
        self,
        entity: EntityNode,
        entity_type: str,
        source: RealEntitySource,
    ) -> bool:
        """校验来源描述的主体类别，避免把“相关机构资料”误贴到人物账号上。"""
        category = self._infer_identity_category(entity, entity_type)
        if category != "person":
            return True

        text = f"{source.title} {source.snippet}".strip()
        if not text:
            return False

        name = (entity.name or "").strip()
        if not name or name not in text:
            return False

        # 英文或非中文姓名缺少稳定句式，保持原有名称命中逻辑，只拦截明显机构身份。
        if not self._looks_like_chinese_person_name(name):
            return not self._has_org_identity_text(text)

        has_person_anchor = self._has_person_anchor(name, text)
        name_only_as_case = bool(re.search(fr"{re.escape(name)}\s*(案|案件|事件|专案)", text))
        has_org_identity = self._has_org_identity_text(text)

        if has_person_anchor:
            return True

        if name_only_as_case or has_org_identity:
            logger.info(
                "过滤主体不一致来源: entity=%s, expected=person, title=%s",
                name,
                source.title,
            )
            return False

        return True

    def _infer_identity_category(self, entity: EntityNode, entity_type: str) -> str:
        type_lower = (entity_type or "").lower()
        labels = {label.lower() for label in entity.labels or []}
        name = (entity.name or "").strip()

        if self._looks_like_chinese_person_name(name):
            return "person"
        if type_lower in self.PERSON_TYPES or labels & self.PERSON_TYPES:
            return "person"
        if type_lower in self.ORGANIZATION_TYPES or labels & self.ORGANIZATION_TYPES:
            return "organization"
        if type_lower in self.GROUP_AGENT_TYPES or labels & self.GROUP_AGENT_TYPES:
            return "group"
        return "unknown"

    def _looks_like_chinese_person_name(self, name: str) -> bool:
        value = (name or "").strip()
        if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value):
            return False
        if any(keyword in value for keyword in self.ORG_NAME_KEYWORDS):
            return False
        if value.endswith(("案", "事件", "平台", "官方", "通报", "警方")):
            return False
        return True

    def _has_person_anchor(self, name: str, text: str) -> bool:
        escaped = re.escape(name)
        patterns = [
            fr"被告人\s*{escaped}",
            fr"犯罪嫌疑人\s*{escaped}",
            fr"嫌疑人\s*{escaped}",
            fr"当事人\s*{escaped}",
            fr"丈夫\s*{escaped}",
            fr"妻子\s*{escaped}",
            fr"凶手\s*{escaped}",
            fr"死刑犯\s*{escaped}",
            fr"{escaped}\s*(?:被|因|于|将|向|承认|交代|供述|杀害|杀妻|分尸|获|一审|二审|执行|伏法|死亡|出生|系|为|是)",
            fr"{escaped}\s*[，,]\s*(?:男|女)",
        ]
        return any(re.search(pattern, text) for pattern in patterns)

    def _has_org_identity_text(self, text: str) -> bool:
        if not text:
            return False
        return any(keyword in text for keyword in self.ORG_IDENTITY_KEYWORDS)

    def _looks_ambiguous(self, entity_name: str, sources: Sequence[RealEntitySource]) -> bool:
        name = entity_name.lower().strip()
        titles_without_name = 0
        for source in sources:
            title = source.title.lower()
            if name not in title and titles_without_name >= 1:
                return True
            if name not in title:
                titles_without_name += 1
        return False

    def _build_summary(self, entity: EntityNode, sources: Sequence[RealEntitySource]) -> str:
        snippets = [source.snippet for source in sources if source.snippet]
        if snippets:
            summary = " ".join(snippets[:2])
        else:
            summary = entity.summary or ""
        return summary[:600].strip()

    def _build_verified_facts(self, sources: Sequence[RealEntitySource]) -> List[str]:
        facts = []
        for source in sources:
            text = source.snippet or source.title
            if text:
                facts.append(text[:240])
        return facts

    def _tokens(self, value: str) -> List[str]:
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", value.lower())
        return [
            token for token in tokens
            if len(token) > 1 or re.search(r"[\u4e00-\u9fff]", token)
        ]

    def _result(
        self,
        entity: EntityNode,
        entity_type: str,
        status: str,
        info_confidence: float = 0.0,
        info_sources: Optional[List[Dict[str, Any]]] = None,
        source_citations: Optional[List[Dict[str, Any]]] = None,
        real_identity_summary: str = "",
        verified_facts: Optional[List[str]] = None,
        skip_reason: str = "",
        raw_query: str = "",
    ) -> ResolvedRealEntity:
        return ResolvedRealEntity(
            entity_uuid=entity.uuid,
            entity_name=entity.name,
            entity_type=entity_type,
            verification_status=status,
            info_confidence=info_confidence,
            info_sources=info_sources or [],
            source_citations=source_citations or [],
            real_identity_summary=real_identity_summary,
            verified_facts=verified_facts or [],
            skip_reason=skip_reason,
            raw_query=raw_query,
        )
