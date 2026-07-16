"""
地点实体过滤规则。

图谱中的实体用于社媒舆论模拟和人设 Agent 构建，纯地点、地址、区域等
不能独立发声的节点不应进入本体、前端展示或 Agent 构建链路。
受害人、嫌疑人、当事人等核心人物即使摘要中包含居住地或案发地，也不能被误判为地点。
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


GENERIC_NODE_LABELS = {"Entity", "Node"}

LOCATION_ENTITY_TYPE_NAMES = {
    "Address",
    "AdministrativeArea",
    "AdministrativeRegion",
    "Area",
    "City",
    "Country",
    "County",
    "District",
    "GeoLocation",
    "GeographicLocation",
    "Landmark",
    "Location",
    "Municipality",
    "Neighborhood",
    "Place",
    "Province",
    "Region",
    "Site",
    "State",
    "Street",
    "Territory",
    "Town",
    "Venue",
    "Village",
    "Zone",
    "区域",
    "地址",
    "地点",
    "地标",
    "地区",
    "城市",
    "场所",
    "位置",
    "省份",
    "行政区",
}

LOCATION_ENTITY_TYPE_TOKENS = {
    "address",
    "area",
    "city",
    "country",
    "county",
    "district",
    "geo",
    "geographic",
    "geolocation",
    "landmark",
    "location",
    "municipality",
    "neighborhood",
    "place",
    "province",
    "region",
    "site",
    "state",
    "street",
    "territory",
    "town",
    "venue",
    "village",
    "zone",
}

LOCATION_ENTITY_TYPE_KEYWORDS = {
    "位置",
    "地址",
    "地点",
    "地标",
    "地理",
    "地区",
    "城市",
    "场所",
    "省份",
    "区域",
    "行政区",
}

LOCATION_NAME_SUFFIXES = (
    "自治区",
    "特别行政区",
    "自治州",
    "街道",
    "大道",
    "广场",
    "机场",
    "车站",
    "港口",
    "公园",
    "省",
    "市",
    "县",
    "区",
    "镇",
    "乡",
    "村",
    "州",
    "国",
    "路",
    "街",
    "山",
    "河",
    "湖",
    "海",
    "湾",
    "岛",
)

LOCATION_NAME_KEYWORDS = {
    "北苑",
    "北园",
    "菜场",
    "车站",
    "东苑",
    "东园",
    "服务区",
    "公园",
    "机场",
    "南苑",
    "南园",
    "西苑",
    "西园",
    "小区",
}

CHINESE_ADMIN_LOCATION_NAMES = {
    "中国",
    "北京",
    "天津",
    "上海",
    "重庆",
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
    "内蒙古",
    "香港",
    "澳门",
    "台湾",
    "杭州",
    "宁波",
    "温州",
    "绍兴",
    "嘉兴",
    "湖州",
    "金华",
    "衢州",
    "台州",
    "丽水",
    "舟山",
    "江干",
    "上城",
    "拱墅",
    "西湖",
    "滨江",
    "萧山",
    "余杭",
    "临平",
    "钱塘",
    "富阳",
    "临安",
    "三堡",
}

KNOWN_MEDIA_PLATFORM_NAMES = {
    "bilibili",
    "facebook",
    "instagram",
    "kuaishou",
    "reddit",
    "tiktok",
    "twitter",
    "weibo",
    "wechat",
    "youtube",
    "小红书",
    "快手",
    "抖音",
    "微博",
    "微信",
    "知乎",
    "豆瓣",
    "哔哩哔哩",
    "b站",
}
NORMALIZED_MEDIA_PLATFORM_NAMES = {
    item.casefold().replace(" ", "").replace("_", "").replace("-", "")
    for item in KNOWN_MEDIA_PLATFORM_NAMES
}

SPEAKING_ACTOR_TYPE_NAMES = {
    "Agency",
    "Association",
    "Brand",
    "Bureau",
    "Club",
    "Committee",
    "Company",
    "Council",
    "Court",
    "Department",
    "Enterprise",
    "Government",
    "GovernmentAgency",
    "Group",
    "Hospital",
    "Institution",
    "MediaOutlet",
    "Ministry",
    "NGO",
    "Organization",
    "Person",
    "Platform",
    "Police",
    "Public",
    "Resident",
    "School",
    "Team",
    "University",
}

SPEAKING_ACTOR_TYPE_TOKENS = {
    "agency",
    "association",
    "brand",
    "bureau",
    "club",
    "committee",
    "company",
    "corporation",
    "council",
    "court",
    "department",
    "enterprise",
    "government",
    "group",
    "hospital",
    "institution",
    "media",
    "ministry",
    "ngo",
    "organization",
    "organisation",
    "person",
    "platform",
    "police",
    "public",
    "resident",
    "school",
    "team",
    "university",
}

SPEAKING_ACTOR_KEYWORDS = {
    "协会",
    "医院",
    "单位",
    "品牌",
    "公众",
    "团队",
    "大学",
    "委员会",
    "媒体",
    "学校",
    "学院",
    "官方",
    "官员",
    "平台",
    "市民",
    "企业",
    "人群",
    "居民",
    "报社",
    "政府",
    "机构",
    "检察院",
    "法院",
    "电视台",
    "组织",
    "公司",
    "警方",
    "公安",
    "部门",
    "集团",
    "受害人",
    "被害人",
    "嫌疑人",
    "犯罪嫌疑人",
    "被告人",
    "当事人",
    "主角",
    "配角",
    "亲属",
    "家属",
    "证人",
    "律师",
    "死者",
    "伤者",
}

STRONG_SPEAKING_ACTOR_KEYWORDS = {
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
    "协会",
    "媒体",
    "日报",
    "新闻网",
    "新闻",
    "电视台",
    "平台",
    "医院",
    "银行",
    "品牌",
    "部门",
    "疾控中心",
    "法律援助中心",
    "政务中心",
    "指挥中心",
    "新闻中心",
    "官方",
    "受害人",
    "被害人",
    "嫌疑人",
    "犯罪嫌疑人",
    "被告人",
    "当事人",
    "主角",
    "配角",
    "亲属",
    "家属",
    "证人",
    "律师",
    "死者",
    "伤者",
}

PERSON_SUBJECT_KEYWORDS = {
    "个人",
    "人物",
    "自然人",
    "受害人",
    "被害人",
    "嫌疑人",
    "犯罪嫌疑人",
    "被告人",
    "当事人",
    "主角",
    "配角",
    "亲属",
    "家属",
    "丈夫",
    "妻子",
    "女儿",
    "儿子",
    "证人",
    "律师",
    "死者",
    "伤者",
}

STRONG_SPEAKING_ACTOR_TYPE_NAMES = {
    "Agency",
    "Association",
    "Brand",
    "Bureau",
    "Committee",
    "Company",
    "Corporation",
    "Council",
    "Court",
    "Department",
    "Enterprise",
    "Government",
    "GovernmentAgency",
    "Hospital",
    "Institution",
    "Media",
    "MediaOutlet",
    "Ministry",
    "NGO",
    "NewsMedia",
    "OrganizationAssociation",
    "Platform",
    "Police",
    "RegulatoryAgency",
    "School",
    "SocialMediaPlatform",
    "University",
    "单位",
    "企业",
    "企业/品牌",
    "公司",
    "协会",
    "医院",
    "媒体",
    "媒体平台",
    "媒体机构",
    "学校",
    "平台",
    "政府",
    "政府机构",
    "机构",
    "监管机构",
    "组织/协会",
    "Victim",
    "Suspect",
    "Defendant",
    "Party",
    "KeyPerson",
    "FamilyMember",
    "Witness",
    "Lawyer",
    "受害人",
    "被害人",
    "嫌疑人",
    "犯罪嫌疑人",
    "被告人",
    "当事人",
    "主角",
    "配角",
    "核心人物",
    "亲属",
    "家属",
    "证人",
    "律师",
    "死者",
    "伤者",
}

GENERIC_SPEAKING_ACTOR_TYPES = {
    "Entity",
    "Group",
    "Node",
    "Organization",
    "Person",
    "个人",
    "个人实体",
    "人",
    "人物",
    "实体",
    "组织",
}

PLACE_CONTEXT_KEYWORDS = {
    "案发地",
    "案发地点",
    "发生地",
    "居住地",
    "住址",
    "地址",
    "地点",
    "小区",
    "百货",
    "化粪池",
}

GRAPHITI_EXCLUDED_LOCATION_ENTITY_TYPES = tuple(sorted(LOCATION_ENTITY_TYPE_NAMES))
NORMALIZED_LOCATION_ENTITY_TYPE_NAMES = {
    re.sub(r"[\s_\-:：/\\]+", "", str(name or "")).casefold()
    for name in LOCATION_ENTITY_TYPE_NAMES
}
NORMALIZED_SPEAKING_ACTOR_TYPE_NAMES = {
    re.sub(r"[\s_\-:：/\\]+", "", str(name or "")).casefold()
    for name in SPEAKING_ACTOR_TYPE_NAMES
}
NORMALIZED_STRONG_SPEAKING_ACTOR_TYPE_NAMES = {
    re.sub(r"[\s_\-:：/\\]+", "", str(name or "")).casefold()
    for name in STRONG_SPEAKING_ACTOR_TYPE_NAMES
}
NORMALIZED_GENERIC_SPEAKING_ACTOR_TYPES = {
    re.sub(r"[\s_\-:：/\\]+", "", str(name or "")).casefold()
    for name in GENERIC_SPEAKING_ACTOR_TYPES
}


def is_speaking_actor_type(type_name: Any) -> bool:
    """判断实体类型是否明显属于可发声主体。"""
    text = str(type_name or "").strip()
    if not text:
        return False

    normalized = _normalize_type_name(text)
    if normalized in NORMALIZED_SPEAKING_ACTOR_TYPE_NAMES:
        return True

    tokens = _identifier_tokens(text)
    if tokens & SPEAKING_ACTOR_TYPE_TOKENS:
        return True

    return any(keyword in text for keyword in SPEAKING_ACTOR_KEYWORDS)


def is_location_entity_type(type_name: Any) -> bool:
    """判断实体类型是否属于纯地点/位置/地址类。"""
    text = str(type_name or "").strip()
    if not text or is_speaking_actor_type(text):
        return False

    normalized = _normalize_type_name(text)
    if normalized in NORMALIZED_LOCATION_ENTITY_TYPE_NAMES:
        return True

    tokens = _identifier_tokens(text)
    if tokens & LOCATION_ENTITY_TYPE_TOKENS:
        return True

    lowered = text.casefold()
    return any(keyword in lowered for keyword in LOCATION_ENTITY_TYPE_KEYWORDS)


def is_location_entity_node(node: Any) -> bool:
    """判断图谱节点是否应作为地点实体过滤掉。"""
    name = str(_get_value(node, "name", "") or "").strip()
    labels = _as_list(_get_value(node, "labels", []))
    custom_labels = [
        str(label).strip()
        for label in labels
        if str(label).strip() and str(label).strip() not in GENERIC_NODE_LABELS
    ]

    if is_known_media_platform_name(name):
        return False

    if _looks_like_person_subject_node(name, node):
        return False

    if _looks_like_physical_place_node(name, node) and not _has_strong_speaking_actor_evidence(name, node):
        return True

    if _is_name_like_location(name) and not _has_strong_speaking_actor_evidence(name, node):
        return True

    attributes = _get_value(node, "attributes", {}) or {}
    attribute_type_values = _iter_attribute_type_values(attributes)

    if any(is_speaking_actor_type(label) for label in custom_labels):
        return False
    if any(is_location_entity_type(label) for label in custom_labels):
        return True

    if any(is_location_entity_type(value) for value in attribute_type_values):
        return True
    if any(is_speaking_actor_type(value) for value in attribute_type_values):
        return False

    if custom_labels:
        return False

    return _is_name_like_location(name)


def filter_location_entities(nodes: List[Any], edges: List[Any]) -> Tuple[List[Any], List[Any]]:
    """过滤地点节点，并移除指向这些节点的边。"""
    if not nodes:
        return nodes, edges

    blocked_uuids = {
        str(_get_value(node, "uuid", "") or "")
        for node in nodes
        if is_location_entity_node(node)
    }
    blocked_uuids.discard("")

    if not blocked_uuids:
        return nodes, edges

    filtered_nodes = [
        node for node in nodes
        if str(_get_value(node, "uuid", "") or "") not in blocked_uuids
    ]
    filtered_edges = [
        edge for edge in (edges or [])
        if str(_get_value(edge, "source_node_uuid", "") or "") not in blocked_uuids
        and str(_get_value(edge, "target_node_uuid", "") or "") not in blocked_uuids
    ]
    return filtered_nodes, filtered_edges


def strip_location_entity_types_from_ontology(ontology: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """从本体定义中移除地点实体类型和引用地点类型的关系端点。"""
    cleaned = copy.deepcopy(ontology or {})
    entity_types = cleaned.get("entity_types") or []
    removed_type_names = {
        entity.get("name")
        for entity in entity_types
        if isinstance(entity, dict) and is_location_entity_type(entity.get("name"))
    }

    cleaned["entity_types"] = [
        entity for entity in entity_types
        if not (
            isinstance(entity, dict)
            and entity.get("name") in removed_type_names
        )
    ]

    cleaned_edges = []
    for edge in cleaned.get("edge_types") or []:
        if not isinstance(edge, dict):
            continue

        source_targets = edge.get("source_targets") or []
        had_source_targets = bool(source_targets)
        edge["source_targets"] = [
            source_target
            for source_target in source_targets
            if not _source_target_has_location_type(source_target, removed_type_names)
        ]

        if had_source_targets and not edge["source_targets"]:
            continue
        cleaned_edges.append(edge)
    cleaned["edge_types"] = cleaned_edges

    return cleaned


def _source_target_has_location_type(source_target: Any, removed_type_names: Iterable[str]) -> bool:
    if not isinstance(source_target, dict):
        return False
    removed = set(removed_type_names)
    source = source_target.get("source")
    target = source_target.get("target")
    return (
        source in removed
        or target in removed
        or is_location_entity_type(source)
        or is_location_entity_type(target)
    )


def _get_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple) or isinstance(value, set):
        return list(value)
    return [value]


def _iter_attribute_type_values(attributes: Dict[str, Any]) -> List[Any]:
    values = []
    for key in ("entity_type", "type", "category", "kind", "label", "labels"):
        value = attributes.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(value)
        elif value:
            values.append(value)
    return values


def _identifier_tokens(value: str) -> set[str]:
    with_spaces = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    with_spaces = re.sub(r"[^A-Za-z0-9]+", " ", with_spaces)
    return {token.casefold() for token in with_spaces.split() if token}


def _normalize_type_name(value: str) -> str:
    return re.sub(r"[\s_\-:：/\\]+", "", str(value or "")).casefold()


def _is_name_like_location(name: str) -> bool:
    if not name or is_known_media_platform_name(name):
        return False
    if name in CHINESE_ADMIN_LOCATION_NAMES:
        return True
    if any(keyword in name for keyword in STRONG_SPEAKING_ACTOR_KEYWORDS):
        return False
    if any(keyword in name for keyword in LOCATION_ENTITY_TYPE_KEYWORDS):
        return True
    if any(keyword in name for keyword in LOCATION_NAME_KEYWORDS):
        return True
    return any(
        name.endswith(suffix) and len(name) >= max(3, len(suffix) + 1)
        for suffix in LOCATION_NAME_SUFFIXES
    )


def is_known_media_platform_name(name: Any) -> bool:
    """判断名称是否是常见社交/内容平台。"""
    value = str(name or "").strip()
    if not value:
        return False
    normalized = value.casefold().replace(" ", "").replace("_", "").replace("-", "")
    return normalized in NORMALIZED_MEDIA_PLATFORM_NAMES


def _has_strong_speaking_actor_evidence(name: str, node: Any) -> bool:
    """判断地点性名称是否也有明确可发声主体证据。"""
    if _has_strong_named_actor_evidence(name):
        return True

    labels = [
        str(label).strip()
        for label in _as_list(_get_value(node, "labels", []))
        if str(label).strip() and str(label).strip() not in GENERIC_NODE_LABELS
    ]
    if any(is_location_entity_type(label) for label in labels):
        return False
    if any(_is_strong_speaking_actor_type(label) for label in labels):
        return True

    attributes = _get_value(node, "attributes", {}) or {}
    type_values = _iter_attribute_type_values(attributes)
    if any(is_location_entity_type(value) for value in type_values):
        return False
    if any(_is_strong_speaking_actor_type(value) for value in type_values):
        return True

    text_parts = [name]
    for value in type_values:
        text_parts.append(str(value))
    summary = str(_get_value(node, "summary", "") or "")
    if summary:
        text_parts.append(summary[:240])

    combined = " ".join(text_parts)
    return any(keyword in combined for keyword in STRONG_SPEAKING_ACTOR_KEYWORDS)


def _has_strong_named_actor_evidence(name: str) -> bool:
    return any(keyword in name for keyword in STRONG_SPEAKING_ACTOR_KEYWORDS)


def _looks_like_person_subject_node(name: str, node: Any) -> bool:
    """保护被误写入地点上下文的人物节点，避免因摘要包含小区/地址被删除。"""
    if not _looks_like_chinese_person_name(name):
        return False

    labels = [
        str(label).strip()
        for label in _as_list(_get_value(node, "labels", []))
        if str(label).strip() and str(label).strip() not in GENERIC_NODE_LABELS
    ]
    attributes = _get_value(node, "attributes", {}) or {}
    type_values = [str(value) for value in _iter_attribute_type_values(attributes)]
    summary = str(_get_value(node, "summary", "") or "")
    combined = " ".join([name, *labels, *type_values, summary[:240]])

    return (
        any(_normalize_type_name(label) in {"person", "个人", "人物", "个人实体", "人"} for label in labels)
        or any(keyword in combined for keyword in PERSON_SUBJECT_KEYWORDS)
    )


def _looks_like_chinese_person_name(name: str) -> bool:
    value = str(name or "").strip()
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value):
        return False
    if len(value) == 4 and not ("·" in value):
        common_compound_surnames = (
            "欧阳",
            "司马",
            "上官",
            "诸葛",
            "东方",
            "尉迟",
            "慕容",
            "长孙",
            "令狐",
            "宇文",
            "司徒",
            "南宫",
        )
        if not value.startswith(common_compound_surnames):
            return False
    if value in CHINESE_ADMIN_LOCATION_NAMES:
        return False
    if any(keyword in value for keyword in LOCATION_NAME_KEYWORDS):
        return False
    if any(keyword in value for keyword in SPEAKING_ACTOR_KEYWORDS):
        return False
    if any(keyword in value for keyword in ("公司", "集团", "法院", "检察", "公安", "媒体", "平台", "日报", "新闻")):
        return False
    return not value.endswith(LOCATION_NAME_SUFFIXES + ("案", "事件", "官方", "警方"))


def _is_strong_speaking_actor_type(type_name: Any) -> bool:
    text = str(type_name or "").strip()
    if not text:
        return False
    normalized = _normalize_type_name(text)
    if normalized in NORMALIZED_GENERIC_SPEAKING_ACTOR_TYPES:
        return False
    if normalized in NORMALIZED_STRONG_SPEAKING_ACTOR_TYPE_NAMES:
        return True
    return is_speaking_actor_type(text) and not is_location_entity_type(text)


def _looks_like_physical_place_node(name: str, node: Any) -> bool:
    """结合摘要/属性判断实体是否是物理地点或场所。"""
    if not name or is_known_media_platform_name(name):
        return False
    if _is_name_like_location(name):
        return True

    attributes = _get_value(node, "attributes", {}) or {}
    summary = str(_get_value(node, "summary", "") or "")
    attr_text = " ".join(
        str(value)
        for value in attributes.values()
        if value is not None and str(value).strip()
    )
    combined = f"{name} {attr_text} {summary[:360]}"
    if not any(keyword in combined for keyword in PLACE_CONTEXT_KEYWORDS):
        return False

    return bool(
        re.search(r"(地址|地点|案发地|案发地点|发生地|居住地|住址).{0,40}(省|市|区|县|路|街|小区|百货|化粪池)", combined)
        or re.search(r"(小区|百货|化粪池)", combined)
    )
