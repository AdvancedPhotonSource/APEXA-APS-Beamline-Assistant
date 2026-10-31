#!/usr/bin/env python3
"""
APEXA Interaction Logger — Tier 1 RL foundation

Logs every query→agent→tool_calls→result interaction to a local JSONL file.
This data enables:
  1. Offline prompt optimization (DSPy)
  2. Routing classifier training (replace keyword scoring)
  3. Tool-chain RL (reward modeling from success/failure patterns)

Design:
  - Append-only JSONL (one JSON object per line, easy to parse)
  - Stored in ~/.apexa/logs/ (survives git updates, per-machine)
  - Captures tool call timing, success, and loop detection
  - Persist metadata only: prompts, usernames, arguments and result text are omitted

Usage:
    logger = InteractionLogger()
    entry = logger.start(query, user, model)
    entry.set_agent(agent_name)
    entry.add_tool_call(name, args, result, success, duration_ms)
    entry.finish(final_result, iterations)
    logger.save(entry)
"""

import json
import os
import time
import uuid
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ToolCallRecord:
    name: str
    arguments: Dict[str, Any]
    success: bool
    duration_ms: int
    result_preview: str = ""    # first 200 chars of result


@dataclass
class InteractionEntry:
    id: str
    timestamp: str
    user: str
    model: str
    query: str
    agent: str = ""
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    iterations: int = 0
    looped: bool = False
    final_result: str = ""
    total_duration_ms: int = 0
    _start_time: float = field(default=0.0, repr=False)

    def set_agent(self, name: str):
        self.agent = name

    def add_tool_call(self, name: str, arguments: Dict[str, Any],
                      result: str, success: bool, duration_ms: int):
        self.tool_calls.append(ToolCallRecord(
            name=name,
            arguments=arguments,
            success=success,
            duration_ms=duration_ms,
            result_preview=result[:200] if result else "",
        ))

    def finish(self, final_result: str, iterations: int, looped: bool = False):
        self.final_result = final_result[:500] if final_result else ""
        self.iterations = iterations
        self.looped = looped
        self.total_duration_ms = int((time.time() - self._start_time) * 1000)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "user": self.user,
            "model": self.model,
            "query": self.query,
            "agent": self.agent,
            "tool_calls": [
                {
                    "name": tc.name,
                    "arguments": tc.arguments,
                    "success": tc.success,
                    "duration_ms": tc.duration_ms,
                    "result_preview": tc.result_preview,
                }
                for tc in self.tool_calls
            ],
            "iterations": self.iterations,
            "looped": self.looped,
            "total_duration_ms": self.total_duration_ms,
            # Derived quality signals for RL reward modeling
            "n_tool_calls": len(self.tool_calls),
            "all_tools_succeeded": all(tc.success for tc in self.tool_calls),
            "unique_tools_used": len(set(tc.name for tc in self.tool_calls)),
        }


class InteractionLogger:
    """Append-only JSONL logger for APEXA interactions."""

    def __init__(self, log_dir: Optional[str] = None):
        if log_dir:
            self.log_dir = Path(log_dir)
        else:
            self.log_dir = Path.home() / ".apexa" / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "interactions.jsonl"

    def start(self, query: str, user: str = "", model: str = "") -> InteractionEntry:
        """Create a new interaction entry. Call this at the start of each query."""
        if not user:
            user = os.environ.get("ANL_USERNAME", os.environ.get("USER", "unknown"))
        return InteractionEntry(
            id=str(uuid.uuid4())[:8],
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            user=user,
            model=model,
            query=query,
            _start_time=time.time(),
        )

    def save(self, entry: InteractionEntry):
        """Append operational metadata without scientist content or credentials."""
        try:
            record = entry.to_dict()
            record["query_chars"] = len(record.pop("query", ""))
            record.pop("user", None)
            for tool in record["tool_calls"]:
                tool.pop("arguments", None)
                tool.pop("result_preview", None)
            record["content_policy"] = "metadata_only"
            from apexa_observability import current_ids
            record["trace_id"] = current_ids().get("trace_id")
            with open(self.log_file, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            logging.getLogger(__name__).warning("Interaction metadata write failed (%s)", type(exc).__name__)

    def recent(self, n: int = 20) -> List[Dict]:
        """Read the last n entries (for dashboarding / reflection)."""
        if not self.log_file.exists():
            return []
        entries = deque(maxlen=max(1, min(n, 10000)))
        try:
            with open(self.log_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
            return list(entries)
        except Exception:
            return []

    def stats(self) -> Dict:
        """Compute aggregate stats from the log (for prompt optimization)."""
        entries = self.recent(1000)
        if not entries:
            return {"total": 0}

        total = len(entries)
        looped = sum(1 for e in entries if e.get("looped"))
        avg_tools = sum(e.get("n_tool_calls", 0) for e in entries) / total
        avg_iters = sum(e.get("iterations", 0) for e in entries) / total
        all_succeeded = sum(1 for e in entries if e.get("all_tools_succeeded"))

        # Per-agent breakdown
        agent_counts: Dict[str, int] = {}
        agent_loops: Dict[str, int] = {}
        for e in entries:
            agent = e.get("agent", "unknown")
            agent_counts[agent] = agent_counts.get(agent, 0) + 1
            if e.get("looped"):
                agent_loops[agent] = agent_loops.get(agent, 0) + 1

        return {
            "total": total,
            "looped": looped,
            "loop_rate": f"{100*looped/total:.1f}%",
            "avg_tool_calls": round(avg_tools, 1),
            "avg_iterations": round(avg_iters, 1),
            "success_rate": f"{100*all_succeeded/total:.1f}%",
            "agent_counts": agent_counts,
            "agent_loop_rates": {
                a: f"{100*agent_loops.get(a,0)/c:.0f}%"
                for a, c in agent_counts.items()
            },
        }
