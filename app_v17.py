# -*- coding: utf-8 -*-
"""A股盯盘 V17 实验版：四板块精简展示 + QMT短线方向预警。"""
from __future__ import annotations

import ast
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from modules.big_orders import combine_l2_scores, score_manual_l2
from modules.data_sources import fetch_em_snapshot, fetch_tencent_kline
from modules.event_radar import (
    FALLBACK_INDUSTRY,
    analyze_event_catalyst,
    auto_event_radar,
    detect_industry_concepts,
    event_certainty_grade,
)
from modules.qishi import analyze_qishi
from modules.short_direction import analyze_short_direction
from modules.signals import make_action
from modules.ui_blocks import plot_qishi
from modules.utils import fmt_num, fmt_pct, normalize_code

st.set_page_config(page_title="A股盯盘 V17", layout="wide")
st.title("A股盯盘 V17｜QMT实验版")
st.caption("四板块精简展示｜60/120秒高置信预警｜只读行情，不自动交易")


def _parse_list(v):
    if isinstance(v, list):
        return v
    if not isinstance(v, str) or not v:
        return []
    try:
        x = ast.literal_eval(v)
        return x if isinstance(x, list) else []
    except Exception:
        return []


def load_qmt_ticks(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        for c in ["bidPrice", "askPrice", "bidVol", "askVol"]:
            if c in df.columns:
                df[c] = df[c].apply(_parse_list)
        return df.tail(300)
    except Exception:
        return pd.DataFrame()


with st.sidebar:
    code_input = st.text_input("股票代码", "000400")
    has_position = st.checkbox("已有仓位", False)
    cost = st.number_input("成本价", min_value=0.0, value=0.0, step=0.01)
    position_pct = st.number_input("持仓比例%", 0.0, 100.0, 0.0, 5.0)
    qmt_file = st.text_input("QMT采集文件", "runtime/qmt_ticks.csv")
    auto_event = st.checkbox("自动抓新闻（会变慢）", False)
    show_chart = st.checkbox("显示完整起势图", False)
    st.caption("先启动 services/qmt_experiment_collector.py，再打开本页。")

code = normalize_code(code_input)
qmt_ticks = load_qmt_ticks(qmt_file)
short = analyze_short_direction(qmt_ticks)

snapshots = fetch_em_snapshot((code,))
snapshot = snapshots.get(code, {})
df = fetch_tencent_kline(code, 260)
if df.empty:
    st.error("日K线获取失败。")
    st.stop()

if not qmt_ticks.empty:
    qrow = qmt_ticks.iloc[-1]
    qprice = pd.to_numeric(pd.Series([qrow.get("lastPrice")]), errors="coerce").iloc[0]
    if pd.notna(qprice) and qprice > 0:
        snapshot["price"] = float(qprice)
        snapshot["source"] = "国盛QMT本地实时采集"
        if snapshot.get("name") is None:
            snapshot["name"] = code

if not snapshot:
    latest = df.iloc[-1]
    snapshot = {
        "code": code,
        "name": FALLBACK_INDUSTRY.get(code, (code, []))[0],
        "price": float(latest["close"]),
        "pct": float(latest.get("pct_change", 0)),
        "amount": None,
        "source": "K线兜底",
    }

qishi = analyze_qishi(df)
industry, concepts, industry_src = detect_industry_concepts(code, snapshot)
auto_radar = auto_event_radar(code, snapshot, industry, concepts) if auto_event else {
    "score": 0, "label": "未启用", "news": [], "reasons": []
}
news_text = " ".join(n.get("title", "") for n in auto_radar.get("news", []))
catalyst = analyze_event_catalyst(code, news_text, concepts)
event_certainty = event_certainty_grade(news_text, auto_radar.get("news", []), catalyst)

# V17实验期：QMT盘口还未完成逐笔分类，先把方向预警映射为保守L2评分。
manual_l2 = score_manual_l2("中性", "无明显", "无明显", "一般", "中性")
if short.get("direction_60") == "UP" and short.get("confidence", 0) >= 80:
    manual_l2 = score_manual_l2("大单流入", "千手买入强", "无明显", "承接强", "主动买多")
elif short.get("direction_60") == "DOWN" and short.get("confidence", 0) >= 80:
    manual_l2 = score_manual_l2("大单流出", "千手卖出强", "无明显", "承接弱", "主动卖多")
l2 = combine_l2_scores(manual_l2, {"has_detail": False, "score": 50, "label": "QMT实验采集", "reasons": [], "summary": {}, "df": pd.DataFrame()})
action = make_action(snapshot, qishi, catalyst, l2, has_position, cost, position_pct)

# 1. 结论 + 短线预警
st.markdown("## ① 今日动作与短线预警")
c1, c2, c3, c4 = st.columns(4)
c1.metric("当前价", fmt_num(snapshot.get("price")))
c2.metric("买点", f"{action['buy_grade']}｜{action['buy_label']}")
c3.metric("未来60秒", short.get("label_60", "中性"))
c4.metric("预警强度", f"{short.get('confidence', 0)}%")

if short.get("level") == "红色":
    st.error(short["alert"])
elif short.get("level") == "橙色":
    st.warning(short["alert"])
elif short.get("direction_60") == "UP":
    st.success(short["alert"])
else:
    st.info(short["alert"])

st.write(f"**持仓建议：** {action['holding_advice']}")
if short.get("reasons"):
    st.caption("｜".join(short["reasons"][:3]))

# 2. 资金与盘口
st.markdown("## ② 大资金与盘口")
m = short.get("metrics", {})
c1, c2, c3, c4 = st.columns(4)
c1.metric("盘口方向", short.get("label_60", "中性"))
c2.metric("五档不平衡", f"{m.get('obi', 0):.2f}")
c3.metric("30秒动量", f"{m.get('r30_bps', 0):.1f} bp")
c4.metric("数据源", snapshot.get("source", "未知"))
if qmt_ticks.empty:
    st.warning("未读到QMT采集文件，当前短线预测暂停。")
else:
    ts = str(qmt_ticks.iloc[-1].get("captured_at", ""))
    st.caption(f"QMT最新采集：{ts}｜样本数：{len(qmt_ticks)}")

# 3. 趋势指标
st.markdown("## ③ 趋势与指标")
last = qishi.get("df", pd.DataFrame()).iloc[-1] if qishi.get("ok") else pd.Series(dtype=float)
close = float(last.get("close", 0) or 0)
ma5 = float(last.get("MA5", 0) or 0)
ma10 = float(last.get("MA10", 0) or 0)
ma20 = float(last.get("MA20", 0) or 0)
trend_text = "多头" if close > ma5 > ma10 > ma20 else ("空头" if close < ma5 < ma10 < ma20 else "震荡")
c1, c2, c3, c4 = st.columns(4)
c1.metric("AI起势", action["q_state"])
c2.metric("趋势结构", trend_text)
c3.metric("资金量能", f"{qishi.get('fund_score', 0):.0f}/100")
c4.metric("风险", action["risk_state"])
st.caption("MACD等滞后指标只作为趋势确认；60/120秒预警主要由实时盘口、微观价格与短周期动量触发。")
if show_chart and qishi.get("ok"):
    plot_qishi(qishi, title=f"{snapshot.get('name', code)} {code}")

# 4. 事件催化
st.markdown("## ④ 事件与基本面背景")
c1, c2, c3, c4 = st.columns(4)
c1.metric("行业", industry)
c2.metric("催化", catalyst.get("label", "无"))
c3.metric("确定性", event_certainty.get("certainty", "低"))
c4.metric("影响", event_certainty.get("impact_type", "线索"))
reasons = (catalyst.get("reasons", []) + auto_radar.get("reasons", []))[:3]
st.caption("｜".join(reasons) if reasons else "暂未发现明确硬催化。")

with st.expander("技术明细与风控位"):
    st.write("买入条件：", action.get("buy_zone", []))
    st.write("卖出/风控：", action.get("sell_zone", []))
    st.write("升级条件：", action.get("upgrade", []))
    st.write("降级条件：", action.get("downgrade", []))
    st.write("更新时间：", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
