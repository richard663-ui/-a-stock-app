# -*- coding: utf-8 -*-
from typing import Any, Dict

import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["pct_change"] = df["close"].pct_change() * 100
    for n in [5, 10, 20, 60, 120]:
        df[f"MA{n}"] = df["close"].rolling(n).mean()
    df["VOL5"] = df["volume"].rolling(5).mean()
    df["VOL20"] = df["volume"].rolling(20).mean()
    df["RET3"] = df["close"].pct_change(3) * 100
    df["RET5"] = df["close"].pct_change(5) * 100
    df["RET10"] = df["close"].pct_change(10) * 100
    df["RET20"] = df["close"].pct_change(20) * 100
    df["RET60"] = df["close"].pct_change(60) * 100
    df["HIGH20_PREV"] = df["high"].rolling(20).max().shift(1)
    df["HIGH60_PREV"] = df["high"].rolling(60).max().shift(1)
    df["LOW20"] = df["low"].rolling(20).min()
    df["LOW60"] = df["low"].rolling(60).min()
    df["DIST_MA20"] = (df["close"] - df["MA20"]) / df["MA20"] * 100
    df["MA20_SLOPE"] = df["MA20"] - df["MA20"].shift(5)
    return df


def qishi_color(score: float, risk_state: str = "") -> str:
    if score >= 90:
        return "darkred"
    if score >= 75:
        return "red"
    if score >= 60:
        return "lightcoral"
    if score >= 40:
        return "gold"
    return "lightgray"


def fund_color(score: float, label: str) -> str:
    if "放量下跌" in label:
        return "black"
    if "冲高回落" in label or "分歧" in label:
        return "purple"
    if score >= 75:
        return "darkred"
    if score >= 58:
        return "red"
    if score >= 40:
        return "gold"
    return "lightgray"


def analyze_qishi(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or len(df) < 80:
        return {"ok": False, "reason": "K线不足"}

    df = df.copy().reset_index(drop=True)
    rows = []
    prev_strength = 0

    for i in range(len(df)):
        row = df.iloc[i]
        if i < 60 or pd.isna(row["MA20"]):
            rows.append({"score": 0, "fund_score": 0, "fund_label": "无", "state": "未启动"})
            continue

        close, high, low, open_ = row["close"], row["high"], row["low"], row["open"]
        vol, vol20, vol5 = row["volume"], row["VOL20"], row["VOL5"]
        pct = row["pct_change"] if pd.notna(row["pct_change"]) else 0
        vol_ratio = vol / vol20 if vol20 and vol20 > 0 else 1
        vol5_ratio = vol5 / vol20 if vol20 and vol20 > 0 else 1
        close_pos = (close - low) / (high - low) if high > low else 0.5

        # 趋势结构 30
        trend = 0
        if close > row["MA5"]: trend += 5
        if close > row["MA10"]: trend += 5
        if close > row["MA20"]: trend += 6
        if row["MA5"] > row["MA10"]: trend += 5
        if row["MA10"] > row["MA20"]: trend += 5
        if row["MA20_SLOPE"] > 0: trend += 4

        # 量价资金 30
        fund = 0
        fund_label = "无明显资金动作"
        if vol_ratio > 1.2: fund += 6
        if vol_ratio > 1.8: fund += 7
        if vol5_ratio > 1.1: fund += 5
        if pct > 1 and vol_ratio > 1.2 and close_pos > 0.55:
            fund += 8
            fund_label = "放量上涨"
        if pct < -1.5 and vol_ratio > 1.3:
            fund -= 12
            fund_label = "放量下跌风险"
        if pct > 1.5 and close_pos < 0.45 and vol_ratio > 1.3:
            fund -= 6
            fund_label = "冲高回落分歧"
        if abs(pct) < 1 and vol_ratio < 0.8 and close > row["MA10"]:
            fund += 4
            fund_label = "缩量强势整理"
        fund_score = max(0, min(100, fund * 3.3))

        # 突破 20
        breakout = 0
        if close > row["HIGH20_PREV"]: breakout += 8
        if close > row["HIGH60_PREV"]: breakout += 8
        if close > row["MA20"] and row["MA20_SLOPE"] > 0: breakout += 4

        # 动量 15
        momentum = 0
        if row["RET3"] > 2: momentum += 3
        if row["RET5"] > 4: momentum += 4
        if row["RET10"] > 6: momentum += 4
        if row["RET20"] > 8: momentum += 4

        raw = trend + fund + breakout + momentum
        raw = max(0, min(95, raw))

        # 趋势延续：只要没跌破 MA10 / MA20，上一阶段的红柱会缓慢衰减，不会一天消失
        intact = close > row["MA10"] or (close > row["MA20"] and row["MA20_SLOPE"] > 0)
        if intact:
            score = max(raw, prev_strength * 0.88)
        else:
            score = raw * 0.7
        prev_strength = score

        if score >= 90:
            state = "深红加速"
        elif score >= 75:
            state = "红柱延续"
        elif score >= 60:
            state = "浅红起势"
        elif score >= 40:
            state = "黄柱异动"
        else:
            state = "未启动"

        rows.append({"score": score, "fund_score": fund_score, "fund_label": fund_label, "state": state})

    qdf = pd.DataFrame(rows)
    out = pd.concat([df, qdf], axis=1)

    latest = out.iloc[-1]
    recent = out.tail(20)
    red_days = int((recent["score"] >= 60).sum())
    strong_red_days = int((recent["score"] >= 75).sum())
    consecutive_red = 0
    for s in reversed(list(out["score"].tail(30))):
        if s >= 60:
            consecutive_red += 1
        else:
            break

    # 追高风险单独算，不压制红柱
    dist_ma20 = latest["DIST_MA20"] if pd.notna(latest["DIST_MA20"]) else 0
    ret20 = latest["RET20"] if pd.notna(latest["RET20"]) else 0
    ret60 = latest["RET60"] if pd.notna(latest["RET60"]) else 0
    risk_points = 0
    risk_reasons = []
    if dist_ma20 > 12:
        risk_points += 1; risk_reasons.append("距离MA20偏远")
    if dist_ma20 > 22:
        risk_points += 1; risk_reasons.append("严重远离MA20")
    if ret20 > 35:
        risk_points += 1; risk_reasons.append("20日涨幅较大")
    if ret60 > 80:
        risk_points += 1; risk_reasons.append("60日涨幅过大")
    if latest["fund_label"] in ["冲高回落分歧", "放量下跌风险"]:
        risk_points += 1; risk_reasons.append(latest["fund_label"])

    if risk_points >= 3:
        risk_state = "高位风险"
    elif risk_points >= 1:
        risk_state = "中等风险"
    else:
        risk_state = "风险可控"

    reasons = []
    if latest["close"] > latest["MA5"] > 0: reasons.append("股价站上MA5")
    if latest["close"] > latest["MA10"] > 0: reasons.append("股价站上MA10")
    if latest["close"] > latest["MA20"] > 0: reasons.append("股价站上MA20")
    if latest["MA5"] > latest["MA10"] > latest["MA20"]: reasons.append("短中期均线多头排列")
    if latest["close"] > latest["HIGH20_PREV"]: reasons.append("突破20日平台")
    if latest["close"] > latest["HIGH60_PREV"]: reasons.append("突破60日平台")
    if latest["fund_score"] >= 58: reasons.append(f"资金/量能：{latest['fund_label']}")
    if consecutive_red >= 3: reasons.append(f"起势柱连续{consecutive_red}天")
    if not reasons: reasons.append("趋势与量能尚未形成明显共振")

    return {
        "ok": True,
        "df": out,
        "latest_score": float(latest["score"]),
        "latest_state": str(latest["state"]),
        "fund_score": float(latest["fund_score"]),
        "fund_label": str(latest["fund_label"]),
        "red_days_20": red_days,
        "strong_red_days_20": strong_red_days,
        "consecutive_red": consecutive_red,
        "risk_state": risk_state,
        "risk_reasons": risk_reasons,
        "reasons": reasons,
    }

