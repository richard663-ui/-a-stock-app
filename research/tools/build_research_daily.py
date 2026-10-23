"""Build the research daily-bar store (research only, read-only market data).

Writes ``~/AStockData/research_daily/`` (separate from production caches):
  bars_qmt/part-*.parquet  all currently listed SH/SZ A-shares from QMT, 2014 -> as_of
  bars_delisted.parquet  delisted SH/SZ A-shares from BaoStock (reduces survivorship bias)
  index.parquet          benchmark indices from QMT
  instruments.csv        listing dates and names
  build_report.json      counts, sources, gaps

Columns: date, symbol, open, high, low, close (front-ratio adjusted), close_raw,
pre_close_raw, volume_shares, amount_cny, suspended. Missing bars stay missing.

Usage:
  python research/tools/build_research_daily.py qmt --start 20140101
  python research/tools/build_research_daily.py delisted --start 2014-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

OUT = Path.home() / "AStockData" / "research_daily"
INDICES = ["000300.SH", "000905.SH", "000852.SH", "000001.SH", "399006.SZ"]
FIELDS = ["open", "high", "low", "close", "volume", "amount", "preClose", "suspendFlag"]


def _frame(data: dict, adjusted: dict) -> pd.DataFrame:
    parts = []
    for symbol, raw in data.items():
        adj = adjusted.get(symbol)
        if raw is None or raw.empty or adj is None or adj.empty:
            continue
        # QMT can return a different date set for adjusted and raw series; align on raw dates.
        adj = adj[~adj.index.duplicated()].reindex(raw.index)
        df = pd.DataFrame({
            "date": pd.to_datetime(raw.index.astype(str), format="%Y%m%d"),
            "symbol": symbol,
            "open": adj["open"].to_numpy(float), "high": adj["high"].to_numpy(float),
            "low": adj["low"].to_numpy(float), "close": adj["close"].to_numpy(float),
            "close_raw": raw["close"].to_numpy(float), "pre_close_raw": raw["preClose"].to_numpy(float),
            # QMT daily volume is in lots of 100 shares.
            "volume_shares": raw["volume"].to_numpy(float) * 100.0,
            "amount_cny": raw["amount"].to_numpy(float),
            "suspended": raw["suspendFlag"].to_numpy(float) > 0 if "suspendFlag" in raw else False,
        })
        parts.append(df)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def build_qmt(start: str, batch: int, resume: bool = False) -> dict:
    from xtquant import xtdata
    xtdata.enable_hello = False
    stocks = sorted(set(xtdata.get_stock_list_in_sector("沪深A股") or []))
    stocks = [s for s in stocks if s.endswith((".SH", ".SZ"))]
    end = datetime.now().strftime("%Y%m%d")
    from modules.trading_calendar import _normalize_days
    calendar = {d.replace("-", "") for d in _normalize_days(xtdata.get_trading_dates("SH", start, end))}
    parts_dir = OUT / "bars_qmt"
    parts_dir.mkdir(parents=True, exist_ok=True)
    failed, rows, first, last = [], 0, None, None
    t0 = time.time()
    # One parquet part per batch keeps memory flat (the PC also runs QMT and recorders).
    for i in range(0, len(stocks), batch):
        chunk = stocks[i:i + batch]
        if resume and (parts_dir / f"part-{i // batch:04d}.parquet").exists():
            continue
        try:
            xtdata.download_history_data2(chunk, "1d", start_time=start, end_time=end)
            raw = xtdata.get_market_data_ex(FIELDS, chunk, period="1d", start_time=start, end_time=end,
                                            dividend_type="none", fill_data=False) or {}
            adj = xtdata.get_market_data_ex(["open", "high", "low", "close"], chunk, period="1d", start_time=start,
                                            end_time=end, dividend_type="front_ratio", fill_data=False) or {}
            frame = _frame(raw, adj)
            if calendar and not frame.empty:
                frame = frame[frame["date"].dt.strftime("%Y%m%d").isin(calendar)]
            if not frame.empty:
                floats = frame.select_dtypes("float64").columns
                frame[floats] = frame[floats].astype("float32")
                frame.to_parquet(parts_dir / f"part-{i // batch:04d}.parquet", index=False)
                rows += len(frame)
                first = min(first or frame["date"].min(), frame["date"].min())
                last = max(last or frame["date"].max(), frame["date"].max())
            failed.extend(s for s in chunk if s not in raw or raw[s] is None or raw[s].empty)
        except Exception as exc:  # keep going; report the gap
            failed.extend(chunk)
            print(f"batch {i} failed: {exc}", flush=True)
        print(f"{min(i + batch, len(stocks))}/{len(stocks)} stocks, {time.time() - t0:.0f}s", flush=True)

    xtdata.download_history_data2(INDICES, "1d", start_time=start, end_time=end)
    raw = xtdata.get_market_data_ex(FIELDS, INDICES, period="1d", start_time=start, end_time=end,
                                    dividend_type="none", fill_data=False) or {}
    _frame(raw, raw).to_parquet(OUT / "index.parquet", index=False)

    details = []
    for s in stocks:
        d = xtdata.get_instrument_detail(s) or {}
        details.append({"symbol": s, "name": d.get("InstrumentName"), "open_date": d.get("OpenDate"),
                        "float_shares": d.get("FloatVolume"), "total_shares": d.get("TotalVolume")})
    pd.DataFrame(details).to_csv(OUT / "instruments.csv", index=False, encoding="utf-8-sig")
    stored = ds.dataset(str(parts_dir), format="parquet").to_table(columns=["date", "symbol"]).to_pandas()
    return {"source": "QMT xtdata 1d (front_ratio + none)", "stocks_listed": len(stocks),
            "stocks_stored": int(stored["symbol"].nunique()), "rows": int(len(stored)),
            "not_stored": sorted(set(stocks) - set(stored["symbol"])), "download_failed": failed,
            "first": str(stored["date"].min().date()), "last": str(stored["date"].max().date()),
            "calendar_days": len(calendar), "seconds": round(time.time() - t0)}


def build_delisted(start: str) -> dict:
    import baostock as bs
    bs.login()
    try:
        rs = bs.query_stock_basic()
        basic = rs.get_data()
        # type 1 = stock, status 0 = delisted
        gone = basic[(basic["type"] == "1") & (basic["status"] == "0")].copy()
        gone = gone[gone["outDate"].fillna("") >= start]
        gone = gone[gone["code"].str.startswith(("sh.6", "sz.0", "sz.3"))]
        frames, failed = [], []
        fields = "date,open,high,low,close,preclose,volume,amount,tradestatus,isST"
        for n, row in enumerate(gone.itertuples(index=False), 1):
            code = row.code
            symbol = code[3:] + (".SH" if code.startswith("sh.") else ".SZ")
            try:
                adj = bs.query_history_k_data_plus(code, "date,open,high,low,close", start_date=start,
                                                   end_date=row.outDate, frequency="d", adjustflag="2").get_data()
                raw = bs.query_history_k_data_plus(code, fields, start_date=start, end_date=row.outDate,
                                                   frequency="d", adjustflag="3").get_data()
            except Exception:
                failed.append(symbol)
                continue
            if raw.empty or adj.empty:
                failed.append(symbol)
                continue
            m = raw.merge(adj, on="date", suffixes=("_raw", ""))
            num = lambda c: pd.to_numeric(m[c], errors="coerce")
            frames.append(pd.DataFrame({
                "date": pd.to_datetime(m["date"]), "symbol": symbol,
                "open": num("open"), "high": num("high"), "low": num("low"), "close": num("close"),
                "close_raw": num("close_raw"), "pre_close_raw": num("preclose"),
                "volume_shares": num("volume"), "amount_cny": num("amount"),
                "suspended": m["tradestatus"].astype(str) != "1", "is_st": m["isST"].astype(str) == "1",
            }))
            if n % 50 == 0:
                print(f"{n}/{len(gone)} delisted", flush=True)
    finally:
        bs.logout()
    bars = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    OUT.mkdir(parents=True, exist_ok=True)
    bars.to_parquet(OUT / "bars_delisted.parquet", index=False)
    gone.to_csv(OUT / "delisted_list.csv", index=False, encoding="utf-8-sig")
    return {"source": "BaoStock (adjustflag 2 + 3)", "stocks": int(len(gone)), "rows": int(len(bars)),
            "failed": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("part", choices=["qmt", "delisted"])
    ap.add_argument("--start", default="20140101")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--resume", action="store_true", help="only download batches without a stored part")
    args = ap.parse_args()
    report_path = OUT / "build_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    if args.part == "qmt":
        report["qmt"] = build_qmt(args.start.replace("-", ""), args.batch, args.resume)
    else:
        start = args.start if "-" in args.start else f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:]}"
        report["delisted"] = build_delisted(start)
    report["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    OUT.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items()}, ensure_ascii=False)[:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
