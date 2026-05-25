# arXiv Literature Report Skill

## Current default coverage

The built-in polariton profile now searches and classifies exciton polaritons, 2D/TMD systems, perovskite polaritons, plasmonics, optical microcavities, and photonic crystal cavities.

Every report card includes a lightweight `nature-reader` + `nature-polishing` style digest based on the arXiv abstract:

- Chinese reading summary
- key takeaways
- polished English guide
- full English abstract
- materials/systems, methods/evidence, and why-it-matters notes

The digest is source-grounded rather than keyword-only: it extracts problem, evidence/approach, and result/implication signals from the abstract, then formats them as fluent Chinese guide text plus an automatic Chinese abstract rendering without inventing data or claims. Internal workflow names should not appear in generated report cards.

The daily and weekly automation remains source-grounded to arXiv metadata. Full-paper bilingual readers, figure extraction, and page-level source maps should still be generated through the separate `nature-reader` workflow when a paper needs intensive reading.

## Fixed weekly archives

Weekly reports now use a fixed Asia/Shanghai natural-week window: Monday 00:00 through the following Monday 00:00, displayed as Monday to Sunday. `--report-kind weekly` ignores `--days` for the window but keeps the flag for compatibility.

Weekly output paths are stable and are not dynamically rolled forward or cleaned by ISO week. Re-running the same week overwrites the same summary target only.

For a normal week, the summary is written under:

```text
outputs/arxiv_literature_reports/YYYY/MM/周报/YYYY-MM-DD_to_YYYY-MM-DD/
arxiv_literature_weekly_summary_YYYY-MM-DD_to_YYYY-MM-DD.*
```

For a cross-month week, each involved month gets the same week folder with its own segment report, and the Sunday month also keeps the full-week summary:

```text
outputs/arxiv_literature_reports/2026/06/周报/2026-06-29_to_2026-07-05/arxiv_literature_weekly_segment_2026-06-29_to_2026-06-30.*
outputs/arxiv_literature_reports/2026/07/周报/2026-06-29_to_2026-07-05/arxiv_literature_weekly_segment_2026-07-01_to_2026-07-05.*
outputs/arxiv_literature_reports/2026/07/周报/2026-06-29_to_2026-07-05/arxiv_literature_weekly_summary_2026-06-29_to_2026-07-05.*
```

Weekly JSON/TXT/HTML outputs include `report_scope`, `week_start`, `week_end`, `segment_start`, and `segment_end`. Weekly runs merge seen-state and summary override files from all months touched by the week, but they do not write seen state; daily runs remain responsible for deduplication state.

Daily and weekly outputs also include `date_counts`, show the date distribution in TXT/HTML, and render paper cards under updated-date sections before topic subsections. This keeps a 5-day daily window readable instead of mixing all records into one undated list.

一个可配置的 arXiv 文献日报/周报生成器，同时提供 Python CLI 和 Codex skill。它可以按用户指定的研究领域检索 arXiv，基于更新时间窗口去重，生成 HTML、JSON 和 TXT 报告，并长期追踪重点课题组或重点论文的发表状态。

默认示例面向“极化激元 + 2D/TMD + 钙钛矿极化激元”方向，并内置 Mak-Shan 通讯团队追踪模板；你也可以不用改 Python 代码，直接通过 JSON 配置换成自己的领域，例如 WSe2 superconductivity、二维磁性材料、perovskite polariton、LLM agents 等。

## 功能

- 按 arXiv API 检索任意关键词或检索式。
- 支持日报和固定周一到周日周报，重跑只覆盖同一周固定目标，不清理其它周报。
- 支持中文、英文、中英双语报告：`zh`、`en`、`bilingual`。
- 输出 HTML、JSON、TXT 三种文件。
- 用 seen-state 文件记录已报告 arXiv ID，避免日报重复。
- 追踪已见 preprint 的 DOI 和 journal reference 更新。
- 维护课题组追踪目录，记录重点作者、主题、论文和时间线。
- 无第三方 Python 依赖，适合本地、服务器或自动化环境运行。
- 可作为 Codex skill 调用，并可桥接 `nature-reader`、`nature-polishing`、`nature-academic-search`、`nature-paper2ppt` 等增强工作流。

## 安装

Windows PowerShell:

```powershell
git clone https://github.com/<your-name>/arxiv-literature-report-skill.git
cd arxiv-literature-report-skill
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

macOS/Linux:

```bash
git clone https://github.com/<your-name>/arxiv-literature-report-skill.git
cd arxiv-literature-report-skill
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

安装后会得到命令：

```powershell
arxiv-literature-report --help
```

也可以不安装，直接从源码运行：

```powershell
python -m arxiv_literature_report --help
```

如果你的 Python 环境不能联网安装 build backend，可以先用源码运行，或在已有 `setuptools` 的环境中使用：

```powershell
pip install -e . --no-build-isolation
```

## 快速生成第一份周报

使用默认极化激元/TMD profile：

```powershell
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --report-kind weekly --language zh --include-seen
```

生成中英双语周报：

```powershell
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --report-kind weekly --language bilingual --include-seen
```

测试环境或演示时可以不用联网：

```powershell
arxiv-literature-report --empty-fixture --report-kind weekly --language zh
```

默认输出到：

```text
outputs/arxiv_literature_reports/YYYY/MM/日报/
outputs/arxiv_literature_reports/YYYY/MM/周报/YYYY-MM-DD_to_YYYY-MM-DD/
```

每份报告会生成：

```text
arxiv_literature_weekly_summary_YYYY-MM-DD_to_YYYY-MM-DD.html
arxiv_literature_weekly_summary_YYYY-MM-DD_to_YYYY-MM-DD.json
arxiv_literature_weekly_summary_YYYY-MM-DD_to_YYYY-MM-DD.txt
```

跨月周还会在每个涉及月份写入 `arxiv_literature_weekly_segment_SEGMENT-START_to_SEGMENT-END.*`。日报对应文件名为 `arxiv_literature_daily_report_YYYY-MM-DD.*`。

## 自定义领域

最直接的方式是命令行传入字段名和 arXiv query：

```powershell
arxiv-literature-report --field-name "WSe2 superconductivity" --query "all:WSe2 AND all:superconductivity" --report-kind weekly --language bilingual --include-uncategorized
```

更推荐使用配置文件。编辑 `examples/config.example.json`，新增一个 profile：

```json
{
  "profiles": {
    "my_field": {
      "field_name": "My research field",
      "query": [
        "all:keyword1 AND all:keyword2",
        "all:\"exact phrase\""
      ],
      "days": 7,
      "language": "zh",
      "report_kind": "weekly",
      "include_uncategorized": true
    }
  }
}
```

运行：

```powershell
arxiv-literature-report --config examples/config.example.json --profile my_field
```

### arXiv query 提示

- `all:WSe2 AND all:superconductivity`：标题、摘要、作者等全部字段中同时包含两个词。
- `all:"exciton polariton"`：精确短语。
- `(all:MoTe2 OR all:WSe2) AND all:moire`：组合检索。
- 如果你希望保留所有自定义 query 拉到的结果，建议加 `include_uncategorized: true` 或命令行 `--include-uncategorized`。

## 语言模式

中文报告：

```powershell
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --language zh
```

英文报告：

```powershell
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --language en
```

中英双语报告：

```powershell
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --language bilingual
```

## 课题组追踪

开启 Mak-Shan 通讯团队追踪：

```powershell
arxiv-literature-report --config examples/config.example.json --profile mak_shan_tracking --track-group Mak-Shan通讯团队 --report-kind weekly --include-seen
```

追踪目录默认写入：

```text
outputs/arxiv_literature_reports/group_tracking/Mak-Shan通讯团队/
```

目录中包含：

```text
tracked_topics.json
timeline.md
updates/YYYY/MM/
articles/<safe-paper-title>/
```

`tracked_topics.json` 是机器可读状态，适合后续自动检查 DOI、journal_ref、arXiv 版本和相关新作；`timeline.md` 是人工可读时间线，适合快速回顾课题组动态。

## 配置文件结构

`examples/config.example.json` 支持：

- `defaults`：所有 profile 共用的默认参数。
- `profiles`：不同研究方向或任务的配置。
- `field_name`：报告标题中的领域名称。
- `query`：字符串或字符串数组，可使用 arXiv API query 语法。
- `days`：更新时间窗口。
- `language`：`zh`、`en`、`bilingual`。
- `report_kind`：`daily`、`weekly`、`auto`。
- `output_dir`：输出根目录。
- `track_group`：课题组名称。
- `include_seen`：是否包含已经报告过的文献，周报通常建议开启。
- `include_uncategorized`：自定义领域建议开启，避免内置 TMD/极化激元分类器过滤掉跨领域结果。

命令行参数优先级高于配置文件。例如：

```powershell
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --language en
```

会使用 `polariton_tmd` 的其他配置，但临时把语言改成英文。

## Codex skill 使用

这个仓库同时包含 Codex skill：

```text
skills/arxiv-literature-report/
```

安装方式：

```powershell
Copy-Item -Recurse .\skills\arxiv-literature-report "$env:CODEX_HOME\skills\arxiv-literature-report"
```

如果没有设置 `CODEX_HOME`，通常可以复制到：

```text
%USERPROFILE%\.codex\skills\arxiv-literature-report
```

在 Codex 中可以这样请求：

```text
用 arxiv-literature-report skill 帮我生成 WSe2 superconductivity 的中英双语周报。
```

skill 会优先调用已安装的 `arxiv-literature-report` CLI；如果在本仓库内使用，也可以调用 `python -m arxiv_literature_report`。

## 与 nature skills 联动

普通 CLI 不依赖 nature 系列 skills。Codex 环境中如果安装了相关 skills，可以按需增强：

- `nature-reader`：全文精读、中英双语对照、逐图讲解、HTML reader。
- `nature-polishing`：中文学术摘要或英文摘要润色。
- `nature-academic-search`：arXiv 之外的 DOI、CrossRef、PubMed 或引用核验。
- `nature-paper2ppt`：把精读论文转成组会 PPT。

## arXiv 429/503 与重试

arXiv API 有速率限制。脚本默认：

- 使用 `User-Agent` 标识。
- 每页请求之间等待 `--sleep-seconds`。
- 遇到 429、503 或 rate-limit 文本会按 `--retry-attempts` 和 `--retry-base-seconds` 重试。
- 如果 arXiv API 持续 429/503，脚本会退到官方 arXiv OAI `ListRecords`，扫描 `physics:cond-mat`、`physics:physics`、`physics:quant-ph` 和 `eess:eess`，再用本地极化激元/TMD/等离激元/微腔规则做严格过滤。这样可以避免把 API 限流误报成“真实空结果”。

如果你频繁运行多个 profile，建议增大间隔：

```powershell
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --sleep-seconds 20 --retry-attempts 3
```

## 开发与测试

```powershell
python -m compileall src skills/arxiv-literature-report/scripts
arxiv-literature-report --empty-fixture --report-kind weekly --language zh
arxiv-literature-report --config examples/config.example.json --profile polariton_tmd --empty-fixture
arxiv-literature-report --field-name "WSe2 superconductivity" --query "all:WSe2 AND all:superconductivity" --language bilingual --empty-fixture
arxiv-literature-report --config examples/config.example.json --profile mak_shan_tracking --track-group Mak-Shan通讯团队 --empty-fixture
```

发布前建议扫描个人路径和生成文件：

```powershell
Select-String -Path .\* -Pattern 'E:\\notebook','C:\\Users\\25431' -Recurse
```

## 发布到 GitHub

如果你使用 GitHub CLI：

```powershell
gh auth login -h github.com
gh repo create arxiv-literature-report-skill --public --source . --remote origin --push
```

如果已经在 GitHub 网页端创建了空仓库：

```powershell
git remote add origin https://github.com/<your-name>/arxiv-literature-report-skill.git
git push -u origin main
```

发布前确认：

```powershell
git status --short
git log --oneline -1
```

## 常见问题

**周报如何处理已在日报出现过的论文？**

日报会记录已报告 ID，周报需要汇总本周完整内容，所以 weekly 运行会自动包含 seen-state 中已由日报报告过的论文，并且不会写回 seen state。

**为什么自定义领域结果为空？**

先确认 arXiv query 是否过窄，再尝试加 `--include-uncategorized`。内置分类器偏向极化激元/TMD，如果你的领域完全不同，应使用自定义 query 并保留 uncategorized 结果。

**能不能自动翻译成高质量中文摘要？**

内置摘要已按 `nature-reader` 的证据定位和 `nature-polishing` 的摘要逻辑生成，会保留摘要中的问题、证据路径和结论线索。若需要全文级中文润色、图文对应或逐段翻译，仍应在 Codex 中联动完整的 `nature-reader` 或 `nature-polishing` 工作流。

**这个项目会下载 PDF 吗？**

默认不会。报告只链接 arXiv abstract 和 PDF URL。全文精读、图片裁剪、reader HTML 等任务交给可选的 `nature-reader` 工作流处理。

**离线环境里 `pip install -e .` 失败怎么办？**

如果错误来自安装 `setuptools` 等 build dependency，而不是项目代码本身，可以直接用 `PYTHONPATH=src python -m arxiv_literature_report ...` 运行，或在已经安装 `setuptools` 的环境里加 `--no-build-isolation`。
