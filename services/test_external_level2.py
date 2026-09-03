# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from modules.external_level2_v2 import ExternalLevel2Manager


def _market_open(now: datetime | None = None) -> bool:
    d = now or datetime.now()
    if d.weekday() >= 5:
        return False
    m = d.hour * 60 + d.minute
    return (555 <= m < 565) or (570 <= m < 690) or (780 <= m < 900)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="301236.SZ")
    p.add_argument("--seconds", type=float, default=10.0)
    args = p.parse_args()

    mgr = ExternalLevel2Manager()
    first = mgr.switch(args.symbol)
    print(f"[EXT-L2] symbol={args.symbol} runtime_available={first.get('runtime_available')} error={first.get('runtime_error')}")
    deadline = time.time() + max(2.0, args.seconds)
    last = first
    while time.time() < deadline:
        time.sleep(1.0)
        last = mgr.snapshot()
        c = last.get("counts") or {}
        ages = last.get("age_seconds") or {}
        print(
            f"[EXT-L2] tx={c.get('l2transaction',0)} order={c.get('l2order',0)} "
            f"queue={c.get('l2orderqueue',0)} quote={c.get('l2quote',0)} "
            f"tx_age={ages.get('l2transaction')} quote_age={ages.get('l2quote')}"
        )
        if int(c.get("l2transaction") or 0) > 0 and int(c.get("l2quote") or 0) > 0:
            break

    c = last.get("counts") or {}
    ok = int(c.get("l2transaction") or 0) > 0 and int(c.get("l2quote") or 0) > 0
    print(json.dumps({
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "symbol": args.symbol,
        "ok": ok,
        "market_open": _market_open(),
        "counts": c,
        "runtime_error": last.get("runtime_error"),
        "provider": last.get("provider"),
    }, ensure_ascii=False))
    if ok:
        print("[PASS] External Level-2 transaction + quote reached the ML adapter.")
        return 0
    if not _market_open() and bool(last.get("runtime_available")):
        print("[INFO] External client is installed, but market is closed; live event-count test is inconclusive.")
        return 3
    print("[WARN] No usable external Level-2 during market hours. Check txtool proxy, provider account, subscription and server settings.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
