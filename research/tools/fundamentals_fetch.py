"""Download and assemble the point-in-time A-share fundamentals store (research only).

Writes ``%USERPROFILE%/AStockData/fundamentals/`` (README.md there explains every table). Stages are
resumable: raw responses are cached under ``raw/`` and finished items are skipped on a rerun.

  em        Eastmoney datacenter tables per report period 2014Q1-latest (main, cpd, dmsk, previews)
  cninfo    cninfo searches: corrected/revised periodic reports, correction notices, restatements
  bsbasic   BaoStock stock list (listing and delisting dates; includes delisted stocks)
  bsprev    BaoStock forecasts (all versions) and express reports per stock
  targets   random sample + correction candidates to verify against BaoStock
  screen    one BaoStock query (dupont) for every other listed stock-period: first-published
            归母/营业总收入 ratio and publication date, to catch silent restatements
  flagged   stock-periods whose screen disagrees with Eastmoney -> fully checked as well
  bscheck   BaoStock first-published figures for the targets (and flagged rows)
  cncheck   cninfo announcement dates for the random sample
  assemble  parquet tables, coverage and verification numbers (build_report.json)
  all       everything in order; stages that use different hosts run concurrently

Polite by design: one request at a time per host with pauses, retries with backoff, a handful of
BaoStock sessions. Run it detached at BelowNormal priority (the PC is also used for games).
Usage: python -I research/tools/fundamentals_fetch.py all
"""
from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")  # ~16 MB instead of ~0.75 GB commit per process

import argparse
import contextlib
import gzip
import importlib.util
import io
import json
import multiprocessing as mp
import sys
import threading
import time
import traceback
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

_spec = importlib.util.spec_from_file_location("fundamentals_pit", Path(__file__).with_name("fundamentals_pit.py"))
pit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pit)

OUT_DEFAULT = Path.home() / "AStockData" / "fundamentals"
START_PERIOD = pd.Timestamp("2014-03-31")  # 2014 is kept as the base year of 2015 growth rates
STUDY_START = pd.Timestamp("2015-03-31")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) research-client"
EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
TYPE_FILTER = '(SECURITY_TYPE_CODE in ("058001001","058001008"))'
EM_TABLES = {  # key: (reportName, period column, stable sort columns)
    "main": ("RPT_F10_FINANCE_MAINFINADATA", "REPORT_DATE", "SECUCODE"),
    "cpd": ("RPT_LICO_FN_CPD", "REPORTDATE", "SECUCODE"),
    "income": ("RPT_DMSK_FN_INCOME", "REPORT_DATE", "SECUCODE"),
    "cash": ("RPT_DMSK_FN_CASHFLOW", "REPORT_DATE", "SECUCODE"),
    "forecast": ("RPT_PUBLIC_OP_NEWPREDICT", "REPORT_DATE", "SECUCODE,PREDICT_FINANCE_CODE,NOTICE_DATE"),
    "express": ("RPT_FCI_PERFORMANCEE", "REPORT_DATE", "SECUCODE,NOTICE_DATE"),
}
CNINFO_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
CN_HEADERS = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "Origin": "http://www.cninfo.com.cn",
              "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search"}
PERIODIC = "category_ndbg_szsh;category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh"
CN_CATEGORY = {"FY": "category_ndbg_szsh", "H1": "category_bndbg_szsh", "Q1": "category_yjdbg_szsh", "Q3": "category_sjdbg_szsh"}
CN_QUERIES = ([("revised", PERIODIC, k, "year") for k in ("更正", "更新", "修订", "修正", "补充")]
              + [("corrections", "category_bcgz_szsh", "", "quarter")]
              + [("restatement", "", k, "year") for k in ("差错更正", "追溯调整")])


# ----------------------------------------------------------------------------- plumbing
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    # sys.__stdout__: BaoStock calls run under redirect_stdout, which would swallow other threads' lines
    stream = sys.__stdout__ or sys.stdout
    if stream is not None:
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", file=stream, flush=True)


def save_gz(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def load_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:  # a line cut by a crash; it is fetched again
                    continue
    return out


class Status:
    """Small JSON progress file (status.json) for checking a detached run."""

    def __init__(self, path: Path):
        self.path, self.lock = path, threading.Lock()
        try:
            self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except json.JSONDecodeError:
            self.data = {}

    def set(self, section: str, **kw) -> None:
        with self.lock:
            self.data.setdefault(section, {}).update(kw, updated=now_iso())
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, self.path)


class Http:
    """One polite client per host: fixed pause between requests, retries with exponential backoff."""

    def __init__(self, interval: float):
        import requests
        self.requests = requests
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.interval, self.last = interval, 0.0

    def call(self, method: str, url: str, tries: int = 6, **kw):
        last = None
        for i in range(tries):
            wait = self.last + self.interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            try:
                r = self.session.request(method, url, timeout=60, **kw)
                if r.status_code in (403, 429, 500, 502, 503, 504):
                    raise self.requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r.json()
            except (self.requests.RequestException, ValueError) as exc:
                last = exc
                delay = min(600, 15 * 2 ** i)
                log(f"  retry {i + 1}/{tries} in {delay}s: {url} ({exc})")
                time.sleep(delay)
        raise RuntimeError(f"giving up on {url}: {last}")


def below_normal_priority() -> None:
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL_PRIORITY_CLASS; children inherit


def periods_until_today() -> list[pd.Timestamp]:
    return pit.report_periods(START_PERIOD, pd.Timestamp(date.today()))


# ----------------------------------------------------------------------------- Eastmoney
def em_fetch(http: Http, table: str, period: pd.Timestamp, raw: Path, refresh: bool = False):
    name, pcol, sort = EM_TABLES[table]
    path = raw / "em" / table / f"{period:%Y-%m-%d}.json.gz"
    if path.exists() and not refresh:
        return None
    for attempt in range(2):
        rows, count, page, pages = [], 0, 1, 1
        while page <= pages:
            params = {"reportName": name, "columns": "ALL", "filter": f"({pcol}='{period:%Y-%m-%d}'){TYPE_FILTER}",
                      "pageNumber": page, "pageSize": 5000, "sortColumns": sort,
                      "sortTypes": ",".join("1" for _ in sort.split(",")), "source": "WEB", "client": "WEB"}
            j = http.call("GET", EM_URL, params=params)
            if not j.get("success"):
                if j.get("code") == 9201:  # "no data" for this period
                    break
                raise RuntimeError(f"{name} {period:%Y-%m-%d}: {j.get('code')} {j.get('message')}")
            res = j.get("result") or {}
            count, pages = int(res.get("count") or 0), int(res.get("pages") or 1)
            rows += res.get("data") or []
            page += 1
        if len(rows) == count:
            break
        log(f"  {table} {period:%Y-%m-%d}: got {len(rows)} of {count} rows, refetching")
    if path.exists():  # keep the previous snapshot: later diffs show revisions with observed dates
        old = load_gz(path)
        save_gz(path.parent / "history" / f"{period:%Y-%m-%d}__{old.get('fetched_at', 'unknown')[:10]}.json.gz", old)
    save_gz(path, {"table": name, "period": f"{period:%Y-%m-%d}", "fetched_at": now_iso(), "count": count,
                   "complete": len(rows) == count, "rows": rows})
    return len(rows), count


def stage_em(args, status: Status) -> None:
    http = Http(args.em_interval)
    periods = periods_until_today()
    recent = set(periods[-args.refresh_recent:]) if args.refresh_recent else set()
    for table in EM_TABLES:
        for n, p in enumerate(periods, 1):
            res = em_fetch(http, table, p, args.raw, refresh=p in recent)
            if res is not None:
                log(f"em {table} {p:%Y-%m-%d}: {res[0]}/{res[1]} rows")
            status.set("em", table=table, period=f"{p:%Y-%m-%d}", done=f"{n}/{len(periods)}")
    status.set("em", finished=True)


# ----------------------------------------------------------------------------- cninfo
def cn_page(http: Http, category: str, key: str, start, end, page: int, stock: str = "") -> dict:
    form = {"pageNum": page, "pageSize": 30, "column": "szse", "tabName": "fulltext", "plate": "", "stock": stock,
            "searchkey": key, "secid": "", "category": category, "trade": "",
            "seDate": f"{pd.Timestamp(start):%Y-%m-%d}~{pd.Timestamp(end):%Y-%m-%d}", "sortName": "", "sortType": "",
            "isHLtitle": "true"}
    return http.call("POST", CNINFO_URL, data=form, headers=CN_HEADERS) or {}


def cn_collect(http: Http, category: str, key: str, start, end, stock: str = "", first: dict | None = None) -> tuple[int, list]:
    first = first if first is not None else cn_page(http, category, key, start, end, 1, stock)
    total = int(first.get("totalAnnouncement") or 0)
    anns = list(first.get("announcements") or [])
    page = 2
    while len(anns) < total and page <= int(first.get("totalpages") or 0) + 1:
        got = cn_page(http, category, key, start, end, page, stock).get("announcements") or []
        if not got:
            break
        anns += got
        page += 1
    return total, anns


def cn_window(http: Http, qkind: str, category: str, key: str, start: pd.Timestamp, end: pd.Timestamp, raw: Path) -> int:
    # The open window ends today, so its file name changes and a later run fetches it again;
    # load_events drops the overlap by announcement id.
    path = raw / "cninfo" / f"{qkind}_{key or 'all'}" / f"{start:%Y%m%d}_{end:%Y%m%d}.json.gz"
    if path.exists():
        return load_gz(path)["n"]
    probe = cn_page(http, category, key, start, end, 1)
    if int(probe.get("totalAnnouncement") or 0) > 2900 and (end - start).days >= 2:
        mid = start + (end - start) / 2
        mid = pd.Timestamp(mid.date())
        return (cn_window(http, qkind, category, key, start, mid, raw)
                + cn_window(http, qkind, category, key, mid + pd.Timedelta(days=1), end, raw))
    total, anns = cn_collect(http, category, key, start, end, first=probe)
    save_gz(path, {"query_kind": qkind, "category": category, "key": key, "window": [f"{start:%Y-%m-%d}", f"{end:%Y-%m-%d}"],
                   "total": total, "n": len(anns), "fetched_at": now_iso(), "announcements": anns})
    if len(anns) < total:
        log(f"  cninfo {qkind}/{key} {start:%Y-%m-%d}: got {len(anns)} of {total}")
    return len(anns)


def cn_windows(kind: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    today = pd.Timestamp(date.today())
    out = []
    for year in range(2015, today.year + 1):
        bounds = [(1, 12)] if kind == "year" else [(1, 3), (4, 6), (7, 9), (10, 12)]
        for m0, m1 in bounds:
            start = pd.Timestamp(year, m0, 1)
            end = min(pd.Timestamp(year, m1, 1) + pd.offsets.MonthEnd(0), today)
            if start <= today:
                out.append((start, end))
    return out


def stage_cninfo(args, status: Status) -> None:
    http = Http(args.cn_interval)
    for qkind, category, key, span in CN_QUERIES:
        total = 0
        for start, end in cn_windows(span):
            total += cn_window(http, qkind, category, key, start, end, args.raw)
            status.set("cninfo", query=f"{qkind}/{key}", window=f"{start:%Y-%m-%d}")
        log(f"cninfo {qkind}/{key or 'all'}: {total} announcements")
    status.set("cninfo", finished=True)


# ----------------------------------------------------------------------------- BaoStock (worker processes)
_BS = None


def _bs_login() -> None:
    global _BS
    import baostock as bs
    _BS = bs
    lg = None
    for i in range(6):
        with contextlib.redirect_stdout(io.StringIO()):
            lg = bs.login()
        if lg.error_code == "0":
            return
        time.sleep(10 * (i + 1))
    raise RuntimeError(f"baostock login failed: {getattr(lg, 'error_msg', '?')}")


def _bs_init() -> None:
    try:
        _bs_login()
    except Exception as exc:  # tasks retry the login
        log(f"baostock worker login failed: {exc}")


def _bs_query(fn_name: str, **kw) -> list[dict]:
    last = None
    for i in range(4):
        try:
            if _BS is None:
                _bs_login()
            with contextlib.redirect_stdout(io.StringIO()):
                rs = getattr(_BS, fn_name)(**kw)
                if rs.error_code != "0":
                    raise RuntimeError(f"{rs.error_code} {rs.error_msg}")
                rows = []
                while rs.next():
                    rows.append(dict(zip(rs.fields, rs.get_row_data())))
            return rows
        except Exception as exc:
            last = exc
            time.sleep(5 * (i + 1))
            with contextlib.suppress(Exception), contextlib.redirect_stdout(io.StringIO()):
                _BS.logout()
            with contextlib.suppress(Exception):
                _bs_login()
    raise RuntimeError(f"{fn_name} {kw}: {last}")


BS_QUERIES = {"profit": "query_profit_data", "dupont": "query_dupont_data", "cash": "query_cash_flow_data"}


def bs_check_task(target: tuple) -> dict:
    """target = (code, year, quarter[, parts]); parts defaults to all three queries."""
    code, year, quarter = target[:3]
    parts = target[3] if len(target) > 3 else tuple(BS_QUERIES)
    key = f"{code}|{year}|{quarter}"
    try:
        return {"key": key, **{name: _bs_query(BS_QUERIES[name], code=code, year=year, quarter=quarter) for name in parts}}
    except Exception as exc:
        return {"key": key, "error": str(exc)[:300]}


def bs_screen_task(target: tuple) -> dict:
    return bs_check_task((*target[:3], ("dupont",)))


def bs_prev_task(code: str) -> dict:
    end = f"{date.today():%Y-%m-%d}"
    try:
        return {"code": code,
                "forecast": _bs_query("query_forecast_report", code=code, start_date="2014-10-01", end_date=end),
                "express": _bs_query("query_performance_express_report", code=code, start_date="2014-10-01", end_date=end)}
    except Exception as exc:
        return {"code": code, "error": str(exc)[:300]}


def run_pool(task, items: list, key_of_item, key_of_result, jsonl: Path, workers: int, status: Status, stage: str) -> None:
    done = {key_of_result(r) for r in read_jsonl(jsonl) if "error" not in r}
    todo = [x for x in items if key_of_item(x) not in done]
    log(f"{stage}: {len(todo)} to fetch ({len(items) - len(todo)} cached)")
    if not todo:
        return
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    errors, t0 = 0, time.time()
    with mp.get_context("spawn").Pool(workers, initializer=_bs_init) as pool, open(jsonl, "a", encoding="utf-8") as f:
        for n, res in enumerate(pool.imap_unordered(task, todo, chunksize=4), 1):
            errors += "error" in res
            f.write(json.dumps(res, ensure_ascii=False) + "\n")
            if n % 100 == 0 or n == len(todo):
                f.flush()
                rate = n / max(time.time() - t0, 1e-9)
                status.set(stage, done=n, todo=len(todo), errors=errors, per_sec=round(rate, 2),
                           eta_min=round((len(todo) - n) / max(rate, 1e-9) / 60, 1))
    log(f"{stage}: finished, {errors} errors")


def stage_bsbasic(args, status: Status) -> None:
    path = args.raw / "baostock" / "stock_basic.csv"
    if path.exists() and not args.refresh_recent:
        return
    _bs_login()
    rows = _bs_query("query_stock_basic")
    with contextlib.suppress(Exception), contextlib.redirect_stdout(io.StringIO()):
        _BS.logout()
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")
    status.set("bsbasic", rows=len(rows), finished=True)


def load_universe(raw: Path) -> pd.DataFrame:
    """SH/SZ A-share stocks from BaoStock (listed and delisted)."""
    df = pd.read_csv(raw / "baostock" / "stock_basic.csv", dtype=str)
    df = df[(df["type"] == "1") & df["code"].str.match(r"^(sh\.6|sz\.0|sz\.3)")]
    return pd.DataFrame({"symbol": df["code"].map(pit.symbol_from_bs), "bs_code": df["code"], "name_bs": df["code_name"],
                         "ipo_date": pd.to_datetime(df["ipoDate"], errors="coerce"),
                         "out_date": pd.to_datetime(df["outDate"], errors="coerce"),
                         "listed_now": df["status"] == "1"}).reset_index(drop=True)


def stage_bsprev(args, status: Status) -> None:
    uni = load_universe(args.raw)
    uni = uni[uni["out_date"].isna() | (uni["out_date"] >= pd.Timestamp("2015-01-01"))]
    path = args.raw / "baostock" / "previews.jsonl"
    if args.refresh_recent and path.exists():  # new forecasts appear for every stock: refetch all, keep the old file
        (path.parent / "history").mkdir(exist_ok=True)
        os.replace(path, path.parent / "history" / f"previews_{date.today():%Y%m%d}.jsonl")
    run_pool(bs_prev_task, list(uni["bs_code"]), lambda c: c, lambda r: r["code"], path, args.bs_workers, status, "bsprev")


# ----------------------------------------------------------------------------- targets and checks
def load_em_frames(raw: Path, table: str, parser) -> tuple[pd.DataFrame, list[dict]]:
    frames, meta = [], []
    for path in sorted((raw / "em" / table).glob("*.json.gz")):
        d = load_gz(path)
        frames.append(parser(d["rows"]))
        meta.append({"period": d["period"], "count": d["count"], "rows": len(d["rows"]), "complete": d.get("complete", True),
                     "fetched_at": d.get("fetched_at")})
    frames = [f for f in frames if not f.empty]
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), meta


def load_dmsk(raw: Path) -> pd.DataFrame:
    frames = []
    for path in sorted((raw / "em" / "income").glob("*.json.gz")):
        cash = raw / "em" / "cash" / path.name
        frames.append(pit.parse_em_dmsk(load_gz(path)["rows"], load_gz(cash)["rows"] if cash.exists() else []))
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_events(raw: Path) -> pd.DataFrame:
    frames = []
    for path in sorted((raw / "cninfo").glob("*/*.json.gz")):
        d = load_gz(path)
        if "query_kind" in d:
            frames.append(pit.parse_cninfo(d["announcements"], d["query_kind"]))
    if not frames:
        return pd.DataFrame(columns=["ann_id", "symbol", "title", "ann_time", "ann_date", "kind", "query_kind", "periods", "url"])
    ev = pd.concat(frames, ignore_index=True)
    rank = {"revised_report": 0, "restatement": 1, "correction_notice": 2, "correction_unspecified": 3, "cancelled": 4, "other": 5}
    ev = ev.assign(_r=ev["kind"].map(rank)).sort_values(["ann_id", "_r"]).drop_duplicates("ann_id").drop(columns="_r")
    return ev.reset_index(drop=True)


def stage_targets(args, status: Status) -> None:
    em, _ = load_em_frames(args.raw, "main", pit.parse_em_main)
    uni = load_universe(args.raw)
    links = pit.link_events(load_events(args.raw), em)
    pool = em[em["symbol"].str.endswith((".SH", ".SZ")) & (em["period_end"] >= STUDY_START) & em["notice_date"].notna()]
    pool = pool.merge(uni[["symbol", "ipo_date"]], on="symbol", how="inner")
    pool = pool[pool["period_end"] >= pool["ipo_date"]]
    sample = pool.sample(n=min(args.sample, len(pool)), random_state=args.seed)[["symbol", "period_end"]].assign(why="sample")
    cand = links[links["symbol"].str.endswith((".SH", ".SZ")) & (links["period_end"] >= START_PERIOD)
                 & (links["period_end"] < pd.Timestamp(date.today()))]
    cand = cand[["symbol", "period_end"]].drop_duplicates().assign(why="candidate")
    targets = (pd.concat([sample, cand]).groupby(["symbol", "period_end"])["why"]
               .agg(lambda s: ";".join(sorted(set(s)))).reset_index())
    work = args.out / "work"
    work.mkdir(parents=True, exist_ok=True)
    targets.to_csv(work / "targets.csv", index=False)
    status.set("targets", sample=len(sample), candidates=len(cand), total=len(targets), finished=True)
    log(f"targets: {len(sample)} sample + {len(cand)} candidates = {len(targets)} stock-periods")


def read_targets(out: Path) -> pd.DataFrame:
    """Full-check targets: work/targets.csv (sample, candidates) + work/targets_flagged.csv (screen)."""
    parts = [pd.read_csv(p, parse_dates=["period_end"])
             for p in (out / "work" / "targets.csv", out / "work" / "targets_flagged.csv") if p.exists()]
    if not parts:
        return pd.DataFrame(columns=["symbol", "period_end", "why"])
    t = pd.concat(parts, ignore_index=True)
    return (t.groupby(["symbol", "period_end"])["why"]
            .agg(lambda s: ";".join(sorted(set(";".join(s).split(";"))))).reset_index())


def stage_bscheck(args, status: Status) -> None:
    work = args.out / "work"
    targets = pd.read_csv(work / "targets.csv", parse_dates=["period_end"])
    items = [(pit.bs_code(s), p.year, p.month // 3) for s, p in zip(targets["symbol"], targets["period_end"])]
    if (work / "targets_flagged.csv").exists():  # the screen already holds their dupont row
        fl = pd.read_csv(work / "targets_flagged.csv", parse_dates=["period_end"])
        items += [(pit.bs_code(s), p.year, p.month // 3, ("profit", "cash")) for s, p in zip(fl["symbol"], fl["period_end"])]
    run_pool(bs_check_task, items, lambda t: f"{t[0]}|{t[1]}|{t[2]}", lambda r: r["key"],
             args.raw / "baostock" / "checks.jsonl", args.bs_workers, status, "bscheck")


def stage_screen(args, status: Status) -> None:
    """Dupont row for every SH/SZ stock-period published while listed, except full-check targets."""
    em, _ = load_em_frames(args.raw, "main", pit.parse_em_main)
    uni = load_universe(args.raw)
    rows = em[em["symbol"].str.endswith((".SH", ".SZ")) & (em["period_end"] >= START_PERIOD) & em["notice_date"].notna()]
    rows = rows.merge(uni[["symbol", "ipo_date"]], on="symbol", how="inner")
    rows = rows[rows["notice_date"] >= rows["ipo_date"]]  # prospectus figures are not in BaoStock
    targets = pd.read_csv(args.out / "work" / "targets.csv", parse_dates=["period_end"])
    rows = rows.merge(targets[["symbol", "period_end"]], on=["symbol", "period_end"], how="left", indicator=True)
    rows = rows[rows["_merge"] == "left_only"].sort_values(["period_end", "symbol"])
    items = [(pit.bs_code(s), p.year, p.month // 3) for s, p in zip(rows["symbol"], rows["period_end"])]
    run_pool(bs_screen_task, items, lambda t: f"{t[0]}|{t[1]}|{t[2]}", lambda r: r["key"],
             args.raw / "baostock" / "screen.jsonl", args.bs_workers, status, "screen")


def load_screen(raw: Path) -> pd.DataFrame:
    rows = []
    for r in read_jsonl(raw / "baostock" / "screen.jsonl"):
        if "error" in r:
            continue
        code, year, quarter = r["key"].split("|")
        du = (r.get("dupont") or [None])[0] or {}
        ratio, err = pit.bs_ratio(du)
        rows.append({"symbol": pit.symbol_from_bs(code), "period_end": pd.Timestamp(int(year), int(quarter) * 3, 1) + pd.offsets.MonthEnd(0),
                     "screen_pub_date": pd.to_datetime(du.get("pubDate"), errors="coerce"), "bs_ratio": ratio, "bs_ratio_err": err})
    out = pd.DataFrame(rows, columns=["symbol", "period_end", "screen_pub_date", "bs_ratio", "bs_ratio_err"])
    for c in ("period_end", "screen_pub_date"):
        out[c] = pd.to_datetime(out[c])
    for c in ("bs_ratio", "bs_ratio_err"):
        out[c] = out[c].astype("float64")
    return out.drop_duplicates(["symbol", "period_end"], keep="last")


def screen_frame(em: pd.DataFrame, scr: pd.DataFrame) -> pd.DataFrame:
    m = scr.merge(em[["symbol", "period_end", "notice_date", "parent_netprofit", "revenue_total"]], on=["symbol", "period_end"], how="inner")
    m["screen"] = pit.screen_verdict(m["parent_netprofit"], m["revenue_total"], m["bs_ratio"], m["bs_ratio_err"])
    return m


def stage_flagged(args, status: Status) -> None:
    em, _ = load_em_frames(args.raw, "main", pit.parse_em_main)
    m = screen_frame(em, load_screen(args.raw))
    targets = pd.read_csv(args.out / "work" / "targets.csv", parse_dates=["period_end"])
    fl = m[m["screen"] == "differ"][["symbol", "period_end"]].merge(targets[["symbol", "period_end"]], how="left", indicator=True)
    fl = fl[fl["_merge"] == "left_only"].drop(columns="_merge").assign(why="screen")
    fl.to_csv(args.out / "work" / "targets_flagged.csv", index=False)
    status.set("flagged", screened=len(m), **{k: int(v) for k, v in m["screen"].value_counts().items()}, flagged=len(fl), finished=True)
    log(f"flagged: {len(fl)} of {len(m)} screened stock-periods differ from Eastmoney")


def cn_orgids(http: Http, raw: Path) -> dict:
    path = raw / "cninfo" / "stock_list.json.gz"
    if not path.exists():
        save_gz(path, http.call("GET", "http://www.cninfo.com.cn/new/data/szse_stock.json"))
    return {x.get("code"): x.get("orgId") for x in (load_gz(path) or {}).get("stockList", [])}


def stage_cncheck(args, status: Status) -> None:
    """Independent first-publication dates (cninfo) for the random sample."""
    http = Http(args.cn_interval)
    targets = pd.read_csv(args.out / "work" / "targets.csv", parse_dates=["period_end"])
    sample = targets[targets["why"].str.contains("sample")]
    em, _ = load_em_frames(args.raw, "main", pit.parse_em_main)
    sample = sample.merge(em[["symbol", "period_end", "notice_date"]], on=["symbol", "period_end"], how="left")
    orgs = cn_orgids(http, args.raw)
    out_path = args.raw / "cninfo" / "sample_dates.jsonl"
    done = {(r["symbol"], r["period_end"]) for r in read_jsonl(out_path)}
    with open(out_path, "a", encoding="utf-8") as f:
        for n, row in enumerate(sample.itertuples(index=False), 1):
            key = (row.symbol, f"{row.period_end:%Y-%m-%d}")
            if key in done:
                continue
            code, ptype = row.symbol[:6], pit.period_type(row.period_end)
            rec = {"symbol": row.symbol, "period_end": key[1], "cn_date": None, "cn_title": None, "n_found": 0}
            org = orgs.get(code)
            if not org:
                with contextlib.suppress(Exception):
                    hits = http.call("POST", "http://www.cninfo.com.cn/new/information/topSearch/query",
                                     data={"keyWord": code, "maxNum": 10}) or []
                    org = next((h.get("orgId") for h in hits if h.get("code") == code), None)
            if org:
                anchor = row.notice_date if pd.notna(row.notice_date) else row.period_end + pd.Timedelta(days=60)
                try:
                    _, anns = cn_collect(http, CN_CATEGORY[ptype], "", anchor - pd.Timedelta(days=45),
                                         anchor + pd.Timedelta(days=45), stock=f"{code},{org}")
                    ev = pit.parse_cninfo(anns, "sample")
                    ev = ev[ev["symbol"].eq(row.symbol) & ~ev["title"].str.contains("英文|English|已取消", regex=True)]
                    ev = ev[ev["periods"].str.contains(key[1], regex=False)]
                    rec["n_found"] = int(len(ev))
                    if len(ev):
                        first = ev.sort_values("ann_time").iloc[0]
                        rec["cn_date"], rec["cn_title"] = f"{first['ann_date']:%Y-%m-%d}", first["title"]
                        rec["cn_time"] = f"{first['ann_time']:%Y-%m-%d %H:%M}"
                except Exception as exc:
                    rec["error"] = str(exc)[:200]
            else:
                rec["error"] = "no cninfo orgId"
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            status.set("cncheck", done=f"{n}/{len(sample)}")
    status.set("cncheck", finished=True)


# ----------------------------------------------------------------------------- assemble
def load_bs_checks(raw: Path, targets: pd.DataFrame) -> pd.DataFrame:
    recs = {}
    for r in read_jsonl(raw / "baostock" / "checks.jsonl"):
        if "error" not in r:
            recs[r["key"]] = r
    screen = {r["key"]: r for r in read_jsonl(raw / "baostock" / "screen.jsonl") if "error" not in r}
    rows = []
    for key, r in recs.items():
        if "dupont" not in r and key in screen:  # flagged rows: profit and cash fetched, dupont from the screen
            r = {**r, "dupont": screen[key].get("dupont")}
        code, year, quarter = key.split("|")
        p = pd.Timestamp(int(year), int(quarter) * 3, 1) + pd.offsets.MonthEnd(0)
        first = {name: (r.get(name) or [None])[0] for name in ("profit", "dupont", "cash")}
        d = pit.bs_derive(first["profit"], first["dupont"], first["cash"])
        rows.append({"symbol": pit.symbol_from_bs(code), "period_end": p,
                     "bs_status": "ok" if any(first.values()) else "empty", **d})
    bs = pd.DataFrame(rows)
    if bs.empty:
        return bs
    return bs.merge(targets, on=["symbol", "period_end"], how="left")


def load_bs_previews(raw: Path) -> pd.DataFrame:
    fc, ex = [], []
    for r in read_jsonl(raw / "baostock" / "previews.jsonl"):
        if "error" not in r:
            fc += r.get("forecast") or []
            ex += r.get("express") or []
    parts = [pit.parse_bs_forecast(fc), pit.parse_bs_express(ex)]
    return pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) else pd.DataFrame()


def _counts(series) -> dict:
    return {str(k): int(v) for k, v in pd.Series(series).value_counts(dropna=False).items()}


def date_agreement(a: pd.Series, b: pd.Series) -> dict:
    a, b = pd.to_datetime(a), pd.to_datetime(b)
    ok = a.notna() & b.notna()
    d = (a[ok] - b[ok]).dt.days
    return {"compared": int(ok.sum()), "same_day": int((d == 0).sum()), "within_1_day": int((d.abs() <= 1).sum()),
            "first_later_by_2plus": int((d >= 2).sum()), "first_earlier_by_2plus": int((d <= -2).sum()),
            "same_day_pct": round(100 * float((d == 0).mean()), 2) if len(d) else None}


def stage_assemble(args, status: Status) -> None:
    out, raw = args.out, args.raw
    report = {"built_at": now_iso(), "out_dir": str(out)}
    em, em_meta = load_em_frames(raw, "main", pit.parse_em_main)
    cpd, cpd_meta = load_em_frames(raw, "cpd", pit.parse_em_cpd)
    dmsk = load_dmsk(raw)
    uni = load_universe(raw)
    events = load_events(raw)
    targets = read_targets(out)
    bs = load_bs_checks(raw, targets)
    report["em_fetch"] = {"main_incomplete_periods": [m["period"] for m in em_meta if not m["complete"]],
                          "cpd_incomplete_periods": [m["period"] for m in cpd_meta if not m["complete"]],
                          "periods": len(em_meta), "fetched_first": min((m["fetched_at"] for m in em_meta), default=None),
                          "fetched_last": max((m["fetched_at"] for m in em_meta), default=None)}

    # --- duplicates and units -------------------------------------------------
    dup = em.duplicated(["symbol", "period_end"], keep=False)
    report["duplicates"] = {"em_main_rows": int(dup.sum()), "em_main_examples": em[dup].head(10)[["symbol", "period_end"]].astype(str).values.tolist(),
                            "cninfo_ann_ids": int(events["ann_id"].duplicated().sum())}
    em = em.drop_duplicates(["symbol", "period_end"], keep="last")
    # day each period was downloaded (Shanghai date): a revision without other dated evidence is public from then
    seen = {pd.Timestamp(m["period"]): pd.Timestamp(m["fetched_at"]).tz_convert("Asia/Shanghai").tz_localize(None).normalize()
            for m in em_meta if m.get("fetched_at")}
    em["observed_date"] = em["period_end"].map(seen)
    flags = pit.unit_flags(em)
    report["unit_flags"] = {"counts": _counts(flags["flag"]) if not flags.empty else {},
                            "examples": flags.head(15)[["symbol", "period_end", "flag"]].astype(str).values.tolist() if not flags.empty else []}
    report["missing_values"] = {f: int(em[f].isna().sum()) for f in pit.FIELDS}
    report["missing_notice_date"] = int(em["notice_date"].isna().sum())

    # --- Eastmoney internal consistency -----------------------------------------
    m = em.merge(cpd, on=["symbol", "period_end"], how="inner")
    report["em_main_vs_cpd"] = {"rows": int(len(m)), "notice_date": date_agreement(m["notice_date"], m["cpd_notice_date"]),
                                "parent_netprofit": _counts(pit.agreement(m["parent_netprofit"], m["cpd_parent_netprofit"])),
                                "revenue_total": _counts(pit.agreement(m["revenue_total"], m["cpd_revenue_total"]))}
    m = em.merge(dmsk, on=["symbol", "period_end"], how="inner")
    report["em_main_vs_dmsk"] = {"rows": int(len(m)),
                                 **{f: _counts(pit.agreement(m[f], m["dmsk_" + f])) for f in ("parent_netprofit", "revenue_total", "deducted_netprofit", "cfo")},
                                 "dmsk_date_after_first_notice_pct": round(100 * float((m["dmsk_notice_date"] > m["notice_date"]).mean()), 2) if len(m) else None,
                                 "scale_mismatch_parent": int(pit.scale_mismatch(m["parent_netprofit"], m["dmsk_parent_netprofit"]).sum())}

    # --- coverage --------------------------------------------------------------
    periods = [p for p in periods_until_today() if p >= STUDY_START]
    shsz = em[em["symbol"].str.endswith((".SH", ".SZ"))]
    cov, missing = pit.coverage_by_period(uni, shsz, periods)
    cov.to_csv(out / "coverage_by_period.csv", index=False)
    missing = missing.merge(uni[["symbol", "name_bs", "ipo_date", "out_date"]], on="symbol", how="left")
    missing.to_csv(out / "missing_reports.csv", index=False, encoding="utf-8-sig")
    in_em = set(shsz["symbol"])
    gone = uni[uni["out_date"].notna() & (uni["out_date"] >= STUDY_START)]
    report["coverage"] = {"expected": int(cov["expected"].sum()), "present": int(cov["present"].sum()),
                          "coverage_pct": round(100 * cov["present"].sum() / max(cov["expected"].sum(), 1), 2),
                          "worst_periods": cov.sort_values("coverage_pct").head(5).astype(str).values.tolist(),
                          "missing_by_period_tail": cov.tail(4).astype(str).values.tolist(),
                          "universe_stocks": int(len(uni)), "delisted_since_2015": int(len(gone)),
                          "delisted_with_any_em_row": int(gone["symbol"].isin(in_em).sum()),
                          "stocks_with_missing": int(missing["symbol"].nunique()),
                          "missing_never_listed_in_em": int((~missing["symbol"].isin(in_em)).sum()),
                          "bj_rows": int(em["symbol"].str.endswith(".BJ").sum()), "bj_stocks": int(em.loc[em["symbol"].str.endswith(".BJ"), "symbol"].nunique())}

    # --- events, links, BaoStock checks ----------------------------------------
    links = pit.link_events(events, em)
    report["cninfo"] = {"announcements": int(len(events)), "by_kind": _counts(events["kind"]),
                        "linked_stock_periods": int(links[["symbol", "period_end"]].drop_duplicates().shape[0]) if not links.empty else 0}
    if not bs.empty:
        bsj = bs.merge(em, on=["symbol", "period_end"], how="left", suffixes=("_bs", ""))
        res = {}
        why = bsj["why"].fillna("")
        for name, sub in (("sample", bsj[why.str.contains("sample")]),
                          ("sample_without_event", bsj[why.eq("sample")]),  # residual risk of unverified rows
                          ("candidates", bsj[why.str.contains("candidate")]),
                          ("flagged_by_screen", bsj[why.str.contains("screen")])):
            r = {"stock_periods": int(len(sub)), "baostock_empty": int((sub["bs_status"] == "empty").sum()),
                 "em_missing": int(sub["notice_date"].isna().sum())}
            ok = sub[(sub["bs_status"] == "ok") & sub["notice_date"].notna()]
            r["compared"] = int(len(ok))
            for f in ("parent_netprofit", "revenue_total", "revenue_op", "cfo"):
                for tol in (1e-3, 1e-2):
                    r[f"{f}@{tol:g}"] = _counts(pit.agreement(ok[f], ok[f + "_bs"], ok[f + "_err"], rel_tol=tol))
            st = pd.DataFrame({f: pit.agreement(ok[f], ok[f + "_bs"], ok[f + "_err"]) for f in pit.CHECK_FIELDS})
            all_agree = ~(st == "differ").any(axis=1) & (st == "agree").any(axis=1)
            r["all_check_fields_agree_pct"] = round(100 * float(all_agree.mean()), 2) if len(ok) else None
            r["any_field_differs"] = int((st == "differ").any(axis=1).sum())
            r["notice_vs_bs_pubdate"] = date_agreement(ok["notice_date"], ok["bs_pub_date"])
            r["scale_mismatch_parent"] = int(pit.scale_mismatch(ok["parent_netprofit"], ok["parent_netprofit_bs"]).sum())
            # what the one-query screen would have said about these fully checked rows (its miss rate)
            sv = pd.Series(pit.screen_verdict(ok["parent_netprofit"], ok["revenue_total"], ok["bs_ratio"], ok["bs_ratio_err"]))
            fv = pd.Series(np.where((st == "differ").any(axis=1), "differ", np.where((st == "agree").any(axis=1), "agree", "na")))
            r["screen_vs_full"] = _counts("screen_" + sv + "/full_" + fv)
            if name.startswith("sample"):
                d = ok[(st == "differ").any(axis=1).to_numpy()]
                r["differ_rows"] = [{"symbol": x.symbol, "period_end": f"{x.period_end:%Y-%m-%d}",
                                     **{f: [None if pd.isna(getattr(x, f)) else round(float(getattr(x, f))),
                                            None if pd.isna(getattr(x, f + "_bs")) else round(float(getattr(x, f + "_bs")))]
                                        for f in pit.CHECK_FIELDS}} for x in d.itertuples(index=False)]
            res[name] = r
        report["baostock_checks"] = res
        smp = bsj[bsj["why"].fillna("").str.contains("sample")].copy()
        smp.to_csv(out / "work" / "sample_checks.csv", index=False, encoding="utf-8-sig")
    else:
        report["baostock_checks"] = {}
    cn = pd.DataFrame(read_jsonl(raw / "cninfo" / "sample_dates.jsonl"))
    if not cn.empty:
        cn["period_end"] = pd.to_datetime(cn["period_end"])
        cn["cn_date"] = pd.to_datetime(cn["cn_date"], errors="coerce")
        cn = cn.merge(em[["symbol", "period_end", "notice_date"]], on=["symbol", "period_end"], how="left")
        report["cninfo_sample_dates"] = {"rows": int(len(cn)), "found": int(cn["cn_date"].notna().sum()),
                                         "vs_em_notice": date_agreement(cn["notice_date"], cn["cn_date"])}
        cn.to_csv(out / "work" / "sample_cninfo_dates.csv", index=False, encoding="utf-8-sig")

    # --- one-query screen of all other listed stock-periods ----------------------
    scr = screen_frame(em, load_screen(raw))
    report["screen"] = {"rows": int(len(scr)), "verdict": _counts(scr["screen"]),
                        "notice_vs_bs_pubdate": date_agreement(scr["notice_date"], scr["screen_pub_date"]),
                        "differ_by_period_year": {str(k): int(v) for k, v in
                                                  scr.loc[scr["screen"] == "differ", "period_end"].dt.year.value_counts().sort_index().items()}}

    # --- tables -----------------------------------------------------------------
    pit_tab = pit.build_pit(em, dmsk, bs.drop(columns=["why"], errors="ignore") if not bs.empty else None, links,
                            screen=scr[["symbol", "period_end", "screen", "screen_pub_date"]] if not scr.empty else None)
    pit_tab = pit_tab.merge(uni[["symbol", "ipo_date", "out_date"]], on="symbol", how="left")
    pit_tab["pre_listing"] = (pit_tab["period_end"] < pit_tab["ipo_date"]).where(pit_tab["ipo_date"].notna())
    v2 = pit_tab[pit_tab["version"] == 2]
    v1_dates = pit_tab[pit_tab["version"] == 1].set_index(["symbol", "period_end"])["available_date"]
    lag = (v2.set_index(["symbol", "period_end"])["available_date"] - v1_dates.reindex(v2.set_index(["symbol", "period_end"]).index)).dt.days
    report["pit"] = {"rows": int(len(pit_tab)), "stocks": int(pit_tab["symbol"].nunique()), "status": _counts(pit_tab["status"]),
                     "strict_later_than_available": int((pit_tab["strict_date"] > pit_tab["available_date"]).sum()),
                     "no_available_date": int(pit_tab["available_date"].isna().sum()),
                     "revised_basis": _counts(v2["revision_basis"]),
                     "revision_lag_days": {"median": float(lag.median()) if len(lag) else None,
                                           "p10": float(lag.quantile(0.1)) if len(lag) else None,
                                           "p90": float(lag.quantile(0.9)) if len(lag) else None},
                     "differs_by_field": _counts(pit_tab.loc[pit_tab["version"] == 1, "differs"].replace("", np.nan).dropna().str.split(";").explode())}
    latest = em.merge(dmsk, on=["symbol", "period_end"], how="left").merge(cpd[["symbol", "period_end", "cpd_notice_date", "cpd_eps", "cpd_cfo_per_share", "cpd_entry_time"]],
                                                                         on=["symbol", "period_end"], how="left")
    latest = latest.merge(uni[["symbol", "ipo_date", "out_date"]], on="symbol", how="left")
    em_prev, _ = load_em_frames(raw, "forecast", pit.parse_em_forecast)
    em_expr, _ = load_em_frames(raw, "express", pit.parse_em_express)
    prev = pd.concat([x for x in (em_prev, em_expr, load_bs_previews(raw)) if not x.empty], ignore_index=True)
    pdup = prev.duplicated(["source", "symbol", "period_end", "notice_date", "item"], keep=False)
    report["previews"] = {"rows": int(len(prev)), "by_source": _counts(prev["source"]), "duplicate_rows": int(pdup.sum()),
                          "bs_forecast_amount_parsed_pct": round(100 * float(prev.loc[prev["source"] == "bs_forecast", "value_low"].notna().mean()), 2)
                          if (prev["source"] == "bs_forecast").any() else None,
                          "em_forecast_items": _counts(prev.loc[prev["source"] == "em_forecast", "item"])}
    for name, df in (("reports_pit", pit_tab), ("reports_latest", latest), ("previews", prev), ("events_cninfo", events[events["kind"] != "other"]),
                     ("universe", uni), ("baostock_checks", bs), ("event_links", links)):
        if not df.empty:
            df.to_parquet(out / f"{name}.parquet", index=False)
    report["files"] = {p.name: round(p.stat().st_size / 1e6, 2) for p in sorted(out.glob("*.parquet"))}
    (out / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    status.set("assemble", finished=True, rows=len(pit_tab))
    log("assemble: " + json.dumps(report["files"]))


# ----------------------------------------------------------------------------- main
STAGES = {"em": stage_em, "cninfo": stage_cninfo, "bsbasic": stage_bsbasic, "bsprev": stage_bsprev, "targets": stage_targets,
          "screen": stage_screen, "flagged": stage_flagged, "bscheck": stage_bscheck, "cncheck": stage_cncheck,
          "assemble": stage_assemble}


def run_safely(fn, args, status: Status) -> bool:
    name = fn.__name__.replace("stage_", "")
    try:
        status.set(name, started=now_iso(), error=None)
        fn(args, status)
        return True
    except Exception as exc:
        log(f"{name} FAILED: {exc}\n{traceback.format_exc()}")
        status.set(name, error=str(exc)[:500])
        return False


def stage_all(args, status: Status) -> None:
    threads = [threading.Thread(target=run_safely, args=(fn, args, status)) for fn in (stage_em, stage_cninfo)]
    for t in threads:
        t.start()
    run_safely(stage_bsbasic, args, status)
    run_safely(stage_bsprev, args, status)
    for t in threads:
        t.join()
    if not run_safely(stage_targets, args, status):
        return
    cn = threading.Thread(target=run_safely, args=(stage_cncheck, args, status))
    cn.start()
    if run_safely(stage_screen, args, status):  # one query per stock-period first: most value per query
        run_safely(stage_flagged, args, status)
    run_safely(stage_bscheck, args, status)  # sample, correction candidates and what the screen flagged
    cn.join()
    run_safely(stage_assemble, args, status)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=[*STAGES, "all"])
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--sample", type=int, default=500, help="random stock-quarters checked against BaoStock and cninfo")
    ap.add_argument("--seed", type=int, default=20261123)
    ap.add_argument("--bs-workers", type=int, default=4)
    ap.add_argument("--em-interval", type=float, default=2.0, help="seconds between Eastmoney requests")
    ap.add_argument("--cn-interval", type=float, default=1.5, help="seconds between cninfo requests")
    ap.add_argument("--refresh-recent", type=int, default=0, help="refetch the last N report periods (old snapshot kept)")
    args = ap.parse_args()
    args.raw = args.out / "raw"
    args.raw.mkdir(parents=True, exist_ok=True)
    below_normal_priority()
    status = Status(args.out / "status.json")
    log(f"start {args.stage} -> {args.out} (pid {os.getpid()})")
    status.set("run", stage=args.stage, pid=os.getpid(), started=now_iso(), finished=None)
    if args.stage == "all":
        stage_all(args, status)
    else:
        run_safely(STAGES[args.stage], args, status)
    status.set("run", finished=now_iso())
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
