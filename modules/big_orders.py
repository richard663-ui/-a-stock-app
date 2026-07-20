# -*- coding: utf-8 -*-
import re
from typing import Any, Dict

import pandas as pd

from modules.utils import fmt_num, to_float


def parse_amount_token(token: str):
    """解析金额，支持 3000万、0.8亿、12000000。返回元。"""
    if token is None:
        return None
    s = str(token).strip().replace(',', '').replace('，', '')
    if not s:
        return None
    try:
        if s.endswith('亿'):
            return float(s[:-1]) * 1e8
        if s.endswith('万'):
            return float(s[:-1]) * 1e4
        return float(s)
    except Exception:
        return None


def parse_level2_order_lines(text: str, default_price=None) -> pd.DataFrame:
    """
    手动录入格式，允许多种写法：
    买 2000 12.35
    卖 4753 12.20
    B 12000 8.88
    S 3000 金额 3600万

    其中 2000/4753/12000 都是“手”，不是股。
    如果没有价格但写了金额，就直接用金额。
    """
    rows = []
    if not text:
        return pd.DataFrame(columns=['方向','手数','价格','金额','类型','原始行'])
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        parts = re.split(r'[\s,，]+', line)
        direction = None
        hands = None
        price = None
        amount = None
        # 方向
        first = parts[0].lower() if parts else ''
        if first in ['买','b','buy','主动买','买入']:
            direction = '买入'
            parts = parts[1:]
        elif first in ['卖','s','sell','主动卖','卖出']:
            direction = '卖出'
            parts = parts[1:]
        else:
            # 尝试从行内找
            if '买' in line or 'B' in line:
                direction = '买入'
            elif '卖' in line or 'S' in line:
                direction = '卖出'
        # 扫描数字
        nums = []
        for t in parts:
            tclean = t.replace('手','').replace('股','').replace('元','')
            if '金额' in tclean:
                continue
            amt = parse_amount_token(tclean)
            if amt is not None:
                nums.append((t, amt))
        # 第一个通常是手数，第二个通常是价格；如果含万/亿，认为是金额
        for t, v in nums:
            if hands is None and ('万' not in t and '亿' not in t) and v >= 100:
                hands = int(v)
            elif amount is None and ('万' in t or '亿' in t):
                amount = v
            elif price is None and v < 1000:
                price = float(v)
            elif amount is None and v >= 1000000:
                amount = v
        # 如果有“金额 xxxx”特殊写法
        m = re.search(r'金额\s*([0-9\.]+\s*[万亿]?)', line)
        if m:
            amount = parse_amount_token(m.group(1).replace(' ', ''))
        if price is None and default_price:
            price = float(default_price)
        if hands is not None and amount is None and price is not None:
            amount = hands * 100 * price
        if direction and hands is not None:
            typ = '万手单' if hands >= 10000 else ('千手单' if hands >= 1000 else '普通单')
            rows.append({
                '方向': direction,
                '手数': hands,
                '价格': price,
                '金额': amount,
                '类型': typ,
                '原始行': line,
            })
    return pd.DataFrame(rows)


def summarize_big_orders(order_df: pd.DataFrame) -> Dict[str, Any]:
    if order_df is None or order_df.empty:
        return {
            'has_detail': False,
            'score': 50,
            'label': '未录入大单明细',
            'reasons': ['未录入具体千手/万手单明细。'],
            'summary': {},
            'df': pd.DataFrame(),
        }
    df = order_df.copy()
    df['金额'] = pd.to_numeric(df['金额'], errors='coerce').fillna(0.0)
    df['手数'] = pd.to_numeric(df['手数'], errors='coerce').fillna(0).astype(int)
    thousand = df[df['手数'] >= 1000]
    wan = df[df['手数'] >= 10000]
    def calc(sub):
        buy = sub[sub['方向'] == '买入']
        sell = sub[sub['方向'] == '卖出']
        return {
            '买入笔数': int(len(buy)),
            '卖出笔数': int(len(sell)),
            '买入金额': float(buy['金额'].sum()),
            '卖出金额': float(sell['金额'].sum()),
            '净流入': float(buy['金额'].sum() - sell['金额'].sum()),
            '最大单手数': int(sub['手数'].max()) if len(sub) else 0,
            '最大单金额': float(sub['金额'].max()) if len(sub) else 0.0,
        }
    ts = calc(thousand)
    ws = calc(wan)
    total_net = ts['净流入'] + ws['净流入'] * 0.5  # 万手单额外强化，但避免完全重复
    score = 50
    reasons = []
    if ts['净流入'] > 0:
        score += 12; reasons.append(f"千手单净流入 {fmt_num(ts['净流入'])}")
    elif ts['净流入'] < 0:
        score -= 14; reasons.append(f"千手单净流出 {fmt_num(abs(ts['净流入']))}")
    if ws['买入笔数'] > 0 or ws['卖出笔数'] > 0:
        if ws['净流入'] > 0:
            score += 18; reasons.append(f"万手单净流入 {fmt_num(ws['净流入'])}")
        elif ws['净流入'] < 0:
            score -= 22; reasons.append(f"万手单净流出 {fmt_num(abs(ws['净流入']))}")
    if ts['最大单手数'] >= 5000:
        score += 5; reasons.append(f"出现较大千手单：最大 {ts['最大单手数']} 手")
    if ws['最大单手数'] >= 10000:
        score += 8; reasons.append(f"出现万手级大单：最大 {ws['最大单手数']} 手")
    score = max(0, min(100, score))
    if score >= 72:
        label = '具体大单强流入'
    elif score >= 58:
        label = '具体大单偏流入'
    elif score >= 43:
        label = '具体大单中性'
    else:
        label = '具体大单偏流出'
    return {
        'has_detail': True,
        'score': score,
        'label': label,
        'reasons': reasons or ['大单明细方向不明显。'],
        'summary': {'千手单': ts, '万手单': ws},
        'df': df.sort_values(['手数','金额'], ascending=False),
    }


def order_readable_row(row: pd.Series) -> Dict[str, Any]:
    """把一笔大单翻译成人话：手数、股数、金额、类型。"""
    hands = int(row.get('手数', 0) or 0)
    shares = hands * 100
    price = to_float(row.get('价格'))
    amount = to_float(row.get('金额')) or 0.0
    direction = row.get('方向', '')
    typ = '万手单/超大单' if hands >= 10000 else ('千手单/大单' if hands >= 1000 else '普通单')
    if hands >= 10000:
        meaning = '超大单，通常代表非常大的盘中资金动作，需要重点看方向。'
    elif hands >= 1000:
        meaning = '大单，说明有较大资金参与，但要结合买卖方向和股价位置。'
    else:
        meaning = '普通成交，不作为千手/万手核心判断。'
    return {
        '方向': direction,
        '手数': hands,
        '股数': shares,
        '价格': price,
        '金额': amount,
        '类型': typ,
        '人话解释': f"{direction}{hands}手 = {shares:,}股，约{fmt_num(amount)}。{meaning}",
        '原始行': row.get('原始行',''),
    }


def build_big_order_explanation(detail_l2: Dict[str, Any], amount_total: float = None) -> Dict[str, Any]:
    """生成普通人能看懂的大单解释。"""
    if not detail_l2.get('has_detail'):
        return {
            'summary_text': '未录入逐笔千手/万手单，无法计算具体大单金额。',
            'plain_reasons': ['现在只能用手动选择的 Level-2 方向做粗略判断。'],
            'quality_label': '无明细',
            'big_net_ratio': None,
            'readable_df': pd.DataFrame(),
        }
    summary = detail_l2.get('summary', {})
    ts = summary.get('千手单', {})
    ws = summary.get('万手单', {})
    ts_net = ts.get('净流入', 0.0)
    ws_net = ws.get('净流入', 0.0)
    buy_amt = ts.get('买入金额',0.0) + ws.get('买入金额',0.0)
    sell_amt = ts.get('卖出金额',0.0) + ws.get('卖出金额',0.0)
    net = buy_amt - sell_amt
    ratio = None
    if amount_total and amount_total > 0:
        ratio = net / amount_total * 100

    if net > 0 and ws_net > 0:
        quality = '大资金明显偏买入'
        summary_text = f"千手/万手合计净流入 {fmt_num(net)}，且万手单也偏买入，说明大资金有较强承接。"
    elif net > 0:
        quality = '大资金偏买入'
        summary_text = f"千手/万手合计净流入 {fmt_num(net)}，买方大单多于卖方大单。"
    elif net < 0 and ws_net < 0:
        quality = '大资金明显偏卖出'
        summary_text = f"千手/万手合计净流出 {fmt_num(abs(net))}，且万手单偏卖出，要警惕大资金砸盘或出货。"
    elif net < 0:
        quality = '大资金偏卖出'
        summary_text = f"千手/万手合计净流出 {fmt_num(abs(net))}，卖方大单压力更大。"
    else:
        quality = '大资金多空均衡'
        summary_text = '千手/万手买卖金额接近，多空暂时没有明显方向。'

    reasons = [
        f"千手单：买入 {ts.get('买入笔数',0)} 笔 / {fmt_num(ts.get('买入金额',0))}，卖出 {ts.get('卖出笔数',0)} 笔 / {fmt_num(ts.get('卖出金额',0))}，净额 {fmt_num(ts_net)}。",
        f"万手单：买入 {ws.get('买入笔数',0)} 笔 / {fmt_num(ws.get('买入金额',0))}，卖出 {ws.get('卖出笔数',0)} 笔 / {fmt_num(ws.get('卖出金额',0))}，净额 {fmt_num(ws_net)}。",
    ]
    if ratio is not None:
        reasons.append(f"大单净额约占当前成交额 {ratio:.2f}%。这个比例越高，大单对盘面的影响越明显。")
    if ws.get('最大单手数',0) >= 10000:
        reasons.append(f"最大万手单 {ws.get('最大单手数',0)} 手，约 {fmt_num(ws.get('最大单金额',0))}，属于需要重点观察的超大单。")
    elif ts.get('最大单手数',0) >= 1000:
        reasons.append(f"最大千手单 {ts.get('最大单手数',0)} 手，约 {fmt_num(ts.get('最大单金额',0))}。")

    rdf = pd.DataFrame()
    if not detail_l2.get('df', pd.DataFrame()).empty:
        rdf = pd.DataFrame([order_readable_row(r) for _, r in detail_l2['df'].iterrows()])

    return {
        'summary_text': summary_text,
        'plain_reasons': reasons,
        'quality_label': quality,
        'big_net_ratio': ratio,
        'readable_df': rdf,
    }


def score_manual_l2(big_order: str, thousand: str, ten_thousand: str, book: str, tape: str) -> Dict[str, Any]:
    score = 50
    reasons = []
    if big_order == "大单流入": score += 15; reasons.append("大单方向偏流入")
    if big_order == "大单流出": score -= 18; reasons.append("大单方向偏流出")
    if thousand == "千手买入强": score += 12; reasons.append("千手单买入强")
    if thousand == "千手卖出强": score -= 14; reasons.append("千手单卖出强")
    if ten_thousand == "万手买入强": score += 18; reasons.append("万手单买入强")
    if ten_thousand == "万手卖出强": score -= 22; reasons.append("万手单卖出强")
    if book == "承接强": score += 10; reasons.append("盘口承接强")
    if book == "承接弱": score -= 12; reasons.append("盘口承接弱")
    if tape == "主动买多": score += 10; reasons.append("逐笔成交主动买多")
    if tape == "主动卖多": score -= 12; reasons.append("逐笔成交主动卖多")
    score = max(0, min(100, score))
    if score >= 70:
        label = "Level-2强确认"
    elif score >= 56:
        label = "Level-2偏正面"
    elif score >= 44:
        label = "Level-2中性"
    else:
        label = "Level-2偏负面"
    return {"score": score, "label": label, "reasons": reasons or ["未输入明显Level-2方向"]}


def combine_l2_scores(manual_l2: Dict[str, Any], detail_l2: Dict[str, Any]) -> Dict[str, Any]:
    """把下拉判断和具体千手/万手单明细合并。明细优先。"""
    if detail_l2.get('has_detail'):
        score = int(detail_l2['score'] * 0.72 + manual_l2['score'] * 0.28)
        if score >= 72:
            label = '大资金强确认'
        elif score >= 58:
            label = '大资金偏正面'
        elif score >= 43:
            label = '大资金中性'
        else:
            label = '大资金偏负面'
        return {
            'score': score,
            'label': label,
            'reasons': detail_l2['reasons'] + manual_l2['reasons'],
            'detail': detail_l2,
            'manual': manual_l2,
        }
    manual_l2['detail'] = detail_l2
    manual_l2['manual'] = manual_l2.copy()
    return manual_l2

