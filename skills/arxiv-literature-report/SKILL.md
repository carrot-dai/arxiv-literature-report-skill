---
name: arxiv-literature-report
description: Generate configurable arXiv daily or weekly literature reports with HTML/JSON/TXT outputs, Chinese/English/bilingual modes, deduplication, publication-status updates, research-group tracking, and optional handoff to nature skills for full-paper reading, polishing, citation search, or PPT generation. Use when the user asks to search a research field on arXiv, make a literature report, track a group such as Mak-Shan, monitor whether preprints have been published, or organize intensive-reading outputs.
---

# arXiv Literature Report

Use this skill to generate arXiv literature reports or maintain long-running paper and research-group tracking.

## Core workflow

1. Prefer the installed CLI:

```powershell
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --report-kind weekly --language zh
```

2. If the current workspace is this repository, running the module is also valid:

```powershell
python -m arxiv_literature_report --config examples/config.example.json --profile polariton_tmd --report-kind weekly --language zh
```

3. If only this skill folder was copied, use the bundled script:

```powershell
python scripts/daily_arxiv_report.py --report-kind weekly --language zh
```

## Common tasks

Default weekly report:

```powershell
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --report-kind weekly --include-seen
```

Custom field report:

```powershell
arxiv-literature-report --field-name "WSe2 superconductivity" --query "all:WSe2 AND all:superconductivity" --language bilingual --include-uncategorized
```

Mak-Shan tracking report:

```powershell
arxiv-literature-report --config examples/config.example.json --profile mak_shan_tracking --track-group Mak-Shan通讯团队 --include-seen
```

Network-free smoke test:

```powershell
arxiv-literature-report --empty-fixture --report-kind weekly --language zh
```

## Parameters

- `--config`: JSON config file with defaults and named profiles.
- `--profile`: profile key inside the config file.
- `--field-name`: human-readable field name used in titles and JSON metadata.
- `--query`: custom arXiv API query. Can be repeated.
- `--days`: arXiv updated-date window.
- `--language zh|en|bilingual`: report language. Default is `zh`.
- `--report-kind auto|daily|weekly`: output folder and report labeling.
- `--track-group`: update `outputs/arxiv_literature_reports/group_tracking/<group-name>/`.
- `--include-seen`: include already-reported records, useful for weekly rollups.
- `--include-uncategorized`: keep records from custom queries even if the built-in classifier does not categorize them.
- `--empty-fixture`: write an empty report without network; use for smoke tests.

## Output layout

Reports write to:

```text
outputs/arxiv_literature_reports/YYYY/MM/日报/
outputs/arxiv_literature_reports/YYYY/MM/周报/
```

Research-group tracking writes to:

```text
outputs/arxiv_literature_reports/group_tracking/<group-name>/
```

Each group folder should contain `tracked_topics.json`, `timeline.md`, `updates/YYYY/MM/`, and optionally `articles/<safe-paper-title>/`.

## Nature skill handoff

Read `references/nature_skill_bridge.md` before invoking another nature skill.

Use `nature-reader` for intensive reading, full translation, figure-by-figure explanation, or bilingual HTML.

Use `nature-polishing` for polished academic abstracts or Nature-style wording.

Use `nature-academic-search` for DOI, CrossRef, PubMed, citation, or publication verification beyond arXiv metadata.

Use `nature-paper2ppt` for group-meeting or journal-club PPTX decks.

## Failure handling

If arXiv returns transient 429/503 errors, rely on the script retry behavior. If the run still exits with warnings or errors, report the warning text and any generated paths.

