"""Configured scientific environments survive a real local MCP round trip."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.asyncio
async def test_solver_and_execution_policy_reach_child_server(tmp_path, monkeypatch):
    from argo_mcp_client import APEXAClient

    script = tmp_path / 'environment_probe.py'
    script.write_text('''import os
from mcp.server.mcpserver import MCPServer
mcp = MCPServer("configuration probe")
@mcp.tool()
def configuration() -> dict:
    return {k: os.environ.get(k) for k in
            ("APEXA_MIDAS_BIN", "APEXA_FORCE_REMOTE_EXEC", "APEXA_SESSION_DIR")}
mcp.run()
''')
    expected = {'APEXA_MIDAS_BIN': str(tmp_path/'solver/bin'),
                'APEXA_FORCE_REMOTE_EXEC': '0',
                'APEXA_SESSION_DIR': str(tmp_path/'sessions')}
    for key, value in expected.items():
        monkeypatch.setenv(key, value)
    # Only exercise server startup; no user sessions or model connections.
    client = APEXAClient.__new__(APEXAClient)
    for field in ('sessions', '_server_stacks', '_server_configs', '_server_pids'):
        setattr(client, field, {})
    monkeypatch.setattr(client, '_server_command', lambda _: sys.executable)
    try:
        await client._connect_one_server('probe', str(script), register_tools=False)
        result = await client.sessions['probe'].call_tool('configuration', {})
        assert not result.is_error
        assert json.loads(result.content[0].text) == expected
    finally:
        for stack in reversed(list(client._server_stacks.values())):
            await stack.aclose()
