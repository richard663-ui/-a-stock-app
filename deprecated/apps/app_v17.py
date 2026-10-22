# -*- coding: utf-8 -*-
"""A股盯盘 V17 final dashboard.

Password protected, cloud/local QMT auto-switching, four concise panels,
high-confidence 60/120-second warnings and a full AI trend chart.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Dict, Tuple

import pandas as pd
import streamlit as st

from modules.auth import logout_button, require_password
from modules.cloud_bridge import BridgeConfig, CloudBridge, load_bridge_config
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

st.set_page_config(page_title="A股盯盘 V17", page_icon="📈", layout="wide")
require_password()

st.markdown(
    """
    <style>
    .block-container {padding-top: 0.8rem; padding-bottom: 2rem; max-width: 1480px;}
    .v17-card {
        border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px 16px;
        min-height: 118px; background: var(--secondary-background-color);
        box-shadow: 0 1px 2px rgba(0,0,0,.03);
    }
    .v17-label {font-size: .88rem; opacity: .72; margin-bottom: 7px;}
    .v17-value {
        font-size: 1.36rem; font-weight: 760; line-height: 1.28;
        white-space: normal; overflow: visible; word-break: break-word;
    }
    .v17-sub {font-size: .82rem; opacity: .72; margin-top: 8px; line-height: 1.4;}
    .status-line {
        border-radius: 10px; padding: 9px 12px; margin: 2px 0 14px 0;
        background: var(--secondary-background-color); font-size: .9rem;
    }
    @media (max-width: 700px) {
        .block-container {padding-left: .8rem; padding-right: .8rem;}
        .v17-card {min-height: 102px; padding: 12px;}
        .v17-value {font-size: 1.18rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


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
    """Use Supabase when configured; otherwise connect to local QMT."""
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
            return frame.tail(1200), status, "国盛QMT云桥"
        except Exception as exc:
            return pd.DataFrame(), {
                "ok": False, "symbol": symbol, "status": "云桥连接失败",
                "samples": 0, "error": str(exc),
            }, "国盛QMT云桥"

    manager = get_qmt_manager()
    manager.set_symbol(symbol)
    return manager.get_frame().tail(1200), manager.get_status(), "国盛QMT本机"


def _fallback_qishi() -> Dict:
    return {
        "ok": False, "latest_score": 0, "latest_state": "数据不足",
        "fund_score": 0, "risk_state": "数据不足", "reasons": [],
        "df": pd.DataFrame(), "consecutive_red": 0,
    }


def _macd_status(df: pd.DataFrame) -> Dict[str, str]:
    if df is None or len(df) < 35:
        return {"label": "数据不足", "detail": "日K暂不可用"}
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
        label = "多头走弱"
    elif now < 0 and now > prev > prev2:
        label = "空头修复"
    elif now > 0:
        label = "金叉区间"
    else:
        label = "死叉区间"
    return {"label": label, "detail": f"DIF-DEA {now:+.3f}"}


def _trend_structure(qishi: Dict) -> str:
    qdf = qishi.get("df", pd.DataFrame())
    if not qishi.get("ok") or qdf.empty:
        return "数据不足"
    last = qdf.iloc[-1]
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


def _card(col, label: str, value: str, sub: str = "") -> None:
    with col:
        st.markdown(
            '<div class="v17-card">'
            f'<div class="v17-label">{html.escape(str(label))}</div>'
            f'<div class="v17-value">{html.escape(str(value))}</div>'
            f'<div class="v17-sub">{html.escape(str(sub))}</div>'
            "</div>",
            unsafe_allow_html=True,
        )


def _funding_score(metrics: Dict) -> Dict:
    buy_pct = float(metrics.get("buy_pct", 50.0) or 50.0)
    book = float(metrics.get("buy_pressure_pct", 50.0) or 50.0)
    score = max(0, min(100, 50 + (buy_pct - 50) * 0.9 + (book - 50) * 0.55))
    if score >= 65:
        label = "买方资金占优"
    elif score <= 35:
        label = "卖方资金占优"
    else:
        label = "资金暂时均衡"
    return {"score": score, "label": label, "reasons": []}


st.title("A股盯盘 V17｜最终实时版")
st.caption("搜索代码自动切股｜高置信短线预警｜资金方向与大成交估算｜完整AI起势图｜只读不下单")

with st.sidebar:
    st.header("股票搜索")
    code_input = st.text_input(
        "6位股票代码", "000400", max_chars=6,
        help="输入代码并按回车；ROG云桥会自动切换，无需PowerShell改股票。",
    )
    has_position = st.checkbox("已有仓位", False)
    cost = st.number_input("成本价", min_value=0.0, value=0.0, step=0.01)
    position_pct = st.number_input("持仓比例%", 0.0, 100.0, 0.0, 5.0)
    auto_event = st.checkbox("联网抓新闻（较慢）", False)
    refresh_seconds = st.selectbox("自动刷新", [1, 2, 5, 10], index=1, format_func=lambda x: f"每{x}秒")
    logout_button()

if st_autorefresh is not None:
    st_autorefresh(interval=refresh_seconds * 1000, key="qmt_v17_final_refresh")

code = normalize_code(code_input)
if len(code) != 6 or not code.isdigit():
    st.error("请输入6位A股股票代码，例如 301666。")
    st.stop()

symbol = _qmt_symbol(code)
qmt_ticks, qmt_status, realtime_mode = _load_realtime(symbol)
short = analyze_short_direction(qmt_ticks)
metrics = short.get("metrics", {})

snapshots = fetch_em_snapshot((code,))
snapshot = snapshots.get(code, {})
daily = fetch_tencent_kline(code, 260)

if not qmt_ticks.empty:
    qrow = qmt_ticks.iloc[-1]
    qprice = pd.to_numeric(pd.Series([qrow.get("lastPrice")]), errors="coerce").iloc[0]
    last_close = pd.to_numeric(pd.Series([qrow.get("lastClose")]), errors="coerce").iloc[0]
    if pd.notna(qprice) and qprice > 0:
        snapshot["price"] = float(qprice)
        if pd.notna(last_close) and last_close > 0:
            snapshot["pct"] = (float(qprice) / float(last_close) - 1.0) * 100.0
        snapshot["source"] = realtime_mode
        for key in ("open", "high", "low", "amount", "volume"):
            snapshot[key] = qrow.get(key)

if not snapshot:
    if not daily.empty:
        latest = daily.iloc[-1]
        snapshot = {
            "code": code,
            "name": FALLBACK_INDUSTRY.get(code, (code, []))[0],
            "price": float(latest["close"]),
            "pct": float(latest.get("pct_change", 0) or 0),
            "source": "日K兜底",
        }
    else:
        snapshot = {"code": code, "name": code, "price": None, "pct": None, "source": "数据等待中"}

qishi = analyze_qishi(daily) if not daily.empty else _fallback_qishi()
industry, concepts, industry_src = detect_industry_concepts(code, snapshot)
auto_radar = auto_event_radar(code, snapshot, industry, concepts) if auto_event else {
    "score": 0, "label": "快速模式", "news": [], "reasons": []
}
news_text = " ".join(n.get("title", "") for n in auto_radar.get("news", []))
catalyst = analyze_event_catalyst(code, news_text, concepts)
event_certainty = event_certainty_grade(news_text, auto_radar.get("news", []), catalyst)

funding = _funding_score(metrics)
action = make_action(snapshot, qishi, catalyst, funding, has_position, cost, position_pct)
macd = _macd_status(daily)
trend_text = _trend_structure(qishi)

latest_time = qmt_status.get("captured_at") or (qmt_ticks.iloc[-1].get("captured_at") if not qmt_ticks.empty else "等待")
age = metrics.get("age_seconds")
age_text = f"{float(age):.0f}秒" if age is not None else "未知"
bridge_text = qmt_status.get("status", "等待桥梁")
st.markdown(
    f'<div class="status-line"><b>{html.escape(symbol)}</b>　'
    f'{html.escape(realtime_mode)}｜{html.escape(str(bridge_text))}｜'
    f'最新 {html.escape(str(latest_time))}｜延迟 {html.escape(age_text)}｜'
    f'样本 {len(qmt_ticks)}</div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    if qmt_status.get("ok"):
        st.success(f"{symbol} 已连接｜{len(qmt_ticks)}条")
    elif qmt_status.get("error"):
        st.error(f"{qmt_status.get('status')}：{qmt_status.get('error')}")
    else:
        st.info(qmt_status.get("status", "正在等待QMT数据"))

st.markdown(f"## ① 今日结论｜{snapshot.get('name', code)}（{code}）")
st.write(
    f"当前价 **{fmt_num(snapshot.get('price'))}**　"
    f"今日涨跌 **{fmt_pct(snapshot.get('pct'))}**　"
    f"来源 **{snapshot.get('source', '未知')}**"
)
agreement = int(short.get("condition_agreement", 0) or 0)
signal_display = f"{agreement}%一致" if short.get("live") else short.get("label_60", "等待")
cols = st.columns(4)
_card(cols[0], "今日动作", f"{action['buy_grade']}｜{action['buy_label']}", f"新仓建议 {action['new_pos']}")
_card(cols[1], "未来1分钟", short.get("label_60", "观望"), short.get("alert", ""))
_card(cols[2], "未来2分钟", short.get("label_120", "观望"), "只在高置信条件同向时提示")
_card(cols[3], "条件一致度", signal_display, "≥90%才发出方向预警")

if short.get("level") == "红色":
    st.error(short.get("alert"))
elif short.get("level") == "绿色":
    st.success(short.get("alert"))
elif short.get("label_60") in ("数据补齐中", "数据延迟", "休市"):
    st.info(short.get("alert"))
else:
    st.warning(short.get("alert"))
st.write(f"**持仓建议：** {action['holding_advice']}")
if short.get("reasons"):
    st.caption("短线依据：" + "；".join(short["reasons"][:3]))

st.markdown("## ② 资金与盘口")
buy_pct = float(metrics.get("buy_pct", 50.0) or 50.0)
buy_pressure = float(metrics.get("buy_pressure_pct", 50.0) or 50.0)
net_lots = float(metrics.get("net_lots", 0.0) or 0.0)
th_buy = int(metrics.get("thousand_buy", 0) or 0)
th_sell = int(metrics.get("thousand_sell", 0) or 0)
ten_buy = int(metrics.get("ten_thousand_buy", 0) or 0)
ten_sell = int(metrics.get("ten_thousand_sell", 0) or 0)
book_text = "买盘承接较强" if buy_pressure >= 60 else ("卖盘压力较强" if buy_pressure <= 40 else "盘口均衡")
flow_text = "主动买入占优" if buy_pct >= 60 else ("主动卖出占优" if buy_pct <= 40 else "主动成交均衡")
cols = st.columns(4)
_card(cols[0], "成交资金方向", flow_text, f"买入估算 {buy_pct:.0f}%｜卖出 {100-buy_pct:.0f}%")
_card(cols[1], "近60秒净主动成交", f"{net_lots:+,.0f} 手", "按QMT成交增量估算，不冒充逐笔主力")
_card(cols[2], "千手级大成交", f"买 {th_buy}｜卖 {th_sell}", f"万手级 买 {ten_buy}｜卖 {ten_sell}")
_card(cols[3], "盘口承接", book_text, f"五档买盘 {buy_pressure:.0f}%｜卖盘 {100-buy_pressure:.0f}%")

change30 = float(metrics.get("change_30s_pct", 0.0) or 0.0)
change60 = float(metrics.get("change_60s_pct", 0.0) or 0.0)
vwap_pos = float(metrics.get("above_vwap_pct", 0.0) or 0.0)
st.caption(
    f"近30秒 {change30:+.2f}%｜近60秒 {change60:+.2f}%｜"
    f"相对近60秒成交均价 {vwap_pos:+.2f}%｜"
    "千手/万手为秒级成交增量估算；逐笔Level-2接入后会自动替换。"
)

with st.expander("查看五档买卖盘"):
    if qmt_ticks.empty:
        st.info("等待QMT盘口数据。")
    else:
        row = qmt_ticks.iloc[-1]
        bids, asks = row.get("bidPrice", []), row.get("askPrice", [])
        bidv, askv = row.get("bidVol", []), row.get("askVol", [])
        rows = []
        for i in range(min(5, len(bids), len(asks), len(bidv), len(askv))):
            rows.append({"档位": i + 1, "买价": bids[i], "买量(手)": bidv[i], "卖价": asks[i], "卖量(手)": askv[i]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.markdown("## ③ 趋势与指标")
cols = st.columns(4)
_card(cols[0], "AI起势", action["q_state"], f"起势分 {qishi.get('latest_score', 0):.0f}/100")
_card(cols[1], "趋势结构", trend_text, "根据日K与均线排列")
_card(cols[2], "MACD状态", macd["label"], macd["detail"])
_card(cols[3], "风险状态", action["risk_state"], f"资金量能 {qishi.get('fund_score', 0):.0f}/100")
if qishi.get("reasons"):
    st.caption("趋势依据：" + "；".join(qishi["reasons"][:4]))
if qishi.get("ok"):
    plot_qishi(qishi, title=f"{snapshot.get('name', code)} {code}")
else:
    st.info("日K数据正在加载，实时行情与资金板块仍可正常使用。")

st.markdown("## ④ 事件催化与基本面")
cols = st.columns(4)
_card(cols[0], "行业", industry, f"来源 {industry_src}")
_card(cols[1], "事件催化", catalyst.get("label", "无"), f"催化分 {catalyst.get('score', 0)}/100")
_card(cols[2], "事件确定性", event_certainty.get("certainty", "低"), event_certainty.get("label", ""))
_card(cols[3], "估值概览", f"PE {fmt_num(snapshot.get('pe_dynamic'))}", f"PB {fmt_num(snapshot.get('pb'))}")
background = (catalyst.get("reasons", []) + auto_radar.get("reasons", []))[:3]
st.caption("背景提示：" + ("；".join(background) if background else "快速模式下暂未发现明确硬催化。"))

with st.expander("买卖条件与风控位"):
    st.write("**买入/加仓条件**")
    for item in action.get("buy_zone", []):
        st.write("- " + item)
    st.write("**卖出/风控条件**")
    for item in action.get("sell_zone", []):
        st.write("- " + item)
    st.caption("页面更新时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
