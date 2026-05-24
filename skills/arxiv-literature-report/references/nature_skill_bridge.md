# Nature Skill Bridge

Use this reference when an arXiv report task needs a specialized nature workflow.

## nature-reader

Use `nature-reader` when the user asks for:

- 精读
- 全文翻译
- 中英双语对照
- 原文/中文段落对照
- 按图讲解
- source-grounded Markdown/HTML reader

Required reader outputs:

- `paper.md`
- `reader.html`
- `source_map.json`
- `translation_notes.md`
- `metadata.json`
- `assets/`

Default HTML layout is side-by-side bilingual: English original on the left and Chinese translation on the right. Figure captions should also be English/Chinese paired.

## nature-polishing

Use `nature-polishing` when the user asks for academic abstract polishing, Nature-style academic wording, or polished Chinese-to-English prose. For report summaries, preserve factual content and avoid overclaiming beyond the arXiv abstract.

## nature-academic-search

Use `nature-academic-search` when arXiv metadata is insufficient and the user needs DOI, CrossRef, PubMed, citation, or publication verification beyond the arXiv Atom feed.

## nature-paper2ppt

Use `nature-paper2ppt` when the user asks to convert a tracked paper or reader into a group-meeting, journal-club, or lab-meeting PPTX deck.

## Tracking integration

When a nature skill creates an artifact for a tracked paper, write the artifact path back to that paper's `metadata.json` and the group's `tracked_topics.json`.

