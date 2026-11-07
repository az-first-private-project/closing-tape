"""东方财富板块（BK 代码）名单与成分。

THS 板块成分页只开放前 5 页（100/50 只），大板块拿不全。东财 clist
接口支持完整分页，按板块名对齐后作为大板块成分的补全源。

仅用于补全：THS 能完整覆盖的板块优先用 THS，保证代码口径一致。
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import Any, Iterable

logger = logging.getLogger(__name__)

CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_BOARD_FS = {"industry": "m:90+t:2", "concept": "m:90+t:3"}

# THS 概念名与东财概念名的同义差异项（语义相同，仅措辞不同）。
# 只收录两个源指向同一概念的改名/缩写；口径不同（如「上海国企改革」vs
# 「央国企改革」）或一方范围更宽（如「细胞免疫治疗」vs「免疫治疗」）的不收录，
# 否则会把不同范围的成分写进同一板块。
CONCEPT_ALIASES: dict[str, list[str]] = {
    "新能源汽车": ["新能源车"],
    "上海自贸区": ["上海自贸"],
    "中字头股票": ["中字头"],
    "装配式建筑": ["装配建筑"],
    "人民币贬值受益": ["贬值受益"],
    "央企国企改革": ["央国企改革"],
    "知识产权保护": ["知识产权"],
    "水利": ["水利建设"],
    "京津冀一体化": ["京津冀"],
    "阿里巴巴概念": ["阿里概念"],
    "PPP概念": ["PPP模式"],
    "摘帽": ["近期摘帽"],
    # THS 把同义名放进括号，归一化会连同括号内容一起删掉，需显式指向括号内的东财名
    "IP经济(谷子经济)": ["谷子经济"],
    "MR(混合现实)": ["混合现实"],
    "新型烟草(电子烟)": ["电子烟"],
}

# THS 行业名与东财行业/二级行业名的差异项（东财更细，一个 THS 板块可能对应多个）
INDUSTRY_ALIASES: dict[str, list[str]] = {
    "港口航运": ["航运港口"],
    "公路铁路运输": ["铁路公路"],
    "机场航运": ["航空机场"],
    "军工装备": ["航空装备Ⅱ", "航天装备Ⅱ", "地面兵装Ⅱ", "航海装备Ⅱ"],
    "零售": ["一般零售", "专业连锁Ⅱ"],
    "旅游及酒店": ["旅游及景区", "酒店餐饮"],
    "煤炭开采加工": ["煤炭开采", "焦炭Ⅱ"],
    "汽车服务及其他": ["汽车服务", "汽车经销商", "汽车综合服务"],
    "汽车整车": ["乘用车", "商用车"],
    "其他社会服务": ["专业服务", "体育Ⅱ", "人力资源服务"],
    "塑料制品": ["塑料", "其他塑料制品"],
    "食品加工制造": ["食品加工"],
    "石油加工贸易": ["炼化及贸易", "油品石化贸易"],
    "文化传媒": ["传媒"],
    "橡胶制品": ["橡胶", "其他橡胶制品"],
    "饮料制造": ["饮料乳品", "其他酒类"],
    "油气开采及服务": ["油气开采Ⅱ", "油服工程"],
    "种植业与林业": ["种植业", "林业Ⅱ"],
}


def _get(params: dict[str, Any], *, attempts: int = 5, timeout: int = 20) -> dict:
    from curl_cffi import requests as cffi_requests

    last: Exception | None = None
    for n in range(1, attempts + 1):
        try:
            response = cffi_requests.get(
                CLIST_URL,
                params=params,
                impersonate="chrome",
                timeout=timeout,
            )
            payload = response.json()
            data = payload.get("data")
            if data:
                return data
            last = RuntimeError(f"空数据 rc={payload.get('rc')}")
        except Exception as exc:  # noqa: BLE001 —— 网络层重试
            last = exc
        time.sleep(1.2 * n)
    raise RuntimeError(f"东财 clist 请求失败：{last}")


def _diff_list(data: dict) -> list[dict]:
    diff = data.get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    return diff


def fetch_board_list(sector_type: str) -> list[dict[str, str]]:
    """东财行业/概念板块清单（完整分页）。"""
    fs = _BOARD_FS[sector_type]
    boards: list[dict[str, str]] = []
    total = None
    page = 1
    while True:
        data = _get({
            "pn": page, "pz": 100, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f12",
            "fs": fs, "fields": "f12,f14", "ut": _UT,
        })
        diff = _diff_list(data)
        for item in diff:
            if item.get("f12") and item.get("f14"):
                boards.append({"em_code": str(item["f12"]), "em_name": str(item["f14"])})
        total = int(data.get("total") or 0)
        if not diff or len(boards) >= total:
            break
        page += 1
        time.sleep(0.15)
    logger.info("东财%s板块 %d 个", sector_type, len(boards))
    return boards


def fetch_board_members(em_code: str, *, attempts: int = 4) -> list[dict[str, str]]:
    """东财板块完整成分股（分页）。"""
    members: list[dict[str, str]] = []
    total = None
    page = 1
    while True:
        data = _get({
            "pn": page, "pz": 100, "po": 1, "np": 1,
            "fltt": 2, "invt": 2, "fid": "f12",
            "fs": f"b:{em_code}", "fields": "f12,f14", "ut": _UT,
        }, attempts=attempts)
        diff = _diff_list(data)
        for item in diff:
            code = str(item.get("f12") or "").strip()
            if code.isdigit() and len(code) == 6:
                members.append({
                    "stock_code": code,
                    "stock_name": str(item.get("f14") or code),
                })
        total = int(data.get("total") or 0)
        if not diff or len(members) >= total or page >= 50:
            break
        page += 1
        time.sleep(0.15)
    return members


def normalize_name(name: str) -> str:
    """板块名归一化：全角转半角、去括号注释、去 Ⅱ/Ⅲ 与概念/板块后缀。"""
    text = unicodedata.normalize("NFKC", str(name or "")).strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[（(].*?[)）]", "", text)
    text = re.sub(r"(概念|板块|指数)$", "", text)
    text = re.sub(r"(Ⅱ|Ⅲ|Ⅳ)+$", "", text)
    return text


def build_em_mapping(ths_boards: Iterable[dict[str, str]]) -> dict[str, dict]:
    """THS 板块 → 东财板块映射（同名归一化优先 + 行业/概念别名）。"""
    em_industry = fetch_board_list("industry")
    em_concept = fetch_board_list("concept")

    by_type: dict[str, dict[str, list[dict[str, str]]]] = {"industry": {}, "concept": {}}
    raw: dict[str, list[dict[str, str]]] = {"industry": [], "concept": []}
    for sector_type, boards in (("industry", em_industry), ("concept", em_concept)):
        raw[sector_type] = boards
        for board in boards:
            by_type[sector_type].setdefault(normalize_name(board["em_name"]), []).append(board)

    mapping: dict[str, dict] = {}
    unmatched: list[str] = []
    for board in ths_boards:
        sector_type = board["sector_type"]
        name = board["sector_name"]
        hits: list[dict[str, str]] = []

        if sector_type == "industry":
            for alias in INDUSTRY_ALIASES.get(name, []):
                hits.extend(by_type["industry"].get(normalize_name(alias), []))
        if not hits:
            hits = by_type.get(sector_type, {}).get(normalize_name(name), [])
        if not hits and sector_type == "concept":
            for alias in CONCEPT_ALIASES.get(name, []):
                hits.extend(by_type["concept"].get(normalize_name(alias), []))
        hits = [hit for hit in hits if hit["em_code"]]

        if hits:
            mapping[board["sector_code"]] = {
                "em_codes": [hit["em_code"] for hit in hits],
                "em_names": [hit["em_name"] for hit in hits],
            }
        else:
            unmatched.append(f"{sector_type}:{name}")

    logger.info(
        "东财映射：匹配 %d/%d，未匹配 %d",
        len(mapping), len(mapping) + len(unmatched), len(unmatched),
    )
    if unmatched:
        logger.info("东财未匹配板块：%s", " | ".join(unmatched[:40]))
    return mapping


__all__ = [
    "CONCEPT_ALIASES",
    "INDUSTRY_ALIASES",
    "fetch_board_list",
    "fetch_board_members",
    "normalize_name",
    "build_em_mapping",
]
