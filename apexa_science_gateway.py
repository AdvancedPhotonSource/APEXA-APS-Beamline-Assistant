"""Optional science API forwarding over an operator-established SSH tunnel."""
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response


def science_gateway(base_url: str, *, transport=None) -> APIRouter:
    parsed = urlparse(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("APEXA_SCIENCE_BASE_URL must be an HTTP loopback SSH tunnel, e.g. http://127.0.0.1:8020")
    router = APIRouter(prefix="/api/science")
    allowed = {"structure": "GET", "report": "GET", "sequence": "POST", "frame": "POST", "rings": "POST"}

    @router.api_route("/{operation}", methods=["GET", "POST"])
    async def forward(operation: str, request: Request):
        if allowed.get(operation) != request.method:
            raise HTTPException(404, "Unknown scientific viewer operation")
        body = await request.body()
        if len(body) > 2_000_000:
            raise HTTPException(413, "Viewer request too large")
        try:
            async with httpx.AsyncClient(timeout=180, transport=transport, trust_env=False) as client:
                result = await client.request(request.method,
                    f"{base_url.rstrip('/')}/api/science/{operation}",
                    params=request.query_params, content=body,
                    headers={"Content-Type": "application/json"})
            return Response(result.content, status_code=result.status_code, media_type="application/json")
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Remote scientific viewer unavailable. Check the SSH tunnel and remote viewer service; local files were not used as a fallback.") from exc
    return router
