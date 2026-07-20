# -*- coding: utf-8 -*-
"""

A股智能盯盘 V16.10.1 快照稳定修正版
核心目标：少废话，只看 大资金/千手万手手动确认、AI起势柱、事件催化、买卖动作。

说明：
1. 这不是自动交易程序，不构成投资建议。
2. 没有接入正式 Level-2 API 时，资金柱是“量价代理资金”，不等同于真实主力净流入。
3. 左侧 Level-2 手动输入用于把你爸在东方财富/券商 Level-2 看到的千手单、万手单、盘口承接纳入判断。

"""

import pandas as pd
import streamlit as st

# =========================
# 页面设置
# =========================
st.set_page_config(page_title="A股盯盘 V16.10.1", layout="wide")
st.title("A股盯盘 V16.10.1")
st.caption("爸爸版：快照多源兜底｜大单人话解释｜事件雷达｜AI起势柱｜买卖动作")

from modules.big_orders import build_big_order_explanation, combine_l2_scores, parse_level2_order_lines, score_manual_l2, summarize_big_orders
from modules.data_sources import fetch_em_snapshot, fetch_tencent_kline
from modules.event_radar import FALLBACK_INDUSTRY, analyze_event_catalyst, auto_event_radar, build_event_direction_table, event_certainty_grade, scan_event_pool, detect_industry_concepts
from modules.qishi import analyze_qishi
from modules.signals import make_action
from modules.ui_blocks import plot_qishi
from modules.utils import fmt_num, fmt_pct, normalize_code


with st.sidebar:
    st.header("输入")
    mode = st.radio("模式", ["单股盯盘", "事件催化扫描", "代码说明"], index=0)
    stock_code = st.text_input("股票代码", value="000400")
    st.markdown("### 持仓")
    has_position = st.checkbox("已有仓位", value=False)
    cost = st.number_input("成本价", min_value=0.0, value=0.0, step=0.01)
    position_pct = st.number_input("持仓比例%", min_value=0.0, max_value=100.0, value=0.0, step=5.0)

    st.markdown("### Level-2 手动观察")
    big_order = st.selectbox("大单方向", ["中性", "大单流入", "大单流出"])
    thousand = st.selectbox("千手单", ["无明显", "千手买入强", "千手卖出强"])
    ten_thousand = st.selectbox("万手单", ["无明显", "万手买入强", "万手卖出强"])
    book = st.selectbox("盘口承接", ["一般", "承接强", "承接弱"])
    tape = st.selectbox("逐笔方向", ["中性", "主动买多", "主动卖多"])
    st.caption("填每一笔千手/万手单。格式：买 2000 12.35；卖 4753 12.20；B 12000 8.88。2000手=20万股。")
    level2_detail_text = st.text_area("千手/万手单明细", value="", height=120)

    st.markdown("### 事件催化")
    auto_event = st.checkbox("自动抓取全球/中国事件线索", value=True)
    event_text = st.text_area("手动补充事件/新闻关键词", value="NVIDIA 发布会 AI服务器 CPO 液冷 机器人", height=100)


# =========================
# 主页面：单股盯盘
# =========================
if mode == "单股盯盘":
    code = normalize_code(stock_code)
    snapshots = fetch_em_snapshot((code,))
    snapshot = snapshots.get(code, {})

    df = fetch_tencent_kline(code, 260)
    if df.empty:
        st.error("K线获取失败。当前网络源不可用，无法生成起势柱。")
        st.stop()

    # 快照如果全源失败，至少用K线最新收盘价兜底，避免整个页面死掉。
    # 注意：这种情况不是实时快照，PE/PB/换手率/量比会缺失。
    if not snapshot:
        latest = df.iloc[-1]
        snapshot = {
            "code": code,
            "name": FALLBACK_INDUSTRY.get(code, (code, []))[0] if code in FALLBACK_INDUSTRY else code,
            "price": float(latest.get("close")),
            "pct": float(latest.get("pct_change")) if pd.notna(latest.get("pct_change")) else None,
            "amount": None, "turnover": None, "volume_ratio": None,
            "pe_dynamic": None, "pb": None, "market_cap": None, "float_cap": None,
            "source": "K线兜底：非实时快照",
        }
        st.warning("实时快照源暂时不可用，本次使用K线最新价兜底；PE/PB、换手率、量比可能缺失。")

    qishi = analyze_qishi(df)
    industry, concepts, industry_src = detect_industry_concepts(code, snapshot)
    auto_radar = auto_event_radar(code, snapshot, industry, concepts) if auto_event else {'score': 0, 'label': '未启用自动事件雷达', 'news': [], 'queries': [], 'reasons': []}
    auto_titles = " ".join([n.get('title','') for n in auto_radar.get('news', [])])
    combined_event_text = (event_text or '') + " " + auto_titles
    catalyst = analyze_event_catalyst(code, combined_event_text, concepts)
    # 自动事件雷达作为催化剂补充，不直接强行买入
    catalyst['score'] = min(100, max(catalyst['score'], int(catalyst['score'] * 0.75 + auto_radar.get('score',0) * 0.45)))
    if catalyst['score'] >= 70:
        catalyst['label'] = '强催化'
    elif catalyst['score'] >= 45:
        catalyst['label'] = '中等催化'
    elif catalyst['score'] >= 20:
        catalyst['label'] = '弱催化'
    else:
        catalyst['label'] = '暂无有效催化'
    manual_l2 = score_manual_l2(big_order, thousand, ten_thousand, book, tape)
    order_df = parse_level2_order_lines(level2_detail_text, default_price=snapshot.get('price'))
    detail_l2 = summarize_big_orders(order_df)
    big_order_exp = build_big_order_explanation(detail_l2, snapshot.get('amount'))
    l2 = combine_l2_scores(manual_l2, detail_l2)
    event_certainty = event_certainty_grade(combined_event_text, auto_radar.get('news', []), catalyst)
    action = make_action(snapshot, qishi, catalyst, l2, has_position, cost, position_pct)

    # 第一屏：只给结论
    st.markdown("## 一、今日盯盘结论")
    st.info(f"""
**{snapshot.get('name', code)}（{code}）**  当前价：**{fmt_num(snapshot.get('price'))}**  今日涨跌幅：**{fmt_pct(snapshot.get('pct'))}**  
**趋势等级：** {action['trend_grade']}  
**当前买点：** {action['buy_grade']}：{action['buy_label']}，建议新仓 **{action['new_pos']}**  
**已有仓位：** {action['holding_advice']}  
**AI起势：** {action['q_state']}，连续红系 **{qishi.get('consecutive_red', 0)} 天**  
**大资金/盘口：** {l2['label']}｜{big_order_exp['quality_label']}  
**事件催化：** {catalyst['label']}｜{event_certainty['label']}｜{event_certainty['impact_type']}  
**风险状态：** {action['risk_state']}

**一句话：** {big_order_exp['summary_text']}
""")

    if action["pnl_text"]:
        st.write(f"**持仓盈亏：** {action['pnl_text']}")

    st.markdown("### 三条核心原因")
    st.write(f"- **起势柱：** {action['q_state']}，红系连续 {qishi.get('consecutive_red', 0)} 天。")
    st.write(f"- **大单：** {big_order_exp['summary_text']}")
    st.write(f"- **事件：** {event_certainty['label']}，{event_certainty['impact_type']}。{event_certainty['reason']}")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### 买点 / 加仓条件")
        for x in action["buy_zone"]:
            st.write(f"- {x}")
        for x in action["upgrade"]:
            st.write(f"- {x}")
    with col2:
        st.markdown("### 卖点 / 风控条件")
        for x in action["sell_zone"]:
            st.write(f"- {x}")
        for x in action["downgrade"]:
            st.write(f"- {x}")

    if action["reasons"]:
        st.markdown("### 为什么不是更高等级")
        for r in action["reasons"]:
            st.write(f"- {r}")

    st.markdown("## 二、大资金与Level-2确认")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("大资金评分", f"{l2['score']}/100")
    c2.metric("大资金状态", l2['label'])
    c3.metric("千手单", thousand)
    c4.metric("万手单", ten_thousand)

    detail = l2.get('detail', {})
    if detail.get('has_detail'):
        st.markdown("### 开盘至今千手/万手单统计")
        ts = detail['summary']['千手单']
        ws = detail['summary']['万手单']
        sum_df = pd.DataFrame([
            {'类型': '千手单>=1000手', **ts},
            {'类型': '万手单>=10000手', **ws},
        ])
        sum_show = sum_df.copy()
        for col in ['买入金额','卖出金额','净流入','最大单金额']:
            sum_show[col] = sum_show[col].apply(fmt_num)
        st.dataframe(sum_show, use_container_width=True)
        st.markdown("### 大单人话解释")
        for x in big_order_exp['plain_reasons']:
            st.write(f"- {x}")
        st.markdown("### 每一笔大单明细")
        detail_show = big_order_exp['readable_df'].copy()
        if not detail_show.empty:
            detail_show['金额'] = detail_show['金额'].apply(fmt_num)
            detail_show['价格'] = detail_show['价格'].apply(lambda x: '--' if pd.isna(x) else f'{float(x):.2f}')
            detail_show['股数'] = detail_show['股数'].apply(lambda x: f"{int(x):,}")
            st.dataframe(detail_show[['方向','手数','股数','价格','金额','类型','人话解释','原始行']], use_container_width=True)
    else:
        st.caption("未录入具体千手/万手单明细。现在使用下拉的Level-2方向判断。")

    for r in l2["reasons"][:8]:
        st.write(f"- {r}")

    st.markdown("## 三、AI起势柱")
    if qishi.get("ok"):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("起势分", f"{qishi['latest_score']:.1f}")
        c2.metric("资金/量能分", f"{qishi['fund_score']:.1f}")
        c3.metric("红系连续", f"{qishi['consecutive_red']}天")
        c4.metric("追高风险", qishi["risk_state"])
        for r in qishi["reasons"]:
            st.write(f"- {r}")
        plot_qishi(qishi, title=f"{snapshot.get('name', code)} {code}")

    st.markdown("## 四、事件催化 / 全球事件雷达")
    st.write(f"- 行业：**{industry}**（来源：{industry_src}）")
    st.write(f"- 概念：**{', '.join(concepts) if concepts else '暂无'}**")
    st.write(f"- 催化剂分：**{catalyst['score']}/100**，状态：**{catalyst['label']}**")
    st.write(f"- 事件确定性：**{event_certainty['label']}**｜确定性：**{event_certainty['certainty']}**｜影响类型：**{event_certainty['impact_type']}**")
    st.write(f"- 自动事件雷达：**{auto_radar.get('label')}**")
    for r in (catalyst["reasons"] + auto_radar.get('reasons', []))[:8]:
        st.write(f"- {r}")
    direction_table = build_event_direction_table(combined_event_text)
    if not direction_table.empty:
        st.markdown("### 事件 → A股方向映射")
        st.dataframe(direction_table, use_container_width=True)
    if auto_radar.get('news'):
        st.markdown("### 自动抓取到的相关事件线索")
        news_rows = []
        for n in auto_radar['news'][:10]:
            news_rows.append({
                '标题': n.get('title'),
                '来源': n.get('source'),
                '时间': n.get('pubDate'),
                '查询词': n.get('query'),
                '链接': n.get('link'),
            })
        st.dataframe(pd.DataFrame(news_rows), use_container_width=True)
    else:
        st.caption("未自动抓到明显相关事件。可以在左侧手动补充 NVIDIA、政策、订单、业绩、机器人等关键词。")

    with st.expander("基础数据"):
        st.write(f"PE动态：{fmt_num(snapshot.get('pe_dynamic'))}")
        st.write(f"PB：{fmt_num(snapshot.get('pb'))}")
        st.write(f"换手率：{fmt_pct(snapshot.get('turnover'))}")
        st.write(f"量比：{fmt_num(snapshot.get('volume_ratio'))}")
        st.write(f"成交额：{fmt_num(snapshot.get('amount'))}")
        st.write(f"总市值：{fmt_num(snapshot.get('market_cap'))}")
        st.write(f"数据源：{snapshot.get('source')}")


# =========================
# 主页面：事件扫描
# =========================
elif mode == "事件催化扫描":
    st.markdown("## 事件催化扫描")
    st.write("根据事件关键词映射 A 股方向，再按 AI起势分、资金量能分、催化分排序。")
    direction_table = build_event_direction_table(event_text)
    if not direction_table.empty:
        st.markdown("### 先看事件对应哪些A股方向")
        st.dataframe(direction_table, use_container_width=True)
    max_n = st.slider("最多扫描股票数", 10, 80, 40, step=10)
    if st.button("开始扫描"):
        with st.spinner("正在扫描事件相关股票..."):
            res = scan_event_pool(event_text, max_n=max_n)
        if res.empty:
            st.warning("没有匹配到事件股票池。请在左侧输入更明确的事件关键词，例如 NVIDIA、CPO、液冷、机器人、半导体。")
        else:
            st.dataframe(res, use_container_width=True)
            st.download_button(
                "下载CSV",
                data=res.to_csv(index=False, encoding="utf-8-sig"),
                file_name="event_scan.csv",
                mime="text/csv",
            )


# =========================
# 代码说明
# =========================
else:
    st.markdown("## 代码含义说明")
    st.markdown("""
### 1. 数据层
- `fetch_em_snapshot()`：从东方财富批量快照拿现价、涨跌幅、PE、PB、换手率、量比、市值。
- `fetch_tencent_kline()`：从腾讯拿日 K 线，用来画K线、均线和AI起势柱。
- `detect_industry_concepts()`：识别行业和概念，优先用内置核心映射和 BaoStock 行业库。

### 2. AI起势柱
- `analyze_qishi()`：计算 AI 起势追踪。
- 起势柱不等于买入命令，它只说明趋势有没有启动。
- 起势强度主要来自：均线结构、量能资金、突破、动量、连续性。
- 追高风险单独计算，不会把红柱压没。

### 3. 资金柱
- 当前资金柱是“量价代理资金”，不是 Level-2 真实主力资金。
- 它根据放量上涨、缩量整理、放量下跌、冲高回落来判断资金动作。
- 如果以后接入 QMT/PTrade/Choice Level-2，可以把这里替换成真实千手单、万手单、逐笔成交。

### 4. Level-2手动输入 / 大单明细
- `score_manual_l2()`：把你爸在东方财富或券商 Level-2 看到的大单、千手单、万手单、盘口承接手动录入。
- 这个分数会影响买入等级：大单流入可以升级，大单流出会降级。

### 5. 事件催化
- `analyze_event_catalyst()`：把 NVIDIA、机器人、半导体、电网等事件关键词映射到 A 股产业链。
- 它不会直接推荐买入，只是告诉系统这只股票有没有事件支持。

### 6. 买卖动作
- `make_action()`：综合 AI起势、资金柱、事件催化、Level-2手动确认、持仓成本，给出：
  - 趋势等级
  - 当前买点 A/B/C/D
  - 建议新仓比例
  - 持仓建议
  - 买点、卖点、升级/降级条件

### 7. 为什么这个版本更适合你爸
- 首页不放长篇研报，只放：大资金、千手万手、AI起势柱、事件催化、买卖动作。
- PE/PB、行业、概念这些仍然在后台用，但不喧宾夺主。
""")
