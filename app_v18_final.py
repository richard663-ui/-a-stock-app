# -*- coding: utf-8 -*-
"""A股盯盘 V18 Final.

Four-layer dashboard:
1) Setup + mandatory 1-minute direction
2) real Level-2 order flow
3) swing/trend context
4) catalyst/fundamentals

Read-only. No order routing.
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
from modules.direction_v18 import analyze_direction_v18
from modules.event_radar import (
    FALLBACK_INDUSTRY,
    analyze_event_catalyst,
    auto_event_radar,
    detect_industry_concepts,
    event_certainty_grade,
)
from modules.qishi import analyze_qishi
from modules.qmt_level2 import QMTLevel2Manager
from modules.qmt_live import QMTLiveManager
from modules.setup_vwap import analyze_vwap_state, grade_setup
from modules.signals import make_action
from modules.ui_blocks import plot_qishi
from modules.utils import fmt_num, fmt_pct, normalize_code

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

st.set_page_config(page_title="A股盯盘 V18", page_icon="📈", layout="wide")
require_password()

st.markdown(
    """
    <style>
    .block-container {padding-top:.75rem;padding-bottom:2rem;max-width:1480px;}
    .v18-card {border:1px solid #e5e7eb;border-radius:14px;padding:13px 15px;
      min-height:112px;background:var(--secondary-background-color);box-shadow:0 1px 2px rgba(0,0,0,.03)}
    .v18-label {font-size:.86rem;opacity:.70;margin-bottom:6px}
    .v18-value {font-size:1.30rem;font-weight:760;line-height:1.25;word-break:break-word}
    .v18-sub {font-size:.80rem;opacity:.72;margin-top:7px;line-height:1.38}
    .status-line {border-radius:10px;padding:9px 12px;margin:2px 0 13px 0;
      background:var(--secondary-background-color);font-size:.88rem}
    @media (max-width:700px){.block-container{padding-left:.75rem;padding-right:.75rem}
      .v18-card{min-height:98px;padding:11px}.v18-value{font-size:1.12rem}}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def _local_tick_manager() -> QMTLiveManager:
    return QMTLiveManager(interval=1.0, max_rows=1800, runtime_dir="runtime")


@st.cache_resource
def _local_l2_manager() -> QMTLevel2Manager:
    return QMTLevel2Manager()


@st.cache_resource
def _cloud_bridge(config: BridgeConfig) -> CloudBridge:
    return CloudBridge(config)


def _symbol(code: str) -> str:
    code = normalize_code(code)
    if code.startswith(("6", "68", "5", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _config() -> BridgeConfig:
    try:
        return load_bridge_config(st.secrets)
    except Exception:
        return load_bridge_config()


def _load_realtime(symbol: str) -> Tuple[pd.DataFrame, Dict, str, Dict]:
    cfg = _config()
    if cfg.ok:
        try:
            bridge = _cloud_bridge(cfg)
            bridge.request_symbol(symbol)
            tick_payload = bridge.fetch_ticks(symbol) or {}
            ticks = tick_payload.get("ticks", []) if isinstance(tick_payload, dict) else []
            frame = pd.DataFrame(ticks if isinstance(ticks, list) else [])
            try:
                l2_payload = bridge.fetch_level2(symbol) or {}
            except Exception:
                l2_payload = {}
            status = {
                "ok": not frame.empty,
                "status": tick_payload.get("status", "等待本地桥梁") if isinstance(tick_payload, dict) else "等待本地桥梁",
                "captured_at": frame.iloc[-1].get("captured_at") if not frame.empty else None,
                "samples": len(frame),
                "error": "",
            }
            return frame.tail(1200), status, "国盛QMT云桥", l2_payload
        except Exception as exc:
            return pd.DataFrame(), {"ok": False, "status": "云桥连接失败", "samples": 0, "error": str(exc)}, "国盛QMT云桥", {}

    ticks = _local_tick_manager()
    ticks.set_symbol(symbol)
    l2 = _local_l2_manager()
    try:
        l2.switch(symbol)
        l2_payload = l2.snapshot()
    except Exception:
        l2_payload = {}
    return ticks.get_frame().tail(1200), ticks.get_status(), "国盛QMT本机", l2_payload


def _fallback_qishi() -> Dict:
    return {"ok": False, "latest_score": 0, "latest_state": "数据不足", "fund_score": 0,
            "risk_state": "数据不足", "reasons": [], "df": pd.DataFrame(), "consecutive_red": 0}


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
    zone = "水上" if float(dif.iloc[-1]) > 0 and float(dea.iloc[-1]) > 0 else "水下" if float(dif.iloc[-1]) < 0 and float(dea.iloc[-1]) < 0 else "零轴附近"
    return {"label": label, "detail": f"{zone}｜DIF-DEA {now:+.3f}"}


def _trend_structure(qishi: Dict) -> str:
    qdf = qishi.get("df", pd.DataFrame())
    if not qishi.get("ok") or qdf.empty:
        return "数据不足"
    last = qdf.iloc[-1]
    close = float(last.get("close", 0) or 0)
    ma5, ma10, ma20 = (float(last.get(k, 0) or 0) for k in ("MA5", "MA10", "MA20"))
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
            '<div class="v18-card">'
            f'<div class="v18-label">{html.escape(str(label))}</div>'
            f'<div class="v18-value">{html.escape(str(value))}</div>'
            f'<div class="v18-sub">{html.escape(str(sub))}</div></div>',
            unsafe_allow_html=True,
        )


def _money(value: float) -> str:
    value = float(value or 0.0)
    sign = "+" if value > 0 else "-" if value < 0 else ""
    x = abs(value)
    if x >= 100_000_000:
        return f"{sign}{x/100_000_000:.2f}亿"
    if x >= 10_000:
        return f"{sign}{x/10_000:.1f}万"
    return f"{sign}{x:.0f}"


def _funding(metrics: Dict, l2_summary: Dict) -> Dict:
    if l2_summary.get("ok"):
        m = l2_summary.get("metrics", {}) or {}
        active = float(m.get("active_buy_pct", 50) or 50)
        big = float(m.get("big_buy_pct", 50) or 50)
        score = max(0, min(100, 50 + (active - 50) * .9 + (big - 50) * .7))
        return {"score": score, "label": l2_summary.get("fund_label", "真实L2资金"), "reasons": l2_summary.get("reasons", [])}
    buy = float(metrics.get("buy_pct", 50) or 50)
    book = float(metrics.get("buy_pressure_pct", 50) or 50)
    score = max(0, min(100, 50 + (buy - 50) * .9 + (book - 50) * .55))
    label = "买方资金占优" if score >= 65 else "卖方资金占优" if score <= 35 else "资金暂时均衡"
    return {"score": score, "label": label, "reasons": []}


def _accuracy_text(validation: Dict) -> str:
    n = int(validation.get("true_l2_high_conf_samples", 0) or 0)
    acc = validation.get("true_l2_high_conf_accuracy_pct")
    if not n or acc is None:
        return "真实90%命中率：待积累样本"
    return f"L2高置信历史 {n}次｜命中 {float(acc):.1f}%"


st.title("A股盯盘 V18｜Setup × VWAP × Level-2")
st.caption("波段负责方向｜Setup负责筛选｜VWAP负责位置｜Level-2负责1分钟Timing｜只读不自动下单")

with st.sidebar:
    st.header("股票搜索")
    code_input = st.text_input("6位股票代码", "000400", max_chars=6)
    has_position = st.checkbox("已有仓位", False)
    cost = st.number_input("成本价", min_value=0.0, value=0.0, step=0.01)
    position_pct = st.number_input("持仓比例%", 0.0, 100.0, 0.0, 5.0)
    auto_event = st.checkbox("联网抓新闻（较慢）", False)
    refresh_seconds = st.selectbox("自动刷新", [1, 2, 5, 10], index=1, format_func=lambda x: f"每{x}秒")
    logout_button()

if st_autorefresh is not None:
    st_autorefresh(interval=refresh_seconds * 1000, key="v18_final_refresh")

code = normalize_code(code_input)
if len(code) != 6 or not code.isdigit():
    st.error("请输入6位A股股票代码，例如 301666。")
    st.stop()

symbol = _symbol(code)
ticks, qmt_status, mode, l2_payload = _load_realtime(symbol)
l2_summary = l2_payload.get("summary", {}) if isinstance(l2_payload, dict) else {}
short = analyze_direction_v18(ticks, l2_summary)
metrics = short.get("metrics", {}) or {}

snapshot = fetch_em_snapshot((code,)).get(code, {})
daily = fetch_tencent_kline(code, 260)
if not ticks.empty:
    row = ticks.iloc[-1]
    price = pd.to_numeric(pd.Series([row.get("lastPrice")]), errors="coerce").iloc[0]
    prev = pd.to_numeric(pd.Series([row.get("lastClose")]), errors="coerce").iloc[0]
    if pd.notna(price) and price > 0:
        snapshot["price"] = float(price)
        if pd.notna(prev) and prev > 0:
            snapshot["pct"] = (float(price) / float(prev) - 1.0) * 100.0
        snapshot["source"] = mode

if not snapshot:
    if not daily.empty:
        latest = daily.iloc[-1]
        snapshot = {"code": code, "name": FALLBACK_INDUSTRY.get(code, (code, []))[0],
                    "price": float(latest["close"]), "pct": 0.0, "source": "日K兜底"}
    else:
        snapshot = {"code": code, "name": code, "price": None, "pct": None, "source": "数据等待中"}

qishi = analyze_qishi(daily) if not daily.empty else _fallback_qishi()
macd = _macd_status(daily)
trend = _trend_structure(qishi)
industry, concepts, industry_src = detect_industry_concepts(code, snapshot)
auto_radar = auto_event_radar(code, snapshot, industry, concepts) if auto_event else {"score": 0, "label": "快速模式", "news": [], "reasons": []}
news_text = " ".join(n.get("title", "") for n in auto_radar.get("news", []))
catalyst = analyze_event_catalyst(code, news_text, concepts)
event_certainty = event_certainty_grade(news_text, auto_radar.get("news", []), catalyst)

vwap = analyze_vwap_state(ticks)
setup = grade_setup(qishi=qishi, macd=macd, catalyst=catalyst, vwap=vwap,
                    short_metrics=metrics, l2_summary=l2_summary)
funding = _funding(metrics, l2_summary)
action = make_action(snapshot, qishi, catalyst, funding, has_position, cost, position_pct)
validation = l2_summary.get("validation", {}) if isinstance(l2_summary, dict) else {}

latest_time = qmt_status.get("captured_at") or (ticks.iloc[-1].get("captured_at") if not ticks.empty else "等待")
age = metrics.get("age_seconds")
age_text = f"{float(age):.0f}秒" if age is not None else "未知"
l2_status = l2_payload.get("status", "等待Level-2") if isinstance(l2_payload, dict) else "等待Level-2"
st.markdown(
    f'<div class="status-line"><b>{html.escape(symbol)}</b>　{html.escape(mode)}｜'
    f'{html.escape(str(qmt_status.get("status", "等待桥梁")))}｜L2 {html.escape(str(l2_status))}｜'
    f'最新 {html.escape(str(latest_time))}｜延迟 {html.escape(age_text)}</div>', unsafe_allow_html=True)

# ① Setup + mandatory one-minute direction.
st.markdown(f"## ① Setup与1分钟方向｜{snapshot.get('name', code)}（{code}）")
st.write(f"当前价 **{fmt_num(snapshot.get('price'))}**　今日涨跌 **{fmt_pct(snapshot.get('pct'))}**　来源 **{snapshot.get('source', '未知')}**")
cols = st.columns(4)
_card(cols[0], "Setup评级", f"{setup['grade']}｜{setup['score']}/100", f"{setup['label']}｜{setup['trade_state']}")
_card(cols[1], "未来1分钟", short.get("label_60", "等待"), f"条件一致度 {int(short.get('condition_agreement', 0) or 0)}%｜{short.get('signal_source', '')}")
_card(cols[2], "未来2分钟", short.get("label_120", "等待"), "用于波段持仓的短时风险提示")
_card(cols[3], "VWAP位置", vwap.get("state", "等待VWAP"), f"偏离 {float(vwap.get('distance_pct', 0) or 0):+.2f}%｜60秒斜率 {float(vwap.get('slope_60s_pct', 0) or 0):+.2f}%")
st.caption("1分钟『条件一致度』不是历史准确率。" + _accuracy_text(validation))
if short.get("reasons"):
    st.caption("1分钟依据：" + "；".join(short["reasons"][:3]))
if setup.get("reasons"):
    st.caption("Setup依据：" + "；".join(setup["reasons"][:4]))
st.write(f"**持仓/动作：** {action.get('holding_advice', '')}")

# ② True L2 when available; explicit fallback otherwise.
st.markdown("## ② Level-2资金与盘口")
if l2_summary.get("ok"):
    lm = l2_summary.get("metrics", {}) or {}
    active = float(lm.get("active_buy_pct", 50) or 50)
    big = float(lm.get("big_buy_pct", 50) or 50)
    cols = st.columns(4)
    _card(cols[0], "真实主动成交", l2_summary.get("fund_label", "资金均衡"), f"主动买 {active:.0f}%｜净额 {_money(lm.get('active_net_amount', 0))}")
    _card(cols[1], "大/特大单", l2_summary.get("large_label", "大单均衡"), f"买入占 {big:.0f}%｜净额 {_money(lm.get('big_net_amount', 0))}")
    _card(cols[2], "撤单行为", l2_summary.get("cancel_label", "撤单均衡"), f"撤卖支持 {float(lm.get('cancel_sell_support_pct', 50) or 50):.0f}%")
    _card(cols[3], "全盘口/队列", "买方占优" if float(lm.get("total_book_buy_pct", 50) or 50) >= 55 else "卖方占优" if float(lm.get("total_book_buy_pct", 50) or 50) <= 45 else "盘口均衡", f"总委买 {float(lm.get('total_book_buy_pct', 50) or 50):.0f}%｜队列买方 {float(lm.get('queue_buy_pct', 50) or 50):.0f}%")
    caps = l2_payload.get("capabilities", {}) if isinstance(l2_payload, dict) else {}
    available = [k for k, v in caps.items() if isinstance(v, dict) and v.get("available")]
    st.caption("真实L2源：" + ("、".join(available) if available else "正在等待回调"))
else:
    buy = float(metrics.get("buy_pct", 50) or 50)
    book = float(metrics.get("buy_pressure_pct", 50) or 50)
    cols = st.columns(4)
    _card(cols[0], "成交方向（降级）", "买方偏强" if buy >= 60 else "卖方偏强" if buy <= 40 else "均衡", f"买入估算 {buy:.0f}%")
    _card(cols[1], "近60秒净成交", f"{float(metrics.get('net_lots', 0) or 0):+,.0f}手", "Tick估算，不冒充逐笔主力")
    _card(cols[2], "大成交（降级）", f"千手 买{int(metrics.get('thousand_buy', 0) or 0)}｜卖{int(metrics.get('thousand_sell', 0) or 0)}", "等待真实Level-2")
    _card(cols[3], "五档盘口", "买盘偏强" if book >= 60 else "卖盘偏强" if book <= 40 else "均衡", f"买盘 {book:.0f}%")

# ③ Swing context.
st.markdown("## ③ 波段与趋势")
cols = st.columns(4)
_card(cols[0], "今日动作", f"{action['buy_grade']}｜{action['buy_label']}", f"新仓建议 {action['new_pos']}")
_card(cols[1], "AI起势", action.get("q_state", "数据不足"), f"起势分 {float(qishi.get('latest_score', 0) or 0):.0f}/100")
_card(cols[2], "MACD", macd.get("label", "数据不足"), macd.get("detail", ""))
_card(cols[3], "波段结构", trend, f"风险 {action.get('risk_state', '数据不足')}")
if qishi.get("reasons"):
    st.caption("波段依据：" + "；".join(qishi["reasons"][:4]))
if qishi.get("ok"):
    plot_qishi(qishi, title=f"{snapshot.get('name', code)} {code}")

# ④ Events/fundamentals.
st.markdown("## ④ 事件催化与基本面")
cols = st.columns(4)
_card(cols[0], "行业", industry, f"来源 {industry_src}")
_card(cols[1], "事件催化", catalyst.get("label", "无"), f"催化分 {catalyst.get('score', 0)}/100")
_card(cols[2], "事件确定性", event_certainty.get("certainty", "低"), event_certainty.get("label", ""))
_card(cols[3], "估值概览", f"PE {fmt_num(snapshot.get('pe_dynamic'))}", f"PB {fmt_num(snapshot.get('pb'))}")
background = (catalyst.get("reasons", []) + auto_radar.get("reasons", []))[:3]
st.caption("背景提示：" + ("；".join(background) if background else "快速模式下暂未发现明确硬催化。"))

with st.expander("系统说明 / 风控"):
    st.write("- Setup分数是当前固定规则的工程基线，不是胜率。")
    st.write("- 1分钟高置信只代表实时条件高度同向；真实命中率由ROG端60秒后自动回标。")
    st.write("- 未来只有在样本外高置信命中率达到目标且样本量足够时，页面才应把它称为已验证准确率。")
    st.write("- 当前系统只读，不自动下单；A股T+1下，1分钟偏跌主要用于已持仓风险提示，不能当天买入后立刻卖出。")
    st.caption("页面更新时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
