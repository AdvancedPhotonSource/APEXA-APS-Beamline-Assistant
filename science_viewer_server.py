"""Run next to detector data; access from APEXA through an SSH port forward.

    .venv/bin/python science_viewer_server.py --port 8020

Requires the preinstalled scientific/web dependencies but no LLM, credentials,
model downloads, MCP servers, or internet access.
"""
import argparse
from fastapi import FastAPI
from apexa_viewer_api import router

app = FastAPI(title="APEXA scientific viewer")
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "scope": "viewer HTTP liveness; not scientific validation"}


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
