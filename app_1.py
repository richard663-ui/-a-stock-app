
import os
import time
import requests
import pandas as pd
import streamlit as st

try:
    import akshare as ak
    AK_OK = True
except Exception:
    ak = None
    AK_OK = False

try:
    import tushare as ts
    TS_OK = True
except Exception:
    TS_OK = False

try:
    import baostock as bs
    BS_OK = True
except Exception:
    BS_OK = False


st.set_page_config(page_title="A股智能研报 V16.7", layout="wide")

st.title("A股智能研报 V16.7")
st.caption("实用融合版｜单股快看｜AI起势追踪｜Level-2手动确认｜持仓建议")

CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

MASTER_FILE = os.path.join(CACHE_DIR, "stock_master.csv")
A_STOCK_MASTER_FILE = os.path.join(CACHE_DIR, "a_stock_master.csv")
BAOSTOCK_INDUSTRY_FILE = os.path.join(CACHE_DIR, "industry_map_baostock.csv")
INDUSTRY_MAP_FILE = os.path.join(CACHE_DIR, "industry_map.csv")
CONCEPT_MAP_FILE = os.path.join(CACHE_DIR, "concept_map.csv")
SPOT_CACHE_FILE = os.path.join(CACHE_DIR, "spot_cache.csv")
FUND_FLOW_CACHE_DIR = os.path.join(CACHE_DIR, "fund_flow")
os.makedirs(FUND_FLOW_CACHE_DIR, exist_ok=True)

# =========================
# 可选付费/增强数据源配置
# =========================
# 你拿到 Tushare Token 后，可以在系统环境变量设置 TUSHARE_TOKEN，
# 或直接把下面空字符串改成你的 token。V15 仍然默认不依赖 Tushare，没 token 也能跑。
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "").strip()
USE_TUSHARE = bool(TUSHARE_TOKEN and TS_OK)



# =========================
# 内置主数据 / 兜底行业池
# =========================

BUILTIN_MASTER = {
    "000001": {"name": "上证指数", "market": "sh", "type": "index", "industry": "指数"},
    "000300": {"name": "沪深300", "market": "sh", "type": "index", "industry": "指数"},
    "000905": {"name": "中证500", "market": "sh", "type": "index", "industry": "指数"},
    "000852": {"name": "中证1000", "market": "sh", "type": "index", "industry": "指数"},
    "399001": {"name": "深证成指", "market": "sz", "type": "index", "industry": "指数"},
    "399006": {"name": "创业板指", "market": "sz", "type": "index", "industry": "指数"},

    "000400": {"name": "许继电气", "market": "sz", "type": "stock", "industry": "电网设备"},
    "600312": {"name": "平高电气", "market": "sh", "type": "stock", "industry": "电网设备"},
    "600406": {"name": "国电南瑞", "market": "sh", "type": "stock", "industry": "电网设备"},
    "601179": {"name": "中国西电", "market": "sh", "type": "stock", "industry": "电网设备"},
    "002028": {"name": "思源电气", "market": "sz", "type": "stock", "industry": "电网设备"},

    "300308": {"name": "中际旭创", "market": "sz", "type": "stock", "industry": "通信设备"},
    "300502": {"name": "新易盛", "market": "sz", "type": "stock", "industry": "通信设备"},
    "300394": {"name": "天孚通信", "market": "sz", "type": "stock", "industry": "通信设备"},
    "603083": {"name": "剑桥科技", "market": "sh", "type": "stock", "industry": "通信设备"},
    "000988": {"name": "华工科技", "market": "sz", "type": "stock", "industry": "通信设备"},

    "000977": {"name": "浪潮信息", "market": "sz", "type": "stock", "industry": "计算机设备"},
    "603019": {"name": "中科曙光", "market": "sh", "type": "stock", "industry": "计算机设备"},
    "300276": {"name": "三丰智能", "market": "sz", "type": "stock", "industry": "机器人"},
    "002156": {"name": "通富微电", "market": "sz", "type": "stock", "industry": "半导体"},

    "002594": {"name": "比亚迪", "market": "sz", "type": "stock", "industry": "汽车整车"},
    "300750": {"name": "宁德时代", "market": "sz", "type": "stock", "industry": "电池"},
    "600519": {"name": "贵州茅台", "market": "sh", "type": "stock", "industry": "酿酒行业"},
    "600036": {"name": "招商银行", "market": "sh", "type": "stock", "industry": "银行"},
    "601318": {"name": "中国平安", "market": "sh", "type": "stock", "industry": "保险"},
}


FALLBACK_SECTOR_POOLS = {
    "电网设备": ["000400", "600312", "600406", "601179", "002028", "600517", "002090"],
    "通信设备": ["300308", "300502", "300394", "603083", "000988", "002281", "600498", "300548"],
    "计算机设备": ["000977", "603019", "000066", "002152", "300454"],
    "软件开发": ["600570", "300339", "300033", "002230", "688111", "300253"],
    "半导体": ["688981", "603501", "300661", "688041", "688012", "002371", "688008", "002156", "600584", "603986", "300782", "688126"],
    "消费电子": ["002475", "002241", "000725", "300433", "688036", "002384"],
    "机器人": ["300024", "002050", "002747", "300124", "688017", "603662", "300276", "300278", "603728", "002979", "301029", "688322"],
    "通用设备": ["300276", "300024", "002747", "300124", "603662", "002472", "002896", "603728"],
    "专用设备": ["300276", "300278", "300415", "300450", "603338", "688518", "301029"],
    "汽车整车": ["002594", "601633", "600104", "000625", "601127"],
    "汽车零部件": ["002050", "601689", "002920", "600741", "603596"],
    "电池": ["300750", "002812", "002709", "300014", "600884"],
    "光伏设备": ["601012", "300274", "688223", "002459", "600438"],
    "风电设备": ["002202", "601615", "300443", "603218"],
    "储能": ["300750", "300274", "002335", "002518", "688063"],
    "酿酒行业": ["600519", "000858", "000568", "600809", "002304"],
    "食品饮料": ["600887", "603288", "600872", "002507", "300999"],
    "银行": ["600036", "601398", "601939", "601288", "601166", "600000"],
    "保险": ["601318", "601601", "601628", "601336"],
    "证券": ["600030", "300059", "601688", "601211", "000776"],
    "房地产开发": ["000002", "600048", "001979", "600383"],
    "建筑装饰": ["601668", "601800", "601390", "601186"],
    "工程机械": ["600031", "000425", "000157", "600761"],
    "煤炭行业": ["601225", "601088", "600188", "600985"],
    "石油行业": ["601857", "600028", "600938", "600256"],
    "有色金属": ["601899", "603799", "600547", "000933", "002466"],
    "钢铁行业": ["600019", "000708", "600010", "000932"],
    "化学制品": ["600309", "002648", "002601", "600426"],
    "化学制药": ["600276", "000963", "300558", "688235"],
    "中药": ["600436", "000538", "600085", "000999"],
    "医疗器械": ["300760", "688271", "300347", "603259"],
    "军工电子": ["600760", "002179", "300034", "600893"],
    "航天航空": ["600760", "600893", "000768", "600316"],
    "电力行业": ["600900", "600905", "600011", "600027"],
    "环保行业": ["300070", "600323", "000967", "300152"],
    "传媒": ["300418", "002555", "300413", "002624"],
    "游戏": ["002555", "300418", "002602", "300031"],
    "互联网服务": ["300059", "300017", "300226", "600845"],
    "物流行业": ["601816", "002352", "600233", "603056"],
    "港口航运": ["601919", "600018", "601872", "000905"],
    "家电行业": ["000333", "600690", "000651", "002050"],
    "旅游酒店": ["600754", "000888", "601888", "600258"],
}


# 常用概念兜底池：只在 AKShare 概念库失效时使用；能拿到真实概念成分股就优先用真实数据。
FALLBACK_CONCEPT_POOLS = {
    "特高压": ["000400", "600312", "601179", "600406", "002028", "600517", "002090"],
    "智能电网": ["000400", "600406", "600312", "601179", "002028", "300360", "002090"],
    "虚拟电厂": ["000400", "600406", "300360", "002121", "002169"],
    "光模块": ["300308", "300502", "300394", "603083", "000988", "002281", "600498"],
    "CPO": ["300308", "300502", "300394", "603083", "000988", "002281"],
    "算力": ["300308", "300502", "000977", "603019", "002837", "600845", "000063"],
    "液冷服务器": ["002837", "000977", "603019", "300308", "300502"],
    "机器人": ["300024", "002050", "002747", "300124", "688017", "603662", "300276", "300278", "603728", "002979", "301029", "688322"],
    "通用设备": ["300276", "300024", "002747", "300124", "603662", "002472", "002896", "603728"],
    "专用设备": ["300276", "300278", "300415", "300450", "603338", "688518", "301029"],
    "减速器": ["002472", "002896", "603662", "002050", "300024"],
    "智能物流": ["300276", "300278", "603066", "603895", "002009"],
    "工业4.0": ["300276", "300024", "300124", "002747", "002050", "000988"],
    "新能源车": ["002594", "300750", "601633", "600104", "000625", "601127"],
    "动力电池": ["300750", "002812", "002709", "300014", "600884"],
    "白酒": ["600519", "000858", "000568", "600809", "002304"],
    "银行": ["600036", "601398", "601939", "601288", "601166", "600000"],
    "证券": ["600030", "300059", "601688", "601211", "000776"],
    "半导体": ["688981", "603501", "300661", "688041", "688012", "002371", "688008", "002156", "600584", "603986", "300782", "688126"],
    "先进封装": ["002156", "600584", "688362", "688521", "300782"],
    "Chiplet": ["002156", "600584", "688362", "300782"],
    "集成电路": ["002156", "688981", "603501", "300661", "688041", "688012", "600584"],
    "军工": ["600760", "002179", "300034", "600893", "000768", "600316"],
    "中字头": ["601668", "601390", "601186", "601800", "601857", "600028"],
}

STOCK_CONCEPT_HINTS = {
    "000400": ["特高压", "智能电网", "虚拟电厂"],
    "600312": ["特高压", "智能电网"],
    "600406": ["智能电网", "特高压", "虚拟电厂"],
    "300308": ["CPO", "光模块", "算力"],
    "300502": ["CPO", "光模块", "算力"],
    "300394": ["CPO", "光模块"],
    "000977": ["算力", "液冷服务器"],
    "603019": ["算力", "液冷服务器"],
    "300276": ["机器人", "智能物流", "工业4.0"],
    "002156": ["半导体", "先进封装", "Chiplet", "集成电路"],
    "002594": ["新能源车"],
    "300750": ["动力电池", "新能源车"],
    "600519": ["白酒"],
    "600036": ["银行"],
}



# 板块规范化别名：把各种接口/缓存里的不同叫法统一成可用板块。
BOARD_ALIASES = {
    "工业机器人": "机器人",
    "人形机器人": "机器人",
    "机器视觉": "机器人",
    "自动化设备": "机器人",
    "智能装备": "机器人",
    "智能制造": "机器人",
    "专用设备": "专用设备",
    "通用设备": "通用设备",
    "电网": "电网设备",
    "电气设备": "电网设备",
    "电源设备": "电网设备",
    "输配电气": "电网设备",
    "通信": "通信设备",
    "光通信": "通信设备",
    "光模块": "光模块",
    "CPO概念": "CPO",
    "酿酒": "酿酒行业",
    "白酒概念": "白酒",
    "券商": "证券",
    "证券行业": "证券",
}

NAME_KEYWORD_INDUSTRY = [
    (["银行"], "银行"),
    (["证券", "券商"], "证券"),
    (["保险"], "保险"),
    (["电气", "电网", "西电", "南瑞", "许继", "平高", "思源"], "电网设备"),
    (["通信", "光迅", "中际", "新易盛", "天孚", "剑桥", "华工"], "通信设备"),
    (["软件", "数科", "信息", "曙光", "浪潮"], "软件开发"),
    (["智能", "机器人", "装备", "自动化"], "机器人"),
    (["汽车", "比亚迪", "长城"], "汽车整车"),
    (["电池", "宁德"], "电池"),
    (["酒", "茅台", "五粮液", "老窖", "汾酒"], "酿酒行业"),
    (["药", "医药", "制药"], "化学制药"),
    (["中药"], "中药"),
    (["煤"], "煤炭行业"),
    (["钢", "特钢"], "钢铁行业"),
    (["电力"], "电力行业"),
]

def infer_industry_by_name(name):
    name = str(name or "")
    for keys, industry in NAME_KEYWORD_INDUSTRY:
        if any(k in name for k in keys):
            return industry
    return "行业未识别"

def canonical_board_name(raw_name, board_type="industry"):
    """
    把旧缓存里的“电网设备/特高压”这类混合字段，规范成可用于成分股匹配的板块名。
    这样不会因为缓存里多了概念后缀，导致行业对比失效。
    """
    if raw_name is None:
        return "行业未识别" if board_type == "industry" else "概念未识别"
    name = str(raw_name).strip()
    if name in ["", "nan", "None", "行业未识别", "概念未识别"]:
        return "行业未识别" if board_type == "industry" else "概念未识别"

    pool = FALLBACK_SECTOR_POOLS if board_type == "industry" else FALLBACK_CONCEPT_POOLS

    if name in BOARD_ALIASES:
        alias = BOARD_ALIASES[name]
        if alias in pool:
            return alias

    if name in pool:
        return name

    # 先按常见分隔符切开，优先命中精确板块名
    for sep in ["/", "｜", "|", ",", "，", "、", ";", "；", " "]:
        if sep in name:
            for part in [x.strip() for x in name.split(sep) if x.strip()]:
                if part in BOARD_ALIASES and BOARD_ALIASES[part] in pool:
                    return BOARD_ALIASES[part]
                if part in pool:
                    return part

    # 再做包含匹配：例如 “电网设备/特高压” -> “电网设备”
    for k in pool:
        if k in name or name in k:
            return k

    return name


def clean_status_for_main(status_text):
    """主界面不展示连接失败、扫描失败这类噪音；完整诊断放到折叠区。"""
    if not status_text:
        return "已读取可用缓存/兜底数据"
    bad_words = ["失败", "aborted", "RemoteDisconnected", "closed connection", "异常", "不可用"]
    parts = [p.strip() for p in str(status_text).split("；") if p.strip()]
    good = [p for p in parts if not any(w in p for w in bad_words)]
    if good:
        return "；".join(good[:3])
    return "已启用本地兜底数据"


# =========================
# 基础工具
# =========================



def get_board_db_status():
    """返回本地全市场板块库覆盖情况。"""
    result = {
        "industry_ready": False, "concept_ready": False,
        "industry_rows": 0, "concept_rows": 0,
        "industry_boards": 0, "concept_boards": 0,
        "industry_codes": 0, "concept_codes": 0,
    }
    try:
        if os.path.exists(INDUSTRY_MAP_FILE):
            df = pd.read_csv(INDUSTRY_MAP_FILE, dtype={"code": str})
            if df is not None and not df.empty:
                result["industry_ready"] = True
                result["industry_rows"] = len(df)
                result["industry_boards"] = df["board"].nunique() if "board" in df.columns else 0
                result["industry_codes"] = df["code"].astype(str).str.zfill(6).nunique() if "code" in df.columns else 0
    except Exception:
        pass
    try:
        if os.path.exists(CONCEPT_MAP_FILE):
            df = pd.read_csv(CONCEPT_MAP_FILE, dtype={"code": str})
            if df is not None and not df.empty:
                result["concept_ready"] = True
                result["concept_rows"] = len(df)
                result["concept_boards"] = df["board"].nunique() if "board" in df.columns else 0
                result["concept_codes"] = df["code"].astype(str).str.zfill(6).nunique() if "code" in df.columns else 0
    except Exception:
        pass
    return result
def safe_get(url, params=None, retries=2, timeout=6):
    headers = {"User-Agent": "Mozilla/5.0"}
    for _ in range(retries):
        try:
            return requests.get(url, params=params, headers=headers, timeout=timeout)
        except Exception:
            time.sleep(1.0)
    return None


def infer_market(code):
    code = str(code).strip().zfill(6)
    if code in ["000001", "000300", "000905", "000852"]:
        return "sh"
    if code.startswith("399"):
        return "sz"
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "sz"
    if code.startswith(("8", "4", "9")):
        return "bj"
    return "sz"


def fetch_stock_name(code, market=None):
    code = str(code).strip().zfill(6)
    market = market or infer_market(code)
    r = safe_get(f"https://qt.gtimg.cn/q={market}{code}")
    if not r:
        return code
    try:
        text = r.content.decode("gbk", errors="ignore")
        parts = text.split("~")
        if len(parts) > 1 and parts[1]:
            return parts[1]
    except Exception:
        pass
    return code


def to_float(x):
    try:
        if pd.isna(x):
            return None
        if isinstance(x, str):
            raw = x.strip()
            if raw in ["", "-", "--", "None", "nan"]:
                return None
            raw = raw.replace("%", "").replace(",", "").replace("亿", "").replace("万", "")
            return float(raw)
        return float(x)
    except Exception:
        return None


def pick_col(df, keywords):
    for col in df.columns:
        name = str(col)
        if all(k in name for k in keywords):
            return col
    return None


def first_existing_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None


def normalize_series(s):
    s = s.dropna()
    if len(s) == 0:
        return s
    first = s.iloc[0]
    if first == 0 or pd.isna(first):
        return s
    return s / first * 100


def show_value(label, value, suffix=""):
    if value is not None:
        try:
            if isinstance(value, float):
                st.write(f"- {label}：**{value:,.2f}{suffix}**")
            else:
                st.write(f"- {label}：**{value}{suffix}**")
        except Exception:
            st.write(f"- {label}：**{value}{suffix}**")


def format_money(x):
    if x is None or pd.isna(x):
        return None
    try:
        x = float(x)
        if abs(x) >= 1e8:
            return f"{x/1e8:.2f}亿"
        if abs(x) >= 1e4:
            return f"{x/1e4:.2f}万"
        return f"{x:.2f}"
    except Exception:
        return None


# =========================
# 主数据
# =========================

def load_stock_master():
    """
    修复旧缓存污染：以前 stock_master.csv 里如果把 000400 写成“行业未识别”，
    会覆盖内置主数据，导致许继电气这种常见票都识别不出行业。
    现在逻辑：先放内置主数据，再读缓存；缓存只有在行业不是“行业未识别”时才覆盖行业。
    """
    master = {k: v.copy() for k, v in BUILTIN_MASTER.items()}

    if os.path.exists(MASTER_FILE):
        try:
            df = pd.read_csv(MASTER_FILE, dtype=str)
            for _, row in df.iterrows():
                code = str(row.get("code", "")).zfill(6)
                if not code or code == "000nan":
                    continue

                cached = {
                    "name": row.get("name", code),
                    "market": row.get("market", infer_market(code)),
                    "type": row.get("type", "stock"),
                    "industry": row.get("industry", "行业未识别"),
                }

                if code in master:
                    # 只用缓存补名字/市场，不允许“行业未识别”覆盖内置行业。
                    if cached.get("name") and cached["name"] not in ["nan", "None"]:
                        master[code]["name"] = cached["name"]
                    if cached.get("market") and cached["market"] not in ["nan", "None"]:
                        master[code]["market"] = cached["market"]
                    if cached.get("industry") and cached["industry"] != "行业未识别":
                        # 只接受能规范到已知行业池的缓存行业；否则保留内置行业，避免旧缓存污染。
                        normalized_industry = canonical_board_name(cached["industry"], "industry")
                        if normalized_industry in FALLBACK_SECTOR_POOLS:
                            master[code]["industry"] = normalized_industry
                else:
                    master[code] = cached
        except Exception:
            pass

    # 每次写回一次，清理旧缓存。
    try:
        df = pd.DataFrame([{"code": k, **v} for k, v in master.items()])
        df.to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
    except Exception:
        pass

    return master

def get_meta_from_master(code):
    code = str(code).strip().zfill(6)
    master = load_stock_master()
    if code in master:
        meta = master[code].copy()
        meta["industry"] = canonical_board_name(meta.get("industry", "行业未识别"), "industry")
        if meta["industry"] == "行业未识别":
            bs_hit = lookup_baostock_industry(code)
            if bs_hit and bs_hit.get("industry") != "行业未识别":
                meta["industry"] = bs_hit["industry"]
                if bs_hit.get("name"):
                    meta["name"] = bs_hit["name"]
        return meta
    bs_hit = lookup_baostock_industry(code)
    if bs_hit:
        return {
            "name": bs_hit.get("name", code),
            "market": infer_market(code),
            "type": "stock",
            "industry": bs_hit.get("industry", "行业未识别"),
        }
    market = infer_market(code)
    name = fetch_stock_name(code, market)
    return {"name": name, "market": market, "type": "stock", "industry": "行业未识别"}


def get_symbol(code):
    code = str(code).strip().zfill(6)
    meta = get_meta_from_master(code)
    return f"{meta['market']}{code}"


# =========================
# AKShare 实时快照：PE/PB/换手率/量比
# =========================

def get_spot_snapshot(force=False):
    if os.path.exists(SPOT_CACHE_FILE) and not force:
        try:
            df = pd.read_csv(SPOT_CACHE_FILE, dtype={"代码": str})
            if df is not None and not df.empty and "代码" in df.columns:
                df["代码"] = df["代码"].astype(str).str.zfill(6)
                return df, "读取本地快照缓存"
        except Exception:
            pass

    if not AK_OK:
        return None, "AKShare不可用"

    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return None, "AKShare快照为空"
        if "代码" not in df.columns:
            return None, f"AKShare快照字段异常：{list(df.columns)}"

        df["代码"] = df["代码"].astype(str).str.zfill(6)

        numeric_cols = [
            "最新价", "涨跌幅", "涨跌额", "成交量", "成交额",
            "振幅", "最高", "最低", "今开", "昨收",
            "量比", "换手率", "市盈率-动态", "市净率",
            "总市值", "流通市值", "60日涨跌幅", "年初至今涨跌幅",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df.to_csv(SPOT_CACHE_FILE, index=False, encoding="utf-8-sig")
        return df, "AKShare实时快照成功"
    except Exception as e:
        return None, f"AKShare快照失败：{e}"


# =========================
# K线行情
# =========================

def fetch_tencent_kline(code, count=500):
    code = str(code).strip().zfill(6)
    symbol = get_symbol(code)
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {"param": f"{symbol},day,,,{count},qfq"}

    r = safe_get(url, params=params)
    if not r:
        return None

    try:
        data = r.json()
    except Exception:
        return None

    if data.get("code") != 0:
        return None

    raw = data.get("data", {}).get(symbol, {})
    klines = raw.get("qfqday") or raw.get("day")
    if not klines:
        return None

    rows = []
    for x in klines:
        rows.append({
            "date": x[0],
            "open": float(x[1]),
            "close": float(x[2]),
            "high": float(x[3]),
            "low": float(x[4]),
            "volume": float(x[5]),
        })

    df = pd.DataFrame(rows)
    df["pct_change"] = df["close"].pct_change() * 100
    return df


def add_indicators(df):
    df = df.copy()
    df["MA5"] = df["close"].rolling(5).mean()
    df["MA10"] = df["close"].rolling(10).mean()
    df["MA20"] = df["close"].rolling(20).mean()
    df["MA60"] = df["close"].rolling(60).mean()
    df["MA120"] = df["close"].rolling(120).mean()

    df["MA20_SLOPE"] = df["MA20"] - df["MA20"].shift(5)
    df["MA60_SLOPE"] = df["MA60"] - df["MA60"].shift(10)

    df["VOL5"] = df["volume"].rolling(5).mean()
    df["VOL20"] = df["volume"].rolling(20).mean()

    df["TR"] = df["high"] - df["low"]
    df["ATR14"] = df["TR"].rolling(14).mean()

    df["RET5"] = df["close"].pct_change(5) * 100
    df["RET20"] = df["close"].pct_change(20) * 100
    df["RET60"] = df["close"].pct_change(60) * 100
    df["RET120"] = df["close"].pct_change(120) * 100

    df["VOLATILITY20"] = df["pct_change"].rolling(20).std()
    df["MAX_DRAWDOWN60"] = (df["close"] / df["close"].rolling(60).max() - 1) * 100

    df["DIST_MA20"] = (df["close"] - df["MA20"]) / df["close"] * 100
    df["DIST_MA60"] = (df["close"] - df["MA60"]) / df["close"] * 100

    return df


def get_kline(code, force=False):
    code = str(code).strip().zfill(6)
    cache_file = os.path.join(CACHE_DIR, f"{code}.csv")

    if os.path.exists(cache_file) and not force:
        try:
            df = pd.read_csv(cache_file)
            if df is not None and len(df) >= 130:
                return add_indicators(df), "读取本地缓存"
        except Exception:
            pass

    df = fetch_tencent_kline(code)
    if df is not None and len(df) >= 130:
        df.to_csv(cache_file, index=False, encoding="utf-8-sig")
        return add_indicators(df), "腾讯实时更新成功"

    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            if df is not None and len(df) >= 130:
                return add_indicators(df), "腾讯失败，读取本地缓存"
        except Exception:
            pass

    return None, "数据获取失败"


def validate_market_data(df):
    if df is None or len(df) < 130:
        return False
    latest = df.iloc[-1]
    return (
        pd.notna(latest["RET20"])
        and pd.notna(latest["RET60"])
        and latest["close"] > 0
        and (abs(latest["RET20"]) > 0.01 or abs(latest["RET60"]) > 0.01)
    )


def get_benchmark_data(force=False):
    benchmarks = [
        ("000300", "沪深300"),
        ("000001", "上证指数"),
        ("399001", "深证成指"),
        ("399006", "创业板指"),
        ("000905", "中证500"),
    ]
    for code, name in benchmarks:
        df, status = get_kline(code, force)
        if validate_market_data(df):
            return df, name, status
    return None, "基准指数不可用", "失败"



# =========================
# 全A数据底座：BaoStock / Tushare 本地库
# =========================

def to_plain_code(code):
    raw = str(code or "").strip().lower()
    raw = raw.replace("sh.", "").replace("sz.", "").replace("bj.", "")
    raw = raw.replace(".sh", "").replace(".sz", "").replace(".bj", "")
    digits = "".join([c for c in raw if c.isdigit()])
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6) if digits else ""


def to_baostock_code(code):
    code = to_plain_code(code)
    m = infer_market(code)
    if m == "sh":
        return f"sh.{code}"
    if m == "bj":
        return f"bj.{code}"
    return f"sz.{code}"


def build_baostock_industry_db(force=False):
    """建立全A行业底座。BaoStock 的行业表比盘中临时扫描东方财富板块更适合做行业主表。"""
    if os.path.exists(BAOSTOCK_INDUSTRY_FILE) and not force:
        try:
            df = pd.read_csv(BAOSTOCK_INDUSTRY_FILE, dtype={"code": str})
            if df is not None and not df.empty:
                df["code"] = df["code"].astype(str).str.zfill(6)
                return df, "读取BaoStock本地行业库"
        except Exception:
            pass

    if not BS_OK:
        return pd.DataFrame(), "BaoStock未安装，跳过行业底座更新"

    try:
        lg = bs.login()
        if getattr(lg, "error_code", "0") != "0":
            return pd.DataFrame(), f"BaoStock登录失败：{getattr(lg, 'error_msg', '')}"

        rs = bs.query_stock_industry()
        rows = []
        while (rs.error_code == "0") and rs.next():
            rows.append(rs.get_row_data())
        fields = list(rs.fields)
        bs.logout()

        if not rows:
            return pd.DataFrame(), "BaoStock行业数据为空"

        raw = pd.DataFrame(rows, columns=fields)
        code_col = first_existing_col(raw, ["code", "股票代码"])
        name_col = first_existing_col(raw, ["code_name", "股票名称", "name"])
        industry_col = first_existing_col(raw, ["industry", "行业", "industryName"])
        class_col = first_existing_col(raw, ["industryClassification", "行业分类"])
        update_col = first_existing_col(raw, ["updateDate", "更新日期"])

        out = pd.DataFrame()
        out["code"] = raw[code_col].apply(to_plain_code) if code_col else ""
        out["name"] = raw[name_col].astype(str) if name_col else out["code"]
        out["industry"] = raw[industry_col].astype(str).apply(lambda x: canonical_board_name(x, "industry")) if industry_col else "行业未识别"
        out["raw_industry"] = raw[industry_col].astype(str) if industry_col else ""
        out["industry_classification"] = raw[class_col].astype(str) if class_col else ""
        out["update_date"] = raw[update_col].astype(str) if update_col else ""
        out = out[(out["code"].str.len() == 6) & (out["code"].str.isdigit())]
        out = out[out["industry"].notna() & (out["industry"] != "") & (out["industry"] != "nan")]
        out = out.drop_duplicates(subset=["code"], keep="last")
        out.to_csv(BAOSTOCK_INDUSTRY_FILE, index=False, encoding="utf-8-sig")
        return out, f"BaoStock全A行业库更新成功：股票{out['code'].nunique()}只，行业{out['industry'].nunique()}个"
    except Exception as e:
        try:
            bs.logout()
        except Exception:
            pass
        return pd.DataFrame(), f"BaoStock行业库更新失败：{e}"


def load_baostock_industry_db():
    if os.path.exists(BAOSTOCK_INDUSTRY_FILE):
        try:
            df = pd.read_csv(BAOSTOCK_INDUSTRY_FILE, dtype={"code": str})
            if df is not None and not df.empty:
                df["code"] = df["code"].astype(str).str.zfill(6)
                return df
        except Exception:
            pass
    return pd.DataFrame()


def lookup_baostock_industry(code):
    code = str(code).strip().zfill(6)
    df = load_baostock_industry_db()
    if df is None or df.empty:
        return None
    hit = df[df["code"].astype(str).str.zfill(6) == code]
    if hit.empty:
        return None
    row = hit.iloc[0]
    return {
        "code": code,
        "name": str(row.get("name", code)),
        "industry": canonical_board_name(str(row.get("industry", "行业未识别")), "industry"),
        "raw_industry": str(row.get("raw_industry", "")),
        "source": "BaoStock本地行业库",
    }


def build_tushare_stock_basic_db(force=False):
    """可选：Tushare Token 存在时补全全A名称/行业。没有 Token 不影响运行。"""
    if os.path.exists(A_STOCK_MASTER_FILE) and not force:
        try:
            df = pd.read_csv(A_STOCK_MASTER_FILE, dtype={"code": str})
            if df is not None and not df.empty:
                df["code"] = df["code"].astype(str).str.zfill(6)
                return df, "读取Tushare/本地主表缓存"
        except Exception:
            pass
    if not USE_TUSHARE:
        return pd.DataFrame(), "Tushare未启用，跳过全A主表"
    try:
        pro = ts.pro_api(TUSHARE_TOKEN)
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,area,industry,market,list_date")
        if df is None or df.empty:
            return pd.DataFrame(), "Tushare stock_basic为空"
        out = pd.DataFrame()
        out["code"] = df["symbol"].astype(str).str.zfill(6)
        out["name"] = df["name"].astype(str)
        out["industry"] = df.get("industry", "").astype(str).apply(lambda x: canonical_board_name(x, "industry"))
        out["market"] = out["code"].apply(infer_market)
        out["type"] = "stock"
        out.to_csv(A_STOCK_MASTER_FILE, index=False, encoding="utf-8-sig")
        return out, f"Tushare全A主表更新成功：股票{out['code'].nunique()}只"
    except Exception as e:
        return pd.DataFrame(), f"Tushare全A主表更新失败：{e}"


def build_full_a_foundation(force=False):
    """一键更新数据底座：行业优先 BaoStock，股票主表可选 Tushare。概念仍由东方财富/AKShare补充。"""
    statuses = []
    bs_df, bs_status = build_baostock_industry_db(force=force)
    statuses.append(bs_status)
    ts_df, ts_status = build_tushare_stock_basic_db(force=force)
    statuses.append(ts_status)
    # 将 BaoStock 行业写入 stock_master，避免旧缓存/未识别污染。
    try:
        master = load_stock_master()
        if bs_df is not None and not bs_df.empty:
            for _, r in bs_df.iterrows():
                code = str(r["code"]).zfill(6)
                if not code or code == "000nan":
                    continue
                current = master.get(code, {})
                name = str(r.get("name", current.get("name", code)))
                industry = canonical_board_name(str(r.get("industry", current.get("industry", "行业未识别"))), "industry")
                master[code] = {
                    "name": name if name and name != "nan" else current.get("name", fetch_stock_name(code)),
                    "market": current.get("market", infer_market(code)),
                    "type": "stock",
                    "industry": industry if industry not in ["", "nan", "行业未识别"] else current.get("industry", "行业未识别"),
                }
        if ts_df is not None and not ts_df.empty:
            for _, r in ts_df.iterrows():
                code = str(r["code"]).zfill(6)
                current = master.get(code, {})
                industry = canonical_board_name(str(r.get("industry", current.get("industry", "行业未识别"))), "industry")
                if current.get("industry") and current.get("industry") != "行业未识别":
                    industry = current.get("industry")
                master[code] = {
                    "name": str(r.get("name", current.get("name", code))),
                    "market": current.get("market", infer_market(code)),
                    "type": "stock",
                    "industry": industry,
                }
        pd.DataFrame([{"code": k, **v} for k, v in master.items()]).to_csv(MASTER_FILE, index=False, encoding="utf-8-sig")
        statuses.append(f"本地stock_master已同步：{len(master)}只")
    except Exception as e:
        statuses.append(f"同步stock_master失败：{e}")
    return statuses


def get_foundation_status():
    out = {"baostock_ready": False, "baostock_codes": 0, "baostock_industries": 0, "master_codes": 0}
    try:
        if os.path.exists(BAOSTOCK_INDUSTRY_FILE):
            df = pd.read_csv(BAOSTOCK_INDUSTRY_FILE, dtype={"code": str})
            out["baostock_ready"] = not df.empty
            out["baostock_codes"] = int(df["code"].nunique()) if "code" in df.columns else 0
            out["baostock_industries"] = int(df["industry"].nunique()) if "industry" in df.columns else 0
        if os.path.exists(MASTER_FILE):
            m = pd.read_csv(MASTER_FILE, dtype={"code": str})
            out["master_codes"] = int(m["code"].nunique()) if "code" in m.columns else 0
    except Exception:
        pass
    return out

# =========================
# 行业 / 概念扫描
# =========================

def normalize_board_map_df(df):
    """统一全市场板块库字段，避免不同接口字段名变化导致扫描结果为空。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "name", "board", "board_type"])
    out = df.copy()
    if "code" in out.columns:
        out["code"] = out["code"].astype(str).str.zfill(6)
    if "board" in out.columns:
        out["board"] = out["board"].astype(str)
    return out.drop_duplicates(subset=["code", "board", "board_type"])


def build_board_map(board_type="industry", force=False, max_boards=99999):
    """
    V15.3 修正版：全市场板块库更新更稳。
    关键改动：
    1) 成分股接口同时尝试板块名称和板块代码；
    2) 单个板块失败不会导致全局失败；
    3) 若实时扫描失败，自动生成内置兜底库，避免所有股票行业/概念空白；
    4) 主报告只读缓存/兜底，不在盘中强制扫描。
    """
    if board_type == "industry":
        cache_file = INDUSTRY_MAP_FILE
        name_func = ak.stock_board_industry_name_em
        cons_func = ak.stock_board_industry_cons_em
        label = "行业"
        fallback_pool = FALLBACK_SECTOR_POOLS
    else:
        cache_file = CONCEPT_MAP_FILE
        name_func = ak.stock_board_concept_name_em
        cons_func = ak.stock_board_concept_cons_em
        label = "概念"
        fallback_pool = FALLBACK_CONCEPT_POOLS

    if os.path.exists(cache_file) and not force:
        try:
            df = pd.read_csv(cache_file, dtype={"code": str})
            df = normalize_board_map_df(df)
            if df is not None and not df.empty:
                return df, f"读取{label}映射缓存"
        except Exception:
            pass

    if not force:
        # 不在生成报告时扫描全市场，避免盘中卡死；直接返回 None，让 detect_boards 走内置兜底。
        return None, f"{label}映射缓存不存在，已跳过实时扫描"

    rows = []
    errors = 0

    if AK_OK:
        try:
            board_df = name_func()
            if board_df is not None and not board_df.empty:
                name_col = first_existing_col(board_df, ["板块名称", "名称", "board", "name"])
                code_col = first_existing_col(board_df, ["板块代码", "代码", "code"])

                if name_col is not None:
                    board_records = []
                    for _, r in board_df.iterrows():
                        board_name = str(r[name_col]).strip()
                        board_code = str(r[code_col]).strip() if code_col is not None and pd.notna(r[code_col]) else None
                        if board_name and board_name not in ["nan", "None"]:
                            board_records.append((board_name, board_code))
                    board_records = board_records[:max_boards] if max_boards else board_records

                    prog = st.progress(0, text=f"正在更新{label}板块库...") if 'st' in globals() else None
                    total = max(1, len(board_records))

                    for idx, (board_name, board_code) in enumerate(board_records):
                        if prog and idx % 5 == 0:
                            prog.progress(min(1.0, idx / total), text=f"正在更新{label}板块库：{idx}/{total}  {board_name}")
                        cons = None
                        # 先用板块名称，再用板块代码。有些 AKShare/东方财富版本两者只有一个能通。
                        for symbol in [board_name, board_code]:
                            if symbol is None or symbol in ["", "nan", "None"]:
                                continue
                            try:
                                cons = cons_func(symbol=symbol)
                                if cons is not None and not cons.empty:
                                    break
                            except Exception:
                                cons = None
                                continue

                        if cons is None or cons.empty:
                            errors += 1
                            continue

                        stock_code_col = first_existing_col(cons, ["代码", "股票代码", "证券代码"])
                        stock_name_col = first_existing_col(cons, ["名称", "股票名称", "证券简称"])
                        if stock_code_col is None:
                            errors += 1
                            continue

                        board_clean = canonical_board_name(board_name, board_type)
                        for _, row in cons.iterrows():
                            stock_code = str(row[stock_code_col]).zfill(6)
                            stock_name = str(row[stock_name_col]) if stock_name_col else stock_code
                            if len(stock_code) == 6 and stock_code.isdigit():
                                rows.append({
                                    "code": stock_code,
                                    "name": stock_name,
                                    "board": board_clean,
                                    "board_type": board_type,
                                })
                        time.sleep(0.01)
                    if prog:
                        prog.progress(1.0, text=f"{label}板块库更新完成")
        except Exception as e:
            errors += 1

    # 实时扫描失败或覆盖不足时，自动补内置兜底库；注意这不是最终全集，但能保证核心股票不空白。
    fallback_rows = []
    for board, codes in fallback_pool.items():
        board_clean = canonical_board_name(board, board_type)
        for c in codes:
            c = str(c).zfill(6)
            fallback_rows.append({
                "code": c,
                "name": fetch_stock_name(c),
                "board": board_clean,
                "board_type": board_type,
            })

    if rows:
        df = pd.DataFrame(rows + fallback_rows)
        df = normalize_board_map_df(df)
        df.to_csv(cache_file, index=False, encoding="utf-8-sig")
        return df, f"AKShare{label}映射扫描成功，补充本地兜底；板块{df['board'].nunique()}个，股票{df['code'].nunique()}只"

    if fallback_rows:
        df = pd.DataFrame(fallback_rows)
        df = normalize_board_map_df(df)
        df.to_csv(cache_file, index=False, encoding="utf-8-sig")
        return df, f"{label}实时扫描失败，已生成本地兜底库；板块{df['board'].nunique()}个，股票{df['code'].nunique()}只"

    return None, f"{label}成分股扫描失败"

def detect_boards(code, force=False):
    """
    V15 数据激活：行业/概念强兜底。
    顺序：内置主数据 -> AKShare行业缓存/扫描 -> 内置行业池 -> AKShare概念缓存/扫描 -> 手工概念提示/概念池。
    """
    code = str(code).strip().zfill(6)
    boards = {"industry": None, "concepts": [], "status": []}

    meta = get_meta_from_master(code)
    if meta.get("industry") and meta["industry"] != "行业未识别":
        boards["industry"] = canonical_board_name(meta["industry"], "industry")
        boards["status"].append("主数据识别行业")

    # BaoStock 全A行业库兜底：这是 V15.4 的行业主底座，不再依赖盘中临时扫描。
    if boards["industry"] is None:
        bs_hit = lookup_baostock_industry(code)
        if bs_hit and bs_hit.get("industry") and bs_hit["industry"] != "行业未识别":
            boards["industry"] = canonical_board_name(bs_hit["industry"], "industry")
            boards["status"].append("BaoStock本地行业库识别行业")

    # AKShare 行业映射只作为补充，不再作为唯一底座。
    if boards["industry"] is None:
        industry_map, status = build_board_map("industry", force=force)
        if status:
            boards["status"].append(status)
        if industry_map is not None and not industry_map.empty:
            hit = industry_map[industry_map["code"].astype(str).str.zfill(6) == code]
            if not hit.empty:
                boards["industry"] = str(hit.iloc[0]["board"])

    # 内置行业池兜底
    if boards["industry"] is None:
        for industry, codes in FALLBACK_SECTOR_POOLS.items():
            if code in [str(x).zfill(6) for x in codes]:
                boards["industry"] = industry
                boards["status"].append("内置行业池识别行业")
                break

    # 股票名称关键词兜底：解决 300276 这类常见票缓存/扫描没命中时行业为空的问题。
    if boards["industry"] is None:
        guessed_industry = infer_industry_by_name(meta.get("name", ""))
        if guessed_industry != "行业未识别":
            boards["industry"] = guessed_industry
            boards["status"].append("股票名称关键词识别行业")

    # 概念 AKShare 扫描
    concept_map, c_status = build_board_map("concept", force=force)
    if c_status:
        boards["status"].append(c_status)
    if concept_map is not None and not concept_map.empty:
        hit = concept_map[concept_map["code"].astype(str).str.zfill(6) == code]
        if not hit.empty:
            concepts = hit["board"].dropna().astype(str).unique().tolist()
            boards["concepts"].extend(concepts[:12])

    # 手工概念提示兜底
    for c in STOCK_CONCEPT_HINTS.get(code, []):
        if c not in boards["concepts"]:
            boards["concepts"].append(c)

    # 概念池反查兜底
    for concept, codes in FALLBACK_CONCEPT_POOLS.items():
        if code in [str(x).zfill(6) for x in codes] and concept not in boards["concepts"]:
            boards["concepts"].append(concept)

    # 如果还没有概念，按规范行业补一组可用概念，避免主界面出现一堆无效概念板块。
    if not boards["concepts"]:
        industry_for_concept = boards.get("industry")
        industry_default_concepts = {
            "电网设备": ["特高压", "智能电网"],
            "通信设备": ["光模块", "CPO", "算力"],
            "计算机设备": ["算力"],
            "机器人": ["机器人", "工业4.0", "智能物流"],
            "通用设备": ["机器人", "工业4.0"],
            "专用设备": ["机器人", "智能物流"],
            "汽车整车": ["新能源车"],
            "电池": ["动力电池", "储能"],
            "酿酒行业": ["白酒"],
            "银行": ["银行"],
            "证券": ["证券"],
        }
        for c in industry_default_concepts.get(industry_for_concept, []):
            if c not in boards["concepts"]:
                boards["concepts"].append(c)

    if boards["industry"] is None:
        boards["industry"] = "行业未识别"
    else:
        boards["industry"] = canonical_board_name(boards["industry"], "industry")

    boards["concepts"] = [canonical_board_name(c, "concept") for c in boards["concepts"]]
    boards["concepts"] = [c for c in boards["concepts"] if c not in ["", "概念未识别"]]
    boards["concepts"] = list(dict.fromkeys(boards["concepts"]))[:8]
    return boards

def get_board_constituents(board_name, board_type="industry"):
    board_name = canonical_board_name(board_name, board_type)
    if board_name in [None, "", "行业未识别", "概念未识别"]:
        return [], "板块名称无效"

    # 内置兜底先准备好，接口失败时立刻可用。
    fallback = []
    if board_type == "industry" and board_name in FALLBACK_SECTOR_POOLS:
        fallback = FALLBACK_SECTOR_POOLS[board_name]
    if board_type == "concept" and board_name in FALLBACK_CONCEPT_POOLS:
        fallback = FALLBACK_CONCEPT_POOLS[board_name]

    # V15.4：行业成分股优先从 BaoStock 本地全A行业库取，覆盖更广且不会盘中卡死。
    if board_type == "industry":
        bs_df = load_baostock_industry_db()
        if bs_df is not None and not bs_df.empty and "industry" in bs_df.columns:
            temp = bs_df.copy()
            temp["industry_norm"] = temp["industry"].astype(str).apply(lambda x: canonical_board_name(x, "industry"))
            hit = temp[temp["industry_norm"] == board_name]
            if not hit.empty:
                codes = hit["code"].dropna().astype(str).str.zfill(6).unique().tolist()
                if codes:
                    return codes, "BaoStock本地行业库成分股"

    if AK_OK:
        try:
            if board_type == "industry":
                cons = ak.stock_board_industry_cons_em(symbol=board_name)
            else:
                cons = ak.stock_board_concept_cons_em(symbol=board_name)

            if cons is not None and not cons.empty:
                code_col = first_existing_col(cons, ["代码", "股票代码"])
                if code_col is not None:
                    codes = cons[code_col].dropna().astype(str).str.zfill(6).unique().tolist()
                    if len(codes) > 0:
                        return codes, f"AKShare {board_type} 成分股"
        except Exception:
            pass

    if fallback:
        return [str(x).zfill(6) for x in fallback], f"内置{('行业' if board_type=='industry' else '概念')}池"

    return [], f"{board_type}成分股不可用"

def get_market_cap_map(force=False):
    spot_df, status = get_spot_snapshot(force=force)
    if spot_df is None or spot_df.empty:
        return {}, status
    if "代码" not in spot_df.columns or "总市值" not in spot_df.columns:
        return {}, "快照缺少代码或总市值字段"

    temp = spot_df[["代码", "总市值"]].copy()
    temp["代码"] = temp["代码"].astype(str).str.zfill(6)
    temp["总市值"] = pd.to_numeric(temp["总市值"], errors="coerce")
    cap_map = dict(zip(temp["代码"], temp["总市值"]))
    return cap_map, status


def build_board_average(codes, force=False, max_members=12, weighted=True):
    series_list = []
    rows = []
    cap_map, cap_status = get_market_cap_map(force=force)
    limited_codes = codes[:max_members]

    for code in limited_codes:
        code = str(code).zfill(6)
        df, status = get_kline(code, force)
        if df is None or len(df) < 130:
            continue

        meta = get_meta_from_master(code)
        latest = df.iloc[-1]
        cap = to_float(cap_map.get(code, None))

        temp = df[["date", "close"]].tail(120).copy()
        temp["norm"] = normalize_series(temp["close"])
        temp = temp[["date", "norm"]].rename(columns={"norm": code})
        series_list.append(temp)

        rows.append({
            "code": code,
            "name": meta["name"],
            "market_cap": cap,
            "ret20": latest["RET20"],
            "ret60": latest["RET60"],
            "ret120": latest["RET120"],
            "above_ma20": latest["close"] > latest["MA20"],
            "above_ma60": latest["close"] > latest["MA60"],
            "volatility20": latest["VOLATILITY20"],
        })

    if not series_list or not rows:
        return None, pd.DataFrame(), "板块行情样本不足"

    merged = series_list[0]
    for s in series_list[1:]:
        merged = pd.merge(merged, s, on="date", how="outer")

    merged = merged.sort_values("date")
    rows_df = pd.DataFrame(rows)
    code_cols = [c for c in merged.columns if c != "date"]

    if weighted and rows_df["market_cap"].notna().sum() >= 3:
        valid_caps = rows_df.dropna(subset=["market_cap"])
        total_cap = valid_caps["market_cap"].sum()
        if total_cap and total_cap > 0:
            weights = {row["code"]: row["market_cap"] / total_cap for _, row in valid_caps.iterrows()}
            weighted_cols = [c for c in code_cols if c in weights]
            if weighted_cols:
                merged["board_avg"] = 0.0
                weight_sum = 0.0
                for c in weighted_cols:
                    merged[c] = merged[c].ffill().bfill()
                    merged["board_avg"] += merged[c] * weights[c]
                    weight_sum += weights[c]
                if weight_sum > 0:
                    merged["board_avg"] = merged["board_avg"] / weight_sum
                    return merged[["date", "board_avg"]], rows_df, f"市值加权板块走势，样本数 {len(rows_df)}"

    merged["board_avg"] = merged[code_cols].mean(axis=1)
    return merged[["date", "board_avg"]], rows_df, f"等权板块走势，样本数 {len(rows_df)}"


# =========================
# 自动基本面
# =========================


def fetch_tushare_daily_basic(code):
    """
    Tushare 作为可选增强层：主要用于 PE/PB/换手率/量比/市值的盘后兜底。
    注意：它不是盘中实时买点核心；没 token 或接口不可用时自动跳过。
    """
    result = {"success": False, "data": {}, "notes": []}
    if not USE_TUSHARE:
        result["notes"].append("Tushare未启用。")
        return result

    code = str(code).strip().zfill(6)
    suffix = ".SH" if infer_market(code) == "sh" else ".SZ"
    ts_code = f"{code}{suffix}"

    try:
        pro = ts.pro_api(TUSHARE_TOKEN)
        df = pro.daily_basic(ts_code=ts_code, fields="ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,total_mv,circ_mv")
        if df is None or df.empty:
            result["notes"].append("Tushare daily_basic为空。")
            return result
        row = df.iloc[0]
        result["data"] = {
            "turnover": to_float(row.get("turnover_rate")),
            "volume_ratio": to_float(row.get("volume_ratio")),
            "pe_dynamic": to_float(row.get("pe")),
            "pe_ttm": to_float(row.get("pe_ttm")),
            "pb": to_float(row.get("pb")),
            "ps_ttm": to_float(row.get("ps_ttm")) or to_float(row.get("ps")),
            "market_cap": to_float(row.get("total_mv")),
            "float_market_cap": to_float(row.get("circ_mv")),
        }
        result["success"] = True
        result["notes"].append("Tushare daily_basic获取成功。")
        return result
    except Exception as e:
        result["notes"].append(f"Tushare daily_basic失败：{e}")
        return result

def fetch_auto_fundamental(code, force=False):
    result = {
        "source": [],
        "success": False,
        "pe_ttm": None,
        "pe_dynamic": None,
        "pb": None,
        "ps_ttm": None,
        "dividend_yield": None,
        "market_cap": None,
        "turnover": None,
        "volume_ratio": None,
        "roe": None,
        "revenue_growth": None,
        "profit_growth": None,
        "notes": [],
    }

    code = str(code).strip().zfill(6)

    spot_df, spot_status = get_spot_snapshot(force=force)
    if spot_df is not None and not spot_df.empty and "代码" in spot_df.columns:
        row = spot_df[spot_df["代码"].astype(str).str.zfill(6) == code]
        if not row.empty:
            latest = row.iloc[0]
            if "市盈率-动态" in spot_df.columns:
                result["pe_dynamic"] = to_float(latest["市盈率-动态"])
            if "市净率" in spot_df.columns:
                result["pb"] = to_float(latest["市净率"])
            if "总市值" in spot_df.columns:
                result["market_cap"] = to_float(latest["总市值"])
            if "换手率" in spot_df.columns:
                result["turnover"] = to_float(latest["换手率"])
            if "量比" in spot_df.columns:
                result["volume_ratio"] = to_float(latest["量比"])

            result["source"].append("AKShare实时快照")
            result["notes"].append(f"实时快照：{spot_status}")
        else:
            result["notes"].append("实时快照中未找到该股票代码。")
    else:
        result["notes"].append(f"实时快照不可用：{spot_status}")

    if AK_OK:
        try:
            val_df = ak.stock_a_lg_indicator(symbol=code)
            if val_df is not None and not val_df.empty:
                latest = val_df.iloc[-1]
                pe_col = pick_col(val_df, ["市盈率TTM"]) or pick_col(val_df, ["滚动市盈率"]) or pick_col(val_df, ["市盈率"])
                pb_col = pick_col(val_df, ["市净率"])
                ps_col = pick_col(val_df, ["市销率TTM"]) or pick_col(val_df, ["市销率"])
                dy_col = pick_col(val_df, ["股息率TTM"]) or pick_col(val_df, ["股息率"])
                mv_col = pick_col(val_df, ["总市值"])

                if result["pe_ttm"] is None and pe_col:
                    result["pe_ttm"] = to_float(latest[pe_col])
                if result["pb"] is None and pb_col:
                    result["pb"] = to_float(latest[pb_col])
                if ps_col:
                    result["ps_ttm"] = to_float(latest[ps_col])
                if dy_col:
                    result["dividend_yield"] = to_float(latest[dy_col])
                if result["market_cap"] is None and mv_col:
                    result["market_cap"] = to_float(latest[mv_col])

                result["source"].append("AKShare估值指标")
                result["notes"].append("估值指标获取成功。")
        except Exception as e:
            result["notes"].append(f"估值指标获取失败：{e}")

    if AK_OK:
        try:
            fin_df = ak.stock_financial_analysis_indicator(symbol=code, start_year="2021")
            if fin_df is not None and not fin_df.empty:
                latest = fin_df.iloc[-1]
                roe_col = pick_col(fin_df, ["加权", "净资产收益率"]) or pick_col(fin_df, ["净资产收益率"]) or pick_col(fin_df, ["ROE"])
                rev_col = pick_col(fin_df, ["主营业务收入增长率"]) or pick_col(fin_df, ["营业收入增长率"]) or pick_col(fin_df, ["营收", "增长"])
                profit_col = pick_col(fin_df, ["净利润增长率"]) or pick_col(fin_df, ["归属", "净利润", "增长"]) or pick_col(fin_df, ["净利润", "增长"])

                result["roe"] = to_float(latest[roe_col]) if roe_col else None
                result["revenue_growth"] = to_float(latest[rev_col]) if rev_col else None
                result["profit_growth"] = to_float(latest[profit_col]) if profit_col else None

                result["source"].append("AKShare财务指标")
                result["notes"].append("财务指标获取成功。")
        except Exception as e:
            result["notes"].append(f"财务指标获取失败：{e}")

    # 第四层：Tushare daily_basic 作为可选盘后兜底
    ts_basic = fetch_tushare_daily_basic(code)
    if ts_basic.get("success"):
        d = ts_basic.get("data", {})
        for k in ["pe_ttm", "pe_dynamic", "pb", "ps_ttm", "market_cap", "turnover", "volume_ratio"]:
            if result.get(k) is None and d.get(k) is not None:
                result[k] = d.get(k)
        result["source"].append("Tushare daily_basic")
    for note in ts_basic.get("notes", []):
        if "未启用" not in note:
            result["notes"].append(note)

    useful = [
        result["pe_ttm"],
        result["pe_dynamic"],
        result["pb"],
        result["market_cap"],
        result["turnover"],
        result["volume_ratio"],
        result["roe"],
        result["revenue_growth"],
        result["profit_growth"],
    ]
    result["success"] = any(x is not None for x in useful)

    if not result["success"]:
        result["notes"].append("自动基本面有效字段不足，本次不参与评分。")

    return result


# =========================
# 大资金流
# =========================

def fund_flow_market_param(code):
    m = infer_market(code)
    if m == "sh":
        return "sh"
    if m == "sz":
        return "sz"
    return m


def fetch_fund_flow(code, force=False):
    result = {
        "success": False,
        "df": pd.DataFrame(),
        "source": "AKShare个股资金流",
        "notes": [],
    }

    if not AK_OK:
        result["notes"].append("AKShare不可用，资金流无法获取。")
        return result

    code = str(code).strip().zfill(6)
    cache_file = os.path.join(FUND_FLOW_CACHE_DIR, f"{code}_fund_flow.csv")

    if os.path.exists(cache_file) and not force:
        try:
            df = pd.read_csv(cache_file)
            if df is not None and not df.empty:
                result["success"] = True
                result["df"] = df
                result["notes"].append("读取本地资金流缓存。")
                return result
        except Exception:
            pass

    # V15.1 快速模式：不强制更新时不主动请求慢速资金流接口，直接交给量价代理，避免卡死。
    # 需要真实资金流时，勾选“强制实时更新行情/快照/资金流”。
    if not force:
        result["notes"].append("未强制更新，跳过慢速真实资金流接口，使用量价代理。")
        return result

    market = fund_flow_market_param(code)

    try:
        last_err = None
        df = None
        # AKShare 不同版本/源站对 market 参数兼容性不一致，逐个尝试。
        candidates = [market, "沪深A股"]
        candidates = list(dict.fromkeys(candidates))
        for m in candidates:
            try:
                temp = ak.stock_individual_fund_flow(stock=code, market=m)
                if temp is not None and not temp.empty:
                    df = temp.copy()
                    result["notes"].append(f"资金流接口参数命中：{m}")
                    break
            except Exception as inner_e:
                last_err = inner_e
                continue

        if df is None or df.empty:
            result["notes"].append(f"资金流数据为空或接口不支持；最后错误：{last_err}")
            return result

        # 字段名兼容清洗
        rename_map = {}
        for col in df.columns:
            c = str(col).strip()
            if "主力" in c and "净额" in c:
                rename_map[col] = "主力净流入-净额"
            elif "主力" in c and "净占比" in c:
                rename_map[col] = "主力净流入-净占比"
            elif "超大单" in c and "净额" in c:
                rename_map[col] = "超大单净流入-净额"
            elif c.startswith("大单") and "净额" in c:
                rename_map[col] = "大单净流入-净额"
        if rename_map:
            df = df.rename(columns=rename_map)

        for col in df.columns:
            if col != "日期":
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "日期" in df.columns:
            df["日期"] = df["日期"].astype(str)

        df.to_csv(cache_file, index=False, encoding="utf-8-sig")
        result["success"] = True
        result["df"] = df
        result["notes"].append("资金流数据获取成功。")
        return result
    except Exception as e:
        if os.path.exists(cache_file):
            try:
                df = pd.read_csv(cache_file)
                if df is not None and not df.empty:
                    result["success"] = True
                    result["df"] = df
                    result["notes"].append(f"实时资金流失败，读取缓存：{e}")
                    return result
            except Exception:
                pass
        result["notes"].append(f"资金流获取失败：{e}")
        return result


def sum_tail(df, col, n):
    if col not in df.columns:
        return None
    temp = df[col].tail(n).dropna()
    if temp.empty:
        return None
    return float(temp.sum())


def positive_ratio_tail(df, col, n):
    if col not in df.columns:
        return None
    temp = df[col].tail(n).dropna()
    if temp.empty:
        return None
    return float((temp > 0).mean() * 100)


def analyze_proxy_fund_flow(stock_df):
    """
    真实资金流接口不可用时，使用真实行情量价做替代资金信号。
    注意：这不是主力资金流，只是量价代理，页面会明确标注。
    """
    latest = stock_df.iloc[-1]
    ret5 = latest.get("RET5", 0)
    ret20 = latest.get("RET20", 0)
    vol5 = latest.get("VOL5", None)
    vol20 = latest.get("VOL20", None)
    pct = stock_df["pct_change"].tail(10)
    vol = stock_df["volume"].tail(10)

    volume_ratio = None
    if vol20 is not None and pd.notna(vol20) and vol20 > 0:
        volume_ratio = float(vol5 / vol20)

    signed_power_5 = None
    signed_power_10 = None
    try:
        signed = pct.fillna(0) * vol.fillna(0)
        signed_power_5 = float(signed.tail(5).sum())
        signed_power_10 = float(signed.tail(10).sum())
    except Exception:
        pass

    score = 50
    reasons = ["真实资金流接口未取到，本次使用真实行情量价代理信号；它不等同于主力资金流。"]

    if volume_ratio is not None:
        if ret5 > 0 and volume_ratio >= 1.15:
            score += 14
            reasons.append("近5日上涨且量能放大，量价代理显示有进攻资金迹象。")
        elif ret5 < 0 and volume_ratio >= 1.15:
            score -= 16
            reasons.append("近5日下跌且放量，量价代理显示抛压偏大。")
        elif ret5 < 0 and volume_ratio < 0.85:
            score += 6
            reasons.append("近5日回调但缩量，抛压没有明显放大。")
        elif ret5 > 0 and volume_ratio < 0.85:
            score -= 5
            reasons.append("近5日上涨但缩量，进攻质量一般。")

    if signed_power_5 is not None:
        if signed_power_5 > 0:
            score += 8
            reasons.append("近5日量价合成资金强度为正。")
        else:
            score -= 8
            reasons.append("近5日量价合成资金强度为负。")

    if signed_power_10 is not None:
        if signed_power_10 > 0:
            score += 6
            reasons.append("近10日量价合成资金强度为正。")
        else:
            score -= 6
            reasons.append("近10日量价合成资金强度为负。")

    if ret20 < 0 and signed_power_10 is not None and signed_power_10 < 0:
        score -= 10
        divergence = "价格走弱且量价代理资金为负，偏弱。"
    elif ret5 < 0 and signed_power_5 is not None and signed_power_5 > 0:
        score += 6
        divergence = "价格回调但量价代理资金为正，可能有承接。"
    elif ret5 > 0 and signed_power_5 is not None and signed_power_5 > 0:
        divergence = "价格上涨且量价代理资金为正，趋势确认度较好。"
    else:
        divergence = "量价代理信号中性。"
    reasons.append(divergence)

    score = max(0, min(100, score))
    if score >= 70:
        grade = "量价代理偏强"
    elif score >= 55:
        grade = "量价代理中性偏正"
    elif score >= 42:
        grade = "量价代理中性"
    else:
        grade = "量价代理偏弱"

    return {
        "enabled": True,
        "is_proxy": True,
        "score": score,
        "grade": grade,
        "buy_confirm": score >= 65,
        "risk_warning": score < 42,
        "main_3": None, "main_5": None, "main_10": None, "main_20": None,
        "super_3": None, "super_5": None, "super_10": None,
        "big_5": None, "big_10": None,
        "main_pos_10": None, "main_pos_20": None, "latest_main_ratio": None,
        "proxy_volume_ratio": volume_ratio,
        "proxy_signed_power_5": signed_power_5,
        "proxy_signed_power_10": signed_power_10,
        "divergence": divergence,
        "reasons": reasons,
    }


def analyze_fund_flow(fund_result, stock_df):
    if not fund_result["success"] or fund_result["df"].empty:
        return analyze_proxy_fund_flow(stock_df)

    df = fund_result["df"].copy()

    main_col = "主力净流入-净额"
    main_ratio_col = "主力净流入-净占比"
    super_col = "超大单净流入-净额"
    big_col = "大单净流入-净额"

    latest_stock = stock_df.iloc[-1]
    ret5 = latest_stock["RET5"]
    ret20 = latest_stock["RET20"]

    main_3 = sum_tail(df, main_col, 3)
    main_5 = sum_tail(df, main_col, 5)
    main_10 = sum_tail(df, main_col, 10)
    main_20 = sum_tail(df, main_col, 20)

    super_3 = sum_tail(df, super_col, 3)
    super_5 = sum_tail(df, super_col, 5)
    super_10 = sum_tail(df, super_col, 10)

    big_5 = sum_tail(df, big_col, 5)
    big_10 = sum_tail(df, big_col, 10)

    main_pos_10 = positive_ratio_tail(df, main_col, 10)
    main_pos_20 = positive_ratio_tail(df, main_col, 20)

    latest_main_ratio = None
    if main_ratio_col in df.columns and not df[main_ratio_col].dropna().empty:
        latest_main_ratio = to_float(df[main_ratio_col].dropna().iloc[-1])

    score = 50
    reasons = []

    if main_3 is not None:
        if main_3 > 0:
            score += 8; reasons.append("近3日主力累计净流入为正，短线有资金承接。")
        else:
            score -= 8; reasons.append("近3日主力累计净流出，短线承接不足。")
    if main_5 is not None:
        if main_5 > 0:
            score += 10; reasons.append("近5日主力累计净流入为正，低吸确认度提高。")
        else:
            score -= 10; reasons.append("近5日主力累计净流出，低吸信号需要降级。")
    if main_10 is not None:
        if main_10 > 0:
            score += 10; reasons.append("近10日主力累计净流入为正，资金趋势偏改善。")
        else:
            score -= 10; reasons.append("近10日主力累计净流出，资金趋势偏弱。")
    if super_5 is not None:
        if super_5 > 0:
            score += 10; reasons.append("近5日超大单净流入为正，说明大资金有参与。")
        else:
            score -= 8; reasons.append("近5日超大单净流出，大资金参与度不足。")
    if big_10 is not None:
        if big_10 > 0:
            score += 6; reasons.append("近10日大单资金为正，机构/大户承接有所改善。")
        else:
            score -= 5; reasons.append("近10日大单资金为负，大单承接偏弱。")
    if main_pos_10 is not None:
        if main_pos_10 >= 60:
            score += 8; reasons.append(f"近10日主力净流入天数占比 {main_pos_10:.1f}%，持续性较好。")
        elif main_pos_10 <= 40:
            score -= 8; reasons.append(f"近10日主力净流入天数占比 {main_pos_10:.1f}%，持续性偏弱。")
    if latest_main_ratio is not None:
        if latest_main_ratio > 5:
            score += 6; reasons.append("最新主力净占比较高，短线资金态度偏积极。")
        elif latest_main_ratio < -5:
            score -= 6; reasons.append("最新主力净占比较低，短线资金态度偏谨慎。")

    divergence = "资金价格关系不明显"
    if ret5 < 0 and main_5 is not None and main_5 > 0:
        score += 8; divergence = "价格回调但主力净流入，可能存在承接/吸筹。"; reasons.append(divergence)
    elif ret5 > 0 and main_5 is not None and main_5 < 0:
        score -= 12; divergence = "价格上涨但主力净流出，警惕拉高派发。"; reasons.append(divergence)
    elif ret20 < 0 and main_10 is not None and main_10 < 0:
        score -= 12; divergence = "价格走弱且主力持续流出，属于弱势组合。"; reasons.append(divergence)
    elif ret20 > 0 and main_10 is not None and main_10 > 0:
        score += 8; divergence = "价格上涨且主力流入，趋势确认度较高。"; reasons.append(divergence)

    score = max(0, min(100, score))
    if score >= 75: grade = "资金强确认"
    elif score >= 60: grade = "资金偏正面"
    elif score >= 45: grade = "资金中性"
    else: grade = "资金偏负面"

    buy_confirm = score >= 60 and ((main_5 is not None and main_5 > 0) or (main_10 is not None and main_10 > 0))
    risk_warning = score < 45 or (main_5 is not None and main_10 is not None and main_5 < 0 and main_10 < 0)

    return {
        "enabled": True, "is_proxy": False, "score": score, "grade": grade,
        "buy_confirm": buy_confirm, "risk_warning": risk_warning,
        "main_3": main_3, "main_5": main_5, "main_10": main_10, "main_20": main_20,
        "super_3": super_3, "super_5": super_5, "super_10": super_10,
        "big_5": big_5, "big_10": big_10,
        "main_pos_10": main_pos_10, "main_pos_20": main_pos_20,
        "latest_main_ratio": latest_main_ratio, "divergence": divergence, "reasons": reasons,
    }

# =========================
# 市场 / 板块分析
# =========================

def analyze_market(index_df):
    latest = index_df.iloc[-1]
    score = 0
    reasons = []

    ret20 = latest["RET20"]
    ret60 = latest["RET60"]
    vol20 = latest["VOLATILITY20"]
    drawdown60 = latest["MAX_DRAWDOWN60"]

    if latest["close"] > latest["MA20"]:
        score += 15
        reasons.append("基准指数站上MA20，短线环境较好。")
    else:
        reasons.append("基准指数低于MA20，短线环境偏弱。")

    if latest["close"] > latest["MA60"]:
        score += 15
        reasons.append("基准指数站上MA60，中期环境较好。")
    else:
        reasons.append("基准指数低于MA60，中期趋势仍有压力。")

    if latest["MA20_SLOPE"] > 0:
        score += 15
        reasons.append("MA20斜率向上，短期趋势改善。")
    else:
        reasons.append("MA20斜率未向上，短期趋势改善不明显。")

    if ret20 > 5:
        score += 15
        reasons.append("基准指数近20日涨幅超过5%，短线赚钱效应较强。")
    elif ret20 > 0:
        score += 8
        reasons.append("基准指数近20日上涨，但强度一般。")
    else:
        reasons.append("基准指数近20日下跌，赚钱效应不足。")

    if ret60 > 10:
        score += 15
        reasons.append("基准指数近60日涨幅超过10%，中期趋势较强。")
    elif ret60 > 0:
        score += 8
        reasons.append("基准指数近60日上涨，但趋势强度一般。")
    else:
        reasons.append("基准指数近60日下跌，中期环境偏弱。")

    if vol20 < 1.2:
        score += 10
        reasons.append("市场20日波动率较低，环境相对稳定。")
    elif vol20 < 2.0:
        score += 6
        reasons.append("市场20日波动率中等，风险基本可控。")
    else:
        reasons.append("市场20日波动率较高，短线风险上升。")

    if drawdown60 > -3:
        score += 15
        reasons.append("基准指数距离60日高点较近，回撤压力较小。")
    elif drawdown60 > -8:
        score += 8
        reasons.append("基准指数有一定回撤，但趋势未明显破坏。")
    else:
        reasons.append("基准指数回撤较深，市场风险偏高。")

    score = min(score, 100)

    if score >= 80:
        status = "强进攻环境"
    elif score >= 65:
        status = "进攻环境"
    elif score >= 45:
        status = "震荡环境"
    else:
        status = "防守环境"

    return {
        "score": score, "status": status, "ret20": ret20, "ret60": ret60,
        "vol20": vol20, "drawdown60": drawdown60, "reasons": reasons,
    }


def analyze_relative(stock_df, index_df, benchmark_name):
    s = stock_df.iloc[-1]
    i = index_df.iloc[-1]
    excess20 = s["RET20"] - i["RET20"]
    excess60 = s["RET60"] - i["RET60"]

    if pd.isna(excess20) or pd.isna(excess60):
        return {
            "valid": False, "stock_ret20": 0, "stock_ret60": 0, "index_ret20": 0,
            "index_ret60": 0, "excess20": 0, "excess60": 0, "status": "相对强弱不可用",
            "explain": "基准指数或个股收益数据异常，本次不纳入相对强弱评分。",
        }

    if excess20 > 8 and excess60 > 8:
        status = f"显著强于{benchmark_name}"
        explain = f"20日和60日都大幅跑赢{benchmark_name}，说明资金对个股本身有明显偏好。"
    elif excess20 > 3 and excess60 > 0:
        status = f"强于{benchmark_name}"
        explain = "短中期都具备超额收益，说明个股相对强度较好。"
    elif excess20 > 0 or excess60 > 0:
        status = f"略强于{benchmark_name}"
        explain = f"至少一个周期跑赢{benchmark_name}，说明个股有一定相对强度。"
    else:
        status = f"弱于{benchmark_name}"
        explain = f"没有跑赢{benchmark_name}，说明资金更偏向其他方向。"

    return {
        "valid": True, "stock_ret20": s["RET20"], "stock_ret60": s["RET60"],
        "index_ret20": i["RET20"], "index_ret60": i["RET60"], "excess20": excess20,
        "excess60": excess60, "status": status, "explain": explain,
    }


def analyze_board_vs_stock(stock_code, board_name, board_type, stock_df, benchmark_df, force=False):
    board_name = canonical_board_name(board_name, board_type)
    if board_name in [None, "", "行业未识别", "概念未识别"]:
        return {
            "enabled": False, "board_name": board_name, "board_type": board_type, "score": None,
            "grade": "板块不可用", "rows": pd.DataFrame(), "chart_df": pd.DataFrame(),
            "reasons": ["板块无法识别，本次不展示板块对比。"],
        }

    if board_type == "industry":
        codes, source = get_board_constituents(board_name, "industry")
        if not codes and board_name in FALLBACK_SECTOR_POOLS:
            codes = FALLBACK_SECTOR_POOLS[board_name]
            source = "内置行业池"
    else:
        codes, source = get_board_constituents(board_name, "concept")

    if not codes:
        return {
            "enabled": False, "board_name": board_name, "board_type": board_type, "score": None,
            "grade": "板块成分股不可用", "rows": pd.DataFrame(), "chart_df": pd.DataFrame(),
            "reasons": [f"{board_name} 成分股无法可靠获取，本次不展示。"],
        }

    board_avg_df, rows_df, avg_status = build_board_average(codes, force=force, max_members=12, weighted=True)
    if board_avg_df is None or rows_df.empty:
        return {
            "enabled": False, "board_name": board_name, "board_type": board_type, "score": None,
            "grade": "板块行情不可用", "rows": pd.DataFrame(), "chart_df": pd.DataFrame(),
            "reasons": [f"{board_name} 板块行情样本不足，本次不展示。"],
        }

    stock_norm = stock_df[["date", "close"]].tail(120).copy()
    stock_norm["个股"] = normalize_series(stock_norm["close"])
    stock_norm = stock_norm[["date", "个股"]]

    bench_norm = benchmark_df[["date", "close"]].tail(120).copy()
    bench_norm["基准"] = normalize_series(bench_norm["close"])
    bench_norm = bench_norm[["date", "基准"]]

    chart_df = pd.merge(stock_norm, board_avg_df, on="date", how="inner")
    chart_df = pd.merge(chart_df, bench_norm, on="date", how="inner")
    display_col = f"行业：{board_name}" if board_type == "industry" else f"概念：{board_name}"
    chart_df = chart_df.rename(columns={"board_avg": display_col})

    latest = stock_df.iloc[-1]
    board_ret20 = rows_df["ret20"].mean()
    board_ret60 = rows_df["ret60"].mean()
    board_ret120 = rows_df["ret120"].mean()

    stock_ret20 = latest["RET20"]
    stock_ret60 = latest["RET60"]
    stock_ret120 = latest["RET120"]

    excess20 = stock_ret20 - board_ret20
    excess60 = stock_ret60 - board_ret60
    excess120 = stock_ret120 - board_ret120
    strong_ratio = ((rows_df["above_ma20"]) & (rows_df["above_ma60"])).mean() * 100

    rows_df["rank60"] = rows_df["ret60"].rank(ascending=False, method="min").astype(int)
    rows_df = rows_df.sort_values("rank60")

    hit = rows_df[rows_df["code"] == stock_code]
    if not hit.empty:
        rank60 = int(hit.iloc[0]["rank60"])
        total = len(rows_df)
        rank_text = f"个股在{board_name}近60日收益排名：{rank60}/{total}"
    else:
        rank60 = None
        total = len(rows_df)
        rank_text = f"个股未进入{board_name}样本池，无法计算排名。"

    score = 50
    if board_ret20 > 5:
        score += 10
    elif board_ret20 > 0:
        score += 5
    else:
        score -= 8

    if board_ret60 > 10:
        score += 12
    elif board_ret60 > 0:
        score += 6
    else:
        score -= 8

    if strong_ratio > 70:
        score += 10
    elif strong_ratio > 50:
        score += 5
    else:
        score -= 6

    if excess20 > 5:
        score += 8
    elif excess20 > 0:
        score += 4
    else:
        score -= 5

    if excess60 > 8:
        score += 10
    elif excess60 > 0:
        score += 5
    else:
        score -= 6

    score = max(0, min(100, score))

    if score >= 78:
        grade = "个股强于强势板块"
    elif score >= 62:
        grade = "个股与板块同步偏强"
    elif score >= 45:
        grade = "个股/板块中性"
    else:
        grade = "个股或板块偏弱"

    if excess20 > 0 and excess60 > 0 and board_ret60 > 0:
        relation = "个股强于板块，板块也在走强，这是较好的组合。"
    elif excess20 > 0 and excess60 > 0 and board_ret60 <= 0:
        relation = "个股强于板块，但板块本身不强，属于独立强势，持续性需要观察。"
    elif excess20 < 0 and excess60 < 0 and board_ret60 > 0:
        relation = "板块走强但个股跑输板块，说明个股在板块内掉队。"
    else:
        relation = "个股与板块强弱关系不够清晰，需要继续观察。"

    reasons = [
        f"板块来源：{source}；走势计算：{avg_status}。",
        f"{board_name}近20日平均收益 {board_ret20:.2f}%。",
        f"{board_name}近60日平均收益 {board_ret60:.2f}%。",
        f"个股近20日相对{board_name}超额收益 {excess20:.2f}%。",
        f"个股近60日相对{board_name}超额收益 {excess60:.2f}%。",
        f"板块内同时站上MA20和MA60的个股占比约 {strong_ratio:.1f}%。",
        rank_text,
        relation,
    ]

    return {
        "enabled": True, "board_name": board_name, "board_type": board_type,
        "score": score, "grade": grade, "rows": rows_df, "chart_df": chart_df,
        "board_ret20": board_ret20, "board_ret60": board_ret60, "board_ret120": board_ret120,
        "excess20": excess20, "excess60": excess60, "excess120": excess120,
        "strong_ratio": strong_ratio, "rank60": rank60, "rank_total": total, "reasons": reasons,
    }


def choose_best_concept_analysis(stock_code, concepts, stock_df, benchmark_df, force=False):
    analyses = []
    for concept in concepts[:3]:
        result = analyze_board_vs_stock(
            stock_code=stock_code, board_name=concept, board_type="concept",
            stock_df=stock_df, benchmark_df=benchmark_df, force=force,
        )
        if result["enabled"]:
            analyses.append(result)

    if not analyses:
        return {
            "enabled": False, "board_name": "概念未识别", "board_type": "concept", "score": None,
            "grade": "概念不可用", "rows": pd.DataFrame(), "chart_df": pd.DataFrame(),
            "reasons": ["没有可靠概念板块数据，本次不展示概念对比。"],
        }

    analyses = sorted(
        analyses,
        key=lambda x: (x.get("score", 0), x.get("excess60", -999), x.get("board_ret60", -999)),
        reverse=True,
    )
    return analyses[0]


def combine_stock_industry_concept_chart(stock_df, benchmark_df, industry_analysis, concept_analysis):
    stock_norm = stock_df[["date", "close"]].tail(120).copy()
    stock_norm["个股"] = normalize_series(stock_norm["close"])
    stock_norm = stock_norm[["date", "个股"]]

    bench_norm = benchmark_df[["date", "close"]].tail(120).copy()
    bench_norm["基准"] = normalize_series(bench_norm["close"])
    bench_norm = bench_norm[["date", "基准"]]

    chart = pd.merge(stock_norm, bench_norm, on="date", how="inner")

    if industry_analysis["enabled"] and not industry_analysis["chart_df"].empty:
        ind_cols = [c for c in industry_analysis["chart_df"].columns if c.startswith("行业：")]
        if ind_cols:
            ind = industry_analysis["chart_df"][["date", ind_cols[0]]].copy()
            chart = pd.merge(chart, ind, on="date", how="inner")

    if concept_analysis["enabled"] and not concept_analysis["chart_df"].empty:
        con_cols = [c for c in concept_analysis["chart_df"].columns if c.startswith("概念：")]
        if con_cols:
            con = concept_analysis["chart_df"][["date", con_cols[0]]].copy()
            chart = pd.merge(chart, con, on="date", how="inner")

    return chart


# =========================
# 风险 / 基本面 / 买点
# =========================

def risk_grade(df):
    latest = df.iloc[-1]
    volatility = latest["VOLATILITY20"]
    drawdown = latest["MAX_DRAWDOWN60"]
    atr_pct = latest["ATR14"] / latest["close"] * 100
    dist_ma60 = latest["DIST_MA60"]
    ret20 = latest["RET20"]
    ret60 = latest["RET60"]
    ret120 = latest["RET120"]

    risk_points = 0
    reasons = []

    if volatility > 5:
        risk_points += 3
        reasons.append("20日波动率很高，价格短线波动剧烈。")
    elif volatility > 3:
        risk_points += 2
        reasons.append("20日波动率偏高，需要控制仓位。")
    elif volatility > 2:
        risk_points += 1
        reasons.append("20日波动率中等。")
    else:
        reasons.append("20日波动率较低，波动风险相对可控。")

    if drawdown < -20:
        risk_points += 3
        reasons.append("近60日回撤较深，上方套牢压力和趋势修复压力较大。")
    elif drawdown < -10:
        risk_points += 2
        reasons.append("近60日存在明显回撤，结构仍需修复。")
    elif drawdown < -5:
        risk_points += 1
        reasons.append("近60日有一定回撤，但尚未明显破坏趋势。")
    else:
        reasons.append("近60日回撤可控。")

    if atr_pct > 5:
        risk_points += 3
        reasons.append("ATR占股价比例很高，日内波动风险较大。")
    elif atr_pct > 3:
        risk_points += 2
        reasons.append("ATR占股价比例偏高，止损空间需要放宽。")
    elif atr_pct > 2:
        risk_points += 1
        reasons.append("ATR占股价比例中等。")
    else:
        reasons.append("ATR占股价比例较低。")

    if latest["close"] < latest["MA20"]:
        risk_points += 2
        reasons.append("股价低于MA20，短线趋势偏弱。")
    if latest["close"] < latest["MA60"]:
        risk_points += 2
        reasons.append("股价低于MA60，中期趋势尚未修复。")

    if dist_ma60 > 15:
        risk_points += 3
        reasons.append("股价距离MA60过远，存在高位回撤风险。")
    elif dist_ma60 > 8:
        risk_points += 2
        reasons.append("股价距离MA60较远，不适合追高。")

    if ret20 > 30:
        risk_points += 2
        reasons.append("近20日涨幅过大，存在短期涨幅透支风险。")
    if ret60 > 60:
        risk_points += 3
        reasons.append("近60日涨幅过大，趋势虽强但追高风险很高。")
    elif ret60 > 35:
        risk_points += 2
        reasons.append("近60日涨幅较大，需要防止高位震荡。")
    if ret120 > 100:
        risk_points += 3
        reasons.append("近120日累计涨幅过大，存在预期透支风险。")

    if risk_points >= 11:
        level = "极高风险"
    elif risk_points >= 8:
        level = "高风险"
    elif risk_points >= 4:
        level = "中等风险"
    else:
        level = "低风险"

    safety_score = max(0, 100 - risk_points * 8)

    return {
        "risk_points": risk_points, "safety_score": safety_score, "level": level,
        "volatility": volatility, "drawdown": drawdown, "atr_pct": atr_pct,
        "dist_ma60": dist_ma60, "ret20": ret20, "ret60": ret60, "ret120": ret120,
        "reasons": reasons,
    }


def analyze_fundamental(data):
    if not data["success"]:
        return {
            "enabled": False, "score": None, "grade": "自动基本面缺失", "peg": None,
            "reasons": ["自动基本面没有获得有效字段，本次不参与评分。"],
        }

    pe = data.get("pe_ttm") or data.get("pe_dynamic")
    pb = data.get("pb")
    roe = data.get("roe")
    revenue_growth = data.get("revenue_growth")
    profit_growth = data.get("profit_growth")

    score = 40
    reasons = []

    if pe is None:
        reasons.append("PE缺失，不参与PE判断。")
    elif pe <= 0:
        score -= 10
        reasons.append("PE无效或亏损，估值参考价值较低。")
    elif pe < 20:
        score += 15
        reasons.append("PE低于20倍，估值压力相对较小。")
    elif pe < 40:
        score += 8
        reasons.append("PE处于20~40倍，估值中等，需要成长性匹配。")
    elif pe < 80:
        reasons.append("PE处于40~80倍，估值偏高，需要较强成长支撑。")
    else:
        score -= 15
        reasons.append("PE高于80倍，估值压力较大，不能只靠趋势给绿灯。")

    if pb is None:
        reasons.append("PB缺失，不参与PB判断。")
    elif pb < 3:
        score += 8
        reasons.append("PB低于3倍，资产估值压力不高。")
    elif pb < 8:
        score += 2
        reasons.append("PB处于3~8倍，需要结合行业和盈利能力判断。")
    else:
        score -= 8
        reasons.append("PB高于8倍，资产估值较贵，需要警惕回撤。")

    if roe is not None:
        if roe > 20:
            score += 12
            reasons.append("ROE高于20%，盈利质量较强。")
        elif roe > 10:
            score += 6
            reasons.append("ROE高于10%，盈利能力尚可。")
        elif roe > 0:
            reasons.append("ROE为正但不算强。")
        else:
            score -= 8
            reasons.append("ROE为负或接近无效，盈利质量有压力。")

    if profit_growth is not None:
        if profit_growth > 50:
            score += 18
            reasons.append("净利润增速高于50%，成长性较强，可以部分消化高估值。")
        elif profit_growth > 20:
            score += 10
            reasons.append("净利润增速高于20%，成长性尚可。")
        elif profit_growth > 0:
            score += 4
            reasons.append("净利润仍在增长，但增速不算强。")
        else:
            score -= 15
            reasons.append("净利润增速为负，基本面存在压力。")

    if revenue_growth is not None:
        if revenue_growth > 30:
            score += 10
            reasons.append("营收增速高于30%，需求端较强。")
        elif revenue_growth > 10:
            score += 5
            reasons.append("营收保持增长，经营趋势尚可。")
        elif revenue_growth > 0:
            reasons.append("营收小幅增长，但弹性一般。")
        else:
            score -= 8
            reasons.append("营收增速为负，收入端存在压力。")

    if pe is not None and profit_growth is not None and pe > 0 and profit_growth > 0:
        peg = pe / profit_growth
        if peg < 1:
            score += 12
            reasons.append(f"PEG约为{peg:.2f}，估值与成长匹配度较好。")
        elif peg < 2:
            score += 5
            reasons.append(f"PEG约为{peg:.2f}，估值与成长基本匹配。")
        else:
            score -= 10
            reasons.append(f"PEG约为{peg:.2f}，估值相对成长偏贵。")
    else:
        peg = None
        reasons.append("PEG无法有效计算，因为PE或利润增速缺失/无效。")

    score = max(0, min(100, score))
    if score >= 80:
        grade = "A：基本面强"
    elif score >= 65:
        grade = "B：基本面较好"
    elif score >= 50:
        grade = "C：基本面中性"
    elif score >= 35:
        grade = "D：基本面偏弱"
    else:
        grade = "E：基本面风险较高"

    return {"enabled": True, "score": score, "grade": grade, "peg": peg, "reasons": reasons}


def cluster_levels(values, tolerance):
    clean = sorted([float(x) for x in values if pd.notna(x) and x > 0])
    if not clean:
        return []
    clusters = []
    for v in clean:
        if not clusters:
            clusters.append([v])
        else:
            current = clusters[-1]
            avg = sum(current) / len(current)
            if abs(v - avg) <= tolerance:
                current.append(v)
            else:
                clusters.append([v])
    return [sum(c) / len(c) for c in clusters]


def advanced_trade_plan(df, industry_analysis, concept_analysis=None):
    latest = df.iloc[-1]
    price = latest["close"]
    ma5, ma10, ma20, ma60, ma120 = latest["MA5"], latest["MA10"], latest["MA20"], latest["MA60"], latest["MA120"]
    atr = latest["ATR14"]

    recent20 = df.tail(20)
    recent60 = df.tail(60)
    recent90 = df.tail(90)
    recent120 = df.tail(120)

    support_candidates = []
    support_candidates += list(recent20["low"].nsmallest(3))
    support_candidates += list(recent60["low"].nsmallest(5))
    support_candidates += [ma20, ma60]

    for i in range(1, len(recent90) - 1):
        if recent90.iloc[i]["low"] <= recent90.iloc[i - 1]["low"] and recent90.iloc[i]["low"] <= recent90.iloc[i + 1]["low"]:
            support_candidates.append(recent90.iloc[i]["low"])

    support_clusters = cluster_levels(support_candidates, tolerance=max(atr * 0.45, price * 0.005))
    below_supports = [x for x in support_clusters if x <= price]
    key_support = max(below_supports) if below_supports else recent20["low"].min()
    support_band_low = max(0, key_support - atr * 0.35)
    support_band_high = key_support + atr * 0.45

    resistance_candidates = []
    resistance_candidates += list(recent20["high"].nlargest(3))
    resistance_candidates += list(recent60["high"].nlargest(5))
    resistance_candidates += list(recent120["high"].nlargest(5))

    for i in range(1, len(recent90) - 1):
        if recent90.iloc[i]["high"] >= recent90.iloc[i - 1]["high"] and recent90.iloc[i]["high"] >= recent90.iloc[i + 1]["high"]:
            resistance_candidates.append(recent90.iloc[i]["high"])

    resistance_clusters = cluster_levels(resistance_candidates, tolerance=max(atr * 0.45, price * 0.005))
    above_resistances = [x for x in resistance_clusters if x >= price]
    key_resistance = min(above_resistances) if above_resistances else recent20["high"].max()

    pullback_low = key_resistance - atr * 0.45
    pullback_high = key_resistance + atr * 0.25

    stop_loss = min(price - atr * 2.0, key_support - atr * 0.65)
    hard_stop = min(price - atr * 2.8, recent60["low"].min() * 0.98)

    target1 = key_resistance
    target2 = recent60["high"].max()
    target3 = recent120["high"].max()

    volume_ratio = latest["VOL5"] / latest["VOL20"] if latest["VOL20"] and latest["VOL20"] > 0 else 1

    dist_support = (price - key_support) / price * 100
    dist_ma20 = (price - ma20) / price * 100
    dist_ma60 = (price - ma60) / price * 100
    reward_risk = (target2 - price) / (price - stop_loss) if price > stop_loss else 0

    sector_ok = True
    sector_reason = "行业数据未参与低吸判断。"
    if industry_analysis["enabled"]:
        sector_ok = industry_analysis["score"] >= 45 and industry_analysis["excess60"] >= -5
        sector_reason = "行业没有明显拖累，个股没有严重跑输行业。" if sector_ok else "行业偏弱或个股明显跑输行业，低吸有效性下降。"

    concept_ok = True
    concept_reason = "概念数据未参与低吸判断。"
    if concept_analysis is not None and concept_analysis.get("enabled"):
        concept_ok = concept_analysis["score"] >= 45 and concept_analysis["excess60"] >= -5
        concept_reason = "概念没有明显拖累，题材共振尚可。" if concept_ok else "概念偏弱或个股跑输概念，买点有效性下降。"

    trend_ok = price > ma60 or (price > ma20 and latest["MA20_SLOPE"] > 0)
    support_ok = price >= key_support and price > stop_loss
    rr_ok = reward_risk >= 1.2
    not_chasing = dist_ma20 <= 6 and dist_ma60 <= 15

    low_absorb_score = 0
    low_reasons = []

    if dist_support <= 3:
        low_absorb_score += 20
        low_reasons.append("价格接近关键支撑。")
    else:
        low_reasons.append("价格距离关键支撑不够近。")

    if trend_ok:
        low_absorb_score += 20
        low_reasons.append("趋势结构尚未明显破坏。")
    else:
        low_reasons.append("趋势结构偏弱，低吸风险较高。")

    if support_ok:
        low_absorb_score += 18
        low_reasons.append("未跌破关键支撑和风控位。")
    else:
        low_reasons.append("已经跌破关键支撑或风控位，低吸无效。")

    if rr_ok:
        low_absorb_score += 14
        low_reasons.append("盈亏比满足最低要求。")
    else:
        low_reasons.append("盈亏比不足，低吸性价比不够。")

    if sector_ok:
        low_absorb_score += 14
        low_reasons.append(sector_reason)
    else:
        low_reasons.append(sector_reason)

    if concept_ok:
        low_absorb_score += 14
        low_reasons.append(concept_reason)
    else:
        low_reasons.append(concept_reason)

    if low_absorb_score >= 80:
        low_validity = "高"
    elif low_absorb_score >= 60:
        low_validity = "中"
    elif low_absorb_score >= 40:
        low_validity = "低"
    else:
        low_validity = "无效"

    breakout_confirm = price > key_resistance and volume_ratio >= 1.2 and not_chasing and reward_risk >= 1.2
    fake_breakout_risk = price > key_resistance and volume_ratio < 1.1

    if price < stop_loss or (price < ma20 and price < ma60):
        trade_zone = "禁买区"
        new_buy = "不建议新开仓。趋势或风控结构没有修复。"
    elif dist_support <= 3 and low_validity in ["高", "中"]:
        trade_zone = "低吸有效区"
        new_buy = f"低吸有效性为{low_validity}，可观察{support_band_low:.2f}~{support_band_high:.2f}是否有承接。"
    elif dist_support <= 3 and low_validity in ["低", "无效"]:
        trade_zone = "低吸无效区"
        new_buy = "价格虽然接近支撑，但低吸条件不足，不建议因为便宜就买。"
    elif price > ma20 and price > ma60 and price < key_resistance:
        trade_zone = "趋势确认区"
        new_buy = "趋势已修复但尚未突破压力，适合已有仓位持有，新仓不宜激进。"
    elif breakout_confirm:
        trade_zone = "有效突破区"
        new_buy = "突破放量且位置不过分偏离均线，属于右侧确认信号，可小仓或分批参与。"
    elif fake_breakout_risk:
        trade_zone = "疑似假突破区"
        new_buy = "价格突破但量能不足，疑似假突破，不建议追高。"
    elif dist_ma20 > 6 or dist_ma60 > 15 or reward_risk < 1:
        trade_zone = "追高风险区"
        new_buy = "当前距离均线较远或盈亏比不足，不建议新开仓追高。"
    else:
        trade_zone = "观察区"
        new_buy = "买点不够清晰，等待低吸、回踩确认或放量突破。"

    if reward_risk < 1:
        rr_comment = "盈亏比低于1，风险收益不划算。"
    elif reward_risk < 1.5:
        rr_comment = "盈亏比一般，只适合轻仓观察。"
    elif reward_risk < 2.5:
        rr_comment = "盈亏比较合理，可以结合趋势、板块和资金情况分批操作。"
    else:
        rr_comment = "盈亏比较好，但仍需避免情绪化追高。"

    low_absorb_triggers = [
        f"价格进入 {support_band_low:.2f} ~ {support_band_high:.2f}",
        "回踩时成交量不明显放大，说明不是恐慌砸盘",
        "次日重新站回MA5或收盘价不破关键支撑",
        f"跌破 {stop_loss:.2f} 则低吸逻辑失败",
        "行业/概念不能明显走弱，个股不能持续跑输板块",
        "主力资金或超大单资金需要出现承接确认",
    ]

    breakout_triggers = [
        f"收盘价有效站上 {key_resistance:.2f}",
        "成交量至少达到20日均量的1.2倍",
        "突破后1-2天不跌回压力位下方",
        "距离MA20不能过远，否则追高风险增加",
        "突破时主力资金不能持续净流出",
    ]

    price_zone_status = "未进入低吸价格区"
    if support_band_low <= price <= support_band_high:
        price_zone_status = "已进入低吸价格区"
    elif dist_support <= 3:
        price_zone_status = "接近低吸价格区"

    price_zone_explain = f"当前价 {price:.2f}，低吸价格区为 {support_band_low:.2f} ~ {support_band_high:.2f}。"

    return {
        "price": price, "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma120": ma120,
        "atr": atr, "key_support": key_support, "support_band_low": support_band_low,
        "support_band_high": support_band_high, "key_resistance": key_resistance,
        "pullback_low": pullback_low, "pullback_high": pullback_high,
        "stop_loss": stop_loss, "hard_stop": hard_stop,
        "target1": target1, "target2": target2, "target3": target3,
        "reward_risk": reward_risk, "volume_ratio": volume_ratio,
        "dist_support": dist_support, "dist_ma20": dist_ma20, "dist_ma60": dist_ma60,
        "trade_zone": trade_zone, "new_buy": new_buy, "rr_comment": rr_comment,
        "low_validity": low_validity, "low_absorb_score": low_absorb_score,
        "low_reasons": low_reasons, "low_absorb_triggers": low_absorb_triggers,
        "breakout_triggers": breakout_triggers, "price_zone_status": price_zone_status,
        "price_zone_explain": price_zone_explain,
        "bull_case": f"若放量突破并站稳{key_resistance:.2f}，第一目标看{target2:.2f}，强趋势看{target3:.2f}。",
        "base_case": f"若回踩{support_band_low:.2f}~{support_band_high:.2f}不破，且低吸有效性不低于中，可视为低吸观察区。",
        "pullback_case": f"若突破后回踩{pullback_low:.2f}~{pullback_high:.2f}不破，可视为回踩确认买点。",
        "bear_case": f"若跌破{stop_loss:.2f}，交易假设失败；若跌破{hard_stop:.2f}，应进入强风控。",
    }


# =========================
# 评分 / 评级 / 仓位 / 买入信号
# =========================

def score_system(stock_df, market, relative, industry_analysis, concept_analysis, risk, plan, fundamental, fund_analysis):
    latest = stock_df.iloc[-1]
    components = []
    components.append(("市场环境", market["score"], 8))

    trend_score = 0
    if latest["close"] > latest["MA20"]:
        trend_score += 30
    if latest["close"] > latest["MA60"]:
        trend_score += 30
    if latest["close"] > latest["MA120"]:
        trend_score += 20
    if latest["MA5"] > latest["MA10"] > latest["MA20"]:
        trend_score += 20
    components.append(("趋势结构", min(trend_score, 100), 12))

    if relative["valid"]:
        relative_score = 50
        if relative["excess20"] > 0:
            relative_score += 20
        if relative["excess60"] > 0:
            relative_score += 20
        if relative["excess20"] > 8 and relative["excess60"] > 8:
            relative_score += 10
        components.append(("相对大盘强弱", min(relative_score, 100), 8))

    if industry_analysis["enabled"]:
        components.append(("个股相对行业", industry_analysis["score"], 16))
    if concept_analysis["enabled"]:
        components.append(("个股相对概念", concept_analysis["score"], 14))

    volume_score = 85 if latest["VOL5"] > latest["VOL20"] else 45
    components.append(("量能表现", volume_score, 6))
    components.append(("风险安全", risk["safety_score"], 14))

    trade_score = 50
    if plan["reward_risk"] >= 2:
        trade_score += 25
    elif plan["reward_risk"] >= 1.5:
        trade_score += 15
    elif plan["reward_risk"] >= 1:
        trade_score += 5
    else:
        trade_score -= 20

    if plan["trade_zone"] in ["低吸有效区", "有效突破区", "趋势确认区"]:
        trade_score += 20
    elif plan["trade_zone"] == "观察区":
        trade_score += 5
    elif plan["trade_zone"] in ["追高风险区", "禁买区", "疑似假突破区", "低吸无效区"]:
        trade_score -= 25
    components.append(("交易位置", max(0, min(100, trade_score)), 14))

    if fundamental["enabled"]:
        components.append(("自动基本面", fundamental["score"], 10))
    if fund_analysis["enabled"]:
        components.append(("大资金确认", fund_analysis["score"], 18))

    weighted_sum = sum(score * weight for _, score, weight in components)
    total_weight = sum(weight for _, _, weight in components)
    total = weighted_sum / total_weight if total_weight > 0 else 0
    return {"total": total, "components": components, "total_weight": total_weight}


def base_rating_from_score(score):
    if score >= 85:
        return "STRONG BUY", "🟢 绿灯"
    elif score >= 72:
        return "BUY", "🟢 绿灯"
    elif score >= 62:
        return "HIGH-RISK BUY", "🟡 黄绿灯"
    elif score >= 52:
        return "HOLD", "🟡 黄灯"
    elif score >= 42:
        return "REDUCE", "🟠 橙灯"
    else:
        return "SELL", "🔴 红灯"


def apply_risk_override(total, risk, plan, fundamental, industry_analysis, concept_analysis, fund_analysis):
    adjusted = total
    notes = []

    if risk["level"] in ["高风险", "极高风险"]:
        adjusted -= 10
        notes.append(f"风险等级为{risk['level']}，不能给普通绿灯。")
    if plan["trade_zone"] == "追高风险区":
        adjusted -= 12
        notes.append("当前处于追高风险区，即使趋势强，也不适合新开仓追高。")
    if plan["trade_zone"] == "禁买区":
        adjusted -= 20
        notes.append("当前处于禁买区，交易假设尚未恢复。")
    if plan["trade_zone"] == "疑似假突破区":
        adjusted -= 10
        notes.append("突破量能不足，存在假突破风险。")
    if plan["trade_zone"] == "低吸无效区":
        adjusted -= 10
        notes.append("价格接近支撑但低吸有效性不足，不能简单按低吸处理。")
    if plan["reward_risk"] < 1:
        adjusted -= 12
        notes.append("盈亏比低于1，向上空间不足以覆盖下行风险。")
    if fundamental["enabled"] and fundamental["score"] < 45:
        adjusted -= 12
        notes.append("自动基本面评分偏低，不能只凭技术面给买入信号。")
    if industry_analysis["enabled"] and industry_analysis["score"] < 40:
        adjusted -= 12
        notes.append("行业对比偏弱，个股或行业没有形成有效共振。")
    if concept_analysis["enabled"] and concept_analysis["score"] < 40:
        adjusted -= 8
        notes.append("概念板块对比偏弱，题材共振不足。")
    if fund_analysis["enabled"] and fund_analysis["risk_warning"]:
        adjusted -= 18
        notes.append("大资金流偏负面，买入信号必须降级。")

    adjusted = max(0, min(100, adjusted))
    final_rating, final_light = base_rating_from_score(adjusted)
    if final_rating in ["BUY", "STRONG BUY"] and risk["level"] in ["高风险", "极高风险"]:
        final_rating = "强势但高风险"
        final_light = "🟡 黄绿灯"
        notes.append("趋势可能较强，但风险等级较高，结论改为强势但高风险。")

    return {"adjusted_score": adjusted, "final_rating": final_rating, "final_light": final_light, "notes": notes}


def dynamic_position(final, risk, plan, industry_analysis, concept_analysis, fund_analysis):
    """
    V15 新仓比例：不再因为“缺数据”直接全部归零。
    D 只给明确风险；B 允许在量价代理确认、行业/概念不差、技术结构没破坏时给 10%-20%。
    """
    score = final["adjusted_score"]
    rr = plan.get("reward_risk", 0)

    if plan["trade_zone"] in ["禁买区", "追高风险区", "疑似假突破区", "低吸无效区"]:
        return 0, "交易区域不合格，不建议新开仓。"
    if risk["level"] == "极高风险":
        return 0, "极高风险，不建议新开仓。"
    if fund_analysis["enabled"] and fund_analysis.get("risk_warning", False) and fund_analysis.get("score", 50) < 38:
        return 0, "资金/量价信号明显偏负面，不建议新开仓。"

    industry_ok = (not industry_analysis["enabled"]) or industry_analysis["score"] >= 50
    concept_ok = (not concept_analysis["enabled"]) or concept_analysis["score"] >= 48
    sector_ok = industry_ok or concept_ok
    fund_score = fund_analysis.get("score", 50) if fund_analysis.get("enabled") else 50
    fund_ok = fund_score >= 52
    fund_strong = fund_score >= 65

    # A：较强共振，给 30%-50%，真实资金或强量价确认都可以，但真实资金更优。
    if score >= 78 and risk["level"] in ["低风险", "中等风险"] and sector_ok and fund_strong and rr >= 1.5:
        return 40, "A档候选：可分批参与，建议新仓 30%-50%，先按40%上限控制。"

    # B：实战小仓试探，不要求完美数据，但不能有硬风险。
    if score >= 58 and risk["level"] in ["低风险", "中等风险"] and sector_ok and fund_ok and rr >= 1.15:
        return 20, "B档：可小仓试探，建议新仓 10%-20%，后续放量/资金确认再加。"

    # C：只观察，不给新仓。
    if score >= 48 or plan.get("price_zone_status") in ["已进入低吸价格区", "接近低吸价格区"]:
        return 0, "C档：只观察，价格或结构有关注价值，但确认不足。"

    return 0, "D档：不建议新开仓。"


def apply_signal_priority_gate(final, risk, plan, industry_analysis, concept_analysis, fund_analysis, position_pct):
    price = plan["price"]
    in_low_price_zone = plan["support_band_low"] <= price <= plan["support_band_high"]
    near_low_price_zone = plan["dist_support"] <= 3

    if in_low_price_zone:
        price_zone_status = "已进入低吸价格区"
    elif near_low_price_zone:
        price_zone_status = "接近低吸价格区"
    else:
        price_zone_status = "未进入低吸价格区"

    plan["price_zone_status"] = price_zone_status
    plan["price_zone_explain"] = (
        f"当前价 {price:.2f}，低吸价格区为 "
        f"{plan['support_band_low']:.2f} ~ {plan['support_band_high']:.2f}。"
    )

    final_rating = final["final_rating"]
    # V16.6：这里不能再把“新仓暂时为0”或“综合评级偏弱”直接改成低吸无效。
    # 价格状态只是事实；真正能不能买，交给后面的 AI起势柱 + 买入等级判断。
    hard_block = (
        risk["level"] == "极高风险"
        or (fund_analysis["enabled"] and fund_analysis["risk_warning"] and fund_analysis.get("score", 50) < 35)
    )

    if hard_block and (in_low_price_zone or near_low_price_zone):
        plan["trade_zone"] = "价格在低吸区，但信号无效"
        plan["low_validity"] = "无效"
        plan["new_buy"] = (
            "股价确实接近或进入低吸价格区，但低吸价格区只是位置事实，不是买入指令。"
            "由于最终评级偏弱、风险等级较高、行业/概念共振不足或大资金流未确认，本次不建议新开仓。"
        )
        plan["low_reasons"].append("最终评级、风险否决或资金流优先级高于价格位置，因此低吸信号被降级为无效。")

    return plan


def generate_buy_signal(final, risk, plan, industry_analysis, concept_analysis, fund_analysis, catalyst_analysis=None):
    reasons = []
    final_rating = final["final_rating"]
    score = final["adjusted_score"]

    price_good = plan["trade_zone"] in ["低吸有效区", "趋势确认区", "有效突破区"]
    low_price = plan.get("price_zone_status", "") in ["已进入低吸价格区", "接近低吸价格区"]
    rr = plan.get("reward_risk", 0)
    rr_good = rr >= 1.15
    rr_strong = rr >= 1.5

    industry_score = industry_analysis.get("score") if industry_analysis.get("enabled") else None
    concept_score = concept_analysis.get("score") if concept_analysis.get("enabled") else None
    industry_ok = industry_score is None or industry_score >= 50
    concept_ok = concept_score is None or concept_score >= 48
    sector_ok = industry_ok or concept_ok

    fund_enabled = fund_analysis.get("enabled", False)
    fund_score = fund_analysis.get("score", 50) if fund_enabled else 50
    fund_is_proxy = fund_analysis.get("is_proxy", False)
    fund_bad = fund_enabled and fund_analysis.get("risk_warning", False) and fund_score < 38
    true_fund_ok = fund_enabled and fund_analysis.get("buy_confirm", False) and not fund_is_proxy
    proxy_fund_ok = fund_enabled and fund_analysis.get("buy_confirm", False) and fund_is_proxy
    fund_ok = true_fund_ok or proxy_fund_ok or (fund_enabled and fund_score >= 55)

    # V15：硬否决只用于明确风险，不再因为缺数据直接 D。
    # V16.6：追高风险不等于“不建议买入D”，它应该是“有起势但不追高”的C。
    # D 只留给明确破位/极高风险/资金明显负面。
    hard_block = (
        risk["level"] == "极高风险"
        or fund_bad
        or plan["trade_zone"] in ["禁买区", "疑似假突破区", "低吸无效区"]
        or (final_rating == "SELL" and score < 35 and plan.get("trade_zone") not in ["趋势确认区", "有效突破区"])
    )

    if risk["level"] == "极高风险":
        reasons.append("风险等级为极高风险，买入信号硬性降级。")
    if fund_bad:
        reasons.append("资金/量价信号明显偏负面，说明承接不足。")
    if not sector_ok:
        reasons.append("行业和概念共振不足，不能提高仓位。")
    if catalyst_medium:
        reasons.append(f"催化剂评分{catalyst_score:.0f}/100，存在题材/公告/行业催化线索。")
    else:
        reasons.append("未发现足够催化剂，买入等级不会轻易升级。")
    if not rr_good:
        reasons.append("盈亏比不足，买入性价比不够。")
    if not fund_enabled:
        reasons.append("资金数据缺失，本次不会给A，只能依靠价格、趋势和板块做保守判断。")
    elif fund_is_proxy:
        reasons.append("真实资金流未取到，本次使用量价代理；量价代理不能等同于主力资金流。")

    if hard_block:
        grade = "D"
        label = "不建议买入"
        action = "不建议新开仓。等待风险解除、重新站回关键结构，或出现明确资金回流。"
    elif price_good and true_fund_ok and sector_ok and catalyst_strong and rr_strong and score >= 72 and risk["level"] in ["低风险", "中等风险"]:
        grade = "A"
        label = "可分批买入"
        action = "买入信号较强，可分批参与，建议新仓 30%-50%，仍需分批执行。"
        reasons.append("价格位置、真实资金流、行业/概念、催化剂和盈亏比形成较好共振。")
    elif (price_good or low_price) and fund_ok and sector_ok and rr_good and score >= 58 and risk["level"] in ["低风险", "中等风险"]:
        grade = "B"
        label = "可小仓试探"
        action = "可以买观察仓或小仓试探，建议新仓 10%-20%，后续资金/放量确认再加。"
        if fund_is_proxy:
            reasons.append("由于资金使用量价代理，最高只给B档，不升级为A。")
        else:
            reasons.append("资金、价格或板块有初步配合，但还没达到重仓条件。")
    elif low_price or price_good or score >= 48:
        grade = "C"
        label = "只观察"
        action = "价格或结构值得观察，但确认不足，不建议主动建仓；等待资金、板块或突破确认。"
        reasons.append("有观察价值，但买入确认条件不足。")
    else:
        grade = "D"
        label = "不建议买入"
        action = "当前没有明确买入点，且结构/资金/板块确认不足。"

    return {"grade": grade, "label": label, "action": action, "reasons": reasons}


def generate_position_advice(final, risk, plan, position_pct, buy_signal=None):
    final_rating = final["final_rating"]
    if buy_signal and buy_signal["grade"] in ["D", "C"]:
        new_position = buy_signal["action"]
    elif final_rating in ["SELL", "REDUCE"] or position_pct <= 0:
        new_position = (
            "不建议新开仓。即使价格接近支撑，也不能自动视为低吸机会；"
            "当前总评分、风险、资金流或行业/概念共振不足，等待重新转强。"
        )
    else:
        new_position = f"新开仓建议上限约为 {position_pct}% 观察仓/分批仓，优先等待低吸有效区或回踩确认，不建议一次性买满。"

    if final_rating in ["BUY", "STRONG BUY", "HIGH-RISK BUY", "强势但高风险"]:
        existing_position = f"已有仓位可以继续观察，但跌破{plan['stop_loss']:.2f}应减仓；跌破{plan['hard_stop']:.2f}应进入强风控。接近{plan['target2']:.2f}可考虑部分止盈。"
    elif final_rating == "HOLD":
        existing_position = f"已有仓位以观察为主，不建议继续加仓。跌破{plan['stop_loss']:.2f}需要控制风险。"
    elif final_rating == "REDUCE":
        existing_position = "已有仓位建议降低一部分，等待重新站稳MA20/MA60，并且行业/概念与资金流重新走强后再评估。"
    else:
        existing_position = "已有仓位建议优先风险控制。若跌破风控位，应考虑离场或大幅降低仓位。"

    return new_position, existing_position


# =========================
# 回测
# =========================

def backtest(df):
    trades = []
    for i in range(130, len(df) - 20):
        row = df.iloc[i]
        price = row["close"]
        recent20 = df.iloc[i - 20:i]
        resistance20 = recent20["high"].max()

        signal = (
            price > row["MA20"]
            and price > row["MA60"]
            and row["MA5"] > row["MA10"] > row["MA20"]
            and price >= resistance20 * 0.98
            and row["VOL5"] > row["VOL20"]
        )
        if signal:
            future5 = df.iloc[i + 1:i + 6]
            future10 = df.iloc[i + 1:i + 11]
            future20 = df.iloc[i + 1:i + 21]
            trades.append({
                "date": row["date"], "buy_price": price,
                "return_5d": (future5.iloc[-1]["close"] - price) / price * 100,
                "return_10d": (future10.iloc[-1]["close"] - price) / price * 100,
                "return_20d": (future20.iloc[-1]["close"] - price) / price * 100,
                "max_gain_20d": (future20["high"].max() - price) / price * 100,
                "max_loss_20d": (future20["low"].min() - price) / price * 100,
            })

    bt = pd.DataFrame(trades)
    if bt.empty:
        return bt, {
            "count": 0, "win_rate_5d": 0, "win_rate_10d": 0, "win_rate_20d": 0,
            "avg_return_5d": 0, "avg_return_10d": 0, "avg_return_20d": 0,
            "avg_max_gain_20d": 0, "avg_max_loss_20d": 0, "sample_quality": "样本不足",
        }

    stats = {
        "count": len(bt),
        "win_rate_5d": (bt["return_5d"] > 0).mean() * 100,
        "win_rate_10d": (bt["return_10d"] > 0).mean() * 100,
        "win_rate_20d": (bt["return_20d"] > 0).mean() * 100,
        "avg_return_5d": bt["return_5d"].mean(),
        "avg_return_10d": bt["return_10d"].mean(),
        "avg_return_20d": bt["return_20d"].mean(),
        "avg_max_gain_20d": bt["max_gain_20d"].mean(),
        "avg_max_loss_20d": bt["max_loss_20d"].mean(),
    }
    if len(bt) >= 30:
        stats["sample_quality"] = "样本较充分"
    elif len(bt) >= 10:
        stats["sample_quality"] = "样本一般"
    else:
        stats["sample_quality"] = "样本偏少，仅供参考"
    return bt, stats




def generate_portfolio_advice(has_position, cost_price, holding_pct, trade_horizon, current_price, final, risk, plan, buy_signal):
    """根据用户持仓成本/仓位，输出已有仓位建议。"""
    if not has_position or cost_price is None or cost_price <= 0:
        return "未填写有效持仓成本，本次只给新开仓建议。"

    pnl_pct = (current_price - cost_price) / cost_price * 100
    grade = buy_signal.get("grade", "C")
    stop = plan.get("stop_loss")
    hard_stop = plan.get("hard_stop")
    resistance = plan.get("key_resistance")

    base = f"当前价 {current_price:.2f}，成本 {cost_price:.2f}，浮动盈亏 {pnl_pct:.2f}%，当前持仓约 {holding_pct:.0f}%，计划周期：{trade_horizon}。"

    if grade == "A":
        if pnl_pct < 0 and holding_pct < 60:
            action = "信号较强但仍建议分批，不要一次性补满；可等回踩不破或放量确认后小幅加仓。"
        else:
            action = "已有仓位可继续持有，接近压力位可分批止盈。"
    elif grade == "B":
        if pnl_pct < -3:
            action = "可以买入信号只到B档，已有浮亏时不建议激进补仓；先观察是否站回成本/MA20。"
        elif holding_pct < 30:
            action = "可保留观察仓，若回踩不破且资金继续改善，可小幅加仓。"
        else:
            action = "已有仓位不建议继续加大，优先持有观察。"
    elif grade == "C":
        action = "只观察，不建议补仓；等待买入信号升级到B以上再考虑加仓。"
    else:
        action = "不建议补仓，已有仓位以风险控制为主。"

    risk_line = ""
    if stop and hard_stop:
        risk_line = f" 风控：跌破 {stop:.2f} 先减仓，跌破 {hard_stop:.2f} 进入强风控。"
    target_line = ""
    if resistance:
        target_line = f" 若重新站上/突破 {resistance:.2f} 且资金改善，信号可能升级。"

    return base + action + risk_line + target_line


def data_completeness(auto_data, industry_analysis, concept_analysis, fund_analysis, stock_status, benchmark_status):
    score = 0
    items = []
    if "实时" in str(stock_status) or "腾讯" in str(stock_status):
        score += 20; items.append("行情可用")
    if auto_data.get("success"):
        score += 20; items.append("基本面/交易活跃度可用")
    if industry_analysis.get("enabled"):
        score += 18; items.append("行业对比可用")
    if concept_analysis.get("enabled"):
        score += 14; items.append("概念对比可用")
    if fund_analysis.get("enabled"):
        score += 18 if not fund_analysis.get("is_proxy", False) else 10
        items.append("真实资金流可用" if not fund_analysis.get("is_proxy", False) else "量价代理资金可用")
    if "失败" not in str(benchmark_status):
        score += 10; items.append("基准指数可用")
    return min(score, 100), "、".join(items)




# =========================
# V15.2 覆盖层：盘中交易语义修正
# =========================
# 这一层放在页面主程序前面，覆盖 V15.1 中过度保守/容易误导的函数。
# 核心变化：
# 1. 不再把“不建议新开仓”误写成“已有仓位应该卖出”。
# 2. 今日涨跌、量比、换手率进入盘中信号。
# 3. SELL/REDUCE 不再自动等于 D；只有硬风险才给 D。
# 4. 买卖点和持仓处理放到页面前面。

_old_advanced_trade_plan_v151 = advanced_trade_plan

def advanced_trade_plan(df, industry_analysis, concept_analysis):
    plan = _old_advanced_trade_plan_v151(df, industry_analysis, concept_analysis)
    latest = df.iloc[-1]
    today_pct = to_float(latest.get("pct_change"))
    vol5 = to_float(latest.get("VOL5"))
    vol20 = to_float(latest.get("VOL20"))
    live_volume_ratio = None
    if vol5 is not None and vol20 is not None and vol20 > 0:
        live_volume_ratio = vol5 / vol20
    plan["today_pct"] = today_pct
    plan["live_volume_ratio"] = live_volume_ratio

    if today_pct is None:
        intraday_status = "盘中状态不可用"
        intraday_explain = "未能从行情中识别今日涨跌幅，本次不使用盘中修正。"
    elif today_pct >= 2.0 and (live_volume_ratio is None or live_volume_ratio >= 0.85):
        intraday_status = "盘中明显修复"
        intraday_explain = f"今日涨幅约 {today_pct:.2f}%，短线正在修复；已有仓位不应机械按弱势结论处理。"
    elif today_pct >= 0.8:
        intraday_status = "盘中温和修复"
        intraday_explain = f"今日涨幅约 {today_pct:.2f}%，短线有修复迹象，但还要看量能和压力位。"
    elif today_pct <= -2.0 and (live_volume_ratio is None or live_volume_ratio >= 1.05):
        intraday_status = "盘中放量转弱"
        intraday_explain = f"今日跌幅约 {today_pct:.2f}%，且量能不弱，盘中风险上升。"
    elif today_pct < 0:
        intraday_status = "盘中偏弱"
        intraday_explain = f"今日涨跌幅约 {today_pct:.2f}%，短线偏弱。"
    else:
        intraday_status = "盘中中性"
        intraday_explain = f"今日涨跌幅约 {today_pct:.2f}%，盘中方向暂不极端。"

    plan["intraday_status"] = intraday_status
    plan["intraday_explain"] = intraday_explain
    return plan


def apply_signal_priority_gate(final, risk, plan, industry_analysis, concept_analysis, fund_analysis, position_pct):
    price = plan["price"]
    in_low_price_zone = plan["support_band_low"] <= price <= plan["support_band_high"]
    near_low_price_zone = plan["dist_support"] <= 3

    if in_low_price_zone:
        price_zone_status = "已进入低吸价格区"
    elif near_low_price_zone:
        price_zone_status = "接近低吸价格区"
    else:
        price_zone_status = "未进入低吸价格区"

    plan["price_zone_status"] = price_zone_status
    plan["price_zone_explain"] = (
        f"当前价 {price:.2f}，低吸价格区为 "
        f"{plan['support_band_low']:.2f} ~ {plan['support_band_high']:.2f}。"
    )

    # V15.2：这里不再因为 position_pct=0 就把“低吸价格区”改成无效。
    # position_pct 是买入信号之后的结果，不能反过来污染价格事实。
    final_rating = final["final_rating"]
    today_pct = plan.get("today_pct")

    true_hard_block = (
        risk["level"] == "极高风险"
        or plan["trade_zone"] in ["禁买区", "追高风险区", "疑似假突破区", "低吸无效区"]
        or (fund_analysis.get("enabled") and fund_analysis.get("risk_warning") and fund_analysis.get("score", 50) < 35)
        or (final_rating == "SELL" and final.get("adjusted_score", 50) < 35 and (today_pct is None or today_pct <= 0))
    )

    if true_hard_block and (in_low_price_zone or near_low_price_zone):
        plan["trade_zone"] = "价格在低吸区，但信号无效"
        plan["low_validity"] = "无效"
        plan["new_buy"] = (
            "股价接近或进入低吸价格区，但出现硬风险：极高风险、明显破位、资金/量价恶化或总评分过低。"
            "低吸价格区只是位置事实，不等于可以买。"
        )
        plan["low_reasons"].append("触发硬风险否决，低吸信号被降级为无效。")

    return plan


def generate_buy_signal(final, risk, plan, industry_analysis, concept_analysis, fund_analysis, catalyst_analysis=None):
    reasons = []
    final_rating = final["final_rating"]
    score = final["adjusted_score"]

    price_good = plan["trade_zone"] in ["低吸有效区", "趋势确认区", "有效突破区"]
    low_price = plan.get("price_zone_status", "") in ["已进入低吸价格区", "接近低吸价格区"]
    rr = plan.get("reward_risk", 0)
    rr_good = rr >= 1.05
    rr_strong = rr >= 1.45
    today_pct = plan.get("today_pct")
    intraday_repair = today_pct is not None and today_pct >= 0.8
    strong_repair = today_pct is not None and today_pct >= 2.0

    industry_score = industry_analysis.get("score") if industry_analysis.get("enabled") else None
    concept_score = concept_analysis.get("score") if concept_analysis.get("enabled") else None
    industry_ok = industry_score is None or industry_score >= 45
    concept_ok = concept_score is None or concept_score >= 45
    sector_ok = industry_ok or concept_ok

    fund_enabled = fund_analysis.get("enabled", False)
    fund_score = fund_analysis.get("score", 50) if fund_enabled else 50
    fund_is_proxy = fund_analysis.get("is_proxy", False)
    fund_bad = fund_enabled and fund_analysis.get("risk_warning", False) and fund_score < 35
    true_fund_ok = fund_enabled and fund_analysis.get("buy_confirm", False) and not fund_is_proxy
    proxy_fund_ok = fund_enabled and fund_analysis.get("buy_confirm", False) and fund_is_proxy
    fund_ok = true_fund_ok or proxy_fund_ok or (fund_enabled and fund_score >= 50)

    catalyst_analysis = catalyst_analysis or {"enabled": False, "score": 0, "grade": "未评估", "strong": False, "medium": False, "reasons": []}
    catalyst_score = catalyst_analysis.get("score", 0) or 0
    catalyst_strong = bool(catalyst_analysis.get("strong")) or catalyst_score >= 70
    catalyst_medium = bool(catalyst_analysis.get("medium")) or catalyst_score >= 45

    # V15.2：D 只给明确风险。REDUCE/SELL 若遇到盘中修复，不能直接当成“现在卖”。
    hard_block = (
        risk["level"] == "极高风险"
        or fund_bad
        or plan["trade_zone"] in ["禁买区", "追高风险区", "疑似假突破区", "低吸无效区", "价格在低吸区，但信号无效"]
        or (final_rating == "SELL" and score < 35 and not intraday_repair)
    )

    if risk["level"] == "极高风险":
        reasons.append("风险等级为极高风险，买入信号硬性降级。")
    if fund_bad:
        reasons.append("资金/量价信号明显偏负面，说明承接不足。")
    if final_rating in ["SELL", "REDUCE"] and intraday_repair:
        reasons.append("基础评级偏弱，但今日盘中出现修复，因此不把它直接解释成已有仓位应立即卖出。")
    if not sector_ok:
        reasons.append("行业和概念共振不足，不能提高仓位。")
    if not rr_good:
        reasons.append("盈亏比不足，买入性价比不够。")
    if not fund_enabled:
        reasons.append("资金数据缺失，本次不会给A，只能依靠价格、趋势和板块做保守判断。")
    elif fund_is_proxy:
        reasons.append("真实资金流未取到，本次使用量价代理；量价代理不能等同于主力资金流。")

    if hard_block:
        grade = "D"
        label = "不建议买入"
        action = "不建议新开仓。等待风险解除、重新站回关键结构，或出现明确资金回流。"
    elif price_good and true_fund_ok and sector_ok and rr_strong and score >= 72 and risk["level"] in ["低风险", "中等风险"]:
        grade = "A"
        label = "可分批买入"
        action = "买入信号较强，可分批参与，建议新仓 30%-50%，仍需分批执行。"
        reasons.append("价格位置、真实资金流、行业/概念和盈亏比形成较好共振。")
    elif (price_good or low_price or intraday_repair or catalyst_medium) and fund_ok and sector_ok and rr_good and score >= 48 and risk["level"] in ["低风险", "中等风险"]:
        grade = "B"
        label = "可小仓试探"
        action = "可以买观察仓或小仓试探，建议新仓 10%-20%，后续资金/放量确认再加。"
        if fund_is_proxy:
            reasons.append("由于资金使用量价代理，最高只给B档，不升级为A。")
        if intraday_repair:
            reasons.append("今日盘中修复改善了短线信号，但仍需确认是否能站稳关键位。")
    elif low_price or price_good or intraday_repair or score >= 45:
        grade = "C"
        label = "只观察"
        action = "价格、盘中表现或结构有观察价值，但确认不足，不建议主动追买。"
        reasons.append("有观察价值，但买入确认条件不足。")
    else:
        grade = "D"
        label = "不建议买入"
        action = "当前没有明确买入点，且结构/资金/板块确认不足。"

    return {"grade": grade, "label": label, "action": action, "reasons": reasons}


def generate_position_advice(final, risk, plan, position_pct, buy_signal=None):
    # V15.2：这里只表达“新开仓”，不再暗示已有仓位必须卖出。
    if buy_signal:
        if buy_signal["grade"] == "A":
            new_position = "新开仓信号为A，可分批参与，建议新仓30%-50%，但必须分批。"
        elif buy_signal["grade"] == "B":
            new_position = "新开仓信号为B，可小仓试探，建议新仓10%-20%，不建议一次性买满。"
        elif buy_signal["grade"] == "C":
            new_position = "新开仓信号为C，只观察，不追买；等待资金、行业或突破确认。"
        else:
            new_position = "新开仓信号为D，不建议买入。"
    elif position_pct > 0:
        new_position = f"新开仓建议上限约为 {position_pct}%。"
    else:
        new_position = "不建议新开仓。"

    today_pct = plan.get("today_pct")
    if risk["level"] == "极高风险":
        existing_position = "已有仓位以强风控为主，若跌破强风控线应优先减仓/离场。"
    elif today_pct is not None and today_pct >= 1.0:
        existing_position = f"已有仓位可暂持观察。今日上涨约 {today_pct:.2f}%，短线有修复，不应把“不建议新买”误解成“立刻卖出”。"
    elif final["final_rating"] == "REDUCE":
        existing_position = "已有仓位不建议加仓；若反弹无量或跌破风控位，再考虑降低仓位。"
    elif final["final_rating"] == "SELL":
        existing_position = "已有仓位需要警惕，但只有跌破风控位或放量转弱时才执行卖出/大减仓。"
    else:
        existing_position = f"已有仓位可以观察，跌破 {plan['stop_loss']:.2f} 先减仓，跌破 {plan['hard_stop']:.2f} 强风控。"

    return new_position, existing_position


def summarize_position_action(has_position, final, risk, plan, buy_signal):
    today_pct = plan.get("today_pct")
    if not has_position:
        return "未持仓"
    if risk["level"] == "极高风险":
        return "强风控"
    if today_pct is not None and today_pct >= 1.0:
        return "暂持观察"
    if buy_signal.get("grade") in ["A", "B"]:
        return "持有/小幅加"
    if final["final_rating"] == "REDUCE":
        return "不加仓"
    if final["final_rating"] == "SELL":
        return "看风控位"
    return "持有观察"


def generate_portfolio_advice(has_position, cost_price, holding_pct, trade_horizon, current_price, final, risk, plan, buy_signal):
    if not has_position or cost_price is None or cost_price <= 0:
        return "未填写有效持仓成本，本次只给新开仓建议。"

    pnl_pct = (current_price - cost_price) / cost_price * 100
    dist_to_cost = (cost_price - current_price) / current_price * 100 if current_price else 0
    grade = buy_signal.get("grade", "C")
    today_pct = plan.get("today_pct")
    stop = plan.get("stop_loss")
    hard_stop = plan.get("hard_stop")
    resistance = plan.get("key_resistance")

    base = f"当前价 {current_price:.2f}，成本 {cost_price:.2f}，浮动盈亏 {pnl_pct:.2f}%，当前持仓约 {holding_pct:.0f}%，计划周期：{trade_horizon}。"

    if risk["level"] == "极高风险":
        action = "风险等级极高，已有仓位以保护本金为主，不建议补仓。"
    elif today_pct is not None and today_pct >= 1.0 and pnl_pct > -5:
        action = "今日盘中修复，且距离成本不算失控，已有仓位可暂持观察；不建议因为基础评级偏弱就盘中割肉。"
        if current_price < cost_price:
            action += f" 若能站回成本 {cost_price:.2f} 并放量/资金改善，再考虑是否小幅加仓。"
    elif grade == "A":
        action = "买入信号较强，已有仓位可继续持有；若仓位不高，可等回踩不破或放量确认后分批加仓。"
    elif grade == "B":
        if pnl_pct < -3:
            action = "买入信号只到B档，已有浮亏时不建议激进补仓；先观察是否站回成本/MA20。"
        elif holding_pct < 30:
            action = "可保留观察仓，若回踩不破且资金继续改善，可小幅加仓。"
        else:
            action = "已有仓位不建议继续加大，优先持有观察。"
    elif grade == "C":
        action = "只观察，不建议补仓；等待买入信号升级到B以上再考虑加仓。"
    else:
        action = "不建议补仓，已有仓位看风控位处理；D代表不适合新买，不等同于必须立刻卖。"

    if pnl_pct < 0:
        action += f" 距离回本约还需要上涨 {abs(pnl_pct):.2f}%。"
    else:
        action += f" 当前已有浮盈，可关注压力位附近是否分批止盈。"

    risk_line = ""
    if stop and hard_stop:
        risk_line = f" 风控：跌破 {stop:.2f} 先减仓，跌破 {hard_stop:.2f} 进入强风控。"
    target_line = ""
    if resistance:
        target_line = f" 加仓/升级条件：站稳 {resistance:.2f} 或站回成本线，同时量能/资金继续改善。"

    return base + action + risk_line + target_line




# =========================
# V15.5 批量覆盖率检测
# =========================

def normalize_code_input(text):
    codes = []
    for part in str(text).replace("，", ",").replace("\n", ",").replace(" ", ",").split(","):
        c = part.strip()
        if not c:
            continue
        c = c[-6:] if len(c) > 6 and c[-6:].isdigit() else c
        if c.isdigit():
            codes.append(c.zfill(6))
    # 去重保序
    seen = set()
    out = []
    for c in codes:
        if c not in seen:
            out.append(c); seen.add(c)
    return out


def get_foundation_codes(limit=50, sample_mode="前N只"):
    """从本地全A底座里拿检测股票列表；没有底座就使用内置样本。"""
    codes = []
    if os.path.exists(BAOSTOCK_INDUSTRY_FILE):
        try:
            df = pd.read_csv(BAOSTOCK_INDUSTRY_FILE, dtype={"code": str})
            if df is not None and not df.empty and "code" in df.columns:
                codes = df["code"].dropna().astype(str).str.zfill(6).unique().tolist()
        except Exception:
            codes = []
    if not codes and os.path.exists(A_STOCK_MASTER_FILE):
        try:
            df = pd.read_csv(A_STOCK_MASTER_FILE, dtype={"code": str})
            if df is not None and not df.empty and "code" in df.columns:
                codes = df["code"].dropna().astype(str).str.zfill(6).unique().tolist()
        except Exception:
            codes = []
    if not codes:
        codes = [
            "000400", "002156", "300276", "300308", "600519", "300750", "600036", "002594",
            "603083", "601318", "000977", "603019", "002475", "688981", "600030", "601398",
        ]
    if sample_mode == "随机抽样":
        try:
            return pd.Series(codes).sample(min(limit, len(codes)), random_state=42).tolist()
        except Exception:
            return codes[:limit]
    return codes[:limit]


def check_one_stock_coverage(code, include_kline=True, include_basic=True, include_board=False, benchmark_df=None):
    """轻量覆盖率检测：重点看行业/概念/行情/基本面，不生成完整研报。"""
    code = str(code).zfill(6)
    row = {
        "code": code,
        "name": "",
        "industry": "行业未识别",
        "concept_count": 0,
        "concepts": "",
        "industry_ok": False,
        "concept_ok": False,
        "kline_ok": False,
        "basic_ok": False,
        "pe_pb_ok": False,
        "turnover_ok": False,
        "board_compare_ok": False,
        "score": 0,
        "status": "",
    }
    status = []
    try:
        meta = get_meta_from_master(code)
        row["name"] = meta.get("name", code)
        boards = detect_boards(code, force=False)
        industry = boards.get("industry", "行业未识别")
        concepts = boards.get("concepts", []) or []
        row["industry"] = industry
        row["concept_count"] = len(concepts)
        row["concepts"] = ", ".join(concepts[:6])
        row["industry_ok"] = industry not in [None, "", "行业未识别"]
        row["concept_ok"] = len(concepts) > 0
        if row["industry_ok"]: row["score"] += 25
        if row["concept_ok"]: row["score"] += 15
    except Exception as e:
        status.append(f"识别异常:{e}")

    if include_kline:
        try:
            df, stt = get_kline(code, force=False)
            row["kline_ok"] = df is not None and len(df) >= 130
            status.append(f"行情:{stt}")
            if row["kline_ok"]: row["score"] += 25
        except Exception as e:
            status.append(f"行情异常:{e}")
            df = None
    else:
        df = None

    if include_basic:
        try:
            auto = fetch_auto_fundamental(code, force=False)
            row["basic_ok"] = bool(auto.get("success"))
            row["pe_pb_ok"] = auto.get("pe_ttm") is not None or auto.get("pe_dynamic") is not None or auto.get("pb") is not None
            row["turnover_ok"] = auto.get("turnover") is not None or auto.get("volume_ratio") is not None
            if row["basic_ok"]: row["score"] += 15
            if row["pe_pb_ok"]: row["score"] += 10
            if row["turnover_ok"]: row["score"] += 10
        except Exception as e:
            status.append(f"基本面异常:{e}")

    if include_board and df is not None and benchmark_df is not None and row["industry_ok"]:
        try:
            ba = analyze_board_vs_stock(code, row["industry"], "industry", df, benchmark_df, force=False)
            row["board_compare_ok"] = bool(ba.get("enabled"))
            if row["board_compare_ok"]: row["score"] += 10
        except Exception as e:
            status.append(f"板块对比异常:{e}")

    row["score"] = min(int(row["score"]), 100)
    row["status"] = "；".join(status[:4])
    return row


def summarize_coverage_result(df):
    if df is None or df.empty:
        return {}
    n = len(df)
    def pct(col):
        return float(df[col].mean() * 100) if col in df.columns and n else 0.0
    return {
        "total": n,
        "industry_rate": pct("industry_ok"),
        "concept_rate": pct("concept_ok"),
        "kline_rate": pct("kline_ok"),
        "basic_rate": pct("basic_ok"),
        "pepb_rate": pct("pe_pb_ok"),
        "turnover_rate": pct("turnover_ok"),
        "board_rate": pct("board_compare_ok"),
        "avg_score": float(df["score"].mean()) if "score" in df.columns else 0.0,
    }




# =========================
# V15.7 覆盖率修正版：快照批量兜底 + 概念推断 + 快速检测
# =========================

SPOT_CACHE_TTL_SECONDS = 300
COVERAGE_SPOT_LOOKUP = None
COVERAGE_SPOT_STATUS = "未加载"


def _file_age_seconds(path):
    try:
        return time.time() - os.path.getmtime(path)
    except Exception:
        return 10**9


def get_spot_snapshot(force=False, max_age_seconds=SPOT_CACHE_TTL_SECONDS):
    """
    V15.6：全市场快照只拉一次，并带 5 分钟缓存。
    覆盖检测和单股报告都优先复用缓存，避免每只票重复请求 AKShare。
    """
    if os.path.exists(SPOT_CACHE_FILE) and not force:
        try:
            age = _file_age_seconds(SPOT_CACHE_FILE)
            df = pd.read_csv(SPOT_CACHE_FILE, dtype={"代码": str})
            if df is not None and not df.empty and "代码" in df.columns:
                df["代码"] = df["代码"].astype(str).str.zfill(6)
                if age <= max_age_seconds:
                    return df, f"读取快照缓存，约{int(age)}秒前更新"
                # 即使过期，也先返回旧缓存作为兜底；如果后续实时失败不会空。
                stale_df = df
            else:
                stale_df = None
        except Exception:
            stale_df = None
    else:
        stale_df = None

    if not AK_OK:
        if stale_df is not None:
            return stale_df, "AKShare不可用，读取过期快照缓存"
        return None, "AKShare不可用"

    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            if stale_df is not None:
                return stale_df, "AKShare快照为空，读取过期缓存"
            return None, "AKShare快照为空"
        if "代码" not in df.columns:
            if stale_df is not None:
                return stale_df, f"AKShare快照字段异常，读取过期缓存：{list(df.columns)}"
            return None, f"AKShare快照字段异常：{list(df.columns)}"

        df["代码"] = df["代码"].astype(str).str.zfill(6)
        for col in df.columns:
            if col not in ["代码", "名称"]:
                df[col] = pd.to_numeric(df[col], errors="ignore")
        numeric_cols = [
            "最新价", "涨跌幅", "涨跌额", "成交量", "成交额", "振幅", "最高", "最低", "今开", "昨收",
            "量比", "换手率", "市盈率-动态", "市净率", "总市值", "流通市值", "60日涨跌幅", "年初至今涨跌幅",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df.to_csv(SPOT_CACHE_FILE, index=False, encoding="utf-8-sig")
        return df, "AKShare全市场快照成功"
    except Exception as e:
        if stale_df is not None:
            return stale_df, f"AKShare快照失败，读取过期缓存：{e}"
        return None, f"AKShare快照失败：{e}"


def _first_value(row, candidates):
    for c in candidates:
        if c in row.index:
            v = to_float(row[c])
            if v is not None:
                return v, c
    return None, None


def build_spot_lookup(spot_df):
    lookup = {}
    if spot_df is None or spot_df.empty or "代码" not in spot_df.columns:
        return lookup
    for _, r in spot_df.iterrows():
        code = str(r.get("代码", "")).zfill(6)
        if len(code) != 6:
            continue
        latest, latest_col = _first_value(r, ["最新价", "现价", "最新", "收盘", "收盘价"])
        pe_dyn, pe_col = _first_value(r, ["市盈率-动态", "动态市盈率", "市盈率", "PE", "pe"])
        pb, pb_col = _first_value(r, ["市净率", "PB", "pb"])
        turnover, turnover_col = _first_value(r, ["换手率", "换手", "turnover_rate", "turnover"])
        vol_ratio, vol_ratio_col = _first_value(r, ["量比", "volume_ratio"])
        mv, mv_col = _first_value(r, ["总市值", "总市值(元)", "market_cap", "total_mv"])
        amount, amount_col = _first_value(r, ["成交额", "成交金额", "amount"])
        pct, pct_col = _first_value(r, ["涨跌幅", "pct_chg", "change_pct"])
        name = str(r.get("名称", "")) if "名称" in r.index else ""
        lookup[code] = {
            "name": name,
            "latest": latest,
            "pct_chg": pct,
            "pe_dynamic": pe_dyn,
            "pb": pb,
            "turnover": turnover,
            "volume_ratio": vol_ratio,
            "market_cap": mv,
            "amount": amount,
            "pepb_source": pe_col or pb_col or "",
            "turnover_source": turnover_col or vol_ratio_col or "",
            "quote_source": latest_col or pct_col or amount_col or "",
        }
    return lookup




# =========================
# V15.7：东方财富批量行情/基础数据兜底
# 解决 AKShare 全市场快照失败时 PE/PB、换手率、量比全部为 0 的问题。
# =========================
EASTMONEY_BATCH_CACHE_FILE = os.path.join(CACHE_DIR, "eastmoney_batch_quote_cache.csv")
EASTMONEY_BATCH_TTL_SECONDS = 180


def eastmoney_secid(code):
    code = str(code).zfill(6)
    # 东方财富 secid: 沪市=1，深市=0，北交所常用=0/2；这里先用常见规则，失败不阻塞。
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return f"1.{code}"
    return f"0.{code}"


def get_eastmoney_batch_quotes(codes, force=False, max_age_seconds=EASTMONEY_BATCH_TTL_SECONDS):
    """
    批量获取东方财富实时快照。一次批量拿 PE/PB/换手率/量比/市值，
    专门用于覆盖率检测和 AKShare spot 失败时的基础数据兜底。
    """
    codes = [str(c).zfill(6) for c in codes if str(c).strip()]
    codes = list(dict.fromkeys(codes))
    if not codes:
        return {}, "无代码"

    # 如果缓存仍新，优先读取缓存，但只返回本次需要的代码。
    if os.path.exists(EASTMONEY_BATCH_CACHE_FILE) and not force:
        try:
            age = _file_age_seconds(EASTMONEY_BATCH_CACHE_FILE)
            df_cache = pd.read_csv(EASTMONEY_BATCH_CACHE_FILE, dtype={"code": str})
            if age <= max_age_seconds and df_cache is not None and not df_cache.empty:
                df_cache["code"] = df_cache["code"].astype(str).str.zfill(6)
                hit = df_cache[df_cache["code"].isin(codes)]
                if len(hit) >= max(1, int(len(codes) * 0.6)):
                    lookup = {}
                    for _, r in hit.iterrows():
                        lookup[str(r["code"]).zfill(6)] = {
                            "name": str(r.get("name", "")),
                            "latest": to_float(r.get("latest")),
                            "pct_chg": to_float(r.get("pct_chg")),
                            "amount": to_float(r.get("amount")),
                            "pe_dynamic": to_float(r.get("pe_dynamic")),
                            "pb": to_float(r.get("pb")),
                            "turnover": to_float(r.get("turnover")),
                            "volume_ratio": to_float(r.get("volume_ratio")),
                            "market_cap": to_float(r.get("market_cap")),
                            "float_market_cap": to_float(r.get("float_market_cap")),
                            "pepb_source": "东方财富批量缓存",
                            "turnover_source": "东方财富批量缓存",
                            "quote_source": "东方财富批量缓存",
                        }
                    return lookup, f"读取东方财富批量缓存，约{int(age)}秒前更新"
        except Exception:
            pass

    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    fields = "f12,f14,f2,f3,f5,f6,f8,f9,f10,f20,f21,f23"
    rows = []
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

    for i in range(0, len(codes), 80):
        batch = codes[i:i+80]
        secids = ",".join(eastmoney_secid(c) for c in batch)
        params = {"fltt": "2", "invt": "2", "fields": fields, "secids": secids}
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=8)
            js = resp.json()
            diff = js.get("data", {}).get("diff", []) or []
            for d in diff:
                code = str(d.get("f12", "")).zfill(6)
                if len(code) != 6:
                    continue
                rows.append({
                    "code": code,
                    "name": d.get("f14"),
                    "latest": d.get("f2"),
                    "pct_chg": d.get("f3"),
                    "volume": d.get("f5"),
                    "amount": d.get("f6"),
                    "turnover": d.get("f8"),
                    "pe_dynamic": d.get("f9"),
                    "volume_ratio": d.get("f10"),
                    "market_cap": d.get("f20"),
                    "float_market_cap": d.get("f21"),
                    "pb": d.get("f23"),
                })
        except Exception:
            continue
        time.sleep(0.05)

    if not rows:
        return {}, "东方财富批量快照失败"

    df = pd.DataFrame(rows).drop_duplicates(subset=["code"])
    # 更新缓存：这里缓存本次批量结果；不覆盖长期主库，不影响报告。
    try:
        df.to_csv(EASTMONEY_BATCH_CACHE_FILE, index=False, encoding="utf-8-sig")
    except Exception:
        pass

    lookup = {}
    for _, r in df.iterrows():
        code = str(r["code"]).zfill(6)
        lookup[code] = {
            "name": str(r.get("name", "")),
            "latest": to_float(r.get("latest")),
            "pct_chg": to_float(r.get("pct_chg")),
            "amount": to_float(r.get("amount")),
            "pe_dynamic": to_float(r.get("pe_dynamic")),
            "pb": to_float(r.get("pb")),
            "turnover": to_float(r.get("turnover")),
            "volume_ratio": to_float(r.get("volume_ratio")),
            "market_cap": to_float(r.get("market_cap")),
            "float_market_cap": to_float(r.get("float_market_cap")),
            "pepb_source": "东方财富批量快照",
            "turnover_source": "东方财富批量快照",
            "quote_source": "东方财富批量快照",
        }
    return lookup, f"东方财富批量快照成功，覆盖{len(lookup)}只"


def merge_quote_lookups(primary, secondary):
    """secondary 作为兜底，primary 为空字段时补进去。"""
    primary = primary or {}
    secondary = secondary or {}
    merged = dict(primary)
    for code, s in secondary.items():
        if code not in merged or not merged.get(code):
            merged[code] = s
            continue
        m = dict(merged[code])
        for k, v in s.items():
            if m.get(k) in [None, "", "无"] and v not in [None, "", "无"]:
                m[k] = v
        merged[code] = m
    return merged


def load_eastmoney_stale_cache_lookup(codes):
    """
    V15.8.1 兜底1：东方财富实时请求失败时，允许读取旧缓存。
    旧缓存不会标成实时，只用于覆盖率检测，避免主数据源短暂断开时 PE/PB、换手率、量比直接归零。
    """
    codes = [str(c).zfill(6) for c in codes if str(c).strip()]
    if not os.path.exists(EASTMONEY_BATCH_CACHE_FILE):
        return {}, "无东方财富历史缓存"
    try:
        age = _file_age_seconds(EASTMONEY_BATCH_CACHE_FILE)
        df_cache = pd.read_csv(EASTMONEY_BATCH_CACHE_FILE, dtype={"code": str})
        if df_cache is None or df_cache.empty or "code" not in df_cache.columns:
            return {}, "东方财富历史缓存为空"
        df_cache["code"] = df_cache["code"].astype(str).str.zfill(6)
        hit = df_cache[df_cache["code"].isin(codes)]
        lookup = {}
        for _, r in hit.iterrows():
            code = str(r["code"]).zfill(6)
            lookup[code] = {
                "name": str(r.get("name", "")),
                "latest": to_float(r.get("latest")),
                "pct_chg": to_float(r.get("pct_chg")),
                "amount": to_float(r.get("amount")),
                "pe_dynamic": to_float(r.get("pe_dynamic")),
                "pb": to_float(r.get("pb")),
                "turnover": to_float(r.get("turnover")),
                "volume_ratio": to_float(r.get("volume_ratio")),
                "market_cap": to_float(r.get("market_cap")),
                "float_market_cap": to_float(r.get("float_market_cap")),
                "pepb_source": f"东方财富历史缓存({int(age)}秒前)",
                "turnover_source": f"东方财富历史缓存({int(age)}秒前)",
                "quote_source": f"东方财富历史缓存({int(age)}秒前)",
            }
        return lookup, f"读取东方财富历史缓存，约{int(age)}秒前，命中{len(lookup)}只"
    except Exception as e:
        return {}, f"东方财富历史缓存读取失败：{e}"


def maybe_load_ak_spot_for_missing(codes, current_lookup, threshold=0.80):
    """
    V15.8.1 兜底2：只有东方财富覆盖率不足时才调用 AKShare 全市场快照。
    这样不会出现主源成功但页面还刷 AKShare RemoteDisconnected 的问题。
    """
    codes = [str(c).zfill(6) for c in codes if str(c).strip()]
    current_lookup = current_lookup or {}
    if not codes:
        return {}, "无代码"
    coverage = len([c for c in codes if c in current_lookup]) / max(len(codes), 1)
    if coverage >= threshold:
        return {}, f"东方财富覆盖率{coverage*100:.1f}%，跳过AKShare备用源"
    spot_df, spot_status = get_spot_snapshot(force=False, max_age_seconds=SPOT_CACHE_TTL_SECONDS)
    ak_lookup = build_spot_lookup(spot_df)
    return ak_lookup, spot_status

INDUSTRY_DEFAULT_CONCEPTS_V156 = {
    "电网设备": ["特高压", "智能电网", "虚拟电厂"],
    "通信设备": ["光模块", "CPO", "算力"],
    "计算机设备": ["算力", "数据中心", "人工智能"],
    "软件开发": ["人工智能", "信创", "数据要素"],
    "半导体": ["集成电路", "先进封装", "Chiplet", "国产芯片"],
    "电子元件": ["消费电子", "半导体", "PCB"],
    "消费电子": ["消费电子", "苹果概念", "AI手机"],
    "专用设备": ["机器人", "工业4.0", "智能制造"],
    "通用设备": ["机器人", "工业4.0", "智能制造"],
    "机器人": ["机器人", "工业4.0", "智能物流"],
    "汽车整车": ["新能源车", "智能驾驶"],
    "汽车零部件": ["新能源车", "智能驾驶", "汽车零部件"],
    "电池": ["动力电池", "储能", "新能源车"],
    "光伏设备": ["光伏", "新能源"],
    "风电设备": ["风电", "新能源"],
    "储能": ["储能", "新能源"],
    "酿酒行业": ["白酒"],
    "食品饮料": ["大消费", "食品饮料"],
    "银行": ["银行", "中特估"],
    "证券": ["证券", "金融科技"],
    "保险": ["保险", "大金融"],
    "房地产开发": ["房地产", "城中村改造"],
    "建筑装饰": ["基建", "一带一路"],
    "工程机械": ["工程机械", "一带一路"],
    "煤炭行业": ["煤炭", "高股息"],
    "石油行业": ["石油", "高股息"],
    "有色金属": ["有色金属", "稀缺资源"],
    "钢铁行业": ["钢铁", "中特估"],
    "化学制品": ["化工", "新材料"],
    "化学制药": ["医药", "创新药"],
    "中药": ["中药", "医药"],
    "医疗器械": ["医疗器械", "医药"],
    "电力行业": ["电力", "绿色电力", "高股息"],
    "环保行业": ["环保", "碳中和"],
    "传媒": ["传媒", "AIGC"],
    "游戏": ["游戏", "传媒"],
    "互联网服务": ["互联网", "人工智能", "数据要素"],
    "物流行业": ["物流", "统一大市场"],
    "港口航运": ["航运", "港口", "一带一路"],
    "家电行业": ["家电", "消费"],
    "旅游酒店": ["旅游", "消费"],
}

INDUSTRY_KEYWORD_CONCEPTS_V156 = [
    (["医药", "药", "制药", "生物"], ["医药", "创新药"]),
    (["半导体", "集成电路", "芯片"], ["半导体", "集成电路", "国产芯片"]),
    (["环保", "环境"], ["环保", "碳中和"]),
    (["设备", "装备", "自动化"], ["智能制造", "工业4.0"]),
    (["通信", "光"], ["通信设备", "算力"]),
    (["电力", "电气", "电网"], ["智能电网", "特高压"]),
    (["汽车"], ["新能源车", "智能驾驶"]),
    (["银行"], ["银行"]),
    (["证券"], ["证券"]),
    (["食品", "饮料", "酒"], ["大消费"]),
]


def infer_concepts_from_industry(industry, name=""):
    industry = str(industry or "")
    name = str(name or "")
    concepts = []
    base = canonical_board_name(industry, "industry")
    concepts.extend(INDUSTRY_DEFAULT_CONCEPTS_V156.get(base, []))
    text = industry + " " + name
    for keys, vals in INDUSTRY_KEYWORD_CONCEPTS_V156:
        if any(k in text for k in keys):
            concepts.extend(vals)
    return list(dict.fromkeys([c for c in concepts if c]))[:6]


_ORIGINAL_DETECT_BOARDS_V156 = detect_boards

def detect_boards(code, force=False):
    boards = _ORIGINAL_DETECT_BOARDS_V156(code, force=force)
    if not boards.get("concepts"):
        meta = get_meta_from_master(code)
        for c in infer_concepts_from_industry(boards.get("industry"), meta.get("name", "")):
            if c not in boards["concepts"]:
                boards["concepts"].append(c)
        boards["status"].append("V15.6行业关键词补充概念")
    boards["concepts"] = list(dict.fromkeys([canonical_board_name(c, "concept") for c in boards.get("concepts", []) if c]))[:8]
    return boards


def check_one_stock_coverage(code, include_kline=True, include_basic=True, include_board=False, benchmark_df=None):
    """
    V15.6 快速覆盖检测：
    - 行情/PEPB/换手量比优先从一次性全市场快照查，不再每只票完整跑研报；
    - 概念用本地库 + 行业关键词兜底；
    - 标准模式才额外跑行业对比。
    """
    global COVERAGE_SPOT_LOOKUP, COVERAGE_SPOT_STATUS
    code = str(code).zfill(6)
    row = {
        "code": code, "name": "", "industry": "行业未识别", "concept_count": 0, "concepts": "",
        "industry_ok": False, "concept_ok": False, "kline_ok": False, "basic_ok": False,
        "pe_pb_ok": False, "turnover_ok": False, "board_compare_ok": False,
        "quote_source": "", "pepb_source": "", "turnover_source": "", "industry_source": "", "concept_source": "",
        "score": 0, "status": "",
    }
    status = []

    if COVERAGE_SPOT_LOOKUP is None:
        spot_df, spot_status = get_spot_snapshot(force=False, max_age_seconds=SPOT_CACHE_TTL_SECONDS)
        COVERAGE_SPOT_LOOKUP = build_spot_lookup(spot_df)
        COVERAGE_SPOT_STATUS = spot_status

    spot = COVERAGE_SPOT_LOOKUP.get(code, {}) if isinstance(COVERAGE_SPOT_LOOKUP, dict) else {}

    try:
        meta = get_meta_from_master(code)
        row["name"] = spot.get("name") or meta.get("name", code)
        boards = detect_boards(code, force=False)
        industry = boards.get("industry", "行业未识别")
        concepts = boards.get("concepts", []) or []
        row["industry"] = industry
        row["concept_count"] = len(concepts)
        row["concepts"] = ", ".join(concepts[:6])
        row["industry_ok"] = industry not in [None, "", "行业未识别"]
        row["concept_ok"] = len(concepts) > 0
        row["industry_source"] = "BaoStock/本地库/兜底"
        row["concept_source"] = "本地概念库/行业关键词兜底" if row["concept_ok"] else "未命中"
        if row["industry_ok"]: row["score"] += 25
        if row["concept_ok"]: row["score"] += 15
    except Exception as e:
        status.append(f"识别异常:{e}")

    # 覆盖检测的行情成功：优先用全市场快照的现价/涨跌幅/成交额，而不是每只票单独拉K线。
    row["kline_ok"] = spot.get("latest") is not None or spot.get("pct_chg") is not None or spot.get("amount") is not None
    row["quote_source"] = spot.get("quote_source") or COVERAGE_SPOT_STATUS
    if row["kline_ok"]:
        row["score"] += 25
    elif include_kline:
        # 快照没有该票时，才兜底尝试本地/腾讯K线；尽量避免批量检测慢。
        try:
            df, stt = get_kline(code, force=False)
            row["kline_ok"] = df is not None and len(df) >= 60
            row["quote_source"] = stt
            if row["kline_ok"]: row["score"] += 25
        except Exception as e:
            status.append(f"行情异常:{e}")

    if include_basic:
        row["pe_pb_ok"] = spot.get("pe_dynamic") is not None or spot.get("pb") is not None
        row["turnover_ok"] = spot.get("turnover") is not None or spot.get("volume_ratio") is not None
        row["basic_ok"] = row["pe_pb_ok"] or row["turnover_ok"] or spot.get("market_cap") is not None
        row["pepb_source"] = spot.get("pepb_source") or ("无" if not row["pe_pb_ok"] else "快照")
        row["turnover_source"] = spot.get("turnover_source") or ("无" if not row["turnover_ok"] else "快照")
        if row["basic_ok"]: row["score"] += 15
        if row["pe_pb_ok"]: row["score"] += 10
        if row["turnover_ok"]: row["score"] += 10

    if include_board and row["industry_ok"]:
        try:
            # 标准模式：只检查是否能找到同行样本，不完整跑行业曲线，避免批量极慢。
            codes, src = get_board_constituents(row["industry"], "industry")
            row["board_compare_ok"] = len(codes) >= 3
            if row["board_compare_ok"]: row["score"] += 10
            status.append(f"同行样本:{len(codes)} {src}")
        except Exception as e:
            status.append(f"板块对比异常:{e}")

    row["score"] = min(int(row["score"]), 100)
    row["status"] = "；".join(status[:4])
    return row


def summarize_coverage_result(df):
    if df is None or df.empty:
        return {}
    n = len(df)
    def pct(col):
        return float(df[col].mean() * 100) if col in df.columns and n else 0.0
    return {
        "total": n,
        "industry_rate": pct("industry_ok"),
        "concept_rate": pct("concept_ok"),
        "kline_rate": pct("kline_ok"),
        "basic_rate": pct("basic_ok"),
        "pepb_rate": pct("pe_pb_ok"),
        "turnover_rate": pct("turnover_ok"),
        "board_rate": pct("board_compare_ok"),
        "avg_score": float(df["score"].mean()) if "score" in df.columns else 0.0,
    }





# =========================
# V16.6 催化剂评分 / 机会扫描
# =========================

CATALYST_CACHE_DIR = os.path.join(CACHE_DIR, "catalyst")
os.makedirs(CATALYST_CACHE_DIR, exist_ok=True)

STRONG_CATALYST_KEYWORDS = [
    "预增", "大幅增长", "扭亏", "中标", "重大合同", "订单", "回购", "增持", "并购", "重组",
    "定增", "股权激励", "产品通过", "量产", "突破", "国产替代", "英伟达", "AI", "算力",
    "特高压", "机器人", "低空经济", "先进封装", "Chiplet", "CPO", "光模块", "固态电池",
]
NEGATIVE_CATALYST_KEYWORDS = [
    "减持", "立案", "调查", "处罚", "亏损", "预亏", "下滑", "终止", "延期", "风险提示", "退市", "诉讼",
]
MEDIUM_CATALYST_KEYWORDS = [
    "调研", "机构", "研报", "政策", "合作", "签署", "项目", "扩产", "投产", "景气", "涨价", "产业链",
]

HOT_CONCEPT_WEIGHTS = {
    "AI": 10, "算力": 10, "CPO": 10, "光模块": 10, "机器人": 9, "人形机器人": 9,
    "低空经济": 9, "先进封装": 9, "Chiplet": 9, "集成电路": 8, "国产芯片": 8,
    "特高压": 8, "智能电网": 8, "虚拟电厂": 7, "固态电池": 8, "新能源车": 7,
    "创新药": 7, "军工": 7, "数据中心": 8, "液冷服务器": 8,
}


def _safe_json_load(path):
    try:
        import json
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None


def _safe_json_dump(path, obj):
    try:
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def fetch_cninfo_announcements_light(code, name="", days=30, force=False):
    """轻量抓巨潮公告标题。失败不影响报告；只做催化辅助。"""
    code = str(code).zfill(6)
    cache_file = os.path.join(CATALYST_CACHE_DIR, f"cninfo_{code}.json")
    if not force:
        cached = _safe_json_load(cache_file)
        if cached and time.time() - cached.get("ts", 0) < 6 * 3600:
            return cached.get("items", []), "巨潮公告缓存"
    try:
        import datetime
        end = datetime.date.today()
        start = end - datetime.timedelta(days=int(days))
        url = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        }
        data = {
            "stock": f"{code},{name}",
            "searchkey": "",
            "plate": "",
            "category": "",
            "trade": "",
            "column": "szse" if infer_market(code) == "sz" else "sse",
            "pageNum": 1,
            "pageSize": 20,
            "tabName": "fulltext",
            "sortName": "",
            "sortType": "",
            "limit": "",
            "seDate": f"{start}~{end}",
        }
        r = requests.post(url, headers=headers, data=data, timeout=5)
        js = r.json()
        anns = js.get("announcements") or []
        items = []
        for a in anns[:20]:
            title = str(a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
            date = str(a.get("announcementTime") or "")
            items.append({"date": date[:10], "title": title, "source": "巨潮公告"})
        _safe_json_dump(cache_file, {"ts": time.time(), "items": items})
        return items, "巨潮公告"
    except Exception as e:
        return [], f"公告抓取失败:{str(e)[:60]}"


def classify_catalyst_items(items):
    score = 0
    reasons = []
    pos_count = neg_count = mid_count = 0
    for it in items or []:
        title = str(it.get("title", ""))
        if not title:
            continue
        if any(k in title for k in NEGATIVE_CATALYST_KEYWORDS):
            neg_count += 1
        if any(k in title for k in STRONG_CATALYST_KEYWORDS):
            pos_count += 1
        elif any(k in title for k in MEDIUM_CATALYST_KEYWORDS):
            mid_count += 1
    if pos_count:
        add = min(35, 18 + pos_count * 6)
        score += add
        reasons.append(f"近30日公告/新闻命中强催化关键词 {pos_count} 条。")
    if mid_count:
        add = min(18, 8 + mid_count * 3)
        score += add
        reasons.append(f"近30日公告/新闻命中中等催化关键词 {mid_count} 条。")
    if neg_count:
        sub = min(35, 15 + neg_count * 6)
        score -= sub
        reasons.append(f"近30日公告/新闻命中风险关键词 {neg_count} 条，催化分扣减。")
    return score, reasons


def analyze_catalyst(code, name, industry, concepts, qishi=None, fund_analysis=None, concept_analysis=None, force=False, fetch_online=True):
    """V16.6 催化剂评分：公告/题材/行业热度 + 起势配合。"""
    concepts = list(dict.fromkeys([str(c) for c in (concepts or []) if c]))[:8]
    score = 20
    reasons = []
    items = []
    source_parts = ["行业/概念题材"]

    # 题材概念分：不是新闻，但能说明该股是否处在活跃主题里。
    hot_points = 0
    hot_hits = []
    for c in concepts:
        for k, w in HOT_CONCEPT_WEIGHTS.items():
            if k in c:
                hot_points += w
                hot_hits.append(c)
                break
    if hot_hits:
        score += min(25, hot_points)
        reasons.append("命中热门题材/概念：" + "、".join(list(dict.fromkeys(hot_hits))[:5]) + "。")

    # 概念/板块分数辅助。
    if concept_analysis and concept_analysis.get("enabled"):
        cs = concept_analysis.get("score") or 50
        if cs >= 70:
            score += 15; reasons.append("最相关概念板块强度较高。")
        elif cs >= 55:
            score += 8; reasons.append("最相关概念板块不弱。")
        elif cs < 40:
            score -= 8; reasons.append("概念板块强度偏弱。")

    # 起势确认与催化联动。
    if qishi and qishi.get("enabled"):
        level = qishi.get("level")
        if level in ["red", "deepred"]:
            score += 14; reasons.append("AI起势柱已进入红系趋势，说明价格已响应催化/资金。")
        elif level == "lightred":
            score += 10; reasons.append("AI起势进入浅红确认，催化有效性提高。")
        elif level == "yellow":
            score += 5; reasons.append("AI起势出现黄柱异动，可能处于催化早期。")

    # 资金/量价确认。
    if fund_analysis and fund_analysis.get("enabled"):
        fs = fund_analysis.get("score") or 50
        if fs >= 70:
            score += 12; reasons.append("资金/量价确认偏强，催化落地质量提高。")
        elif fs >= 58:
            score += 6; reasons.append("资金/量价确认中性偏正。")
        elif fs < 40:
            score -= 10; reasons.append("资金/量价确认偏弱，催化可能只是短线噪音。")

    if fetch_online:
        ann_items, ann_source = fetch_cninfo_announcements_light(code, name, force=force)
        source_parts.append(ann_source)
        items.extend(ann_items)
        ann_score, ann_reasons = classify_catalyst_items(ann_items)
        score += ann_score
        reasons.extend(ann_reasons)

    score = max(0, min(100, int(score)))
    if score >= 75:
        grade = "强催化"
    elif score >= 55:
        grade = "中等偏强"
    elif score >= 38:
        grade = "弱催化/题材跟随"
    else:
        grade = "未发现有效催化"
    return {
        "enabled": True,
        "score": score,
        "grade": grade,
        "strong": score >= 75,
        "medium": score >= 55,
        "reasons": reasons[:10] if reasons else ["未发现明确公告利好；主要依赖起势柱和行业/概念判断。"],
        "items": items[:30],
        "source": " + ".join(source_parts),
    }


def apply_catalyst_to_buy_signal(buy_signal, qishi, catalyst, risk, plan, fund_analysis):
    """把催化剂加入买入等级：A严格，B更实战。"""
    catalyst = catalyst or {"score": 0, "grade": "未评估", "reasons": []}
    qishi = qishi or {"level": "none", "score": 0, "high_risk_chase": False, "risk_score": 0}
    grade = buy_signal.get("grade", "C")
    reasons = list(buy_signal.get("reasons", []))
    cscore = catalyst.get("score", 0) or 0
    q_level = qishi.get("level", "none")
    q_score = qishi.get("score", 0) or 0
    high_chase = bool(qishi.get("high_risk_chase"))
    fund_score = fund_analysis.get("score", 50) if fund_analysis else 50
    is_proxy = fund_analysis.get("is_proxy", True) if fund_analysis else True
    rr = plan.get("reward_risk", 0)
    hard_bad = risk.get("level") == "极高风险" or plan.get("trade_zone") in ["禁买区", "低吸无效区", "疑似假突破区"]

    # A 档：严格。必须强催化 + 红/深红 + 不追高 + 资金/量价强 + 位置不差。
    if not hard_bad and q_level in ["red", "deepred"] and q_score >= 72 and cscore >= 75 and fund_score >= 68 and rr >= 1.25 and not high_chase and risk.get("level") in ["低风险", "中等风险"]:
        if not is_proxy or fund_score >= 76:
            grade = "A"
            label = "可分批买入"
            action = "A档严格触发：起势红柱、催化较强、资金/量价确认、位置不过热；可分批参与，建议新仓30%-50%。"
            reasons.append("A档触发：强催化 + 红柱起势 + 资金确认 + 非高位追涨。")
        else:
            grade = "B"
            label = "可小仓试探"
            action = "起势和催化较强，但资金仍为量价代理，最高给B；建议10%-20%小仓试探。"
            reasons.append("催化较强但真实资金不足，限制为B。")
    # B 档：实战可用。不要求所有条件完美。
    elif not hard_bad and grade in ["C", "D"] and q_level in ["lightred", "red", "deepred"] and cscore >= 45 and fund_score >= 50 and not (high_chase and q_level == "deepred"):
        grade = "B"
        label = "可小仓试探"
        action = "起势柱确认且存在催化/题材线索，可小仓试探；若位置偏高，优先等回踩MA5/MA10。"
        reasons.append("B档触发：AI起势确认 + 催化/题材线索 + 资金/量价不弱。")
    elif grade == "D" and q_level == "yellow" and cscore >= 55 and not hard_bad:
        grade = "C"
        label = "只观察"
        action = "黄柱异动叠加催化线索，加入观察池；等待黄转红或放量确认。"
        reasons.append("黄柱+催化线索，D修正为观察。")
    else:
        label = buy_signal.get("label", "只观察") if grade != "A" else "可分批买入"
        action = buy_signal.get("action", "只观察。")
    return {"grade": grade, "label": label, "action": action, "reasons": reasons, "catalyst_score": cscore}



def classify_qishi_signal_type(qishi, plan, catalyst=None, fund_analysis=None):
    """V16.6：把A/B/C/D拆成更可解释的类型，避免只给一个等级。"""
    qishi = qishi or {}
    plan = plan or {}
    catalyst = catalyst or {}
    fund_analysis = fund_analysis or {}

    level = qishi.get("level", "none")
    red_streak = int(qishi.get("red_streak", 0) or 0)
    streak = int(qishi.get("streak", 0) or 0)
    qscore = float(qishi.get("score", 0) or 0)
    high_chase = bool(qishi.get("high_risk_chase"))
    trade_zone = plan.get("trade_zone", "")
    cscore = float(catalyst.get("score", 0) or 0)
    fscore = float(fund_analysis.get("score", 50) or 50)

    if level in ["lightred", "red"] and red_streak <= 2 and "突破" in trade_zone:
        return "B1 黄转红突破型"
    if level in ["lightred", "red"] and red_streak <= 3 and trade_zone in ["低吸有效区", "趋势确认区"]:
        return "B2 红柱早期回踩型"
    if level in ["red", "deepred"] and cscore >= 65 and not high_chase:
        return "B3 行业/催化共振型"
    if level in ["yellow", "lightred"] and cscore >= 70:
        return "B4 催化题材异动型"
    if level in ["red", "deepred"] and high_chase:
        return "强趋势但追高"
    if level in ["yellow"]:
        return "黄柱观察型"
    if level == "none":
        return "未启动"
    if fscore < 40:
        return "资金转弱风险"
    return "综合观察型"


def build_candidate_reason(row_like, buy=None, qishi=None, catalyst=None, fund_analysis=None, risk=None, plan=None):
    """生成扫描排序原因/排除理由，给用户知道为什么排前面或为什么不能买。"""
    buy = buy or {}
    qishi = qishi or {}
    catalyst = catalyst or {}
    fund_analysis = fund_analysis or {}
    risk = risk or {}
    plan = plan or {}
    grade = buy.get("grade", "C")
    pieces = []
    qscore = qishi.get("score", 0)
    cscore = catalyst.get("score", 0)
    fscore = fund_analysis.get("score", 0)
    level = qishi.get("state", qishi.get("level", ""))
    red_days = qishi.get("red_streak", 0)
    if qscore:
        pieces.append(f"起势{qscore:.0f}分/{level}")
    if red_days:
        pieces.append(f"红系{red_days}天")
    if cscore:
        pieces.append(f"催化{cscore:.0f}分")
    if fscore:
        pieces.append(f"资金{fscore:.0f}分")
    if plan.get("trade_zone"):
        pieces.append(plan.get("trade_zone"))
    if risk.get("level"):
        pieces.append("风险" + risk.get("level"))

    if grade in ["A", "B"]:
        return "；".join(pieces[:6]) + "。"

    exclude = []
    if qishi.get("level") == "none":
        exclude.append("起势未启动")
    if qishi.get("high_risk_chase"):
        exclude.append("位置偏高，不适合追")
    if fund_analysis.get("score", 50) < 45:
        exclude.append("资金/量价偏弱")
    if catalyst.get("score", 0) < 40:
        exclude.append("催化不足")
    if risk.get("level") in ["高风险", "极高风险"]:
        exclude.append("风险偏高")
    if plan.get("trade_zone") in ["禁买区", "低吸无效区", "疑似假突破区", "追高风险区"]:
        exclude.append(plan.get("trade_zone"))
    if not exclude:
        exclude.append("确认条件不足")
    return "排除/观察原因：" + "；".join(exclude[:5]) + "。"


def validate_qishi_signal_history(stock_df, qishi=None, signal_type="", lookback=260, horizon_list=(3,5,10,20)):
    """V16.6：对当前类型做轻量历史验证，不使用未来数据做当前判断，只用于参考。

    规则：在过去lookback个交易日里，找到与当前相近的起势状态：
    - 若当前为红/深红：历史红系柱触发日
    - 若当前为黄柱：历史黄柱触发日
    - 若当前为无柱：不做买点统计
    返回3/5/10/20日收益、胜率、最大回撤等。
    """
    if stock_df is None or len(stock_df) < 120:
        return {"enabled": False, "reason": "样本不足"}
    if not qishi or not qishi.get("enabled") or qishi.get("df") is None or qishi.get("df").empty:
        qishi = compute_qishi_tracking(stock_df, None, None, None, None)
        if not qishi.get("enabled"):
            return {"enabled": False, "reason": "起势追踪不可用"}
    d = qishi["df"].copy().reset_index(drop=True)
    if "QISHI_LEVEL" not in d.columns:
        return {"enabled": False, "reason": "缺少起势等级"}
    cur_level = qishi.get("level", "none")
    if cur_level == "none":
        return {"enabled": False, "reason": "当前未启动，不做买点历史验证"}

    # 信号触发日：红系早期/黄柱异动，排除最后horizon天避免未来收益不存在。
    max_h = max(horizon_list)
    start = max(60, len(d) - int(lookback))
    end = len(d) - max_h - 1
    rows = []
    prev_levels = list(d["QISHI_LEVEL"].shift(1).fillna("none"))
    for i in range(start, max(start, end)):
        lv = d.loc[i, "QISHI_LEVEL"]
        prev = prev_levels[i]
        red_early = lv in ["lightred", "red", "deepred"] and prev not in ["lightred", "red", "deepred"]
        yellow_new = lv == "yellow" and prev == "none"
        red_cont = lv in ["red", "deepred"] and prev in ["lightred", "red", "deepred"]
        if cur_level == "yellow":
            matched = yellow_new
        elif "回踩" in signal_type:
            matched = lv in ["lightred", "red"] and d.loc[i, "close"] >= d.loc[i, "MA10"] * 0.985 and d.loc[i, "close"] <= d.loc[i, "MA5"] * 1.03
        elif "强趋势" in signal_type:
            matched = red_cont
        else:
            matched = red_early or (lv in ["lightred", "red"] and prev == "yellow")
        if not matched:
            continue
        base = float(d.loc[i, "close"])
        if base <= 0:
            continue
        rec = {"date": d.loc[i, "date"], "price": base, "level": lv}
        future = d.iloc[i+1:i+max_h+1]
        if future.empty:
            continue
        for h in horizon_list:
            if i + h < len(d):
                rec[f"ret_{h}d"] = (float(d.loc[i+h, "close"]) - base) / base * 100
        rec["max_gain_20d"] = (future["high"].max() - base) / base * 100
        rec["max_drawdown_20d"] = (future["low"].min() - base) / base * 100
        rows.append(rec)
    if not rows:
        return {"enabled": False, "reason": "过去一年类似信号样本不足"}
    bt = pd.DataFrame(rows)
    stats = {"enabled": True, "count": len(bt), "sample_quality": "样本较少" if len(bt) < 8 else ("样本一般" if len(bt) < 20 else "样本较充分")}
    for h in horizon_list:
        col = f"ret_{h}d"
        if col in bt.columns:
            stats[f"avg_ret_{h}d"] = float(bt[col].mean())
            stats[f"win_rate_{h}d"] = float((bt[col] > 0).mean() * 100)
    stats["avg_max_gain_20d"] = float(bt["max_gain_20d"].mean())
    stats["avg_max_drawdown_20d"] = float(bt["max_drawdown_20d"].mean())
    stats["recent_samples"] = bt.tail(8).to_dict("records")
    return stats


def scan_qishi_opportunities(codes, max_items=120):
    """V16.6机会扫描：输出A/B候选、B档类型、排序原因和排除原因。"""
    rows = []
    codes = [str(c).zfill(6) for c in codes][:int(max_items)]
    for code in codes:
        try:
            meta = get_meta_from_master(code)
            boards = detect_boards(code, force=False)
            df, status = get_kline(code, force=False)
            if df is None or len(df) < 80:
                continue
            fund = analyze_proxy_fund_flow(df)
            qishi = compute_qishi_tracking(df, None, None, None, fund)
            catalyst = analyze_catalyst(code, meta.get("name", code), boards.get("industry"), boards.get("concepts"), qishi=qishi, fund_analysis=fund, concept_analysis=None, fetch_online=False)
            risk = risk_grade(df)
            plan = advanced_trade_plan(df, {"enabled": False}, {"enabled": False})
            dummy_final = {"final_rating": "HOLD", "adjusted_score": 60}
            dummy_analysis = {"enabled": False}
            buy = generate_buy_signal(dummy_final, risk, plan, dummy_analysis, dummy_analysis, fund, catalyst)
            buy = apply_qishi_to_buy_signal(buy, qishi, risk, plan)
            buy = apply_catalyst_to_buy_signal(buy, qishi, catalyst, risk, plan, fund)
            sig_type = classify_qishi_signal_type(qishi, plan, catalyst, fund)
            reason = build_candidate_reason(None, buy, qishi, catalyst, fund, risk, plan)
            bt = validate_qishi_signal_history(df, qishi, sig_type, lookback=260, horizon_list=(5, 10, 20))
            rows.append({
                "code": code,
                "name": meta.get("name", code),
                "industry": boards.get("industry", ""),
                "concepts": ",".join((boards.get("concepts") or [])[:4]),
                "buy_grade": buy.get("grade"),
                "buy_label": buy.get("label"),
                "signal_type": sig_type,
                "rank_reason": reason,
                "qishi_state": qishi.get("state"),
                "qishi_score": round(qishi.get("score", 0), 1),
                "red_streak": qishi.get("red_streak", 0),
                "streak": qishi.get("streak", 0),
                "catalyst": catalyst.get("grade"),
                "catalyst_score": catalyst.get("score", 0),
                "fund_score": round(fund.get("score", 0), 1),
                "risk": risk.get("level"),
                "trade_zone": plan.get("trade_zone"),
                "price": round(plan.get("price", 0), 2),
                "hist_count": bt.get("count", 0) if bt.get("enabled") else 0,
                "hist_5d_win": round(bt.get("win_rate_5d", 0), 1) if bt.get("enabled") else None,
                "hist_10d_avg": round(bt.get("avg_ret_10d", 0), 2) if bt.get("enabled") else None,
                "hist_20d_gain": round(bt.get("avg_max_gain_20d", 0), 2) if bt.get("enabled") else None,
                "hist_20d_dd": round(bt.get("avg_max_drawdown_20d", 0), 2) if bt.get("enabled") else None,
            })
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
    # 排名：等级优先，其次起势、催化、资金、历史表现，惩罚回撤和追高风险。
    out["rank"] = (
        out["buy_grade"].map(grade_rank).fillna(0) * 120
        + out["qishi_score"].fillna(0)
        + out["catalyst_score"].fillna(0) * 0.25
        + out["fund_score"].fillna(0) * 0.18
        + out["hist_5d_win"].fillna(0) * 0.08
        + out["hist_10d_avg"].fillna(0) * 1.2
        + out["hist_20d_dd"].fillna(0) * 0.7
    )
    out = out.sort_values("rank", ascending=False).drop(columns=["rank"])
    return out


# =========================
# V16 AI 起势追踪
# =========================

def _rolling_prev_high(series, n):
    return series.rolling(n).max().shift(1)



def compute_qishi_tracking(stock_df, benchmark_df=None, industry_analysis=None, concept_analysis=None, fund_analysis=None):
    """
    V16.6 AI起势柱精炼版。

    核心原则：
    1）起势柱只表达“趋势生命周期”：未启动→异动→起势→延续→加速→衰减。
    2）追高风险不再压没红柱，而是单独提示“强趋势但不适合追”。
    3）资金柱不只看量大不大，还看放量上涨、缩量回踩、放量下跌、冲高回落。

    起势强度 = 趋势结构 30 + 资金量能 30 + 平台突破 20 + 连续性 15 + 相对强弱 5
    资金确认 = 量能放大 + 上涨放量 + 缩量回踩 - 放量下跌 - 冲高回落
    追高风险 = 距离MA20 + 短期涨幅透支 + 放量滞涨 + 跌破关键均线
    """
    df = stock_df.copy().reset_index(drop=True)
    if df is None or df.empty or len(df) < 80:
        return {
            "enabled": False,
            "score": 0,
            "state": "样本不足",
            "level": "none",
            "stage": "样本不足",
            "streak": 0,
            "red_streak": 0,
            "reasons": ["K线样本不足，无法计算AI起势追踪。"],
            "df": pd.DataFrame(),
            "component_scores": {},
            "component_max": {},
            "component_reasons": {},
            "upgrade_conditions": [],
            "downgrade_conditions": [],
            "trade_refinement": [],
        }

    df["VOL_MA20"] = df["volume"].rolling(20).mean()
    df["VOL_MA5"] = df["volume"].rolling(5).mean()
    df["VOL_RATIO20"] = df["volume"] / df["VOL_MA20"]
    df["VOL_RATIO5_20"] = df["VOL_MA5"] / df["VOL_MA20"]
    df["HIGH20_PREV"] = _rolling_prev_high(df["high"], 20)
    df["HIGH60_PREV"] = _rolling_prev_high(df["high"], 60)
    df["LOW20_PREV"] = df["low"].rolling(20).min().shift(1)
    df["STD20"] = df["close"].rolling(20).std()
    df["BOLL_UPPER"] = df["MA20"] + 2 * df["STD20"]
    df["DIST_MA20_NOW"] = (df["close"] - df["MA20"]) / df["close"] * 100
    df["UPPER_SHADOW_PCT"] = (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"] * 100
    df["CLOSE_POS"] = (df["close"] - df["low"]) / (df["high"] - df["low"]).replace(0, pd.NA)

    latest_sector_bonus = 0
    sector_reasons = []
    if industry_analysis and industry_analysis.get("enabled"):
        sc = industry_analysis.get("score") or 50
        if sc >= 70:
            latest_sector_bonus += 3
            sector_reasons.append("行业共振较强。")
        elif sc >= 55:
            latest_sector_bonus += 2
            sector_reasons.append("行业不弱。")
        elif sc < 40:
            latest_sector_bonus -= 2
            sector_reasons.append("行业对比偏弱。")
    if concept_analysis and concept_analysis.get("enabled"):
        sc = concept_analysis.get("score") or 50
        if sc >= 70:
            latest_sector_bonus += 3
            sector_reasons.append("概念共振较强。")
        elif sc >= 55:
            latest_sector_bonus += 2
            sector_reasons.append("概念不弱。")
        elif sc < 40:
            latest_sector_bonus -= 2
            sector_reasons.append("概念对比偏弱。")

    latest_fund_bonus = 0
    fund_reasons = []
    if fund_analysis and fund_analysis.get("enabled"):
        fs = fund_analysis.get("score", 50)
        if fs >= 70:
            latest_fund_bonus += 5
            fund_reasons.append("资金/量价代理偏强。")
        elif fs >= 58:
            latest_fund_bonus += 3
            fund_reasons.append("资金/量价代理中性偏正。")
        elif fs < 40:
            latest_fund_bonus -= 5
            fund_reasons.append("资金/量价代理偏弱。")

    def _num(x, default=0):
        try:
            if pd.isna(x):
                return default
            return float(x)
        except Exception:
            return default

    def score_one(row, i, continuity_bonus=0, with_latest_bonus=False):
        close = row.get("close")
        pct = row.get("pct_change")
        vol_ratio = row.get("VOL_RATIO20")
        vol_ratio5 = row.get("VOL_RATIO5_20")
        ret5 = row.get("RET5")
        ret20 = row.get("RET20")
        ret60 = row.get("RET60")
        dist_ma20 = row.get("DIST_MA20_NOW")
        vol20 = row.get("VOLATILITY20")
        upper_shadow = row.get("UPPER_SHADOW_PCT")
        close_pos = row.get("CLOSE_POS")

        comp = {
            "趋势结构": 0,
            "资金量能": 0,
            "平台突破": 0,
            "连续性": 0,
            "相对强弱": 0,
            "追高风险": 0,
        }
        comp_reasons = {k: [] for k in comp}
        capital_risk = 0
        capital_tag = "neutral"

        # 趋势结构，满分30。
        if pd.notna(row.get("MA5")) and close > row["MA5"]:
            comp["趋势结构"] += 4; comp_reasons["趋势结构"].append("收盘价站上MA5。")
        if pd.notna(row.get("MA10")) and close > row["MA10"]:
            comp["趋势结构"] += 4; comp_reasons["趋势结构"].append("收盘价站上MA10。")
        if pd.notna(row.get("MA20")) and close > row["MA20"]:
            comp["趋势结构"] += 5; comp_reasons["趋势结构"].append("收盘价站上MA20。")
        if pd.notna(row.get("MA60")) and close > row["MA60"]:
            comp["趋势结构"] += 3; comp_reasons["趋势结构"].append("收盘价站上MA60。")
        if pd.notna(row.get("MA5")) and pd.notna(row.get("MA10")) and pd.notna(row.get("MA20")) and row["MA5"] > row["MA10"] > row["MA20"]:
            comp["趋势结构"] += 7; comp_reasons["趋势结构"].append("MA5>MA10>MA20，短线均线多头排列。")
        if pd.notna(row.get("MA10")) and pd.notna(row.get("MA20")) and pd.notna(row.get("MA60")) and row["MA10"] > row["MA20"] > row["MA60"]:
            comp["趋势结构"] += 3; comp_reasons["趋势结构"].append("MA10>MA20>MA60，中期均线结构向上。")
        if pd.notna(row.get("MA20_SLOPE")) and row["MA20_SLOPE"] > 0:
            comp["趋势结构"] += 4; comp_reasons["趋势结构"].append("MA20斜率向上。")
        comp["趋势结构"] = min(comp["趋势结构"], 30)

        # 资金量能，满分30。这里是“量价代理资金”，不是Level-2真实主力。
        if pd.notna(vol_ratio):
            if vol_ratio >= 2.2:
                comp["资金量能"] += 10; comp_reasons["资金量能"].append("成交量超过20日均量2.2倍，资金推动很强。")
            elif vol_ratio >= 1.6:
                comp["资金量能"] += 8; comp_reasons["资金量能"].append("成交量超过20日均量1.6倍。")
            elif vol_ratio >= 1.2:
                comp["资金量能"] += 6; comp_reasons["资金量能"].append("成交量超过20日均量1.2倍。")
            elif vol_ratio >= 0.85:
                comp["资金量能"] += 3; comp_reasons["资金量能"].append("量能未明显萎缩。")
        if pd.notna(vol_ratio5):
            if vol_ratio5 >= 1.35:
                comp["资金量能"] += 6; comp_reasons["资金量能"].append("5日均量明显高于20日均量。")
            elif vol_ratio5 >= 1.08:
                comp["资金量能"] += 4; comp_reasons["资金量能"].append("5日均量高于20日均量。")
        if pd.notna(pct) and pct > 1.5 and pd.notna(vol_ratio) and vol_ratio >= 1.1:
            comp["资金量能"] += 7; comp_reasons["资金量能"].append("放量上涨，资金推动迹象明显。")
            capital_tag = "push"
        elif pd.notna(pct) and pct > 0 and pd.notna(vol_ratio) and vol_ratio >= 1.0:
            comp["资金量能"] += 4; comp_reasons["资金量能"].append("上涨量能配合。")
            capital_tag = "active"
        if pd.notna(pct) and -2.2 <= pct < 0 and pd.notna(vol_ratio) and vol_ratio <= 0.9 and pd.notna(row.get("MA10")) and close >= row["MA10"] * 0.985:
            comp["资金量能"] += 5; comp_reasons["资金量能"].append("回踩缩量且未破MA10，属于健康回踩。")
            capital_tag = "healthy_pullback"
        if pd.notna(close_pos) and close_pos >= 0.7 and pd.notna(pct) and pct > 0:
            comp["资金量能"] += 3; comp_reasons["资金量能"].append("收盘位置靠近当日高位，承接较好。")
        if with_latest_bonus and latest_fund_bonus != 0:
            comp["资金量能"] += latest_fund_bonus
            comp_reasons["资金量能"].extend(fund_reasons[:2])
        if pd.notna(pct) and pct < -2.5 and pd.notna(vol_ratio) and vol_ratio >= 1.25:
            comp["资金量能"] -= 8
            capital_risk += 12
            capital_tag = "risk_down"
            comp_reasons["资金量能"].append("放量下跌，资金质量下降。")
        if pd.notna(pct) and pct > 0 and pd.notna(upper_shadow) and upper_shadow >= 4 and pd.notna(vol_ratio) and vol_ratio >= 1.3:
            comp["资金量能"] -= 5
            capital_risk += 8
            capital_tag = "stall"
            comp_reasons["资金量能"].append("放量冲高回落，资金分歧变大。")
        comp["资金量能"] = max(0, min(comp["资金量能"], 30))

        # 平台突破，满分20。
        if pd.notna(row.get("HIGH20_PREV")) and close > row["HIGH20_PREV"]:
            comp["平台突破"] += 7; comp_reasons["平台突破"].append("突破近20日高点。")
        if pd.notna(row.get("HIGH60_PREV")) and close > row["HIGH60_PREV"]:
            comp["平台突破"] += 8; comp_reasons["平台突破"].append("突破近60日高点。")
        if pd.notna(row.get("BOLL_UPPER")) and close > row["BOLL_UPPER"]:
            comp["平台突破"] += 3; comp_reasons["平台突破"].append("站上Bollinger上轨。")
        if pd.notna(row.get("MA5")) and close >= row["MA5"]:
            comp["平台突破"] += 2; comp_reasons["平台突破"].append("突破后仍守住MA5附近。")
        comp["平台突破"] = min(comp["平台突破"], 20)

        # 连续性，满分15。让红柱变成“状态追踪”，不是偶发触发。
        if continuity_bonus > 0:
            comp["连续性"] += continuity_bonus
            comp_reasons["连续性"].append(f"趋势状态延续，连续性加分 {continuity_bonus}。")
        comp["连续性"] = min(comp["连续性"], 15)

        # 相对强弱，满分5。只有最新窗口才纳入行业/概念共振。
        if with_latest_bonus:
            comp["相对强弱"] += latest_sector_bonus
            comp_reasons["相对强弱"].extend(sector_reasons[:3])
        comp["相对强弱"] = max(0, min(comp["相对强弱"], 5))

        # 动量直接并入趋势生命周期，但不上升为独立组件，避免涨幅过大导致误判。
        # 小幅动量用于补强，涨幅透支放到风险层。
        momentum_bonus = 0
        if pd.notna(ret5):
            if ret5 > 8: momentum_bonus += 4
            elif ret5 > 3: momentum_bonus += 2
        if pd.notna(ret20):
            if ret20 > 18: momentum_bonus += 4
            elif ret20 > 8: momentum_bonus += 2
        if momentum_bonus:
            comp["趋势结构"] = min(30, comp["趋势结构"] + momentum_bonus)
            comp_reasons["趋势结构"].append(f"平台突破改善，趋势结构补强 {momentum_bonus}。")

        # 追高风险，单独展示。
        risk_score = 0
        if pd.notna(dist_ma20):
            if dist_ma20 > 24:
                risk_score += 16; comp_reasons["追高风险"].append("距离MA20超过24%，追高风险很高。")
            elif dist_ma20 > 16:
                risk_score += 11; comp_reasons["追高风险"].append("距离MA20超过16%，新仓不宜追高。")
            elif dist_ma20 > 10:
                risk_score += 6; comp_reasons["追高风险"].append("距离MA20超过10%，更适合等回踩。")
            elif dist_ma20 < -6:
                risk_score += 8; comp_reasons["追高风险"].append("股价低于MA20较多，趋势未修复。")
        if pd.notna(ret20) and ret20 > 35:
            risk_score += 8; comp_reasons["追高风险"].append("近20日涨幅超过35%，短期涨幅可能透支。")
        if pd.notna(ret60) and ret60 > 80:
            risk_score += 8; comp_reasons["追高风险"].append("近60日涨幅超过80%，高位波动风险增加。")
        if capital_risk >= 8:
            risk_score += capital_risk // 2
            comp_reasons["追高风险"].append("量价资金柱出现分歧/放量风险。")
        if pd.notna(row.get("MA10")) and close < row["MA10"]:
            risk_score += 6; comp_reasons["追高风险"].append("跌破MA10，短线红柱持续性下降。")
        if pd.notna(row.get("MA20")) and close < row["MA20"]:
            risk_score += 14; comp_reasons["追高风险"].append("跌破MA20，起势结构受损。")
        if pd.notna(vol20) and vol20 > 6:
            risk_score += 4; comp_reasons["追高风险"].append("20日波动率较高。")
        comp["追高风险"] = min(risk_score, 45)

        strength = comp["趋势结构"] + comp["资金量能"] + comp["平台突破"] + comp["连续性"] + comp["相对强弱"]
        strength = max(0, min(100, float(strength)))
        return strength, comp, comp_reasons, capital_risk, capital_tag

    def level_from_score(s):
        if s >= 88: return "deepred"
        if s >= 72: return "red"
        if s >= 56: return "lightred"
        if s >= 36: return "yellow"
        return "none"

    strengths, base_levels, levels, comps, comp_rs, cap_risks, cap_tags = [], [], [], [], [], [], []
    prev_lv = "none"
    prev_red_streak = 0
    for i, row in df.iterrows():
        if i < 60:
            strengths.append(0); base_levels.append("none"); levels.append("none"); comps.append({}); comp_rs.append({}); cap_risks.append(0); cap_tags.append("neutral"); continue
        close = row.get("close")
        trend_intact = False
        if pd.notna(row.get("MA10")) and pd.notna(row.get("MA20")):
            trend_intact = close >= row["MA10"] or (close >= row["MA20"] and pd.notna(row.get("MA5")) and row.get("MA5") >= row.get("MA10"))
        severe_break = (pd.notna(row.get("MA20")) and close < row["MA20"]) or (pd.notna(row.get("pct_change")) and row.get("pct_change") < -6)
        continuity_bonus = 0
        if prev_lv in ["red", "deepred", "lightred"] and trend_intact and not severe_break:
            continuity_bonus += 8
            if prev_red_streak >= 2: continuity_bonus += 4
            if prev_red_streak >= 5: continuity_bonus += 3
        elif prev_lv == "yellow" and trend_intact:
            continuity_bonus += 4

        s, comp, comp_reasons, cap_risk, cap_tag = score_one(row, i, continuity_bonus=continuity_bonus, with_latest_bonus=(i >= len(df)-3))
        base = level_from_score(s)
        lv = base

        # 状态延续：红柱进入趋势后，只要MA10/MA20结构没坏，就不因为单日分数小波动立刻断柱。
        if prev_lv in ["red", "deepred"] and base in ["none", "yellow", "lightred"] and trend_intact and s >= 48 and not severe_break:
            lv = "red" if s >= 58 else "yellow"
        if prev_lv == "deepred" and base == "red" and s >= 76 and trend_intact:
            lv = "deepred"
        if severe_break:
            lv = "yellow" if base in ["red", "deepred", "lightred"] else "none"
        if cap_risk >= 12 and lv == "deepred":
            lv = "red"

        strengths.append(s); base_levels.append(base); levels.append(lv); comps.append(comp); comp_rs.append(comp_reasons); cap_risks.append(cap_risk); cap_tags.append(cap_tag)
        if lv in ["red", "deepred", "lightred"]:
            prev_red_streak += 1
        else:
            prev_red_streak = 0
        prev_lv = lv

    df["QISHI_SCORE"] = strengths
    df["QISHI_BASE_LEVEL"] = base_levels
    df["QISHI_LEVEL"] = levels
    df["CAPITAL_SCORE"] = [c.get("资金量能", 0) if c else 0 for c in comps]
    df["CAPITAL_RISK"] = cap_risks
    df["CAPITAL_TAG"] = cap_tags
    df["RISK_SCORE"] = [c.get("追高风险", 0) if c else 0 for c in comps]

    latest = df.iloc[-1]
    score = float(latest["QISHI_SCORE"])
    level = latest["QISHI_LEVEL"]
    base_level = latest["QISHI_BASE_LEVEL"]
    component_scores = comps[-1] if comps else {}
    component_reasons = comp_rs[-1] if comp_rs else {}
    component_max = {
        "趋势结构": 30,
        "资金量能": 30,
        "平台突破": 20,
        "连续性": 15,
        "相对强弱": 5,
        "追高风险": 45,
    }

    streak = 0
    red_streak = 0
    prev_level = df["QISHI_LEVEL"].iloc[-2] if len(df) >= 2 else "none"
    for lv in reversed(list(df["QISHI_LEVEL"])):
        if lv != "none": streak += 1
        else: break
    for lv in reversed(list(df["QISHI_LEVEL"])):
        if lv in ["lightred", "red", "deepred"]: red_streak += 1
        else: break

    risk_score = float(component_scores.get("追高风险", 0) or 0)
    high_risk_chase = risk_score >= 14
    very_high_risk = risk_score >= 26

    if level == "deepred": stage = "加速"
    elif level == "red": stage = "趋势延续"
    elif level == "lightred": stage = "起势确认"
    elif level == "yellow": stage = "异动观察"
    else: stage = "未启动"

    if prev_level in ["red", "deepred", "lightred"] and level in ["yellow", "none"]:
        stage = "动能衰减"

    state = {
        "deepred": "深红加速",
        "red": "红柱趋势延续",
        "lightred": "浅红起势确认",
        "yellow": "黄柱异动",
        "none": "未启动",
    }.get(level, "未知")
    if level in ["lightred", "red", "deepred"] and high_risk_chase:
        state = f"{state}｜高位风险"

    reasons = []
    if level == "none":
        reasons.append("当前无起势柱：趋势、资金量能和平台突破没有形成连续共振。")
    elif level == "yellow":
        reasons.append("黄柱：资金或趋势开始异动，适合观察，不等于确认买点。")
    elif level == "lightred":
        reasons.append("浅红：起势确认初期，趋势/量能开始共振。")
    elif level == "red":
        reasons.append("红柱：趋势进入延续阶段，资金量能和均线结构仍在跟随。")
    else:
        reasons.append("深红：趋势加速阶段，已有仓位可跟踪，但新仓要看位置。")
    if prev_level == "yellow" and level in ["lightred", "red", "deepred"]:
        reasons.append("黄转红：异动升级为起势确认。")
    if prev_level in ["lightred", "red", "deepred"] and level in ["none", "yellow"]:
        reasons.append("红柱断档/降级：动能开始衰减。")
    if streak > 0:
        reasons.append(f"连续起势柱 {streak} 天，红系柱连续 {red_streak} 天。")
    if base_level != level and level != "none":
        reasons.append("本柱包含趋势状态延续：趋势未破坏，所以不因单日小波动立刻断柱。")
    if high_risk_chase:
        reasons.append("注意：起势柱仍有效，但位置偏高；风险只限制追买，不抹掉红柱。")

    trade_refinement = []
    if stage == "未启动":
        trade_refinement.append("新开仓：不买，等待黄柱/红柱或放量突破。")
        trade_refinement.append("已有仓位：按MA20/强风控线管理，不加仓。")
    elif stage == "异动观察":
        trade_refinement.append("新开仓：观察；若黄柱连续并转浅红/红柱，可小仓试探。")
        trade_refinement.append("已有仓位：可暂持；黄柱消失且跌破MA20则降级。")
    elif stage == "起势确认":
        trade_refinement.append("新开仓：可小仓试探；更稳的买点是回踩MA5/MA10不破。")
        trade_refinement.append("已有仓位：继续观察，红柱延续可持有。")
    elif stage == "趋势延续":
        trade_refinement.append("新开仓：不追连续红柱，优先等回踩MA5/MA10不破。")
        trade_refinement.append("已有仓位：红柱不断可持有；红柱断档或跌破MA10减仓。")
    elif stage == "加速":
        trade_refinement.append("新开仓：深红加速不代表追高买，优先等回踩或二次确认。")
        trade_refinement.append("已有仓位：趋势强可持有，放量冲高回落或红柱降级时止盈一部分。")
    else:
        trade_refinement.append("新开仓：动能衰减，不追买。")
        trade_refinement.append("已有仓位：若跌破MA10减仓，跌破MA20强风控。")

    upgrade_conditions = [
        "无柱→黄柱：站回MA5/MA10/MA20之一，量能不再萎缩。",
        "黄柱→浅红：趋势多头排列改善，且资金/量价代理不弱。",
        "浅红→红柱：放量突破20日平台，或红系柱连续且股价守住MA10。",
        "红柱→深红：突破60日平台并且资金柱同步增强。",
    ]
    downgrade_conditions = [
        "黄柱消失：异动失败，继续等待。",
        "红柱转黄：动能衰减，已有仓位减仓观察。",
        "红柱断档 + 跌破MA10：短线动能破坏。",
        "跌破MA20或强风控线：起势结构破坏。",
        "高位深红后放量冲高回落：不追，已有仓位分批止盈。",
    ]

    qishi_score_by_level = {"none": "未启动", "yellow": "异动观察", "lightred": "起势确认", "red": "趋势延续", "deepred": "趋势加速"}
    return {
        "enabled": True,
        "score": score,
        "state": state,
        "stage": stage,
        "level": level,
        "base_level": base_level,
        "level_explain": qishi_score_by_level.get(level, "未知"),
        "streak": streak,
        "red_streak": red_streak,
        "high_risk_chase": bool(high_risk_chase),
        "very_high_risk": bool(very_high_risk),
        "risk_score": risk_score,
        "reasons": reasons,
        "df": df.tail(150).copy(),
        "component_scores": component_scores,
        "component_max": component_max,
        "component_reasons": component_reasons,
        "upgrade_conditions": upgrade_conditions,
        "downgrade_conditions": downgrade_conditions,
        "trade_refinement": trade_refinement,
    }


def apply_qishi_to_buy_signal(buy_signal, qishi, risk, plan):
    """把AI起势追踪接入买入等级：红柱提升观察价值，追高风险限制新仓但不直接判D。"""
    if not qishi or not qishi.get("enabled"):
        return buy_signal
    grade = buy_signal.get("grade", "C")
    reasons = list(buy_signal.get("reasons", []))
    q_level = qishi.get("level", "none")
    stage = qishi.get("stage", "未启动")
    high_chase = qishi.get("high_risk_chase", False)

    hard_risk = risk.get("level") == "极高风险" or plan.get("trade_zone") in ["禁买区", "疑似假突破区", "低吸无效区"]

    if q_level == "none" and grade in ["A", "B"]:
        grade = "C"
        label = "只观察"
        action = "AI起势追踪未启动，不能仅凭价格位置开仓；等待黄柱/红柱或放量确认。"
        reasons.append("AI起势追踪未启动，买入信号降为观察。")
    elif q_level == "yellow" and grade == "D" and not hard_risk:
        grade = "C"
        label = "只观察"
        action = "出现黄色异动柱，有观察价值，但还不是买入确认。"
        reasons.append("AI起势追踪出现黄柱，说明开始异动。")
    elif q_level in ["lightred", "red", "deepred"] and grade in ["C", "D"] and not hard_risk:
        if high_chase:
            grade = "C"
            label = "只观察"
            action = "AI起势较强但位置偏高，不建议追买；等待回踩MA5/MA10不破或二次确认。"
            reasons.append("AI起势红系柱确认，但位置偏高，不能直接追买。")
        elif q_level == "lightred":
            grade = "B"
            label = "可小仓试探"
            action = "AI起势进入浅红确认，趋势/量能开始共振；可小仓试探，等待红柱延续再加仓。"
            reasons.append("AI起势浅红确认，买入信号提升为小仓试探。")
        else:
            grade = "B"
            label = "可小仓试探"
            action = "AI起势进入红柱/深红趋势；新仓不追连续拉升，优先等回踩确认。"
            reasons.append("AI起势红柱确认，趋势进入跟踪状态。")

    if grade == "A":
        label = buy_signal.get("label", "可分批买入")
        action = buy_signal.get("action", "可分批买入。")
    elif grade == "B":
        label = locals().get("label", "可小仓试探")
        action = locals().get("action", "可以买观察仓或小仓试探，建议新仓10%-20%。")
    elif grade == "C":
        label = locals().get("label", "只观察")
        action = locals().get("action", "只观察，不追买；等待起势确认、资金确认或回踩确认。")
    else:
        label = "不建议买入"
        action = locals().get("action", "不建议买入。")

    return {"grade": grade, "label": label, "action": action, "reasons": reasons, "stage": stage}


def plot_qishi_tracking(qishi):
    """绘制K线/均线 + AI起势生命周期柱 + 资金量能确认柱。"""
    if not qishi or not qishi.get("enabled") or qishi.get("df") is None or qishi["df"].empty:
        return None
    import matplotlib.pyplot as plt
    d = qishi["df"].copy().reset_index(drop=True)
    x = range(len(d))
    color_map = {
        "none": "#e0e0e0",
        "yellow": "#f5d547",
        "lightred": "#ff8a80",
        "red": "#e53935",
        "deepred": "#8b0000",
    }
    colors = [color_map.get(v, "#e0e0e0") for v in d["QISHI_LEVEL"]]
    heights = [v if v >= 30 else 0 for v in d["QISHI_SCORE"]]

    fig, axes = plt.subplots(3, 1, figsize=(13, 7.8), gridspec_kw={"height_ratios": [2.25, 1.0, 0.9]}, sharex=True)

    # K线：红涨绿跌，叠加MA。中文解释放页面，图内用英文避免乱码。
    for i, row in d.iterrows():
        o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
        if pd.isna(o) or pd.isna(h) or pd.isna(l) or pd.isna(c):
            continue
        col = "#e53935" if c >= o else "#2e7d32"
        axes[0].vlines(i, l, h, color=col, linewidth=0.8, alpha=0.9)
        body_low = min(o, c)
        body_h = max(abs(c - o), max(c * 0.002, 0.01))
        axes[0].add_patch(plt.Rectangle((i - 0.32, body_low), 0.64, body_h, edgecolor=col, facecolor=col if c >= o else "white", linewidth=0.8, alpha=0.9))

    for ma, lw in [("MA5", 0.8), ("MA10", 0.8), ("MA20", 1.0), ("MA60", 1.0)]:
        if ma in d.columns:
            axes[0].plot(x, d[ma], label=ma, linewidth=lw)
    axes[0].set_title("Price Trend: K-line + MA")
    axes[0].legend(loc="upper left", ncol=4, fontsize=8)
    axes[0].grid(True, alpha=0.18)

    axes[1].bar(x, heights, color=colors, width=0.88)
    axes[1].axhline(36, linestyle="--", linewidth=0.8, alpha=0.35)
    axes[1].axhline(56, linestyle="--", linewidth=0.8, alpha=0.35)
    axes[1].axhline(72, linestyle="--", linewidth=0.8, alpha=0.35)
    axes[1].axhline(88, linestyle="--", linewidth=0.8, alpha=0.35)
    axes[1].set_ylim(0, 105)
    axes[1].set_title("AI Trend Lifecycle: Yellow=Watch, Pink=Start, Red=Trend, DarkRed=Acceleration")
    axes[1].grid(True, axis="y", alpha=0.18)

    cap = d.get("CAPITAL_SCORE", pd.Series([0]*len(d))).fillna(0)
    cap_risk = d.get("CAPITAL_RISK", pd.Series([0]*len(d))).fillna(0)
    cap_tag = d.get("CAPITAL_TAG", pd.Series(["neutral"]*len(d))).fillna("neutral")
    cap_colors = []
    for v, r, tag in zip(cap, cap_risk, cap_tag):
        if r >= 12 or tag == "risk_down":
            cap_colors.append("#333333")
        elif tag == "stall":
            cap_colors.append("#8e24aa")
        elif v >= 22:
            cap_colors.append("#8b0000")
        elif v >= 15:
            cap_colors.append("#e53935")
        elif v >= 8:
            cap_colors.append("#f5d547")
        else:
            cap_colors.append("#d9d9d9")
    axes[2].bar(x, cap, color=cap_colors, width=0.88)
    axes[2].set_ylim(0, 32)
    axes[2].set_title("Capital/Volume Quality: Yellow=Active, Red=Push, Purple=Stall, Black=Risk")
    axes[2].grid(True, axis="y", alpha=0.18)

    if "date" in d.columns:
        step = max(1, len(d) // 8)
        axes[2].set_xticks(list(x)[::step])
        axes[2].set_xticklabels(d["date"].astype(str).tolist()[::step], rotation=30, ha="right")
    plt.tight_layout()
    return fig


# =========================
# V16.7 实用融合：趋势等级、Level-2手动确认、升级/降级条件
# =========================

def analyze_manual_level2(big_order_direction="中性", orderbook_strength="中性", tick_direction="中性", l2_note=""):
    """手机东方财富Level-2无法直接作为API读取；这里先把人工观察结果转成模型加减分。"""
    score = 50
    reasons = []

    if big_order_direction == "明显流入":
        score += 22; reasons.append("Level-2观察：大单/超大单方向偏流入。")
    elif big_order_direction == "小幅流入":
        score += 12; reasons.append("Level-2观察：大单方向小幅偏流入。")
    elif big_order_direction == "明显流出":
        score -= 25; reasons.append("Level-2观察：大单/超大单方向明显流出。")
    elif big_order_direction == "小幅流出":
        score -= 12; reasons.append("Level-2观察：大单方向小幅偏流出。")
    else:
        reasons.append("Level-2观察：大单方向中性或未输入。")

    if orderbook_strength == "买盘强":
        score += 12; reasons.append("盘口承接偏强，买盘支撑较好。")
    elif orderbook_strength == "卖盘强":
        score -= 14; reasons.append("卖盘压力较强，短线承接不足。")
    else:
        reasons.append("盘口承接中性。")

    if tick_direction == "主动买多":
        score += 12; reasons.append("逐笔成交主动买入偏多。")
    elif tick_direction == "主动卖多":
        score -= 14; reasons.append("逐笔成交主动卖出偏多。")
    else:
        reasons.append("逐笔成交方向中性。")

    score = max(0, min(100, score))
    if score >= 75:
        grade = "Level-2强确认"
    elif score >= 60:
        grade = "Level-2偏正面"
    elif score >= 45:
        grade = "Level-2中性"
    else:
        grade = "Level-2偏负面"
    if l2_note:
        reasons.append(f"备注：{l2_note}")
    return {"score": score, "grade": grade, "reasons": reasons}


def apply_manual_level2_to_buy_signal(buy_signal, l2_analysis, qishi, risk):
    """Level-2人工确认只做辅助：强确认可把C升级B；明显流出可把A/B降级。"""
    bsig = dict(buy_signal)
    grade = bsig.get("grade", "C")
    reasons = list(bsig.get("reasons", []))
    score = l2_analysis.get("score", 50)
    q_score = qishi.get("score", 0)

    if score >= 75 and grade == "C" and q_score >= 55 and risk.get("level") not in ["极高风险"]:
        bsig["grade"] = "B"
        bsig["label"] = "可小仓试探"
        bsig["action"] = "Level-2人工观察偏强，且AI起势不弱，可小仓试探；仍以回踩/确认买点为主。"
        reasons.append("Level-2人工确认较强，将C观察升级为B小仓试探。")
    elif score < 40 and grade in ["A", "B"]:
        bsig["grade"] = "C" if grade == "B" else "B"
        bsig["label"] = "只观察" if grade == "B" else "可小仓试探"
        bsig["action"] = "Level-2人工观察偏弱，买入信号降级，等待资金重新确认。"
        reasons.append("Level-2显示资金/盘口偏弱，买入信号降级。")

    bsig["reasons"] = reasons
    return bsig


def get_trend_grade(qishi, plan, risk):
    """趋势等级和当前买点分开：强趋势不代表现在可以买。"""
    score = qishi.get("score", 0)
    state = str(qishi.get("state", ""))
    red_streak = qishi.get("red_streak", 0)
    if score >= 85 or "深红" in state:
        grade = "A趋势"
        desc = "强趋势/加速阶段"
    elif score >= 70 or red_streak >= 2 or "红" in state:
        grade = "B趋势"
        desc = "起势确认或趋势延续"
    elif score >= 45 or "黄" in state:
        grade = "C趋势"
        desc = "异动观察，趋势尚未完全确认"
    else:
        grade = "D趋势"
        desc = "未启动或趋势偏弱"
    if risk.get("level") == "极高风险":
        desc += "，但风险等级极高，需要优先风控"
    return {"grade": grade, "desc": desc}


def generate_trigger_conditions(qishi, plan, buy_signal):
    price = plan.get("price", 0)
    ma5 = plan.get("ma5", 0)
    ma10 = plan.get("ma10", 0)
    ma20 = plan.get("ma20", 0)
    key_res = plan.get("key_resistance", 0)
    stop_loss = plan.get("stop_loss", 0)
    hard_stop = plan.get("hard_stop", 0)

    upgrade = []
    downgrade = []

    if buy_signal.get("grade") in ["C", "D"]:
        upgrade.append(f"站稳关键压力/突破位 {key_res:.2f}，且AI起势柱不回落")
        upgrade.append(f"回踩 MA5/MA10（约 {ma5:.2f}/{ma10:.2f}）不破，并重新放量转强")
        upgrade.append("资金/量能柱重新转红，或Level-2观察大单方向偏流入")
        upgrade.append("行业/概念不转弱，个股不明显跑输板块")
    elif buy_signal.get("grade") == "B":
        upgrade.append("红柱连续且资金柱同步转红，回踩不破MA5/MA10")
        upgrade.append("突破前高后不快速跌回，成交量保持温和放大")
    else:
        upgrade.append("A档已经较强，重点不是继续升级，而是分批执行与风控")

    downgrade.append(f"跌破风控线 {stop_loss:.2f}，买入假设失败")
    downgrade.append(f"跌破强风控线 {hard_stop:.2f}，进入强风控/离场区")
    downgrade.append(f"跌破 MA20（约 {ma20:.2f}）且AI红柱断档")
    downgrade.append("资金/量能柱转黑或出现放量下跌")
    downgrade.append("高位深红后冲高回落，且次日不能修复")

    return {"upgrade": upgrade, "downgrade": downgrade}


def explain_grade_gap(buy_signal, qishi, plan, catalyst_analysis, fund_analysis):
    """告诉用户为什么不是A/B，减少误解。"""
    grade = buy_signal.get("grade", "C")
    notes = []
    q_score = qishi.get("score", 0)
    red_streak = qishi.get("red_streak", 0)
    cat_score = catalyst_analysis.get("score", 0) if isinstance(catalyst_analysis, dict) else 0
    fund_score = fund_analysis.get("score", 50) if isinstance(fund_analysis, dict) and fund_analysis.get("enabled") else 50
    rr = plan.get("reward_risk", 0)
    trade_zone = plan.get("trade_zone", "")

    if grade != "A":
        if q_score < 80:
            notes.append("AI起势分还没有达到强A区间。")
        if cat_score < 70:
            notes.append("催化剂分不够强，暂不支持A档。")
        if fund_score < 65:
            notes.append("资金/量能确认不足，暂不支持A档。")
        if rr < 1.5:
            notes.append("盈亏比不足，不能给A档。")
        if "追高" in trade_zone or red_streak >= 5:
            notes.append("红柱已经连续较多天或位置偏高，不适合新仓追高。")
    if grade not in ["A", "B"]:
        if q_score < 60:
            notes.append("AI起势还不够确认，B档条件不足。")
        if trade_zone in ["禁买区", "低吸无效区", "疑似假突破区"]:
            notes.append(f"当前交易区为{trade_zone}，不适合试仓。")
        if rr < 1.2:
            notes.append("盈亏比低于B档要求。")
    if not notes:
        notes.append("当前等级主要由买点位置、风险收益比和资金确认共同决定。")
    return notes[:6]

def make_quick_board_analysis(board_name, board_type='industry'):
    """快看模式下不拉同行K线，只保留板块识别与中性评分，避免卡顿。"""
    if not board_name or board_name in ["行业未识别", "概念未识别"]:
        return {
            "enabled": False, "score": None, "grade": "未识别", "board_name": board_name or "未识别",
            "board_type": board_type, "rows": pd.DataFrame(), "chart_df": pd.DataFrame(),
            "reasons": ["快看模式：未识别到可靠板块，不参与板块评分。"],
        }
    return {
        "enabled": True, "score": 55, "grade": "快看模式：板块中性", "board_name": board_name,
        "board_type": board_type, "industry": board_name, "status": "本地识别/快看模式",
        "rows": pd.DataFrame(), "chart_df": pd.DataFrame(),
        "board_ret20": 0, "board_ret60": 0, "board_ret120": 0,
        "excess20": 0, "excess60": 0, "excess120": 0,
        "excess_ind20": 0, "excess_ind60": 0, "excess_ind120": 0,
        "strong_ratio": 0, "rank60": None, "rank_total": 0,
        "reasons": [f"快看模式：已识别{('行业' if board_type=='industry' else '概念')} {board_name}；未拉同行曲线以提升速度。"],
    }

# =========================
# 页面
# =========================

with st.sidebar:
    st.header("输入区")
    stock_code = st.text_input("请输入A股股票代码", value="000400")
    force_update = st.checkbox("强制实时更新行情/快照/资金流", value=False)
    st.divider()
    st.subheader("我的持仓（可选）")
    has_position = st.checkbox("我已有仓位", value=False)
    cost_price_input = st.number_input("成本价", min_value=0.0, value=0.0, step=0.01, format="%.2f")
    holding_pct_input = st.slider("当前持仓比例 %", min_value=0, max_value=100, value=0, step=5)
    trade_horizon = st.selectbox("计划周期", ["短线", "波段", "中线"], index=1)
    st.divider()
    st.subheader("模式")
    app_mode = st.radio("选择功能", ["单股快看", "深度分析", "起势机会扫描", "批量覆盖检测"], index=0)
    quick_mode = app_mode == "单股快看"
    coverage_source = st.selectbox("检测范围", ["自定义列表", "全A底座前N只", "全A底座随机抽样"], index=0)
    coverage_n = st.number_input("检测数量N", min_value=5, max_value=5000, value=50, step=5)
    coverage_codes_text = st.text_area("自定义股票代码", value="000400,002156,300276,300308,600519,300750,600036,002594", height=90)
    coverage_depth = st.selectbox("检测深度", ["快速：行业/概念/行情/基本面", "标准：额外检测行业对比"], index=0)
    st.divider()
    st.subheader("V16.7 起势/催化/校准")
    online_catalyst = st.checkbox("单股尝试抓公告催化（快看模式默认关闭更快）", value=False)
    scan_source = st.selectbox("机会扫描范围", ["自定义列表", "全A底座随机抽样", "全A底座前N只"], index=1)
    scan_n = st.number_input("机会扫描数量", min_value=10, max_value=5000, value=100, step=10)
    scan_codes_text = st.text_area("机会扫描自定义代码", value="000400,002156,300276,300308,600519,300750,600036,002594", height=80)
    run_scan = st.button("开始起势机会扫描")
    run_coverage = st.button("开始批量覆盖检测")
    st.divider()
    st.subheader("Level-2人工确认（可选）")
    big_order_direction = st.selectbox("大单/超大单方向", ["中性", "明显流入", "小幅流入", "小幅流出", "明显流出"], index=0)
    orderbook_strength = st.selectbox("盘口承接", ["中性", "买盘强", "卖盘强"], index=0)
    tick_direction = st.selectbox("逐笔成交", ["中性", "主动买多", "主动卖多"], index=0)
    l2_note = st.text_input("Level-2备注", value="")
    st.divider()
    update_board_db = st.button("初始化/更新全A数据底座")
    fds = get_foundation_status()
    dbs = get_board_db_status()
    if fds["baostock_ready"] or dbs["industry_ready"] or dbs["concept_ready"]:
        st.caption(
            f"全A底座：BaoStock行业{fds['baostock_industries']}个/股票{fds['baostock_codes']}只；"
            f"板块库：行业{dbs['industry_boards']}个/股票{dbs['industry_codes']}只；"
            f"概念{dbs['concept_boards']}个/股票{dbs['concept_codes']}只"
        )
    else:
        st.caption("尚未建立全A数据底座；建议先点击上方按钮初始化一次。")

if update_board_db:
    with st.spinner("正在初始化/更新全A数据底座：优先建立BaoStock行业库，再补充行业/概念板块缓存..."):
        foundation_statuses = build_full_a_foundation(force=True)
        # 概念库仍用东方财富/AKShare补充；失败不会影响行业底座。
        industry_map, industry_status = build_board_map("industry", force=True, max_boards=99999)
        concept_map, concept_status = build_board_map("concept", force=True, max_boards=99999)
    for msg in foundation_statuses:
        st.success(msg) if ("成功" in msg or "同步" in msg or "读取" in msg) else st.info(msg)
    st.success(f"行业/板块补充库：{industry_status}")
    st.success(f"概念补充库：{concept_status}")
    fds = get_foundation_status()
    dbs = get_board_db_status()
    st.info(
        f"全A底座覆盖：BaoStock行业 {fds['baostock_industries']} 个，股票 {fds['baostock_codes']} 只；"
        f"行业板块 {dbs['industry_boards']} 个，行业股票 {dbs['industry_codes']} 只；"
        f"概念板块 {dbs['concept_boards']} 个，概念股票 {dbs['concept_codes']} 只。"
    )



if 'run_scan' in globals() and run_scan:
    st.markdown("# V16.6 起势机会扫描")
    st.caption("目标：扫描A/B候选，并给出B档类型、排序原因和该股历史类似信号表现。A档仍然严格，B档用于小仓试探。")
    if scan_source == "自定义列表":
        scan_codes = normalize_code_input(scan_codes_text)
    elif scan_source == "全A底座前N只":
        scan_codes = get_foundation_codes(int(scan_n), sample_mode="前N只")
    else:
        scan_codes = get_foundation_codes(int(scan_n), sample_mode="随机抽样")
    if not scan_codes:
        st.error("没有可扫描的股票代码。请先初始化全A数据底座，或输入自定义代码。")
        st.stop()
    start_ts = time.time()
    with st.spinner(f"正在扫描 {len(scan_codes)} 只股票的AI起势/催化信号..."):
        opp_df = scan_qishi_opportunities(scan_codes, max_items=int(scan_n))
    elapsed = time.time() - start_ts
    if opp_df.empty:
        st.warning("本次没有扫出有效结果。可能是K线缓存不足或网络请求过慢。")
        st.stop()
    st.success(f"扫描完成：{len(opp_df)} 只有效样本，耗时 {elapsed:.1f} 秒。")
    a_count = int((opp_df["buy_grade"] == "A").sum()) if "buy_grade" in opp_df else 0
    b_count = int((opp_df["buy_grade"] == "B").sum()) if "buy_grade" in opp_df else 0
    c_count = int((opp_df["buy_grade"] == "C").sum()) if "buy_grade" in opp_df else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("A档", a_count)
    c2.metric("B档", b_count)
    c3.metric("C档", c_count)
    c4.metric("平均起势分", f"{opp_df['qishi_score'].mean():.1f}")

    st.markdown("## A/B候选优先看")
    ab = opp_df[opp_df["buy_grade"].isin(["A", "B"])].copy()
    priority_cols = ["code", "name", "buy_grade", "buy_label", "signal_type", "rank_reason", "qishi_score", "red_streak", "catalyst_score", "fund_score", "hist_count", "hist_5d_win", "hist_10d_avg", "hist_20d_dd", "trade_zone", "industry", "concepts"]
    if ab.empty:
        st.info("本批没有A/B。A档严格；B档若仍少，建议扩大扫描数量或观察黄柱/红柱早期池。")
    else:
        st.dataframe(ab[[c for c in priority_cols if c in ab.columns]].head(80), use_container_width=True)

    st.markdown("## 分类池")
    col_a, col_b = st.columns(2)
    with col_a:
        st.write("**强趋势但不追高**")
        hot = opp_df[opp_df["signal_type"].astype(str).str.contains("追高", na=False)].head(30)
        if hot.empty: st.caption("暂无。")
        else: st.dataframe(hot[[c for c in priority_cols if c in hot.columns]], use_container_width=True)
    with col_b:
        st.write("**黄柱/观察池**")
        watch = opp_df[opp_df["signal_type"].astype(str).str.contains("黄柱|观察", na=False)].head(30)
        if watch.empty: st.caption("暂无。")
        else: st.dataframe(watch[[c for c in priority_cols if c in watch.columns]], use_container_width=True)

    st.markdown("## 全部扫描结果")
    st.dataframe(opp_df.head(300), use_container_width=True)
    csv = opp_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("下载机会扫描CSV", data=csv, file_name="v16_6_opportunity_scan.csv", mime="text/csv")
    st.stop()

if 'run_coverage' in globals() and run_coverage:
    st.markdown("# V15.8.1 覆盖率稳定版")
    st.caption("目标：快速检查全A数据底座、PE/PB、换手率/量比、概念覆盖和耗时。失败样本优先展示，详细表格可下载。")

    if coverage_source == "自定义列表":
        test_codes = normalize_code_input(coverage_codes_text)
    elif coverage_source == "全A底座随机抽样":
        test_codes = get_foundation_codes(int(coverage_n), sample_mode="随机抽样")
    else:
        test_codes = get_foundation_codes(int(coverage_n), sample_mode="前N只")

    if not test_codes:
        st.error("没有可检测的股票代码。请先初始化全A数据底座，或输入自定义代码。")
        st.stop()

    include_board = coverage_depth.startswith("标准")
    benchmark_df = None
    if include_board:
        benchmark_df, _, _ = get_benchmark_data(force=False)

    # V15.8.1：保留 V15.7 的东方财富主逻辑，只优化备用源与提示。
    # 主源：东方财富批量快照；兜底1：东方财富历史缓存；兜底2：仅在覆盖不足时才调用AKShare。
    COVERAGE_SPOT_LOOKUP = None
    COVERAGE_SPOT_STATUS = "未加载"
    diagnostics = []

    em_lookup, em_status = get_eastmoney_batch_quotes(test_codes, force=False)
    diagnostics.append(em_status)

    if len(em_lookup) < max(1, int(len(test_codes) * 0.80)):
        stale_lookup, stale_status = load_eastmoney_stale_cache_lookup(test_codes)
        diagnostics.append(stale_status)
        em_lookup = merge_quote_lookups(em_lookup, stale_lookup)

    ak_lookup, ak_status = maybe_load_ak_spot_for_missing(test_codes, em_lookup, threshold=0.80)
    diagnostics.append(f"AKShare备用源：{ak_status}")

    COVERAGE_SPOT_LOOKUP = merge_quote_lookups(em_lookup, ak_lookup)
    covered_count = len([c for c in test_codes if c in COVERAGE_SPOT_LOOKUP]) if isinstance(COVERAGE_SPOT_LOOKUP, dict) else 0

    if not COVERAGE_SPOT_LOOKUP:
        COVERAGE_SPOT_STATUS = "批量快照不可用"
        st.warning("批量快照不可用，本次 PE/PB、换手率、量比覆盖率可能偏低。具体原因见下方数据诊断。")
    else:
        primary_text = em_status if em_lookup else "备用源命中"
        COVERAGE_SPOT_STATUS = primary_text
        st.success(f"批量快照已载入：{primary_text}，覆盖 {covered_count} / {len(test_codes)} 只股票。")

    with st.expander("数据诊断 / 备用源详情", expanded=False):
        for msg in diagnostics:
            st.write(f"- {msg}")

    progress = st.progress(0)
    status_box = st.empty()
    rows = []
    start_ts = time.time()
    for i, c in enumerate(test_codes, start=1):
        status_box.write(f"正在检测 {i}/{len(test_codes)}：{c}")
        rows.append(check_one_stock_coverage(
            c, include_kline=True, include_basic=True, include_board=include_board, benchmark_df=benchmark_df
        ))
        progress.progress(i / len(test_codes))

    result_df = pd.DataFrame(rows)
    summary = summarize_coverage_result(result_df)
    elapsed = time.time() - start_ts

    st.success(f"检测完成：{summary.get('total', 0)} 只，耗时 {elapsed:.1f} 秒，平均 {elapsed/max(summary.get('total', 1),1):.2f} 秒/只。")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("行业识别", f"{summary.get('industry_rate',0):.1f}%")
    c2.metric("概念识别", f"{summary.get('concept_rate',0):.1f}%")
    c3.metric("行情成功", f"{summary.get('kline_rate',0):.1f}%")
    c4.metric("基本面成功", f"{summary.get('basic_rate',0):.1f}%")
    c5.metric("PE/PB成功", f"{summary.get('pepb_rate',0):.1f}%")
    c6.metric("换手/量比", f"{summary.get('turnover_rate',0):.1f}%")

    if include_board:
        st.metric("行业对比成功率", f"{summary.get('board_rate',0):.1f}%")
    st.metric("平均覆盖分", f"{summary.get('avg_score',0):.1f}/100")

    st.markdown("## 失败样本优先看")
    fail_cols = ["industry_ok", "concept_ok", "pe_pb_ok", "turnover_ok", "kline_ok"]
    fail_df = result_df[result_df[fail_cols].eq(False).any(axis=1)].copy() if all(c in result_df.columns for c in fail_cols) else pd.DataFrame()
    if fail_df.empty:
        st.success("这批样本没有明显失败项。")
    else:
        show_cols = ["code", "name", "industry", "concepts", "industry_ok", "concept_ok", "pe_pb_ok", "turnover_ok", "kline_ok", "pepb_source", "turnover_source", "status"]
        st.dataframe(fail_df[[c for c in show_cols if c in fail_df.columns]], use_container_width=True)

    st.markdown("## 覆盖明细")
    st.dataframe(result_df, use_container_width=True)
    csv = result_df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button("下载检测结果 CSV", data=csv, file_name="v16_coverage_result.csv", mime="text/csv")

    st.markdown("## 下一步怎么用这个结果")
    st.write("- 行业识别低：说明全A行业底座没建立好，优先检查 BaoStock 初始化。")
    st.write("- PE/PB 或换手率低：先看 pepb_source / turnover_source；如果全是无，说明快照字段未命中，下一步接腾讯字段或 Tushare 兜底。")
    st.write("- 行业对比低：说明同行样本或K线缓存不足，应先减少板块样本或补缓存。")
    st.stop()

if st.button("生成V16.7 实用融合报告"):
    with st.spinner("正在生成V16.7 实用融合报告..."):
        code = str(stock_code).strip().zfill(6)
        meta = get_meta_from_master(code)

        boards = detect_boards(code, force=False)
        detected_industry = boards["industry"]
        industry_detect_status = "；".join(boards["status"])
        detected_concepts = boards["concepts"]

        if detected_industry != "行业未识别":
            meta["industry"] = detected_industry

        name = meta["name"]

        stock_df, stock_status = get_kline(code, force_update)
        benchmark_df, benchmark_name, benchmark_status = get_benchmark_data(force_update)

        if stock_df is None:
            st.error("该股票数据获取失败。请确认输入的是6位A股代码。")
            st.stop()

        if benchmark_df is None:
            st.warning("所有基准指数获取失败，本次将不计算有效相对强弱。")
            benchmark_df = stock_df.copy()
            benchmark_name = "个股自身替代基准"

        auto_data = fetch_auto_fundamental(code, force=force_update)
        fund_result = fetch_fund_flow(code, force=force_update)

        market = analyze_market(benchmark_df)
        relative = analyze_relative(stock_df, benchmark_df, benchmark_name)

        if quick_mode:
            industry_analysis = make_quick_board_analysis(meta["industry"], "industry")
            concept_analysis = make_quick_board_analysis(detected_concepts[0] if detected_concepts else "概念未识别", "concept")
            combined_chart = pd.DataFrame()
        else:
            industry_analysis = analyze_board_vs_stock(
                stock_code=code, board_name=meta["industry"], board_type="industry",
                stock_df=stock_df, benchmark_df=benchmark_df, force=force_update,
            )

            concept_analysis = choose_best_concept_analysis(
                stock_code=code, concepts=detected_concepts, stock_df=stock_df,
                benchmark_df=benchmark_df, force=force_update,
            )

            combined_chart = combine_stock_industry_concept_chart(
                stock_df=stock_df, benchmark_df=benchmark_df,
                industry_analysis=industry_analysis, concept_analysis=concept_analysis,
            )

        risk = risk_grade(stock_df)
        plan = advanced_trade_plan(stock_df, industry_analysis, concept_analysis)
        fundamental = analyze_fundamental(auto_data)
        fund_analysis = analyze_fund_flow(fund_result, stock_df)
        qishi = compute_qishi_tracking(stock_df, benchmark_df, industry_analysis, concept_analysis, fund_analysis)
        catalyst_analysis = analyze_catalyst(
            code=code, name=name, industry=meta["industry"], concepts=detected_concepts,
            qishi=qishi, fund_analysis=fund_analysis, concept_analysis=concept_analysis,
            force=force_update, fetch_online=(online_catalyst and not quick_mode)
        )

        completeness_score, completeness_items = data_completeness(
            auto_data, industry_analysis, concept_analysis, fund_analysis, stock_status, benchmark_status
        )

        score = score_system(stock_df, market, relative, industry_analysis, concept_analysis, risk, plan, fundamental, fund_analysis)
        final = apply_risk_override(score["total"], risk, plan, fundamental, industry_analysis, concept_analysis, fund_analysis)

        position_pct, position_text = dynamic_position(final, risk, plan, industry_analysis, concept_analysis, fund_analysis)

        # V15：价格状态与买入信号分离；缺数据不直接等于坏数据
        plan = apply_signal_priority_gate(
            final=final, risk=risk, plan=plan, industry_analysis=industry_analysis,
            concept_analysis=concept_analysis, fund_analysis=fund_analysis, position_pct=position_pct
        )

        buy_signal = generate_buy_signal(
            final=final, risk=risk, plan=plan, industry_analysis=industry_analysis,
            concept_analysis=concept_analysis, fund_analysis=fund_analysis, catalyst_analysis=catalyst_analysis
        )
        buy_signal = apply_qishi_to_buy_signal(buy_signal, qishi, risk, plan)
        buy_signal = apply_catalyst_to_buy_signal(buy_signal, qishi, catalyst_analysis, risk, plan, fund_analysis)
        l2_analysis = analyze_manual_level2(big_order_direction, orderbook_strength, tick_direction, l2_note)
        buy_signal = apply_manual_level2_to_buy_signal(buy_signal, l2_analysis, qishi, risk)
        signal_type = classify_qishi_signal_type(qishi, plan, catalyst_analysis, fund_analysis)
        signal_validation = {"count": 0, "summary": "快看模式未运行历史验证"} if quick_mode else validate_qishi_signal_history(stock_df, qishi, signal_type, lookback=260, horizon_list=(3,5,10,20))
        trend_grade = get_trend_grade(qishi, plan, risk)
        trigger_conditions = generate_trigger_conditions(qishi, plan, buy_signal)
        grade_gap_notes = explain_grade_gap(buy_signal, qishi, plan, catalyst_analysis, fund_analysis)

        # 按买入信号重新映射新仓上限，避免“买入信号B但新仓0%”
        if buy_signal["grade"] == "A":
            position_pct = max(position_pct, 40)
        elif buy_signal["grade"] == "B":
            position_pct = max(position_pct, 20)
        elif buy_signal["grade"] in ["C", "D"]:
            position_pct = 0

        new_advice, existing_advice = generate_position_advice(final, risk, plan, position_pct, buy_signal)
        holding_advice = generate_portfolio_advice(
            has_position, cost_price_input, holding_pct_input, trade_horizon,
            plan["price"], final, risk, plan, buy_signal
        )
        position_action_label = summarize_position_action(has_position, final, risk, plan, buy_signal)
        if quick_mode:
            bt, stats = pd.DataFrame(), {"count": 0, "win_rate_5d": 0, "win_rate_10d": 0, "win_rate_20d": 0, "avg_max_gain_20d": 0, "avg_max_loss_20d": 0, "sample_quality": "快看模式未回测"}
        else:
            bt, stats = backtest(stock_df)

    st.success(
        f"股票：{name}（{code}）｜行业：{meta['industry']}｜个股数据：{stock_status}｜基准指数：{benchmark_name}｜基准状态：{benchmark_status}"
    )

    st.markdown("## 一、今日操作结论")
    st.info(f"""
**趋势等级：** {trend_grade['grade']}（{trend_grade['desc']}）｜**AI起势：** {qishi.get('state', '未知')}｜起势分 **{qishi.get('score', 0):.1f}/100**｜红柱连续 **{qishi.get('red_streak', 0)} 天**  
**当前买点：** {buy_signal['grade']}：{buy_signal['label']}｜**类型：** {signal_type}｜**建议新仓：** {position_pct}%  
**已有仓位：** {position_action_label}｜**今日状态：** {plan.get('intraday_status', '未知')}｜**风控：** {risk['level']}｜**数据完整度：** {completeness_score}%  
**Level-2人工确认：** {l2_analysis.get('grade')}（{l2_analysis.get('score'):.0f}/100）
""")

    col_buy, col_sell = st.columns(2)
    with col_buy:
        st.markdown("### 升级到更强买点的条件")
        for x in trigger_conditions['upgrade'][:4]:
            st.write(f"- {x}")
    with col_sell:
        st.markdown("### 降级/减仓条件")
        for x in trigger_conditions['downgrade'][:4]:
            st.write(f"- {x}")

    st.markdown("### 为什么不是更高等级")
    for x in grade_gap_notes:
        st.write(f"- {x}")

    st.info(
        f"""
{name}（{code}）｜{meta['industry']}｜**新开仓和已有仓位分开判断**。

V16.6 重点：**AI起势柱 + 催化剂评分 + 买卖点联动。A档严格，B档允许小仓试探；没有催化/资金确认不轻易给A。**

价格状态：**{plan['price_zone_status']}**。  
交易状态：**{plan['trade_zone']}**。  
今日状态：**{plan.get('intraday_status', '未知')}**。  
AI起势追踪：**{qishi.get('state', '未知')}（{qishi.get('score', 0):.1f}/100）**。  
买入信号：**{buy_signal['grade']}：{buy_signal['label']}**。  
建议新开仓上限：**{position_pct}%**。  
已有仓位动作：**{position_action_label}**。  
数据完整度：**{completeness_score}%**（{completeness_items}）。

说明：低吸价格区只是“价格位置”，不等于可以买。V15 会综合实时行情、行业/概念、资金/量价、盈亏比和持仓成本。

新开仓建议：{new_advice}  
通用已有仓位建议：{existing_advice}  
结合我的成本/仓位建议：{holding_advice}
"""
    )

    st.markdown("## 二、AI起势追踪")
    st.info(f"""
**当前状态：** {qishi.get('state', '未知')}｜**起势分：** {qishi.get('score', 0):.1f}/100｜**连续起势柱：** {qishi.get('streak', 0)} 天｜**红/深红连续：** {qishi.get('red_streak', 0)} 天  
**算法：** 起势强度 = 趋势结构 + 资金量能 + 平台突破 + 连续性 + 相对强弱。追高风险单独显示，不再压没红柱。  
**柱子含义：** 0-37无柱｜38-61黄柱｜62-84红柱｜85以上深红柱
""")

    comp_scores = qishi.get("component_scores", {}) or {}
    comp_max = qishi.get("component_max", {}) or {}
    if comp_scores:
        comp_rows = []
        for k in ["趋势结构", "资金量能", "突破强度", "平台突破", "行业概念", "追高风险"]:
            comp_rows.append({
                "模块": k,
                "得分/扣分": comp_scores.get(k, 0),
                "上限": comp_max.get(k, ""),
            })
        st.dataframe(pd.DataFrame(comp_rows), use_container_width=True)

    st.markdown("### 柱子原因")
    for r in qishi.get('reasons', [])[:5]:
        st.write(f"- {r}")

    comp_reasons = qishi.get("component_reasons", {}) or {}
    with st.expander("分项原因：趋势、资金、突破、动量、行业、风险", expanded=True):
        for k in ["趋势结构", "资金量能", "突破强度", "平台突破", "行业概念", "追高风险"]:
            st.markdown(f"**{k}**")
            reasons = comp_reasons.get(k, [])
            if reasons:
                for x in reasons[:4]:
                    st.write(f"- {x}")
            else:
                st.write("- 本项没有明显加分/扣分。")

    st.markdown("## 三、催化剂评分")
    st.write(f"- 催化剂状态：**{catalyst_analysis.get('grade', '未评估')}**")
    st.write(f"- 催化剂分：**{catalyst_analysis.get('score', 0):.0f}/100**")
    st.write(f"- 数据来源：**{catalyst_analysis.get('source', '本地题材/可选公告')}**")
    for r in catalyst_analysis.get("reasons", [])[:8]:
        st.write(f"- {r}")
    if catalyst_analysis.get("items"):
        with st.expander("公告/催化明细", expanded=False):
            st.dataframe(pd.DataFrame(catalyst_analysis.get("items", [])[:20]), use_container_width=True)

    st.markdown("## 四、信号历史验证")
    st.write(f"- 当前信号类型：**{signal_type}**")
    if signal_validation.get("enabled"):
        vcols = st.columns(5)
        vcols[0].metric("类似样本", signal_validation.get("count", 0))
        vcols[1].metric("5日胜率", f"{signal_validation.get('win_rate_5d',0):.1f}%")
        vcols[2].metric("10日均收益", f"{signal_validation.get('avg_ret_10d',0):.2f}%")
        vcols[3].metric("20日均最大涨幅", f"{signal_validation.get('avg_max_gain_20d',0):.2f}%")
        vcols[4].metric("20日均回撤", f"{signal_validation.get('avg_max_drawdown_20d',0):.2f}%")
        st.caption("说明：这是该股历史上类似AI起势信号后的统计，只作概率参考，不是未来保证。")
        with st.expander("最近类似信号样本", expanded=False):
            st.dataframe(pd.DataFrame(signal_validation.get("recent_samples", [])), use_container_width=True)
    else:
        st.info("该股类似历史信号样本不足：" + str(signal_validation.get("reason", "暂无统计")))

    fig = plot_qishi_tracking(qishi)
    if fig is not None:
        st.pyplot(fig)

    with st.expander("AI起势追踪：算法参考与升级/降级条件", expanded=False):
        st.markdown("""
**参考的市场逻辑**  
- 趋势结构：MA5/MA10/MA20/MA60、多头排列、MA20斜率。  
- 资金量能：成交量相对20日均量、5日均量相对20日均量、上涨放量、放量下跌扣分。  
- 突破强度：20日高点、60日高点、Bollinger上轨。  
- 平台突破：5日、20日、60日收益。  
- 行业概念：个股相对行业、概念、基准是否有共振。  
- 风险扣分：距离MA20过远、短期涨幅过大、放量下跌、跌破MA10/MA20。

它不是东方财富/同花顺的专有公式复制，而是一个可解释、可调参的趋势+资金+突破组合模型。
""")
        st.markdown("**信号升级条件**")
        for x in qishi.get('upgrade_conditions', []):
            st.write(f"- {x}")
        st.markdown("**信号降级条件**")
        for x in qishi.get('downgrade_conditions', []):
            st.write(f"- {x}")

    st.markdown("### AI起势驱动的买卖点精进")
    for x in qishi.get("trade_refinement", []):
        st.write(f"- {x}")

    st.markdown("## 三、今日操作结论 / 买卖点")
    st.write(f"- **新开仓**：{buy_signal['grade']}：{buy_signal['label']}。{new_advice}")
    st.write(f"- **已有仓位**：{position_action_label}。{holding_advice}")
    st.write(f"- **今日盘中状态**：{plan.get('intraday_status', '未知')}。{plan.get('intraday_explain', '')}")
    st.write(f"- **低吸价格区**：{plan['support_band_low']:.2f} ~ {plan['support_band_high']:.2f}；当前价格状态：{plan['price_zone_status']}。")
    st.write(f"- **加仓/突破观察位**：{plan['key_resistance']:.2f}；若站稳并放量/资金改善，信号可能升级。")
    st.write(f"- **减仓风控位**：{plan['stop_loss']:.2f}；强风控线：{plan['hard_stop']:.2f}。")
    st.write(f"- **止盈压力区**：目标1 {plan['target1']:.2f}，目标2 {plan['target2']:.2f}，目标3 {plan['target3']:.2f}。")

    st.line_chart(stock_df.set_index("date")[["close", "MA20", "MA60", "MA120"]])

    st.markdown("## 四、可信数据状态")
    st.write(f"- 股票名称：**{name}**")
    st.write(f"- 股票代码：**{code}**")
    st.write(f"- 市场：**{meta['market']}**")
    st.write(f"- 行业识别：**{meta['industry']}**")
    st.write(f"- 行业/概念识别状态：**{clean_status_for_main(industry_detect_status)}**")
    st.write(f"- 行情数据来源：**腾讯行情 / {stock_status}**")
    st.write(f"- 基准指数：**{benchmark_name} / {benchmark_status}**")
    st.write(f"- Tushare增强层：**{'已启用' if USE_TUSHARE else '未启用'}**")
    st.write(f"- 数据完整度：**{completeness_score}%**（{completeness_items}）")
    if detected_concepts:
        st.write(f"- 识别到的概念：**{', '.join(detected_concepts[:8])}**")
    if auto_data["success"]:
        st.success("自动基本面/交易数据获取成功：有效字段已参与评分。")
    else:
        st.warning("自动基本面/交易数据未成功获取：本次不参与基本面评分。")
    if industry_analysis["enabled"]:
        st.success("行业对比已启用。")
    else:
        pass  # 不在主界面刷“不可用”，详细原因放到数据诊断。
    if fund_analysis["enabled"]:
        if fund_analysis.get("is_proxy", False):
            st.info("真实资金流未取到，已启用量价代理信号。")
        else:
            st.success("真实大资金流已启用。")
    # 诊断信息折叠，避免页面被无效消息刷屏。
    with st.expander("数据诊断", expanded=False):
        st.write(f"- 原始行业/概念识别状态：{industry_detect_status}")
        if not industry_analysis["enabled"]:
            for r in industry_analysis.get("reasons", []):
                st.write(f"- 行业对比：{r}")
        if not concept_analysis["enabled"]:
            for r in concept_analysis.get("reasons", []):
                st.write(f"- 概念对比：{r}")
        for note in auto_data["notes"]:
            st.write(f"- 基本面：{note}")
        for note in fund_result["notes"]:
            st.write(f"- 资金流：{note}")

    st.markdown("## 五、个股 vs 行业 vs 概念 vs 基准指数走势")
    st.caption(
        "图中的 Value 是标准化走势，不是股价。起点统一设为 100。"
        "例如 Value=110 代表从起点上涨 10%，Value=90 代表从起点下跌 10%。"
        "曲线名称会直接显示为 行业：xxx / 概念：xxx，避免看不懂概念线代表什么。"
    )
    if combined_chart is not None and not combined_chart.empty:
        st.line_chart(combined_chart.set_index("date"))
    else:
        st.caption("行业/概念对比图未生成；本次主界面只保留可用结论，详细原因在数据诊断。")

    st.markdown("## 六、最终买入信号")
    st.write(f"- 买入信号等级：**{buy_signal['grade']}：{buy_signal['label']}**")
    st.write(f"- 操作建议：**{buy_signal['action']}**")
    for r in buy_signal["reasons"]:
        st.write(f"- {r}")

    st.markdown("## 七、大资金确认")
    if fund_analysis["enabled"]:
        st.write(f"- 资金状态：**{fund_analysis['grade']}**")
        st.write(f"- 资金评分：**{fund_analysis['score']:.1f}/100**")

        for label, key in [
            ("近3日主力累计净流入", "main_3"),
            ("近5日主力累计净流入", "main_5"),
            ("近10日主力累计净流入", "main_10"),
            ("近20日主力累计净流入", "main_20"),
            ("近5日超大单累计净流入", "super_5"),
            ("近10日超大单累计净流入", "super_10"),
            ("近5日大单累计净流入", "big_5"),
            ("近10日大单累计净流入", "big_10"),
        ]:
            val = fund_analysis.get(key)
            if val is not None:
                st.write(f"- {label}：**{format_money(val)}**")

        show_value("近10日主力净流入天数占比", fund_analysis.get("main_pos_10"), "%")
        show_value("近20日主力净流入天数占比", fund_analysis.get("main_pos_20"), "%")
        show_value("最新主力净占比", fund_analysis.get("latest_main_ratio"), "%")
        show_value("量价代理量能比", fund_analysis.get("proxy_volume_ratio"))
        show_value("近5日量价代理强度", fund_analysis.get("proxy_signed_power_5"))
        show_value("近10日量价代理强度", fund_analysis.get("proxy_signed_power_10"))
        st.write(f"- 资金价格关系：**{fund_analysis['divergence']}**")
        for r in fund_analysis["reasons"]:
            st.write(f"- {r}")
    else:
        st.warning("资金流数据未可靠获取，本次不参与买入信号。")

    if industry_analysis["enabled"]:
        st.markdown("## 八、行业与个股强弱对比")
        st.write(f"- 行业：**{industry_analysis['board_name']}**")
        st.write(f"- 行业对比评级：**{industry_analysis['grade']}**")
        for r in industry_analysis["reasons"]:
            st.write(f"- {r}")
        st.dataframe(industry_analysis["rows"])

    if concept_analysis["enabled"]:
        st.markdown("## 六、概念与个股强弱对比")
        st.write(f"- 最相关概念：**{concept_analysis['board_name']}**")
        st.write(f"- 概念对比评级：**{concept_analysis['grade']}**")
        for r in concept_analysis["reasons"]:
            st.write(f"- {r}")
        st.dataframe(concept_analysis["rows"])

    st.markdown("## 七、自动基本面 / 交易活跃度")
    if auto_data["success"]:
        show_value("PE TTM", auto_data["pe_ttm"])
        show_value("动态PE", auto_data["pe_dynamic"])
        show_value("PB", auto_data["pb"])
        show_value("PS TTM", auto_data["ps_ttm"])
        show_value("股息率", auto_data["dividend_yield"], "%")
        show_value("总市值", auto_data["market_cap"])
        show_value("换手率", auto_data["turnover"], "%")
        show_value("量比", auto_data["volume_ratio"])
        show_value("ROE", auto_data["roe"], "%")
        show_value("营收增速", auto_data["revenue_growth"], "%")
        show_value("净利润增速", auto_data["profit_growth"], "%")
        st.write(f"- 基本面评级：**{fundamental['grade']}**")
        for r in fundamental["reasons"]:
            st.write(f"- {r}")
    else:
        st.warning("自动基本面缺失或不可靠，本次不展示详细基本面，也不参与评分。")

    st.markdown("## 八、评分拆解")
    for name_component, raw_score, weight in score["components"]:
        st.write(f"- {name_component}：**{raw_score:.1f}/100**，权重 **{weight}**")
    st.write(f"参与评分总权重：**{score['total_weight']}**")
    st.write(f"基础综合评分：**{score['total']:.1f}**")
    st.write(f"风险调整后评分：**{final['adjusted_score']:.1f}**")

    st.markdown("## 九、市场环境")
    st.write(f"- 使用基准指数：**{benchmark_name}**")
    st.write(f"- 市场状态：**{market['status']}**")
    st.write(f"- 市场分数：**{market['score']}/100**")
    st.write(f"- 20日收益：**{market['ret20']:.2f}%**")
    st.write(f"- 60日收益：**{market['ret60']:.2f}%**")
    st.write(f"- 20日波动率：**{market['vol20']:.2f}%**")
    st.write(f"- 60日回撤：**{market['drawdown60']:.2f}%**")
    for r in market["reasons"]:
        st.write(f"- {r}")

    st.markdown("## 十、相对大盘强弱")
    st.write(f"- 个股近20日收益：**{relative['stock_ret20']:.2f}%**")
    st.write(f"- 基准指数近20日收益：**{relative['index_ret20']:.2f}%**")
    st.write(f"- 20日超额收益：**{relative['excess20']:.2f}%**")
    st.write(f"- 个股近60日收益：**{relative['stock_ret60']:.2f}%**")
    st.write(f"- 基准指数近60日收益：**{relative['index_ret60']:.2f}%**")
    st.write(f"- 60日超额收益：**{relative['excess60']:.2f}%**")
    st.write(f"- 结论：**{relative['status']}**")
    st.write(relative["explain"])

    st.markdown("## 十一、风险分析：涨幅透支 + 波动 + 距离均线")
    st.write(f"- 风险等级：**{risk['level']}**")
    st.write(f"- 近20日涨幅：**{risk['ret20']:.2f}%**")
    st.write(f"- 近60日涨幅：**{risk['ret60']:.2f}%**")
    st.write(f"- 近120日涨幅：**{risk['ret120']:.2f}%**")
    st.write(f"- 20日波动率：**{risk['volatility']:.2f}%**")
    st.write(f"- 60日回撤：**{risk['drawdown']:.2f}%**")
    st.write(f"- ATR占股价比例：**{risk['atr_pct']:.2f}%**")
    st.write(f"- 距离MA60：**{risk['dist_ma60']:.2f}%**")
    for r in risk["reasons"]:
        st.write(f"- {r}")

    st.markdown("## 十二、精细买点算法")
    st.write(f"- 价格状态：**{plan['price_zone_status']}**")
    st.write(f"- 价格区间说明：{plan['price_zone_explain']}")
    st.write(f"- 当前交易状态：**{plan['trade_zone']}**")
    st.write(f"- 低吸有效性：**{plan['low_validity']}**")
    st.write(f"- 低吸有效性评分：**{plan['low_absorb_score']} / 100**")
    st.write(f"- 关键支撑：**{plan['key_support']:.2f}**")
    st.write(f"- 低吸观察区：**{plan['support_band_low']:.2f} ~ {plan['support_band_high']:.2f}**")
    st.write(f"- 关键压力：**{plan['key_resistance']:.2f}**")
    st.write(f"- 回踩确认区：**{plan['pullback_low']:.2f} ~ {plan['pullback_high']:.2f}**")
    st.write(f"- 风控止损：**{plan['stop_loss']:.2f}**")
    st.write(f"- 强风控线：**{plan['hard_stop']:.2f}**")
    st.write(f"- 目标1：**{plan['target1']:.2f}**")
    st.write(f"- 目标2：**{plan['target2']:.2f}**")
    st.write(f"- 目标3：**{plan['target3']:.2f}**")
    st.write(f"- 量能比 VOL5/VOL20：**{plan['volume_ratio']:.2f}**")
    st.write(f"- 盈亏比：**{plan['reward_risk']:.2f}**")
    st.write(f"- 盈亏比评价：{plan['rr_comment']}")

    st.markdown("### 低吸有效性原因")
    for x in plan["low_reasons"]:
        st.write(f"- {x}")

    st.markdown("### 低吸触发条件")
    for x in plan["low_absorb_triggers"]:
        st.write(f"- {x}")

    st.markdown("### 突破触发条件")
    for x in plan["breakout_triggers"]:
        st.write(f"- {x}")

    st.markdown("## 十三、Bull / Base / Pullback / Bear 情景交易计划")
    st.write(f"**Base Case：** {plan['base_case']}")
    st.write(f"**Bull Case：** {plan['bull_case']}")
    st.write(f"**Pullback Case：** {plan['pullback_case']}")
    st.write(f"**Bear Case：** {plan['bear_case']}")

    st.markdown("## 十四、新开仓 / 已有仓位建议")
    st.write(f"**建议新仓上限：** {position_pct}%")
    st.write(f"**仓位说明：** {position_text}")
    st.write(f"**新开仓建议：** {new_advice}")
    st.write(f"**通用已有仓位建议：** {existing_advice}")
    st.write(f"**结合我的成本/仓位建议：** {holding_advice}")

    st.markdown("## 十五、风险否决机制")
    if final["notes"]:
        for note in final["notes"]:
            st.write(f"- {note}")
    else:
        st.write("- 没有触发重大风险否决。")

    st.markdown("## 十六、历史回测")
    st.write(f"- 历史信号次数：**{stats['count']}**")
    st.write(f"- 样本质量：**{stats['sample_quality']}**")
    st.write(f"- 5日胜率：**{stats['win_rate_5d']:.2f}%**")
    st.write(f"- 10日胜率：**{stats['win_rate_10d']:.2f}%**")
    st.write(f"- 20日胜率：**{stats['win_rate_20d']:.2f}%**")
    st.write(f"- 20日平均最大上涨：**{stats['avg_max_gain_20d']:.2f}%**")
    st.write(f"- 20日平均最大回撤：**{stats['avg_max_loss_20d']:.2f}%**")

    st.markdown("## 十七、解释型研报结论")
    st.write(f"当前最终结论为：**{final['final_rating']} / {final['final_light']}**。")
    st.write(f"当前买入信号为：**{buy_signal['grade']}：{buy_signal['label']}**。")
    st.write("V15 的核心是实战信号：数据源分层、持仓建议、买入信号优先：股价进入低吸区只是价格事实，不等于可以买。只有价格位置、行业/概念共振、真实资金流或强量价代理、盈亏比和风险等级同时满足，买入信号才会升级。")
    st.write(f"如果后续股价突破 **{plan['key_resistance']:.2f}** 且主力资金继续回流，同时行业/概念没有转弱，信号可信度提高。")
    st.write(f"如果跌破 **{plan['stop_loss']:.2f}**，说明交易假设失败；如果跌破 **{plan['hard_stop']:.2f}**，应进入强风控。")

    st.subheader("最近20个交易日行情")
    st.dataframe(stock_df.tail(20))

    st.subheader("历史回测信号")
    st.dataframe(bt.tail(20))
