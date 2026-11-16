"""DreamWeaver MCP Server 包。

对外暴露的是工具、鉴权范围与审计；传输方式（stdio / streamable-http）在 server.py 里切换。
"""
from app.mcp.policy import READ, WRITE, TokenInfo, load_tokens, stdio_identity

__all__ = ["READ", "WRITE", "TokenInfo", "load_tokens", "stdio_identity"]
