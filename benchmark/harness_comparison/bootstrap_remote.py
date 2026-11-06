#!/usr/bin/env python3
"""Install pinned benchmark CLIs inside the reviewed study folder only.

Run on Chiltepin: the shared directory is then usable from Copland. The npm
installation runs inside Bubblewrap with writes confined to the study root.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

PACKAGES = ["@anthropic-ai/claude-code@2.1.318", "opencode-ai@1.18.23",
            "@earendil-works/pi-coding-agent@1.2.0"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit("The reviewed study directory must already exist")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SystemExit("Bubblewrap is required before installing packages")
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    downloads = runtime / "downloads"
    downloads.mkdir(exist_ok=True)
    node = runtime / "node"
    if not node.exists():
        base = "https://nodejs.org/dist/latest-v22.x/"
        with urllib.request.urlopen(base + "SHASUMS256.txt", timeout=30) as r:
            checksums = r.read().decode()
        choices = [line.split() for line in checksums.splitlines() if line.endswith("-linux-x64.tar.xz")]
        if len(choices) != 1:
            raise RuntimeError("Could not identify one Linux x64 Node archive")
        expected, filename = choices[0]
        archive = downloads / filename
        with urllib.request.urlopen(base + filename, timeout=60) as source, archive.open("xb") as target:
            shutil.copyfileobj(source, target)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Node archive checksum mismatch")
        with tarfile.open(archive) as tf:
            tf.extractall(runtime, filter="data")
        node.symlink_to(filename.removesuffix(".tar.xz"), target_is_directory=True)
        (downloads / "node-provenance.json").write_text(json.dumps({"url": base + filename,
            "sha256": expected, "checksum_source": base + "SHASUMS256.txt"}, indent=2) + "\n")
    harnesses = runtime / "harnesses"
    harnesses.mkdir(exist_ok=True)
    if (harnesses / "package-lock.json").exists():
        raise SystemExit("An installation lock already exists; inspect it instead of overwriting it")
    for name in ("user.npmrc", "global.npmrc"):
        path = runtime / name
        if not path.exists():
            path.touch(mode=0o600)
    env = dict(os.environ)
    env.update(PATH=str(node / "bin") + os.pathsep + env.get("PATH", ""),
               npm_config_cache=str(runtime / "npm-cache"),
               XDG_CACHE_HOME=str(runtime / "cache"), npm_config_update_notifier="false",
               XDG_CONFIG_HOME=str(runtime / "config"), XDG_DATA_HOME=str(runtime / "data"),
               XDG_STATE_HOME=str(runtime / "state"),
               npm_config_userconfig=str(runtime / "user.npmrc"),
               npm_config_globalconfig=str(runtime / "global.npmrc"))
    command = [bwrap, "--die-with-parent", "--new-session", "--unshare-user", "--unshare-pid",
               "--ro-bind", "/", "/", "--bind", str(root), str(root), "--tmpfs", "/tmp",
               "--proc", "/proc", "--dev", "/dev", "--chdir", str(harnesses), "--",
               str(node / "bin/npm"), "install", "--prefix", str(harnesses),
               "--save-exact", "--no-audit", "--no-fund", *PACKAGES]
    subprocess.run(command, env=env, check=True)
    print("Installed only under", root, flush=True)


if __name__ == "__main__":
    main()
