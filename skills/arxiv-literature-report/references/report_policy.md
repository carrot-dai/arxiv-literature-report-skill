# arXiv Report Policy

## Defaults

Default field name: `arXiv 极化激元与二维/TMD 材料`.

Default topics:

- exciton polaritons, exciton-polaritons, polaritonic condensates, microcavity polaritons
- TMD/TMDC and 2D systems including MoS2, MoSe2, WS2, WSe2, MoTe2, moire WSe2, twisted WSe2, moire semiconductors
- perovskite polaritons and halide-perovskite strong-coupling systems

TMD superconductivity, WSe2 superconductivity, MoTe2 superconductivity, moire Chern bands, and strongly correlated TMD papers are in scope even when they are not explicitly polariton papers.

## Report cadence

Daily reports use `--days 5` by default and write to `YYYY/MM/日报`.

Weekly reports use `--report-kind weekly --include-seen` and write to `YYYY/MM/周报`.

Only keep the latest weekly report for the same ISO week. The script removes older weekly HTML/JSON/TXT files from that weekly folder before writing the new one.

## Language modes

`--language zh` writes a Chinese report with English abstract details in each card.

`--language en` writes English report text and English abstracts first, with Chinese notes available in the paper cards.

`--language bilingual` writes bilingual report labels and side-by-side English/Chinese summary cards where possible.

## Publication updates

The seen-state file stores reported arXiv base IDs plus known DOI and journal reference fields. If a previously seen preprint later gains a DOI or `journal_ref`, surface it in the report's publication-update section and update tracking files when a tracked group or tracked paper matches.

## Group tracking

For `Mak-Shan通讯团队`, use these default people keywords:

- Kin Fai Mak
- Jie Shan
- Zhongdong Han
- Wenjin Zhao
- Yichi Zhang
- Yiyu Xia
- Zhengchao Xia

Use these default topic keywords:

- MoTe2/WSe2
- stacking order
- SHG calibration
- ADF-STEM
- Chern insulator
- mixed-valence topological insulator
- Kondo lattice
- TMD superconductivity
- moire Chern band

Report files only summarize changes. Long-lived history belongs in the group folder's `tracked_topics.json` and `timeline.md`.

## Validation

Always validate that JSON outputs parse, HTML/TXT paths exist, and tracked group files update when `--track-group` is used.

