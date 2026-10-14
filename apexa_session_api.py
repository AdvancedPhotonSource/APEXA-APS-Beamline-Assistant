"""Session browsing works without starting an LLM or any beamline engine."""
import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from apexa_sessions import SessionStore, SessionConflict, valid_name

router = APIRouter(prefix="/api/sessions")


def _call(operation):
    try:
        return operation(SessionStore())
    except SessionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "Session not found") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _public(record):
    return {k: v for k, v in record.items() if k not in ("conversation_history", "turn")}


class NewSession(BaseModel):
    id: str = Field(default_factory=lambda: f"sess-{uuid.uuid4().hex}")


class RenameSession(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class WorkspaceUpdate(BaseModel):
    workspace: dict
    expected_revision: int = Field(ge=0)


class BrowserImport(BaseModel):
    id: str
    title: str = "New chat"
    createdAt: int
    updatedAt: int
    messages: list[dict]


@router.get("")
def list_sessions(archived: bool = False):
    def read(store):
        last = None
        try:
            last = valid_name((store.root / ".last_session").read_text().strip())
        except (OSError, ValueError):
            pass
        return {"sessions": store.list(archived), "last_active": last}
    return _call(read)


@router.post("")
def create_session(payload: NewSession):
    return _call(lambda store: _public(store.ensure(payload.id)))


@router.post("/import")
def import_browser_session(payload: BrowserImport):
    data = payload.model_dump()
    if len(json.dumps(data).encode()) > 20_000_000:
        raise HTTPException(413, "Browser history import exceeds 20 MB; the browser copy has been retained")
    for message in payload.messages:
        if message.get("role") not in ("user", "assistant", "system") or not isinstance(message.get("content"), str):
            raise HTTPException(422, "Invalid browser history message")
    return _call(lambda store: _public(store.import_browser(data)))


@router.get("/{session_id}")
def read_session(session_id: str):
    return _call(lambda store: _public(store.get(session_id)))


@router.patch("/{session_id}")
def rename_session(session_id: str, payload: RenameSession):
    return _call(lambda store: _public(store.rename(session_id, payload.title)))


@router.put("/{session_id}/workspace")
def save_workspace(session_id: str, payload: WorkspaceUpdate):
    record = _call(lambda store: store.workspace(session_id, payload.workspace, payload.expected_revision))
    return {"workspaceRevision": record["workspaceRevision"], "revision": record["revision"]}


@router.delete("/{session_id}")
def archive_session(session_id: str):
    _call(lambda store: store.archive(session_id))
    return {"archived": True}
