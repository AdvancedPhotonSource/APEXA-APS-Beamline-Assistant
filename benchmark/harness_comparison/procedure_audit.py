"""Observe automatic procedure construction without changing returned context.

Only document blocks are stored, never credentials or the whole model prompt.
The relay separately establishes whether each exact block reached a request.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time


def install(module, output):
    output = Path(output)
    seen = set()

    def save(kind, value):
        content = value.get("content", "") if isinstance(value, dict) else value
        if not isinstance(content, str) or not content:
            return
        digest = hashlib.sha256(content.encode()).hexdigest()
        if (kind, digest) in seen:
            return
        record = {"kind": kind, "sha256": digest, "content": content,
                  "prepared_unix": time.time()}
        with output.open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        seen.add((kind, digest))

    original = module.skill_context_for_tools

    def skills(*args, **kwargs):
        value = original(*args, **kwargs)
        save("automatic_skill_context", value)
        return value

    module.skill_context_for_tools = skills
    for name in ("_maybe_stage_skill_msg", "_maybe_capsule_msg"):
        fn = getattr(module.AgentRunner, name)

        def wrapper(*args, _fn=fn, _name=name, **kwargs):
            value = _fn(*args, **kwargs)
            save(_name, value)
            return value

        setattr(module.AgentRunner, name, staticmethod(wrapper))


def delivered_blocks(payload, candidates):
    """Return exact automatic blocks present in the outgoing model messages."""
    if candidates is None or not Path(candidates).is_file():
        return []

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)

    messages = payload.get("messages", [])
    texts = [(m.get("role", "unknown"), list(strings(m.get("content"))))
             for m in messages if isinstance(m, dict)]
    if "system" in payload:
        texts.append(("system", list(strings(payload["system"]))))
    found = []
    for line in Path(candidates).read_text().splitlines():
        try:
            item = json.loads(line)
        except ValueError:  # a concurrent append may not yet have its final LF
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content:
            continue
        roles = sorted({role for role, values in texts if any(content in v for v in values)})
        if roles:
            found.append({"sha256": hashlib.sha256(content.encode()).hexdigest(),
                          "kind": item["kind"], "roles": roles, "characters": len(content)})
    return found
