"""Atomic same-host resource reservations across cooperating APEXA processes.

Orphan reservations remain charged: owner death does not prove descendants or
remote work stopped. An operator/qualified supervisor must reconcile them using
explicit evidence. SQLite must live on a filesystem with reliable local locking.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import time
import uuid

import psutil


class ReservationDenied(RuntimeError):
    pass


def host_identity():
    # Hostnames scope a registry shared accidentally through a home directory.
    # Linux machine-id disambiguates machines configured with the same name.
    machine = ""
    try:
        machine = Path("/etc/machine-id").read_text().strip()
    except OSError:
        pass
    return hashlib.sha256((socket.gethostname() + ":" + machine).encode()).hexdigest()


def target_key(host):
    value = (host or "").strip().lower()
    aliases = {"", "local", "localhost", "127.0.0.1", "::1", socket.gethostname().lower()}
    return "local" if value in aliases else value


def boot_identity():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return str(psutil.boot_time())


def process_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    return {"pid": pid, "started": psutil.Process(pid).create_time(), "boot": boot_identity()}


def _alive(identity):
    """True/False/None; inability to inspect a process is not proof it stopped."""
    if identity["boot"] != boot_identity():
        return False
    try:
        process = psutil.Process(identity["pid"])
        if identity["started"] is None:
            return None  # exited before attachment; a reused PID is ambiguous
        return (abs(process.create_time() - identity["started"]) < 0.001
                and process.status() != psutil.STATUS_ZOMBIE and process.is_running())
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return None


class ResourceRegistry:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.controller = host_identity()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS resource_reservations (
                    id TEXT PRIMARY KEY, controller TEXT NOT NULL, target TEXT NOT NULL,
                    cpu INTEGER NOT NULL, ram REAL NOT NULL, gpu TEXT, vram REAL NOT NULL,
                    cpu_limit INTEGER NOT NULL, ram_limit REAL NOT NULL, gpu_limit REAL NOT NULL,
                    owner TEXT NOT NULL, state TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, children TEXT NOT NULL,
                    reason TEXT);
                CREATE INDEX IF NOT EXISTS resource_scope ON resource_reservations(controller,target,state);
                CREATE TABLE IF NOT EXISTS resource_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, reservation_id TEXT NOT NULL,
                    kind TEXT NOT NULL, at REAL NOT NULL, evidence TEXT);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _event(db, reservation_id, kind, evidence=None):
        db.execute("INSERT INTO resource_events(reservation_id,kind,at,evidence) VALUES(?,?,?,?)",
                   (reservation_id, kind, time.time(), evidence))

    def _reconcile(self, db):
        rows = db.execute("SELECT id,owner FROM resource_reservations WHERE controller=? AND state='active'", (self.controller,)).fetchall()
        changed = []
        for row in rows:
            if _alive(json.loads(row["owner"])) is False:
                reason = "owner process identity disappeared; descendant/external execution outcome unknown"
                db.execute("UPDATE resource_reservations SET state='uncertain',updated=?,reason=? WHERE id=?", (time.time(), reason, row["id"]))
                self._event(db, row["id"], "uncertain", reason)
                changed.append(row["id"])
        return changed

    def reconcile(self):
        """Mark lost owners uncertain, while continuing to charge their capacity."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._reconcile(db)

    def _rows(self, db, target):
        return db.execute("SELECT * FROM resource_reservations WHERE controller=? AND target=? AND state IN ('active','uncertain')",
                          (self.controller, target_key(target))).fetchall()

    @staticmethod
    def _totals(rows):
        gpu = {}
        for row in rows:
            if row["gpu"]:
                gpu[row["gpu"]] = gpu.get(row["gpu"], 0.0) + row["vram"]
        return {"cpu": sum(row["cpu"] for row in rows), "ram": sum(row["ram"] for row in rows), "gpu": gpu}

    def reserved(self, target=""):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reconcile(db)
            return self._totals(self._rows(db, target))

    def reserve(self, *, target="", cpu, ram, cpu_limit, ram_limit,
                gpu=None, vram=0.0, gpu_limit=0.0):
        if type(cpu) is not int or cpu <= 0 or type(cpu_limit) is not int or cpu_limit <= 0:
            raise ReservationDenied("positive CPU demand and capacity are required")
        if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
               for v in (ram, ram_limit, vram, gpu_limit)) or ram_limit <= 0:
            raise ReservationDenied("finite known memory demand and capacity are required")
        if gpu and (not isinstance(gpu, str) or not gpu.startswith("GPU-") or vram <= 0 or gpu_limit <= 0):
            raise ReservationDenied("GPU admission requires physical GPU UUID and known peak VRAM")
        if not gpu and (vram or gpu_limit):
            raise ReservationDenied("VRAM reservation requires a GPU identity")
        owner = process_identity()
        reservation_id = uuid.uuid4().hex
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reconcile(db)
            rows = self._rows(db, target)
            held = self._totals(rows)
            # An optimistic caller cannot enlarge another active caller's
            # admission ceiling. Fresh lower ceilings can only reduce capacity.
            cpu_bound = min([cpu_limit] + [row["cpu_limit"] for row in rows])
            ram_bound = min([ram_limit] + [row["ram_limit"] for row in rows])
            gpu_bound = min([gpu_limit] + [row["gpu_limit"] for row in rows if gpu and row["gpu"] == gpu])
            if (held["cpu"] + cpu > cpu_bound or held["ram"] + ram > ram_bound + 1e-9
                    or (gpu and held["gpu"].get(gpu, 0) + vram > gpu_bound + 1e-9)):
                raise ReservationDenied("shared host CPU/RAM/GPU capacity is held by active or uncertain reservations")
            db.execute("INSERT INTO resource_reservations VALUES(?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,NULL)",
                       (reservation_id, self.controller, target_key(target), cpu, ram, gpu, vram,
                        cpu_limit, ram_limit, gpu_limit, json.dumps(owner), now, now, "[]"))
            self._event(db, reservation_id, "reserved")
        return ResourceLease(self, reservation_id, owner)

    def get(self, reservation_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM resource_reservations WHERE id=? AND controller=?", (reservation_id, self.controller)).fetchone()
        if row is None:
            raise KeyError(reservation_id)
        return {**dict(row), "owner": json.loads(row["owner"]), "children": json.loads(row["children"])}

    def list(self, *, target="", include_released=False):
        with self.connect() as db:
            query = "SELECT id FROM resource_reservations WHERE controller=? AND target=?"
            if not include_released:
                query += " AND state!='released'"
            ids = [row[0] for row in db.execute(query + " ORDER BY created", (self.controller, target_key(target)))]
        return [self.get(reservation_id) for reservation_id in ids]

    def mark_uncertain(self, reservation_id, *, evidence):
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("uncertainty requires an explanation")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE resource_reservations SET state='uncertain',updated=?,reason=? WHERE id=? AND controller=? AND state='active'", (time.time(), evidence, reservation_id, self.controller)).rowcount
            if changed:
                self._event(db, reservation_id, "uncertain", evidence)
            return bool(changed)

    def release_verified(self, reservation_id, *, evidence):
        """Explicit supervisor/operator resolution after verifying effects stopped.

        Evidence is an audit statement, not automated proof. No timeout or dead
        owner PID invokes this method automatically. Do not expose as a generic
        unauthenticated resource-freeing HTTP route.
        """
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("release requires explicit stopped-execution evidence")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM resource_reservations WHERE id=? AND controller=?", (reservation_id, self.controller)).fetchone()
            if row is None:
                raise KeyError(reservation_id)
            if row["state"] == "released":
                return False
            db.execute("UPDATE resource_reservations SET state='released',updated=?,reason=? WHERE id=?", (time.time(), evidence, reservation_id))
            self._event(db, reservation_id, "released_verified", evidence)
            return True


class ResourceLease:
    def __init__(self, registry, reservation_id, owner):
        self.registry, self.id, self.owner = registry, reservation_id, owner

    def attach_child(self, pid, *, process_group=None):
        """Optional ownership evidence; call immediately after local spawn."""
        if process_identity() != self.owner:
            raise ReservationDenied("only the reserving process can attach a child")
        try:
            child = process_identity(pid)
        except psutil.NoSuchProcess:
            # A tiny child can be reaped before the launcher records its PID.
            # Keep its known new-session process group: descendants may remain.
            child = {"pid": pid, "started": None, "boot": boot_identity(),
                     "exited_before_attach": True}
        if process_group is not None:
            if os.name != "posix":
                raise ValueError("child does not belong to the declared process group")
            try:
                actual_group = os.getpgid(pid)
            except ProcessLookupError:
                if process_group != pid:
                    raise ValueError("exited child needs its launcher-known new-session group")
            else:
                if actual_group != process_group:
                    raise ValueError("child does not belong to the declared process group")
            child["process_group"] = process_group
        elif child.get("exited_before_attach"):
            child["unknown_descendants"] = True
        with self.registry.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT children,state FROM resource_reservations WHERE id=?", (self.id,)).fetchone()
            if row["state"] != "active":
                raise ReservationDenied("reservation is no longer active")
            children = json.loads(row["children"])
            children.append(child)
            db.execute("UPDATE resource_reservations SET children=?,updated=? WHERE id=?", (json.dumps(children), time.time(), self.id))
            self.registry._event(db, self.id, "child_attached")

    def mark_uncertain(self, evidence):
        return self.registry.mark_uncertain(self.id, evidence=evidence)

    def release(self):
        """Owner asserts its synchronous execution context has finished cleanup."""
        if process_identity() != self.owner:
            raise ReservationDenied("a different process cannot release this reservation")
        row = self.registry.get(self.id)
        if row["state"] != "active":
            return False  # uncertainty requires separate explicit resolution
        for child in row["children"]:
            if child.get("unknown_descendants"):
                self.mark_uncertain("child exited before attachment without a verifiable process group")
                return False
            if _alive(child) is not False:
                self.mark_uncertain("attached child has not been verified stopped")
                return False
            if child.get("process_group") is not None and child["boot"] == boot_identity():
                try:
                    os.killpg(child["process_group"], 0)
                except ProcessLookupError:
                    pass
                except (PermissionError, OSError):
                    self.mark_uncertain("unable to verify attached process group stopped")
                    return False
                else:
                    self.mark_uncertain("attached process group still exists")
                    return False
        # Keep check and release atomic with respect to explicit uncertainty.
        with self.registry.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE resource_reservations SET state='released',updated=?,reason=? WHERE id=? AND state='active' AND children=?",
                                 (time.time(), "owner execution context finished cleanup", self.id, json.dumps(row["children"]))).rowcount
            if changed:
                self.registry._event(db, self.id, "released", "owner execution context finished cleanup")
            else:
                # A concurrent attachment invalidates the preceding checks.
                if db.execute("UPDATE resource_reservations SET state='uncertain',updated=?,reason=? WHERE id=? AND state='active'",
                              (time.time(), "child ownership changed during release verification", self.id)).rowcount:
                    self.registry._event(db, self.id, "uncertain", "child ownership changed during release verification")
            return bool(changed)
