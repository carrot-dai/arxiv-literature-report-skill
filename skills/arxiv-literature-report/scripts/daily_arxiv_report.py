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


API_URL = "https://export.arxiv.org/api/query"
USER_AGENT = "ArxivLiteratureReport/1.0 (+https://arxiv.org/help/api)"
SEEN_STATE_FILENAME = "arxiv_literature_seen_ids.json"
SUMMARY_STATE_FILENAME = "arxiv_literature_cn_summaries.json"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
RATE_LIMIT_BODY = "rate exceeded"


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


def load_summary_overrides(path: str | Path) -> dict[str, str]:
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


def collect_records(args: argparse.Namespace, as_of: dt.datetime) -> tuple[list[dict[str, Any]], list[str]]:
    window_start = (as_of - dt.timedelta(days=args.days)).astimezone(UTC)
    window_end = as_of.astimezone(UTC)
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
                if window_start <= updated <= window_end:
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


def html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def is_weekly_report(days: int, report_kind: str = "auto") -> bool:
    return report_kind == "weekly" or (report_kind == "auto" and days >= 7)


def report_label(days: int, report_kind: str = "auto") -> str:
    return "周报" if is_weekly_report(days, report_kind) else "日报"


def period_dir(base_output_dir: str | Path, as_of: dt.datetime) -> Path:
    return Path(base_output_dir) / as_of.strftime("%Y") / as_of.strftime("%m")


def period_report_dir(
    base_output_dir: str | Path,
    as_of: dt.datetime,
    days: int,
    report_kind: str = "auto",
) -> Path:
    subdir = "周报" if is_weekly_report(days, report_kind) else "日报"
    return period_dir(base_output_dir, as_of) / subdir


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
) -> str:
    publication_updates = publication_updates or []
    tracking_updates = tracking_updates or []
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
        footer_text = "数据源：arXiv API。记录按 arXiv ID 去重；更新时间使用 arXiv updated 字段过滤。中文要点由本地规则基于题名与摘要自动提取，正式引用前请打开原文核对。"
    window_start = as_of - dt.timedelta(days=days)
    counts = topic_counts(records)
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

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, record in enumerate(records, start=1):
        grouped.setdefault(primary_topic(record), []).append((index, record))

    grouped_html = ""
    topic_render_order = PRIMARY_TOPIC_ORDER + [
        topic for topic in grouped if topic not in set(PRIMARY_TOPIC_ORDER + ["其他相关文献"])
    ] + ["其他相关文献"]
    for topic in topic_render_order:
        papers = grouped.get(topic, [])
        if not papers:
            continue
        cards = "\n".join(render_paper_card(record, index, language) for index, record in papers)
        grouped_html += f"""
        <section class="topic-section">
          <h2>{html_escape(topic)} <small>{len(papers)} 篇</small></h2>
          {cards}
        </section>
        """

    if not grouped_html:
        grouped_html = """
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
    .topic-section {{
      margin-top: 28px;
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
      <p class="muted">{html_escape(report_date_label)}：{html_escape(report_date)} · {html_escape(window_label)}：{html_escape(window_start.strftime('%Y-%m-%d %H:%M'))} 至 {html_escape(as_of.strftime('%Y-%m-%d %H:%M'))}（Asia/Shanghai）</p>
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
) -> str:
    publication_updates = publication_updates or []
    tracking_updates = tracking_updates or []
    report_date = as_of.strftime("%Y-%m-%d")
    label = report_label(days, report_kind)
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
) -> dict[str, Any]:
    publication_updates = publication_updates or []
    tracking_updates = tracking_updates or []
    return {
        "report_date": as_of.strftime("%Y-%m-%d"),
        "timezone": "Asia/Shanghai",
        "window_start": (as_of - dt.timedelta(days=days)).isoformat(),
        "window_end": as_of.isoformat(),
        "field_name": field_name,
        "language": language,
        "report_kind": report_label(days, report_kind),
        "queries": queries or TOPICS,
        "topics": TOPICS,
        "topic_counts": topic_counts(records),
        "total_records": len(records),
        "skipped_seen_records": skipped_seen_count,
        "publication_updates": publication_updates,
        "track_group": track_group,
        "tracking_path": tracking_path,
        "tracking_updates": tracking_updates,
        "records": records,
        "errors": errors,
    }


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


def remove_prior_weekly_outputs(output_dir: Path, as_of: dt.datetime, current_stamp: str) -> list[Path]:
    """Keep one weekly report per ISO week to avoid repeated overlapping reports."""
    if not output_dir.exists():
        return []

    stem = "arxiv_polariton_weekly_report"
    current_week = as_of.isocalendar()[:2]
    removed: list[Path] = []
    seen_stamps: set[str] = set()
    for html_path in output_dir.glob(f"{stem}_*.html"):
        date_text = html_path.stem.removeprefix(f"{stem}_")
        if date_text == current_stamp or date_text in seen_stamps:
            continue
        seen_stamps.add(date_text)
        try:
            report_date = dt.datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            continue
        if report_date.isocalendar()[:2] != current_week:
            continue
        for ext in (".html", ".json", ".txt"):
            report_path = output_dir / f"{stem}_{date_text}{ext}"
            if report_path.exists():
                report_path.unlink()
                removed.append(report_path)
    return removed


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
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of.strftime("%Y-%m-%d")
    weekly = is_weekly_report(args.days, args.report_kind)
    stem = "arxiv_literature_weekly_report" if weekly else "arxiv_literature_daily_report"
    if weekly:
        remove_prior_weekly_outputs(output_dir, as_of, stamp)
    html_path = output_dir / f"{stem}_{stamp}.html"
    json_path = output_dir / f"{stem}_{stamp}.json"
    briefing_path = output_dir / f"{stem}_{stamp}.txt"

    html_path.write_text(
        render_html(
            records,
            errors,
            as_of,
            args.days,
            skipped_seen_count,
            publication_updates,
            args.field_name,
            args.language,
            args.report_kind,
            tracking_updates,
            str(tracking_path) if tracking_path else None,
        ),
        encoding="utf-8-sig",
    )
    json_path.write_text(
        json.dumps(
            serializable_report(
                records,
                errors,
                as_of,
                args.days,
                skipped_seen_count,
                publication_updates,
                args.field_name,
                args.language,
                args.report_kind,
                configured_search_queries(args),
                args.track_group,
                tracking_updates,
                str(tracking_path) if tracking_path else None,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    briefing = make_briefing(
        records,
        errors,
        html_path.resolve(),
        as_of,
        args.days,
        skipped_seen_count,
        publication_updates,
        args.field_name,
        args.language,
        args.report_kind,
        tracking_updates,
        str(tracking_path) if tracking_path else None,
    )
    briefing_path.write_text(briefing + "\n", encoding="utf-8-sig")
    return html_path, json_path, briefing_path, briefing


def validate_window(records: list[dict[str, Any]], as_of: dt.datetime, days: int) -> list[str]:
    start = as_of - dt.timedelta(days=days)
    errors = []
    for record in records:
        updated = dt.datetime.fromisoformat(record["updated_local"])
        if not (start <= updated <= as_of):
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
    if args.report_kind == "weekly" and args.days < 7:
        args.days = 7
    base_output_dir = Path(args.output_dir)
    args.period_dir = str(period_dir(base_output_dir, as_of))
    args.output_dir = str(period_report_dir(base_output_dir, as_of, args.days, args.report_kind))
    if args.summary_overrides is None:
        args.summary_overrides = str(Path(args.period_dir) / SUMMARY_STATE_FILENAME)
    if args.days <= 0:
        raise SystemExit("--days must be positive")
    if args.page_size <= 0 or args.max_pages <= 0:
        raise SystemExit("--page-size and --max-pages must be positive")
    if args.retry_attempts <= 0 or args.retry_base_seconds <= 0:
        raise SystemExit("--retry-attempts and --retry-base-seconds must be positive")

    if args.empty_fixture:
        records: list[dict[str, Any]] = []
        errors: list[str] = []
    else:
        records, errors = collect_records(args, as_of)

    seen_state = load_seen_state(state_path(args.period_dir))
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

    window_errors = validate_window(records, as_of, args.days)
    if window_errors:
        errors.extend(window_errors)

    tracking_updates: list[str] = []
    tracking_path = tracking_group_dir(base_output_dir, args.track_group) if args.track_group else None

    if args.dry_run:
        print(
            json.dumps(
                {
                    "report_date": as_of.strftime("%Y-%m-%d"),
                    "window_start": (as_of - dt.timedelta(days=args.days)).isoformat(),
                    "window_end": as_of.isoformat(),
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
    if not args.include_seen:
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
