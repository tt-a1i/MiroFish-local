"""
LLM客户端封装
统一使用OpenAI格式调用
"""

import json
from typing import Iterator, Optional, Dict, Any, List
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from ..config import Config
from .llm_routing import LLMEndpoint, get_preferred_llm_endpoint


class LLMRequestError(RuntimeError):
    """LLM 调用失败，携带可安全返回给前端的错误信息。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        retryable: bool = False,
        route_name: Optional[str] = None,
        cause: Optional[BaseException] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.route_name = route_name
        self.__cause__ = cause


class LLMClient:
    """LLM客户端"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        prefer_boost: bool = False
    ):
        self.endpoint: Optional[LLMEndpoint] = None
        if api_key or base_url or model:
            self.api_key = api_key or Config.LLM_API_KEY
            self.base_url = base_url or Config.LLM_BASE_URL
            self.model = model or Config.LLM_MODEL_NAME
            self.route_name = "custom"
        else:
            endpoint = get_preferred_llm_endpoint(prefer_boost=prefer_boost)
            self.endpoint = endpoint
            self.api_key = endpoint.api_key
            self.base_url = endpoint.base_url
            self.model = endpoint.model
            self.route_name = endpoint.route_name
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY 未配置")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        发送聊天请求
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            response_format: 响应格式（如JSON模式）
            
        Returns:
            模型响应文本
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if response_format:
            kwargs["response_format"] = response_format
        
        try:
            response = self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content
        except Exception as exc:
            raise normalize_llm_error(exc, route_name=self.route_name) from exc

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> Iterator[str]:
        """
        发送流式聊天请求，逐段返回模型输出文本。

        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数

        Yields:
            模型增量输出文本
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
        except Exception as exc:
            raise normalize_llm_error(exc, route_name=self.route_name) from exc

        for chunk in response:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield content
    
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        发送聊天请求并返回JSON
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            
        Returns:
            解析后的JSON对象
        """
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        
        try:
            return json.loads(response)
        except json.JSONDecodeError as exc:
            raise LLMRequestError(
                "LLM 返回内容不是有效 JSON，请稍后重试",
                status_code=502,
                retryable=True,
                route_name=self.route_name,
                cause=exc,
            ) from exc


def normalize_llm_error(exc: BaseException, route_name: Optional[str] = None) -> LLMRequestError:
    """把 OpenAI SDK 异常归一化为前端可读的业务异常。"""
    if isinstance(exc, LLMRequestError):
        return exc

    if isinstance(exc, RateLimitError):
        return LLMRequestError(
            "LLM 服务限流，请稍后重试",
            status_code=503,
            retryable=True,
            route_name=route_name,
            cause=exc,
        )

    if isinstance(exc, (APIConnectionError, APITimeoutError, TimeoutError)):
        return LLMRequestError(
            "LLM 服务连接超时或网络不可用，请稍后重试",
            status_code=503,
            retryable=True,
            route_name=route_name,
            cause=exc,
        )

    if isinstance(exc, APIStatusError):
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403}:
            return LLMRequestError(
                "LLM 认证失败，请检查 LLM_API_KEY 或 LLM_BOOST_API_KEY 配置",
                status_code=502,
                retryable=False,
                route_name=route_name,
                cause=exc,
            )
        if status_code == 429:
            return LLMRequestError(
                "LLM 服务限流，请稍后重试",
                status_code=503,
                retryable=True,
                route_name=route_name,
                cause=exc,
            )
        if status_code and status_code >= 500:
            return LLMRequestError(
                "LLM 服务暂时不可用，请稍后重试",
                status_code=503,
                retryable=True,
                route_name=route_name,
                cause=exc,
            )

    return LLMRequestError(
        f"LLM 调用失败: {exc}",
        status_code=502,
        retryable=False,
        route_name=route_name,
        cause=exc,
    )
