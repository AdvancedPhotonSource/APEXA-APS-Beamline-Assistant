"""Connection setup routes behind the ordinary single-operator boundary."""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

import apexa_connection as connection
from apexa_llm_endpoints import EndpointRejected

router = APIRouter(prefix="/api/connection")
_verifying = False


async def read_request(request):
    body = await request.body()
    if len(body) > 16384:
        raise HTTPException(413, "Connection request is too large")
    try:
        import json
        data = json.loads(body)
        if not isinstance(data, dict) or set(data) - {"config", "api_key", "expected_revision"}:
            raise ValueError()
        key = data.get("api_key", "")
        if not isinstance(key, str) or len(key) > 8192 or any(ord(c) < 32 for c in key):
            raise ValueError()
        config = connection.ConnectionConfig.model_validate(data["config"])
        revision = data.get("expected_revision")
        if revision is not None and (not isinstance(revision, str) or len(revision) > 64):
            raise ValueError()
        return config, key.strip(), revision
    except (ValueError, KeyError, TypeError, ValidationError):
        # Never echo submitted secrets or upstream provider bodies in an error.
        raise HTTPException(422, "Invalid connection. Check the preset, HTTPS URL, model ID and required ANL username.") from None


@router.get("")
async def get_connection():
    return await asyncio.to_thread(connection.describe)


@router.put("")
async def save_connection(request: Request):
    config, key, revision = await read_request(request)
    try:
        return await asyncio.to_thread(connection.save_config, config, key, revision)
    except (ValueError, EndpointRejected) as exc:
        raise HTTPException(409, str(exc)) from None
    except OSError:
        raise HTTPException(503, "Could not save connection settings on this server") from None


@router.post("/verify")
async def verify_connection(request: Request):
    global _verifying
    config, key, _ = await read_request(request)
    if _verifying:
        raise HTTPException(409, "A connection check is already running")
    _verifying = True
    try:
        async with asyncio.timeout(45):
            return await connection.verify(config, key)
    except (ValueError, EndpointRejected) as exc:
        # Only our fixed validation messages reach the client, never HTTP bodies.
        return {"ok": False, "detail": str(exc) if not isinstance(exc, EndpointRejected)
                else "Credential unavailable. Check the server credential or supply a key for this endpoint."}
    except (TimeoutError, asyncio.TimeoutError):
        return {"ok": False, "detail": "Connection check timed out; no settings were changed"}
    except Exception:
        return {"ok": False, "detail": "Connection check failed. Check network access, credentials and provider tool-call support."}
    finally:
        _verifying = False
