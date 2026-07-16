"""Neo4j 错误分类工具。"""


NEO4J_AUTH_ERROR_CODES = {
    "Neo.ClientError.Security.Unauthorized",
    "Neo.ClientError.Security.AuthenticationRateLimit",
}

NEO4J_AUTH_ERROR_HINTS = (
    "incorrect authentication details",
    "unauthorized due to authentication failure",
    "authenticationratelimit",
)


def is_neo4j_auth_error(error: Exception) -> bool:
    """判断异常是否属于 Neo4j 认证失败或认证限流。"""
    code = _get_neo4j_error_code(error)
    if code in NEO4J_AUTH_ERROR_CODES:
        return True

    message = str(error).lower()
    return any(code.lower() in message for code in NEO4J_AUTH_ERROR_CODES) or any(
        hint in message for hint in NEO4J_AUTH_ERROR_HINTS
    )


def format_neo4j_auth_error(error: Exception) -> str:
    """生成面向接口和日志的 Neo4j 认证错误说明。"""
    return (
        "Neo4j 认证失败或已触发认证限流，请检查 NEO4J_URI、NEO4J_USER、"
        "NEO4J_PASSWORD 是否与当前 Neo4j 数据库实际凭证一致；"
        f"原始错误: {error}"
    )


def _get_neo4j_error_code(error: Exception) -> str:
    for attr in ("code", "neo4j_code", "gql_status"):
        value = getattr(error, attr, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                value = None
        if value:
            return str(value)
    return ""
