"""
联网搜索 provider 选择层
"""

from typing import Optional

from ..config import Config
from .bailian_web_search_service import BailianWebSearchService
from .bocha_search_service import BochaSearchService


class WebSearchProviderFactory:
    """根据配置创建联网搜索服务。"""

    PROVIDER_BAILIAN = "bailian"
    PROVIDER_BOCHA = "bocha"

    @classmethod
    def get_provider_name(cls, provider: Optional[str] = None) -> str:
        if Config.USE_BOCHA_WEB_SEARCH:
            return cls.PROVIDER_BOCHA
        provider_name = (provider or Config.WEB_SEARCH_PROVIDER or cls.PROVIDER_BAILIAN).strip().lower()
        if provider_name in {"aliyun", "dashscope", "qwen"}:
            return cls.PROVIDER_BAILIAN
        return provider_name

    @classmethod
    def create(cls, provider: Optional[str] = None):
        provider_name = cls.get_provider_name(provider)
        if provider_name == cls.PROVIDER_BOCHA:
            return BochaSearchService()
        if provider_name == cls.PROVIDER_BAILIAN:
            return BailianWebSearchService()
        raise ValueError(f"不支持的联网搜索 provider: {provider_name}")
