"""Shared MCP transport instrumentation; public tool schemas remain unchanged."""
from mcp.server.mcpserver import MCPServer
from apexa_observability import incoming_trace, span
from apexa_outcomes import normalize_mcp_result, classify_result


class TracedMCPServer(MCPServer):
    async def call_tool(self, name, arguments, context=None):
        metadata = None
        if context is not None:
            try:
                meta = context.request_context.meta
            except (AttributeError, ValueError, RuntimeError):
                meta = None
            metadata = (meta if isinstance(meta, dict) else meta.model_dump()).get('apexa_trace') if meta else None
        with incoming_trace(metadata), span('mcp.tool', tool=name) as fields:
            result = await super().call_tool(name, arguments, context)
            outcome = classify_result(normalize_mcp_result(result))
            fields.update(execution=outcome.execution, scientific=outcome.scientific_verdict)
            return result
