"""Append-only acquisition event ingestion with durable cursors and snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from apexa_dataset import DatasetManifest, canonical


class AcquisitionStore:
    def __init__(self, jobs):
        self.jobs = jobs
        with jobs.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS acquisition_cursors (
                    source TEXT PRIMARY KEY, device INTEGER NOT NULL,
                    inode INTEGER NOT NULL, offset INTEGER NOT NULL,
                    prefix_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS acquisition_events (
                    producer TEXT NOT NULL, event_id TEXT NOT NULL,
                    version TEXT NOT NULL, received REAL NOT NULL,
                    PRIMARY KEY(producer,event_id));
                CREATE TABLE IF NOT EXISTS acquisition_snapshots (
                    producer TEXT NOT NULL, acquisition_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, version TEXT NOT NULL,
                    manifest TEXT NOT NULL, disposition TEXT NOT NULL,
                    reason TEXT, run_id TEXT,
                    PRIMARY KEY(producer,acquisition_id,revision));
            """)

    def _ingest(self, db, event, producer):
        if (not isinstance(event, dict) or set(event) != {"event_id", "manifest"}
                or not isinstance(event["event_id"], str) or not event["event_id"]
                or len(event["event_id"]) > 256):
            raise ValueError("event requires event_id (up to 256 characters) and manifest")
        manifest = DatasetManifest(event["manifest"])
        value = manifest.to_dict()
        if value["producer"] != producer:
            raise ValueError("event producer does not match the configured acquisition producer")
        existing = db.execute("SELECT version FROM acquisition_events WHERE producer=? AND event_id=?", (producer, event["event_id"])).fetchone()
        if existing:
            if existing[0] != manifest.version:
                raise ValueError("event_id reused for different acquisition content")
            return
        key = (producer, value["acquisition_id"], value["revision"])
        existing = db.execute("SELECT version FROM acquisition_snapshots WHERE producer=? AND acquisition_id=? AND revision=?", key).fetchone()
        if existing and existing[0] != manifest.version:
            raise ValueError("acquisition revision reused for a different snapshot")
        if not existing:
            # The same correction revision must retain the same content across
            # every dataset snapshot, including snapshots delivered out of order.
            previous = db.execute("SELECT revision,manifest FROM acquisition_snapshots WHERE producer=? AND acquisition_id=?", key[:2]).fetchall()
            for row in previous:
                prior = json.loads(row["manifest"])
                for name in set(prior["corrections"]) & set(value["corrections"]):
                    old, new = prior["corrections"][name], value["corrections"][name]
                    if old["revision"] == new["revision"] and old != new:
                        raise ValueError("correction revision reused for different content")
                    if ((value["revision"] > row["revision"] and new["revision"] < old["revision"])
                            or (value["revision"] < row["revision"] and new["revision"] > old["revision"])):
                        raise ValueError("correction revision cannot decrease across dataset revisions")
            db.execute("INSERT INTO acquisition_snapshots(producer,acquisition_id,revision,version,manifest,disposition) VALUES(?,?,?,?,?,'pending')", (*key, manifest.version, canonical(value)))
            # Only the highest delivered revision can start. Already executing
            # effects keep their own identity/outcome and are never erased.
            latest = db.execute("SELECT max(revision) FROM acquisition_snapshots WHERE producer=? AND acquisition_id=?", key[:2]).fetchone()[0]
            old = db.execute("SELECT run_id FROM acquisition_snapshots WHERE producer=? AND acquisition_id=? AND revision<? AND run_id IS NOT NULL", (*key[:2], latest)).fetchall()
            for row in old:
                changed = db.execute("UPDATE runs SET state='cancelled',cancellation_status='stopped',token=NULL,lease_until=NULL,updated=? WHERE id=? AND state IN ('queued','claimed')", (time.time(), row[0])).rowcount
                if changed:
                    self.jobs._event(db, row[0], "superseded", {"new_revision": latest, "execution_started": False})
            db.execute("UPDATE acquisition_snapshots SET disposition='superseded' WHERE producer=? AND acquisition_id=? AND revision<?", (*key[:2], latest))
        db.execute("INSERT INTO acquisition_events VALUES(?,?,?,?)", (producer, event["event_id"], manifest.version, time.time()))

    def ingest_log(self, path, *, producer, source_id=None):
        """Read complete JSONL records; persist each batch and its cursor atomically.

        Truncation, rotation or modification of consumed bytes is refused. Use a
        new source_id for a new append-only log; event deduplication still holds.
        The producer is operator-configured, not a cryptographic authentication.
        """
        path = Path(path).expanduser().resolve()
        source = source_id or str(path)
        count = 0
        with path.open("rb") as stream, self.jobs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            import os
            info = os.fstat(stream.fileno())
            cursor = db.execute("SELECT * FROM acquisition_cursors WHERE source=?", (source,)).fetchone()
            offset = cursor["offset"] if cursor else 0
            if cursor and (cursor["device"], cursor["inode"]) != (info.st_dev, info.st_ino):
                raise ValueError("acquisition event log rotated; configure a new source_id")
            if info.st_size < offset:
                raise ValueError("acquisition event log truncated")
            digest = hashlib.sha256()
            remaining = offset
            while remaining:
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise ValueError("acquisition event log truncated during read")
                digest.update(chunk)
                remaining -= len(chunk)
            if cursor and cursor["prefix_sha256"] != digest.hexdigest():
                raise ValueError("previously consumed acquisition event bytes changed")
            # Bound a scan to the log size observed at open. A continuously
            # writing producer must not keep the ingestion transaction open.
            while stream.tell() < info.st_size:
                line = stream.readline(min(16 * 1024 * 1024 + 1, info.st_size - stream.tell()))
                if len(line) > 16 * 1024 * 1024:
                    raise ValueError("acquisition event exceeds 16 MiB")
                if not line or not line.endswith(b"\n"):
                    break  # incomplete producer write, leave cursor unchanged
                self._ingest(db, json.loads(line), producer)
                digest.update(line)
                offset += len(line)
                count += 1
            db.execute("INSERT INTO acquisition_cursors VALUES(?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET offset=excluded.offset,prefix_sha256=excluded.prefix_sha256",
                       (source, info.st_dev, info.st_ino, offset, digest.hexdigest()))
        return count

    def latest(self, *, producer):
        with self.jobs.connect() as db:
            rows = db.execute("""SELECT s.* FROM acquisition_snapshots s WHERE producer=? AND revision=(
                SELECT max(t.revision) FROM acquisition_snapshots t WHERE t.producer=s.producer AND t.acquisition_id=s.acquisition_id)
                ORDER BY acquisition_id""", (producer,))
            return [{**dict(row), "manifest": json.loads(row["manifest"])} for row in rows]

    def note(self, version, disposition, reason):
        with self.jobs.connect() as db:
            db.execute("UPDATE acquisition_snapshots SET disposition=?,reason=? WHERE version=? AND run_id IS NULL AND disposition!='superseded'", (disposition, reason, version))

    def submit(self, manifest, plan, *, trace_id=None):
        """Bind a plan and snapshot to a run in the cursor database transaction."""
        value = manifest.to_dict()
        with self.jobs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM acquisition_snapshots WHERE producer=? AND acquisition_id=? ORDER BY revision DESC LIMIT 1", (value["producer"], value["acquisition_id"])).fetchone()
            if row is None or row["version"] != manifest.version:
                raise ValueError("only the latest ingested snapshot may be submitted")
            if row["run_id"]:
                return row["run_id"]
            payload = {"kind": "acquisition", "dataset_version": manifest.version,
                       "manifest": value, "plan": plan.to_dict()}
            run_id = self.jobs.submit(payload, trace_id=trace_id, _connection=db)
            db.execute("UPDATE acquisition_snapshots SET run_id=?,disposition='submitted',reason=NULL WHERE version=?", (run_id, manifest.version))
        return run_id
