#!/usr/bin/env python3
"""Capture configured source/package identity without starting MCP or engines.

This inventory distinguishes declared tools, runtime connectivity and scientific
qualification. It intentionally cannot certify the latter two from source files.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys


def inventory(root: Path):
    servers = []
    for line in (root / 'servers.config').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, filename = line.split(':', 1)
        source = (root / filename.strip()).resolve()
        row = {'name': name, 'source': filename.strip(), 'present': source.is_file(),
               'runtime_connected': 'not_probed', 'engine_qualification': 'not_verified'}
        if source.is_file():
            content = source.read_bytes()
            tree = ast.parse(content)
            tools = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                    isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == 'tool' for decorator in node.decorator_list):
                    tools.append(node.name)
            row.update(source_sha256=hashlib.sha256(content).hexdigest(),
                       declared_tool_count=len(tools), declared_tools=sorted(tools))
        servers.append(row)
    packages = {}
    for name in ('mcp', 'pydantic', 'langgraph', 'numpy', 'chromadb', 'sentence-transformers', 'fastapi', 'psutil'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {'schema_version': 1, 'python': sys.version.split()[0], 'platform': platform.platform(),
            'packages': packages, 'lockfile_sha256': hashlib.sha256((root / 'uv.lock').read_bytes()).hexdigest(),
            'servers': servers, 'surfaces': {'maintained': ['React', 'CLI'],
                'experimental': ['Gradio', 'Trame', 'legacy HTML'],
                'qualified_platform_target': ['Linux', 'macOS'],
                'windows_status': 'not_qualified_for_POSIX_workers_and_locks'},
            'native_interpreters_configured': {key: bool(os.environ.get(key)) for key in
                ('APEXA_TOMO_PYTHON', 'APEXA_GSASII_PYTHON', 'APEXA_SYNTH_PYTHON')},
            'deployment_ready': 'not_established_by_source_inventory'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps(inventory(Path(__file__).resolve().parents[1]), indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result)
    else:
        print(result, end='')


if __name__ == '__main__':
    main()
