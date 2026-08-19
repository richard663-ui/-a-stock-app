# -*- coding: utf-8 -*-
"""V18 mobile/live dashboard: progressive render, deliberate stock switching, responsive cards."""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict

import pandas as pd
import streamlit as st

from modules.auth import logout_button, require_password
from modules.cloud_bridge import CloudBridge, load_bridge_config
from modules.data_sources import fetch_em_snapshot, fetch_tencent_kline
from modules.direction_v18 import analyze_direction_v18
from modules.event_radar import analyze_event_catalyst, detect_industry_concepts, event_certainty_grade
from modules.qishi import analyze_qishi
from modules.setup_vwap import analyze_vwap_state, grade_setup
from modules.signals import make_action
from modules.ui_blocks import plot_qishi
from modules.utils import fmt_num, fmt_pct, normalize_code

st.set_page_config(page_title="A股盯盘 V18", page_icon="📈", layout="wide")
require_password()

st.markdown(
    """
    <style>
    .block-container {
        max-width: 1440px;
        padding-top: .75rem;
        padding-bottom: 2.2rem;
    }
    h1 {letter-spacing: -0.02em; margin-bottom: .25rem !important;}
    .v18-subtitle {
        color: var(--text-color);
        opacity: .66;
        font-size: .92rem;
        line-height: 1.55;
        margin: 0 0 .75rem 0;
    }
    .ticker-strip {
        display: flex;
        flex-wrap: wrap;
        gap: .4rem .8rem;
        align-items: center;
        border: 1px solid rgba(128,128,128,.22);
        background: var(--secondary-background-color);
        border-radius: 12px;
        padding: .68rem .85rem;
        margin: .35rem 0 .85rem 0;
        font-size: .88rem;
        line-height: 1.5;
        white-space: normal;
        overflow: visible;
    }
    .ticker-main {font-weight: 760;}
    .section-head {
        display: flex;
        align-items: baseline;
        gap: .5rem;
        margin: 1.15rem 0 .55rem 0;
    }
    .section-no {
        font-size: .86rem;
        font-weight: 760;
        opacity: .58;
        min-width: 1.6rem;
    }
    .section-title {
        font-size: 1.12rem;
        font-weight: 780;
        line-height: 1.35;
    }
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: .72rem;
        margin: .2rem 0 .55rem 0;
    }
    .v18-card {
        min-width: 0;
        min-height: 112px;
        border: 1px solid rgba(128,128,128,.20);
        border-radius: 14px;
        background: var(--secondary-background-color);
        padding: .82rem .88rem;
        box-shadow: 0 1px 2px rgba(0,0,0,.025);
    }
    .v18-label {
        font-size: .79rem;
        opacity: .62;
        line-height: 1.35;
        margin-bottom: .42rem;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        word-break: break-word;
    }
    .v18-value {
        font-size: 1.17rem;
        font-weight: 780;
        line-height: 1.38;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        word-break: break-word;
        overflow-wrap: anywhere;
    }
    .v18-sub {
        margin-top: .42rem;
        font-size: .79rem;
        line-height: 1.45;
        opacity: .67;
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
        word-break: break-word;
        overflow-wrap: anywhere;
    }
    .notice-box {
        border-left: 3px solid rgba(128,128,128,.38);
        background: var(--secondary-background-color);
        border-radius: 8px;
        padding: .62rem .8rem;
        margin: .45rem 0 .65rem 0;
        font-size: .88rem;
        line-height: 1.55;
        white-space: normal;
        overflow-wrap: anywhere;
    }
    [data-testid="stMetricValue"],
    [data-testid="stMetricLabel"],
    [data-testid="stMetricDelta"] {
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
    }
    @media (max-width: 980px) {
        .metric-grid {grid-template-columns: repeat(2, minmax(0, 1fr));}
        .v18-card {min-height: 104px;}
    }
    @media (max-width: 560px) {
        .block-container {padding-left: .72rem; padding-right: .72rem; padding-top: .5rem;}
        .metric-grid {grid-template-columns: 1fr; gap: .55rem;}
        .v18-card {min-height: 0; padding: .72rem .78rem; border-radius: 12px;}
        .v18-value {font-size: 1.08rem;}
        .section-head {margin-top: .95rem;}
        .ticker-strip {font-size: .82rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def _bridge() -> CloudBridge:
    cfg = load_bridge_config(st.secrets)
    return CloudBridge(cfg)


@st.cache_data(ttl=300, show_spinner=False)
def _load_static_pack(code: str) -> Dict[str, Any]:
    """Load slow/static inputs separately so live cards can render first."""
    snapshot: Dict[str, Any] = {}
    daily = pd.DataFrame()
    errors = []

    try:
        snapshot = dict(fetch_em_snapshot((code,)).get(code, {}) or {})
    except Exception as exc:
        errors.append(f"快照：{type(exc).__name__}")

    try:
        daily = fetch_tencent_kline(code, 260)
    except Exception as exc:
        errors.append(f"日K：{type(exc).__name__}")
        daily = pd.DataFrame()

    qishi = analyze_qishi(daily) if daily is not None and not daily.empty else {
        "ok": False,
        "latest_score": 0,
        "latest_state": "数据不足",
        "fund_score": 0,
        "risk_state": "数据不足",
        "reasons": [],
        "df": pd.DataFrame(),
        "consecutive_red": 0,
    }
    macd = _macd(daily)

    try:
        industry, concepts, industry_src = detect_industry_concepts(code, snapshot)
    except Exception:
        industry, concepts, industry_src = "待识别", [], "暂不可用"

    try:
        catalyst = analyze_event_catalyst(code, "", concepts)
    except Exception:
        catalyst = {"label": "暂无", "score": 0, "reasons": []}

    try:
        event_certainty = event_certainty_grade("", [], catalyst)
    except Exception:
        event_certainty = {"certainty": "低", "label": "暂无明确硬催化"}

    return {
        "snapshot": snapshot,
        "daily": daily,
        "qishi": qishi,
        "macd": macd,
        "industry": industry,
        "concepts": concepts,
        "industry_src": industry_src,
        "catalyst": catalyst,
        "event_certainty": event_certainty,
        "errors": errors,
    }


def _symbol(code: str) -> str:
    code = normalize_code(code)
    if code.startswith(("6", "68", "5", "9")):
        return code + ".SH"
    if code.startswith(("4", "8")):
        return code + ".BJ"
    return code + ".SZ"


def _macd(df: pd.DataFrame) -> Dict[str, str]:
    if df is None or len(df) < 35:
        return {"label": "数据不足", "detail": "日K暂不可用"}
    close = pd.to_numeric(df["close"], errors="coerce")
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    gap = dif - dea
    now, prev = float(gap.iloc[-1]), float(gap.iloc[-2])
    label = "刚形成金叉" if prev <= 0 < now else "刚形成死叉" if prev >= 0 > now else "金叉区间" if now > 0 else "死叉区间"
    zone = "水上" if float(dif.iloc[-1]) > 0 and float(dea.iloc[-1]) > 0 else "水下" if float(dif.iloc[-1]) < 0 and float(dea.iloc[-1]) < 0 else "零轴附近"
    return {"label": label, "detail": f"{zone}｜DIF-DEA {now:+.3f}"}


def _funding(metrics: Dict[str, Any], l2: Dict[str, Any]) -> Dict[str, Any]:
    if l2.get("ok"):
        m = l2.get("metrics", {}) or {}
        active = float(m.get("active_buy_pct", 50) or 50)
        big = float(m.get("big_buy_pct", 50) or 50)
        return {
            "score": max(0, min(100, 50 + .9 * (active - 50) + .7 * (big - 50))),
            "label": l2.get("fund_label", "真实L2资金"),
            "reasons": l2.get("reasons", []),
        }
    buy = float(metrics.get("buy_pct", 50) or 50)
    book = float(metrics.get("buy_pressure_pct", 50) or 50)
    return {
        "score": max(0, min(100, 50 + .9 * (buy - 50) + .55 * (book - 50))),
        "label": "Tick资金估算",
        "reasons": [],
    }


def _money(v: Any) -> str:
    v = float(v or 0)
    sign = "+" if v > 0 else "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e8:
        return f"{sign}{a / 1e8:.2f}亿"
    if a >= 1e4:
        return f"{sign}{a / 1e4:.1f}万"
    return f"{sign}{a:.0f}"


def _section(no: str, title: str) -> None:
    st.markdown(
        f'<div class="section-head"><span class="section-no">{html.escape(no)}</span>'
        f'<span class="section-title">{html.escape(title)}</span></div>',
        unsafe_allow_html=True,
    )


def _grid(cards) -> None:
    blocks = []
    for label, value, sub in cards:
        blocks.append(
            '<div class="v18-card">'
            f'<div class="v18-label">{html.escape(str(label))}</div>'
            f'<div class="v18-value">{html.escape(str(value))}</div>'
            f'<div class="v18-sub">{html.escape(str(sub or ""))}</div>'
            '</div>'
        )
    st.markdown('<div class="metric-grid">' + ''.join(blocks) + '</div>', unsafe_allow_html=True)


def _notice(text: str) -> None:
    st.markdown(f'<div class="notice-box">{html.escape(str(text))}</div>', unsafe_allow_html=True)


def _fetch_live(symbol: str) -> Dict[str, Any]:
    bridge = _bridge()
    bridge.request_symbol(symbol)
    tick_payload = bridge.fetch_ticks(symbol) or {}
    l2_payload = bridge.fetch_level2(symbol) or {}
    ticks = pd.DataFrame(tick_payload.get("ticks", []) if isinstance(tick_payload, dict) else [])
    l2 = l2_payload.get("summary", {}) if isinstance(l2_payload, dict) else {}
    short = analyze_direction_v18(ticks, l2)
    metrics = short.get("metrics", {}) or {}
    vwap = analyze_vwap_state(ticks)

    live_snapshot: Dict[str, Any] = {"price": None, "pct": None, "source": "等待QMT"}
    if not ticks.empty:
        row = ticks.iloc[-1]
        p = pd.to_numeric(pd.Series([row.get("lastPrice")]), errors="coerce").iloc[0]
        pc = pd.to_numeric(pd.Series([row.get("lastClose")]), errors="coerce").iloc[0]
        if pd.notna(p) and p > 0:
            live_snapshot["price"] = float(p)
            live_snapshot["source"] = "国盛QMT云桥"
            if pd.notna(pc) and pc > 0:
                live_snapshot["pct"] = (float(p) / float(pc) - 1) * 100

    return {
        "tick_payload": tick_payload if isinstance(tick_payload, dict) else {},
        "l2_payload": l2_payload if isinstance(l2_payload, dict) else {},
        "ticks": ticks,
        "l2": l2 if isinstance(l2, dict) else {},
        "short": short if isinstance(short, dict) else {},
        "metrics": metrics,
        "vwap": vwap if isinstance(vwap, dict) else {},
        "snapshot": live_snapshot,
    }


if "active_code" not in st.session_state:
    st.session_state["active_code"] = "000400"
if "stock_names" not in st.session_state:
    st.session_state["stock_names"] = {}

st.title("A股盯盘 V18")
st.markdown(
    '<div class="v18-subtitle">波段负责方向 · Setup筛选 · VWAP位置 · Level-2负责1分钟Timing · 只读不下单</div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("股票搜索")
    with st.form("stock_switch_form", clear_on_submit=False):
        raw_code = st.text_input(
            "6位股票代码",
            value=st.session_state["active_code"],
            max_chars=6,
            help="输入完整6位代码后点“切换股票”。不会再在输入过程中反复重载页面。",
        )
        submitted = st.form_submit_button("切换股票", use_container_width=True, type="primary")
    if submitted:
        candidate = str(raw_code).strip()
        if len(candidate) == 6 and candidate.isdigit():
            st.session_state["active_code"] = candidate
        else:
            st.error("请输入完整6位数字股票代码。")

    has_position = st.checkbox("已有仓位", False)
    cost = st.number_input("成本价", min_value=0.0, value=0.0, step=0.01)
    position_pct = st.number_input("持仓比例%", 0.0, 100.0, 0.0, 5.0)
    refresh_seconds = st.selectbox("实时刷新", [3, 5, 10, 30], index=1, format_func=lambda x: f"每{x}秒")
    st.caption("手机建议5秒；ROG后台仍每秒采样。")
    logout_button()

code = st.session_state["active_code"]
symbol = _symbol(code)
stock_name = st.session_state["stock_names"].get(code, code)

st.markdown(
    f'<div class="ticker-strip"><span class="ticker-main">{html.escape(stock_name)} · {html.escape(symbol)}</span>'
    '<span>切股后实时区先显示，日K与基本面随后加载</span></div>',
    unsafe_allow_html=True,
)


@st.fragment(run_every=refresh_seconds)
def _live_panels() -> None:
    try:
        live = _fetch_live(symbol)
        st.session_state["last_live"] = {"symbol": symbol, "data": live}

        tick_payload = live["tick_payload"]
        l2_payload = live["l2_payload"]
        l2 = live["l2"]
        short = live["short"]
        metrics = live["metrics"]
        vwap = live["vwap"]
        snap = live["snapshot"]

        validation = l2.get("validation", {}) if isinstance(l2, dict) else {}
        n = int(validation.get("true_l2_high_conf_samples", 0) or 0)
        acc = validation.get("true_l2_high_conf_accuracy_pct")
        agreement = int(short.get("condition_agreement", 0) or 0)

        st.markdown(
            f'<div class="ticker-strip"><span>QMT {html.escape(str(tick_payload.get("status", "等待")))}</span>'
            f'<span>L2 {html.escape(str(l2_payload.get("status", "等待")))}</span>'
            f'<span>刷新 {datetime.now().strftime("%H:%M:%S")}</span></div>',
            unsafe_allow_html=True,
        )

        _section("①", "实时结论与1分钟方向")
        _grid([
            ("当前价", fmt_num(snap.get("price")), f"今日 {fmt_pct(snap.get('pct'))}｜{snap.get('source', '等待')}"),
            ("未来1分钟", short.get("label_60", "等待"), f"条件一致度 {agreement}%"),
            ("未来2分钟", short.get("label_120", "等待"), "仅在价格与订单流同向时触发"),
            ("VWAP位置", vwap.get("state", "等待"), f"偏离 {float(vwap.get('distance_pct', 0) or 0):+.2f}%"),
        ])
        alert = short.get("alert") or "正在积累实时样本，暂不强行给方向。"
        history = f"真实L2高置信历史：{float(acc):.1f}% / {n}次" if n and acc is not None else "真实L2高置信历史：待积累"
        _notice(f"{alert}　｜　{history}")

        _section("②", "Level-2资金与盘口")
        if l2.get("ok"):
            lm = l2.get("metrics", {}) or {}
            _grid([
                ("真实主动成交", l2.get("fund_label", "均衡"), f"主动买 {float(lm.get('active_buy_pct', 50) or 50):.0f}%"),
                ("大/特大单", l2.get("large_label", "均衡"), f"净额 {_money(lm.get('big_net_amount', 0))}"),
                ("撤单行为", l2.get("cancel_label", "均衡"), "撤买偏弱｜撤卖偏强"),
                ("十档盘口", f"买方 {float(lm.get('depth10_buy_pct', 50) or 50):.0f}%", f"队列买方 {float(lm.get('queue_buy_pct', 50) or 50):.0f}%"),
            ])
        else:
            buy = float(metrics.get("buy_pct", 50) or 50)
            book = float(metrics.get("buy_pressure_pct", 50) or 50)
            _grid([
                ("Level-2状态", "等待真实逐笔数据", "未返回时自动降级，不冒充主力资金"),
                ("成交方向（降级）", f"买方 {buy:.0f}%", "基于QMT Tick估算"),
                ("近60秒净成交", f"{float(metrics.get('net_lots', 0) or 0):+,.0f}手", "仅作短线辅助"),
                ("五档盘口", f"买方 {book:.0f}%", "真实L2恢复后自动替换"),
            ])
    except Exception as exc:
        st.session_state["last_live"] = {"symbol": symbol, "data": {}}
        _section("①", "实时结论与1分钟方向")
        _grid([
            ("当前状态", "实时连接暂不可用", "页面保留，不会白屏"),
            ("股票", symbol, "下一刷新周期自动重试"),
            ("故障类型", type(exc).__name__, "请看下方详细信息"),
            ("刷新频率", f"每{refresh_seconds}秒", "无需重新输入股票代码"),
        ])
        _notice(f"实时刷新失败：{type(exc).__name__}: {exc}")


_live_panels()

# Slow/static content deliberately comes AFTER the live panels. A new stock can no longer
# leave the screen empty while Eastmoney/Tencent data are being fetched.
with st.spinner(f"正在加载 {code} 的日K、Setup与基本面…"):
    static = _load_static_pack(code)

snapshot = dict(static.get("snapshot", {}) or {})
daily = static.get("daily", pd.DataFrame())
qishi = static.get("qishi", {}) or {}
macd = static.get("macd", {}) or {}
industry = static.get("industry", "待识别")
industry_src = static.get("industry_src", "暂不可用")
catalyst = static.get("catalyst", {}) or {}
event_certainty = static.get("event_certainty", {}) or {}

if snapshot.get("name"):
    st.session_state["stock_names"][code] = str(snapshot.get("name"))

last_live_state = st.session_state.get("last_live", {})
live = last_live_state.get("data", {}) if last_live_state.get("symbol") == symbol else {}
live_snapshot = live.get("snapshot", {}) if isinstance(live, dict) else {}
if live_snapshot.get("price"):
    snapshot["price"] = live_snapshot.get("price")
    snapshot["pct"] = live_snapshot.get("pct")
    snapshot["source"] = live_snapshot.get("source")

metrics = live.get("metrics", {}) if isinstance(live, dict) else {}
l2 = live.get("l2", {}) if isinstance(live, dict) else {}
vwap = live.get("vwap", {}) if isinstance(live, dict) else {}
funding = _funding(metrics or {}, l2 or {})
setup = grade_setup(
    qishi=qishi,
    macd=macd,
    catalyst=catalyst,
    vwap=vwap or {"state": "等待", "distance_pct": 0},
    short_metrics=metrics or {},
    l2_summary=l2 or {},
)
action = make_action(snapshot, qishi, catalyst, funding, has_position, cost, position_pct)

_section("③", "Setup、波段与趋势")
_grid([
    ("Setup评级", f"{setup.get('grade', 'C')}｜{setup.get('score', 0)}/100", setup.get("trade_state", "等待更多确认")),
    ("今日动作", f"{action.get('buy_grade', 'C')}｜{action.get('buy_label', '观察')}", f"新仓建议 {action.get('new_pos', '0%')}"),
    ("AI起势", action.get("q_state", "数据不足"), f"起势分 {float(qishi.get('latest_score', 0) or 0):.0f}/100"),
    ("MACD", macd.get("label", "数据不足"), macd.get("detail", "")),
])
_notice(action.get("holding_advice", "未输入持仓，按新开仓逻辑处理。"))
if qishi.get("ok"):
    plot_qishi(qishi, title=f"{snapshot.get('name', code)} {code}")
else:
    st.info("日K暂未取到；实时方向和Level-2仍可继续使用。")

_section("④", "事件催化与基本面")
_grid([
    ("行业", industry, f"来源 {industry_src}"),
    ("事件催化", catalyst.get("label", "暂无"), f"催化分 {catalyst.get('score', 0)}/100"),
    ("事件确定性", event_certainty.get("certainty", "低"), event_certainty.get("label", "暂无明确硬催化")),
    ("估值", f"PE {fmt_num(snapshot.get('pe_dynamic'))}", f"PB {fmt_num(snapshot.get('pb'))}"),
])

if static.get("errors"):
    st.caption("静态数据部分降级：" + "；".join(static.get("errors", [])))

with st.expander("查看买卖条件与风控位"):
    st.write("**买入/加仓条件**")
    for item in action.get("buy_zone", []):
        st.write("- " + item)
    st.write("**卖出/风控条件**")
    for item in action.get("sell_zone", []):
        st.write("- " + item)
    st.caption("页面更新时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
