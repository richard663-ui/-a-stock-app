# -*- coding: utf-8 -*-
"""A股盯盘 V17 实验版：Streamlit内置QMT实时采集与自动切股。"""
from __future__ import annotations

from datetime import datetime
from typing import Dict

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
from modules.qmt_live import QMTLiveManager
from modules.short_direction import analyze_short_direction
from modules.signals import make_action
from modules.ui_blocks import plot_qishi
from modules.utils import fmt_num, fmt_pct, normalize_code

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

st.set_page_config(page_title="A股盯盘 V17", layout="wide")
st.markdown(
    """
    <style>
    .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
    .v17-card {border:1px solid #e5e7eb; border-radius:12px; padding:14px 16px; min-height:110px; background:#ffffff;}
    .v17-label {font-size:0.92rem; color:#6b7280; margin-bottom:8px;}
    .v17-value {font-size:1.55rem; font-weight:700; line-height:1.25; white-space:normal; word-break:break-word;}
    .v17-sub {font-size:0.85rem; color:#6b7280; margin-top:8px; line-height:1.35;}
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("A股盯盘 V17｜QMT实时实验版")
st.caption("在左侧输入股票代码，QMT自动切换｜保留四大板块与完整起势图｜只读行情，不自动交易")


@st.cache_resource
def get_qmt_manager() -> QMTLiveManager:
    return QMTLiveManager(interval=1.0, max_rows=1800, runtime_dir="runtime")


def _qmt_symbol(code: str) -> str:
    code = normalize_code(code)
    if code.startswith(("6", "68", "5", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _macd_status(df: pd.DataFrame) -> Dict[str, str]:
    if df is None or len(df) < 35:
        return {"label": "数据不足", "detail": "MACD暂不可用"}
    close = pd.to_numeric(df["close"], errors="coerce")
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    gap = dif - dea
    if len(gap.dropna()) < 3:
        return {"label": "数据不足", "detail": "MACD暂不可用"}
    now, prev, prev2 = float(gap.iloc[-1]), float(gap.iloc[-2]), float(gap.iloc[-3])
    if prev <= 0 < now:
        label = "刚形成金叉"
    elif prev >= 0 > now:
        label = "刚形成死叉"
    elif now > 0 and now < prev < prev2:
        label = "多头但正在走弱"
    elif now < 0 and now > prev > prev2:
        label = "空头但正在修复"
    elif now > 0:
        label = "金叉区间"
    else:
        label = "死叉区间"
    return {"label": label, "detail": f"DIF与DEA差值 {now:+.3f}"}


def _trend_structure(qishi: Dict) -> str:
    if not qishi.get("ok"):
        return "未知"
    d = qishi.get("df", pd.DataFrame())
    if d.empty:
        return "未知"
    last = d.iloc[-1]
    close = float(last.get("close", 0) or 0)
    ma5 = float(last.get("MA5", 0) or 0)
    ma10 = float(last.get("MA10", 0) or 0)
    ma20 = float(last.get("MA20", 0) or 0)
    if close > ma5 > ma10 > ma20:
        return "明显上升"
    if close < ma5 < ma10 < ma20:
        return "明显下降"
    if close > ma20 and ma5 >= ma10:
        return "偏强震荡"
    if close < ma20 and ma5 <= ma10:
        return "偏弱震荡"
    return "震荡整理"


def _card(col, label: str, value: str, sub: str = ""):
    with col:
        st.markdown(
            f'<div class="v17-card"><div class="v17-label">{label}</div>'
            f'<div class="v17-value">{value}</div>'
            f'<div class="v17-sub">{sub}</div></div>',
            unsafe_allow_html=True,
        )


with st.sidebar:
    st.header("输入")
    code_input = st.text_input("股票代码", "000400", max_chars=6, help="输入6位代码并按回车，QMT会自动切换")
    has_position = st.checkbox("已有仓位", False)
    cost = st.number_input("成本价", min_value=0.0, value=0.0, step=0.01)
    position_pct = st.number_input("持仓比例%", 0.0, 100.0, 0.0, 5.0)
    auto_event = st.checkbox("自动抓新闻（会变慢）", False)
    refresh_seconds = st.selectbox("页面自动刷新", [2, 5, 10], index=1, format_func=lambda x: f"每{x}秒")
    st.caption("国盛QMT保持登录即可，不再需要单独运行采集器。")

if st_autorefresh is not None:
    st_autorefresh(interval=refresh_seconds * 1000, key="qmt_v17_refresh")
else:
    st.sidebar.warning("缺少自动刷新组件：双击 start_v17.bat 会自动提示安装。")

code = normalize_code(code_input)
if len(code) != 6 or not code.isdigit():
    st.error("请输入6位A股股票代码，例如 301666。")
    st.stop()

expected_symbol = _qmt_symbol(code)
qmt_manager = get_qmt_manager()
qmt_manager.set_symbol(expected_symbol)
qmt_ticks = qmt_manager.get_frame()
qmt_status = qmt_manager.get_status()

with st.sidebar:
    if qmt_status.get("ok"):
        st.success(f"QMT已连接：{qmt_status.get('symbol')}｜{qmt_status.get('samples')}条")
    elif qmt_status.get("error"):
        st.error(f"{qmt_status.get('status')}：{qmt_status.get('error')}")
    else:
        st.info(qmt_status.get("status", "正在连接QMT"))

short = analyze_short_direction(qmt_ticks)
snapshots = fetch_em_snapshot((code,))
snapshot = snapshots.get(code, {})
df = fetch_tencent_kline(code, 260)
if df.empty:
    st.error("日K线获取失败，无法计算趋势与起势图。")
    st.stop()

if not qmt_ticks.empty:
    qrow = qmt_ticks.iloc[-1]
    qprice = pd.to_numeric(pd.Series([qrow.get("lastPrice")]), errors="coerce").iloc[0]
    last_close = pd.to_numeric(pd.Series([qrow.get("lastClose")]), errors="coerce").iloc[0]
    if pd.notna(qprice) and qprice > 0:
        snapshot["price"] = float(qprice)
        if pd.notna(last_close) and last_close > 0:
            snapshot["pct"] = (float(qprice) / float(last_close) - 1.0) * 100.0
        snapshot["source"] = "国盛QMT实时"
        snapshot["open"] = qrow.get("open")
        snapshot["high"] = qrow.get("high")
        snapshot["low"] = qrow.get("low")

if not snapshot:
    latest = df.iloc[-1]
    fallback_name = FALLBACK_INDUSTRY.get(code, (code, []))[0]
    snapshot = {
        "code": code,
        "name": fallback_name,
        "price": float(latest["close"]),
        "pct": float(latest.get("pct_change", 0)),
        "amount": None,
        "source": "日K线兜底（非实时）",
    }

qishi = analyze_qishi(df)
industry, concepts, industry_src = detect_industry_concepts(code, snapshot)
auto_radar = auto_event_radar(code, snapshot, industry, concepts) if auto_event else {
    "score": 0, "label": "未启用", "news": [], "reasons": []
}
news_text = " ".join(n.get("title", "") for n in auto_radar.get("news", []))
catalyst = analyze_event_catalyst(code, news_text, concepts)
event_certainty = event_certainty_grade(news_text, auto_radar.get("news", []), catalyst)

manual_l2 = score_manual_l2("中性", "无明显", "无明显", "一般", "中性")
l2 = combine_l2_scores(
    manual_l2,
    {"has_detail": False, "score": 50, "label": "逐笔大单待接入", "reasons": [], "summary": {}, "df": pd.DataFrame()},
)
action = make_action(snapshot, qishi, catalyst, l2, has_position, cost, position_pct)
macd = _macd_status(df)
trend_text = _trend_structure(qishi)
m = short.get("metrics", {})
strength_text = f"{short.get('signal_strength', 0)}/100" if short.get("direction_60") != "NEUTRAL" else "未触发"

st.markdown(f"## ① 今日结论｜{snapshot.get('name', code)}（{code}）")
st.write(f"当前价 **{fmt_num(snapshot.get('price'))}**　今日涨跌 **{fmt_pct(snapshot.get('pct'))}**　数据源 **{snapshot.get('source', '未知')}**")
cols = st.columns(4)
_card(cols[0], "今日动作", f"{action['buy_grade']}｜{action['buy_label']}", f"建议新仓 {action['new_pos']}")
_card(cols[1], "未来1分钟倾向", short.get("label_60", "暂无明确方向"), short.get("alert", ""))
_card(cols[2], "未来2分钟倾向", short.get("label_120", "暂无明确方向"), "只在多条件一致时触发")
_card(cols[3], "短线信号", strength_text, "不是已验证胜率")

if short.get("level") == "橙色":
    st.warning(short.get("alert"))
elif short.get("direction_60") == "UP":
    st.success(short.get("alert"))
else:
    st.info(short.get("alert"))
st.write(f"**持仓建议：** {action['holding_advice']}")
if short.get("reasons"):
    st.write("**短线依据：** " + "；".join(short["reasons"][:3]))

st.markdown("## ② 大资金与盘口确认")
buy_pressure = float(m.get("buy_pressure_pct", 50.0) or 50.0)
if buy_pressure >= 58:
    book_text = "买盘承接较强"
elif buy_pressure <= 42:
    book_text = "卖盘压力较强"
else:
    book_text = "买卖力量接近"
change30 = float(m.get("change_30s_pct", 0.0) or 0.0)
change60 = float(m.get("change_60s_pct", 0.0) or 0.0)
vwap_pos = float(m.get("above_vwap_pct", 0.0) or 0.0)
cols = st.columns(4)
_card(cols[0], "盘口结论", book_text, f"五档买盘占 {buy_pressure:.0f}%")
_card(cols[1], "近30秒价格", f"{change30:+.2f}%", "直接显示涨跌")
_card(cols[2], "近60秒价格", f"{change60:+.2f}%", "观察是否持续同向")
_card(cols[3], "近60秒成交均价", "上方" if vwap_pos > 0 else ("下方" if vwap_pos < 0 else "附近"), f"偏离 {vwap_pos:+.2f}%")

if qmt_ticks.empty:
    st.warning(f"正在等待 {expected_symbol} 的QMT实时数据，短线方向暂不判断。")
else:
    latest_time = str(qmt_ticks.iloc[-1].get("captured_at", ""))
    st.caption(f"QMT最新采集：{latest_time}｜当前股票：{expected_symbol}｜样本数：{len(qmt_ticks)}")
st.info("目前接入实时价格和五档盘口。真正的千手/万手主动买卖要在逐笔成交与逐笔委托接口验证后显示，现阶段不伪造‘主力净流入’。")

with st.expander("查看五档买卖盘"):
    if not qmt_ticks.empty:
        r = qmt_ticks.iloc[-1]
        bids = r.get("bidPrice", [])
        asks = r.get("askPrice", [])
        bidv = r.get("bidVol", [])
        askv = r.get("askVol", [])
        rows = []
        for i in range(min(5, len(bids), len(asks), len(bidv), len(askv))):
            rows.append({"档位": i + 1, "买价": bids[i], "买量": bidv[i], "卖价": asks[i], "卖量": askv[i]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.markdown("## ③ 趋势与指标")
cols = st.columns(4)
_card(cols[0], "AI起势", action["q_state"], f"起势分 {qishi.get('latest_score', 0):.0f}/100")
_card(cols[1], "趋势结构", trend_text, "根据日K与均线结构")
_card(cols[2], "MACD状态", macd["label"], macd["detail"])
_card(cols[3], "风险状态", action["risk_state"], f"资金量能 {qishi.get('fund_score', 0):.0f}/100")
if qishi.get("reasons"):
    st.write("**趋势依据：** " + "；".join(qishi["reasons"][:4]))
if qishi.get("ok"):
    plot_qishi(qishi, title=f"{snapshot.get('name', code)} {code}")

st.markdown("## ④ 事件催化与基本面")
cols = st.columns(4)
_card(cols[0], "行业", industry, f"来源：{industry_src}")
_card(cols[1], "事件催化", catalyst.get("label", "无"), f"催化分 {catalyst.get('score', 0)}/100")
_card(cols[2], "事件确定性", event_certainty.get("certainty", "低"), event_certainty.get("label", ""))
_card(cols[3], "估值概览", f"PE {fmt_num(snapshot.get('pe_dynamic'))}", f"PB {fmt_num(snapshot.get('pb'))}")
background_reasons = (catalyst.get("reasons", []) + auto_radar.get("reasons", []))[:3]
st.write("**背景提示：** " + ("；".join(background_reasons) if background_reasons else "暂未发现明确硬催化。"))

with st.expander("买卖条件与风控位"):
    st.write("**买入/加仓条件**")
    for x in action.get("buy_zone", []):
        st.write("- " + x)
    st.write("**卖出/风控条件**")
    for x in action.get("sell_zone", []):
        st.write("- " + x)
    st.caption("更新时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
