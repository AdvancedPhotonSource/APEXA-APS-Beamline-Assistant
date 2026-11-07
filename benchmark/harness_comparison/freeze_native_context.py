"""Freeze unedited git snapshots; do not rebuild or rewrite native knowledge."""
import argparse
import json
from pathlib import Path
from native_context import MODE, sha, checked_files


def inventory(root, directories):
    files = {}
    for directory in directories:
        for path in sorted((root / directory).rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Snapshot contains a symlink: {path}")
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(root).as_posix()] = sha(path)
    checked_files(root, files)
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--midas-snapshot", type=Path, required=True)
    ap.add_argument("--apexa-commit", required=True)
    ap.add_argument("--midas-commit", required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--embedding-assets-root", type=Path)
    ap.add_argument("--embedding-assets-manifest", type=Path)
    args = ap.parse_args()
    record = {"schema_version": 1, "mode": MODE,
        "apexa": {"commit": args.apexa_commit, "files": inventory(args.source,
            ["knowledge_base", ".agents/skills"])},
        "midas": {"commit": args.midas_commit, "root": str(args.midas_snapshot.resolve()),
            "files": inventory(args.midas_snapshot, ["manuals", ".claude/skills"])},
        "native_documents_edited": False, "apexa_resources_exported_to_external_arms": False,
        "held_out_knowledge": False,
        "qualification": "Development acquisitions have been discussed in upstream manuals; inspect fresh execution and raw-image evidence, not hidden-answer recovery."}
    if bool(args.embedding_assets_root) != bool(args.embedding_assets_manifest):
        ap.error("Both embedding asset root and manifest are required")
    if args.embedding_assets_root:
        assets = json.loads(args.embedding_assets_manifest.read_text())
        checked_files(args.embedding_assets_root, assets["files"])
        record["embedding_assets"] = {**assets, "root": str(args.embedding_assets_root.resolve())}
    with args.output.open("x") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"receipt_sha256": sha(args.output),
        "apexa_files": len(record["apexa"]["files"]), "midas_files": len(record["midas"]["files"])}))


if __name__ == "__main__":
    main()
