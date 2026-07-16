"""
图谱类型翻译服务

维护实体类型、关系类型和图谱属性键的中英文互译表。翻译表使用 JSON 文件持久化，
用于本体生成、图谱展示和 Agent 人设生成等链路。
"""

import json
import os
import re
import threading
from typing import Any, Dict, Iterable, List, Optional

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger('mirofish.type_translation')


class TypeTranslationService:
    """实体类型/关系类型翻译表服务。"""

    DATA_PATH = os.path.join(os.path.dirname(__file__), '../data/type_translations.json')
    _lock = threading.RLock()
    _cache: Optional[Dict[str, Any]] = None

    @classmethod
    def normalize_key(cls, value: Any) -> str:
        """标准化类型键，兼容 PascalCase、snake_case、空格和连字符。"""
        if value is None:
            return ''
        return str(value).strip().upper().replace(' ', '').replace('-', '').replace('_', '')

    @classmethod
    def normalize_relation_key(cls, value: Any) -> str:
        """关系类型保留下划线，优先匹配 UPPER_SNAKE_CASE。"""
        if value is None:
            return ''
        return str(value).strip().upper().replace(' ', '_').replace('-', '_')

    @classmethod
    def load(cls) -> Dict[str, Any]:
        """加载翻译表。"""
        with cls._lock:
            return cls._load_locked()

    @classmethod
    def get_public_payload(cls) -> Dict[str, Any]:
        """返回前端可直接使用的翻译表。"""
        data = cls.load()
        return {
            "version": data.get("version", 1),
            "entity_types": data.get("entity_types", {}),
            "relation_types": data.get("relation_types", {}),
            "attribute_keys": data.get("attribute_keys", {}),
        }

    @classmethod
    def translate_entity_type(cls, type_name: Any) -> str:
        if not type_name:
            return '未知'
        data = cls.load()
        key = cls.normalize_key(type_name)
        return data.get("entity_types", {}).get(key) or str(type_name)

    @classmethod
    def translate_relation_type(cls, type_name: Any) -> str:
        if not type_name:
            return '关联'
        data = cls.load()
        relation_types = data.get("relation_types", {})
        key = cls.normalize_relation_key(type_name)
        key_no_underscore = cls.normalize_key(type_name)
        return relation_types.get(key) or relation_types.get(key_no_underscore) or str(type_name)

    @classmethod
    def translate_attribute_key(cls, key_name: Any) -> str:
        if not key_name:
            return '属性'
        data = cls.load()
        key = cls.normalize_key(key_name)
        return data.get("attribute_keys", {}).get(key) or str(key_name)

    @classmethod
    def ensure_ontology_translations(cls, ontology: Dict[str, Any]) -> Dict[str, Any]:
        """确保本体中的实体和关系类型都有中文翻译，并把翻译写回本体。"""
        entity_names = [item.get("name") for item in ontology.get("entity_types", []) if item.get("name")]
        relation_names = [item.get("name") for item in ontology.get("edge_types", []) if item.get("name")]
        cls.ensure_translations(entity_names=entity_names, relation_names=relation_names)

        for entity in ontology.get("entity_types", []):
            if entity.get("name"):
                entity["display_name"] = cls.translate_entity_type(entity["name"])

        for edge in ontology.get("edge_types", []):
            if edge.get("name"):
                edge["display_name"] = cls.translate_relation_type(edge["name"])
            for conn in edge.get("source_targets", []) or []:
                if conn.get("source"):
                    conn["source_display_name"] = cls.translate_entity_type(conn["source"])
                if conn.get("target"):
                    conn["target_display_name"] = cls.translate_entity_type(conn["target"])

        return ontology

    @classmethod
    def ensure_graph_data_translations(cls, graph_data: Dict[str, Any]) -> Dict[str, Any]:
        """给图谱数据附加中文展示字段，并补齐翻译表。"""
        entity_names = []
        relation_names = []
        attribute_names = []

        for node in graph_data.get("nodes", []) or []:
            for label in node.get("labels", []) or []:
                if label not in ["Entity", "Node"]:
                    entity_names.append(label)
            attribute_names.extend((node.get("attributes") or {}).keys())

        for edge in graph_data.get("edges", []) or []:
            relation_name = edge.get("name") or edge.get("fact_type")
            if relation_name:
                relation_names.append(relation_name)
            attribute_names.extend((edge.get("attributes") or {}).keys())

        cls.ensure_translations(
            entity_names=entity_names,
            relation_names=relation_names,
            attribute_names=attribute_names,
        )

        for node in graph_data.get("nodes", []) or []:
            node["label_display_names"] = [
                cls.translate_entity_type(label) if label not in ["Entity", "Node"] else label
                for label in node.get("labels", []) or []
            ]
            entity_type = next((label for label in node.get("labels", []) or [] if label not in ["Entity", "Node"]), None)
            if entity_type:
                node["entity_type_display_name"] = cls.translate_entity_type(entity_type)

        for edge in graph_data.get("edges", []) or []:
            relation_name = edge.get("name") or edge.get("fact_type")
            if relation_name:
                edge["display_name"] = cls.translate_relation_type(relation_name)

        graph_data["type_translations"] = cls.get_public_payload()
        return graph_data

    @classmethod
    def ensure_translations(
        cls,
        entity_names: Optional[Iterable[str]] = None,
        relation_names: Optional[Iterable[str]] = None,
        attribute_names: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """补齐缺失翻译。"""
        entity_names = [name for name in (entity_names or []) if name]
        relation_names = [name for name in (relation_names or []) if name]
        attribute_names = [name for name in (attribute_names or []) if name]
        if not entity_names and not relation_names and not attribute_names:
            return cls.load()

        with cls._lock:
            data = cls._load_locked()
            entity_map = data.setdefault("entity_types", {})
            relation_map = data.setdefault("relation_types", {})
            attribute_map = data.setdefault("attribute_keys", {})

            missing_entities = [
                name for name in cls._unique(entity_names)
                if cls.normalize_key(name) not in entity_map
            ]
            missing_relations = [
                name for name in cls._unique(relation_names)
                if cls.normalize_relation_key(name) not in relation_map
                and cls.normalize_key(name) not in relation_map
            ]
            missing_attributes = [
                name for name in cls._unique(attribute_names)
                if cls.normalize_key(name) not in attribute_map
            ]

        if not missing_entities and not missing_relations and not missing_attributes:
            return cls.load()

        generated = cls._generate_translations(missing_entities, missing_relations, missing_attributes)

        with cls._lock:
            data = cls._load_locked()
            entity_map = data.setdefault("entity_types", {})
            relation_map = data.setdefault("relation_types", {})
            attribute_map = data.setdefault("attribute_keys", {})

            for name in missing_entities:
                key = cls.normalize_key(name)
                entity_map[key] = cls._clean_translation(
                    generated.get("entity_types", {}).get(name),
                    fallback=cls._fallback_entity_translation(name),
                )

            for name in missing_relations:
                key = cls.normalize_relation_key(name)
                relation_map[key] = cls._clean_translation(
                    generated.get("relation_types", {}).get(name),
                    fallback=cls._fallback_relation_translation(name),
                )

            for name in missing_attributes:
                key = cls.normalize_key(name)
                attribute_map[key] = cls._clean_translation(
                    generated.get("attribute_keys", {}).get(name),
                    fallback=cls._fallback_attribute_translation(name),
                )

            data["version"] = int(data.get("version", 1)) + 1
            cls._write_locked(data)
            cls._cache = data
            return data

    @classmethod
    def _load_locked(cls) -> Dict[str, Any]:
        if cls._cache is not None:
            return cls._cache
        os.makedirs(os.path.dirname(cls.DATA_PATH), exist_ok=True)
        if not os.path.exists(cls.DATA_PATH):
            cls._cache = {"version": 1, "entity_types": {}, "relation_types": {}}
            cls._write_locked(cls._cache)
            return cls._cache
        with open(cls.DATA_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data.setdefault("version", 1)
        data.setdefault("entity_types", {})
        data.setdefault("relation_types", {})
        data.setdefault("attribute_keys", {})
        cls._cache = data
        return cls._cache

    @classmethod
    def _generate_translations(
        cls,
        entity_names: List[str],
        relation_names: List[str],
        attribute_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """优先用 LLM 翻译，失败时回退到规则翻译。"""
        attribute_names = attribute_names or []
        if not entity_names and not relation_names and not attribute_names:
            return {"entity_types": {}, "relation_types": {}, "attribute_keys": {}}

        try:
            prompt = (
                "请把知识图谱类型名翻译成简洁中文。"
                "实体类型翻译为名词短语，关系类型翻译为动词或关系短语。"
                "属性键翻译为字段名短语，保留原业务含义，例如 orgname 翻译为组织名称。"
                "只返回 JSON，结构为 {\"entity_types\": {原文: 中文}, \"relation_types\": {原文: 中文}, \"attribute_keys\": {原文: 中文}}。\n\n"
                f"实体类型: {entity_names}\n关系类型: {relation_names}\n属性键: {attribute_names}"
            )
            result = LLMClient().chat_json(
                messages=[
                    {"role": "system", "content": "你是知识图谱中英文术语翻译助手，只返回有效 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1200,
            )
            return {
                "entity_types": result.get("entity_types", {}) if isinstance(result, dict) else {},
                "relation_types": result.get("relation_types", {}) if isinstance(result, dict) else {},
                "attribute_keys": result.get("attribute_keys", {}) if isinstance(result, dict) else {},
            }
        except Exception as e:
            logger.warning(f"LLM 生成类型翻译失败，使用规则回退: {e}")
            return {"entity_types": {}, "relation_types": {}, "attribute_keys": {}}

    @classmethod
    def _write_locked(cls, data: Dict[str, Any]) -> None:
        tmp_path = f"{cls.DATA_PATH}.tmp"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
        os.replace(tmp_path, cls.DATA_PATH)

    @classmethod
    def _unique(cls, values: Iterable[str]) -> List[str]:
        seen = set()
        result = []
        for value in values:
            raw = str(value).strip()
            if not raw:
                continue
            key = raw.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(raw)
        return result

    @classmethod
    def _clean_translation(cls, value: Any, fallback: str) -> str:
        text = str(value or '').strip()
        if not text:
            return fallback
        text = re.sub(r'[，。,.;；:：].*$', '', text).strip()
        return text[:24] or fallback

    @classmethod
    def _fallback_entity_translation(cls, name: str) -> str:
        words = cls._split_type_words(name)
        known = {
            "agency": "机构",
            "association": "协会",
            "buyer": "买方",
            "consumer": "消费者",
            "distributor": "经销商",
            "expert": "专家",
            "farmer": "农户",
            "fruit": "水果",
            "government": "政府",
            "industry": "行业",
            "media": "媒体",
            "official": "官员",
            "person": "人",
            "regulatory": "监管",
            "reporter": "记者",
            "site": "场所",
        }
        translated = ''.join(known.get(word.lower(), word) for word in words)
        return translated or name

    @classmethod
    def _fallback_relation_translation(cls, name: str) -> str:
        words = cls._split_type_words(name)
        known = {
            "affiliated": "隶属于",
            "affects": "影响",
            "comments": "评论",
            "for": "",
            "in": "于",
            "located": "位于",
            "on": "",
            "opposes": "反对",
            "regulates": "监管",
            "reports": "报道",
            "responds": "回应",
            "supports": "支持",
            "to": "",
            "works": "工作",
        }
        translated = ''.join(known.get(word.lower(), word) for word in words)
        return translated or name

    @classmethod
    def _fallback_attribute_translation(cls, name: str) -> str:
        words = cls._split_type_words(name)
        known = {
            "age": "年龄",
            "area": "地区",
            "bio": "简介",
            "category": "分类",
            "city": "城市",
            "confidence": "置信度",
            "country": "国家",
            "description": "描述",
            "gender": "性别",
            "info": "信息",
            "name": "名称",
            "occupation": "职业",
            "org": "组织",
            "organization": "组织",
            "person": "人物",
            "profession": "职业",
            "region": "地区",
            "role": "角色",
            "source": "来源",
            "summary": "摘要",
            "type": "类型",
        }
        translated = ''.join(known.get(word.lower(), word) for word in words)
        return translated or name

    @classmethod
    def _split_type_words(cls, name: str) -> List[str]:
        value = str(name or '').replace('_', ' ').replace('-', ' ')
        value = re.sub(r'([A-Za-z]+)([\u4e00-\u9fff]+)', r'\1 \2', value)
        value = re.sub(r'([\u4e00-\u9fff]+)([A-Za-z]+)', r'\1 \2', value)
        value = re.sub(r'([a-z])([A-Z])', r'\1 \2', value)
        return [word for word in value.split() if word]
