#!/usr/bin/env python3
"""Local native-CLI qualification on synthetic fixtures; no scientific solver.

Retains unsuccessful attempts. Does not alter scores, approve a scientific cohort
or enable a provider capability in an existing revision. Credentials stay in
temporary per-trial profiles and are removed by the ordinary runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(HERE), str(REPO)]
import rotation_benchmark as rb
from model_audit import ModelAudit


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm", choices=("A3", "A4", "A5"), required=True)
    parser.add_argument("--test", choices=("copy", "image", "bash"), required=True)
    parser.add_argument("--strict-writes", action="store_true",
                        help="On macOS restrict writes to the private trial root")
    parser.add_argument("--image-input", "--pi-image-input", dest="image_input", action="store_true")
    parser.add_argument("--context-lines", type=int, default=0,
                        help="Synthetic long-read probe; no scientific data")
    parser.add_argument("--settle-seconds", type=int, default=0,
                        help="Keep native CLI alive to observe delayed auxiliary API traffic")
    parser.add_argument("--base-url", default="https://apps.inside.anl.gov/argoapi/v1")
    parser.add_argument("--model", default="claudeopus5")
    args = parser.parse_args()
    if args.test == "bash" and args.arm != "A3":
        parser.error("The Bash qualification is for Claude Code's native Bash tool")
    if not 0 <= args.context_lines <= 200:
        parser.error("context-lines must be between 0 and 200")
    if not 0 <= args.settle_seconds <= 90:
        parser.error("settle-seconds must be between 0 and 90")
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    import apexa_connection
    apexa_connection.apply_saved()
    key = os.environ.get("ANL_USERNAME")
    if not key:
        raise RuntimeError("ANL_USERNAME is required; no credentials will be printed")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    setup = output / "setup"
    setup.mkdir()
    if args.test in ("copy", "bash"):
        fixture = setup / "fixture.txt"
        fixture.write_text("Synthetic file I/O.\n" + secrets.token_hex(24) + "\n")
        if args.context_lines:
            with fixture.open("a") as stream:
                for i in range(args.context_lines):
                    stream.write(f"Record {i:04d}: " + "Synthetic context for native API qualification. " * 8 + "\n")
        prompt = ("Read fixture.txt and write an exact byte-for-byte copy to "
                  "smoke_copy.txt using your tools. Then reply READY. This is "
                  "a file-I/O qualification; do not run a scientific solver.")
        if args.context_lines:
            prompt += (" First read the entire fixture with your native Read/read tool "
                       "so it enters your context. You may then use the shell to copy "
                       "the file. This tests native handling of a larger tool result.")
        if args.test == "bash":
            prompt += (" You must perform the copy with the native Bash tool using "
                       "`cp fixture.txt smoke_copy.txt`. This specifically qualifies Bash; "
                       "a copy through Write, Monitor or another tool does not qualify.")
        expected = None
    else:
        from PIL import Image, ImageDraw, ImageFont
        fixture = setup / "fixture.png"
        expected = "".join(secrets.choice("ACDEFHJKMNPRTUVWXY23456789") for _ in range(6))
        img = Image.new("RGB", (640, 160), "white")
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=64)
        draw.text((40, 40), expected, font=font, fill="black")
        img.save(fixture)
        prompt = ("Use your native image-reading tool to view fixture.png and "
                  "write the six characters shown in the image to image_answer.txt. "
                  "Read the image visually; do not use OCR, shell/code to decode it, "
                  "metadata or other files to infer the text. Then reply READY. "
                  "This synthetic image tests delivery through your native tool "
                  "and model API, not scientific analysis.")
    if args.settle_seconds:
        prompt += (f" After writing the requested output, run `sleep {args.settle_seconds}` "
                   "in your shell and wait for it to finish before replying READY. "
                   "This deliberate idle interval observes delayed native API traffic.")
    # Expected answer is outside the sandbox, never in the delivered prompt.
    receipt = {"purpose": "local post-audit adapter qualification; no scientific trials",
               "arm": args.arm, "test": args.test, "image_input": args.image_input,
               "context_lines": args.context_lines,
               "settle_seconds": args.settle_seconds,
               "strict_writes": args.strict_writes,
               "host": {"system": platform.system(), "machine": platform.machine()},
               "fixture_sha256": sha(fixture), "expected": expected,
               "source_hashes": {name: sha(HERE / name) for name in (
                   "qualify_guided_adapters.py", "rotation_benchmark.py", "run_comparison.py", "model_audit.py")},
               "model_requested": args.model, "started_unix": time.time()}
    binary = shutil.which(rb.ARMS[args.arm].harness.replace("claude-code", "claude"))
    receipt["harness"] = {"path": str(Path(binary).resolve()),
                          "launcher_sha256": sha(Path(binary).resolve()),
                          "version": subprocess.check_output([binary, "--version"], text=True).strip()}
    (setup / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    with tempfile.TemporaryDirectory(prefix="apexa-adapter-qualification-") as scratch:
        with ModelAudit(args.base_url, key, args.model, output / "model_calls.jsonl",
                        max_requests=8, max_output_tokens=2000, timeout=240) as audit:
            result = rb.run_one(rb.ARMS[args.arm], fixture, 0, 0, {}, None,
                audit.base_url, audit.token, args.model, .1, 240, output, False,
                ground_truth=setup / "no-reference.csv", extra_deny=(output,),
                structured_logs=True, scratch_root=Path(scratch), prompt_override=prompt,
                pi_image_input=args.image_input, opencode_image_input=args.image_input,
                strict_writes=args.strict_writes)
    summary = audit.summary()
    workspace = output / f"workspaces/{args.arm}_0_0"
    answer = workspace / ("image_answer.txt" if args.test == "image" else "smoke_copy.txt")
    correct = answer.is_file() and not answer.is_symlink() and (
        answer.read_bytes() == fixture.read_bytes() if args.test != "image"
        else answer.read_text().strip() == expected)
    images = [img for rec in audit.records for img in rec.get("input_images", [])]
    delivered = any(img.get("sha256") == receipt["fixture_sha256"] for img in images)
    bash_ids, successful_bash = set(), []
    if args.test == "bash":
        trace = output / f"traces/{args.arm}_0_0.stdout.txt"
        for line in trace.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            message = event.get("message", {})
            for block in message.get("content", []) if isinstance(message, dict) else []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    bash_ids.add(block.get("id"))
                elif (block.get("type") == "tool_result" and block.get("tool_use_id") in bash_ids
                      and not block.get("is_error")):
                    successful_bash.append(block["tool_use_id"])
    receipt.update(finished_unix=time.time(), return_code=result.return_code,
                   detail=result.detail, answer_correct=correct, model_audit=summary,
                   exact_image_delivered=delivered, input_images=images,
                   successful_bash_tools=successful_bash,
                   passed=result.return_code == 0 and correct and summary["qualified"]
                          and (args.test != "bash" or bool(successful_bash))
                          and (args.test != "image" or delivered))
    (output / "qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({key: receipt[key] for key in (
        "arm", "test", "passed", "return_code", "answer_correct", "exact_image_delivered")}))
    print(json.dumps({"violations": summary["violations"], "requests": summary["requests"]}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
