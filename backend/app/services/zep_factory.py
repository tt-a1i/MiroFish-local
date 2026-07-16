"""
Zep 客户端工厂

根据配置自动选择 Zep Cloud 或 Graphiti 本地实现。
通过 ZEP_BACKEND 环境变量控制后端选择。
"""

import logging
from typing import Dict, Optional

from ..config import Config
from .zep_adapter import ZepClientAdapter

logger = logging.getLogger('mirofish.zep_factory')


def create_zep_client(
    backend: Optional[str] = None,
    api_key: Optional[str] = None,
    neo4j_uri: Optional[str] = None,
    neo4j_user: Optional[str] = None,
    neo4j_password: Optional[str] = None,
    use_singleton: bool = True,
    llm_endpoint: Optional[object] = None,
) -> ZepClientAdapter:
    """
    创建 Zep 客户端实例

    根据 backend 参数或 ZEP_BACKEND 环境变量选择实现：
    - 'cloud': 使用 Zep Cloud (需要 ZEP_API_KEY)
    - 'graphiti': 使用 Graphiti + Neo4j 本地部署

    Args:
        backend: 后端选择 ('cloud' | 'graphiti')，默认从环境变量读取
        api_key: Zep Cloud API Key（仅 cloud 模式需要）
        neo4j_uri: Neo4j URI（仅 graphiti 模式需要）
        neo4j_user: Neo4j 用户名
        neo4j_password: Neo4j 密码

    Returns:
        ZepClientAdapter 实例
    """
    # 确定后端类型
    backend = backend or Config.ZEP_BACKEND

    if backend == 'graphiti':
        return _create_graphiti_client(
            neo4j_uri,
            neo4j_user,
            neo4j_password,
            use_singleton=use_singleton,
            llm_endpoint=llm_endpoint,
        )
    else:
        return _create_cloud_client(api_key)


def _create_cloud_client(api_key: Optional[str] = None) -> ZepClientAdapter:
    """创建 Zep Cloud 客户端"""
    from .zep_cloud_impl import ZepCloudClient

    key = api_key or Config.ZEP_API_KEY
    if not key:
        raise ValueError(
            "ZEP_API_KEY 未配置。使用 Zep Cloud 需要设置 ZEP_API_KEY 环境变量。"
        )

    logger.info("创建 Zep Cloud 客户端")
    return ZepCloudClient(api_key=key)


def _create_graphiti_client(
    neo4j_uri: Optional[str] = None,
    neo4j_user: Optional[str] = None,
    neo4j_password: Optional[str] = None,
    use_singleton: bool = True,
    llm_endpoint: Optional[object] = None,
) -> ZepClientAdapter:
    """创建 Graphiti 本地客户端"""
    from .zep_graphiti_impl import GraphitiClient

    uri = neo4j_uri or Config.NEO4J_URI
    user = neo4j_user or Config.NEO4J_USER
    password = neo4j_password or Config.NEO4J_PASSWORD

    if not all([uri, user, password]):
        raise ValueError(
            "Neo4j 配置不完整。使用 Graphiti 需要设置 NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD。"
        )

    logger.info(f"创建 Graphiti 本地客户端: {uri}")
    return GraphitiClient(
        neo4j_uri=uri,
        neo4j_user=user,
        neo4j_password=password,
        use_singleton=use_singleton,
        llm_endpoint=llm_endpoint,
    )


# ============================================================
# 单例缓存（可选，用于需要共享客户端实例的场景）
# ============================================================

import threading

_client_instances: Dict[str, ZepClientAdapter] = {}
_client_lock = threading.Lock()


def get_zep_client(backend: Optional[str] = None) -> ZepClientAdapter:
    """
    获取全局共享的 Zep 客户端实例（线程安全）

    首次调用时创建实例，后续调用返回相同实例。
    适用于需要复用连接的场景（如 Neo4j 连接池）。

    使用 double-checked locking 确保线程安全且性能最优。

    注意：如果需要独立实例，请直接调用 create_zep_client()。
    """
    backend_key = backend or Config.ZEP_BACKEND
    if backend_key not in _client_instances:
        with _client_lock:
            if backend_key not in _client_instances:
                _client_instances[backend_key] = create_zep_client(backend=backend_key)
    return _client_instances[backend_key]


def reset_zep_client():
    """
    重置全局客户端实例（线程安全）

    用于测试或需要重新初始化的场景。
    """
    with _client_lock:
        for client in _client_instances.values():
            if hasattr(client, 'close'):
                try:
                    client.close()
                except Exception:
                    pass
        _client_instances.clear()
        logger.info("全局 Zep 客户端实例已重置")
