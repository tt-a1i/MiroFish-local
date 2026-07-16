"""
Graphiti LLM 结构化输出适配。

Graphiti 负责图谱抽取编排，底层 OpenAI-compatible 模型负责生成结构化 JSON。
不同模型服务对 response_format 和 JSON schema 的支持程度不一致，本模块只处理
“模型响应 -> Graphiti Pydantic schema payload”的兼容转换，不触碰 Neo4j 写入逻辑。
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, get_args, get_origin

from pydantic import BaseModel

logger = logging.getLogger("mirofish.graphiti_llm_adapter")


class GraphitiLLMInvalidJSONError(RuntimeError):
    """Graphiti LLM fallback 返回了无法解析的 JSON。"""


@dataclass(frozen=True)
class _ListNormalizationResult:
    items: List[Any]
    changed: bool


def iter_exception_chain(exc: Exception):
    """遍历异常链，兼容 Graphiti 包装后的上游 OpenAI 异常。"""
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)


def is_response_format_unsupported_error(exc: Exception) -> bool:
    """识别 OpenAI-compatible 服务不支持 response_format 的 400 错误。"""
    for current in iter_exception_chain(exc):
        status_code = getattr(current, "status_code", None)
        text = str(current).lower()
        if status_code == 400 and "response_format" in text and (
            "unavailable" in text
            or "unsupported" in text
            or "not support" in text
            or "not supported" in text
        ):
            return True
        if "response_format" in text and "unavailable" in text:
            return True
    return False


def is_retryable_llm_response_error(exc: Exception) -> bool:
    """识别 LLM 临时返回不可解析内容的错误。"""
    return any(isinstance(current, GraphitiLLMInvalidJSONError) for current in iter_exception_chain(exc))


def ensure_graphiti_json_instruction(args: tuple, kwargs: Dict[str, Any]) -> None:
    """兼容要求 JSON 模式提示词必须包含 json 字样的 OpenAI-compatible 服务。"""
    messages = kwargs.get("messages")
    if messages is None and args:
        messages = args[0]
    if not messages:
        return

    try:
        has_json_instruction = any(
            "json" in ((getattr(message, "content", "") or "").lower())
            for message in messages
        )
    except TypeError:
        return
    if has_json_instruction:
        return

    instruction = "\n\n请只返回有效 JSON（json）内容，不能包含 Markdown 代码块或额外解释。"
    for message in messages:
        if getattr(message, "role", None) == "system":
            message.content = f"{message.content or ''}{instruction}"
            return

    first_message = messages[0]
    first_message.content = f"{getattr(first_message, 'content', '') or ''}{instruction}"


def get_response_model_arg(args: tuple, kwargs: Dict[str, Any]) -> Optional[type[BaseModel]]:
    """兼容 Graphiti generate_response 的关键字和位置参数调用。"""
    response_model = kwargs.get("response_model")
    if response_model is None and len(args) >= 2:
        response_model = args[1]
    if isinstance(response_model, type) and issubclass(response_model, BaseModel):
        return response_model
    return None


def normalize_graphiti_response_model_payload(
    payload: Any,
    response_model: Optional[type[BaseModel]],
) -> Any:
    """
    兼容部分 OpenAI-compatible 服务忽略结构化输出外层对象的情况。

    Graphiti 的抽取 prompt 通常要求返回形如 {"extracted_entities": [...]} 的对象；
    个别模型会直接返回顶层数组，或使用 nodes/relationships 等同义字段。
    对“仅包含一个列表字段”的 Pydantic schema，可以安全地补回外层字段。
    """
    if response_model is None:
        return payload

    model_fields = getattr(response_model, "model_fields", {}) or {}
    list_fields = []
    for field_name, field_info in model_fields.items():
        annotation = getattr(field_info, "annotation", None)
        if get_origin(annotation) in {list, List}:
            list_fields.append(field_name)

    if len(model_fields) != 1 or len(list_fields) != 1:
        return payload

    field_name = list_fields[0]
    items = _extract_list_payload(payload, field_name)
    if not isinstance(items, list):
        return payload

    normalized = _normalize_graphiti_list_items(
        items,
        field_name=field_name,
        response_model=response_model,
    )
    if isinstance(payload, dict) and field_name in payload and not normalized.changed:
        return payload

    if isinstance(payload, list):
        logger.warning(
            "Graphiti LLM 返回顶层数组，已按 response_model=%s 包装为字段 %s",
            getattr(response_model, "__name__", str(response_model)),
            field_name,
        )
    else:
        logger.warning(
            "Graphiti LLM 返回字段名与 schema 不一致，已归一化为字段 %s: response_model=%s",
            field_name,
            getattr(response_model, "__name__", str(response_model)),
        )
    return {field_name: normalized.items}


def parse_graphiti_llm_json_content(content: str) -> Dict[str, Any] | List[Any]:
    """解析不支持 response_format 端点返回的 JSON 内容。"""
    text = (content or "").strip()
    if not text:
        raise GraphitiLLMInvalidJSONError("Graphiti LLM fallback 返回空内容，无法解析 JSON")

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as first_exc:
        candidate = _extract_json_candidate(text)
        if candidate:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        preview = text[:200].replace("\n", "\\n")
        raise GraphitiLLMInvalidJSONError(
            f"Graphiti LLM fallback 返回非 JSON 内容，无法解析: preview={preview!r}"
        ) from first_exc


async def generate_response_without_response_format(
    llm_client: Any,
    *args,
    **kwargs,
) -> Any:
    """
    兼容不支持 response_format 的 OpenAI-compatible LLM。

    graphiti-core 0.25.x 的 OpenAIGenericClient 会无条件传 response_format；
    部分兼容端点会因此返回 400。这里复用同一批 messages/model 参数，
    去掉 response_format 再请求一次，并由本地解析 JSON。
    """
    messages = kwargs.get("messages")
    if messages is None and args:
        messages = args[0]
    if not messages:
        raise ValueError("Graphiti LLM fallback 缺少 messages")

    max_tokens = kwargs.get("max_tokens")
    if max_tokens is None and len(args) >= 3:
        max_tokens = args[2]
    if max_tokens is None:
        max_tokens = getattr(llm_client, "max_tokens", None)

    client = getattr(llm_client, "client", None)
    if client is None:
        raise ValueError("Graphiti LLM fallback 缺少 OpenAI client")

    response = await client.chat.completions.create(
        model=getattr(llm_client, "model", None),
        messages=_to_openai_messages(llm_client, messages),
        temperature=getattr(llm_client, "temperature", 0),
        max_tokens=max_tokens,
    )
    content = response.choices[0].message.content or ""
    return parse_graphiti_llm_json_content(content)


def _extract_list_payload(payload: Any, field_name: str) -> Optional[List[Any]]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    items = payload.get(field_name)
    if isinstance(items, list):
        return items
    return _pick_graphiti_compatible_list(payload, field_name)


def _pick_graphiti_compatible_list(payload: Dict[str, Any], field_name: str) -> Optional[List[Any]]:
    candidate_keys_by_field = {
        "extracted_entities": ("nodes", "entities", "entity_list", "extracted_nodes"),
        "edges": ("relations", "relationships", "extracted_edges", "facts"),
    }
    for key in candidate_keys_by_field.get(field_name, ()):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    list_values = [value for value in payload.values() if isinstance(value, list)]
    if len(list_values) == 1:
        return list_values[0]
    return None


def _normalize_graphiti_list_items(
    items: List[Any],
    *,
    field_name: str,
    response_model: type[BaseModel],
) -> _ListNormalizationResult:
    item_model = _get_single_list_item_model(response_model)
    if item_model is None:
        return _ListNormalizationResult(items=items, changed=False)

    required_fields = set((getattr(item_model, "model_fields", {}) or {}).keys())
    normalized = []
    changed = False
    for item in items:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        next_item = dict(item)
        aliases = _item_aliases_for_field(field_name)
        for target_key, source_keys in aliases.items():
            changed = _copy_first_present(next_item, target_key, source_keys) or changed
        normalized.append(next_item)

    if changed:
        logger.warning(
            "Graphiti LLM 返回列表项字段名与 schema 不一致，已归一化: response_model=%s, field=%s, required=%s",
            getattr(response_model, "__name__", str(response_model)),
            field_name,
            sorted(required_fields),
        )
    return _ListNormalizationResult(items=normalized, changed=changed)


def _item_aliases_for_field(field_name: str) -> Dict[str, tuple[str, ...]]:
    if field_name == "extracted_entities":
        return {
            "name": ("entity_name", "title", "label"),
            "entity_type_id": ("type_id", "entity_type_index"),
        }
    if field_name == "edges":
        return {
            "relation_type": ("relation", "relation_name", "predicate", "type"),
            "source_entity_id": ("source_id", "source", "source_entity", "from"),
            "target_entity_id": ("target_id", "target", "target_entity", "to"),
            "fact": ("description", "summary", "relationship"),
        }
    return {}


def _get_single_list_item_model(response_model: type[BaseModel]) -> Optional[type[BaseModel]]:
    model_fields = getattr(response_model, "model_fields", {}) or {}
    if len(model_fields) != 1:
        return None
    field_info = next(iter(model_fields.values()))
    annotation = getattr(field_info, "annotation", None)
    if get_origin(annotation) not in {list, List}:
        return None
    args = get_args(annotation)
    if not args:
        return None
    item_model = args[0]
    if isinstance(item_model, type) and issubclass(item_model, BaseModel):
        return item_model
    return None


def _copy_first_present(item: Dict[str, Any], target_key: str, source_keys: tuple[str, ...]) -> bool:
    if item.get(target_key) not in (None, ""):
        return False
    for source_key in source_keys:
        value = item.get(source_key)
        if value not in (None, ""):
            item[target_key] = value
            return True
    return False


def _extract_json_candidate(text: str) -> Optional[str]:
    start_candidates = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    end_candidates = [index for index in (text.rfind("}"), text.rfind("]")) if index >= 0]
    if not start_candidates or not end_candidates:
        return None
    start = min(start_candidates)
    end = max(end_candidates)
    if start >= end:
        return None
    return text[start:end + 1]


def _to_openai_messages(llm_client: Any, messages: List[Any]) -> List[Dict[str, str]]:
    openai_messages = []
    clean_input = getattr(llm_client, "_clean_input", lambda value: value)
    for message in messages:
        role = getattr(message, "role", None)
        content = clean_input(getattr(message, "content", "") or "")
        if role in {"system", "user", "assistant"}:
            openai_messages.append({"role": role, "content": content})
    return openai_messages
