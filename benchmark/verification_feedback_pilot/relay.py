#!/usr/bin/env python3
"""Local renewable ALCF credentials; remote receives only a short-lived relay key.

Run outside Box. State is mode 0600 and contains no Globus/provider credential.
No prompts, responses, request headers or credentials are logged here. Per-trial
scientific accounting is performed by the remote ModelAudit relay.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time

import httpx
from alcf_tokens.auth import get_access_token


def serve(state_path):
    upstream = 'https://inference-api.alcf.anl.gov/resource_server/sophia/vllm/v1'
    relay_key = secrets.token_hex(32)
    token_lock = threading.Lock()
    deadline = time.monotonic() + 12 * 3600

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def fail(self, code, message):
            body = json.dumps({'error': {'message': message}}).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)

        def do_GET(self):
            self.forward(False)

        def do_POST(self):
            self.forward(True)

        def forward(self, post):
            if time.monotonic() > deadline:
                return self.fail(503, 'Relay lifetime expired')
            if not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + relay_key):
                return self.fail(401, 'Relay credential required')
            if self.path != ('/v1/chat/completions' if post else '/v1/models'):
                return self.fail(404, 'Endpoint outside pilot')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 16000000 or self.headers.get('Transfer-Encoding'):
                    return self.fail(413, 'Invalid request size')
                body = self.rfile.read(length) if length else None
                if post:
                    payload = json.loads(body)
                    if payload.get('model') != 'openai/gpt-oss-120b':
                        return self.fail(400, 'Model outside pilot')
                    # Shared endpoint settings, identical across both harnesses.
                    # Trial logs retain the originals; these common effective
                    # values are frozen in the trial receipt.
                    payload['reasoning_effort'] = 'medium'
                    payload['temperature'] = 1.0
                    payload.pop('top_p', None)
                    if payload.get('stream'):
                        payload['stream_options'] = {'include_usage': True}
                    body = json.dumps(payload).encode()
                with token_lock:
                    token = get_access_token('inference')
                with httpx.Client(timeout=httpx.Timeout(300, connect=20)) as client:
                    with client.stream('POST' if post else 'GET',
                            upstream + self.path.removeprefix('/v1'), content=body,
                            headers={'Authorization': 'Bearer ' + token,
                                     'Content-Type': 'application/json',
                                     'Accept-Encoding': 'identity'}) as response:
                        self.send_response(response.status_code)
                        self.send_header('Content-Type', response.headers.get('content-type', 'application/json'))
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        for block in response.iter_bytes():
                            self.wfile.write(block)
                            self.wfile.flush()
            except (httpx.HTTPError, OSError):
                self.close_connection = True
            except Exception:
                return self.fail(502, 'Credential or request preparation failed')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'pid': os.getpid(), 'port': server.server_port, 'relay_key': relay_key,
                   'expires_unix': time.time() + 12 * 3600}, stream)
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, type=Path)
    serve(parser.parse_args().state)
