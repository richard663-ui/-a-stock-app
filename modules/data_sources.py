# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

from modules.qishi import add_indicators
from modules.utils import CACHE_DIR, eastmoney_secid, normalize_code, safe_get, tencent_symbol, to_float


try:
    import baostock as bs
    BAOSTOCK_OK = True
except Exception:
    BAOSTOCK_OK = False


SNAPSHOT_CACHE_FILE = os.path.join(CACHE_DIR, "snapshot_cache_v16_10_1.json")


def _load_snapshot_cache() -> Dict[str, Dict[str, Any]]:
    try:
        if os.path.exists(SNAPSHOT_CACHE_FILE):
            with open(SNAPSHOT_CACHE_FILE, "r", encoding="utf-8") as f:
                obj = json.load(f)
            return obj.get("data", {}) if isinstance(obj, dict) else {}
    except Exception:
        pass
    return {}


def _save_snapshot_cache(data: Dict[str, Dict[str, Any]]):
    if not data:
        return
    try:
        old = _load_snapshot_cache()
        old.update(data)
        with open(SNAPSHOT_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "data": old}, f, ensure_ascii=False)
    except Exception:
        pass


def fetch_em_ulist_snapshot(clean_codes: List[str]) -> Dict[str, Dict[str, Any]]:
    result = {}
    batch_size = 80
    fields = "f12,f14,f2,f3,f4,f5,f6,f8,f10,f9,f23,f20,f21"
    for i in range(0, len(clean_codes), batch_size):
        batch = clean_codes[i:i + batch_size]
        secids = ",".join(eastmoney_secid(c) for c in batch)
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {"fltt": 2, "invt": 2, "fields": fields, "secids": secids}
        r = safe_get(url, params=params, timeout=5, retries=1)
        if not r:
            continue
        try:
            data = r.json().get("data", {}).get("diff", []) or []
            for row in data:
                code = str(row.get("f12", "")).zfill(6)
                price = to_float(row.get("f2"))
                if price is None or price <= 0:
                    continue
                result[code] = {
                    "code": code, "name": row.get("f14"),
                    "price": price, "pct": to_float(row.get("f3")), "change": to_float(row.get("f4")),
                    "volume": to_float(row.get("f5")), "amount": to_float(row.get("f6")),
                    "turnover": to_float(row.get("f8")), "volume_ratio": to_float(row.get("f10")),
                    "pe_dynamic": to_float(row.get("f9")), "pb": to_float(row.get("f23")),
                    "market_cap": to_float(row.get("f20")), "float_cap": to_float(row.get("f21")),
                    "source": "东方财富批量快照",
                }
        except Exception:
            continue
    return result


def fetch_em_single_snapshot(code: str) -> Dict[str, Any]:
    """东方财富单股快照兜底。批量接口抽风时，单股接口经常还能用。"""
    code = normalize_code(code)
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    fields = "f57,f58,f43,f170,f169,f47,f48,f168,f164,f162,f167,f116,f117"
    params = {"secid": eastmoney_secid(code), "fields": fields, "fltt": 2, "invt": 2}
    r = safe_get(url, params=params, timeout=5, retries=1)
    if not r:
        return {}
    try:
        row = r.json().get("data", {}) or {}
        price = to_float(row.get("f43"))
        # stock/get 有时 f43/f170 是放大100倍的整数，有时 fltt=2 已经是小数；这里做兼容
        if price and price > 1000:
            price = price / 100
        pct = to_float(row.get("f170"))
        if pct and abs(pct) > 100:
            pct = pct / 100
        amount = to_float(row.get("f48"))
        snap = {
            "code": code, "name": row.get("f58") or code,
            "price": price, "pct": pct, "change": to_float(row.get("f169")),
            "volume": to_float(row.get("f47")), "amount": amount,
            "turnover": to_float(row.get("f168")), "volume_ratio": to_float(row.get("f164")),
            "pe_dynamic": to_float(row.get("f162")), "pb": to_float(row.get("f167")),
            "market_cap": to_float(row.get("f116")), "float_cap": to_float(row.get("f117")),
            "source": "东方财富单股快照",
        }
        return snap if snap.get("price") else {}
    except Exception:
        return {}


def fetch_tencent_snapshot(codes: List[str]) -> Dict[str, Dict[str, Any]]:
    """腾讯行情兜底：至少拿到名称、现价、涨跌幅。PE/PB等字段可能没有。"""
    result = {}
    if not codes:
        return result
    q = ",".join(tencent_symbol(c) for c in codes)
    r = safe_get("https://qt.gtimg.cn/q=" + q, timeout=5, retries=1)
    if not r:
        return result
    try:
        text = r.content.decode("gbk", errors="ignore")
        for line in text.split(";"):
            if "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) < 6:
                continue
            code = normalize_code(parts[2])
            price = to_float(parts[3])
            prev = to_float(parts[4])
            pct = None
            if price is not None and prev not in [None, 0]:
                pct = (price - prev) / prev * 100
            # 腾讯字段较多且会变，这里只取稳定字段。
            result[code] = {
                "code": code, "name": parts[1], "price": price, "pct": pct,
                "change": (price - prev) if price is not None and prev is not None else None,
                "volume": to_float(parts[6]) if len(parts) > 6 else None,
                "amount": None, "turnover": None, "volume_ratio": None,
                "pe_dynamic": None, "pb": None, "market_cap": None, "float_cap": None,
                "source": "腾讯行情兜底",
            }
    except Exception:
        pass
    return result


@st.cache_data(ttl=60)
def fetch_em_snapshot(codes: Tuple[str, ...]) -> Dict[str, Dict[str, Any]]:
    """多源快照：东方财富批量 -> 东方财富单股 -> 腾讯行情 -> 本地缓存。
    目标：不要因为一个源临时抽风就让页面直接报错。
    """
    clean_codes = list(dict.fromkeys([normalize_code(c) for c in codes if normalize_code(c).isdigit()]))
    if not clean_codes:
        return {}

    result = fetch_em_ulist_snapshot(clean_codes)

    missing = [c for c in clean_codes if c not in result]
    for c in missing:
        snap = fetch_em_single_snapshot(c)
        if snap:
            result[c] = snap

    missing = [c for c in clean_codes if c not in result]
    if missing:
        tencent = fetch_tencent_snapshot(missing)
        result.update({k: v for k, v in tencent.items() if v.get("price")})

    # 最后用历史缓存兜底，但必须标清楚来源，避免误以为是实时。
    missing = [c for c in clean_codes if c not in result]
    if missing:
        cache = _load_snapshot_cache()
        for c in missing:
            if c in cache:
                snap = cache[c]
                snap["source"] = "历史快照缓存兜底"
                result[c] = snap

    _save_snapshot_cache({k: v for k, v in result.items() if v.get("source") != "历史快照缓存兜底"})
    return result


@st.cache_data(ttl=300)
def fetch_tencent_kline(code: str, count: int = 260) -> pd.DataFrame:
    code = normalize_code(code)
    symbol = tencent_symbol(code)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,,,{count},qfq"}
    r = safe_get(url, params=params, timeout=8, retries=2)
    if not r:
        return pd.DataFrame()
    try:
        data = r.json()
        raw = data.get("data", {}).get(symbol, {})
        klines = raw.get("qfqday") or raw.get("day") or []
        rows = []
        for x in klines:
            rows.append({
                "date": x[0],
                "open": float(x[1]),
                "close": float(x[2]),
                "high": float(x[3]),
                "low": float(x[4]),
                "volume": float(x[5]),
            })
        df = pd.DataFrame(rows)
        return add_indicators(df)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=86400)
def load_baostock_industry() -> pd.DataFrame:
    """BaoStock 行业底座。若不可用，返回空表。"""
    if not BAOSTOCK_OK:
        return pd.DataFrame()
    cache_file = os.path.join(CACHE_DIR, "industry_map_baostock.csv")
    if os.path.exists(cache_file):
        try:
            return pd.read_csv(cache_file, dtype={"code": str})
        except Exception:
            pass
    try:
        lg = bs.login()
        rs = bs.query_stock_industry()
        rows = []
        while (rs.error_code == "0") and rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        df = pd.DataFrame(rows, columns=rs.fields)
        if not df.empty:
            df["code"] = df["code"].astype(str).str.extract(r"(\d{6})")[0]
            df.to_csv(cache_file, index=False, encoding="utf-8-sig")
        return df
    except Exception:
        try:
            bs.logout()
        except Exception:
            pass
        return pd.DataFrame()

