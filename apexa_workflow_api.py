"""Typed operator decisions for the shared scientific workflow runtime."""
import asyncio
from contextlib import suppress

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from apexa_sessions import valid_name
from apexa_workflow_client import recipes, runtime, discovered_schemas
from apexa_workflow_graph import WorkflowConflict, WorkflowNotFound, WorkflowUnavailable

_operations = {}


class Prepare(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str
    recipe: str
    parameters: dict
    dataset_id: str = Field(min_length=1)


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    gate_id: str = Field(min_length=1, max_length=256)
    decision: str = Field(pattern='^(approve|reject)$')
    request_id: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=2000)


class Cancellation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=256)
    reason: str | None = Field(default=None, max_length=2000)


def _http_error(exc):
    if isinstance(exc, WorkflowNotFound):
        return HTTPException(404, 'Workflow not found')
    if isinstance(exc, WorkflowConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, WorkflowUnavailable):
        return HTTPException(503, str(exc))
    return HTTPException(422, str(exc))


async def close_workflow_operations():
    tasks = [task for task, _, _ in _operations.values()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def workflow_router(client_getter):
    router = APIRouter(prefix='/api/workflows')

    def client():
        value = client_getter()
        if value is None:
            raise HTTPException(503, 'Workflow runtime is waiting for the MCP client')
        return value

    @router.get('/catalog')
    async def catalogue():
        return {'recipes': recipes(client())}

    @router.get('')
    async def list_runs(session_id: str, limit: int = 100):
        try:
            return {'workflows': await runtime(client()).list(session_id=valid_name(session_id), limit=limit)}
        except (ValueError, KeyError, WorkflowUnavailable) as exc:
            raise _http_error(exc) from exc

    @router.post('')
    async def prepare(body: Prepare):
        from apexa_workflow_recipes import compile_plan
        value = client()
        try:
            owner = valid_name(body.session_id)
            plan = compile_plan(body.recipe, body.parameters, tool_schemas=discovered_schemas(value),
                                dataset_id=body.dataset_id)
            return await runtime(value).create(plan, session_id=owner)
        except (ValueError, KeyError, WorkflowUnavailable) as exc:
            raise _http_error(exc) from exc

    @router.get('/{workflow_id}')
    async def status(workflow_id: str):
        try:
            return await runtime(client()).get(workflow_id)
        except (ValueError, KeyError, WorkflowUnavailable) as exc:
            raise _http_error(exc) from exc

    @router.post('/{workflow_id}/advance')
    async def advance(workflow_id: str, body: Decision):
        value = client()
        manager = runtime(value)
        try:
            snapshot = await manager.get(workflow_id)
            existing = _operations.get(workflow_id)
            if existing and not existing[0].done():
                if existing[1] != body.model_dump():
                    raise WorkflowConflict('A workflow action is already in progress; refresh its state')
                # A queued task is not durable admission. Every retry observes
                # the same admission barrier (or the same pre-admission error).
                task, _, admitted = existing
            else:
                if len(_operations) >= 16:
                    raise HTTPException(429, 'Workflow operation queue is full')
                admitted = asyncio.Event()
                async def execute():
                    async with value._web_thread_lock:
                        await value.activate_web_thread(snapshot['session_id'])
                        return await manager.advance(workflow_id, **body.model_dump(), on_admitted=admitted.set)
                task = asyncio.create_task(execute())
                _operations[workflow_id] = (task, body.model_dump(), admitted)
                def done(completed):
                    if _operations.get(workflow_id, (None,))[0] is completed:
                        _operations.pop(workflow_id, None)
                    if not completed.cancelled():
                        completed.exception()  # Runtime persists errors; do not leak task exceptions.
                task.add_done_callback(done)
            waiting = asyncio.create_task(admitted.wait())
            try:
                await asyncio.wait((task, waiting), return_when=asyncio.FIRST_COMPLETED)
            finally:
                waiting.cancel()
                with suppress(asyncio.CancelledError):
                    await waiting
            if task.done():
                return await task
            return JSONResponse(await manager.get(workflow_id), status_code=202)
        except (ValueError, KeyError, WorkflowUnavailable) as exc:
            raise _http_error(exc) from exc

    @router.post('/{workflow_id}/cancel')
    async def cancel(workflow_id: str, body: Cancellation):
        try:
            return await runtime(client()).cancel(workflow_id, **body.model_dump())
        except (ValueError, KeyError, WorkflowUnavailable) as exc:
            raise _http_error(exc) from exc

    return router
