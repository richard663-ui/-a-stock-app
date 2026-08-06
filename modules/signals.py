# -*- coding: utf-8 -*-
import pandas as pd


def make_action(snapshot, qishi, catalyst, l2, has_position, cost, position_pct):
    price = snapshot.get("price")
    q_score = qishi["latest_score"] if qishi.get("ok") else 0
    q_state = qishi["latest_state"] if qishi.get("ok") else "数据不足"
    f_score = qishi["fund_score"] if qishi.get("ok") else 0
    red_days = qishi.get("consecutive_red", 0)
    risk_state = qishi.get("risk_state", "数据不足")
    cat_score = catalyst.get("score", 0)
    flow_score = float(l2.get("score", 50) or 50)

    if q_score >= 85:
        trend_grade = "A趋势：强趋势加速"
    elif q_score >= 70:
        trend_grade = "B趋势：起势延续"
    elif q_score >= 45:
        trend_grade = "C趋势：观察修复"
    else:
        trend_grade = "D趋势：未启动"

    buy_grade = "C"
    buy_label = "只观察"
    new_pos = "0%"
    reasons_not_high = []

    hard_bad = False
    if flow_score < 35:
        hard_bad = True
        reasons_not_high.append("实时资金确认偏负面")
    if qishi.get("ok") and qishi.get("fund_label") == "放量下跌风险":
        hard_bad = True
        reasons_not_high.append("资金柱出现放量下跌风险")

    strong_but_high = q_score >= 75 and risk_state in ["中等风险", "高位风险"] and red_days >= 4
    flow_positive = flow_score >= 56
    flow_strong = flow_score >= 70
    catalyst_ok = cat_score >= 35

    if hard_bad:
        buy_grade, buy_label, new_pos = "D", "不建议买入", "0%"
    elif q_score >= 82 and f_score >= 58 and flow_strong and catalyst_ok and risk_state == "风险可控" and red_days <= 3:
        buy_grade, buy_label, new_pos = "A", "可分批买入", "20%-40%"
    elif q_score >= 62 and f_score >= 45 and (flow_positive or catalyst_ok) and not strong_but_high:
        buy_grade, buy_label, new_pos = "B", "可小仓试探", "10%-20%"
    elif q_score >= 75 and strong_but_high:
        buy_grade, buy_label, new_pos = "C", "强趋势但不追高", "0%"
        reasons_not_high.append("位置偏高，新仓等回踩")
    elif q_score >= 40:
        buy_grade, buy_label, new_pos = "C", "只观察", "0%"
        reasons_not_high.append("起势或资金确认不足")
    else:
        buy_grade, buy_label, new_pos = "D", "未启动不买", "0%"
        reasons_not_high.append("AI起势柱未启动")

    qdf = qishi.get("df", pd.DataFrame())
    if qdf is not None and not qdf.empty:
        last = qdf.iloc[-1]
        ma5, ma10, ma20 = last.get("MA5"), last.get("MA10"), last.get("MA20")
        low20 = last.get("LOW20")
        high20 = last.get("HIGH20_PREV")
    else:
        ma5 = ma10 = ma20 = low20 = high20 = None

    buy_zone = []
    sell_zone = []
    if ma5 and ma10:
        buy_zone.append(f"强势回踩：MA5/MA10 附近 {ma5:.2f} / {ma10:.2f}")
    if ma20:
        buy_zone.append(f"趋势防守：MA20 附近 {ma20:.2f}")
    if high20:
        buy_zone.append(f"突破观察：站稳20日平台 {high20:.2f}")
    if ma10:
        sell_zone.append(f"减仓观察：起势转弱且跌破MA10 {ma10:.2f}")
    if ma20:
        sell_zone.append(f"强风控：跌破MA20 {ma20:.2f}")
    if low20:
        sell_zone.append(f"破位风险：跌破20日低点 {low20:.2f}")

    holding_advice = "未输入持仓，按新开仓逻辑处理。"
    pnl_text = ""
    if has_position and price and cost and cost > 0:
        pnl = (price - cost) / cost * 100
        pnl_text = f"当前浮盈/浮亏：{pnl:.2f}%"
        if buy_grade in ["A", "B"] and price >= cost * 0.98:
            holding_advice = "已有仓位可继续持有；回踩MA5/MA10不破可小幅加仓，避免一次打满。"
        elif q_score >= 70:
            holding_advice = "已有仓位以持有为主；新仓不追高，资金转弱或跌破风控位再减。"
        elif hard_bad:
            holding_advice = "已有仓位注意风险；若同时跌破MA20，应减仓或执行强风控。"
        else:
            holding_advice = "已有仓位暂持观察；等资金与起势确认后再考虑加仓。"

    return {
        "trend_grade": trend_grade,
        "buy_grade": buy_grade,
        "buy_label": buy_label,
        "new_pos": new_pos,
        "reasons": reasons_not_high,
        "buy_zone": buy_zone,
        "sell_zone": sell_zone,
        "holding_advice": holding_advice,
        "pnl_text": pnl_text,
        "upgrade": [
            "升级到B：起势转强 + 资金确认不流出",
            "升级到A：强趋势早期 + 资金强确认 + 有事件催化 + 位置不过热",
        ],
        "downgrade": [
            "降级到D：放量下跌 / 大成交持续卖出 / 跌破MA20",
            "减仓信号：起势转弱 + 跌破MA10，或资金确认转负",
        ],
        "q_state": q_state,
        "risk_state": risk_state,
    }
