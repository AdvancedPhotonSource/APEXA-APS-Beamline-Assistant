"""Client-owned workflow preparation and explicit operator CLI actions.

The model receives prepare/list/status capabilities only. Advancing a persisted
review gate belongs to typed UI requests or an explicit CLI command.
"""
from __future__ import annotations

import json
import shlex
import uuid
from contextvars import ContextVar
from pathlib import Path
import os

_dispatching = ContextVar('apexa_reviewed_workflow_dispatch', default=False)


def is_reviewed_workflow_dispatch():
    """Whether this task executes a reviewed job outside a model chat turn."""
    return _dispatching.get()


def _tool(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}}}


WORKFLOW_TOOLS = [
    _tool('list_scientific_workflows', 'List checkpointed scientific workflow recipes, registered-tool readiness, and this session’s runs. No processing starts.', {}),
    _tool('prepare_scientific_workflow', 'Prepare an immutable multi-stage scientific plan for explicit operator review. Use the recipe’s exact stage IDs and registered tool arguments; do not invent missing scientific inputs. No scientific tool runs until the operator approves in Active workflows or CLI.',
          {'recipe': {'type': 'string'}, 'parameters': {'type': 'object'}, 'dataset_id': {'type': 'string'}}, ['recipe', 'parameters', 'dataset_id']),
    _tool('get_scientific_workflow', 'Read a persisted workflow’s stages, job receipts, scientific verdict and pending review gate. This does not approve, resume or rerun anything.',
          {'workflow_id': {'type': 'string'}}, ['workflow_id']),
]
WORKFLOW_TOOL_NAMES = {tool['function']['name'] for tool in WORKFLOW_TOOLS}


def session_id(client):
    return (getattr(client, '_active_web_thread', None)
            or getattr(client.context, 'active_session', None) or 'cli-default')


def discovered_schemas(client):
    return {tool['function']['name']: tool['function']['parameters'] for tool in client._available_tools}


def runtime(client):
    from apexa_workflow_graph import WorkflowRuntime
    value = getattr(client, '_scientific_workflows', None)
    if value is None:
        async def execute_reviewed(tool, arguments):
            token = _dispatching.set(True)
            try:
                return await client.execute_tool_call(tool, arguments)
            finally:
                _dispatching.reset(token)
        value = WorkflowRuntime(execute_reviewed, tool_schemas=discovered_schemas(client))
        client._scientific_workflows = value
    # Validate new steps against the currently advertised schemas, including
    # reconnects. Each persisted plan retains its immutable schema fingerprints.
    value.tool_schemas = discovered_schemas(client)
    return value


async def pending_workflow_denial(client, tool_name):
    if _dispatching.get() or tool_name in WORKFLOW_TOOL_NAMES:
        return None
    from apexa_workflow_recipes import catalog
    guarded = {stage['tool'] for recipe in catalog() for stage in recipe['stages']}
    guarded -= {'inspect_dataset_file', 'survey_tomo_stack', 'synth_inspect_series', 'preflight_alignment'}
    if tool_name not in guarded:
        return None
    root = Path(os.environ.get('APEXA_WORKFLOW_DIR', '~/.apexa/workflows')).expanduser()
    if getattr(client, '_scientific_workflows', None) is None and not (root / 'workflows.sqlite3').exists():
        return None
    owner = await runtime(client).owner_for_tool(session_id(client), tool_name)
    if owner:
        return f"A persisted workflow owns this processing stage ({owner}). Use its explicit review controls; chat/tool calls cannot bypass its gate."
    return None


def recipes(client):
    from apexa_workflow_recipes import catalog
    available = {tool['function']['name'] for tool in client._available_tools}
    result = []
    for recipe in catalog():
        row = dict(recipe)
        stages = row.get('stages', [])
        missing = sorted({stage['tool'] for stage in stages if stage.get('required') and stage['tool'] not in available})
        row.update(registered_tools_available=not missing, missing_tools=missing,
                   engine_qualification='not_verified')
        result.append(row)
    return result


async def handle_tool(client, name, arguments):
    from apexa_workflow_recipes import compile_plan
    manager = runtime(client)
    owner = session_id(client)
    if name == 'list_scientific_workflows':
        result = {'recipes': recipes(client), 'workflows': await manager.list(session_id=owner)}
    elif name == 'prepare_scientific_workflow':
        plan = compile_plan(arguments['recipe'], arguments['parameters'],
                            tool_schemas=discovered_schemas(client), dataset_id=arguments['dataset_id'])
        result = await manager.create(plan, session_id=owner)
    elif name == 'get_scientific_workflow':
        result = await manager.get(arguments['workflow_id'])
        if result['session_id'] != owner:
            raise ValueError('Workflow belongs to another session; switch sessions before reading it')
    else:
        raise ValueError('Unknown workflow preparation tool')
    if 'orchestration_state' in result:
        state = result['orchestration_state']
        result['_apexa_outcome'] = {'schema_version': 1,
            'execution': 'completed' if state == 'completed' else 'cancelled' if state in ('cancelled', 'rejected') else
                         'unknown' if state == 'blocked' else 'running' if state == 'running' else 'pending',
            'scientific_verdict': result.get('scientific_verdict', 'unassessed')}
    return json.dumps(result, default=str)


async def cli_command(client, text):
    """Explicit human CLI grammar; never installed as a model-callable tool."""
    parts = shlex.split(text)
    manager = runtime(client)
    async def owned_snapshot(workflow_id):
        snapshot = await manager.get(workflow_id)
        if snapshot['session_id'] != session_id(client):
            raise ValueError('Workflow belongs to another session; switch sessions before using it')
        return snapshot
    if len(parts) == 2 and parts[1] in ('list', 'catalog'):
        value = recipes(client) if parts[1] == 'catalog' else await manager.list(session_id=session_id(client))
    elif len(parts) == 3 and parts[1] == 'show':
        value = await owned_snapshot(parts[2])
    elif len(parts) == 6 and parts[1] in ('approve', 'reject'):
        # workflow approve <id> <gate_id> <revision> <reason>
        await owned_snapshot(parts[2])
        value = await manager.advance(parts[2], gate_id=parts[3], expected_revision=int(parts[4]),
                                      decision=parts[1], request_id=uuid.uuid4().hex, reason=parts[5])
    elif len(parts) == 4 and parts[1] == 'cancel':
        await owned_snapshot(parts[2])
        value = await manager.cancel(parts[2], expected_revision=int(parts[3]), request_id=uuid.uuid4().hex)
    else:
        return ('Workflow commands: workflow catalog | list | show ID | '
                'approve ID GATE REVISION "reason" | reject ID GATE REVISION "reason" | cancel ID REVISION. '
                'Prepare plans with prepare_scientific_workflow; ordinary chat never approves a gate.')
    return json.dumps(value, indent=2, default=str)
