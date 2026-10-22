"""Fail CI when secrets or runtime code downloads enter the repository.

Checks every tracked text file for credential-shaped strings (Supabase secret
keys, JWTs, private keys, committed password hashes) and for code that fetches
Python/JS from GitHub at runtime instead of shipping in a pinned release.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = {
    "supabase secret key": re.compile(r"sb_secret_[A-Za-z0-9_\-]{20,}"),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_\-]{15,}\.eyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{15,}"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "committed password hash": re.compile(r"PASSWORD_(?:HASH|SALT)_B64\s*=\s*['\"][A-Za-z0-9+/=]{16,}"),
    "service key assignment": re.compile(r"SUPABASE_SERVICE(?:_ROLE)?_KEY\s*=\s*['\"](?:sb_secret_|eyJ)[^'\"]{10,}"),
}
DOWNLOAD_PATTERN = re.compile(r"raw\.githubusercontent\.com|codeload\.github\.com")
# The installer may fetch an exact commit archive; deprecated scripts are inert text.
DOWNLOAD_ALLOWED = {"deploy/install_runtime.ps1", "tools/check_repo_hygiene.py", "tests/test_deploy_integrity.py"}
TEXT_SUFFIXES = {".py", ".ts", ".js", ".mjs", ".cjs", ".ps1", ".bat", ".cmd", ".html", ".json",
                 ".toml", ".yml", ".yaml", ".sql", ".md", ".txt", ".cfg", ".ini", ".example"}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [ROOT / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]


def main() -> int:
    problems = []
    for path in tracked_files():
        if path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{rel}: looks like a {label}")
        if rel not in DOWNLOAD_ALLOWED and not rel.startswith("deprecated/") and DOWNLOAD_PATTERN.search(text):
            problems.append(f"{rel}: downloads code from GitHub at runtime")
    for problem in problems:
        print("[HYGIENE] " + problem)
    print(f"Repository hygiene: {'FAILED' if problems else 'OK'} ({len(problems)} problem(s))")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
