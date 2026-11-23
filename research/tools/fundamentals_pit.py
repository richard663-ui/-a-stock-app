"""Point-in-time (PIT) A-share fundamentals: parsing, versioning and as-of joins. Research only.

Pure pandas/numpy functions shared by ``research/tools/fundamentals_fetch.py`` (network, CLI) and
``tests/test_fundamentals_pit.py``. Nothing in this module downloads data.

Conventions
-----------
symbol          "600519.SH", "000001.SZ", "920002.BJ"
period_end      report period end: 03-31 Q1, 06-30 H1, 09-30 Q3, 12-31 FY. Figures are
                year-to-date cumulative, exactly as published.
money           CNY (yuan) as published; never scaled, filled or interpolated.
available_date  exchange disclosure date of a version (Eastmoney NOTICE_DATE, or the later of the
                Eastmoney and BaoStock dates when both exist). Use a version for decisions taken
                after the close of that date at the earliest, i.e. trade at the next session.
strict_date     >= available_date. Later only for unverified rows whose stored (latest) value may
                differ from what was first published: from strict_date on the value was public.

Why versions: Eastmoney serves the LATEST revised figures under the FIRST notice date (Kangmei
600518 FY2017 shows the 2019 corrected revenue 17.6bn under the 2018-04-26 notice; the original
report said 26.5bn). BaoStock keeps first-published figures, so where the two disagree the store
keeps version 1 (BaoStock, first notice date) and version 2 (Eastmoney, date of the revision).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

FIELDS = ["parent_netprofit", "deducted_netprofit", "revenue_total", "revenue_op", "cfo"]
CHECK_FIELDS = ["parent_netprofit", "revenue_total", "cfo"]
DEPENDS_ON = {"deducted_netprofit": "parent_netprofit", "revenue_op": "revenue_total"}
A_SHARE_TYPES = {"058001001", "058001008"}  # Eastmoney SECURITY_TYPE_CODE: A share, CDR
PERIOD_TYPE = {(3, 31): "Q1", (6, 30): "H1", (9, 30): "Q3", (12, 31): "FY"}
PERIOD_MONTH = {"Q1": (3, 31), "H1": (6, 30), "Q3": (9, 30), "FY": (12, 31)}
RATIO_HALF_UNIT = 5e-7  # BaoStock ratios carry six decimals
PREVIEW_COLS = ["source", "kind", "symbol", "period_end", "notice_date", "update_date", "item", "item_cn",
                "value_low", "value_high", "chg_pct_low", "chg_pct_high", "prior_value", "forecast_type",
                "text", "is_latest"]


# ----------------------------------------------------------------------------- periods, codes
def period_type(period_end) -> str | None:
    ts = pd.Timestamp(period_end)
    return PERIOD_TYPE.get((ts.month, ts.day))


def report_periods(start, end) -> list[pd.Timestamp]:
    """Quarter ends from ``start`` (inclusive) to the last quarter end strictly before ``end``."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    out = []
    for year in range(start.year, end.year + 1):
        for month, day in PERIOD_MONTH.values():
            ts = pd.Timestamp(year, month, day)
            if start <= ts < end:
                out.append(ts)
    return out


def filing_deadline(period_end) -> pd.Timestamp:
    """Legal deadline of the periodic report (annual 04-30 next year, Q1 04-30, H1 08-31, Q3 10-31)."""
    ts = pd.Timestamp(period_end)
    if ts.month == 12:
        return pd.Timestamp(ts.year + 1, 4, 30)
    return pd.Timestamp(ts.year, {3: 4, 6: 8, 9: 10}[ts.month], {3: 30, 6: 31, 9: 31}[ts.month])


def symbol_from_code(code) -> str | None:
    """Exchange suffix for a 6-digit A-share code; None for B shares and non-stock codes."""
    code = str(code or "").strip()
    if len(code) != 6 or not code.isdigit():
        return None
    if code.startswith(("60", "68")):
        return code + ".SH"
    if code.startswith(("00", "30")):
        return code + ".SZ"
    if code.startswith(("43", "83", "87", "88", "92")):
        return code + ".BJ"
    return None


def bs_code(symbol: str) -> str | None:
    code, _, exch = str(symbol).partition(".")
    return f"{exch.lower()}.{code}" if exch in ("SH", "SZ") else None


def symbol_from_bs(code: str) -> str:
    exch, _, num = str(code).partition(".")
    return f"{num}.{exch.upper()}"


# ----------------------------------------------------------------------------- small helpers
def _frame(rows) -> pd.DataFrame:
    return pd.DataFrame.from_records(rows) if rows else pd.DataFrame()


def _num(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _dates(series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.normalize()


def _col(df: pd.DataFrame, name: str):
    return df[name] if name in df.columns else pd.Series([None] * len(df), index=df.index, dtype="object")


def _a_shares(df: pd.DataFrame) -> pd.DataFrame:
    if "SECURITY_TYPE_CODE" in df.columns:
        df = df[df["SECURITY_TYPE_CODE"].astype(str).isin(A_SHARE_TYPES)]
    # Eastmoney also files IPO applicants under placeholder codes such as A22001.SZ (prospectus
    # figures, never traded under that code): only 6-digit exchange codes are kept.
    return df[df["SECUCODE"].astype(str).str.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)")]


def _f(value) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if np.isfinite(out) else float("nan")


# ----------------------------------------------------------------------------- Eastmoney parsers
EM_MAIN_MAP = {"PARENTNETPROFIT": "parent_netprofit", "KCFJCXSYJLR": "deducted_netprofit",
               "TOTALOPERATEREVE": "revenue_total", "OPERATE_INCOME_PK": "revenue_op", "NETCASH_OPERATE_PK": "cfo"}
MAIN_COLS = ["symbol", "name", "period_end", "period_type", "report_type", "org_type", "currency",
             "notice_date", "update_date", *FIELDS]


def parse_em_main(rows) -> pd.DataFrame:
    """RPT_F10_FINANCE_MAINFINADATA rows -> one row per (symbol, period_end); values are Eastmoney's
    latest (possibly revised) figures, notice_date is the FIRST publication date."""
    df = _frame(rows)
    if df.empty:
        return pd.DataFrame(columns=MAIN_COLS)
    df = _a_shares(df)
    out = pd.DataFrame({"symbol": df["SECUCODE"].astype(str), "name": _col(df, "SECURITY_NAME_ABBR"),
                        "period_end": _dates(df["REPORT_DATE"]), "report_type": _col(df, "REPORT_TYPE"),
                        "org_type": _col(df, "ORG_TYPE"), "currency": _col(df, "CURRENCY"),
                        "notice_date": _dates(df["NOTICE_DATE"]), "update_date": _dates(_col(df, "UPDATE_DATE"))})
    for src, dst in EM_MAIN_MAP.items():
        out[dst] = _num(df[src]) if src in df.columns else np.nan
    out["period_type"] = out["period_end"].map(period_type)
    return out[MAIN_COLS].reset_index(drop=True)


def parse_em_dmsk(income_rows, cash_rows) -> pd.DataFrame:
    """RPT_DMSK_FN_INCOME/CASHFLOW: latest figures with the date of the LATEST report that disclosed
    them (e.g. the next annual report's comparative column). Listed stocks only."""
    def part(rows, mapping):
        df = _frame(rows)
        cols = ["symbol", "period_end", "notice"] + list(mapping.values())
        if df.empty:
            return pd.DataFrame(columns=cols)
        df = _a_shares(df)
        out = pd.DataFrame({"symbol": df["SECUCODE"].astype(str), "period_end": _dates(df["REPORT_DATE"]),
                            "notice": _dates(df["NOTICE_DATE"])})
        for src, dst in mapping.items():
            out[dst] = _num(df[src]) if src in df.columns else np.nan
        return out[cols]

    inc = part(income_rows, {"PARENT_NETPROFIT": "dmsk_parent_netprofit", "TOTAL_OPERATE_INCOME": "dmsk_revenue_total",
                             "DEDUCT_PARENT_NETPROFIT": "dmsk_deducted_netprofit"})
    cash = part(cash_rows, {"NETCASH_OPERATE": "dmsk_cfo"})
    m = inc.merge(cash, on=["symbol", "period_end"], how="outer", suffixes=("_inc", "_cash"))
    m["dmsk_notice_date"] = m[["notice_inc", "notice_cash"]].max(axis=1)
    return m.drop(columns=["notice_inc", "notice_cash"])


def parse_em_cpd(rows) -> pd.DataFrame:
    """RPT_LICO_FN_CPD (业绩报表): used to cross-check NOTICE_DATE and values of the main table."""
    cols = ["symbol", "period_end", "cpd_notice_date", "cpd_parent_netprofit", "cpd_revenue_total",
            "cpd_eps", "cpd_cfo_per_share", "cpd_entry_time"]
    df = _frame(rows)
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = _a_shares(df)
    return pd.DataFrame({"symbol": df["SECUCODE"].astype(str), "period_end": _dates(df["REPORTDATE"]),
                         "cpd_notice_date": _dates(df["NOTICE_DATE"]),
                         "cpd_parent_netprofit": _num(_col(df, "PARENT_NETPROFIT")),
                         "cpd_revenue_total": _num(_col(df, "TOTAL_OPERATE_INCOME")),
                         "cpd_eps": _num(_col(df, "BASIC_EPS")), "cpd_cfo_per_share": _num(_col(df, "MGJYXJJE")),
                         "cpd_entry_time": pd.to_datetime(_col(df, "EITIME"), errors="coerce")})[cols].reset_index(drop=True)


# Eastmoney PREDICT_FINANCE_CODE -> item (names as served on 2026-11-23)
FORECAST_CODES = {"001": "revenue_main",        # 主营业务收入
                  "002": "net_profit",          # 净利润
                  "003": "eps",                 # 每股收益
                  "004": "parent_netprofit",    # 归属于上市公司股东的净利润
                  "005": "deducted_netprofit",  # 扣除非经常性损益后的净利润
                  "006": "revenue",             # 营业收入
                  "007": "non_recurring",       # 非经常性损益
                  "008": "revenue_deducted",    # 扣除后营业收入 (revenue net of items unrelated to the main business)
                  "009": "eps_deducted"}        # 扣非后每股收益


def forecast_item(name, code=None) -> str:
    """Item of an Eastmoney forecast row: by PREDICT_FINANCE_CODE, else by its Chinese name."""
    if code is not None and str(code) in FORECAST_CODES:
        return FORECAST_CODES[str(code)]
    n = str(name or "")
    deducted = "扣除" in n or "扣非" in n
    if "每股收益" in n:
        return "eps_deducted" if deducted else "eps"
    if "收入" in n:
        if deducted:
            return "revenue_deducted"
        return "revenue_total" if "营业总收入" in n else "revenue_main" if "主营" in n else "revenue"
    if "非经常性损益" in n and not deducted:
        return "non_recurring"
    if deducted and "净利润" in n:
        return "deducted_netprofit"
    if "归属" in n and "净利润" in n:
        return "parent_netprofit"
    if "净利润" in n:
        return "net_profit"
    return "other"


def _previews(**cols) -> pd.DataFrame:
    n = max((len(v) for v in cols.values() if hasattr(v, "__len__") and not isinstance(v, str)), default=0)
    out = pd.DataFrame(index=range(n))
    for col in PREVIEW_COLS:
        val = cols.get(col)
        out[col] = val.to_numpy() if isinstance(val, pd.Series) else val
    for col in ("period_end", "notice_date", "update_date"):
        out[col] = pd.to_datetime(out[col], errors="coerce")
    for col in ("value_low", "value_high", "chg_pct_low", "chg_pct_high", "prior_value"):
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("float64")
    return out


def parse_em_forecast(rows) -> pd.DataFrame:
    """业绩预告 (Eastmoney keeps only the LATEST forecast per stock, period and item)."""
    df = _frame(rows)
    if df.empty:
        return pd.DataFrame(columns=PREVIEW_COLS)
    df = _a_shares(df)
    return _previews(source="em_forecast", kind="forecast", symbol=df["SECUCODE"].astype(str),
                     period_end=_dates(df["REPORT_DATE"]), notice_date=_dates(df["NOTICE_DATE"]), update_date=pd.NaT,
                     item=pd.Series([forecast_item(n, c) for n, c in zip(_col(df, "PREDICT_FINANCE"),
                                                                         _col(df, "PREDICT_FINANCE_CODE"))], index=df.index),
                     item_cn=_col(df, "PREDICT_FINANCE"),
                     value_low=_num(_col(df, "PREDICT_AMT_LOWER")), value_high=_num(_col(df, "PREDICT_AMT_UPPER")),
                     chg_pct_low=_num(_col(df, "ADD_AMP_LOWER")), chg_pct_high=_num(_col(df, "ADD_AMP_UPPER")),
                     prior_value=_num(_col(df, "PREYEAR_SAME_PERIOD")), forecast_type=_col(df, "PREDICT_TYPE"),
                     text=_col(df, "PREDICT_CONTENT"), is_latest=_col(df, "IS_LATEST").astype(str).eq("T"))


def parse_em_express(rows) -> pd.DataFrame:
    """业绩快报 (Eastmoney, listed stocks only): one long row per item."""
    df = _frame(rows)
    if df.empty:
        return pd.DataFrame(columns=PREVIEW_COLS)
    df = _a_shares(df)
    base = dict(source="em_express", kind="express", symbol=df["SECUCODE"].astype(str),
                period_end=_dates(df["REPORT_DATE"]), notice_date=_dates(df["NOTICE_DATE"]),
                update_date=_dates(_col(df, "UPDATE_DATE")), is_latest=True)
    parts = []
    for item, value, prior in (("parent_netprofit", "PARENT_NETPROFIT", "PARENT_NETPROFIT_SQ"),
                               ("revenue_total", "TOTAL_OPERATE_INCOME", "TOTAL_OPERATE_INCOME_SQ"),
                               ("eps", "BASIC_EPS", None), ("roe_wa_pct", "WEIGHTAVG_ROE", None)):
        v = _num(_col(df, value))
        parts.append(_previews(**base, item=item, value_low=v, value_high=v,
                               prior_value=_num(_col(df, prior)) if prior else np.nan))
    return pd.concat(parts, ignore_index=True)


# ----------------------------------------------------------------------------- BaoStock parsers
_AMT = r"(-?\d[\d,]*(?:\.\d+)?)"
_UNIT = r"(亿元|万元|元)"
_RANGE_RE = re.compile(_AMT + r"\s*" + _UNIT + r"?\s*(?:至|到|~|～|-|—|－|–)\s*约?\s*" + _AMT + r"\s*" + _UNIT)
_SINGLE_RE = re.compile(_AMT + r"\s*" + _UNIT)
_SCALE = {"亿元": 1e8, "万元": 1e4, "元": 1.0}
_CHANGE_WORDS = re.compile(r"增加|减少|增长|下降|降低|上升|变动|同比|相比|比上年|较上年")


def parse_forecast_amounts(text) -> tuple[float, float, str | None]:
    """(low, high, item) in CNY from a forecast abstract such as
    '预计2019年1-12月归属于上市公司股东的净利润亏损:135,000万元至165,000万元。'.
    Returns NaNs when the text gives only a change or a percentage, never a guess."""
    t = str(text or "")
    k = t.find("净利润")
    if k < 0:
        return np.nan, np.nan, None
    start = k + 3
    rng, single = _RANGE_RE.search(t, start), _SINGLE_RE.search(t, start)
    m = rng if rng is not None and (single is None or rng.start() <= single.start()) else single
    if m is None:
        return np.nan, np.nan, None
    between = t[start:m.start()]
    if _CHANGE_WORDS.search(between):
        return np.nan, np.nan, None
    sign = -1.0 if "亏损" in between else 1.0
    if m is rng:
        a, ua, b, ub = m.group(1), m.group(2) or m.group(4), m.group(3), m.group(4)
        vals = sorted([sign * float(a.replace(",", "")) * _SCALE[ua], sign * float(b.replace(",", "")) * _SCALE[ub]])
    else:
        v = sign * float(m.group(1).replace(",", "")) * _SCALE[m.group(2)]
        vals = [v, v]
    before = t[max(0, k - 25):k]
    item = ("deducted_netprofit" if "扣除" in before or "扣非" in before
            else "parent_netprofit" if "归属" in before or "归母" in before else "net_profit")
    return vals[0], vals[1], item


def parse_bs_forecast(records) -> pd.DataFrame:
    """BaoStock query_forecast_report rows (all versions, incl. delisted stocks)."""
    df = _frame(records)
    if df.empty:
        return pd.DataFrame(columns=PREVIEW_COLS)
    parsed = [parse_forecast_amounts(x) for x in _col(df, "profitForcastAbstract")]
    up, dn = _num(_col(df, "profitForcastChgPctUp")), _num(_col(df, "profitForcastChgPctDwn"))
    return _previews(source="bs_forecast", kind="forecast", symbol=df["code"].map(symbol_from_bs),
                     period_end=_dates(df["profitForcastExpStatDate"]), notice_date=_dates(df["profitForcastExpPubDate"]),
                     update_date=pd.NaT, item=pd.Series([p[2] or "pct_only" for p in parsed], index=df.index),
                     item_cn=None, value_low=pd.Series([p[0] for p in parsed], index=df.index),
                     value_high=pd.Series([p[1] for p in parsed], index=df.index),
                     chg_pct_low=np.fmin(up, dn), chg_pct_high=np.fmax(up, dn), prior_value=np.nan,
                     forecast_type=_col(df, "profitForcastType"), text=_col(df, "profitForcastAbstract"), is_latest=None)


def parse_bs_express(records) -> pd.DataFrame:
    """BaoStock query_performance_express_report: no absolute profit; yoy as fractions (-0.36 = -36%)."""
    df = _frame(records)
    if df.empty:
        return pd.DataFrame(columns=PREVIEW_COLS)
    base = dict(source="bs_express", kind="express", symbol=df["code"].map(symbol_from_bs),
                period_end=_dates(df["performanceExpStatDate"]), notice_date=_dates(df["performanceExpPubDate"]),
                update_date=_dates(_col(df, "performanceExpUpdateDate")), is_latest=None)
    parts = []
    for item, col in (("eps_diluted", "performanceExpressEPSDiluted"), ("roe_wa_pct", "performanceExpressROEWa"),
                      ("revenue_yoy_frac", "performanceExpressGRYOY"), ("op_profit_yoy_frac", "performanceExpressOPYOY"),
                      ("eps_yoy_frac", "performanceExpressEPSChgPct"), ("total_assets", "performanceExpressTotalAsset"),
                      ("net_assets", "performanceExpressNetAsset")):
        v = _num(_col(df, col))
        parts.append(_previews(**base, item=item, value_low=v, value_high=v))
    return pd.concat(parts, ignore_index=True)


def bs_derive(profit: dict | None, dupont: dict | None = None, cash: dict | None = None) -> dict:
    """First-published figures from BaoStock ratios, each with an absolute rounding bound (*_err).

    parent_netprofit = netProfit * dupontPnitoni; revenue_total = netProfit / dupontNitogr;
    revenue_op = netProfit / npMargin; cfo = CFOToNP * netProfit (else CFOToOR * revenue_op)."""
    profit, dupont, cash = profit or {}, dupont or {}, cash or {}
    q = RATIO_HALF_UNIT
    pub = profit.get("pubDate") or dupont.get("pubDate") or cash.get("pubDate")
    npt = _f(profit.get("netProfit"))
    out = {"bs_pub_date": pd.to_datetime(pub, errors="coerce"), "bs_net_profit": npt,
           "bs_mb_revenue": _f(profit.get("MBRevenue"))}
    pn, nitogr, npm = _f(dupont.get("dupontPnitoni")), _f(dupont.get("dupontNitogr")), _f(profit.get("npMargin"))
    cfonp, cfoor = _f(cash.get("CFOToNP")), _f(cash.get("CFOToOR"))
    out["parent_netprofit"], out["parent_netprofit_err"] = npt * pn, abs(npt) * q
    for name, ratio in (("revenue_total", nitogr), ("revenue_op", npm)):
        if np.isfinite(ratio) and ratio != 0:
            out[name] = npt / ratio
            out[name + "_err"] = abs(out[name] / ratio) * q
        else:
            out[name], out[name + "_err"] = np.nan, np.nan
    if np.isfinite(cfonp) and np.isfinite(npt) and npt != 0:
        out["cfo"], out["cfo_err"] = cfonp * npt, abs(npt) * q
    elif np.isfinite(cfoor) and np.isfinite(out["revenue_op"]):
        out["cfo"] = cfoor * out["revenue_op"]
        out["cfo_err"] = abs(out["revenue_op"]) * q + abs(cfoor) * out["revenue_op_err"]
    else:
        out["cfo"], out["cfo_err"] = np.nan, np.nan
    out["bs_ratio"], out["bs_ratio_err"] = bs_ratio(dupont)
    return out


def bs_ratio(dupont: dict | None) -> tuple[float, float]:
    """First-published 归母净利润 / 营业总收入 from ONE BaoStock dupont row (dupontPnitoni x dupontNitogr)
    and its rounding bound: the one-query screen for silent restatements."""
    d = dupont or {}
    pn, ng = _f(d.get("dupontPnitoni")), _f(d.get("dupontNitogr"))
    return pn * ng, RATIO_HALF_UNIT * (abs(pn) + abs(ng)) + RATIO_HALF_UNIT ** 2


def screen_verdict(parent, revenue_total, ratio_bs, ratio_err=None, rel_tol: float = 1e-3) -> np.ndarray:
    """'agree' / 'differ' / 'na': does Eastmoney's (latest) 归母/营业总收入 equal the first-published ratio?

    Catches restatements that change profit or revenue; misses cash-flow-only changes and the rare
    restatement that scales profit and revenue by the same factor (the random sample measures both)."""
    parent = np.asarray(parent, dtype="float64")
    rev = np.asarray(revenue_total, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        em = np.where(rev != 0, parent / rev, np.nan)
    bs = np.asarray(ratio_bs, dtype="float64")
    err = np.zeros_like(bs) if ratio_err is None else np.nan_to_num(np.asarray(ratio_err, dtype="float64"))
    both = np.isfinite(em) & np.isfinite(bs)
    tol = np.maximum(rel_tol * np.maximum(np.abs(em), np.abs(bs)), 3 * err)
    with np.errstate(invalid="ignore"):
        ok = np.abs(em - bs) <= tol
    return np.where(both, np.where(ok, "agree", "differ"), "na")


def agreement(em, bs, err=None, rel_tol: float = 1e-3, abs_floor: float = 1e3) -> np.ndarray:
    """'agree' / 'differ' / 'na' per element: |em - bs| <= max(rel_tol*scale, 3*rounding bound, floor)."""
    em = np.asarray(em, dtype="float64")
    bs = np.asarray(bs, dtype="float64")
    err = np.zeros_like(em) if err is None else np.nan_to_num(np.asarray(err, dtype="float64"))
    both = np.isfinite(em) & np.isfinite(bs)
    tol = np.maximum(np.maximum(rel_tol * np.maximum(np.abs(em), np.abs(bs)), 3 * err), abs_floor)
    with np.errstate(invalid="ignore"):
        ok = np.abs(em - bs) <= tol
    return np.where(both, np.where(ok, "agree", "differ"), "na")


# ----------------------------------------------------------------------------- cninfo titles
_TAG = re.compile(r"<[^>]+>")
_YEAR = re.compile(r"(20\d{2}|19\d{2})\s*年")
_KINDS = [("H1", re.compile(r"半年度(?:财务)?报告|半年报|中期报告|半年度(?:财务)?报表")),
          ("Q1", re.compile(r"第一季度(?:财务)?报告|一季度(?:财务)?报告|1季度报告|一季报|第一季度(?:财务)?报表")),
          ("Q3", re.compile(r"第三季度(?:财务)?报告|三季度(?:财务)?报告|3季度报告|三季报|第三季度(?:财务)?报表")),
          ("FY", re.compile(r"年度(?:财务)?报告|年报|年度(?:财务)?报表"))]
_REVISED = re.compile(r"(?:更正|更新|修订|修正|补充)(?:后|版|稿)|[（(](?:更正|更新|修订|修正)[）)]")
_CANCELLED = re.compile(r"已取消|作废|撤销")
_RESTATE = re.compile(r"差错更正|追溯调整|追溯重述|会计差错")
_CORRECT = re.compile(r"更正|修正|勘误")
_REPORT_WORD = re.compile(r"报告|年报|季报|半年报|报表")
_FIN_WORD = re.compile(r"财务数据|财务报表|主要会计数据|财务指标|会计数据")
_GENERIC = re.compile(r"(?:关于)?(?:公司)?(?:的)?(?:补充)?更正(?:的)?(?:公告|说明)?")


def clean_title(title) -> str:
    return _TAG.sub("", str(title or "")).strip()


def report_periods_in_title(title) -> list[tuple[pd.Timestamp, str]]:
    """Report periods referenced in an announcement title, e.g.
    '关于2018年年度报告及2019年第一季度报告的更正公告' -> [(2018-12-31, FY), (2019-03-31, Q1)]."""
    t = clean_title(title)
    years = list(_YEAR.finditer(t))
    out = []
    for i, m in enumerate(years):
        seg = t[m.start() + 4: years[i + 1].start() if i + 1 < len(years) else len(t)]
        best = None
        for kind, pat in _KINDS:
            hit = pat.search(seg)
            if hit and (best is None or hit.start() < best[0]):
                best = (hit.start(), kind)
        if best:
            month, day = PERIOD_MONTH[best[1]]
            item = (pd.Timestamp(int(m.group(1)), month, day), best[1])
            if item not in out:
                out.append(item)
    return out


def classify_announcement(title, query_kind: str) -> str:
    """revised_report | correction_notice | correction_unspecified | restatement | cancelled | other."""
    t = clean_title(title)
    periods = report_periods_in_title(t)
    if _CANCELLED.search(t):
        return "cancelled"
    if _RESTATE.search(t):
        return "restatement"
    if query_kind == "revised" and periods and _REVISED.search(t):
        return "revised_report"
    if _CORRECT.search(t) and periods and _REPORT_WORD.search(t):
        return "correction_notice"
    if _CORRECT.search(t) and (_FIN_WORD.search(t) or _GENERIC.fullmatch(t)):
        return "correction_unspecified"
    return "other"


def parse_cninfo(announcements, query_kind: str) -> pd.DataFrame:
    """cninfo hisAnnouncement rows -> events (A shares only; times are China Standard Time)."""
    cols = ["ann_id", "symbol", "title", "ann_time", "ann_date", "kind", "query_kind", "periods", "url"]
    df = _frame(announcements)
    if df.empty:
        return pd.DataFrame(columns=cols)
    titles = df["announcementTitle"].map(clean_title)
    ts = pd.to_datetime(_num(df["announcementTime"]), unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    out = pd.DataFrame({"ann_id": df["announcementId"].astype(str), "symbol": df["secCode"].map(symbol_from_code),
                        "title": titles, "ann_time": ts, "ann_date": ts.dt.normalize(),
                        "kind": [classify_announcement(t, query_kind) for t in titles], "query_kind": query_kind,
                        "periods": [";".join(f"{p:%Y-%m-%d}" for p, _ in report_periods_in_title(t)) for t in titles],
                        "url": _col(df, "adjunctUrl")})
    return out[out["symbol"].notna()][cols].reset_index(drop=True)


def link_events(events: pd.DataFrame, reports: pd.DataFrame, unspecified_days: int = 45,
                restatement_days: int = 1100) -> pd.DataFrame:
    """Map announcement events to (symbol, period_end, event_date, kind, ann_id).

    Explicit periods come from the title. A restatement also links to every period first published in
    the ``restatement_days`` before it; a correction without a period links to the periods first
    published in the ``unspecified_days`` before it."""
    cols = ["symbol", "period_end", "event_date", "kind", "ann_id"]
    ev = events[events["kind"].isin(["revised_report", "correction_notice", "correction_unspecified", "restatement"])]
    if ev.empty:
        return pd.DataFrame(columns=cols)
    explicit = ev[ev["periods"].astype(str).str.len() > 0].assign(period_end=lambda d: d["periods"].str.split(";")).explode("period_end")
    explicit = explicit.assign(period_end=pd.to_datetime(explicit["period_end"]), event_date=explicit["ann_date"])[cols]
    parts = [explicit]
    rep = reports[["symbol", "period_end", "notice_date"]].dropna(subset=["notice_date"])
    for kind, days, only_without_period in (("restatement", restatement_days, False),
                                            ("correction_unspecified", unspecified_days, True)):
        sub = ev[ev["kind"].eq(kind)]
        if only_without_period:
            sub = sub[sub["periods"].astype(str).str.len() == 0]
        if sub.empty:
            continue
        m = sub[["symbol", "ann_date", "kind", "ann_id"]].merge(rep, on="symbol")
        m = m[(m["notice_date"] <= m["ann_date"]) & (m["notice_date"] > m["ann_date"] - pd.Timedelta(days=days))]
        parts.append(m.rename(columns={"ann_date": "event_date"})[cols])
    out = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) else pd.DataFrame(columns=cols)
    return out.drop_duplicates(["symbol", "period_end", "event_date", "kind"]).reset_index(drop=True)


# ----------------------------------------------------------------------------- versions
PIT_COLS = ["symbol", "period_end", "period_type", "version", "available_date", "strict_date", *FIELDS,
            "status", "value_source", "notice_date_em", "notice_date_bs", "dmsk_notice_date", "update_date_em",
            "revision_date", "revision_basis", "n_revision_events", "revision_kinds", "differs", "screen", "org_type",
            "currency", "name"]


def _rowmax(*series) -> pd.Series:
    return pd.concat(series, axis=1).max(axis=1)


def build_pit(em: pd.DataFrame, dmsk: pd.DataFrame | None = None, bs: pd.DataFrame | None = None,
              links: pd.DataFrame | None = None, check_fields=CHECK_FIELDS, rel_tol: float = 1e-3,
              screen: pd.DataFrame | None = None) -> pd.DataFrame:
    """Versioned PIT table: one row per (symbol, period_end, version).

    em     parse_em_main output (latest values, first notice date); optional observed_date = the day
           the row was downloaded (dates a revision that has no other dated evidence)
    dmsk   parse_em_dmsk output (date of the latest report carrying the period's figures)
    bs     BaoStock first-published figures: symbol, period_end, bs_pub_date and bs_derive fields
    links  link_events output (correction / restatement announcements per period)
    screen one-query BaoStock screen: symbol, period_end, screen ('agree'/'differ'/'na'), screen_pub_date

    verified            BaoStock agrees with Eastmoney -> one version at the first notice date
    first_from_baostock BaoStock differs -> v1 = BaoStock (first published), v2 = Eastmoney 'revised',
                        dated at the latest evidence of the revision (announcement, later report or update)
    screened            not fully checked, but the first-published 归母/营业总收入 ratio equals Eastmoney's
                        -> Eastmoney values at the first notice date
    revised_only        the screen differs and no full check exists: the first-published figures are
                        unknown, so only Eastmoney's revised version is kept (version 2, revision date)
    unverified          not checked -> Eastmoney values at the first notice date; strict_date moves to
                        the latest correction announcement when one exists."""
    base = em.copy()
    keys = ["symbol", "period_end"]
    if dmsk is not None and not dmsk.empty:
        base = base.merge(dmsk, on=keys, how="left")
        same = np.ones(len(base), dtype=bool)
        for f in ("parent_netprofit", "revenue_total", "cfo"):
            same &= agreement(base[f], base["dmsk_" + f], rel_tol=1e-6, abs_floor=1.0) != "differ"
        base["dmsk_same"] = same & base["dmsk_notice_date"].notna()
    else:
        base["dmsk_notice_date"], base["dmsk_same"] = pd.NaT, False
    base["n_revision_events"], base["revision_kinds"], base["event_date_max"] = 0, "", pd.NaT
    if links is not None and not links.empty:
        ev = links.merge(base[keys + ["notice_date"]], on=keys)
        ev = ev[ev["event_date"] > ev["notice_date"]]
        if not ev.empty:
            agg = ev.groupby(keys).agg(event_date_max=("event_date", "max"), n_revision_events=("ann_id", "nunique"),
                                       revision_kinds=("kind", lambda s: ";".join(sorted(set(s))))).reset_index()
            base = base.drop(columns=["n_revision_events", "revision_kinds", "event_date_max"]).merge(agg, on=keys, how="left")
            base["n_revision_events"] = base["n_revision_events"].fillna(0).astype(int)
            base["revision_kinds"] = base["revision_kinds"].fillna("")
    later_report = base["dmsk_notice_date"].where(base["dmsk_same"] & (base["dmsk_notice_date"] > base["notice_date"]))
    later_update = base["update_date"].where(~base["dmsk_same"] & (base["update_date"] > base["notice_date"]))
    base["revision_date"] = _rowmax(base["event_date_max"], later_report, later_update)
    base["revision_basis"] = np.select([base["event_date_max"].eq(base["revision_date"]), later_report.eq(base["revision_date"]),
                                        later_update.eq(base["revision_date"])], ["event", "later_report", "em_update"], "")

    if screen is not None and not screen.empty:
        base = base.merge(screen[keys + ["screen", "screen_pub_date"]], on=keys, how="left")
        base["screen"] = base["screen"].fillna("na")
        base["screen_pub_date"] = pd.to_datetime(base["screen_pub_date"])
    else:
        base["screen"], base["screen_pub_date"] = "na", pd.NaT
    checked = pd.Series(False, index=base.index)
    base["notice_date_bs"], base["differs"] = pd.NaT, ""
    if bs is not None and not bs.empty:
        b = bs.rename(columns={f: "bs_" + f for f in FIELDS + [f + "_err" for f in FIELDS] if f in bs.columns})
        base = base.merge(b, on=keys, how="left")
        base["notice_date_bs"] = base["bs_pub_date"]
        status = {f: agreement(base[f], base["bs_" + f], base.get("bs_" + f + "_err"), rel_tol=rel_tol) for f in check_fields}
        checked = base["bs_pub_date"].notna() | pd.concat([base["bs_" + f].notna() for f in check_fields], axis=1).any(axis=1)
        names = pd.Series("", index=base.index)
        for f in check_fields:
            names = names.where(status[f] != "differ", names + f + ";")
        base["differs"] = names.str.rstrip(";")
    differs = base["differs"].str.len() > 0
    screened = ~checked & base["screen"].eq("agree")
    flagged = ~checked & base["screen"].eq("differ")
    case_b, case_a, case_c = checked & differs, checked & ~differs, ~checked & ~flagged
    base["notice_date_bs"] = pd.to_datetime(base["notice_date_bs"]).where(checked, base["screen_pub_date"])
    base.loc[flagged, "differs"] = "ratio"

    avail = _rowmax(base["notice_date"], base["notice_date_bs"])
    v1 = base.copy()
    v1["version"], v1["available_date"], v1["value_source"] = 1, avail, "eastmoney"
    v1["status"] = np.select([case_a, case_b, screened], ["verified", "first_from_baostock", "screened"], "unverified")
    v1["strict_date"] = avail
    late = case_c & (base["event_date_max"] > avail)
    v1.loc[late, "strict_date"] = base.loc[late, "event_date_max"]
    if case_b.any():
        # first-published values: BaoStock where compared; a dependent field (扣非 on 归母, 营业收入 on
        # 营业总收入) keeps the Eastmoney value only when its parent field agreed; anything else is unknown.
        for f in FIELDS:
            if f in check_fields:
                v1.loc[case_b, f] = base.loc[case_b, "bs_" + f]
                continue
            parent = DEPENDS_ON.get(f)
            unknown = case_b & (base["differs"].str.contains(parent) if parent in check_fields else True)
            v1.loc[unknown, f] = np.nan
        v1.loc[case_b, "value_source"] = "baostock"
    rev = case_b | flagged
    v2 = base[rev].copy()
    v2["version"], v2["value_source"] = 2, "eastmoney"
    v2["status"] = np.where(flagged[rev].to_numpy(), "revised_only", "revised")
    v2_date = _rowmax(v2["revision_date"], avail[rev])
    # No dated evidence of the revision after first publication: the revised figures are only known to
    # be public from the day Eastmoney was seen serving them (em["observed_date"]), else never joinable.
    undated = v2_date.isna() | (v2_date <= avail[rev])
    observed = v2["observed_date"] if "observed_date" in v2.columns else pd.Series(pd.NaT, index=v2.index, dtype="datetime64[ns]")
    v2["available_date"] = v2_date.mask(undated, observed)
    v2.loc[undated, "revision_basis"] = "observed"
    v2["strict_date"] = v2["available_date"]
    out = pd.concat([v1[~flagged], v2], ignore_index=True).rename(columns={"notice_date": "notice_date_em", "update_date": "update_date_em"})
    for col in PIT_COLS:
        if col not in out.columns:
            out[col] = np.nan
    return out[PIT_COLS].sort_values(["symbol", "period_end", "version"]).reset_index(drop=True)


# ----------------------------------------------------------------------------- as-of joins
def asof_period(pit: pd.DataFrame, queries: pd.DataFrame, date_col: str = "available_date") -> pd.DataFrame:
    """Value of a GIVEN period as known on each query date.

    queries: symbol, period_end, date. Uses the latest version with ``date_col`` <= date."""
    right = pit.dropna(subset=[date_col]).sort_values([date_col, "version"]).rename(columns={date_col: "_known"})
    left = queries.reset_index().rename(columns={"index": "_row"}).sort_values("date")
    left["date"] = pd.to_datetime(left["date"])
    out = pd.merge_asof(left, right.drop(columns=[c for c in ("available_date", "strict_date") if c in right.columns]),
                        left_on="date", right_on="_known", by=["symbol", "period_end"], direction="backward")
    return out.sort_values("_row").drop(columns="_row").rename(columns={"_known": "known_since"}).reset_index(drop=True)


def latest_snapshots(pit: pd.DataFrame, date_col: str = "available_date") -> pd.DataFrame:
    """Per symbol, the latest report period known after each disclosure, with its latest version.

    A late revision of an OLD period never replaces a newer period."""
    fields = [f for f in FIELDS if f in pit.columns]
    rows = []
    data = pit.dropna(subset=[date_col]).sort_values(["symbol", date_col, "version"])
    for symbol, grp in data.groupby("symbol", sort=False):
        state, newest = {}, None
        for rec in grp.itertuples(index=False):
            state[rec.period_end] = rec
            newest = rec.period_end if newest is None or rec.period_end > newest else newest
            top = state[newest]
            rows.append({"symbol": symbol, "_known": getattr(rec, date_col), "period_end": top.period_end,
                         "version": top.version, "status": top.status, "period_known_since": getattr(top, date_col),
                         **{f: getattr(top, f) for f in fields}})
    snaps = pd.DataFrame(rows)
    if snaps.empty:
        return snaps
    return snaps.drop_duplicates(["symbol", "_known"], keep="last").reset_index(drop=True)


def asof_latest(pit: pd.DataFrame, queries: pd.DataFrame, date_col: str = "available_date") -> pd.DataFrame:
    """Most recent report period known on each query date (symbol, date), with its latest version."""
    snaps = latest_snapshots(pit, date_col).sort_values("_known")
    left = queries.reset_index().rename(columns={"index": "_row"})
    left["date"] = pd.to_datetime(left["date"])
    out = pd.merge_asof(left.sort_values("date"), snaps, left_on="date", right_on="_known", by="symbol", direction="backward")
    return out.sort_values("_row").drop(columns=["_row", "_known"]).reset_index(drop=True)


# ----------------------------------------------------------------------------- quality checks
def coverage_by_period(universe: pd.DataFrame, reports: pd.DataFrame, periods) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Expected = listed on period_end and not delisted before the filing deadline.
    Returns (per-period counts, missing symbol-periods)."""
    have = set(zip(reports["symbol"], reports["period_end"]))
    stats, missing = [], []
    for p in periods:
        p = pd.Timestamp(p)
        dl = filing_deadline(p)
        exp = universe[(universe["ipo_date"] <= p) & (universe["out_date"].isna() | (universe["out_date"] > dl))]
        miss = [s for s in exp["symbol"] if (s, p) not in have]
        stats.append({"period_end": p, "expected": len(exp), "present": len(exp) - len(miss), "missing": len(miss),
                      "coverage_pct": round(100 * (1 - len(miss) / len(exp)), 2) if len(exp) else np.nan})
        missing += [{"symbol": s, "period_end": p} for s in miss]
    return pd.DataFrame(stats), pd.DataFrame(missing, columns=["symbol", "period_end"])


def unit_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Rows with a non-CNY currency, absurd magnitudes or a negative revenue."""
    flags = []
    if "currency" in df.columns:
        flags.append(df[df["currency"].notna() & (df["currency"].astype(str) != "CNY")].assign(flag="non_cny"))
    for f in FIELDS:
        if f in df.columns:
            flags.append(df[df[f].abs() >= 5e12].assign(flag=f"huge_{f}"))
    for f in ("revenue_total", "revenue_op"):
        if f in df.columns:
            flags.append(df[df[f] < 0].assign(flag=f"negative_{f}"))
    flags = [f for f in flags if not f.empty]
    return pd.concat(flags, ignore_index=True) if flags else pd.DataFrame(columns=list(df.columns) + ["flag"])


def scale_mismatch(a, b) -> np.ndarray:
    """True where two sources differ by a factor of ~1e4 or ~1e8 (万元/亿元 unit slips)."""
    a = np.asarray(a, dtype="float64")
    b = np.asarray(b, dtype="float64")
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.abs(a / b)
    out = np.zeros(len(a), dtype=bool)
    for k in (1e4, 1e-4, 1e8, 1e-8):
        out |= np.isfinite(r) & (np.abs(r / k - 1) < 0.02)
    return out
