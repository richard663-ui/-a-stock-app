# -*- coding: utf-8 -*-
import os
import re
import time
from typing import Any, Dict

import pandas as pd
import requests


CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)


def normalize_code(code: str) -> str:
    code = str(code).strip().upper().replace("SH", "").replace("SZ", "").replace(".", "")
    m = re.search(r"(\d{6})", code)
    return m.group(1) if m else code.zfill(6)


def infer_market(code: str) -> str:
    code = normalize_code(code)
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz"
    if code.startswith(("8", "4", "9")):
        return "bj"
    return "sz"


def eastmoney_secid(code: str) -> str:
    code = normalize_code(code)
    market = infer_market(code)
    # 东方财富：1=沪市，0=深市/北交所常用
    return f"1.{code}" if market == "sh" else f"0.{code}"


def tencent_symbol(code: str) -> str:
    return infer_market(code) + normalize_code(code)


def safe_get(url: str, params: Dict[str, Any] = None, timeout: int = 8, retries: int = 2):
    headers = {"User-Agent": "Mozilla/5.0"}
    for _ in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r
        except Exception:
            time.sleep(0.3)
    return None


def to_float(x):
    try:
        if x is None or pd.isna(x):
            return None
        if isinstance(x, str):
            x = x.strip().replace("%", "").replace(",", "").replace("--", "")
            if x == "":
                return None
        return float(x)
    except Exception:
        return None


def fmt_pct(x):
    if x is None or pd.isna(x):
        return "--"
    return f"{x:.2f}%"


def fmt_num(x):
    if x is None or pd.isna(x):
        return "--"
    try:
        x = float(x)
        if abs(x) >= 1e8:
            return f"{x/1e8:.2f}亿"
        if abs(x) >= 1e4:
            return f"{x/1e4:.2f}万"
        return f"{x:.2f}"
    except Exception:
        return "--"

