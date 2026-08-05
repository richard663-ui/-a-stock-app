# -*- coding: utf-8 -*-
"""A股盯盘 V17：密码保护、QMT本地/云端自动切股、完整数据后再展示。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Tuple

import pandas as pd
import streamlit as st

from modules.auth import logout_button, require_password
from modules.big_orders import combine_l2_scores, score_manual_l2
from modules.cloud_bridge import CloudBridge, BridgeConfig, load_bridge_config
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
require_password()

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1500px;}
    .v17-card {border:1px solid #e5e7eb; border-radius:12px; padding:14px 16px; min-height:122px; background:#ffffff;}
    .v17-label {font-size:0.92rem; color:#6b7280; margin-bottom:8px;}
    .v17-value {font-size:1.48rem; font-weight:700; line-height:1.25; white-space:normal; overflow:visible; word-break:break-word;}
    .v17-sub {font-size:0.86rem; color:#6b7280; margin-top:8px; line-height:1.4; white-space:normal;}
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("A股盯盘 V17｜QMT实时版")
st.caption("搜索股票后自动切换数据｜先加载完整分笔再展示｜保留四大板块与完整起势图｜只读行情")


@st.cache_resource
def get_qmt_manager() -> QMTLiveManager:
    return QMTLiveManager(interval=1.0, max_rows=1800, runtime_dir="runtime")


@st.cache_resource
def get_cloud_bridge(config: BridgeConfig) -> CloudBridge:
    return CloudBridge(config)


def _qmt_symbol(code: str) -> str:
    code = normalize_code(code)
    if code.startswith(("6", "68", "5", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _cloud_config() -> BridgeConfig:
    try:
        return load_bridge_config(st.secrets)
    except Exception:
        return load_bridge_config()


def _load_realtime(symbol: str) -> Tuple[pd.DataFrame, Dict, str]:
    """Use Supabase on Streamlit Cloud; otherwise use local QMT directly."""
    config = _cloud_config()
    if config.ok:
        try:
            bridge = get_cloud_bridge(config)
            bridge.request_symbol(symbol)
            payload = bridge.fetch_ticks(symbol)
            ticks = payload.get("ticks", []) if isinstance(payload, dict) else []
            frame = pd.DataFrame(ticks if isinstance(ticks, list) else [])
            status = {
                "ok": not frame.empty,
                "symbol": symbol,
                "status": payload.get("status", "等待本地桥梁") if isinstance(payload, dict) else "等待本地桥梁",
                "samples": len(frame),
                "captured_at": frame.iloc[-1].get("captured_at") if not frame.empty else None,
                "updated_at": payload.get("updated_at") if isinstance(payload, dict) else None,
                "error": "",
            }
            return frame.tail(900), status, "云端QMT桥梁"
        except Exception as exc:
            return pd.DataFrame(), {
                "ok": False,
                "symbol": symbol,
                "status": "云端桥梁连接失败",
                "samples": 0,
                "error": str(exc),
            }, "云端QMT桥梁"

    manager = get_qmt_manager()
    manager.set_symbol(symbol)
    return manager.get_frame().tail(900), manager.get_status(), "本机QMT直连"


def _macd_status(df: pd.DataFrame) -> Dict[str, str]:
    if df is None or len(df) < 35:
        return {"label": "数据不足", "detail": "MACD暂不可用"}
    close = pd.to_numeric(df["close"], errors="coerce")
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    gap = dif - dea
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
    if not qishi.get("ok") or qishi.get("df", pd.DataFrame()).empty:
        return "未知"
    last = qishi["df"].iloc[-1]
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
    st.header("股票搜索")
    code_input = st.text_input("6位股票代码", "000400", max_chars=6, help="输入代码并按回车，系统自动切换")
    has_position = st.checkbox("已有仓位", False)
    cost = st.number_input("成本价", min_value=0.0, value=0.0, step=0.01)
    position_pct = st.number_input("持仓比例%", 0.0, 100.0, 0.0, 5.0)
    auto_event = st.checkbox("自动抓新闻（会变慢）", False)
    refresh_seconds = st.selectbox("自动刷新", [1, 2, 5, 10], index=1, format_func=lambda x: f"每{x}秒")
    logout_button()

if st_autorefresh is not None:
    st_autorefresh(interval=refresh_seconds * 1000, key="qmt_v17_refresh")

code = normalize_code(code_input)
if len(code) != 6 or not code.isdigit():
    st.error("请输入6位A股股票代码，例如 301666。")
    st.stop()

expected_symbol = _qmt_symbol(code)
qmt_ticks, qmt_status, realtime_mode = _load_realtime(expected_symbol)

with st.sidebar:
    if qmt_status.get("ok"):
        st.success(f"{realtime_mode}：{expected_symbol}｜{len(qmt_ticks)}条")
    elif qmt_status.get("error"):
        st.error(f"{qmt_status.get('status')}：{qmt_status.get('error')}")
    else:
        st.info(qmt_status.get("status", "正在加载实时数据"))

# 用户要求不显示半成品：分笔未加载完整时只显示加载页。
MIN_TICKS = 45
if len(qmt_ticks) < MIN_TICKS:
    loaded = min(len(qmt_ticks), MIN_TICKS)
    st.subheader(f"正在加载 {expected_symbol} 的完整实时数据")
    st.progress(loaded / MIN_TICKS, text=f"已加载 {loaded}/{MIN_TICKS} 条分笔")
    st.info("本地桥梁会优先回填当日分笔；完成后页面会自动显示四大板块，无需重新输入代码。")
    if realtime_mode == "云端QMT桥梁":
        st.caption("请确保ROG已开机、国盛QMT已登录，并且本地云桥任务正在运行。")
    st.stop()

short = analyze_short_direction(qmt_ticks)
snapshots = fetch_em_snapshot((code,))
snapshot = snapshots.get(code, {})
df = fetch_tencent_kline(code, 260)
if df.empty:
    st.error("日K线获取失败，无法计算趋势与起势图。")
    st.stop()

qrow = qmt_ticks.iloc[-1]
qprice = pd.to_numeric(pd.Series([qrow.get("lastPrice")]), errors="coerce").iloc[0]
last_close = pd.to_numeric(pd.Series([qrow.get("lastClose")]), errors="coerce").iloc[0]
if pd.notna(qprice) and qprice > 0:
    snapshot["price"] = float(qprice)
    if pd.notna(last_close) and last_close > 0:
        snapshot["pct"] = (float(qprice) / float(last_close) - 1.0) * 100.0
    snapshot["source"] = realtime_mode
    snapshot["open"] = qrow.get("open")
    snapshot["high"] = qrow.get("high")
    snapshot["low"] = qrow.get("low")

if not snapshot:
    latest = df.iloc[-1]
    snapshot = {
        "code": code,
        "name": FALLBACK_INDUSTRY.get(code, (code, []))[0],
        "price": float(latest["close"]),
        "pct": float(latest.get("pct_change", 0)),
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
st.write(f"当前价 **{fmt_num(snapshot.get('price'))}**　今日涨跌 **{fmt_pct(snapshot.get('pct'))}**　数据源 **{snapshot.get('source')}**")
cols = st.columns(4)
_card(cols[0], "今日动作", f"{action['buy_grade']}｜{action['buy_label']}", f"建议新仓 {action['new_pos']}")
_card(cols[1], "未来1分钟倾向", short.get("label_60", "暂无明确方向"), short.get("alert", ""))
_card(cols[2], "未来2分钟倾向", short.get("label_120", "暂无明确方向"), "多条件一致才触发")
_card(cols[3], "短线信号", strength_text, "信号强度，不是已验证胜率")

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
book_text = "买盘承接较强" if buy_pressure >= 58 else ("卖盘压力较强" if buy_pressure <= 42 else "买卖力量接近")
change30 = float(m.get("change_30s_pct", 0.0) or 0.0)
change60 = float(m.get("change_60s_pct", 0.0) or 0.0)
vwap_pos = float(m.get("above_vwap_pct", 0.0) or 0.0)
cols = st.columns(4)
_card(cols[0], "盘口结论", book_text, f"五档买盘占 {buy_pressure:.0f}%")
_card(cols[1], "近30秒价格", f"{change30:+.2f}%", "直接显示涨跌")
_card(cols[2], "近60秒价格", f"{change60:+.2f}%", "观察是否持续同向")
_card(cols[3], "近60秒成交均价", "上方" if vwap_pos > 0 else ("下方" if vwap_pos < 0 else "附近"), f"偏离 {vwap_pos:+.2f}%")
latest_time = str(qmt_ticks.iloc[-1].get("captured_at", ""))
st.caption(f"最新采集：{latest_time}｜股票：{expected_symbol}｜样本数：{len(qmt_ticks)}")
st.info("当前已接入实时价格和五档盘口；逐笔成交/委托验证完成前，不伪造千手万手净流入。")

with st.expander("查看五档买卖盘"):
    r = qmt_ticks.iloc[-1]
    bids, asks = r.get("bidPrice", []), r.get("askPrice", [])
    bidv, askv = r.get("bidVol", []), r.get("askVol", [])
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
