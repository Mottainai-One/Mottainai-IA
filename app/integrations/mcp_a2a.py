"""HTTP contracts for Mottainai's MCP and A2A integration.

Note: unlike the chat agents' SYSTEM_PROMPT, the strings in this file (tool
descriptions, error messages, agent card) are protocol-level text consumed
by other systems/agents via MCP and A2A, not by the Mottainai retail end
customer — so they are translated to English here.
"""
import secrets
from typing import Any

from app.config import get_settings
from app.tools.mcp_tools import mcp_expose_tool

MCP_PROTOCOL_VERSION = "2024-11-05"


def _authorized(authorization: str | None, expected_token: str) -> bool:
    if not expected_token or not authorization or not authorization.startswith("Bearer "):
        return False
    return secrets.compare_digest(authorization.removeprefix("Bearer "), expected_token)


def _company_scoped_arguments(
    arguments: object,
    authorized_empresa_id: object,
) -> tuple[dict[str, Any] | None, str | None]:
    """Binds a read call to the company configured for the token."""
    if isinstance(authorized_empresa_id, bool) or not isinstance(authorized_empresa_id, int) or authorized_empresa_id < 1:
        return None, "Integration has no authorized company configured."
    if not isinstance(arguments, dict):
        return None, "Invalid arguments."

    requested_empresa_id = arguments.get("empresa_id")
    if requested_empresa_id is not None and requested_empresa_id != authorized_empresa_id:
        return None, "Company not authorized."

    return {**arguments, "empresa_id": authorized_empresa_id}, None


def mcp_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "get_active_alerts",
            "description": "Returns active stock alerts for an authorized company.",
            "inputSchema": {"type": "object", "properties": {"empresa_id": {"type": "integer", "minimum": 1}}, "required": ["empresa_id"]},
        },
        {
            "name": "get_company_kpis",
            "description": "Returns KPIs for the authorized company.",
            "inputSchema": {"type": "object", "properties": {"empresa_id": {"type": "integer", "minimum": 1}}, "required": ["empresa_id"]},
        },
    ]


async def dispatch_mcp(request: dict[str, Any], authorization: str | None) -> dict[str, Any]:
    """Processes the subset of MCP JSON-RPC required for internal tools."""
    request_id = request.get("id")
    method = request.get("method")
    settings = get_settings()
    if not _authorized(authorization, settings.mcp_shared_token):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "Unauthorized."}}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": MCP_PROTOCOL_VERSION, "serverInfo": {"name": "mottainai", "version": "1.0.0"}, "capabilities": {"tools": {}}}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": mcp_tools()}}
    if method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid parameters."}}
        name = params.get("name")
        arguments, error = _company_scoped_arguments(
            params.get("arguments") or {},
            getattr(settings, "mcp_empresa_id", 0),
        )
        if error:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": error}}
        result = await mcp_expose_tool(name, arguments)
        if result.get("error"):
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": result["error"]}}
        return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": str(result["result"])}], "isError": False}}
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Unsupported method."}}


def a2a_agent_card(base_url: str) -> dict[str, Any]:
    """Agent Card for discovery by external A2A-compatible agents."""
    return {
        "name": "Mottainai Inventory Intelligence",
        "description": "Predictive inventory and sustainability agent for retail.",
        "url": f"{base_url.rstrip('/')}/a2a",
        "version": "1.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [{"id": "inventory-insights", "name": "Inventory insights", "description": "Queries authorized stock alerts and KPIs."}],
        "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
    }


async def dispatch_a2a(message: dict[str, Any], authorization: str | None) -> dict[str, Any]:
    """Executes an A2A request with an allowlist of read actions."""
    settings = get_settings()
    if not _authorized(authorization, settings.a2a_shared_token):
        return {"error": {"code": "unauthorized", "message": "Unauthorized."}}
    action = message.get("action")
    payload, error = _company_scoped_arguments(
        message.get("payload") or {},
        getattr(settings, "a2a_empresa_id", 0),
    )
    if action not in {"get_active_alerts", "get_company_kpis"}:
        return {"error": {"code": "unsupported_action", "message": "Unsupported action."}}
    if error:
        return {"error": {"code": "tenant_not_authorized", "message": error}}
    result = await mcp_expose_tool(action, payload)
    return {"status": "completed", "action": action, "result": result.get("result"), "error": result.get("error")}
