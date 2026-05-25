#!/usr/bin/env python3
"""Build configurable arXiv literature reports.

The engine intentionally uses only the Python standard library so it can run in
clean local and automation environments. It queries the public arXiv API,
filters records by the last-updated timestamp, deduplicates by base arXiv ID,
and writes standalone HTML plus JSON/TXT sidecars.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import html
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

API_URL = "https://export.arxiv.org/api/query"
OAI_URL = "https://export.arxiv.org/oai2"
USER_AGENT = "ArxivLiteratureReport/1.0 (+https://arxiv.org/help/api)"
SEEN_STATE_FILENAME = "arxiv_literature_seen_ids.json"
SUMMARY_STATE_FILENAME = "arxiv_literature_cn_summaries.json"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc
DAILY_SUBDIR = "\u65e5\u62a5"
WEEKLY_SUBDIR = "\u5468\u62a5"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
OAI = "{http://www.openarchives.org/OAI/2.0/}"
OAI_ARXIV = "{http://arxiv.org/OAI/arXiv/}"
RATE_LIMIT_BODY = "rate exceeded"
OAI_FALLBACK_SETS = (
    "physics:cond-mat",
    "physics:physics",
    "physics:quant-ph",
    "eess:eess",
)


class ArxivRateLimitError(RuntimeError):
    """Raised when arXiv asks the client to slow down."""

TOPICS = [
    {
        "key": "exciton",
        "name": "激子极化激元",
        "query": (
            'all:"exciton polariton" OR all:"exciton-polariton" OR '
            'all:"polaritonic condensate" OR all:"microcavity polariton"'
        ),
    },
    {
        "key": "tmd",
        "name": "二维/TMD 材料研究",
        "query": (
            '(all:TMDC OR all:"transition metal dichalcogenide" OR '
            'all:MoS2 OR all:MoSe2 OR all:WS2 OR all:WSe2 OR all:MoTe2 OR '
            'all:"moiré WSe2" OR all:"moire WSe2" OR all:"twisted WSe2" OR '
            'all:"moiré semiconductor" OR all:"moire semiconductor")'
        ),
    },
    {
        "key": "perovskite",
        "name": "钙钛矿极化激元",
        "query": (
            '(all:perovskite OR all:"halide perovskite" OR '
            'all:"lead halide perovskite") AND '
            '(all:polariton OR all:polaritons OR all:polaritonic OR all:"strong coupling")'
        ),
    },
]

TOPIC_ORDER = [topic["name"] for topic in TOPICS]
PRIMARY_TOPIC_ORDER = [
    "二维/TMD 材料研究",
    "钙钛矿极化激元",
    "激子极化激元",
]

MATERIAL_PATTERNS = [
    (r"\bMoS2\b|MoS₂", "MoS2"),
    (r"\bMoSe2\b|MoSe₂", "MoSe2"),
    (r"\bWS2\b|WS₂", "WS2"),
    (r"\bWSe2\b|WSe₂", "WSe2"),
    (r"\bMoTe2\b|MoTe₂", "MoTe2"),
    (r"\bTMD\b|\bTMDC\b|transition metal dichalcogenide", "TMD/TMDC"),
    (r"monolayer|bilayer|heterobilayer|van der Waals|2D material", "二维材料"),
    (r"perovskite|halide perovskite|lead halide", "钙钛矿"),
    (r"organic", "有机半导体"),
    (r"microcavity|cavity|Fabry", "微腔"),
]

PHENOMENA_PATTERNS = [
    (r"exciton[- ]polariton|exciton polariton", "激子极化激元"),
    (r"polariton condens|polaritonic condens|Bose[- ]Einstein", "极化激元凝聚"),
    (r"strong coupling", "强耦合"),
    (r"Rabi", "Rabi 劈裂/振荡"),
    (r"moir[eé]", "莫尔势/莫尔激子"),
    (r"valley", "谷自由度"),
    (r"topolog", "拓扑态"),
    (r"nonlinear|non-linearity|nonlinearity", "非线性光学"),
    (r"lasing|laser", "极化激元激光"),
    (r"transport|flow|propagation", "输运/传播"),
]

METHOD_PATTERNS = [
    (r"photoluminescence|\bPL\b", "光致发光"),
    (r"angle[- ]resolved", "角分辨光谱"),
    (r"reflectance|reflection", "反射/反射率谱"),
    (r"spectroscop", "光谱测量"),
    (r"pump[- ]probe|ultrafast|time[- ]resolved", "超快/时间分辨测量"),
    (r"theor|model|Hamiltonian|analytical", "理论建模"),
    (r"simulation|numerical", "数值模拟"),
    (r"first[- ]principles|DFT|ab initio", "第一性原理/DFT"),
    (r"experiment|demonstrat|observ", "实验观测"),
    (r"device|fabricat", "器件制备"),
]

DEFAULT_FIELD_NAME = "arXiv 极化激元与二维/TMD 材料"
DEFAULT_TRACK_GROUP = "Mak-Shan通讯团队"
DEFAULT_TRACK_PEOPLE = [
    "Kin Fai Mak",
    "Jie Shan",
    "Zhongdong Han",
    "Wenjin Zhao",
    "Yichi Zhang",
    "Yiyu Xia",
    "Zhengchao Xia",
]
DEFAULT_TRACK_KEYWORDS = [
    "MoTe2/WSe2",
    "MoTe2",
    "WSe2",
    "stacking order",
    "SHG calibration",
    "ADF-STEM",
    "Chern insulator",
    "mixed-valence topological insulator",
    "Kondo lattice",
    "TMD superconductivity",
    "moire Chern band",
]
DEFAULT_TRACK_ARTICLES = [
    {
        "arxiv_id": "2605.21233v1",
        "base_id": "2605.21233",
        "title": "Stacking-order-dependent electronic properties of MoTe2/WSe2 moiré bilayers",
        "safe_title": "Stacking-order-dependent electronic properties of MoTe2-WSe2 moire bilayers",
        "status": "未发表/未同步期刊信息",
        "journal_ref": "",
        "doi": "",
    }
]


TOPIC_EXCITON = "激子极化激元"
TOPIC_TMD = "二维/TMD 材料研究"
TOPIC_PEROVSKITE = "钙钛矿极化激元"
TOPIC_PLASMONICS = "等离激元/等离激元学"
TOPIC_MICROCAVITY = "微腔与腔光子学"
TOPIC_PHOTONIC_CRYSTAL = "光子晶体腔"
TOPIC_OTHER = "其他相关文献"

TOPICS = [
    {
        "key": "exciton",
        "name": TOPIC_EXCITON,
        "query": (
            'all:"exciton polariton" OR all:"exciton-polariton" OR '
            'all:"polaritonic condensate" OR all:"microcavity polariton"'
        ),
    },
    {
        "key": "tmd",
        "name": TOPIC_TMD,
        "query": (
            '(all:TMDC OR all:"transition metal dichalcogenide" OR '
            'all:MoS2 OR all:MoSe2 OR all:WS2 OR all:WSe2 OR all:MoTe2 OR '
            'all:"moiré WSe2" OR all:"moire WSe2" OR all:"twisted WSe2" OR '
            'all:"moiré semiconductor" OR all:"moire semiconductor")'
        ),
    },
    {
        "key": "perovskite",
        "name": TOPIC_PEROVSKITE,
        "query": (
            '(all:perovskite OR all:"halide perovskite" OR '
            'all:"lead halide perovskite") AND '
            '(all:polariton OR all:polaritons OR all:polaritonic OR all:"strong coupling")'
        ),
    },
    {
        "key": "plasmonics",
        "name": TOPIC_PLASMONICS,
        "query": (
            'all:plasmon OR all:plasmons OR all:plasmonic OR '
            'all:"surface plasmon polariton" OR all:"surface plasmon-polariton" OR '
            'all:SPP OR all:"localized surface plasmon" OR all:nanoplasmonic'
        ),
    },
    {
        "key": "microcavity",
        "name": TOPIC_MICROCAVITY,
        "query": (
            'all:microcavity OR all:"optical microcavity" OR all:"planar microcavity" OR '
            'all:"Fabry-Perot cavity" OR all:"Fabry Perot cavity" OR '
            'all:"whispering-gallery mode" OR all:"whispering gallery mode"'
        ),
    },
    {
        "key": "photonic-crystal-cavity",
        "name": TOPIC_PHOTONIC_CRYSTAL,
        "query": (
            'all:"photonic crystal cavity" OR all:"photonic crystal nanocavity" OR '
            'all:nanocavity OR all:"nanobeam cavity" OR all:"L3 cavity" OR '
            'all:"photonic crystal resonator"'
        ),
    },
]

TOPIC_ORDER = [topic["name"] for topic in TOPICS]
PRIMARY_TOPIC_ORDER = [
    TOPIC_TMD,
    TOPIC_PEROVSKITE,
    TOPIC_EXCITON,
    TOPIC_PLASMONICS,
    TOPIC_MICROCAVITY,
    TOPIC_PHOTONIC_CRYSTAL,
]

DEFAULT_FIELD_NAME = "arXiv 极化激元、等离激元、微腔/光子晶体腔与二维材料"

MATERIAL_PATTERNS = [
    (r"\bMoS2\b|MoS鈧?", "MoS2"),
    (r"\bMoSe2\b|MoSe鈧?", "MoSe2"),
    (r"\bWS2\b|WS鈧?", "WS2"),
    (r"\bWSe2\b|WSe鈧?", "WSe2"),
    (r"\bMoTe2\b|MoTe鈧?", "MoTe2"),
    (r"\bTMD\b|\bTMDC\b|transition metal dichalcogenide", "TMD/TMDC"),
    (r"monolayer|bilayer|heterobilayer|van der Waals|2D material", "二维材料"),
    (r"perovskite|halide perovskite|lead halide", "钙钛矿"),
    (r"organic", "有机半导体"),
    (r"plasmon|plasmonic|SPP|surface plasmon", "等离激元"),
    (r"microcavity|cavity|Fabry|whispering[- ]gallery", "微腔"),
    (r"photonic crystal|nanocavity|nanobeam cavity|L3 cavity", "光子晶体腔"),
]

PHENOMENA_PATTERNS = [
    (r"exciton[- ]polariton|exciton polariton", "激子极化激元"),
    (r"polariton condens|polaritonic condens|Bose[- ]Einstein", "极化激元凝聚"),
    (r"strong coupling", "强耦合"),
    (r"Rabi", "Rabi 劈裂/振荡"),
    (r"plasmon|plasmonic|surface plasmon|localized surface plasmon|SPP", "等离激元模式"),
    (r"photonic crystal|nanocavity|nanobeam cavity|L3 cavity", "光子晶体腔模"),
    (r"moir[e茅é]", "莫尔势/莫尔激子"),
    (r"valley", "谷自由度"),
    (r"topolog", "拓扑性质"),
    (r"nonlinear|non-linearity|nonlinearity", "非线性光学"),
    (r"lasing|laser", "激射/激光"),
    (r"transport|flow|propagation", "输运/传播"),
]

def combined_search_query() -> str:
    return " OR ".join(f"({topic['query']})" for topic in TOPICS)


def search_queries(query_mode: str) -> list[dict[str, str]]:
    if query_mode == "combined":
        return [
            {
                "key": "combined",
                "name": "combined polariton and 2D/TMD search",
                "query": combined_search_query(),
            }
        ]
    return TOPICS


def configured_search_queries(args: argparse.Namespace) -> list[dict[str, str]]:
    custom_queries = getattr(args, "query", None) or []
    if custom_queries:
        field_name = getattr(args, "field_name", None) or "custom arXiv search"
        return [
            {
                "key": f"custom-{index}",
                "name": field_name if len(custom_queries) == 1 else f"{field_name} #{index}",
                "query": query,
            }
            for index, query in enumerate(custom_queries, start=1)
        ]
    return search_queries(args.query_mode)


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read config JSON: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Config JSON must be an object: {path}")
    return data


def _profile_from_config(config: dict[str, Any], profile_name: str | None) -> dict[str, Any]:
    if not profile_name:
        return {}
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict) or profile_name not in profiles:
        available = ", ".join(sorted(profiles)) if isinstance(profiles, dict) else ""
        raise SystemExit(f"Profile not found: {profile_name}. Available profiles: {available}")
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise SystemExit(f"Profile must be an object: {profile_name}")
    return copy.deepcopy(profile)


def _merge_config_values(config: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    defaults = config.get("defaults", {})
    if isinstance(defaults, dict):
        merged.update(defaults)
    merged.update(profile)
    return merged


def _config_queries(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise SystemExit("Config value 'query' must be a string or list of strings")


def _apply_config(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    raw_argv: list[str],
) -> argparse.Namespace:
    if not args.config:
        return args
    config_path = Path(args.config)
    config = _read_json_file(config_path)
    profile = _profile_from_config(config, args.profile)
    values = _merge_config_values(config, profile)
    provided: set[str] = set()
    for action in parser._actions:
        for option in action.option_strings:
            if any(token == option or token.startswith(option + "=") for token in raw_argv):
                provided.add(action.dest)

    scalar_fields = {
        "days": int,
        "as_of": str,
        "output_dir": str,
        "field_name": str,
        "language": str,
        "report_kind": str,
        "track_group": str,
        "page_size": int,
        "max_pages": int,
        "sleep_seconds": float,
        "timeout": int,
        "retry_attempts": int,
        "retry_base_seconds": float,
        "summary_overrides": str,
        "query_mode": str,
    }
    for key, caster in scalar_fields.items():
        if key in values and key not in provided and values[key] is not None:
            setattr(args, key, caster(values[key]))

    if "query" in values and "query" not in provided:
        args.query = _config_queries(values["query"])

    if "include_seen" in values and "include_seen" not in provided:
        args.include_seen = bool(values["include_seen"])
    if "include_uncategorized" in values and "include_uncategorized" not in provided:
        args.include_uncategorized = bool(values["include_uncategorized"])
    return args


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_as_of(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now(CHINA_TZ)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        parsed = dt.datetime.fromisoformat(value + "T21:00:00")
    else:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def parse_arxiv_time(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def arxiv_base_id(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id)


def term_list(text: str, patterns: list[tuple[str, str]], fallback: str) -> list[str]:
    found: list[str] = []
    for pattern, label in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE) and label not in found:
            found.append(label)
    return found or [fallback]


def is_2d_tmd_material(text: str) -> bool:
    specific_material = re.search(
        r"\bTMDCs?\b|transition metal dichalcogenides?|MoS2|MoSe2|WS2|WSe2|MoTe2|"
        r"MoS\$_?2|MoSe\$_?2|WS\$_?2|WSe\$_?2|MoTe\$_?2",
        text,
        flags=re.IGNORECASE,
    )
    if specific_material:
        return True
    return bool(
        re.search(
            r"(twisted|moir[eé]|monolayer|bilayer|heterobilayer|van der Waals).{0,120}"
            r"(semiconductor|dichalcogenide|MoS|MoSe|WS|WSe|MoTe)",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"(semiconductor|dichalcogenide|MoS|MoSe|WS|WSe|MoTe).{0,120}"
            r"(twisted|moir[eé]|monolayer|bilayer|heterobilayer|van der Waals)",
            text,
            flags=re.IGNORECASE,
        )
    )


def infer_topics(record: dict[str, Any]) -> list[str]:
    text = f"{record['title']} {record['summary']}"
    topics: list[str] = []
    polariton_like = re.search(
        r"polariton|polaritonic|strong coupling|microcavity|Rabi",
        text,
        flags=re.IGNORECASE,
    )
    if polariton_like:
        topics.append("激子极化激元")
    if is_2d_tmd_material(text):
        topics.append("二维/TMD 材料研究")
    if polariton_like and re.search(
        r"perovskite|halide perovskite|lead halide",
        text,
        flags=re.IGNORECASE,
    ):
        topics.append("钙钛矿极化激元")
    return topics


def score_record(record: dict[str, Any]) -> int:
    text = f"{record['title']} {record['summary']}".lower()
    score = 0
    weights = {
        "exciton polariton": 6,
        "exciton-polariton": 6,
        "polariton condens": 8,
        "strong coupling": 5,
        "rabi": 4,
        "microcavity": 4,
        "monolayer": 4,
        "bilayer": 3,
        "moire": 5,
        "moiré": 5,
        "perovskite": 5,
        "wse2": 5,
        "mose2": 5,
        "mos2": 4,
        "ws2": 4,
        "room temperature": 4,
        "topological": 3,
        "nonlinear": 3,
        "lasing": 3,
    }
    for term, weight in weights.items():
        if term in text:
            score += weight
    score += 2 * len(record.get("topics", []))
    return score


def action_phrase(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["propose", "theory", "model", "predict"]):
        return "提出或分析了一个理论/模型框架"
    if any(word in lowered for word in ["demonstrate", "observe", "report", "realize"]):
        return "报告了实验观测或器件实现"
    if any(word in lowered for word in ["control", "tune", "modulate", "engineer"]):
        return "展示了对耦合、色散或发光性质的调控"
    if any(word in lowered for word in ["review", "perspective"]):
        return "梳理了该方向的关键进展与问题"
    return "围绕材料体系、强耦合机制或极化激元行为给出新的结果"


def build_cn_fields(record: dict[str, Any]) -> dict[str, str]:
    text = f"{record['title']} {record['summary']}"
    materials = term_list(text, MATERIAL_PATTERNS, "激子-光场强耦合体系")
    phenomena = term_list(text, PHENOMENA_PATTERNS, "极化激元相关现象")
    methods = term_list(text, METHOD_PATTERNS, "题名/摘要中的理论或实验分析")
    topics = set(record.get("topics", []))

    summary = (
        f"基于题名与摘要判断，这篇预印本研究{join_cn(materials[:4])}中的"
        f"{join_cn(phenomena[:4])}，重点关联{join_cn(methods[:4])}。"
    )
    contribution = f"主要贡献可概括为：{action_phrase(text)}。"

    why_parts: list[str] = []
    if "二维/TMD 材料极化激元" in topics:
        why_parts.append("可为二维半导体、谷/莫尔激子与片上强耦合器件提供参考")
    if "钙钛矿极化激元" in topics:
        why_parts.append("有助于跟踪室温、低阈值或可加工极化激元平台")
    if "激子极化激元" in topics:
        why_parts.append("对微腔极化激元凝聚、相干性、输运或非线性研究有参考价值")
    why = "；".join(why_parts) + "。" if why_parts else "适合作为极化激元方向的近期待查文献。"

    return {
        "summary_cn": summary,
        "contribution_cn": contribution,
        "materials_cn": join_cn(materials[:6]),
        "methods_cn": join_cn(methods[:6]),
        "why_it_matters_cn": why,
    }


def load_summary_overrides(path: str | Path | list[str | Path] | tuple[str | Path, ...] | None) -> dict[str, str]:
    if path is None:
        return {}
    if isinstance(path, (list, tuple, set)):
        merged: dict[str, str] = {}
        for item in path:
            merged.update(load_summary_overrides(item))
        return merged
    override_path = Path(path)
    if not override_path.exists():
        return {}
    try:
        data = json.loads(override_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    summaries = data.get("summaries", data)
    if not isinstance(summaries, dict):
        return {}
    return {str(key): str(value).strip() for key, value in summaries.items() if str(value).strip()}


def join_cn(items: list[str]) -> str:
    if not items:
        return "未明确标注"
    if len(items) == 1:
        return items[0]
    return "、".join(items)


def join_cn(items: list[str]) -> str:
    cleaned = [item for item in items if item]
    if not cleaned:
        return "未明确标注"
    if len(cleaned) == 1:
        return cleaned[0]
    return "、".join(cleaned)


def is_polariton_like(text: str) -> bool:
    return bool(
        re.search(
            r"polariton|polaritonic|strong coupling|microcavity polariton|Rabi",
            text,
            flags=re.IGNORECASE,
        )
    )


def is_plasmonics_like(text: str) -> bool:
    return bool(
        re.search(
            r"\bplasmon(s|ic)?\b|surface plasmon polariton|surface plasmon-polariton|"
            r"localized surface plasmon|\bSPP\b|nanoplasmonic",
            text,
            flags=re.IGNORECASE,
        )
    )


def is_microcavity_like(text: str) -> bool:
    return bool(
        re.search(
            r"\bmicrocavit(y|ies)\b|optical microcavity|planar microcavity|"
            r"Fabry[- ]Perot cavity|whispering[- ]gallery mode",
            text,
            flags=re.IGNORECASE,
        )
    )


def is_photonic_crystal_cavity_like(text: str) -> bool:
    return bool(
        re.search(
            r"photonic crystal cavity|photonic crystal nanocavity|"
            r"\bnanocavit(y|ies)\b|nanobeam cavity|\bL3 cavity\b|photonic crystal resonator",
            text,
            flags=re.IGNORECASE,
        )
    )


def infer_topics(record: dict[str, Any]) -> list[str]:
    text = f"{record['title']} {record['summary']}"
    topics: list[str] = []
    polariton_like = is_polariton_like(text)
    if polariton_like:
        topics.append(TOPIC_EXCITON)
    if is_2d_tmd_material(text):
        topics.append(TOPIC_TMD)
    if polariton_like and re.search(
        r"perovskite|halide perovskite|lead halide",
        text,
        flags=re.IGNORECASE,
    ):
        topics.append(TOPIC_PEROVSKITE)
    if is_plasmonics_like(text):
        topics.append(TOPIC_PLASMONICS)
    if is_microcavity_like(text):
        topics.append(TOPIC_MICROCAVITY)
    if is_photonic_crystal_cavity_like(text):
        topics.append(TOPIC_PHOTONIC_CRYSTAL)
    return topics


def score_record(record: dict[str, Any]) -> int:
    text = f"{record['title']} {record['summary']}".lower()
    score = 0
    weights = {
        "exciton polariton": 6,
        "exciton-polariton": 6,
        "polariton condens": 8,
        "strong coupling": 5,
        "rabi": 4,
        "microcavity": 4,
        "photonic crystal cavity": 5,
        "photonic crystal nanocavity": 5,
        "nanocavity": 4,
        "plasmon": 5,
        "plasmonic": 5,
        "surface plasmon polariton": 6,
        "localized surface plasmon": 5,
        "spp": 4,
        "monolayer": 4,
        "bilayer": 3,
        "moire": 5,
        "moiré": 5,
        "perovskite": 5,
        "wse2": 5,
        "mose2": 5,
        "mos2": 4,
        "ws2": 4,
        "room temperature": 4,
        "topological": 3,
        "nonlinear": 3,
        "lasing": 3,
    }
    for term, weight in weights.items():
        if term in text:
            score += weight
    score += 2 * len(record.get("topics", []))
    return score


def abstract_sentences(text: str) -> list[str]:
    normalized = normalize_space(text)
    if not normalized:
        return []
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])", normalized)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def infer_paper_type(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["review", "perspective", "roadmap"]):
        return "review or perspective"
    if any(word in lowered for word in ["method", "algorithm", "framework", "tool", "platform"]):
        return "methods or platform paper"
    if any(word in lowered for word in ["demonstrate", "observe", "realize", "fabricat", "experiment"]):
        return "experimental study"
    if any(word in lowered for word in ["theory", "model", "calculate", "simulation", "predict"]):
        return "theoretical or computational study"
    return "research preprint"


TERM_EN = {
    TOPIC_EXCITON: "exciton polaritons",
    TOPIC_TMD: "two-dimensional and TMD materials",
    TOPIC_PEROVSKITE: "perovskite polaritons",
    TOPIC_PLASMONICS: "plasmonics",
    TOPIC_MICROCAVITY: "microcavity photonics",
    TOPIC_PHOTONIC_CRYSTAL: "photonic crystal cavities",
    "二维材料": "two-dimensional materials",
    "钙钛矿": "perovskites",
    "有机半导体": "organic semiconductors",
    "等离激元": "plasmonic systems",
    "微腔": "microcavities",
    "光子晶体腔": "photonic crystal cavities",
    "激子极化激元": "exciton polaritons",
    "极化激元凝聚": "polariton condensation",
    "强耦合": "strong coupling",
    "Rabi 劈裂/振荡": "Rabi splitting or oscillations",
    "等离激元模式": "plasmonic modes",
    "光子晶体腔模": "photonic crystal cavity modes",
    "莫尔势/莫尔激子": "moiré potentials or moiré excitons",
    "谷自由度": "valley degrees of freedom",
    "拓扑性质": "topological properties",
    "非线性光学": "nonlinear optics",
    "激射/激光": "lasing or laser emission",
    "输运/传播": "transport or propagation",
    "光致发光": "photoluminescence",
    "角分辨光谱": "angle-resolved spectroscopy",
    "反射/反射率谱": "reflectance spectroscopy",
    "光谱测量": "spectroscopy",
    "超快/时间分辨测量": "ultrafast or time-resolved measurements",
    "理论建模": "theoretical modelling",
    "数值模拟": "numerical simulation",
    "第一性原理/DFT": "first-principles or DFT calculations",
    "实验观测": "experimental observation",
    "器件制备": "device fabrication",
    "题名/摘要中的理论或实验分析": "theoretical or experimental analysis described in the title or abstract",
}


def terms_en(items: list[str], fallback: str) -> str:
    translated: list[str] = []
    for item in items:
        value = TERM_EN.get(item)
        if value is None and item.isascii():
            value = item
        if value and value not in translated:
            translated.append(value)
    return ", ".join(translated) if translated else fallback


def sentence_matching(
    sentences: list[str],
    patterns: list[str],
    fallback_index: int = 0,
    reverse: bool = False,
) -> str:
    search_space = list(reversed(sentences)) if reverse else sentences
    for pattern in patterns:
        regex = re.compile(pattern, flags=re.IGNORECASE)
        for sentence in search_space:
            if regex.search(sentence):
                return sentence
    if not sentences:
        return ""
    return sentences[min(max(fallback_index, 0), len(sentences) - 1)]


def shorten_sentence(sentence: str, max_chars: int = 240) -> str:
    sentence = normalize_space(sentence)
    if len(sentence) <= max_chars:
        return sentence
    return sentence[: max_chars - 1].rstrip(" ,;:") + "…"


def sentence_period(sentence: str) -> str:
    sentence = normalize_space(sentence)
    if not sentence or sentence.endswith((".", "?", "!", "。", "？", "！", "…")):
        return sentence
    return sentence + "."


def evidence_sentences(record: dict[str, Any]) -> dict[str, str]:
    sentences = abstract_sentences(record.get("summary", ""))
    title = normalize_space(record.get("title", ""))
    if not sentences and title:
        sentences = [title]
    problem = sentence_matching(
        sentences,
        [
            r"\b(challenge|problem|bottleneck|limitation|limited|unclear|unknown|need|requires?|remain|gap|difficult)\b",
            r"\b(we investigate|we study|this work|here)\b",
        ],
        0,
    )
    approach = sentence_matching(
        sentences,
        [
            r"\b(here|in this work|this study|we).{0,120}\b(use|using|construct|develop|model|measure|probe|calculate|simulate|demonstrate|report|observe|analy[sz]e|derive|solve|grow)\b",
            r"\b(using|via|through|based on|with)\b.{0,140}\b(measure|mapping|spectroscop|photoluminescence|simulation|calculation|model|analysis|microscopy|transport)\b",
            r"\b(photoluminescence|spectroscop|mapping|measurement|simulation|calculation|first-principles|DFT|microscopy|transport)\b.{0,140}\b(reveal|show|indicat|demonstrat|confirm|measure)\b",
        ],
        1 if len(sentences) > 1 else 0,
    )
    result = sentence_matching(
        sentences,
        [
            r"\b(these results|these findings|our findings|this work|we show|we find|we demonstrate|we reveal)\b",
            r"\b(show|shows|shown|find|finds|found|demonstrate|demonstrates|reveal|reveals|enable|enables|achieve|achieves|suggest|suggests|provide|provides|establish|indicat\w*|opens?|offers?)\b",
            r"\b(result|therefore|thus|indicat|lead)\b",
        ],
        len(sentences) - 1,
        reverse=True,
    )
    return {
        "problem": shorten_sentence(problem),
        "approach": shorten_sentence(approach),
        "result": shorten_sentence(result),
    }


def polished_english_digest(record: dict[str, Any], materials: list[str], phenomena: list[str], methods: list[str]) -> str:
    evidence = evidence_sentences(record)
    paper_type = infer_paper_type(f"{record.get('title', '')} {record.get('summary', '')}")
    topic_text = terms_en(record.get("topics", [])[:3], "the target research area")
    system_text = terms_en(materials[:3], "the reported material or photonic platform")
    phenomena_text = terms_en(phenomena[:3], "the relevant light-matter interaction")
    method_text = terms_en(methods[:3], "")

    first = (
        f"This {paper_type} addresses {phenomena_text} in {system_text}, "
        f"making it relevant to {topic_text}."
    )
    second = (
        f"The abstract frames the central question as: {sentence_period(evidence['problem'])}"
        if evidence["problem"]
        else f"The abstract positions the work around {system_text} and {phenomena_text}."
    )
    if method_text:
        third = f"The reported route combines {method_text}, with the key evidence summarized as: {sentence_period(evidence['approach'])}"
    else:
        third = f"The reported route is summarized in the abstract as: {sentence_period(evidence['approach'])}"
    fourth = (
        f"The main implication to check is: {sentence_period(evidence['result'])}"
        if evidence["result"] and evidence["result"] != evidence["problem"]
        else "Read the full abstract and paper before treating the claim as established."
    )
    return " ".join(part for part in [first, second, third, fourth] if part)


def chinese_reader_summary(record: dict[str, Any], materials: list[str], phenomena: list[str], methods: list[str]) -> str:
    evidence = evidence_sentences(record)
    paper_type_map = {
        "review or perspective": "综述/观点型预印本",
        "methods or platform paper": "方法或平台型预印本",
        "experimental study": "实验研究型预印本",
        "theoretical or computational study": "理论或计算研究型预印本",
        "research preprint": "研究型预印本",
    }
    paper_type = paper_type_map.get(infer_paper_type(f"{record.get('title', '')} {record.get('summary', '')}"), "研究型预印本")
    problem = chinese_evidence_clause(evidence["problem"], "problem", materials, phenomena, methods)
    approach = chinese_evidence_clause(evidence["approach"], "approach", materials, phenomena, methods)
    result = chinese_evidence_clause(evidence["result"], "result", materials, phenomena, methods)
    summary = (
        f"这篇{paper_type}围绕{join_cn(materials[:4])}的{join_cn(phenomena[:4])}展开。"
        f"摘要中的核心问题是：{problem}；"
        f"主要证据路径是：{approach}；"
        f"需要重点核对的结论是：{result}。"
    )
    return summary


def abstract_measurements(summary: str) -> list[str]:
    values: list[str] = []
    pattern = re.compile(
        r"~?\d+(?:\.\d+)?(?:\s*(?:x|×)\s*\d+(?:\.\d+)?)?\s*(?:meV|eV|K|ML|nm|um|μm|ps|fs|GHz|THz|Gbs-1|%|atoms?|points?|fold|times)",
        flags=re.IGNORECASE,
    )
    for match in pattern.findall(summary):
        value = normalize_space(match)
        if value and value not in values:
            values.append(value)
    return values[:6]


def chinese_evidence_clause(
    sentence: str,
    role: str,
    materials: list[str],
    phenomena: list[str],
    methods: list[str],
) -> str:
    text = sentence.lower()
    system_text = join_cn(materials[:4])
    phenomenon_text = join_cn(phenomena[:4])
    method_text = join_cn(methods[:4])
    if role == "problem":
        if "bottleneck" in text:
            return f"核心背景是{phenomenon_text}受到瓶颈效应或弛豫效率限制，其物理来源仍需厘清"
        if "difficult" in text or "unclear" in text or "unknown" in text:
            return f"现有难点在于{system_text}中的谱线、结构或机制仍难以直接判定"
        if "limited" in text or "limitation" in text or "remain" in text:
            return f"该方向仍受材料参数、器件条件或机理认识不足的限制"
        return f"研究问题集中在{system_text}中{phenomenon_text}的机理、调控方式和适用边界"
    if role == "approach":
        if method_text:
            return f"作者主要通过{method_text}研究{system_text}中的{phenomenon_text}"
        if "construct" in text or "fabricat" in text or "grow" in text:
            return f"作者构建或制备了{system_text}相关结构，用于检验{phenomenon_text}"
        return f"作者围绕{system_text}搭建理论、实验或器件平台来分析{phenomenon_text}"
    if "rabi" in text:
        return "结果重点涉及 Rabi 劈裂、反交叉色散或强耦合特征"
    if "emission" in text or "photoluminescence" in text:
        return "结果主要体现在发光、光谱响应或空间分布特征的变化上"
    if "transport" in text or "conductance" in text:
        return "结果显示相关输运、传播或电导响应可以被有效调控"
    if "establish" in text or "provide" in text or "open" in text:
        return f"结果为理解或利用{system_text}中的{phenomenon_text}提供了新的证据"
    return f"主要结论指向{system_text}中{phenomenon_text}的可观测响应、调控机制或应用潜力"


def chinese_abstract_translation(
    record: dict[str, Any],
    materials: list[str],
    phenomena: list[str],
    methods: list[str],
) -> str:
    evidence = evidence_sentences(record)
    problem = chinese_evidence_clause(evidence["problem"], "problem", materials, phenomena, methods)
    approach = chinese_evidence_clause(evidence["approach"], "approach", materials, phenomena, methods)
    result = chinese_evidence_clause(evidence["result"], "result", materials, phenomena, methods)
    measurements = abstract_measurements(record.get("summary", ""))
    quantitative_note = ""
    if measurements:
        quantitative_note = f"摘要中的关键量化信息包括：{join_cn(measurements)}"
    return "。".join(part for part in [problem, approach, result, quantitative_note] if part) + "。"


def key_takeaways_cn(record: dict[str, Any], materials: list[str], phenomena: list[str], methods: list[str]) -> str:
    topics = set(record.get("topics", []))
    evidence = evidence_sentences(record)
    problem = chinese_evidence_clause(evidence["problem"], "problem", materials, phenomena, methods)
    approach = chinese_evidence_clause(evidence["approach"], "approach", materials, phenomena, methods)
    result = chinese_evidence_clause(evidence["result"], "result", materials, phenomena, methods)
    points = [
        f"问题：{problem}",
        f"体系：{join_cn(materials[:4])}",
        f"方法/证据：{approach}",
        f"结论线索：{result}",
    ]
    if TOPIC_PLASMONICS in topics:
        points.append("阅读重点：近场增强、模式约束、损耗和可集成性")
    if TOPIC_MICROCAVITY in topics:
        points.append("阅读重点：腔模设计、强耦合判据和器件实现条件")
    if TOPIC_PHOTONIC_CRYSTAL in topics:
        points.append("阅读重点：高 Q、小模体积、片上耦合和量子光学适配性")
    if TOPIC_EXCITON in topics:
        points.append("阅读重点：Rabi 劈裂、凝聚、相干输运或非线性响应")
    if TOPIC_TMD in topics:
        points.append("阅读重点：二维材料、谷/莫尔自由度和片上耦合")
    if TOPIC_PEROVSKITE in topics:
        points.append("阅读重点：室温工作、低阈值和材料可加工性")
    return "；".join(points) + "。"


def build_cn_fields(record: dict[str, Any]) -> dict[str, str]:
    text = f"{record['title']} {record['summary']}"
    materials = term_list(text, MATERIAL_PATTERNS, "激子-光场耦合体系")
    phenomena = term_list(text, PHENOMENA_PATTERNS, "光场耦合相关现象")
    methods = term_list(text, METHOD_PATTERNS, "题名/摘要中的理论或实验分析")
    topics = set(record.get("topics", []))
    summary = chinese_reader_summary(record, materials, phenomena, methods)
    contribution = polished_english_digest(record, materials, phenomena, methods)
    takeaways = key_takeaways_cn(record, materials, phenomena, methods)
    abstract_translation = chinese_abstract_translation(record, materials, phenomena, methods)
    why_parts: list[str] = []
    if TOPIC_TMD in topics:
        why_parts.append("可为二维半导体、谷/莫尔激子与片上耦合器件提供参考")
    if TOPIC_PEROVSKITE in topics:
        why_parts.append("有助于跟踪室温、低阈值或可加工极化激元平台")
    if TOPIC_EXCITON in topics:
        why_parts.append("对微腔极化激元凝聚、相干、输运或非线性研究有参考价值")
    if TOPIC_PLASMONICS in topics:
        why_parts.append("适合跟踪纳米尺度强场约束、近场增强与表面波导方向")
    if TOPIC_MICROCAVITY in topics:
        why_parts.append("有助于跟踪腔模设计、强耦合实现和器件集成路线")
    if TOPIC_PHOTONIC_CRYSTAL in topics:
        why_parts.append("适合关注高 Q 小模体积腔增强和片上量子光学平台")
    why = "；".join(why_parts) + "。" if why_parts else "适合作为本方向的近期候选文献。"
    return {
        "summary_cn": summary,
        "contribution_cn": contribution,
        "materials_cn": join_cn(materials[:6]),
        "methods_cn": join_cn(methods[:6]),
        "why_it_matters_cn": why,
        "full_abstract_en": normalize_space(record.get("summary", "")),
        "polished_abstract_en": contribution,
        "polished_guide_cn": summary,
        "abstract_translation_cn": abstract_translation,
        "key_takeaways_cn": takeaways,
        "paper_type": infer_paper_type(text),
    }


def primary_topic(record: dict[str, Any]) -> str:
    topics = record.get("topics", [])
    for topic in PRIMARY_TOPIC_ORDER:
        if topic in topics:
            return topic
    return topics[0] if topics else TOPIC_OTHER


def topic_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {topic: 0 for topic in TOPIC_ORDER}
    for record in records:
        for topic in record.get("topics", []):
            counts[topic] = counts.get(topic, 0) + 1
    return counts


def retry_delay(headers: Any, attempt: int, base_seconds: float) -> float:
    retry_after = headers.get("Retry-After") if headers else None
    if retry_after and retry_after.isdigit():
        return float(retry_after)
    return base_seconds * (2**attempt)


def fetch_page(
    query: str,
    start: int,
    page_size: int,
    timeout: int,
    retry_attempts: int,
    retry_base_seconds: float,
) -> str:
    params = urllib.parse.urlencode(
        {
            "search_query": query,
            "start": start,
            "max_results": page_size,
            "sortBy": "lastUpdatedDate",
            "sortOrder": "descending",
        }
    )
    request = urllib.request.Request(
        f"{API_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
    )
    for attempt in range(retry_attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
                if text.strip().lower().startswith(RATE_LIMIT_BODY):
                    raise ArxivRateLimitError("arXiv API returned 'Rate exceeded.'")
                return text
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 503}:
                raise
            if attempt == retry_attempts - 1:
                raise ArxivRateLimitError(f"arXiv API returned HTTP {exc.code}.") from exc
            delay = retry_delay(exc.headers, attempt, retry_base_seconds)
            time.sleep(delay)
        except http.client.IncompleteRead:
            if attempt == retry_attempts - 1:
                raise
            delay = retry_delay(None, attempt, retry_base_seconds)
            time.sleep(delay)
        except ArxivRateLimitError:
            if attempt == retry_attempts - 1:
                raise
            delay = retry_delay(None, attempt, retry_base_seconds)
            time.sleep(delay)
    raise RuntimeError("unreachable arXiv retry state")


def parse_entries(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    records: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM}entry"):
        id_url = normalize_space(entry.findtext(f"{ATOM}id"))
        arxiv_id = id_url.rstrip("/").split("/")[-1]
        updated_text = normalize_space(entry.findtext(f"{ATOM}updated"))
        published_text = normalize_space(entry.findtext(f"{ATOM}published"))
        authors = [
            normalize_space(author.findtext(f"{ATOM}name"))
            for author in entry.findall(f"{ATOM}author")
        ]
        categories = [
            category.attrib.get("term", "")
            for category in entry.findall(f"{ATOM}category")
            if category.attrib.get("term")
        ]
        primary = entry.find(f"{ARXIV}primary_category")
        links = entry.findall(f"{ATOM}link")
        pdf_url = ""
        for link in links:
            if link.attrib.get("title") == "pdf" or link.attrib.get("type") == "application/pdf":
                pdf_url = link.attrib.get("href", "")
                break

        records.append(
            {
                "arxiv_id": arxiv_id,
                "base_id": arxiv_base_id(arxiv_id),
                "abs_url": id_url,
                "pdf_url": pdf_url,
                "title": normalize_space(entry.findtext(f"{ATOM}title")),
                "summary": normalize_space(entry.findtext(f"{ATOM}summary")),
                "authors": [author for author in authors if author],
                "updated": updated_text,
                "published": published_text,
                "updated_datetime": parse_arxiv_time(updated_text),
                "published_datetime": parse_arxiv_time(published_text),
                "primary_category": primary.attrib.get("term", "") if primary is not None else "",
                "categories": categories,
                "doi": normalize_space(entry.findtext(f"{ARXIV}doi")),
                "journal_ref": normalize_space(entry.findtext(f"{ARXIV}journal_ref")),
                "comment": normalize_space(entry.findtext(f"{ARXIV}comment")),
                "topics": [],
                "matched_queries": [],
            }
        )
    return records


def parse_oai_date(value: str) -> dt.date:
    return dt.date.fromisoformat(normalize_space(value)[:10])


def oai_datetime_for_window(
    value: str,
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> dt.datetime:
    updated_date = parse_oai_date(value)
    updated = dt.datetime.combine(updated_date, dt.time(12, 0), tzinfo=UTC)
    if updated.date() == window_start.date() and updated < window_start:
        updated = window_start
    if updated.date() == window_end.date() and updated > window_end:
        updated = window_end
    return updated


def fetch_oai_page(
    params: dict[str, str],
    timeout: int,
    retry_attempts: int,
    retry_base_seconds: float,
) -> str:
    request = urllib.request.Request(
        f"{OAI_URL}?{urllib.parse.urlencode(params)}",
        headers={"User-Agent": USER_AGENT},
    )
    for attempt in range(retry_attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
                if text.strip().lower().startswith(RATE_LIMIT_BODY):
                    raise ArxivRateLimitError("arXiv OAI returned 'Rate exceeded.'")
                return text
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 503}:
                raise
            if attempt == retry_attempts - 1:
                raise ArxivRateLimitError(f"arXiv OAI returned HTTP {exc.code}.") from exc
            time.sleep(retry_delay(exc.headers, attempt, retry_base_seconds))
        except (http.client.IncompleteRead, TimeoutError, ArxivRateLimitError):
            if attempt == retry_attempts - 1:
                raise
            time.sleep(retry_delay(None, attempt, retry_base_seconds))
    raise RuntimeError("unreachable arXiv OAI retry state")


def oai_author_name(author: ET.Element) -> str:
    forenames = normalize_space(author.findtext(f"{OAI_ARXIV}forenames"))
    keyname = normalize_space(author.findtext(f"{OAI_ARXIV}keyname"))
    suffix = normalize_space(author.findtext(f"{OAI_ARXIV}suffix"))
    return normalize_space(" ".join(part for part in (forenames, keyname, suffix) if part))


def parse_oai_entries(
    xml_text: str,
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> tuple[list[dict[str, Any]], str | None]:
    root = ET.fromstring(xml_text)
    error = root.find(f"{OAI}error")
    if error is not None and error.attrib.get("code") == "noRecordsMatch":
        return [], None
    if error is not None:
        raise ValueError(normalize_space(error.text or error.attrib.get("code", "OAI error")))

    records: list[dict[str, Any]] = []
    for record in root.findall(f".//{OAI}record"):
        header = record.find(f"{OAI}header")
        metadata = record.find(f"{OAI}metadata")
        if header is not None and header.attrib.get("status") == "deleted":
            continue
        if metadata is None:
            continue
        arxiv = metadata.find(f"{OAI_ARXIV}arXiv")
        if arxiv is None:
            continue

        arxiv_id = normalize_space(arxiv.findtext(f"{OAI_ARXIV}id"))
        if not arxiv_id:
            continue
        updated_text = normalize_space(arxiv.findtext(f"{OAI_ARXIV}updated"))
        datestamp = normalize_space(header.findtext(f"{OAI}datestamp")) if header is not None else ""
        updated_text = updated_text or datestamp
        created_text = normalize_space(arxiv.findtext(f"{OAI_ARXIV}created")) or updated_text
        categories = normalize_space(arxiv.findtext(f"{OAI_ARXIV}categories")).split()
        authors = [
            name
            for name in (oai_author_name(author) for author in arxiv.findall(f".//{OAI_ARXIV}author"))
            if name
        ]
        updated_datetime = oai_datetime_for_window(updated_text, window_start, window_end)
        published_datetime = oai_datetime_for_window(created_text, window_start, window_end)
        records.append(
            {
                "arxiv_id": arxiv_id,
                "base_id": arxiv_base_id(arxiv_id),
                "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                "title": normalize_space(arxiv.findtext(f"{OAI_ARXIV}title")),
                "summary": normalize_space(arxiv.findtext(f"{OAI_ARXIV}abstract")),
                "authors": authors,
                "updated": updated_datetime.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "published": published_datetime.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "updated_datetime": updated_datetime,
                "published_datetime": published_datetime,
                "primary_category": categories[0] if categories else "",
                "categories": categories,
                "doi": normalize_space(arxiv.findtext(f"{OAI_ARXIV}doi")),
                "journal_ref": normalize_space(arxiv.findtext(f"{OAI_ARXIV}journal-ref")),
                "comment": normalize_space(arxiv.findtext(f"{OAI_ARXIV}comments")),
                "topics": [],
                "matched_queries": [],
                "source": "arxiv-oai-fallback",
            }
        )

    token = root.find(f".//{OAI}resumptionToken")
    resumption_token = normalize_space(token.text) if token is not None and token.text else None
    return records, resumption_token


def oai_scope_topics(record: dict[str, Any]) -> list[str]:
    text = f"{record['title']} {record['summary']}"
    topics: list[str] = []
    if re.search(
        r"\b[a-z0-9-]*[- ]?polaritons?\b|\bpolaritonic\b|microcavity polariton",
        text,
        flags=re.IGNORECASE,
    ):
        topics.append(TOPIC_EXCITON)
    if is_2d_tmd_material(text):
        topics.append(TOPIC_TMD)
    if re.search(r"perovskite|halide perovskite|lead halide", text, flags=re.IGNORECASE) and re.search(
        r"\b[a-z0-9-]*[- ]?polaritons?\b|\bpolaritonic\b|strong coupling",
        text,
        flags=re.IGNORECASE,
    ):
        topics.append(TOPIC_PEROVSKITE)
    if is_plasmonics_like(text):
        topics.append(TOPIC_PLASMONICS)
    if is_microcavity_like(text):
        topics.append(TOPIC_MICROCAVITY)
    if is_photonic_crystal_cavity_like(text):
        topics.append(TOPIC_PHOTONIC_CRYSTAL)
    return [topic for topic in TOPIC_ORDER if topic in set(topics)]


def collect_oai_fallback_records(
    args: argparse.Namespace,
    window_start: dt.datetime,
    window_end: dt.datetime,
    summary_overrides: dict[str, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    from_date = window_start.date().isoformat()
    until_date = window_end.date().isoformat()

    for set_spec in OAI_FALLBACK_SETS:
        token: str | None = None
        while True:
            params = (
                {"verb": "ListRecords", "resumptionToken": token}
                if token
                else {
                    "verb": "ListRecords",
                    "metadataPrefix": "arXiv",
                    "from": from_date,
                    "until": until_date,
                    "set": set_spec,
                }
            )
            try:
                xml_text = fetch_oai_page(
                    params,
                    args.timeout,
                    args.retry_attempts,
                    args.retry_base_seconds,
                )
                page_records, token = parse_oai_entries(xml_text, window_start, window_end)
            except ArxivRateLimitError as exc:
                errors.append(f"OAI fallback {set_spec} stopped: {exc}")
                break
            except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
                errors.append(f"OAI fallback {set_spec} failed: {exc}")
                break

            for record in page_records:
                updated = record["updated_datetime"]
                if not (window_start <= updated <= window_end):
                    continue
                scoped_topics = oai_scope_topics(record)
                if not scoped_topics and not args.include_uncategorized and not args.query:
                    continue
                existing = by_id.setdefault(record["base_id"], record)
                if updated > existing["updated_datetime"]:
                    existing.update(record)
                existing["topics"] = [topic for topic in TOPIC_ORDER if topic in set(existing.get("topics", [])) | set(scoped_topics)]
                existing["matched_queries"].append(f"oai:{set_spec}")

            if not token:
                break
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    records = list(by_id.values())
    for record in records:
        if args.query and not record["topics"]:
            record["topics"] = [args.field_name]
        record["score"] = score_record(record)
        record.update(build_cn_fields(record))
        summary_override = summary_overrides.get(record["base_id"]) or summary_overrides.get(record["arxiv_id"])
        if summary_override:
            record["summary_cn"] = summary_override

    records.sort(key=lambda item: (item["updated_datetime"], item["score"]), reverse=True)
    for record in records:
        record["updated_local"] = record["updated_datetime"].astimezone(CHINA_TZ).isoformat()
        record["published_local"] = record["published_datetime"].astimezone(CHINA_TZ).isoformat()
        record.pop("updated_datetime", None)
        record.pop("published_datetime", None)

    return records, errors


def collect_records(args: argparse.Namespace, as_of: dt.datetime) -> tuple[list[dict[str, Any]], list[str]]:
    window_start = getattr(args, "window_start", as_of - dt.timedelta(days=args.days)).astimezone(UTC)
    window_end = getattr(args, "window_end", as_of).astimezone(UTC)
    window_end_exclusive = bool(getattr(args, "window_end_exclusive", False))
    by_id: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    summary_overrides = load_summary_overrides(args.summary_overrides)

    queries = configured_search_queries(args)
    rate_limited = False
    for topic_index, topic in enumerate(queries):
        stop_topic = False
        for page in range(args.max_pages):
            start = page * args.page_size
            try:
                xml_text = fetch_page(
                    topic["query"],
                    start,
                    args.page_size,
                    args.timeout,
                    args.retry_attempts,
                    args.retry_base_seconds,
                )
                page_records = parse_entries(xml_text)
            except ArxivRateLimitError as exc:
                errors.append(f"{topic['name']} query stopped at start={start}: {exc}")
                rate_limited = True
                break
            except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError) as exc:
                errors.append(f"{topic['name']} query failed at start={start}: {exc}")
                break

            if not page_records:
                break

            oldest = min(record["updated_datetime"] for record in page_records)
            for record in page_records:
                updated = record["updated_datetime"]
                in_window = (
                    window_start <= updated < window_end
                    if window_end_exclusive
                    else window_start <= updated <= window_end
                )
                if in_window:
                    existing = by_id.setdefault(record["base_id"], record)
                    if updated > existing["updated_datetime"]:
                        existing.update(record)
                    if topic["key"] != "combined" and topic["name"] not in existing["topics"]:
                        existing["topics"].append(topic["name"])
                    existing["matched_queries"].append(topic["key"])

            if oldest < window_start or len(page_records) < args.page_size:
                stop_topic = True
            if stop_topic:
                break
            if args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)
        if rate_limited:
            break
        if args.sleep_seconds > 0 and topic_index < len(queries) - 1:
            time.sleep(args.sleep_seconds)

    if rate_limited:
        fallback_records, fallback_errors = collect_oai_fallback_records(
            args,
            window_start,
            window_end,
            summary_overrides,
        )
        if fallback_records or not fallback_errors:
            return fallback_records, fallback_errors
        errors.extend(fallback_errors)

    records = list(by_id.values())
    classified_records: list[dict[str, Any]] = []
    for record in records:
        inferred = infer_topics(record)
        record["topics"] = [topic for topic in TOPIC_ORDER if topic in set(record["topics"]) | set(inferred)]
        record["score"] = score_record(record)
        record.update(build_cn_fields(record))
        summary_override = summary_overrides.get(record["base_id"]) or summary_overrides.get(record["arxiv_id"])
        if summary_override:
            record["summary_cn"] = summary_override
        if args.query and not record["topics"]:
            record["topics"] = [args.field_name]
        if record["topics"] or args.include_uncategorized or args.query:
            classified_records.append(record)
    records = classified_records

    records.sort(key=lambda item: (item["updated_datetime"], item["score"]), reverse=True)
    for record in records:
        record["updated_local"] = record["updated_datetime"].astimezone(CHINA_TZ).isoformat()
        record["published_local"] = record["published_datetime"].astimezone(CHINA_TZ).isoformat()
        record.pop("updated_datetime", None)
        record.pop("published_datetime", None)

    return records, errors


def primary_topic(record: dict[str, Any]) -> str:
    topics = record.get("topics", [])
    for topic in PRIMARY_TOPIC_ORDER:
        if topic in topics:
            return topic
    return topics[0] if topics else "其他相关文献"


def topic_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {topic: 0 for topic in TOPIC_ORDER}
    for record in records:
        for topic in record.get("topics", []):
            counts[topic] = counts.get(topic, 0) + 1
    return counts


def record_date_label(record: dict[str, Any]) -> str:
    raw_value = normalize_space(record.get("updated_local") or record.get("updated") or "")
    if not raw_value:
        return "日期未知"
    try:
        parsed = dt.datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(CHINA_TZ).date().isoformat()
    except ValueError:
        return raw_value[:10] or "日期未知"


def record_date_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        label = record_date_label(record)
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[0], reverse=True))


def format_date_distribution(counts: dict[str, int], language: str) -> str:
    if not counts:
        return ""
    if language == "en":
        return "; ".join(f"{date}: {count}" for date, count in counts.items())
    if language == "bilingual":
        return "；".join(f"{date} {count} 篇 / {count} records" for date, count in counts.items())
    return "；".join(f"{date} {count} 篇" for date, count in counts.items())


def html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def is_weekly_report(days: int, report_kind: str = "auto") -> bool:
    return report_kind == "weekly" or (report_kind == "auto" and days >= 7)


def report_label(days: int, report_kind: str = "auto") -> str:
    return WEEKLY_SUBDIR if is_weekly_report(days, report_kind) else DAILY_SUBDIR


def local_date(value: dt.datetime | dt.date) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.astimezone(CHINA_TZ).date()
    return value


def fixed_week_dates(as_of: dt.datetime) -> tuple[dt.date, dt.date]:
    report_date = as_of.astimezone(CHINA_TZ).date()
    week_start = report_date - dt.timedelta(days=report_date.weekday())
    week_end = week_start + dt.timedelta(days=6)
    return week_start, week_end


def fixed_week_datetimes(as_of: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    week_start, week_end = fixed_week_dates(as_of)
    window_start = dt.datetime.combine(week_start, dt.time.min, tzinfo=CHINA_TZ)
    window_end = dt.datetime.combine(week_end + dt.timedelta(days=1), dt.time.min, tzinfo=CHINA_TZ)
    return window_start, window_end


def date_range_stamp(start: dt.date, end: dt.date) -> str:
    return f"{start.isoformat()}_to_{end.isoformat()}"


def month_segments(week_start: dt.date, week_end: dt.date) -> list[dict[str, dt.date]]:
    segments: list[dict[str, dt.date]] = []
    current = week_start
    while current <= week_end:
        if current.month == 12:
            next_month = dt.date(current.year + 1, 1, 1)
        else:
            next_month = dt.date(current.year, current.month + 1, 1)
        segment_end = min(week_end, next_month - dt.timedelta(days=1))
        segments.append({"segment_start": current, "segment_end": segment_end})
        current = segment_end + dt.timedelta(days=1)
    return segments


def period_dir(base_output_dir: str | Path, as_of: dt.datetime | dt.date) -> Path:
    report_date = local_date(as_of)
    return Path(base_output_dir) / report_date.strftime("%Y") / report_date.strftime("%m")


def period_report_dir(
    base_output_dir: str | Path,
    as_of: dt.datetime,
    days: int,
    report_kind: str = "auto",
) -> Path:
    subdir = WEEKLY_SUBDIR if is_weekly_report(days, report_kind) else DAILY_SUBDIR
    return period_dir(base_output_dir, as_of) / subdir


def weekly_report_dir(
    base_output_dir: str | Path,
    week_start: dt.date,
    week_end: dt.date,
    segment_date: dt.date,
) -> Path:
    return period_dir(base_output_dir, segment_date) / WEEKLY_SUBDIR / date_range_stamp(week_start, week_end)


def record_updated_date(record: dict[str, Any]) -> dt.date | None:
    try:
        return dt.datetime.fromisoformat(record["updated_local"]).astimezone(CHINA_TZ).date()
    except (KeyError, TypeError, ValueError):
        return None


def records_in_date_range(
    records: list[dict[str, Any]],
    segment_start: dt.date,
    segment_end: dt.date,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for record in records:
        updated_date = record_updated_date(record)
        if updated_date is not None and segment_start <= updated_date <= segment_end:
            selected.append(record)
    return selected


def report_metadata_for_scope(
    args: argparse.Namespace,
    as_of: dt.datetime,
    report_scope: str,
    segment_start: dt.date | None = None,
    segment_end: dt.date | None = None,
) -> dict[str, Any]:
    window_start = getattr(args, "window_start", as_of - dt.timedelta(days=args.days))
    window_end = getattr(args, "window_end", as_of)
    metadata: dict[str, Any] = {
        "report_scope": report_scope,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "window_end_exclusive": bool(getattr(args, "window_end_exclusive", False)),
    }
    week_start = getattr(args, "week_start_date", None)
    week_end = getattr(args, "week_end_date", None)
    if week_start and week_end:
        metadata["week_start"] = week_start.isoformat()
        metadata["week_end"] = week_end.isoformat()
    if segment_start and segment_end:
        metadata["segment_start"] = segment_start.isoformat()
        metadata["segment_end"] = segment_end.isoformat()
    return metadata


def metadata_datetime(report_metadata: dict[str, Any] | None, key: str) -> dt.datetime | None:
    if not report_metadata:
        return None
    value = report_metadata.get(key)
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def briefing_metadata_lines(report_metadata: dict[str, Any] | None, language: str) -> list[str]:
    if not report_metadata:
        return []
    week_start = report_metadata.get("week_start")
    week_end = report_metadata.get("week_end")
    segment_start = report_metadata.get("segment_start")
    segment_end = report_metadata.get("segment_end")
    lines: list[str] = []
    if week_start and week_end:
        if language == "en":
            lines.append(f"Week range: {week_start} to {week_end}.")
        elif language == "bilingual":
            lines.append(f"\u5468\u62a5\u8303\u56f4 / Week range: {week_start} \u81f3 {week_end} / {week_start} to {week_end}.")
        else:
            lines.append(f"\u5468\u62a5\u8303\u56f4\uff1a{week_start} \u81f3 {week_end}\u3002")
    if segment_start and segment_end and (segment_start != week_start or segment_end != week_end):
        if language == "en":
            lines.append(f"Segment range: {segment_start} to {segment_end}.")
        elif language == "bilingual":
            lines.append(f"\u5206\u6bb5\u8303\u56f4 / Segment range: {segment_start} \u81f3 {segment_end} / {segment_start} to {segment_end}.")
        else:
            lines.append(f"\u5206\u6bb5\u8303\u56f4\uff1a{segment_start} \u81f3 {segment_end}\u3002")
    return lines


def render_report_metadata_html(report_metadata: dict[str, Any] | None, language: str) -> str:
    lines = briefing_metadata_lines(report_metadata, language)
    if not lines:
        return ""
    return '<p class="muted">' + "<br>".join(html_escape(line) for line in lines) + "</p>"


def render_paper_card(record: dict[str, Any], index: int, language: str = "zh") -> str:
    authors = ", ".join(record.get("authors", [])[:12])
    if len(record.get("authors", [])) > 12:
        authors += " et al."
    tags = "".join(f"<span>{html_escape(topic)}</span>" for topic in record.get("topics", []))
    categories = ", ".join(record.get("categories", []))
    pdf_link = (
        f'<a href="{html_escape(record["pdf_url"])}" target="_blank" rel="noopener">PDF</a>'
        if record.get("pdf_url")
        else ""
    )
    doi_line = f"<p><b>DOI:</b> {html_escape(record['doi'])}</p>" if record.get("doi") else ""
    comment_line = (
        f"<p><b>Comment:</b> {html_escape(record['comment'])}</p>" if record.get("comment") else ""
    )
    if language == "en":
        insight_html = f"""
        <p><b>Abstract:</b> {html_escape(record['summary'])}</p>
        {doi_line}
        {comment_line}
        <details>
          <summary>Chinese notes</summary>
          <p><b>中文摘要：</b>{html_escape(record['summary_cn'])}</p>
          <p><b>关键贡献：</b>{html_escape(record['contribution_cn'])}</p>
          <p><b>Materials/system：</b>{html_escape(record['materials_cn'])}</p>
          <p><b>Methods/evidence：</b>{html_escape(record['methods_cn'])}</p>
          <p><b>Why it matters：</b>{html_escape(record['why_it_matters_cn'])}</p>
        </details>
        """
        details_html = ""
    elif language == "bilingual":
        insight_html = f"""
        <div class="bilingual-summary">
          <div>
            <p><b>English abstract:</b> {html_escape(record['summary'])}</p>
          </div>
          <div>
            <p><b>中文摘要：</b>{html_escape(record['summary_cn'])}</p>
            <p><b>关键贡献：</b>{html_escape(record['contribution_cn'])}</p>
            <p><b>材料/体系：</b>{html_escape(record['materials_cn'])}</p>
            <p><b>方法/证据：</b>{html_escape(record['methods_cn'])}</p>
            <p><b>为什么值得看：</b>{html_escape(record['why_it_matters_cn'])}</p>
          </div>
        </div>
        {doi_line}
        {comment_line}
        """
        details_html = ""
    else:
        insight_html = f"""
        <p><b>中文摘要：</b>{html_escape(record['summary_cn'])}</p>
        <p><b>关键贡献：</b>{html_escape(record['contribution_cn'])}</p>
        <p><b>材料/体系：</b>{html_escape(record['materials_cn'])}</p>
        <p><b>方法/证据：</b>{html_escape(record['methods_cn'])}</p>
        <p><b>为什么值得看：</b>{html_escape(record['why_it_matters_cn'])}</p>
        {doi_line}
        {comment_line}
        """
        details_html = f"""
      <details>
        <summary>英文摘要</summary>
        <p>{html_escape(record['summary'])}</p>
      </details>
        """
    return f"""
    <article class="paper" id="paper-{index}">
      <div class="paper-top">
        <div class="rank">{index}</div>
        <div>
          <h3>{html_escape(record['title'])}</h3>
          <div class="tags">{tags}</div>
        </div>
      </div>
      <p class="authors">{html_escape(authors)}</p>
      <div class="meta">
        <span>arXiv: {html_escape(record['arxiv_id'])}</span>
        <span>Updated: {html_escape(record['updated_local'][:10])}</span>
        <span>Primary: {html_escape(record.get('primary_category', ''))}</span>
        <span>Categories: {html_escape(categories)}</span>
      </div>
      <div class="links">
        <a href="{html_escape(record['abs_url'])}" target="_blank" rel="noopener">arXiv</a>
        {pdf_link}
      </div>
      <div class="insight">
        {insight_html}
      </div>
      {details_html}
    </article>
    """


def render_paper_card(record: dict[str, Any], index: int, language: str = "zh") -> str:
    authors = ", ".join(record.get("authors", [])[:12])
    if len(record.get("authors", [])) > 12:
        authors += " et al."
    tags = "".join(f"<span>{html_escape(topic)}</span>" for topic in record.get("topics", []))
    categories = ", ".join(record.get("categories", []))
    pdf_link = (
        f'<a href="{html_escape(record["pdf_url"])}" target="_blank" rel="noopener">PDF</a>'
        if record.get("pdf_url")
        else ""
    )
    doi_line = f"<p><b>DOI:</b> {html_escape(record['doi'])}</p>" if record.get("doi") else ""
    comment_line = (
        f"<p><b>Comment:</b> {html_escape(record['comment'])}</p>" if record.get("comment") else ""
    )
    full_abstract_en = record.get("full_abstract_en") or record.get("summary", "")
    polished_abstract_en = record.get("polished_abstract_en") or record.get("contribution_cn", "")
    polished_guide_cn = record.get("polished_guide_cn") or record.get("summary_cn", "")
    abstract_translation_cn = record.get("abstract_translation_cn") or record.get("summary_cn", "")
    key_takeaways_cn = record.get("key_takeaways_cn") or record.get("why_it_matters_cn", "")

    if language == "en":
        insight_html = f"""
        <p><b>Polished English guide:</b> {html_escape(polished_abstract_en)}</p>
        <p><b>Chinese abstract translation:</b> {html_escape(abstract_translation_cn)}</p>
        <p><b>Full English abstract:</b> {html_escape(full_abstract_en)}</p>
        {doi_line}
        {comment_line}
        <details>
          <summary>Chinese notes</summary>
          <p><b>中文导读：</b>{html_escape(polished_guide_cn)}</p>
          <p><b>重点提炼：</b>{html_escape(key_takeaways_cn)}</p>
          <p><b>Materials/system：</b>{html_escape(record['materials_cn'])}</p>
          <p><b>Methods/evidence：</b>{html_escape(record['methods_cn'])}</p>
          <p><b>Why it matters：</b>{html_escape(record['why_it_matters_cn'])}</p>
        </details>
        """
    elif language == "bilingual":
        insight_html = f"""
        <div class="bilingual-summary">
          <div>
            <p><b>Polished English guide:</b> {html_escape(polished_abstract_en)}</p>
            <p><b>Full English abstract:</b> {html_escape(full_abstract_en)}</p>
          </div>
          <div>
            <p><b>中文导读：</b>{html_escape(polished_guide_cn)}</p>
            <p><b>重点提炼：</b>{html_escape(key_takeaways_cn)}</p>
            <p><b>摘要中文译文：</b>{html_escape(abstract_translation_cn)}</p>
            <p><b>材料/体系：</b>{html_escape(record['materials_cn'])}</p>
            <p><b>方法/证据：</b>{html_escape(record['methods_cn'])}</p>
            <p><b>为什么值得看：</b>{html_escape(record['why_it_matters_cn'])}</p>
          </div>
        </div>
        {doi_line}
        {comment_line}
        """
    else:
        insight_html = f"""
        <p><b>中文导读：</b>{html_escape(polished_guide_cn)}</p>
        <p><b>重点提炼：</b>{html_escape(key_takeaways_cn)}</p>
        <p><b>摘要中文译文：</b>{html_escape(abstract_translation_cn)}</p>
        <p><b>材料/体系：</b>{html_escape(record['materials_cn'])}</p>
        <p><b>方法/证据：</b>{html_escape(record['methods_cn'])}</p>
        <p><b>为什么值得看：</b>{html_escape(record['why_it_matters_cn'])}</p>
        {doi_line}
        {comment_line}
        <details>
          <summary>英文原摘要（核对）</summary>
          <p>{html_escape(full_abstract_en)}</p>
          <p><b>英文导读：</b>{html_escape(polished_abstract_en)}</p>
        </details>
        """

    return f"""
    <article class="paper" id="paper-{index}">
      <div class="paper-top">
        <div class="rank">{index}</div>
        <div>
          <h3>{html_escape(record['title'])}</h3>
          <div class="tags">{tags}</div>
        </div>
      </div>
      <p class="authors">{html_escape(authors)}</p>
      <div class="meta">
        <span>arXiv: {html_escape(record['arxiv_id'])}</span>
        <span>Updated: {html_escape(record['updated_local'][:10])}</span>
        <span>Primary: {html_escape(record.get('primary_category', ''))}</span>
        <span>Categories: {html_escape(categories)}</span>
      </div>
      <div class="links">
        <a href="{html_escape(record['abs_url'])}" target="_blank" rel="noopener">arXiv</a>
        {pdf_link}
      </div>
      <div class="insight">
        {insight_html}
      </div>
    </article>
    """


def render_publication_update(record: dict[str, Any]) -> str:
    journal = record.get("journal_ref") or "arXiv 尚未提供期刊引用"
    doi = record.get("doi") or "arXiv 尚未提供 DOI"
    return f"""
      <li>
        <a href="{html_escape(record['abs_url'])}" target="_blank" rel="noopener">{html_escape(record['title'])}</a>
        <small>arXiv:{html_escape(record['arxiv_id'])}</small>
        <p><b>期刊：</b>{html_escape(journal)}</p>
        <p><b>DOI：</b>{html_escape(doi)}</p>
      </li>
    """


def render_html(
    records: list[dict[str, Any]],
    errors: list[str],
    as_of: dt.datetime,
    days: int,
    skipped_seen_count: int = 0,
    publication_updates: list[dict[str, Any]] | None = None,
    field_name: str = DEFAULT_FIELD_NAME,
    language: str = "zh",
    report_kind: str = "auto",
    tracking_updates: list[str] | None = None,
    tracking_path: str | None = None,
    report_metadata: dict[str, Any] | None = None,
) -> str:
    publication_updates = publication_updates or []
    tracking_updates = tracking_updates or []
    report_metadata = report_metadata or {}
    report_date = as_of.strftime("%Y-%m-%d")
    label = report_label(days, report_kind)
    if language == "en":
        highlights_title = "Highlights"
        counts_title = "Topic Counts"
        total_label = "Deduplicated total"
        tracking_title = "Group Tracking Updates"
        tracking_folder_label = "Tracking folder:"
        no_tracking_text = "No new group-tracking updates in this run."
        no_match_text = f"No matching updates in the last {days} days"
        report_date_label = "Report date"
        window_label = "Search window"
        date_distribution_label = "Date distribution"
        date_section_label = "Updated date"
        paper_count_suffix = "records"
        footer_text = "Data source: arXiv API. Records are deduplicated by arXiv ID and filtered by the arXiv updated timestamp. Please verify the original paper before formal citation."
    elif language == "bilingual":
        highlights_title = "今日重点 / Highlights"
        counts_title = "主题计数 / Topic Counts"
        total_label = "去重后总数 / Deduplicated total"
        tracking_title = "课题组追踪更新 / Group Tracking Updates"
        tracking_folder_label = "追踪档 / Tracking folder:"
        no_tracking_text = "本期无新增课题组追踪更新 / No new group-tracking updates in this run."
        no_match_text = f"近 {days} 天无匹配更新 / No matching updates in the last {days} days"
        report_date_label = "报告日期 / Report date"
        window_label = "检索窗口 / Search window"
        date_distribution_label = "日期分布 / Date distribution"
        date_section_label = "更新日期 / Updated date"
        paper_count_suffix = "篇"
        footer_text = "数据源：arXiv API。记录按 arXiv ID 去重并按 arXiv updated 字段过滤；正式引用前请打开原文核对。 / Data source: arXiv API. Verify the original paper before formal citation."
    else:
        highlights_title = "今日重点"
        counts_title = "主题计数"
        total_label = "去重后总数"
        tracking_title = "课题组追踪更新"
        tracking_folder_label = "追踪档："
        no_tracking_text = "本期无新增课题组追踪更新。"
        no_match_text = "近五天无匹配更新"
        report_date_label = "报告日期"
        window_label = "检索窗口"
        date_distribution_label = "日期分布"
        date_section_label = "更新日期"
        paper_count_suffix = "篇"
        footer_text = "数据源：arXiv API。记录按 arXiv ID 去重；更新时间使用 arXiv updated 字段过滤。中文要点由本地规则基于题名与摘要自动提取，正式引用前请打开原文核对。"
    window_start = metadata_datetime(report_metadata, "window_start") or as_of - dt.timedelta(days=days)
    window_end = metadata_datetime(report_metadata, "window_end") or as_of
    period_extra_html = render_report_metadata_html(report_metadata, language)
    counts = topic_counts(records)
    date_distribution = record_date_counts(records)
    date_distribution_text = format_date_distribution(date_distribution, language)
    date_distribution_html = (
        f'<p class="muted date-counts"><b>{html_escape(date_distribution_label)}:</b> {html_escape(date_distribution_text)}</p>'
        if language == "en" and date_distribution_text
        else f'<p class="muted date-counts"><b>{html_escape(date_distribution_label)}：</b>{html_escape(date_distribution_text)}</p>'
        if date_distribution_text
        else ""
    )
    highlights = sorted(records, key=lambda item: (item.get("score", 0), item["updated_local"]), reverse=True)[:5]

    highlight_html = (
        "\n".join(
            f"""
            <li>
              <a href="#paper-{records.index(record) + 1}">{html_escape(record['title'])}</a>
              <small>arXiv:{html_escape(record['arxiv_id'])} · {html_escape(', '.join(record.get('topics', [])))}</small>
            </li>
            """
            for record in highlights
        )
        if highlights
        else f"<li>{html_escape(no_match_text)}。</li>"
    )

    grouped_by_date: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, record in enumerate(records, start=1):
        grouped_by_date.setdefault(record_date_label(record), []).append((index, record))

    grouped_html = ""
    topic_base_order = PRIMARY_TOPIC_ORDER + [TOPIC_OTHER]
    for date_label in sorted(grouped_by_date, reverse=True):
        day_papers = grouped_by_date[date_label]
        grouped_by_topic: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        for index, record in day_papers:
            grouped_by_topic.setdefault(primary_topic(record), []).append((index, record))

        topic_render_order = topic_base_order + [
            topic for topic in grouped_by_topic if topic not in set(topic_base_order)
        ]
        topic_sections = ""
        for topic in topic_render_order:
            papers = grouped_by_topic.get(topic, [])
            if not papers:
                continue
            cards = "\n".join(render_paper_card(record, index, language) for index, record in papers)
            topic_sections += f"""
          <div class="topic-section">
            <h3 class="topic-heading">{html_escape(topic)} <small>{len(papers)} {html_escape(paper_count_suffix)}</small></h3>
            {cards}
          </div>
            """
        grouped_html += f"""
        <section class="date-section">
          <h2>{html_escape(date_section_label)}：{html_escape(date_label)} <small>{len(day_papers)} {html_escape(paper_count_suffix)}</small></h2>
          {topic_sections}
        </section>
        """

    if not grouped_html:
        grouped_html = f"""
        <section class="empty">
          <h2>{html_escape(no_match_text)}</h2>
          <p>{html_escape(no_match_text)}。</p>
        </section>
        """

    errors_html = ""
    if errors:
        error_items = "".join(f"<li>{html_escape(error)}</li>" for error in errors)
        errors_html = f"""
        <section class="warnings">
          <h2>检索警告</h2>
          <ul>{error_items}</ul>
        </section>
        """

    publication_updates_html = ""
    if publication_updates:
        update_items = "\n".join(render_publication_update(record) for record in publication_updates)
        publication_updates_html = f"""
        <section class="publication-updates">
          <h2>已跟踪 arXiv 的发表信息更新 <small>{len(publication_updates)} 篇</small></h2>
          <ul>{update_items}</ul>
        </section>
        """

    tracking_updates_html = ""
    if tracking_path:
        tracking_items = (
            "".join(f"<li>{html_escape(item)}</li>" for item in tracking_updates)
            if tracking_updates
            else f"<li>{html_escape(no_tracking_text)}</li>"
        )
        tracking_updates_html = f"""
        <section class="tracking-updates">
          <h2>{html_escape(tracking_title)}</h2>
          <p><b>{html_escape(tracking_folder_label)}</b>{html_escape(tracking_path)}</p>
          <ul>{tracking_items}</ul>
        </section>
        """

    count_items = "".join(
        f"<li><b>{html_escape(topic)}</b><span>{count}</span></li>"
        for topic, count in counts.items()
    )
    if skipped_seen_count:
        skipped_seen_html = f"<li><b>已排除此前已汇报</b><span>{skipped_seen_count}</span></li>"
        count_items = skipped_seen_html + count_items
    if publication_updates:
        publication_updates_count_html = (
            f"<li><b>发表信息更新</b><span>{len(publication_updates)}</span></li>"
        )
        count_items = publication_updates_count_html + count_items

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html_escape(field_name)}{html_escape(label)} · {html_escape(report_date)}</title>
  <style>
    :root {{
      --ink: #17202a;
      --muted: #5f6b7a;
      --line: #d8dee8;
      --paper: #ffffff;
      --wash: #f5f7fb;
      --accent: #0d6f75;
      --accent-2: #8a4b20;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background: var(--wash);
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.62;
    }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }}
    .wrap {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(28px, 5vw, 44px);
      letter-spacing: 0;
    }}
    h2 {{
      margin: 34px 0 14px;
      font-size: 24px;
    }}
    h3 {{
      margin: 0;
      font-size: 19px;
      line-height: 1.35;
    }}
    small, .muted, .authors, .meta {{
      color: var(--muted);
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: 1.3fr 1fr;
      gap: 20px;
      align-items: start;
    }}
    .panel, .paper, .empty, .warnings, .tracking-updates {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }}
    .counts {{
      display: grid;
      gap: 10px;
      padding: 0;
      margin: 0;
      list-style: none;
    }}
    .counts li {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 8px;
    }}
    .counts li:last-child {{ border-bottom: 0; padding-bottom: 0; }}
    .highlights {{
      margin: 0;
      padding-left: 20px;
    }}
    .highlights li {{
      margin: 0 0 12px;
    }}
    .highlights small {{
      display: block;
    }}
    .date-counts {{
      margin: 14px 0 0;
    }}
    .date-section {{
      margin-top: 34px;
    }}
    .date-section > h2 {{
      border-bottom: 1px solid var(--line);
      padding-bottom: 8px;
    }}
    .topic-section {{
      margin-top: 20px;
    }}
    .topic-heading {{
      color: var(--accent);
      font-size: 20px;
      margin: 18px 0 10px;
    }}
    .paper {{
      margin: 14px 0;
    }}
    .paper-top {{
      display: grid;
      grid-template-columns: 42px 1fr;
      gap: 12px;
      align-items: start;
    }}
    .rank {{
      width: 34px;
      height: 34px;
      display: grid;
      place-items: center;
      border-radius: 50%;
      background: var(--accent);
      color: white;
      font-weight: 700;
    }}
    .tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 9px;
    }}
    .tags span {{
      border: 1px solid #b7d3d6;
      color: var(--accent);
      background: #eef8f8;
      border-radius: 999px;
      padding: 2px 9px;
      font-size: 13px;
      white-space: nowrap;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 14px;
      font-size: 14px;
      margin: 10px 0;
    }}
    .links {{
      display: flex;
      gap: 10px;
      margin: 8px 0 14px;
    }}
    a {{
      color: #0b5cad;
      text-decoration: none;
    }}
    a:hover {{ text-decoration: underline; }}
    .links a {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 4px 10px;
      background: #fafcff;
    }}
    .insight {{
      border-left: 3px solid var(--accent-2);
      padding-left: 13px;
      background: #fffaf5;
    }}
    .insight p {{
      margin: 8px 0;
    }}
    .bilingual-summary {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 14px;
    }}
    .bilingual-summary > div {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcff;
    }}
    details {{
      margin-top: 12px;
    }}
    summary {{
      cursor: pointer;
      color: var(--muted);
    }}
    .warnings, .publication-updates, .tracking-updates {{
      border-color: #e1bc6c;
      background: #fff9ec;
    }}
    .publication-updates ul, .tracking-updates ul {{
      margin: 0;
      padding-left: 22px;
    }}
    .publication-updates li, .tracking-updates li {{
      margin: 0 0 14px;
    }}
    .publication-updates p, .tracking-updates p {{
      margin: 4px 0;
    }}
    footer {{
      color: var(--muted);
      font-size: 13px;
      padding-bottom: 36px;
    }}
    @media (max-width: 760px) {{
      .summary-grid {{ grid-template-columns: 1fr; }}
      .bilingual-summary {{ grid-template-columns: 1fr; }}
      .paper-top {{ grid-template-columns: 1fr; }}
      .rank {{ border-radius: 6px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>{html_escape(field_name)}{html_escape(label)}</h1>
      <p class="muted">{html_escape(report_date_label)}：{html_escape(report_date)} · {html_escape(window_label)}：{html_escape(window_start.strftime('%Y-%m-%d %H:%M'))} 至 {html_escape(window_end.strftime('%Y-%m-%d %H:%M'))}（Asia/Shanghai）</p>
      {period_extra_html}
    </div>
  </header>
  <main class="wrap">
    <section class="summary-grid">
      <div class="panel">
        <h2>{html_escape(highlights_title)}</h2>
        <ol class="highlights">{highlight_html}</ol>
      </div>
      <div class="panel">
        <h2>{html_escape(counts_title)}</h2>
        <ul class="counts">
          <li><b>{html_escape(total_label)}</b><span>{len(records)}</span></li>
          {count_items}
        </ul>
        {date_distribution_html}
      </div>
    </section>
    {errors_html}
    {publication_updates_html}
    {tracking_updates_html}
    {grouped_html}
  </main>
  <footer class="wrap">
    <p>{html_escape(footer_text)}</p>
  </footer>
</body>
</html>
"""


def make_briefing(
    records: list[dict[str, Any]],
    errors: list[str],
    html_path: Path,
    as_of: dt.datetime,
    days: int,
    skipped_seen_count: int = 0,
    publication_updates: list[dict[str, Any]] | None = None,
    field_name: str = DEFAULT_FIELD_NAME,
    language: str = "zh",
    report_kind: str = "auto",
    tracking_updates: list[str] | None = None,
    tracking_path: str | None = None,
    report_metadata: dict[str, Any] | None = None,
) -> str:
    publication_updates = publication_updates or []
    tracking_updates = tracking_updates or []
    report_date = as_of.strftime("%Y-%m-%d")
    label = report_label(days, report_kind)
    metadata_lines = briefing_metadata_lines(report_metadata, language)
    date_distribution_text = format_date_distribution(record_date_counts(records), language)
    if language == "en":
        en_label = "weekly report" if label == "周报" else "daily report"
        if not records:
            lines = [
                f"{report_date} {field_name} {en_label}: no matching updates in the last {days} days.",
                f"HTML report: {html_path}",
            ]
        else:
            counts = topic_counts(records)
            count_text = "; ".join(f"{topic}: {count}" for topic, count in counts.items())
            top = sorted(records, key=lambda item: (item.get("score", 0), item["updated_local"]), reverse=True)[:3]
            lines = [
                f"{report_date} {field_name} {en_label}: {len(records)} deduplicated records in the last {days} days.",
                f"Topic counts: {count_text}.",
                "Highlights:",
            ]
            for index, record in enumerate(top, start=1):
                lines.append(f"{index}. {record['title']} (arXiv:{record['arxiv_id']})")
            lines.append(f"HTML report: {html_path}")
        if date_distribution_text and records:
            lines.insert(1, f"Date distribution: {date_distribution_text}.")
        if errors:
            lines.append("Warnings: " + " | ".join(errors))
        if publication_updates:
            lines.append("Publication updates:")
            for index, record in enumerate(publication_updates, start=1):
                journal = record.get("journal_ref") or "arXiv has not provided a journal reference"
                doi = record.get("doi") or "arXiv has not provided a DOI"
                lines.append(f"{index}. {record['title']} (arXiv:{record['arxiv_id']})")
                lines.append(f"   Journal: {journal}")
                lines.append(f"   DOI: {doi}")
        if tracking_path:
            lines.append("Group tracking updates:")
            if tracking_updates:
                lines.extend(f"- {item}" for item in tracking_updates)
            else:
                lines.append("- No new group-tracking updates in this run.")
            lines.append(f"Tracking folder: {tracking_path}")
        if skipped_seen_count:
            lines.append(f"Previously reported records excluded automatically: {skipped_seen_count}.")
        if metadata_lines:
            lines[1:1] = metadata_lines
        return "\n".join(lines)
    if language == "bilingual":
        en_label = "weekly report" if label == "周报" else "daily report"
        if not records:
            lines = [
                f"{report_date} {field_name}{label} / {field_name} {en_label}: 近 {days} 天无匹配更新 / no matching updates in the last {days} days.",
                f"HTML 报告 / HTML report: {html_path}",
            ]
        else:
            counts = topic_counts(records)
            count_text = "；".join(f"{topic} {count} 篇" for topic, count in counts.items())
            top = sorted(records, key=lambda item: (item.get("score", 0), item["updated_local"]), reverse=True)[:3]
            lines = [
                f"{report_date} {field_name}{label} / {field_name} {en_label}: 近 {days} 天去重后 {len(records)} 篇 / {len(records)} deduplicated records in the last {days} days.",
                f"主题计数 / Topic counts: {count_text}.",
                "今日重点 / Highlights:",
            ]
            for index, record in enumerate(top, start=1):
                lines.append(f"{index}. {record['title']} (arXiv:{record['arxiv_id']})")
            lines.append(f"HTML 报告 / HTML report: {html_path}")
        if date_distribution_text and records:
            lines.insert(1, f"日期分布 / Date distribution: {date_distribution_text}.")
        if errors:
            lines.append("检索警告 / Warnings: " + " | ".join(errors))
        if publication_updates:
            lines.append("发表信息更新 / Publication updates:")
            for index, record in enumerate(publication_updates, start=1):
                journal = record.get("journal_ref") or "arXiv 尚未提供期刊引用 / arXiv has not provided a journal reference"
                doi = record.get("doi") or "arXiv 尚未提供 DOI / arXiv has not provided a DOI"
                lines.append(f"{index}. {record['title']} (arXiv:{record['arxiv_id']})")
                lines.append(f"   期刊 / Journal: {journal}")
                lines.append(f"   DOI: {doi}")
        if tracking_path:
            lines.append("课题组追踪更新 / Group tracking updates:")
            if tracking_updates:
                lines.extend(f"- {item}" for item in tracking_updates)
            else:
                lines.append("- 本期无新增课题组追踪更新 / No new group-tracking updates in this run.")
            lines.append(f"追踪档 / Tracking folder: {tracking_path}")
        if skipped_seen_count:
            lines.append(f"已自动排除此前已汇报文献 / Previously reported records excluded automatically: {skipped_seen_count}.")
        if metadata_lines:
            lines[1:1] = metadata_lines
        return "\n".join(lines)
    if not records and skipped_seen_count:
        lines = [
            f"{report_date} {field_name}{label}：近 {days} 天没有新增未汇报文献。",
            f"HTML 报告：{html_path}",
        ]
    elif not records:
        lines = [
            f"{report_date} {field_name}{label}：近 {days} 天无匹配更新。",
            f"HTML 报告：{html_path}",
        ]
    else:
        counts = topic_counts(records)
        count_text = "；".join(f"{topic} {count} 篇" for topic, count in counts.items())
        top = sorted(records, key=lambda item: (item.get("score", 0), item["updated_local"]), reverse=True)[:3]
        lines = [
            f"{report_date} {field_name}{label}：近 {days} 天去重后 {len(records)} 篇。",
            f"主题计数：{count_text}。",
            "今日重点：",
        ]
        for index, record in enumerate(top, start=1):
            lines.append(f"{index}. {record['title']} (arXiv:{record['arxiv_id']})")
        lines.append(f"HTML 报告：{html_path}")
    if date_distribution_text and records:
        lines.insert(1, f"日期分布：{date_distribution_text}。")
    if errors and not records:
        lines.insert(1, "检索未完成：本次结果不应视为真实空结果。")
    if errors:
        lines.append("检索警告：" + " | ".join(errors))
    if publication_updates:
        lines.append("发表信息更新：")
        for index, record in enumerate(publication_updates, start=1):
            journal = record.get("journal_ref") or "arXiv 尚未提供期刊引用"
            doi = record.get("doi") or "arXiv 尚未提供 DOI"
            lines.append(f"{index}. {record['title']} (arXiv:{record['arxiv_id']})")
            lines.append(f"   期刊：{journal}")
            lines.append(f"   DOI：{doi}")
    if tracking_path:
        lines.append("课题组追踪更新：")
        if tracking_updates:
            lines.extend(f"- {item}" for item in tracking_updates)
        else:
            lines.append("- 本期无新增课题组追踪更新。")
        lines.append(f"追踪档：{tracking_path}")
    if skipped_seen_count:
        lines.append(f"已自动排除此前已汇报文献：{skipped_seen_count} 篇。")
    if metadata_lines:
        lines[1:1] = metadata_lines
    return "\n".join(lines)


def serializable_report(
    records: list[dict[str, Any]],
    errors: list[str],
    as_of: dt.datetime,
    days: int,
    skipped_seen_count: int = 0,
    publication_updates: list[dict[str, Any]] | None = None,
    field_name: str = DEFAULT_FIELD_NAME,
    language: str = "zh",
    report_kind: str = "auto",
    queries: list[dict[str, str]] | None = None,
    track_group: str | None = None,
    tracking_updates: list[str] | None = None,
    tracking_path: str | None = None,
    report_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    publication_updates = publication_updates or []
    tracking_updates = tracking_updates or []
    report_metadata = dict(report_metadata or {})
    payload = {
        "report_date": as_of.strftime("%Y-%m-%d"),
        "timezone": "Asia/Shanghai",
        "window_start": report_metadata.get("window_start", (as_of - dt.timedelta(days=days)).isoformat()),
        "window_end": report_metadata.get("window_end", as_of.isoformat()),
        "field_name": field_name,
        "language": language,
        "report_kind": report_label(days, report_kind),
        "queries": queries or TOPICS,
        "topics": TOPICS,
        "topic_counts": topic_counts(records),
        "date_counts": record_date_counts(records),
        "total_records": len(records),
        "skipped_seen_records": skipped_seen_count,
        "publication_updates": publication_updates,
        "track_group": track_group,
        "tracking_path": tracking_path,
        "tracking_updates": tracking_updates,
        "records": records,
        "errors": errors,
    }
    payload.update(report_metadata)
    return payload


def state_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / SEEN_STATE_FILENAME


def load_seen_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "seen": {}}
    try:
        raw_state = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "seen": {}}
    if isinstance(raw_state, list):
        return {"version": 1, "seen": {str(item): {} for item in raw_state}}
    if not isinstance(raw_state, dict):
        return {"version": 1, "seen": {}}
    seen = raw_state.get("seen", {})
    if isinstance(seen, list):
        seen = {str(item): {} for item in seen}
    if not isinstance(seen, dict):
        seen = {}
    raw_state["version"] = raw_state.get("version", 1)
    raw_state["seen"] = seen
    return raw_state


def merge_seen_states(paths: list[Path]) -> dict[str, Any]:
    merged: dict[str, Any] = {"version": 1, "seen": {}}
    last_run = ""
    for path in paths:
        state = load_seen_state(path)
        seen = state.get("seen", {})
        if isinstance(seen, dict):
            for base_id, metadata in seen.items():
                previous = merged["seen"].get(base_id, {})
                if not previous:
                    merged["seen"][base_id] = metadata
                    continue
                previous_date = str(previous.get("last_reported") or previous.get("updated_local") or "")
                current_date = str(metadata.get("last_reported") or metadata.get("updated_local") or "")
                if current_date >= previous_date:
                    merged["seen"][base_id] = {**previous, **metadata}
        state_last_run = str(state.get("last_run", ""))
        if state_last_run > last_run:
            last_run = state_last_run
    if last_run:
        merged["last_run"] = last_run
    merged["total_seen"] = len(merged["seen"])
    return merged


def filter_seen_records(
    records: list[dict[str, Any]],
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen_ids = set(state.get("seen", {}).keys())
    fresh: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for record in records:
        if record["base_id"] in seen_ids:
            skipped.append(record)
        else:
            fresh.append(record)
    return fresh, skipped


def has_publication_info(record: dict[str, Any]) -> bool:
    return bool(record.get("journal_ref") or record.get("doi"))


def find_publication_updates(
    records: list[dict[str, Any]],
    state: dict[str, Any],
    report_date: str | None = None,
    include_reported_on_date: bool = False,
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    seen = state.get("seen", {})
    for record in records:
        if not has_publication_info(record):
            continue
        previous = seen.get(record["base_id"], {})
        if (
            include_reported_on_date
            and report_date
            and previous.get("publication_last_reported") == report_date
        ):
            updates.append(record)
            continue
        if record.get("journal_ref") and record.get("journal_ref") != previous.get("journal_ref"):
            updates.append(record)
            continue
        if record.get("doi") and record.get("doi") != previous.get("doi"):
            updates.append(record)
    return updates


def update_seen_state(
    state: dict[str, Any],
    records: list[dict[str, Any]],
    as_of: dt.datetime,
) -> dict[str, Any]:
    state.setdefault("version", 1)
    state.setdefault("seen", {})
    report_date = as_of.strftime("%Y-%m-%d")
    for record in records:
        base_id = record["base_id"]
        previous = state["seen"].get(base_id, {})
        first_reported = previous.get("first_reported", report_date)
        state["seen"][base_id] = {
            "first_reported": first_reported,
            "last_reported": report_date,
            "latest_arxiv_id": record["arxiv_id"],
            "title": record["title"],
            "updated_local": record.get("updated_local", previous.get("updated_local", "")),
            "topics": record.get("topics", []),
        }
        if record.get("journal_ref"):
            state["seen"][base_id]["journal_ref"] = record["journal_ref"]
            state["seen"][base_id]["publication_last_reported"] = report_date
        if record.get("doi"):
            state["seen"][base_id]["doi"] = record["doi"]
            state["seen"][base_id]["publication_last_reported"] = report_date
    state["last_run"] = as_of.isoformat()
    state["total_seen"] = len(state["seen"])
    return state


def save_seen_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def safe_dir_name(value: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*]+', "-", value).strip().strip(".")
    return safe or "tracked-group"


def tracking_group_dir(base_output_dir: str | Path, group_name: str) -> Path:
    return Path(base_output_dir) / "group_tracking" / safe_dir_name(group_name)


def default_tracking_state(group_name: str) -> dict[str, Any]:
    people = DEFAULT_TRACK_PEOPLE if group_name == DEFAULT_TRACK_GROUP else []
    keywords = DEFAULT_TRACK_KEYWORDS if group_name == DEFAULT_TRACK_GROUP else []
    articles = DEFAULT_TRACK_ARTICLES if group_name == DEFAULT_TRACK_GROUP else []
    return {
        "schema_version": "1.0",
        "group_name": group_name,
        "people_keywords": people,
        "topic_keywords": keywords,
        "tracked_articles": articles,
        "related_records": [],
        "last_checked_local": "",
    }


def load_tracking_state(group_dir: Path, group_name: str) -> dict[str, Any]:
    state_path = group_dir / "tracked_topics.json"
    if not state_path.exists():
        return default_tracking_state(group_name)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        state = default_tracking_state(group_name)
    state.setdefault("group_name", group_name)
    state.setdefault("people_keywords", [])
    state.setdefault("topic_keywords", [])
    state.setdefault("tracked_articles", [])
    state.setdefault("related_records", [])
    return state


def record_matches_tracking(record: dict[str, Any], state: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            record.get("title", ""),
            record.get("summary", ""),
            " ".join(record.get("authors", [])),
            " ".join(record.get("categories", [])),
        ]
    ).lower()
    for keyword in state.get("people_keywords", []) + state.get("topic_keywords", []):
        if keyword and keyword.lower() in haystack:
            return True
    return False


def update_tracking_state(
    base_output_dir: str | Path,
    group_name: str | None,
    records: list[dict[str, Any]],
    publication_updates: list[dict[str, Any]],
    as_of: dt.datetime,
) -> tuple[list[str], Path | None]:
    if not group_name:
        return [], None
    group_dir = tracking_group_dir(base_output_dir, group_name)
    group_dir.mkdir(parents=True, exist_ok=True)
    (group_dir / "articles").mkdir(exist_ok=True)
    update_dir = group_dir / "updates" / as_of.strftime("%Y") / as_of.strftime("%m")
    update_dir.mkdir(parents=True, exist_ok=True)

    state = load_tracking_state(group_dir, group_name)
    tracked_by_id = {
        article.get("base_id") or arxiv_base_id(article.get("arxiv_id", "")): article
        for article in state.get("tracked_articles", [])
        if article.get("base_id") or article.get("arxiv_id")
    }
    related_by_id = {
        item.get("base_id") or arxiv_base_id(item.get("arxiv_id", "")): item
        for item in state.get("related_records", [])
        if item.get("base_id") or item.get("arxiv_id")
    }
    changes: list[str] = []
    checked = as_of.isoformat()

    for record in records:
        base_id = record["base_id"]
        if base_id in tracked_by_id:
            article = tracked_by_id[base_id]
            for key in ("arxiv_id", "title", "journal_ref", "doi", "updated_local"):
                if record.get(key) and article.get(key) != record.get(key):
                    article[key] = record.get(key)
            article["last_checked_local"] = checked
        elif record_matches_tracking(record, state) and base_id not in related_by_id:
            related = {
                "arxiv_id": record["arxiv_id"],
                "base_id": base_id,
                "title": record["title"],
                "authors": record.get("authors", []),
                "abs_url": record.get("abs_url", ""),
                "pdf_url": record.get("pdf_url", ""),
                "updated_local": record.get("updated_local", ""),
                "journal_ref": record.get("journal_ref", ""),
                "doi": record.get("doi", ""),
                "first_seen_local": checked,
            }
            state["related_records"].append(related)
            related_by_id[base_id] = related
            changes.append(f"发现相关 arXiv：{record['title']} (arXiv:{record['arxiv_id']})")

    for record in publication_updates:
        base_id = record["base_id"]
        target = tracked_by_id.get(base_id) or related_by_id.get(base_id)
        if not target and record_matches_tracking(record, state):
            target = {
                "arxiv_id": record["arxiv_id"],
                "base_id": base_id,
                "title": record["title"],
                "authors": record.get("authors", []),
                "abs_url": record.get("abs_url", ""),
                "pdf_url": record.get("pdf_url", ""),
                "first_seen_local": checked,
            }
            state["related_records"].append(target)
            related_by_id[base_id] = target
        if target:
            old_journal = target.get("journal_ref", "")
            old_doi = target.get("doi", "")
            if record.get("journal_ref"):
                target["journal_ref"] = record["journal_ref"]
            if record.get("doi"):
                target["doi"] = record["doi"]
            if target.get("journal_ref") != old_journal or target.get("doi") != old_doi:
                changes.append(
                    f"发表信息更新：{record['title']}；期刊：{target.get('journal_ref') or 'arXiv 尚未提供期刊引用'}；DOI：{target.get('doi') or 'arXiv 尚未提供 DOI'}"
                )

    state["last_checked_local"] = checked
    state_path = group_dir / "tracked_topics.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    snapshot_path = update_dir / f"tracking_snapshot_{as_of.strftime('%Y-%m-%d')}.json"
    snapshot_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8-sig")

    timeline_path = group_dir / "timeline.md"
    if not timeline_path.exists():
        timeline_path.write_text(f"# {group_name} 追踪时间线\n\n", encoding="utf-8-sig")
    if changes:
        with timeline_path.open("a", encoding="utf-8") as handle:
            handle.write(f"## {as_of.strftime('%Y-%m-%d %H:%M:%S %z')}\n\n")
            for change in changes:
                handle.write(f"- {change}\n")
            handle.write("\n")
    return changes, group_dir


def write_outputs(
    records: list[dict[str, Any]],
    errors: list[str],
    as_of: dt.datetime,
    args: argparse.Namespace,
    skipped_seen_count: int = 0,
    publication_updates: list[dict[str, Any]] | None = None,
    tracking_updates: list[str] | None = None,
    tracking_path: Path | None = None,
) -> tuple[Path, Path, Path, str]:
    publication_updates = publication_updates or []
    tracking_updates = tracking_updates or []
    weekly = is_weekly_report(args.days, args.report_kind)

    def write_report_file_set(
        output_dir: Path,
        stem: str,
        stamp: str,
        report_records: list[dict[str, Any]],
        report_publication_updates: list[dict[str, Any]],
        report_metadata: dict[str, Any],
    ) -> tuple[Path, Path, Path, str]:
        output_dir.mkdir(parents=True, exist_ok=True)
        html_path = output_dir / f"{stem}_{stamp}.html"
        json_path = output_dir / f"{stem}_{stamp}.json"
        briefing_path = output_dir / f"{stem}_{stamp}.txt"

        html_path.write_text(
            render_html(
                report_records,
                errors,
                as_of,
                args.days,
                skipped_seen_count,
                report_publication_updates,
                args.field_name,
                args.language,
                args.report_kind,
                tracking_updates,
                str(tracking_path) if tracking_path else None,
                report_metadata,
            ),
            encoding="utf-8-sig",
        )
        json_path.write_text(
            json.dumps(
                serializable_report(
                    report_records,
                    errors,
                    as_of,
                    args.days,
                    skipped_seen_count,
                    report_publication_updates,
                    args.field_name,
                    args.language,
                    args.report_kind,
                    configured_search_queries(args),
                    args.track_group,
                    tracking_updates,
                    str(tracking_path) if tracking_path else None,
                    report_metadata,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        briefing = make_briefing(
            report_records,
            errors,
            html_path.resolve(),
            as_of,
            args.days,
            skipped_seen_count,
            report_publication_updates,
            args.field_name,
            args.language,
            args.report_kind,
            tracking_updates,
            str(tracking_path) if tracking_path else None,
            report_metadata,
        )
        briefing_path.write_text(briefing + "\n", encoding="utf-8-sig")
        return html_path, json_path, briefing_path, briefing

    if weekly and getattr(args, "week_start_date", None) and getattr(args, "week_end_date", None):
        week_start: dt.date = args.week_start_date
        week_end: dt.date = args.week_end_date
        base_output_dir = Path(getattr(args, "base_output_dir", args.output_dir))
        segments = list(getattr(args, "week_segments", []))

        if len(segments) > 1:
            for segment in segments:
                segment_start = segment["segment_start"]
                segment_end = segment["segment_end"]
                segment_dir = weekly_report_dir(base_output_dir, week_start, week_end, segment_start)
                segment_records = records_in_date_range(records, segment_start, segment_end)
                segment_updates = records_in_date_range(publication_updates, segment_start, segment_end)
                write_report_file_set(
                    segment_dir,
                    "arxiv_literature_weekly_segment",
                    date_range_stamp(segment_start, segment_end),
                    segment_records,
                    segment_updates,
                    report_metadata_for_scope(args, as_of, "weekly_segment", segment_start, segment_end),
                )

        summary_dir = weekly_report_dir(base_output_dir, week_start, week_end, week_end)
        return write_report_file_set(
            summary_dir,
            "arxiv_literature_weekly_summary",
            date_range_stamp(week_start, week_end),
            records,
            publication_updates,
            report_metadata_for_scope(args, as_of, "weekly_summary", week_start, week_end),
        )

    output_dir = Path(args.output_dir)
    return write_report_file_set(
        output_dir,
        "arxiv_literature_daily_report",
        as_of.strftime("%Y-%m-%d"),
        records,
        publication_updates,
        report_metadata_for_scope(args, as_of, "daily"),
    )


def validate_window(
    records: list[dict[str, Any]],
    as_of: dt.datetime,
    days: int,
    window_start: dt.datetime | None = None,
    window_end: dt.datetime | None = None,
    window_end_exclusive: bool = False,
) -> list[str]:
    start = window_start or as_of - dt.timedelta(days=days)
    end = window_end or as_of
    errors = []
    for record in records:
        updated = dt.datetime.fromisoformat(record["updated_local"])
        in_window = start <= updated < end if window_end_exclusive else start <= updated <= end
        if not in_window:
            errors.append(f"{record['arxiv_id']} outside window: {record['updated_local']}")
    return errors


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="Path to a JSON config file.")
    parser.add_argument("--profile", default=None, help="Profile name inside the JSON config file.")
    parser.add_argument("--days", type=int, default=5, help="Updated-date window in days.")
    parser.add_argument("--as-of", default=None, help="ISO timestamp/date in Asia/Shanghai unless timezone is given.")
    parser.add_argument("--output-dir", default="outputs/arxiv_literature_reports")
    parser.add_argument("--field-name", default=DEFAULT_FIELD_NAME, help="Human-readable report field name.")
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Custom arXiv API search query. Can be repeated. When set, custom query results are kept even if they do not match the built-in polariton/TMD classifier.",
    )
    parser.add_argument("--language", choices=["zh", "en", "bilingual"], default="zh")
    parser.add_argument("--report-kind", choices=["auto", "daily", "weekly"], default="auto")
    parser.add_argument("--track-group", default=None, help="Group name to update under output_dir/group_tracking.")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--sleep-seconds", type=float, default=10.0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retry-attempts", type=int, default=2)
    parser.add_argument("--retry-base-seconds", type=float, default=60.0)
    parser.add_argument(
        "--summary-overrides",
        default=None,
        help="JSON mapping arXiv base IDs to curated Chinese summaries.",
    )
    parser.add_argument(
        "--query-mode",
        choices=["combined", "topic"],
        default="combined",
        help="Use one combined arXiv query by default; topic mode keeps the older per-topic requests.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without writing files.")
    parser.add_argument("--empty-fixture", action="store_true", help="Generate an empty-result report without network.")
    parser.add_argument(
        "--include-seen",
        action="store_true",
        help="Include records that were already reported in earlier runs.",
    )
    parser.add_argument(
        "--include-uncategorized",
        action="store_true",
        help="Keep fetched records that local polariton rules cannot classify.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser()
    args = _apply_config(parser.parse_args(raw_argv), parser, raw_argv)
    as_of = parse_as_of(args.as_of)
    base_output_dir = Path(args.output_dir)
    if args.days <= 0:
        raise SystemExit("--days must be positive")
    if args.page_size <= 0 or args.max_pages <= 0:
        raise SystemExit("--page-size and --max-pages must be positive")
    if args.retry_attempts <= 0 or args.retry_base_seconds <= 0:
        raise SystemExit("--retry-attempts and --retry-base-seconds must be positive")

    weekly = is_weekly_report(args.days, args.report_kind)
    args.base_output_dir = str(base_output_dir)
    if weekly:
        args.days = 7
        args.include_seen = True
        week_start, week_end = fixed_week_dates(as_of)
        window_start, window_end = fixed_week_datetimes(as_of)
        args.window_start = window_start
        args.window_end = window_end
        args.window_end_exclusive = True
        args.week_start_date = week_start
        args.week_end_date = week_end
        args.week_range = date_range_stamp(week_start, week_end)
        args.week_segments = month_segments(week_start, week_end)
        period_dirs: list[str] = []
        for segment in args.week_segments:
            segment_period_dir = str(period_dir(base_output_dir, segment["segment_start"]))
            if segment_period_dir not in period_dirs:
                period_dirs.append(segment_period_dir)
        args.period_dirs = period_dirs
        args.period_dir = str(period_dir(base_output_dir, week_end))
        args.output_dir = str(weekly_report_dir(base_output_dir, week_start, week_end, week_end))
        if args.summary_overrides is None:
            args.summary_overrides = [
                str(Path(period_dir_text) / SUMMARY_STATE_FILENAME)
                for period_dir_text in period_dirs
            ]
    else:
        args.window_start = as_of - dt.timedelta(days=args.days)
        args.window_end = as_of
        args.window_end_exclusive = False
        args.period_dirs = [str(period_dir(base_output_dir, as_of))]
        args.period_dir = args.period_dirs[0]
        args.output_dir = str(period_report_dir(base_output_dir, as_of, args.days, args.report_kind))
        if args.summary_overrides is None:
            args.summary_overrides = str(Path(args.period_dir) / SUMMARY_STATE_FILENAME)

    if args.empty_fixture:
        records: list[dict[str, Any]] = []
        errors: list[str] = []
    else:
        records, errors = collect_records(args, as_of)

    seen_paths = [state_path(Path(period_dir_text)) for period_dir_text in getattr(args, "period_dirs", [args.period_dir])]
    seen_state = merge_seen_states(seen_paths) if weekly else load_seen_state(seen_paths[0])
    skipped_seen_records: list[dict[str, Any]] = []
    publication_updates: list[dict[str, Any]] = []
    report_date = as_of.strftime("%Y-%m-%d")
    if args.include_seen:
        publication_updates = find_publication_updates(
            records,
            seen_state,
            report_date,
            include_reported_on_date=True,
        )
    else:
        records, skipped_seen_records = filter_seen_records(records, seen_state)
        publication_updates = find_publication_updates(skipped_seen_records, seen_state)

    window_errors = validate_window(
        records,
        as_of,
        args.days,
        args.window_start,
        args.window_end,
        args.window_end_exclusive,
    )
    if window_errors:
        errors.extend(window_errors)

    tracking_updates: list[str] = []
    tracking_path = tracking_group_dir(base_output_dir, args.track_group) if args.track_group else None
    dry_run_metadata = report_metadata_for_scope(
        args,
        as_of,
        "weekly_summary" if weekly else "daily",
        getattr(args, "week_start_date", None) if weekly else None,
        getattr(args, "week_end_date", None) if weekly else None,
    )

    if args.dry_run:
        print(
            json.dumps(
                {
                    "report_date": as_of.strftime("%Y-%m-%d"),
                    "report_scope": dry_run_metadata["report_scope"],
                    "window_start": dry_run_metadata["window_start"],
                    "window_end": dry_run_metadata["window_end"],
                    "window_end_exclusive": dry_run_metadata["window_end_exclusive"],
                    "week_start": dry_run_metadata.get("week_start"),
                    "week_end": dry_run_metadata.get("week_end"),
                    "segment_start": dry_run_metadata.get("segment_start"),
                    "segment_end": dry_run_metadata.get("segment_end"),
                    "total_records": len(records),
                    "skipped_seen_records": len(skipped_seen_records),
                    "publication_update_records": len(publication_updates),
                    "topic_counts": topic_counts(records),
                    "field_name": args.field_name,
                    "language": args.language,
                    "report_kind": report_label(args.days, args.report_kind),
                    "track_group": args.track_group,
                    "tracking_path": str(tracking_path) if tracking_path else None,
                    "tracking_updates": tracking_updates,
                    "publication_updates": [
                        {
                            "arxiv_id": record["arxiv_id"],
                            "title": record["title"],
                            "journal_ref": record.get("journal_ref", ""),
                            "doi": record.get("doi", ""),
                        }
                        for record in publication_updates
                    ],
                    "records": [
                        {
                            "arxiv_id": record["arxiv_id"],
                            "updated_local": record["updated_local"],
                            "topics": record["topics"],
                            "title": record["title"],
                        }
                        for record in records
                    ],
                    "errors": errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if errors else 0

    tracking_updates, tracking_path = update_tracking_state(
        base_output_dir,
        args.track_group,
        records,
        publication_updates,
        as_of,
    )

    html_path, json_path, briefing_path, briefing = write_outputs(
        records,
        errors,
        as_of,
        args,
        len(skipped_seen_records),
        publication_updates,
        tracking_updates,
        tracking_path,
    )
    if not weekly and not args.include_seen:
        save_seen_state(
            state_path(args.period_dir),
            update_seen_state(seen_state, records + publication_updates, as_of),
        )
    print(briefing)
    print(f"JSON 数据：{json_path.resolve()}")
    print(f"简报文本：{briefing_path.resolve()}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
