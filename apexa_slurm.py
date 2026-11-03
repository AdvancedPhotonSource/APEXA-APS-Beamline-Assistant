"""Typed integration jobs owned by Slurm on the data host.

Submission intent precedes sbatch. A lost submission reply is reconciled by
the full content-derived job name and Unix owner, never by resubmitting.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import getpass
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
import logging

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from apexa_sessions import file_lock


class IntegrationRequest(BaseModel):
    """Closed remote protocol, validated before any scheduler effect."""
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    parameter_file: str
    images: list[str] | None = None
    image_dir: str | None = None
    pattern: str = "*.h5"
    exclude_substring: str = "dark"
    dark_file: str | None = None
    dark_dir: str | None = None
    dark_pattern: str = "*dark*"
    dark_kind: Literal["after", "before", "any"] = "after"
    dark_source: Literal["file", "embedded", "none"] = "file"
    data_location: str | None = None
    dark_location: str | None = None
    max_files: int | None = Field(default=None, ge=1)
    result_folder: str | None = None
    n_cpus: int = Field(default=8, ge=1)
    n_parallel: int | None = Field(default=None, ge=1)
    compute_target: Literal["remote"]
    convert_files: bool = True
    short_names: bool = True
    csv_output: bool = False
    r_min: float | None = None
    r_max: float | None = None
    r_bin_size: float | None = Field(default=None, gt=0)
    eta_min: float | None = None
    eta_max: float | None = None
    eta_bin_size: float | None = Field(default=None, gt=0)
    two_theta_min: float | None = None
    two_theta_max: float | None = None
    q_min: float | None = None
    q_max: float | None = None
    n_channels: int | None = Field(default=None, ge=1)


def digest_file(path):
    path = Path(path)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Input changed while hashing; stage closed immutable files")
    return digest.hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load_policy(path):
    data = json.loads(Path(path).read_text())
    required = {"run_root", "data_roots", "apexa_root", "python", "midas_path", "engine_sha256", "cpus", "memory_gb", "minutes"}
    if not required <= data.keys() or set(data) - required - {"partition", "account"}:
        raise ValueError("Invalid Slurm policy fields")
    for key in ("run_root", "apexa_root", "python", "midas_path"):
        if not Path(data[key]).is_absolute():
            raise ValueError("Slurm policy paths must be absolute")
    if not isinstance(data["data_roots"], list) or not data["data_roots"] or any(not Path(p).is_absolute() for p in data["data_roots"]):
        raise ValueError("Configure absolute proposal data roots")
    for key in ("cpus", "memory_gb", "minutes"):
        if type(data[key]) is not int or not 1 <= data[key] <= 100000:
            raise ValueError("Invalid Slurm resource budget")
    if not re.fullmatch(r"[a-f0-9]{64}", data["engine_sha256"]):
        raise ValueError("Pin the qualified integration script SHA-256")
    for key in ("partition", "account"):
        if data.get(key) and not re.fullmatch(r"[A-Za-z0-9_.-]+", data[key]):
            raise ValueError("Invalid scheduler account or partition")
    return data


def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=True)
    if len(result.stdout) > 4_000_000:
        raise ValueError("Scheduler reply exceeds the response budget")
    return result.stdout


class SlurmSupervisor:
    def __init__(self, policy, policy_path, *, run=command):
        self.policy, self.policy_path, self.run = policy, str(Path(policy_path).resolve()), run
        self.root = Path(policy["run_root"])
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, job):
        if not isinstance(job, str) or not re.fullmatch(r"[a-f0-9]{64}", job):
            raise ValueError("Invalid APEXA remote job ID")
        return self.root / job

    def input_path(self, value):
        if not isinstance(value, (str, Path)) or not Path(value).is_absolute():
            raise ValueError("Remote inputs must use absolute data-host paths")
        path = Path(value).resolve(strict=True)
        if not any(path.is_relative_to(Path(root).resolve()) for root in self.policy["data_roots"]):
            raise ValueError("Input is outside the configured proposal roots")
        return path

    def submit(self, args, job_id=None):
        if job_id is not None:
            # Recover a coordinator admission BEFORE looking at mutable inputs
            # or an upgraded engine. A retry must never become a second job.
            folder = self.path(job_id)
            folder.mkdir(exist_ok=True)
            with file_lock(folder / ".admit.lock"):
                if (folder / "job.json").exists():
                    record = json.loads((folder / "job.json").read_text())
                    if record.get("request_sha256") != hashlib.sha256(_canonical(args).encode()).hexdigest():
                        raise ValueError("Remote request identity was reused with different arguments")
                    if record["owner"] != getpass.getuser():
                        raise ValueError("Remote job belongs to another Unix identity")
                    return self.public(record)
                return self._submit(args, job_id)
        return self._submit(args)

    def _submit(self, args, job_id=None):
        request_digest = hashlib.sha256(_canonical(args).encode()).hexdigest()
        args = IntegrationRequest.model_validate(args).model_dump()
        # Only the existing typed scientific tool is available to this worker.
        if args.get("compute_target") != "remote":
            raise ValueError("Remote integration requires an explicit remote compute target")
        parameter = self.input_path(args["parameter_file"])
        if args.get("images"):
            images = [self.input_path(p) for p in args["images"]]
        elif args.get("image_dir"):
            pattern = args.get("pattern") or "*.h5"
            if "/" in pattern or "\\" in pattern or ".." in pattern:
                raise ValueError("Use a filename-only sample pattern")
            images = sorted(self.input_path(p) for p in self.input_path(args["image_dir"]).glob(pattern) if p.is_file())
        else:
            raise ValueError("Supply explicit images or a data-host image directory")
        if not images or len(images) > 10000:
            raise ValueError("Remote integration requires 1–10000 staged files")
        inputs = {str(p) for p in images} | {str(parameter)}
        if args.get("dark_file"):
            args["dark_file"] = str(self.input_path(args["dark_file"]))
            inputs.add(args["dark_file"])
        if args.get("dark_dir"):
            raise ValueError("Remote work requires an explicit shared dark_file or embedded dark; omit dark_dir")
        if args.get("dark_source", "file") == "file" and not args.get("dark_file"):
            # Force an explicit correction contract for unattended remote work.
            raise ValueError("Remote integration requires an explicit shared dark_file, embedded dark, or dark_source=none")
        engine = Path(self.policy["midas_path"]) / "FF_HEDM/workflows/integrator.py"
        if digest_file(engine) != self.policy["engine_sha256"]:
            raise ValueError("Integration engine differs from the pinned site policy")
        args = dict(args, images=[str(p) for p in images], image_dir=None, parameter_file=str(parameter))
        if args.get("result_folder"):
            raise ValueError("Remote outputs use an owned directory per job; omit result_folder")
        args["n_cpus"] = min(max(1, int(args.get("n_cpus") or 1)), self.policy["cpus"])
        args["n_parallel"] = min(max(1, int(args.get("n_parallel") or 1)), max(1, self.policy["cpus"] // args["n_cpus"]))
        frozen = {"tool": "midas_integrate_series", "arguments": args,
                  "inputs": {p: digest_file(p) for p in sorted(inputs)}, "policy": self.policy,
                  "worker_sha256": digest_file(__file__)}
        job = job_id or hashlib.sha256(_canonical(frozen).encode()).hexdigest()
        folder = self.path(job)
        folder.mkdir(exist_ok=True)
        with file_lock(folder / ".lock"):
            record_path = folder / "job.json"
            if record_path.exists():
                return self.public(json.loads(record_path.read_text()))
            record = {"job_id": job, "scheduler_id": None, "created": time.time(), "owner": getpass.getuser(),
                      "state": "submitting", "cancellation": "not_requested", "frozen": frozen,
                      "request_sha256": request_digest}
            atomic_json(record_path, record)  # durable intent BEFORE sbatch
            script = folder / "job.sh"
            worker = Path(self.policy["apexa_root"]) / "scripts/slurm_worker.py"
            invocation = [self.policy["python"], str(worker), "--config", self.policy_path, "--execute", job]
            script.write_text("#!/bin/sh\nexec " + shlex.join(invocation) + "\n", encoding="utf-8")
            argv = ["sbatch", "--parsable", "--job-name=apexa-" + job,
                    "--cpus-per-task=" + str(self.policy["cpus"]), "--mem=" + str(self.policy["memory_gb"]) + "G",
                    "--time=" + str(self.policy["minutes"]), "--output=" + str(folder / "scheduler.log")]
            for key in ("account", "partition"):
                if self.policy.get(key):
                    argv.append("--" + key + "=" + self.policy[key])
            try:
                reply = self.run([*argv, str(script)]).strip().split(";")[0]
                if not re.fullmatch(r"[0-9]+", reply):
                    raise ValueError("Unrecognized scheduler job ID")
                record.update(scheduler_id=reply, state="submitted")
            except Exception:
                record["state"] = "unknown"  # reconcile; never blindly retry
            atomic_json(record_path, record)
            return self.public(record)

    def public(self, record):
        status = {"completed": "success", "failed": "error", "submitting": "unknown"}.get(record["state"], record["state"])
        return {"tool": "midas_integrate_series", "status": status, "job_id": record["job_id"],
                "job_ids": [record["scheduler_id"]] if record["scheduler_id"] else [],
                "scheduler_id": record["scheduler_id"], "supervisor": "slurm", "cancellation_status": record["cancellation"],
                "scientific_verdict": "unassessed", "result_folder": str(self.path(record["job_id"]) / "outputs"),
                "result": record.get("result"), "execution": "completed" if record["state"] == "completed" else record["state"]}

    def status(self, job, *, cancel=False):
        folder = self.path(job)
        with file_lock(folder / ".lock"):
            record = json.loads((folder / "job.json").read_text())
            if record["owner"] != getpass.getuser():
                raise ValueError("Remote job belongs to another Unix identity")
            # Query both queues: absence in squeue alone never means completion.
            rows = []
            for argv in (["squeue", "--noheader", "--user", record["owner"], "--format=%i|%128j|%u|%T"],
                         ["sacct", "-n", "-X", "-P", "--user", record["owner"], "--starttime", datetime.fromtimestamp(record["created"] - 60).isoformat(timespec="seconds"), "--format=JobIDRaw,JobName%128,User%64,State%32,ExitCode"]):
                try:
                    rows.extend([part.strip() for part in line.split("|")] for line in self.run(argv).splitlines())
                except Exception:
                    continue
            matches = [r for r in rows if len(r) >= 4 and r[1] == "apexa-" + job and r[2] == record["owner"] and re.fullmatch(r"[0-9]+", r[0])]
            ids = {r[0] for r in matches}
            if len(ids) == 1 and (record["scheduler_id"] is None or record["scheduler_id"] in ids):
                record["scheduler_id"] = next(iter(ids))
                state = matches[0][3].split()[0].rstrip("+")
                if state in {"PENDING", "CONFIGURING", "RUNNING", "COMPLETING", "SUSPENDED"}:
                    record["state"] = "running" if state != "PENDING" else "submitted"
                elif state.startswith("CANCELLED"):
                    record.update(state="cancelled", cancellation="stopped")
                elif state in {"FAILED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED"}:
                    record["state"] = "failed"
                elif state == "COMPLETED":
                    record["state"] = "unknown"  # needs a verified worker receipt
            elif len(ids) > 1 or (ids and record["scheduler_id"] not in ids):
                record["state"] = "unknown"
                atomic_json(folder / "job.json", record)
                return self.public(record)
            if cancel and record["scheduler_id"] and record["state"] not in {"completed", "failed", "cancelled"}:
                record["cancellation"] = "requested"
                atomic_json(folder / "job.json", record)
                self.run(["scancel", record["scheduler_id"]])
            receipt_path = folder / "result.json"
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if receipt.get("job_id") != job or receipt.get("state") not in {"completed", "failed"}:
                    raise ValueError("Worker receipt identity mismatch")
                outputs = receipt.get("outputs", {})
                intact = bool(outputs)
                for name, digest in outputs.items():
                    output = (folder / "outputs" / name).resolve()
                    if not output.is_relative_to((folder / "outputs").resolve()):
                        raise ValueError("Invalid output manifest path")
                    intact = intact and output.is_file() and digest_file(output) == digest
                receipt_state = receipt["state"] if intact or receipt["state"] == "failed" else "unknown"
                # A completed receipt conflicts with a scheduler failure or
                # cancellation; preserve the uncertainty for investigation.
                if receipt_state == "completed" and record["state"] in {"failed", "cancelled"}:
                    receipt_state = "unknown"
                record.update(state=receipt_state, result=receipt["result"])
            atomic_json(folder / "job.json", record)
            return self.public(record)

    def execute(self, job):
        """Scheduled worker. A requeue/restart never repeats a possible effect."""
        folder = self.path(job)
        with file_lock(folder / ".execute.lock", blocking=False):
            record = json.loads((folder / "job.json").read_text())
            if (folder / "started.json").exists():
                raise ValueError("This job may already have executed; reconcile instead of retrying")
            frozen = record["frozen"]
            if frozen["policy"] != self.policy or frozen["worker_sha256"] != digest_file(__file__):
                raise ValueError("Worker or policy changed after admission")
            for path, digest in frozen["inputs"].items():
                if digest_file(self.input_path(path)) != digest:
                    raise ValueError("An input changed after admission")
            engine = Path(self.policy["midas_path"]) / "FF_HEDM/workflows/integrator.py"
            if digest_file(engine) != self.policy["engine_sha256"]:
                raise ValueError("Engine changed after admission")
            atomic_json(folder / "started.json", {"job_id": job, "started": time.time()})
            # This is an isolated Slurm process, never the coordinator process.
            os.environ.update(MIDAS_PATH=self.policy["midas_path"], APEXA_FORCE_REMOTE_EXEC="0")
            import contextlib
            with (folder / "worker.log").open("a") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                import midas_comprehensive_server as midas
                arguments = {**frozen["arguments"], "compute_target": "local", "result_folder": str(folder / "outputs")}
                inspect.signature(midas.midas_integrate_series).bind(**arguments)
                result = json.loads(asyncio.run(midas.midas_integrate_series(**arguments)))
            for path, digest in frozen["inputs"].items():
                if digest_file(path) != digest:
                    raise ValueError("Inputs changed during execution; receipt cannot be accepted")
            state = "completed" if result.get("status") == "success" else "failed"
            outputs = {str(p.relative_to(folder / "outputs")): digest_file(p)
                       for p in (folder / "outputs").rglob("*") if p.is_file()}
            atomic_json(folder / "result.json", {"job_id": job, "state": state, "result": result, "outputs": outputs})


def coordinator_root():
    root = Path(os.environ.get("APEXA_HOME", str(Path.home() / ".apexa"))) / "remote_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_target():
    config_path = os.environ.get("APEXA_SLURM_CONFIG")
    if not config_path:
        raise ValueError("Configure APEXA_SLURM_CONFIG for the data-host Slurm worker; no remote job was submitted")
    target = json.loads(Path(config_path).read_text())
    if set(target) != {"ssh_alias", "python", "worker_script", "config_file"} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", target["ssh_alias"]):
        raise ValueError("Invalid Slurm SSH target configuration")
    if any(not str(target[k]).startswith("/") for k in ("python", "worker_script", "config_file")):
        raise ValueError("Remote interpreter, worker and policy paths must be absolute")
    return target


async def request_remote(action, *, arguments=None, job_id=None):
    from apexa_subprocess import run
    if action not in {"submit", "status", "cancel"}:
        raise ValueError("Unknown remote job action")
    target = load_target()
    root = coordinator_root()
    if action == "submit":
        IntegrationRequest.model_validate(arguments)
        job_id = hashlib.sha256(_canonical({"target": target, "arguments": arguments}).encode()).hexdigest()
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{64}", job_id):
        raise ValueError("Invalid APEXA remote job ID")
    path = root / (job_id + ".json")
    with file_lock(root / (job_id + ".lock")):
        if path.exists():
            record = json.loads(path.read_text())
            target = record["target"]  # a site config change must not retarget a job
            if action == "submit":
                action = "status"  # lost SSH reply: query, never resubmit
        else:
            record = {"job_id": job_id, "target": target, "created": time.time(), "cancellation": "not_requested"}
        if action == "cancel":
            record["cancellation"] = "requested"
        elif record["cancellation"] == "requested":
            action = "cancel"
        # Persist an addressable identity BEFORE opening the SSH connection.
        atomic_json(path, record)
    argv = ["ssh", "-oBatchMode=yes", "-oConnectTimeout=10", "-oServerAliveInterval=15", target["ssh_alias"],
            shlex.join([target["python"], target["worker_script"], "--config", target["config_file"]])]
    payload = _canonical({"action": action, "arguments": arguments, "job_id": job_id})
    try:
        result = await run(argv, input=payload, timeout=120)
        if result.returncode:
            raise ValueError("Remote request was not confirmed")
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or response.get("supervisor") != "slurm" or response.get("job_id") != job_id:
            raise ValueError("Remote receipt has the wrong job identity")
    except Exception:
        response = {"tool": "midas_integrate_series", "supervisor": "slurm", "job_id": job_id,
                    "status": "unknown", "execution": "unknown", "scientific_verdict": "unassessed",
                    "error": "Remote response was not confirmed. Reconcile this job ID; no automatic resubmission is allowed."}
    response.update(host=target["ssh_alias"], data_location="remote")
    response.setdefault("cancellation_status", record["cancellation"])
    with file_lock(root / (job_id + ".lock")):
        record = json.loads(path.read_text())
        record.update(result=response, updated=time.time())
        atomic_json(path, record)
    return response


async def reconcile_jobs():
    """Poll known Slurm receipts; a valid terminal receipt updates common jobs."""
    from apexa_jobs import default_job_store
    store = default_job_store()
    rows = await asyncio.to_thread(store.list, states=["submitted", "uncertain"], limit=1000)
    for row in rows:
        receipt = row.get("result") or {}
        if not isinstance(receipt, dict) or receipt.get("supervisor") != "slurm" or not receipt.get("job_id"):
            continue
        try:
            result = await request_remote("cancel" if row.get("cancellation_status") == "requested" else "status", job_id=receipt["job_id"])
            state = {"success": "completed", "error": "failed", "cancelled": "cancelled"}.get(result.get("status"))
            if state:
                await asyncio.to_thread(store.resolve, row["id"], state=state,
                    evidence="Slurm supervisor reconciled the scheduler owner/job identity and worker output hashes",
                    result=result)
        except (ValueError, OSError, KeyError, TimeoutError):
            continue  # retain uncertainty; never resubmit


async def monitor_jobs():
    while True:
        if os.environ.get("APEXA_SLURM_CONFIG"):
            try:
                # Includes direct tool submissions and interrupted SSH requests,
                # even when no common JobStore result could be recorded.
                for path in coordinator_root().glob("*.json"):
                    try:
                        row = json.loads(path.read_text())
                        if row.get("result", {}).get("status") not in {"success", "error", "cancelled"}:
                            await request_remote("status", job_id=row["job_id"])
                    except Exception:
                        logging.getLogger(__name__).warning("Remote job reconciliation failed; preserving uncertain state")
                await reconcile_jobs()
            except Exception:
                logging.getLogger(__name__).warning("Remote supervisor unavailable; retrying reconciliation next cycle")
        await asyncio.sleep(30)
