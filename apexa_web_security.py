"""Single-operator web boundary and confined, bounded upload storage.

This is not multi-tenant authorization. A shared deployment needs separate
identities/workspaces and a per-principal runtime before enabling multiple users.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit
import uuid

from fastapi import HTTPException
from starlette.responses import JSONResponse


def allowed_origins():
    return [x.strip().rstrip("/") for x in os.environ.get(
        "APEXA_WEB_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if x.strip()]


def upload_limit():
    return max(1, int(os.environ.get("APEXA_UPLOAD_MAX_BYTES", str(512 * 1024 * 1024))))


def authorize_data_path(value):
    path = Path(value).expanduser().resolve()
    roots = [Path(p).expanduser().resolve() for p in os.environ.get("APEXA_DATA_ROOTS", "").split(os.pathsep) if p]
    if roots and not any(path.is_relative_to(root) for root in roots):
        raise HTTPException(403, "Path is outside APEXA_DATA_ROOTS")
    return path


class SingleOperatorBoundary:
    """Deny foreign browser origins and unauthenticated non-loopback access.

    A configured token is checked for HTTP and WebSocket alike. A trusted reverse
    proxy can inject it; client-supplied identity/forwarding headers are not trusted.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        peer = (scope.get("client") or ("", 0))[0]
        try:
            loopback = ipaddress.ip_address(peer).is_loopback
        except ValueError:
            loopback = peer == "testclient"  # Starlette's in-process test transport.
        permitted_hosts = {"localhost", "127.0.0.1", "::1"}
        permitted_hosts.update(x.strip().lower() for x in os.environ.get("APEXA_WEB_HOSTS", "").split(",") if x.strip())
        if peer == "testclient":
            permitted_hosts.add("testserver")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        external = not loopback or hostname not in {"localhost", "127.0.0.1", "::1", "testserver" if peer == "testclient" else ""}
        reason, code = None, 403
        if hostname not in permitted_hosts:
            reason = "Host is not in APEXA_WEB_HOSTS"
        origin = headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                parsed = urlsplit("invalid:")
            same_origin = (parsed.scheme == ("https" if scope.get("scheme") in ("https", "wss") else "http")
                           and parsed.netloc.lower() == host.lower())
            if (parsed.scheme not in ("http", "https") or parsed.username or parsed.password
                    or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                    or not (same_origin or origin.rstrip("/") in allowed_origins())):
                reason = "Browser origin is not allowed"
        token = os.environ.get("APEXA_WEB_TOKEN", "")
        if token:
            supplied = headers.get("authorization", "")
            if not secrets.compare_digest(supplied.encode(), ("Bearer " + token).encode()) and not reason:
                reason, code = "Authentication required", 401
        elif external and not reason:
            reason, code = "Non-loopback access requires APEXA_WEB_TOKEN", 401
        if external and not os.environ.get("APEXA_DATA_ROOTS") and not reason:
            reason = "Non-loopback access requires configured APEXA_DATA_ROOTS"
        if os.environ.get("APEXA_WEB_MODE", "single_operator") != "single_operator":
            reason = "Only single_operator mode is qualified; shared identities are not implemented"
        if reason:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": reason})
            else:
                await JSONResponse({"detail": reason}, status_code=code)(scope, receive, send)
            return
        if scope["type"] == "http":
            limit = upload_limit()
            try:
                if int(headers.get("content-length", "0")) > limit:
                    await JSONResponse({"detail": "Request exceeds APEXA_UPLOAD_MAX_BYTES"}, status_code=413)(scope, receive, send)
                    return
            except ValueError:
                await JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)(scope, receive, send)
                return
            consumed = 0
            async def bounded_receive():
                nonlocal consumed
                message = await receive()
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise HTTPException(413, "Request exceeds APEXA_UPLOAD_MAX_BYTES")
                return message
            return await self.app(scope, bounded_receive, send)
        return await self.app(scope, receive, send)


async def save_upload(upload, directory):
    """Generate a filename; never join an untrusted filename to a directory."""
    name = upload.filename or "upload"
    if name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise HTTPException(400, "Upload filename must be a basename")
    suffix = Path(name).suffix
    if len(suffix) > 16 or any(c not in ".abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in suffix):
        raise HTTPException(400, "Invalid upload extension")
    root = authorize_data_path(directory)
    root.mkdir(parents=True, exist_ok=True)
    path = root / (uuid.uuid4().hex + suffix)
    def copy():
        count = 0
        try:
            with path.open("xb") as stream:
                while chunk := upload.file.read(1024 * 1024):
                    count += len(chunk)
                    if count > upload_limit():
                        raise HTTPException(413, "Upload exceeds APEXA_UPLOAD_MAX_BYTES; open large data by its host path")
                    stream.write(chunk)
            return path, count
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    return await asyncio.to_thread(copy)
