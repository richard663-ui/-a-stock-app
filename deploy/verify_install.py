"""Build and verify SHA-256 manifests for pinned runtime releases.

Layout managed by deploy/install_runtime.ps1::

    <install root>/
        releases/<commit>/        exact tree of one reviewed commit + .astock_manifest.json
        current -> releases/<commit>   (directory junction)
        install_state.json        current commit, previous commits, timestamps
        bin/verify_install.py     copy of this file used by the watchdogs
        runtime/                  logs, watchdogs, integrity_status.json

The watchdogs run ``verify`` before every (re)start and refuse to launch a
service when the code differs from the manifest. This guards against drift
(old installers, manual edits, partially copied files). It cannot protect
against malware already running as the same Windows user; see docs/SECURITY.md.

Standard library only, so it runs before any dependency is importable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

MANIFEST_NAME = ".astock_manifest.json"
STATE_NAME = "install_state.json"
SCHEMA = "astock-release-manifest-v1"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "runtime"}
# Files that can execute code. An unexpected one inside the release is a failure.
EXECUTABLE_SUFFIXES = {".py", ".pyw", ".ps1", ".psm1", ".bat", ".cmd", ".vbs", ".js",
                       ".mjs", ".cjs", ".exe", ".dll", ".pyd", ".so"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_release_files(release_dir: Path) -> List[Path]:
    files: List[Path] = []
    for root, dirs, names in os.walk(release_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if name == MANIFEST_NAME:
                continue
            files.append(Path(root) / name)
    return files


def relpath(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def build_manifest(release_dir: Path, commit: str, source: str) -> Dict:
    if not COMMIT_RE.match(commit):
        raise ValueError("commit must be a full 40-character lowercase hex id")
    files = {relpath(p, release_dir): sha256_file(p) for p in iter_release_files(release_dir)}
    if not files:
        raise ValueError(f"release directory is empty: {release_dir}")
    return {
        "schema": SCHEMA,
        "commit": commit,
        "source": source,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file_count": len(files),
        "files": files,
    }


def write_json_atomic(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> Dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def compare_tree(release_dir: Path, manifest: Dict) -> Tuple[List[str], List[str], List[str]]:
    expected: Dict[str, str] = dict(manifest.get("files") or {})
    missing, modified, unexpected = [], [], []
    for name, digest in expected.items():
        path = release_dir / name
        if not path.is_file():
            missing.append(name)
        elif sha256_file(path) != digest:
            modified.append(name)
    for path in iter_release_files(release_dir):
        name = relpath(path, release_dir)
        if name not in expected and path.suffix.lower() in EXECUTABLE_SUFFIXES:
            unexpected.append(name)
    return missing, modified, unexpected


def same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def verify(install_root: Path) -> Dict:
    started = time.time()
    state = read_json(install_root / STATE_NAME)
    commit = str(state.get("current_commit") or "")
    report: Dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "install_root": str(install_root),
        "commit": commit,
        "ok": False,
        "problems": [],
    }
    problems: List[str] = report["problems"]
    if not COMMIT_RE.match(commit):
        problems.append("install_state.json has no pinned commit; run deploy/install_runtime.ps1")
        return report
    release = install_root / "releases" / commit
    current = install_root / "current"
    if not release.is_dir():
        problems.append(f"release folder missing: releases/{commit}")
        return report
    if not current.exists() or not same_path(current, release):
        problems.append("current does not point at the pinned release")
        return report
    manifest = read_json(release / MANIFEST_NAME)
    if manifest.get("schema") != SCHEMA or manifest.get("commit") != commit:
        problems.append("release manifest missing or for a different commit")
        return report
    missing, modified, unexpected = compare_tree(release, manifest)
    for label, items in (("missing", missing), ("modified", modified), ("unexpected", unexpected)):
        if items:
            problems.append(f"{len(items)} {label} file(s): " + ", ".join(items[:8]))
    report.update({
        "ok": not problems,
        "file_count": len(manifest.get("files") or {}),
        "source": manifest.get("source"),
        "elapsed_seconds": round(time.time() - started, 3),
    })
    return report


def _status_path(install_root: Path) -> Path:
    return install_root / "runtime" / "integrity_status.json"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="write the manifest for a staged release")
    b.add_argument("--release", type=Path, required=True)
    b.add_argument("--commit", required=True)
    b.add_argument("--source", required=True)
    c = sub.add_parser("check", help="compare a staged release with its own manifest")
    c.add_argument("--release", type=Path, required=True)
    v = sub.add_parser("verify", help="check the installed current release")
    v.add_argument("--install-root", type=Path, required=True)
    v.add_argument("--service", default="")
    v.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "build":
        manifest = build_manifest(args.release, args.commit.lower(), args.source)
        write_json_atomic(args.release / MANIFEST_NAME, manifest)
        print(json.dumps({"ok": True, "commit": manifest["commit"], "file_count": manifest["file_count"]}))
        return 0
    if args.command == "check":
        manifest = read_json(args.release / MANIFEST_NAME)
        if manifest.get("schema") != SCHEMA:
            print(json.dumps({"ok": False, "problems": ["manifest missing"]}))
            return 3
        missing, modified, unexpected = compare_tree(args.release, manifest)
        ok = not (missing or modified or unexpected)
        print(json.dumps({"ok": ok, "missing": missing, "modified": modified, "unexpected": unexpected}))
        return 0 if ok else 3

    report = verify(args.install_root)
    report["service"] = args.service
    try:
        write_json_atomic(_status_path(args.install_root), report)
    except OSError:
        pass
    if not args.quiet or not report["ok"]:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        verdict = "OK" if report["ok"] else "FAILED - service not started"
        print(f"[{stamp}] integrity {verdict} commit={report['commit'][:12]} service={args.service}")
        for problem in report["problems"]:
            print(f"  - {problem}")
    return 0 if report["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
