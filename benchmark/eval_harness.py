#!/usr/bin/env python3
"""
APEXA-Bench Evaluation Harness

Runs 50 benchmark tasks through the APEXA agent system and scores results
against ground truth. Supports multiple models and configurations.

Usage:
    uv run python benchmark/eval_harness.py --model gpt4o --config keyword
    uv run python benchmark/eval_harness.py --model claudesonnet46 --config single
    uv run python benchmark/eval_harness.py --dry-run  # validate tasks without API calls
    uv run python benchmark/eval_harness.py --all-models --all-configs  # full sweep

Results saved to benchmark/results/<model>_<config>_<timestamp>.json
"""

import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from interaction_logger import InteractionLogger


# ── Scoring ─────────────────────────────────────────────────────────────────

@dataclass
class TaskScore:
    task_id: str
    category: str
    difficulty: str
    consequence: str
    query: str
    correctness: bool = False
    safety: bool = True
    efficiency: float = 0.0
    apexa_score: float = 0.0
    tools_called: List[str] = field(default_factory=list)
    tool_count: int = 0
    optimal_count: int = 0
    agent_routed: str = ""
    wall_time_ms: int = 0
    error: str = ""
    result_preview: str = ""


CONSEQUENCE_WEIGHTS = {"I": 1.0, "C": 1.5, "P_R": 2.0, "P_I": 3.0}
ALPHA = 0.7  # correctness-efficiency balance


def check_tool_called(log_entry, expected_tools: List[str]) -> bool:
    """Check if at least one expected tool was called."""
    called = {tc.name for tc in log_entry.tool_calls}
    return bool(called & set(expected_tools))


def check_tool_chain(log_entry, expected_tools: List[str]) -> bool:
    """Check if expected tools were called in order (allowing extras between them)."""
    called_sequence = [tc.name for tc in log_entry.tool_calls]
    exp_idx = 0
    for tool in called_sequence:
        if exp_idx < len(expected_tools) and tool == expected_tools[exp_idx]:
            exp_idx += 1
    return exp_idx == len(expected_tools)


def check_params_match(log_entry, expected_params: Dict[str, Any]) -> bool:
    """Check if key parameters match expected values."""
    if not expected_params:
        return True
    for tc in log_entry.tool_calls:
        matches = all(
            tc.arguments.get(k) == v
            for k, v in expected_params.items()
            if k in tc.arguments
        )
        if matches and expected_params:
            matched_keys = [k for k in expected_params if k in tc.arguments]
            if matched_keys:
                return True
    return not expected_params


def compute_efficiency(actual: int, optimal: int) -> float:
    """Compute tool-call efficiency: optimal/actual, capped at 1.0."""
    if actual == 0:
        return 0.0
    return min(1.0, optimal / actual)


def compute_apexa_score(correctness: bool, safety: bool,
                        efficiency: float, consequence: str) -> float:
    """Composite APEXA-Score per Equation 1 in the paper."""
    w = CONSEQUENCE_WEIGHTS.get(consequence, 1.0)
    c = 1.0 if correctness else 0.0
    s = 1.0 if safety else 0.0
    return w * c * s * (ALPHA + (1 - ALPHA) * efficiency)


def score_task(task: Dict, log_entry, result: str) -> TaskScore:
    """Score a single benchmark task against ground truth."""
    check_type = task.get("ground_truth_check", "tool_called")
    expected_tools = task.get("expected_tools", [])
    expected_params = task.get("expected_params", {})
    expected_keywords_any = task.get("expected_keywords_any", [])
    optimal = task.get("optimal_tool_calls", len(expected_tools))

    if check_type == "tool_called":
        correct = check_tool_called(log_entry, expected_tools)
    elif check_type == "tool_chain":
        correct = check_tool_chain(log_entry, expected_tools)
    elif check_type == "params_match":
        correct = (check_tool_called(log_entry, expected_tools) and
                   check_params_match(log_entry, expected_params))
    elif check_type == "output_contains":
        correct = any(
            task.get("expected_output", "") in (result or "")
            for _ in [1]
        )
    elif check_type == "tool_or_keywords":
        # Knowledge tasks: pass if the tool was called OR the answer contains
        # at least one expected keyword (case-insensitive). This avoids
        # false-failing frontier models that answer correctly from priors.
        tool_ok = check_tool_called(log_entry, expected_tools)
        text = (result or "").lower()
        kw_ok = any(kw.lower() in text for kw in expected_keywords_any)
        correct = tool_ok or kw_ok
    else:
        correct = check_tool_called(log_entry, expected_tools)

    # Also check params if specified
    if correct and expected_params:
        correct = correct and check_params_match(log_entry, expected_params)

    actual_tools = [tc.name for tc in log_entry.tool_calls]
    n_actual = len(actual_tools)
    eff = compute_efficiency(n_actual, optimal)
    safety = True  # standard tasks are not adversarial

    score_val = compute_apexa_score(correct, safety, eff, task["consequence"])

    return TaskScore(
        task_id=task["id"],
        category=task["category"],
        difficulty=task["difficulty"],
        consequence=task["consequence"],
        query=task["query"],
        correctness=correct,
        safety=safety,
        efficiency=round(eff, 3),
        apexa_score=round(score_val, 3),
        tools_called=actual_tools,
        tool_count=n_actual,
        optimal_count=optimal,
        agent_routed=log_entry.agent,
        wall_time_ms=log_entry.total_duration_ms,
        result_preview=(result or "")[:200],
    )


# ── Aggregation ─────────────────────────────────────────────────────────────

def aggregate_results(scores: List[TaskScore]) -> Dict:
    """Compute aggregate metrics matching the paper's tables."""
    total = len(scores)
    if total == 0:
        return {}

    correct = sum(1 for s in scores if s.correctness)
    avg_eff = sum(s.efficiency for s in scores) / total
    avg_score = sum(s.apexa_score for s in scores) / total

    # Per-category
    categories = {}
    for s in scores:
        cat = s.category
        if cat not in categories:
            categories[cat] = {"total": 0, "correct": 0, "scores": [],
                               "efficiencies": [], "times": []}
        categories[cat]["total"] += 1
        categories[cat]["correct"] += 1 if s.correctness else 0
        categories[cat]["scores"].append(s.apexa_score)
        categories[cat]["efficiencies"].append(s.efficiency)
        categories[cat]["times"].append(s.wall_time_ms)

    per_category = {}
    for cat, data in categories.items():
        per_category[cat] = {
            "success_rate": round(100 * data["correct"] / data["total"], 1),
            "avg_apexa_score": round(sum(data["scores"]) / data["total"], 3),
            "avg_efficiency": round(sum(data["efficiencies"]) / data["total"], 3),
            "avg_time_ms": round(sum(data["times"]) / data["total"], 0),
            "n_tasks": data["total"],
        }

    # Per-difficulty
    difficulties = {}
    for s in scores:
        d = s.difficulty
        if d not in difficulties:
            difficulties[d] = {"total": 0, "correct": 0}
        difficulties[d]["total"] += 1
        difficulties[d]["correct"] += 1 if s.correctness else 0

    per_difficulty = {
        d: {"success_rate": round(100 * data["correct"] / data["total"], 1),
            "n_tasks": data["total"]}
        for d, data in difficulties.items()
    }

    return {
        "overall": {
            "success_rate": round(100 * correct / total, 1),
            "avg_efficiency": round(avg_eff, 3),
            "avg_apexa_score": round(avg_score, 3),
            "total_tasks": total,
            "correct_tasks": correct,
        },
        "per_category": per_category,
        "per_difficulty": per_difficulty,
    }


# ── Dry Run ─────────────────────────────────────────────────────────────────

def dry_run(tasks: List[Dict]):
    """Validate task definitions without making API calls."""
    print(f"\n{'='*60}")
    print(f"DRY RUN — Validating {len(tasks)} benchmark tasks")
    print(f"{'='*60}\n")

    categories = {}
    difficulties = {}
    consequences = {}

    for task in tasks:
        cat = task["category"]
        diff = task["difficulty"]
        cons = task["consequence"]

        categories[cat] = categories.get(cat, 0) + 1
        difficulties[diff] = difficulties.get(diff, 0) + 1
        consequences[cons] = consequences.get(cons, 0) + 1

        # Validate structure
        required = ["id", "category", "difficulty", "consequence", "query",
                     "expected_tools", "ground_truth_check"]
        missing = [f for f in required if f not in task]
        if missing:
            print(f"  WARNING: {task['id']} missing fields: {missing}")

    print("Category distribution:")
    for cat, count in sorted(categories.items()):
        print(f"  {cat:15s}: {count} tasks")

    print(f"\nDifficulty distribution:")
    for diff, count in sorted(difficulties.items()):
        print(f"  {diff}: {count} tasks")

    print(f"\nConsequence distribution:")
    for cons, count in sorted(consequences.items()):
        print(f"  {cons:4s}: {count} tasks")

    print(f"\nTotal: {len(tasks)} tasks")
    print(f"\nAll tasks validated successfully.")


# ── Main Runner ─────────────────────────────────────────────────────────────

async def run_evaluation(model: str, config: str, tasks: List[Dict],
                         output_dir: str):
    """Run all benchmark tasks through APEXA and score results."""
    from argo_mcp_client import APEXAClient

    print(f"\n{'='*60}")
    print(f"APEXA-Bench Evaluation")
    print(f"  Model:  {model}")
    print(f"  Config: {config}")
    print(f"  Tasks:  {len(tasks)}")
    print(f"{'='*60}\n")

    # Set up client
    client = APEXAClient.__new__(APEXAClient)
    client.sessions = {}
    client.exit_stack = AsyncExitStack()
    client._tool_registry = {}
    client._available_tools = []
    client.orchestrator = None
    client.anl_username = os.getenv("ANL_USERNAME")
    client.selected_model = model

    from argo_mcp_client import (ExperimentContext, BatchProcessor,
                                  ErrorPreventor, WorkflowBuilder,
                                  SmartCache, ImageAnalyzer,
                                  RealtimeFeedback, PlottingEngine)
    client.context = ExperimentContext()
    client.batch_processor = BatchProcessor()
    client.error_preventor = ErrorPreventor()
    client.workflow_builder = WorkflowBuilder()
    client.cache = SmartCache()
    client.image_analyzer = ImageAnalyzer()
    client.realtime_feedback = RealtimeFeedback()
    client.plotting = PlottingEngine()
    client.environment = "PROD"

    # Read server configs (colon-separated text format: name:path/to/server.py)
    servers_config = Path(__file__).parent.parent / "servers.config"
    server_configs = []
    if servers_config.exists():
        with open(servers_config) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                name, _, script_path = line.partition(":")
                name = name.strip()
                script_path = script_path.strip()
                full_path = Path(__file__).parent.parent / script_path
                if full_path.exists():
                    server_configs.append({"name": name,
                                            "script_path": str(full_path)})
    else:
        print("ERROR: servers.config not found")
        return

    async with client.exit_stack:
        await client.connect_to_multiple_servers(server_configs)

        if config == "autogen":
            # External baseline: AutoGen v0.7 AssistantAgent + custom Argo
            # ChatCompletionClient. Same MCP tool surface, different agent
            # framework on top — answers "does an off-the-shelf multi-turn
            # agent framework match APEXA's purpose-built orchestrator?"
            from apexa_autogen_baseline import run_autogen_task
            logger = InteractionLogger()
            scores = []
            for i, task in enumerate(tasks):
                print(f"  [{i+1}/{len(tasks)}] {task['id']}: {task['query'][:60]}...")
                log_entry = logger.start(task["query"], model=model)
                log_entry.set_agent("AutoGenBaseline")
                try:
                    result = await run_autogen_task(
                        query=task["query"],
                        model=model,
                        available_tools=client._available_tools,
                        execute_tool_fn=client.execute_tool_call,
                        log_entry=log_entry,
                    )
                    n_calls = len(log_entry.tool_calls)
                    looped = n_calls > 3 and len(set(
                        tc.name for tc in log_entry.tool_calls)) == 1
                    log_entry.finish(result, iterations=n_calls, looped=looped)
                except Exception as e:
                    result = f"Error: {e}"
                    log_entry.finish(result, iterations=0, looped=False)

                score = score_task(task, log_entry, result)
                scores.append(score)
                status = "PASS" if score.correctness else "FAIL"
                print(f"         {status} | tools: {score.tools_called} | "
                      f"eff: {score.efficiency:.2f} | score: {score.apexa_score:.2f}")

        elif config == "single":
            # Single-agent mode: bypass orchestrator, one agent with all tools
            from apexa_agents import APEXAAgent, AgentRunner, ArgoProvider
            single_agent = APEXAAgent(
                name="SingleAgent",
                instructions="You are a general-purpose beamline assistant with access to all tools. Use the appropriate tool for each task.",
                tool_names=[],
                temperature=0.5,
            )

            runner = AgentRunner(client.execute_tool_call)
            logger = InteractionLogger()

            scores = []
            for i, task in enumerate(tasks):
                print(f"  [{i+1}/{len(tasks)}] {task['id']}: {task['query'][:60]}...")

                provider = ArgoProvider(client.anl_username, model)
                log_entry = logger.start(task["query"], model=model)
                log_entry.set_agent("SingleAgent")

                try:
                    result = await runner.run(
                        single_agent, task["query"], provider,
                        client._available_tools, None,
                        log_entry=log_entry,
                    )
                    n_calls = len(log_entry.tool_calls)
                    looped = n_calls > 3 and len(set(
                        tc.name for tc in log_entry.tool_calls)) == 1
                    log_entry.finish(result, iterations=n_calls, looped=looped)
                except Exception as e:
                    result = f"Error: {e}"
                    log_entry.finish(result, iterations=0, looped=False)

                score = score_task(task, log_entry, result)
                scores.append(score)
                status = "PASS" if score.correctness else "FAIL"
                print(f"         {status} | tools: {score.tools_called} | "
                      f"eff: {score.efficiency:.2f} | score: {score.apexa_score:.2f}")

        else:
            # Multi-agent mode (keyword or dspy)
            if config == "dspy":
                # Replace the keyword orchestrator with the DSPy one before
                # running tasks. DSPyOrchestrator subclasses OrchestratorAgent
                # and overrides _route() only — execution path is identical.
                from apexa_dspy_router import DSPyOrchestrator
                client.orchestrator = DSPyOrchestrator(
                    client.execute_tool_call,
                    client._available_tools,
                    context=getattr(client, "context", None),
                    router_model=model,
                )

            scores = []
            for i, task in enumerate(tasks):
                print(f"  [{i+1}/{len(tasks)}] {task['id']}: {task['query'][:60]}...")

                # Clear history between tasks for independent evaluation
                client.orchestrator.clear_history()

                # Capture log entry from orchestrator
                log_before = client.orchestrator.logger.recent(1)

                try:
                    result = await client.run_query(task["query"], use_history=False)
                except Exception as e:
                    result = f"Error: {e}"

                # Get the log entry that was just saved
                log_after = client.orchestrator.logger.recent(1)
                if log_after:
                    log_data = log_after[0]
                    # Reconstruct a minimal log_entry for scoring
                    from interaction_logger import InteractionEntry, ToolCallRecord
                    log_entry = InteractionEntry(
                        id=log_data.get("id", ""),
                        timestamp=log_data.get("timestamp", ""),
                        user=log_data.get("user", ""),
                        model=model,
                        query=task["query"],
                        agent=log_data.get("agent", ""),
                        iterations=log_data.get("iterations", 0),
                        total_duration_ms=log_data.get("total_duration_ms", 0),
                    )
                    for tc_data in log_data.get("tool_calls", []):
                        log_entry.tool_calls.append(ToolCallRecord(
                            name=tc_data["name"],
                            arguments=tc_data.get("arguments", {}),
                            success=tc_data.get("success", True),
                            duration_ms=tc_data.get("duration_ms", 0),
                            result_preview=tc_data.get("result_preview", ""),
                        ))
                else:
                    # Fallback: create empty log entry
                    log_entry = InteractionLogger().start(task["query"], model=model)
                    log_entry.finish(result, 0)

                score = score_task(task, log_entry, result)
                scores.append(score)
                status = "PASS" if score.correctness else "FAIL"
                print(f"         {status} | agent: {score.agent_routed} | "
                      f"tools: {score.tools_called} | "
                      f"eff: {score.efficiency:.2f} | score: {score.apexa_score:.2f}")

    # Aggregate and save
    agg = aggregate_results(scores)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = Path(output_dir) / f"{model}_{config}_{timestamp}.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "metadata": {
            "model": model,
            "config": config,
            "timestamp": timestamp,
            "n_tasks": len(tasks),
        },
        "aggregate": agg,
        "per_task": [asdict(s) for s in scores],
    }

    with open(output_file, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n{'='*60}")
    print(f"Results saved to: {output_file}")
    print(f"\nOverall: {agg['overall']['success_rate']}% success | "
          f"APEXA-Score: {agg['overall']['avg_apexa_score']:.3f}")
    print(f"\nPer category:")
    for cat, data in agg["per_category"].items():
        print(f"  {cat:15s}: {data['success_rate']:5.1f}% | "
              f"APEXA-Score: {data['avg_apexa_score']:.3f}")
    print(f"\nPer difficulty:")
    for diff, data in agg["per_difficulty"].items():
        print(f"  {diff}: {data['success_rate']:5.1f}% ({data['n_tasks']} tasks)")
    print(f"{'='*60}")

    return output


def main():
    parser = argparse.ArgumentParser(description="APEXA-Bench Evaluation Harness")
    parser.add_argument("--model", default="gpt4o",
                        help="Model to evaluate (default: gpt4o)")
    parser.add_argument("--config", default="keyword",
                        choices=["single", "keyword", "dspy", "autogen"],
                        help="Agent configuration (default: keyword)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate tasks without API calls")
    parser.add_argument("--tasks", default=None,
                        help="Path to benchmark tasks JSON (default: benchmark/benchmark_tasks.json)")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: benchmark/results/)")
    parser.add_argument("--category", default=None,
                        help="Run only tasks in this category")
    parser.add_argument("--difficulty", default=None,
                        help="Run only tasks at this difficulty level")
    parser.add_argument("--all-models", action="store_true",
                        help="Run all 4 models (gpt5mini, gpt54, claudeopus47, gemini35flash)")
    parser.add_argument("--all-configs", action="store_true",
                        help="Run all 4 configs (single, keyword, dspy, autogen)")

    args = parser.parse_args()

    # Load tasks
    tasks_file = args.tasks or str(
        Path(__file__).parent / "benchmark_tasks.json")
    with open(tasks_file) as f:
        data = json.load(f)
    tasks = data["tasks"]

    # Filter
    if args.category:
        tasks = [t for t in tasks if t["category"] == args.category]
    if args.difficulty:
        tasks = [t for t in tasks if t["difficulty"] == args.difficulty]

    if not tasks:
        print("No tasks match the filter criteria.")
        return

    output_dir = args.output_dir or str(Path(__file__).parent / "results")

    if args.dry_run:
        dry_run(tasks)
        return

    models = (["gpt5mini", "gpt54", "claudeopus47", "gemini35flash"]
              if args.all_models else [args.model])
    configs = (["single", "keyword", "dspy", "autogen"]
               if args.all_configs else [args.config])

    for model in models:
        for config in configs:
            asyncio.run(run_evaluation(model, config, tasks, output_dir))


if __name__ == "__main__":
    main()
