# Mineru → EPUB

[中文说明](./README_zh.md)

Convert Mineru-processed PDFs into Kindle-ready EPUBs with popup footnotes, chapter navigation, and embedded images.

## What it does

Mineru (from [opendatalab](https://github.com/opendatalab/MinerU)) produces high-quality structured output from PDFs: JSON block trees + Markdown + extracted images. But this output is page-oriented — footnotes are scattered across page boundaries with numbering that resets every page. This tool bridges the gap to a reflowable ebook:

- **Auto-detects footnote patterns** (circled numbers, superscript digits, symbols) with no manual config
- **Cross-page footnote matching** — heals page breaks where a ref and its body land on different pages
- **Multi-type footnote sections** — translator notes, bibliographic references, and author notes are separated and labeled by an LLM agent
- **Paragraph merging** — fixes mid-sentence breaks caused by PDF page boundaries
- **Enumeration filtering** — distinguishes real footnotes from list markers (①②③ used as item numbers)

## Quick Start

```bash
# 1. Discover footnote patterns (no config needed)
python3 scripts/convert.py --profile --parts part1/ part2/ > profile.json

# 2. Have an LLM read profile.json and generate footnote_config.json

# 3. Convert
python3 scripts/convert.py --convert \
  --parts part1/ part2/ \
  --footnote-config footnote_config.json \
  --config chapters_config.json \
  --output book.epub \
  --title "Book Title" --author "Author" \
  --cover-pdf original.pdf
```

## Processing new PDFs via Mineru API

```bash
# Set API key (one-time)
export MINERU_API_KEY="sk-xxx"

# Process PDF — auto-splits if >200 pages
python3 scripts/mineru_api.py process book.pdf --output ./output/
```

API key resolution: `--api-key` flag > `MINERU_API_KEY` env var > `~/.mineru_api_key` file.

## Supported footnote patterns

| Pattern | Markers | Common label | Display style |
|---------|---------|-------------|---------------|
| Circled ideographs | ①②③ | Translator notes | ① (native circle) |
| Superscript digits | ¹²³ | Bibliographic references | [1] (bracket) |
| Symbols | *†‡ | Author notes | * (asterisk) |

The script makes **zero semantic assumptions** — all labeling decisions are made by an LLM agent guided by `SKILL.md`.

## Example output

```
── 译者注 ──────────       ── 参考文献 ──────────
① 边陲福特主义是...        [1] Freestone, 2000.
② 威廉·布莱克(1757—       [2] Wohl, 1977, 234.
   1827), 英国诗人...      [3] 同上, 238.
```

## Requirements

```bash
pip install pypdfium2 Pillow pikepdf requests
```

## File structure

```
scripts/
  convert.py        Main tool (--profile + --convert)
  mineru_api.py     MinerU API client (split → upload → poll → download)
  analyze.py        Diagnostic analyzer (footnotes, chapters, format issues)
  format_fix.py     Post-processing fixes (paragraph merge, heading dedup)
SKILL.md            Agent workflow guide (for LLM-assisted conversion)
```
