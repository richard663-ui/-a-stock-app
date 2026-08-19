# -*- coding: utf-8 -*-
"""V18 mobile/live dashboard: built-in fragment refresh, no full-page refresh loop."""
from __future__ import annotations

from datetime import datetime
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

@st.cache_resource
def _bridge():
    cfg = load_bridge_config(st.secrets)
    return CloudBridge(cfg)

def _symbol(code: str) -> str:
    if code.startswith(("6", "68", "5", "9")):
        return code + ".SH"
    if code.startswith(("4", "8")):
        return code + ".BJ"
    return code + ".SZ"

def _macd(df: pd.DataFrame):
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

def _funding(metrics, l2):
    if l2.get("ok"):
        m = l2.get("metrics", {})
        active = float(m.get("active_buy_pct", 50) or 50)
        big = float(m.get("big_buy_pct", 50) or 50)
        return {"score": max(0, min(100, 50 + .9*(active-50) + .7*(big-50))), "label": l2.get("fund_label", "真实L2资金"), "reasons": l2.get("reasons", [])}
    buy = float(metrics.get("buy_pct", 50) or 50)
    book = float(metrics.get("buy_pressure_pct", 50) or 50)
    return {"score": max(0, min(100, 50 + .9*(buy-50) + .55*(book-50))), "label": "Tick资金估算", "reasons": []}

def _money(v):
    v = float(v or 0); s = "+" if v > 0 else ""; a = abs(v)
    return f"{s}{v/1e8:.2f}亿" if a >= 1e8 else f"{s}{v/1e4:.1f}万" if a >= 1e4 else f"{v:.0f}"

st.title("A股盯盘 V18｜Setup × VWAP × Level-2")
st.caption("波段负责方向｜Setup筛选｜VWAP位置｜Level-2负责1分钟Timing｜只读")
with st.sidebar:
    code = normalize_code(st.text_input("6位股票代码", "000400", max_chars=6))
    has_position = st.checkbox("已有仓位", False)
    cost = st.number_input("成本价", min_value=0.0, value=0.0, step=0.01)
    position_pct = st.number_input("持仓比例%", 0.0, 100.0, 0.0, 5.0)
    refresh_seconds = st.selectbox("实时刷新", [3, 5, 10, 30], index=1, format_func=lambda x: f"每{x}秒")
    st.caption("手机建议5秒；ROG后台仍每秒采样。")
    logout_button()
if len(code) != 6 or not code.isdigit():
    st.error("请输入6位A股股票代码，例如301666。")
    st.stop()

symbol = _symbol(code)
base_snapshot = dict(fetch_em_snapshot((code,)).get(code, {}) or {})
daily = fetch_tencent_kline(code, 260)
qishi = analyze_qishi(daily) if not daily.empty else {"ok": False, "latest_score": 0, "risk_state": "数据不足", "reasons": [], "df": pd.DataFrame()}
macd = _macd(daily)
industry, concepts, industry_src = detect_industry_concepts(code, base_snapshot)
catalyst = analyze_event_catalyst(code, "", concepts)
event_certainty = event_certainty_grade("", [], catalyst)


def _live():
    try:
        bridge = _bridge()
        bridge.request_symbol(symbol)
        tick_payload = bridge.fetch_ticks(symbol) or {}
        l2_payload = bridge.fetch_level2(symbol) or {}
        ticks = pd.DataFrame(tick_payload.get("ticks", []) if isinstance(tick_payload, dict) else [])
        l2 = l2_payload.get("summary", {}) if isinstance(l2_payload, dict) else {}
        short = analyze_direction_v18(ticks, l2)
        metrics = short.get("metrics", {}) or {}
        snapshot = dict(base_snapshot)
        if not ticks.empty:
            row = ticks.iloc[-1]
            p = pd.to_numeric(pd.Series([row.get("lastPrice")]), errors="coerce").iloc[0]
            pc = pd.to_numeric(pd.Series([row.get("lastClose")]), errors="coerce").iloc[0]
            if pd.notna(p) and p > 0:
                snapshot["price"] = float(p); snapshot["source"] = "国盛QMT云桥"
                if pd.notna(pc) and pc > 0: snapshot["pct"] = (float(p)/float(pc)-1)*100
        vwap = analyze_vwap_state(ticks)
        setup = grade_setup(qishi=qishi, macd=macd, catalyst=catalyst, vwap=vwap, short_metrics=metrics, l2_summary=l2)
        action = make_action(snapshot, qishi, catalyst, _funding(metrics, l2), has_position, cost, position_pct)
        validation = l2.get("validation", {}) if isinstance(l2, dict) else {}
        n = int(validation.get("true_l2_high_conf_samples", 0) or 0)
        acc = validation.get("true_l2_high_conf_accuracy_pct")

        st.caption(f"{symbol}｜QMT云桥 {tick_payload.get('status','等待')}｜L2 {l2_payload.get('status','等待')}｜刷新 {datetime.now().strftime('%H:%M:%S')}")
        st.subheader(f"① Setup与1分钟方向｜{snapshot.get('name', code)}")
        c = st.columns(4)
        c[0].metric("Setup评级", f"{setup['grade']}｜{setup['score']}/100", setup.get("trade_state", ""))
        c[1].metric("未来1分钟", short.get("label_60", "等待"), f"一致度 {int(short.get('condition_agreement',0) or 0)}%")
        c[2].metric("未来2分钟", short.get("label_120", "等待"))
        c[3].metric("VWAP位置", vwap.get("state", "等待"), f"偏离 {float(vwap.get('distance_pct',0) or 0):+.2f}%")
        st.caption(f"当前价 {fmt_num(snapshot.get('price'))}｜今日 {fmt_pct(snapshot.get('pct'))}｜真实L2高置信历史：" + (f"{float(acc):.1f}% / {n}次" if n and acc is not None else "待积累"))
        st.write(f"**持仓/动作：** {action.get('holding_advice','')}")

        st.subheader("② Level-2资金与盘口")
        lm = l2.get("metrics", {}) if l2.get("ok") else {}
        c = st.columns(4)
        if l2.get("ok"):
            c[0].metric("真实主动成交", l2.get("fund_label", "均衡"), f"买 {float(lm.get('active_buy_pct',50) or 50):.0f}%")
            c[1].metric("大/特大单", l2.get("large_label", "均衡"), _money(lm.get("big_net_amount",0)))
            c[2].metric("撤单行为", l2.get("cancel_label", "均衡"))
            c[3].metric("十档盘口", f"买方 {float(lm.get('depth10_buy_pct',50) or 50):.0f}%", f"队列 {float(lm.get('queue_buy_pct',50) or 50):.0f}%")
        else:
            buy = float(metrics.get("buy_pct",50) or 50); book = float(metrics.get("buy_pressure_pct",50) or 50)
            c[0].metric("成交方向（降级）", f"买方 {buy:.0f}%")
            c[1].metric("近60秒净成交", f"{float(metrics.get('net_lots',0) or 0):+,.0f}手")
            c[2].metric("大成交（降级）", f"千手 买{int(metrics.get('thousand_buy',0) or 0)} / 卖{int(metrics.get('thousand_sell',0) or 0)}")
            c[3].metric("五档盘口", f"买方 {book:.0f}%")

        st.subheader("③ 波段与趋势")
        c = st.columns(4)
        c[0].metric("今日动作", f"{action['buy_grade']}｜{action['buy_label']}", f"新仓 {action['new_pos']}")
        c[1].metric("AI起势", action.get("q_state", "数据不足"), f"{float(qishi.get('latest_score',0) or 0):.0f}/100")
        c[2].metric("MACD", macd.get("label", "数据不足"), macd.get("detail", ""))
        c[3].metric("风险", action.get("risk_state", "数据不足"))
        if qishi.get("ok"): plot_qishi(qishi, title=f"{snapshot.get('name',code)} {code}")

        st.subheader("④ 事件催化与基本面")
        c = st.columns(4)
        c[0].metric("行业", industry)
        c[1].metric("事件催化", catalyst.get("label", "无"), f"{catalyst.get('score',0)}/100")
        c[2].metric("事件确定性", event_certainty.get("certainty", "低"))
        c[3].metric("估值", f"PE {fmt_num(snapshot.get('pe_dynamic'))}", f"PB {fmt_num(snapshot.get('pb'))}")
    except Exception as exc:
        st.error("实时刷新失败，但页面不会黑屏；下一周期自动重试。")
        st.caption(f"{type(exc).__name__}: {exc}")


if hasattr(st, "fragment"):
    st.fragment(run_every=refresh_seconds)(_live)()
else:
    st.warning("Streamlit版本过旧，自动刷新已关闭。")
    _live()
