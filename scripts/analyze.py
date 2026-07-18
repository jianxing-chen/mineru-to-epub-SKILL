#!/usr/bin/env python3
"""
Mineru Output Diagnostic Analyzer

Analyzes Mineru _content_list_v2.json output and produces a structured
diagnostic report with precise page/block/sample locations for every finding.

Usage:
  python3 analyze.py --input-dir /path/to/mineru_output
  python3 analyze.py --parts dir1 dir2 dir3
  python3 analyze.py --parts dir1 dir2 --config chapters.json > report.json
"""

import json, re, glob, sys, argparse
from pathlib import Path
from collections import Counter, defaultdict

# ── Constants ────────────────────────────────────────────────

CIRCLE_CHARS = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'
CIRCLE_TO_NUM = {c: str(i) for i, c in enumerate(CIRCLE_CHARS, 1)}

SENTENCE_END = set('。！？…"」』》）)')

CHAPTER_PATTERNS = [
    (re.compile(r'^第[一二三四五六七八九十百千]+章'), "chinese_chapter"),
    (re.compile(r'^第\d+章'), "arabic_chapter"),
    (re.compile(r'^第[一二三四五六七八九十百千]+[节篇]'), "section"),
    (re.compile(r'^第[一二三四五六七八九十]+[部编卷]'), "part_heading"),
    (re.compile(r'^前言|^序[言话]?|^导[言论读]|^致谢'), "front_matter"),
    (re.compile(r'^[后跋]记|^结[语论]|^尾声'), "back_matter"),
    (re.compile(r'^附录|^参考[文资]|^索[引]|^注[释释]'), "back_matter"),
    (re.compile(r'^Contents|^目录'), "toc"),
]

TOC_LINE_RE = re.compile(r'(.+?)\s+(\d{1,4})\s*$')


# ── Data Loading ─────────────────────────────────────────────

def load_part(part_dir, offset=0):
    """Load one Mineru part directory. Same logic as convert.py."""
    json_files = glob.glob(f"{part_dir}/*_content_list_v2.json")
    if not json_files:
        raise FileNotFoundError(f"No content_list_v2.json found in {part_dir}")
    with open(json_files[0], "r") as f:
        pages = json.load(f)
    return pages


def load_all_pages(part_dirs):
    """Load all parts, producing a unified page list with abs_page."""
    all_pages = []
    for part_dir in part_dirs:
        pages = load_part(part_dir, 0)
        offset = len(all_pages)
        for i, blocks in enumerate(pages):
            all_pages.append({"abs_page": offset + i + 1, "blocks": blocks, "part": part_dir})
    return all_pages


def block_text(b, ck=None):
    """Extract concatenated text from a block."""
    if ck is None:
        t = b.get("type", "")
        ck = f"{t}_content"
    items = b.get("content", {}).get(ck, [])
    return "".join(it.get("content", "") for it in items if isinstance(it, dict))


# ── 2a. Footnote Analysis ───────────────────────────────────

def analyze_footnotes(all_pages):
    """Analyze footnote references, bodies, cross-page matching."""
    report = {}

    # Collect all refs and bodies
    refs_by_type = defaultdict(list)
    footnote_bodies = []

    for pdata in all_pages:
        ap = pdata["abs_page"]
        for bi, b in enumerate(pdata["blocks"]):
            t = b.get("type", "")

            # --- Refs in paragraphs/titles ---
            if t in ("paragraph", "title"):
                ck = f"{t}_content"
                items = b.get("content", {}).get(ck, [])
                for ci, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    ct = item.get("content", "")
                    itype = item.get("type", "text")
                    circled = [c for c in ct if c in CIRCLE_CHARS]
                    if circled:
                        if itype == "equation_inline":
                            refs_by_type["equation_inline"].append({
                                "page": ap, "block": bi, "sub": ci,
                                "chars": circled, "context": ct[:80]
                            })
                        elif itype == "text":
                            refs_by_type["text_embedded"].append({
                                "page": ap, "block": bi, "sub": ci,
                                "chars": circled, "context": ct[:80]
                            })
                        else:
                            refs_by_type["other"].append({
                                "page": ap, "block": bi, "sub": ci,
                                "type": itype, "chars": circled, "context": ct[:80]
                            })

            # --- Footnote bodies ---
            if t == "page_footnote":
                text = block_text(b)
                m = re.match(r'([①②③④⑤⑥⑦⑧⑨⑩\d]+)', text)
                if m:
                    footnote_bodies.append({
                        "page": ap, "block": bi, "marker": m.group(1),
                        "text": text[:120], "has_marker": True
                    })
                else:
                    footnote_bodies.append({
                        "page": ap, "block": bi, "marker": None,
                        "text": text[:120], "has_marker": False
                    })

    # Summary stats
    report["refs"] = {
        "equation_inline": {"count": len(refs_by_type["equation_inline"]), "samples": refs_by_type["equation_inline"][:5]},
        "text_embedded": {"count": len(refs_by_type["text_embedded"]), "samples": refs_by_type["text_embedded"][:5]},
        "other": {"count": len(refs_by_type["other"]), "samples": refs_by_type["other"][:5]},
        "total": sum(len(v) for v in refs_by_type.values()),
    }

    # Recommendation
    eq_pct = len(refs_by_type["equation_inline"]) / max(report["refs"]["total"], 1) * 100
    te_pct = len(refs_by_type["text_embedded"]) / max(report["refs"]["total"], 1) * 100
    if eq_pct > 90:
        report["refs"]["strategy"] = "equation_inline_only"
    elif te_pct > 90:
        report["refs"]["strategy"] = "text_only"
    else:
        report["refs"]["strategy"] = "dual"

    # Bodies
    with_marker = [b for b in footnote_bodies if b["has_marker"]]
    without_marker = [b for b in footnote_bodies if not b["has_marker"]]

    # Detect split continuations (without_marker following with_marker on same page)
    continuations = []
    truly_unmatched = []
    pages_with_fns = defaultdict(list)
    for fb in footnote_bodies:
        pages_with_fns[fb["page"]].append(fb)

    for fb in without_marker:
        same_page = pages_with_fns.get(fb["page"], [])
        prev_has_marker = any(
            f["has_marker"] and f["block"] < fb["block"]
            for f in same_page
        )
        if prev_has_marker:
            continuations.append(fb)
        else:
            truly_unmatched.append(fb)

    report["bodies"] = {
        "total": len(footnote_bodies),
        "with_marker": len(with_marker),
        "without_marker": len(without_marker),
        "split_continuation": len(continuations),
        "truly_unmatched": len(truly_unmatched),
        "samples_with_marker": with_marker[:5],
        "samples_continuation": continuations[:3],
        "samples_unmatched": truly_unmatched[:3],
    }

    # Cross-page analysis: simulate matching
    # Build refs in convert.py format
    all_refs = []
    all_contents = []
    refs_by_page = defaultdict(list)
    contents_by_page = defaultdict(list)

    for pdata in all_pages:
        ap = pdata["abs_page"]
        for bi, b in enumerate(pdata["blocks"]):
            t = b.get("type", "")
            if t in ("paragraph", "title"):
                ck = f"{t}_content"
                items = b.get("content", {}).get(ck, [])
                # Type A: equation_inline
                for ci, item in enumerate(items):
                    if isinstance(item, dict) and item.get("type") == "equation_inline":
                        for n in re.findall(r'[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]+', item.get("content", "")):
                            norm = CIRCLE_TO_NUM.get(n, n)
                            all_refs.append((ap, bi, ci, n, norm))
                            refs_by_page[ap].append(len(all_refs) - 1)
                # Type B: text-embedded (simplified)
                all_text = "".join(it.get("content", "") for it in items if isinstance(it, dict))
                if any(r[0] == ap and r[1] == bi for r in all_refs):
                    continue
                for m in re.finditer(r'[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]+', all_text):
                    n = m.group(0)
                    pos = m.start()
                    before = all_text[max(0, pos-5):pos]
                    after = all_text[pos+1:pos+6]
                    if '·' in before or '·' in after or re.search(r'\d$', before):
                        continue
                    norm = CIRCLE_TO_NUM.get(n, n)
                    all_refs.append((ap, bi, -1, n, norm))
                    refs_by_page[ap].append(len(all_refs) - 1)
            # Content blocks
            if t == "page_footnote":
                text = block_text(b)
                m = re.match(r'([①②③④⑤⑥⑦⑧⑨⑩\d]+)', text)
                if m:
                    raw = m.group(1)
                    norm = CIRCLE_TO_NUM.get(raw, raw)
                    all_contents.append((ap, raw, norm, text.strip()))
                    contents_by_page[ap].append(len(all_contents) - 1)

    # Match
    content_used = set()
    cross_page = defaultdict(list)
    unmatched_refs = []
    matched_count = 0

    for ri, (rp, _, _, raw_n, norm_n) in enumerate(all_refs):
        best = None
        best_dp = None
        for dp in [0, -1, 1, -2, 2]:
            for ci in contents_by_page.get(rp + dp, []):
                if ci in content_used:
                    continue
                if all_contents[ci][2] == norm_n:
                    best = ci
                    best_dp = dp
                    break
            if best is not None:
                break
        if best is not None:
            content_used.add(best)
            matched_count += 1
            cp = all_contents[best][0]
            offset = cp - rp
            ctx = ""
            for p in all_pages:
                if p["abs_page"] == rp:
                    for b in p["blocks"]:
                        ctx += block_text(b)
            pos = ctx.find(raw_n)
            snippet = ctx[max(0,pos-20):pos+len(raw_n)+20]
            cross_page[str(offset)].append({
                "ref_page": rp, "body_page": cp,
                "raw": raw_n, "body_text": all_contents[best][3][:100],
                "context": snippet[:80]
            })
        else:
            unmatched_refs.append({
                "page": rp, "raw": raw_n,
                "norm": norm_n,
            })

    report["cross_page"] = {
        "matched": matched_count,
        "unmatched_refs": len(unmatched_refs),
        "unmatched_bodies": len(all_contents) - len(content_used),
        "by_offset": {k: len(v) for k, v in cross_page.items()},
        "samples_cross": {k: v[:3] for k, v in cross_page.items() if k != "0"},
    }
    if unmatched_refs:
        report["cross_page"]["unmatched_samples"] = unmatched_refs[:10]

    return report


# ── 2b. Chapter / TOC Analysis ──────────────────────────────

def analyze_chapters(all_pages):
    """Analyze title blocks, detect missing part headings, extract TOC."""
    report = {}

    # Title inventory
    titles = []
    title_texts = Counter()
    for pdata in all_pages:
        ap = pdata["abs_page"]
        for bi, b in enumerate(pdata["blocks"]):
            if b.get("type") == "title":
                text = block_text(b).strip()
                if text:
                    titles.append({
                        "page": ap, "block": bi, "text": text,
                        "len": len(text),
                        "level": b.get("content", {}).get("level", 1)
                    })
                    title_texts[text] += 1

    report["titles"] = {
        "total": len(titles),
        "all": titles,
    }

    # Page header/footer candidates (appear on ≥ 3 pages)
    header_candidates = [
        {"text": t, "pages_count": c}
        for t, c in title_texts.most_common(20) if c >= 3
    ]
    report["header_footer_candidates"] = header_candidates

    # Chapter boundary detection
    classified = defaultdict(list)
    unclassified = []
    for t in titles:
        matched = False
        for pattern, cat in CHAPTER_PATTERNS:
            if pattern.match(t["text"]):
                classified[cat].append(t)
                matched = True
                break
        if not matched:
            unclassified.append(t)

    report["chapter_boundaries"] = {k: v for k, v in classified.items()}
    report["chapter_boundaries"]["unclassified"] = unclassified

    # Part heading detection (direct text search for 第X部分)
    part_headings = []
    for pdata in all_pages:
        ap = pdata["abs_page"]
        for bi, b in enumerate(pdata["blocks"]):
            text = block_text(b)
            m = re.search(r'第[一二三四五六七八九十]+部分', text)
            if m:
                part_headings.append({"page": ap, "block": bi, "type": b.get("type","?"), "text": text[:120]})

    report["part_headings_found"] = part_headings

    # TOC discovery: search all pages for TOC-like text
    toc_entries = []
    toc_pages = []
    for pdata in all_pages:
        ap = pdata["abs_page"]
        lines_with_numbers = []
        for b in pdata["blocks"]:
            text = block_text(b)
            for line in text.split('\n'):
                m = TOC_LINE_RE.match(line.strip())
                if m:
                    lines_with_numbers.append({"name": m.group(1).strip(), "page_ref": int(m.group(2))})
        if len(lines_with_numbers) >= 5:
            toc_pages.append(ap)
            toc_entries.extend(lines_with_numbers)

    report["toc"] = {
        "found": len(toc_pages) > 0,
        "pages": toc_pages,
        "entries": toc_entries,
        "entry_count": len(toc_entries),
    }

    # Missing part pages: pages with 0 or very few blocks between chapters
    empty_pages = []
    for pdata in all_pages:
        ap = pdata["abs_page"]
        n = len(pdata["blocks"])
        if n <= 1:
            # Check if it has only paragraph with non-content
            has_content = False
            for b in pdata["blocks"]:
                if b.get("type") in ("paragraph", "title") and block_text(b).strip():
                    has_content = True
            if not has_content:
                empty_pages.append(ap)

    report["empty_or_near_empty_pages"] = empty_pages

    return report


# ── 2c. Format Issues ────────────────────────────────────────

def analyze_format(all_pages):
    """Detect broken paragraphs, duplicate headings, image stats."""
    report = {}

    # Broken paragraphs
    broken = []
    all_paras = []
    for pdata in all_pages:
        ap = pdata["abs_page"]
        for bi, b in enumerate(pdata["blocks"]):
            if b.get("type") == "paragraph":
                text = block_text(b).strip()
                if text:
                    last_char = text[-1]
                    all_paras.append((ap, bi, text))
                    if last_char not in SENTENCE_END:
                        broken.append({"page": ap, "block": bi, "ending": text[-50:], "len": len(text)})

    report["broken_paragraphs"] = {
        "total_paragraphs": len(all_paras),
        "broken_count": len(broken),
        "rate": f"{len(broken)}/{len(all_paras)} = {100*len(broken)/max(len(all_paras),1):.1f}%",
        "samples": broken[:20],
        "all": broken,
        "note": "段落不以句末标点结尾（。！？…\"」』》）)",
    }

    # Duplicate/overlapping titles
    duplicates = []
    # Group titles by page for containment check
    page_titles = defaultdict(list)
    for pdata in all_pages:
        ap = pdata["abs_page"]
        for bi, b in enumerate(pdata["blocks"]):
            if b.get("type") == "title":
                text = block_text(b).strip()
                if text:
                    page_titles[ap].append({"page": ap, "block": bi, "text": text})

    # Check containment: is one title on a page a substring of another on the same page?
    for ap, pts in page_titles.items():
        texts = [p["text"] for p in pts]
        for i, p1 in enumerate(pts):
            for j, p2 in enumerate(pts):
                if i != j and p2["text"] in p1["text"] and p2["text"] != p1["text"] and len(p2["text"]) >= 2:
                    duplicates.append({
                        "page": ap,
                        "parent": p1["text"][:80],
                        "child": p2["text"][:80],
                        "parent_block": p1["block"],
                        "child_block": p2["block"],
                    })

    # Check exact duplicates across pages
    all_titles = []
    for pdata in all_pages:
        for b in pdata["blocks"]:
            if b.get("type") == "title":
                text = block_text(b).strip()
                if text:
                    all_titles.append((pdata["abs_page"], text))
    title_counts = Counter(t for _, t in all_titles)
    exact_dups = [
        {"text": t, "pages": sorted(set(p for p, tt in all_titles if tt == t)), "count": c}
        for t, c in title_counts.most_common() if c >= 2 and len(t) >= 3
    ]

    report["duplicate_headings"] = {
        "containment": duplicates,
        "containment_count": len(duplicates),
        "exact_duplicates": exact_dups,
    }

    # Image stats
    img_blocks = []
    for pdata in all_pages:
        ap = pdata["abs_page"]
        for bi, b in enumerate(pdata["blocks"]):
            if b.get("type") == "image":
                bbox = b.get("bbox", [0, 0, 0, 0])
                w = bbox[2] - bbox[0] if len(bbox) >= 4 else 0
                h = bbox[3] - bbox[1] if len(bbox) >= 4 else 0
                area = w * h
                img_blocks.append({"page": ap, "block": bi, "width": w, "height": h, "area": area})

    report["images"] = {
        "total": len(img_blocks),
        "pages_with_images": len(set(b["page"] for b in img_blocks)),
        "all": img_blocks,
    }

    # Page header/footer text in paragraph blocks (top/bottom 10% of page)
    header_texts = Counter()
    footer_texts = Counter()
    for pdata in all_pages:
        ap = pdata["abs_page"]
        for b in pdata["blocks"]:
            if b.get("type") in ("paragraph", "page_header", "page_footer"):
                bbox = b.get("bbox", [0, 0, 0, 0])
                if len(bbox) < 4:
                    continue
                y_start = bbox[1]
                y_end = bbox[3]
                text = block_text(b).strip()
                if not text or len(text) > 100:
                    continue
                # Very rough: top 15% is header, bottom 15% is footer
                page_height = 1000  # approximate for this book
                if y_start < 150:
                    header_texts[text] += 1
                elif y_end > 800:
                    footer_texts[text] += 1

    report["header_footer_filter_candidates"] = {
        "headers": [{"text": t, "count": c} for t, c in header_texts.most_common() if c >= 5],
        "footers": [{"text": t, "count": c} for t, c in footer_texts.most_common() if c >= 5],
    }

    return report


# ── 2d. Recommendations ──────────────────────────────────────

def generate_recommendations(fn_report, ch_report, fmt_report, all_pages):
    """Generate actionable recommendations from diagnostics."""
    recs = {}

    # Footnote
    recs["footnote_strategy"] = fn_report["refs"].get("strategy", "dual")
    recs["footnote_cross_page_count"] = fn_report["cross_page"]["matched"]

    # Chapter config suggestion
    chapters = ch_report["chapter_boundaries"]
    front = [t["text"] for t in chapters.get("front_matter", [])]
    chin_ch = chapters.get("chinese_chapter", [])
    back = [t["text"] for t in chapters.get("back_matter", [])]

    suggested_config = []
    # Front matter
    for t in chapters.get("front_matter", []):
        suggested_config.append({"name": t["text"], "page": t["page"]})
    # Chinese chapters
    for t in chin_ch:
        suggested_config.append({"name": t["text"], "page": t["page"]})
    # Back matter
    for t in chapters.get("back_matter", []):
        suggested_config.append({"name": t["text"], "page": t["page"]})

    # Part headings (from empty pages)
    empty_pages = ch_report.get("empty_or_near_empty_pages", [])
    recs["chapter_config"] = suggested_config
    recs["missing_part_pages"] = empty_pages

    # Skip pages
    toc_pages = ch_report["toc"].get("pages", [])
    cip_pages = [p["page"] for p in ch_report["titles"]["all"] if "CIP" in p["text"] or "图书在版编目" in p["text"]]
    recs["skip_pages"] = sorted(set(toc_pages + cip_pages))

    # Filter rules
    headers = fmt_report["header_footer_filter_candidates"]["headers"]
    recs["filter_header_text"] = [h["text"] for h in headers if h["count"] > 20]

    # Quality issues summary
    recs["quality_summary"] = {
        "broken_paragraphs": fmt_report["broken_paragraphs"]["broken_count"],
        "duplicate_headings": fmt_report["duplicate_headings"]["containment_count"],
        "empty_pages": len(empty_pages),
        "total_images": fmt_report["images"]["total"],
    }

    return recs


# ── Report Builder ───────────────────────────────────────────

def build_report(part_dirs):
    """Run all analyzers and produce the full diagnostic report."""
    all_pages = load_all_pages(part_dirs)
    print(f"Loaded {len(all_pages)} pages from {len(part_dirs)} part(s)\n", file=sys.stderr)

    report = {
        "metadata": {
            "total_pages": len(all_pages),
            "parts": len(part_dirs),
            "part_dirs": part_dirs,
        },
        "footnotes": analyze_footnotes(all_pages),
        "chapters": analyze_chapters(all_pages),
        "format_issues": analyze_format(all_pages),
    }

    report["recommendations"] = generate_recommendations(
        report["footnotes"], report["chapters"], report["format_issues"], all_pages
    )

    return report


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Mineru Output Diagnostic Analyzer")
    parser.add_argument("--input-dir", help="Single Mineru output directory")
    parser.add_argument("--parts", nargs="+", help="Multiple Mineru output directories")
    parser.add_argument("--config", help="Existing chapter config to compare against")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    args = parser.parse_args()

    if args.input_dir:
        part_dirs = [args.input_dir]
    elif args.parts:
        part_dirs = args.parts
    else:
        print("ERROR: --input-dir or --parts required", file=sys.stderr)
        sys.exit(1)

    report = build_report(part_dirs)

    # If config provided, add comparison
    if args.config:
        with open(args.config) as f:
            existing_config = json.load(f)
        report["config_comparison"] = {
            "provided_chapters": len(existing_config.get("chapters", [])),
            "provided_skip_pages": existing_config.get("skip_pages", []),
        }

    indent = 2 if args.pretty else None
    print(json.dumps(report, indent=indent, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
