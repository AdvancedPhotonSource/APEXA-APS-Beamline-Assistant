"""Per-trial ARGO relay: common request budget and provider-response evidence.

Only model API traffic is relayed. It is not a general HTTP proxy. Prompts,
responses and credentials are not logged; hashes, model IDs, settings and usage
are. Streaming content is passed through without changing scientific context.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def model_key(value):
    return "".join(c for c in str(value).lower() if c.isalnum())


def response_evidence(payload, record):
    """Merge JSON/SSE metadata; Anthropic reports input/output in separate events."""
    if not isinstance(payload, dict):
        return
    objects = [payload]
    for key in ("message", "response"):
        if isinstance(payload.get(key), dict):
            objects.append(payload[key])
    for obj in objects:
        if isinstance(obj.get("model"), str):
            record.setdefault("response_models", [])
            if obj["model"] not in record["response_models"]:
                record["response_models"].append(obj["model"])
        if isinstance(obj.get("usage"), dict):
            record.setdefault("usage", {}).update({k: v for k, v in obj["usage"].items() if v is not None})


class ModelAudit:
    def __init__(self, upstream, api_key, model, output, *, max_requests=40,
                 max_output_tokens=16000, timeout=1800):
        parsed = urlsplit(upstream)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Use a model API URL without embedded credentials or query")
        self.upstream = upstream.rstrip("/")
        self.api_key, self.model = api_key, model
        self.output = Path(output)
        self.max_requests, self.max_output_tokens = max_requests, max_output_tokens
        self.deadline = time.monotonic() + timeout
        self.token = secrets.token_hex(24)
        self.lock = threading.Lock()
        self.requests = 0
        self.records = []
        self.violations = []
        self.closed = False

    def record(self, entry):
        with self.lock:
            if self.closed:
                return
            self.records.append(entry)
            self.stream.write(json.dumps(entry) + "\n")
            self.stream.flush()

    def __enter__(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.output.open("x")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def error(self, code, message):
                with owner.lock:
                    owner.violations.append({"status": code, "reason": message})
                owner.record({"event": "rejected_request", "http_status": code, "reason": message})
                body = json.dumps({"error": {"type": "study_gateway", "message": message}}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def do_GET(self):
                self.forward(False)

            def do_POST(self):
                self.forward(True)

            def forward(self, generation):
                path = urlsplit(self.path).path
                if path not in (("/v1/chat/completions", "/v1/messages") if generation else ("/v1/models",)):
                    return self.error(404, "Endpoint outside the study API")
                auth = self.headers.get("Authorization", "").removeprefix("Bearer ")
                if owner.token not in (auth, self.headers.get("x-api-key")):
                    return self.error(401, "Trial credential required")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    return self.error(400, "Invalid Content-Length")
                if self.headers.get("Transfer-Encoding"):
                    return self.error(400, "Chunked requests are not supported")
                if not 0 <= length <= 16_000_000:
                    return self.error(413, "Request exceeds study limit")
                body = self.rfile.read(length) if length else b""
                record = {"event": "generation" if generation else "models", "path": path,
                          "started_unix": time.time(), "response_models": [], "usage": {}}
                if generation:
                    try:
                        payload = json.loads(body)
                        if not isinstance(payload, dict):
                            raise ValueError()
                    except (ValueError, UnicodeError):
                        return self.error(400, "Expected a JSON object")
                    if model_key(payload.get("model")) != model_key(owner.model):
                        return self.error(400, "Requested model differs from frozen study model")
                    token_key = "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"
                    ceiling = payload.get(token_key, owner.max_output_tokens)
                    if type(ceiling) is not int or ceiling < 1:
                        return self.error(400, "Invalid output-token ceiling")
                    with owner.lock:
                        exhausted = owner.requests >= owner.max_requests or time.monotonic() >= owner.deadline
                        if not exhausted:
                            owner.requests += 1
                            record["request_index"] = owner.requests
                    if exhausted:
                        return self.error(429, "Study request or wall-time budget exhausted")
                    record["requested_model"] = payload["model"]
                    record["request_sha256"] = hashlib.sha256(body).hexdigest()
                    record["settings"] = {k: payload[k] for k in (
                        "temperature", "top_p", "thinking", "reasoning_effort", "output_config",
                        "max_tokens", "max_completion_tokens", "stream") if k in payload}
                    payload[token_key] = min(ceiling, owner.max_output_tokens)
                    record["effective_output_ceiling"] = payload[token_key]
                    body = json.dumps(payload).encode()
                    record["forwarded_sha256"] = hashlib.sha256(body).hexdigest()
                headers = {"Authorization": "Bearer " + owner.api_key, "x-api-key": owner.api_key,
                           "Content-Type": "application/json", "Accept-Encoding": "identity"}
                for key in ("anthropic-version", "anthropic-beta", "Accept"):
                    if self.headers.get(key):
                        headers[key] = self.headers[key]
                url = owner.upstream + path.removeprefix("/v1")
                digest = hashlib.sha256()
                try:
                    remaining = max(0.1, min(60, owner.deadline - time.monotonic()))
                    with httpx.Client(timeout=httpx.Timeout(remaining, connect=min(20, remaining))) as client:
                        with client.stream("POST" if generation else "GET", url, headers=headers,
                                           content=body or None) as response:
                            record["http_status"] = response.status_code
                            self.send_response(response.status_code)
                            self.send_header("Content-Type", response.headers.get("content-type", "application/json"))
                            self.send_header("Connection", "close")
                            self.end_headers()
                            self.close_connection = True
                            if "text/event-stream" in response.headers.get("content-type", ""):
                                record["response_hash_format"] = "UTF-8 SSE lines with LF terminators"
                                for line in response.iter_lines():
                                    if owner.closed or time.monotonic() >= owner.deadline:
                                        raise TimeoutError("Trial deadline reached")
                                    encoded = (line + "\n").encode()
                                    digest.update(encoded)
                                    if line.startswith("data:"):
                                        try:
                                            response_evidence(json.loads(line[5:].strip()), record)
                                        except ValueError:
                                            pass
                                    self.wfile.write(encoded)
                                    self.wfile.flush()
                            else:
                                record["response_hash_format"] = "decoded HTTP response body bytes"
                                raw = response.read()
                                digest.update(raw)
                                try:
                                    response_evidence(json.loads(raw), record)
                                except ValueError:
                                    pass
                                self.wfile.write(raw)
                except (httpx.HTTPError, OSError) as exc:
                    record["transport_error"] = type(exc).__name__
                    self.close_connection = True
                finally:
                    if generation:
                        record["elapsed_s"] = time.time() - record["started_unix"]
                        record["response_sha256"] = digest.hexdigest()
                        record["model_matches"] = bool(record["response_models"]) and all(
                            model_key(m) == model_key(owner.model) for m in record["response_models"])
                        owner.record(record)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # A stuck upstream must not indefinitely hold the trial supervisor open.
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self

    def summary(self):
        records = [r for r in self.records if r.get("event") == "generation"]
        success = [r for r in records if r.get("http_status") == 200 and not r.get("transport_error")]
        return {"requests": self.requests, "recorded_requests": len(self.records),
                "completed_generations": len(records), "violations": list(self.violations),
                "qualified": bool(success) and not self.violations and len(success) == self.requests
                             and all(r["model_matches"] and r["usage"] for r in success),
                "successful_responses": len(success),
                "all_response_models_match": bool(success) and all(r["model_matches"] for r in success),
                "all_successes_have_usage": bool(success) and all(bool(r["usage"]) for r in success),
                "response_models": sorted({m for r in success for m in r["response_models"]}),
                "request_budget": self.max_requests, "per_request_output_ceiling": self.max_output_tokens,
                "note": "Provider-reported model IDs; this cannot prove the gateway's internal routing."}

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        with self.lock:
            self.closed = True
            self.stream.close()
