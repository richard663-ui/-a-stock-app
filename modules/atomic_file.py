"""Replace a file atomically without stopping when a reader has it open.

On Windows ``os.replace`` raises PermissionError while another process holds
the destination open without delete sharing: the collection monitor reading a
status file, antivirus, the search indexer. Such a handle is held for a few
milliseconds, so a short retry is enough.

Between 2026-10-25 and 2026-11-13 this error stopped the training recorder 93
times (``training_status.tmp -> training_status.json``). Every stop threw away
the samples still waiting for their 60-second label.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

# About half a second in total: far longer than a reader holds the file,
# short enough for a 5-second sampling loop.
RETRY_DELAYS: Sequence[float] = (0.01, 0.02, 0.05, 0.1, 0.15, 0.2)


def replace_file(tmp, path, delays: Sequence[float] = RETRY_DELAYS,
                 replace: Callable = os.replace, sleep: Callable = time.sleep) -> int:
    """Move ``tmp`` over ``path``. Returns the retries used; raises when every attempt is refused."""
    retries = 0
    while True:
        try:
            replace(tmp, path)
            return retries
        except PermissionError:
            if retries >= len(delays):
                raise
            sleep(delays[retries])
            retries += 1


def write_text_atomic(path, text: str, encoding: str = "utf-8",
                      delays: Sequence[float] = RETRY_DELAYS) -> int:
    """Write ``text`` next to ``path`` and move it into place. Returns the retries used."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding=encoding)
    return replace_file(tmp, path, delays)


def write_heartbeat(path, payload: Any, indent: int = 2, default: Optional[Callable] = None,
                    log: Callable = print, delays: Sequence[float] = RETRY_DELAYS) -> bool:
    """Write a status/heartbeat JSON file. Never raises OSError; False means this write was skipped.

    A skipped heartbeat costs nothing: the next one follows within seconds, and a
    file that stays old is reported by the collection monitor. Stopping the
    writer instead loses market data.
    """
    name = Path(path).name
    try:
        retries = write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=indent, default=default),
                                    delays=delays)
    except OSError as exc:
        log(f"[WARN] status file busy, update skipped ({name}): {type(exc).__name__}: {exc}")
        return False
    if retries:
        log(f"[INFO] status file busy, written after {retries} retry(ies) ({name})")
    return True
