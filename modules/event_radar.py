# -*- coding: utf-8 -*-
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

from modules.data_sources import fetch_em_snapshot, fetch_tencent_kline, load_baostock_industry
from modules.qishi import analyze_qishi
from modules.utils import normalize_code, safe_get


FALLBACK_INDUSTRY = {
    "000400": ("电网设备", ["特高压", "智能电网", "虚拟电厂"]),
    "002156": ("半导体", ["先进封装", "Chiplet", "国产芯片", "集成电路"]),
    "300276": ("专用设备", ["机器人", "智能物流", "工业4.0"]),
    "300308": ("通信设备", ["光模块", "CPO", "算力"]),
    "300502": ("通信设备", ["光模块", "CPO", "算力"]),
    "300394": ("通信设备", ["光模块", "CPO", "算力"]),
    "603083": ("通信设备", ["光模块", "CPO"]),
    "600519": ("酿酒行业", ["白酒"]),
    "300750": ("电池", ["动力电池", "新能源车", "储能"]),
    "002594": ("汽车整车", ["新能源车", "智能驾驶", "动力电池"]),
    "600036": ("银行", ["金融", "大盘蓝筹"]),
    "603773": ("光学光电子", ["玻璃基板", "先进封装", "光电材料"]),
    "301217": ("光伏设备", ["光伏", "新能源"]),
}


INDUSTRY_CONCEPT_BY_KEYWORD = [
    ("半导体", ["集成电路", "国产芯片", "先进封装"]),
    ("通信", ["光模块", "CPO", "算力"]),
    ("电网", ["特高压", "智能电网", "虚拟电厂"]),
    ("电池", ["动力电池", "新能源车", "储能"]),
    ("机器人", ["机器人", "工业母机", "智能制造"]),
    ("软件", ["人工智能", "信创", "数字经济"]),
    ("银行", ["金融", "大盘蓝筹"]),
    ("证券", ["券商", "金融"]),
    ("医药", ["创新药", "医疗", "生物医药"]),
    ("白酒", ["白酒", "消费"]),
]


EVENT_THEMES = {
    "NVIDIA / AI服务器": {
        "keywords": ["nvidia", "英伟达", "AI服务器", "GPU", "算力", "AI factory", "Rubin", "Blackwell", "NVLink"],
        "directions": {
            "光模块/CPO": ["300308", "300502", "300394", "603083"],
            "高速PCB": ["002463", "300476", "688183", "600183"],
            "液冷": ["002837", "300499", "301018", "002472"],
            "机器人/具身智能": ["002050", "688017", "300024", "002747", "300276"],
            "先进封装/玻璃基板": ["002156", "603773", "688072"],
        },
    },
    "机器人": {
        "keywords": ["机器人", "具身智能", "humanoid", "Jetson", "Isaac", "Unitree"],
        "directions": {
            "机器人本体/零部件": ["002050", "688017", "300024", "002747", "300276", "002472"],
            "传感器/机器视觉": ["688322", "688025", "300567"],
        },
    },
    "电网/电力设备": {
        "keywords": ["特高压", "智能电网", "虚拟电厂", "电改", "电力设备", "电网投资"],
        "directions": {
            "电网设备": ["000400", "600312", "600406", "601179", "002028"],
        },
    },
    "半导体国产替代": {
        "keywords": ["半导体", "国产芯片", "先进封装", "Chiplet", "HBM", "存储"],
        "directions": {
            "半导体": ["002156", "688981", "603501", "300661", "688041"],
            "存储": ["301308", "688525", "300604"],
        },
    },
}


def detect_industry_concepts(code: str, snapshot: Dict[str, Any]) -> Tuple[str, List[str], str]:
    code = normalize_code(code)
    if code in FALLBACK_INDUSTRY:
        industry, concepts = FALLBACK_INDUSTRY[code]
        return industry, concepts, "内置核心映射"

    df = load_baostock_industry()
    if df is not None and not df.empty and "code" in df.columns:
        hit = df[df["code"].astype(str).str.zfill(6) == code]
        if not hit.empty:
            row = hit.iloc[-1]
            industry = str(row.get("industry") or row.get("industryClassification") or "行业未识别")
            concepts = []
            for key, cs in INDUSTRY_CONCEPT_BY_KEYWORD:
                if key in industry:
                    concepts = cs
                    break
            return industry, concepts, "BaoStock行业库"

    name = str(snapshot.get("name") or "")
    for key, cs in INDUSTRY_CONCEPT_BY_KEYWORD:
        if key in name:
            return key, cs, "名称关键词兜底"
    return "行业未识别", [], "未识别"


def analyze_event_catalyst(code: str, event_text: str, concepts: List[str]) -> Dict[str, Any]:
    code = normalize_code(code)
    text = (event_text or "").lower()
    score = 0
    matched = []
    related_pool = []
    reasons = []

    for theme_name, cfg in EVENT_THEMES.items():
        kws = cfg["keywords"]
        theme_hit = any(k.lower() in text for k in kws)
        concept_hit = any(any(k.lower() in str(c).lower() for k in kws) for c in concepts)
        code_hit = any(code in stocks for stocks in cfg["directions"].values())
        if theme_hit or concept_hit or code_hit:
            matched.append(theme_name)
            add = 20 if theme_hit else 10
            if code_hit:
                add += 20
            score += add
            for direction, stocks in cfg["directions"].items():
                related_pool.extend(stocks)
                if code in stocks:
                    reasons.append(f"个股命中事件方向：{theme_name} / {direction}")
            if theme_hit:
                reasons.append(f"事件文字命中：{theme_name}")

    if concepts:
        score += min(15, len(concepts) * 4)
        reasons.append(f"概念标签：{', '.join(concepts[:4])}")

    score = min(100, score)
    if score >= 70:
        label = "强催化"
    elif score >= 45:
        label = "中等催化"
    elif score >= 20:
        label = "弱催化"
    else:
        label = "暂无有效催化"

    return {
        "score": score,
        "label": label,
        "matched": matched,
        "related_pool": sorted(set(related_pool)),
        "reasons": reasons or ["未发现明确事件催化。"],
    }


def event_certainty_grade(event_text: str, news: List[Dict[str, str]], catalyst: Dict[str, Any]) -> Dict[str, Any]:
    """把事件催化分成确定性等级：公告/官方事件/权威新闻/普通新闻/线索。"""
    text = (event_text or '') + ' ' + ' '.join([str(n.get('title','')) + ' ' + str(n.get('source','')) for n in news or []])
    low = text.lower()
    level = 5
    label = '五级：概念线索/联想'
    certainty = '低'
    reason = '主要是概念或关键词相关，暂未看到明确公告或权威事件。'

    strong_company = ['公告','业绩预增','业绩快报','回购','增持','中标','重大合同','订单','重组','并购','定增','股权激励']
    official_event = ['nvidia','英伟达','gtc','computex','rubin','blackwell','ai factory','政策','国务院','工信部','发改委','央行','证监会']
    authoritative = ['reuters','bloomberg','财联社','新华社','证券时报','上海证券报','中国证券报','eastmoney','东方财富','同花顺']

    if any(k.lower() in low for k in strong_company):
        level = 1; label = '一级：公司公告/硬催化'; certainty = '高'; reason = '包含公告、业绩、订单、回购、增持、重大合同等硬催化关键词。'
    elif any(k.lower() in low for k in official_event):
        level = 2; label = '二级：官方/海外龙头产业事件'; certainty = '中高'; reason = '包含 NVIDIA、政策、产业发布会等行业级事件，通常属于产业链间接催化。'
    elif any(k.lower() in low for k in authoritative):
        level = 3; label = '三级：权威媒体/财经新闻'; certainty = '中'; reason = '来自较权威媒体或财经新闻，但不一定是公司直接公告。'
    elif news:
        level = 4; label = '四级：普通新闻线索'; certainty = '中低'; reason = '有相关新闻线索，但确定性和直接性一般。'

    direct = '直接利好' if level == 1 else ('产业链间接利好' if level in [2,3] and catalyst.get('score',0) >= 35 else '相关线索')
    return {'level': level, 'label': label, 'certainty': certainty, 'reason': reason, 'impact_type': direct}


def build_event_direction_table(event_text: str) -> pd.DataFrame:
    """事件反向扫描之前先给出产业链映射表，便于你爸看明白。"""
    text = (event_text or '').lower()
    rows = []
    for theme, cfg in EVENT_THEMES.items():
        hit = any(k.lower() in text for k in cfg['keywords']) or not text
        if not hit:
            continue
        for direction, stocks in cfg['directions'].items():
            rows.append({
                '事件主题': theme,
                'A股方向': direction,
                '股票池代码': ', '.join(stocks[:8]),
                '解释': '这是产业链映射，不等于直接推荐；还要看起势柱、资金柱和买点位置。'
            })
    return pd.DataFrame(rows)


@st.cache_data(ttl=900)
def fetch_google_news_rss(query: str, limit: int = 8) -> List[Dict[str, str]]:
    """用 Google News RSS 抓取全球新闻线索。若网络不可用则返回空。"""
    if not query:
        return []
    url = 'https://news.google.com/rss/search?q=' + quote_plus(query) + '&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
    r = safe_get(url, timeout=6, retries=1)
    if not r:
        return []
    try:
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall('.//item')[:limit]:
            title = item.findtext('title') or ''
            link = item.findtext('link') or ''
            pub = item.findtext('pubDate') or ''
            source = ''
            src = item.find('source')
            if src is not None and src.text:
                source = src.text
            items.append({'title': title, 'link': link, 'pubDate': pub, 'source': source, 'query': query})
        return items
    except Exception:
        return []


def build_event_queries(code: str, snapshot: Dict[str, Any], industry: str, concepts: List[str]) -> List[str]:
    name = str(snapshot.get('name') or '')
    concepts = concepts or []
    queries = []
    # 个股相关新闻
    if name:
        queries.append(f'{name} 股票 利好 公告 订单 业绩')
    # 行业/概念相关新闻
    for c in concepts[:3]:
        queries.append(f'{c} A股 利好 新闻')
    if industry and industry != '行业未识别':
        queries.append(f'{industry} A股 政策 利好')
    # 全球事件关键词，按概念定向
    ctext = ' '.join(concepts + [industry])
    if any(k in ctext for k in ['CPO','光模块','算力','通信设备','AI服务器']):
        queries.append('NVIDIA AI Factory Rubin CPO optical module China stocks')
    if any(k in ctext for k in ['液冷','服务器','散热']):
        queries.append('NVIDIA AI data center liquid cooling China stocks')
    if any(k in ctext for k in ['机器人','智能制造','工业4.0']):
        queries.append('NVIDIA robotics humanoid Jetson Thor China A shares')
    if any(k in ctext for k in ['半导体','Chiplet','先进封装','国产芯片']):
        queries.append('semiconductor advanced packaging Chiplet China stocks')
    if any(k in ctext for k in ['电网','特高压','虚拟电厂']):
        queries.append('China power grid UHV investment A shares')
    # 去重并限制
    out = []
    for q in queries:
        if q not in out:
            out.append(q)
    return out[:5]


def auto_event_radar(code: str, snapshot: Dict[str, Any], industry: str, concepts: List[str]) -> Dict[str, Any]:
    queries = build_event_queries(code, snapshot, industry, concepts)
    news = []
    for q in queries:
        news.extend(fetch_google_news_rss(q, limit=5))
    # 去重
    seen, unique = set(), []
    for n in news:
        key = n.get('title','')[:80]
        if key and key not in seen:
            seen.add(key); unique.append(n)
    code = normalize_code(code)
    text_blob = ' '.join([n['title'] for n in unique]).lower()
    score = 0
    reasons = []
    # 按事件主题打分
    for theme, cfg in EVENT_THEMES.items():
        hit = any(k.lower() in text_blob for k in cfg['keywords'])
        code_hit = any(code in stocks for stocks in cfg['directions'].values())
        if hit and code_hit:
            score += 30; reasons.append(f'全球新闻命中相关主题：{theme}')
        elif hit:
            score += 12; reasons.append(f'新闻命中主题但与个股间接相关：{theme}')
    if unique:
        score += min(20, len(unique) * 2)
    score = min(100, score)
    label = '强事件线索' if score >= 70 else ('中等事件线索' if score >= 40 else ('弱事件线索' if score >= 15 else '暂无明显全球事件'))
    return {'score': score, 'label': label, 'news': unique[:12], 'queries': queries, 'reasons': reasons or ['未自动发现明显全球事件线索。']}


def scan_event_pool(event_text: str, max_n: int = 40) -> pd.DataFrame:
    pool = []
    text = event_text or ""
    for _, cfg in EVENT_THEMES.items():
        if any(k.lower() in text.lower() for k in cfg["keywords"]):
            for stocks in cfg["directions"].values():
                pool.extend(stocks)
    pool = sorted(set(pool))[:max_n]
    if not pool:
        return pd.DataFrame()

    snaps = fetch_em_snapshot(tuple(pool))
    rows = []
    for code in pool:
        snap = snaps.get(code, {})
        df = fetch_tencent_kline(code, 180)
        q = analyze_qishi(df) if not df.empty else {"ok": False}
        industry, concepts, src = detect_industry_concepts(code, snap)
        cat = analyze_event_catalyst(code, event_text, concepts)
        if q.get("ok"):
            rank = q["latest_score"] * 0.45 + q["fund_score"] * 0.25 + cat["score"] * 0.30
            rows.append({
                "代码": code,
                "名称": snap.get("name"),
                "现价": snap.get("price"),
                "涨跌幅": snap.get("pct"),
                "行业": industry,
                "概念": ",".join(concepts[:3]),
                "起势状态": q["latest_state"],
                "起势分": round(q["latest_score"], 1),
                "资金分": round(q["fund_score"], 1),
                "催化分": cat["score"],
                "追高风险": q["risk_state"],
                "排序分": round(rank, 1),
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("排序分", ascending=False)

