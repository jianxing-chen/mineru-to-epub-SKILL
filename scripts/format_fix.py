#!/usr/bin/env python3
"""
EPUB Post-Processing Fixes

Fixes common formatting issues in EPUBs generated from Mineru output.
All fixes default to dry-run mode: preview changes, user confirms, then apply.

Usage:
  # Dry-run (preview only, no changes)
  python3 format_fix.py check input.epub

  # Apply fixes (overwrites input)
  python3 format_fix.py fix input.epub --output output.epub

  # Apply specific fixes
  python3 format_fix.py fix input.epub --merge-paragraphs --dedup-headings
"""

import zipfile, re, shutil, os, sys, argparse
from pathlib import Path
from html import unescape, escape


# ── EPUB I/O ─────────────────────────────────────────────────

def read_epub_html(epub_path):
    """Read content.xhtml from an EPUB."""
    with zipfile.ZipFile(epub_path, 'r') as zf:
        return zf.read('OEBPS/content.xhtml').decode('utf-8')


def write_epub_html(epub_path, html_content, output_path=None):
    """Write modified content.xhtml back into an EPUB copy."""
    src = epub_path
    dst = output_path or epub_path
    if dst == src:
        # Work on a copy
        tmp = str(Path(dst).with_suffix('.tmp.epub'))
        shutil.copy(src, tmp)
        src = tmp
        swap = True
    else:
        shutil.copy(src, dst)
        swap = False

    # Replace content.xhtml inside the zip
    # Python's zipfile can't modify in-place, so rebuild
    tmp_zip = str(Path(dst).with_suffix('.rebuild.epub'))
    with zipfile.ZipFile(src, 'r') as zin:
        with zipfile.ZipFile(tmp_zip, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == 'OEBPS/content.xhtml':
                    data = html_content.encode('utf-8')
                zout.writestr(item, data)

    os.replace(tmp_zip, dst)
    if swap:
        os.remove(src)


# ── Helpers ──────────────────────────────────────────────────

def strip_tags(s):
    return unescape(re.sub(r'<[^>]+>', '', s)).strip()


def should_end_sentence(text):
    """Check if text ends with sentence-ending punctuation."""
    clean = re.sub(r'<sup>.*?</sup>', '', text)
    clean = clean.rstrip()
    if not clean:
        return False
    return clean[-1] in '。！？…"」』》）)'


def is_epigraph_or_attribution(text):
    """Check if this is an epigraph or attribution line."""
    t = strip_tags(text)
    return t.startswith('——') or len(t) < 10


# ── Fix: Merge Broken Paragraphs ─────────────────────────────

def merge_broken_paragraphs(html, dry_run=True):
    """
    Merge consecutive <p> tags where the first doesn't end with
    sentence-ending punctuation and no block elements separate them.
    """
    pattern = re.compile(r'<p>(.*?)</p>', re.DOTALL)
    matches = list(pattern.finditer(html))

    if not matches:
        return html, []

    changes = []
    result = []
    last_end = 0
    i = 0

    while i < len(matches):
        m = matches[i]
        p1_text = m.group(1)

        # Add content before this match
        result.append(html[last_end:m.start()])

        # Try to merge with following <p> tags
        merged_text = p1_text
        j = i
        merge_indices = []

        while j + 1 < len(matches):
            between = html[matches[j].end():matches[j+1].start()]
            if between.strip():
                break

            if should_end_sentence(merged_text):
                break

            # Don't merge epigraphs with body text
            next_text = matches[j+1].group(1)
            if is_epigraph_or_attribution(merged_text) and not is_epigraph_or_attribution(next_text):
                break

            merged_text += next_text
            j += 1
            merge_indices.append(j)

        if merge_indices:
            changes.append({
                "type": "merge_paragraphs",
                "original_count": len(merge_indices) + 1,
                "merged_to": 1,
                "preview": strip_tags(merged_text)[:120] + "..."
            })

        result.append(f'<p>{merged_text}</p>')
        i = j + 1
        last_end = matches[j].end()

    result.append(html[last_end:])
    fixed = ''.join(result)
    return fixed, changes


# ── Fix: Remove Duplicate Headings ───────────────────────────

def remove_duplicate_headings(html, dry_run=True):
    """
    Remove <h2>/<h3> elements whose text is contained in or equals
    the most recent <h1> chapter heading.
    """
    heading_pattern = re.compile(
        r'<(h[123])\s*(?:id="([^"]*)")?\s*>(.*?)</\1>',
        re.DOTALL
    )

    matches = list(heading_pattern.finditer(html))
    to_remove = []
    changes = []
    current_h1_text = ""

    for m in matches:
        tag = m.group(1)
        text = strip_tags(m.group(3))

        if tag == 'h1':
            current_h1_text = text
            continue

        if not current_h1_text:
            continue

        # Normalize for comparison
        h1_norm = current_h1_text.replace(' ', '').replace('|', '')
        sub_norm = text.replace(' ', '')

        is_dup = False
        if h1_norm == sub_norm:
            is_dup = True
        elif len(sub_norm) >= 2 and sub_norm in h1_norm:
            is_dup = True
        elif len(h1_norm) >= 2 and h1_norm in sub_norm:
            is_dup = True

        if is_dup:
            end = m.end()
            while end < len(html) and html[end] in ' \t\n\r':
                end += 1
            to_remove.append((m.start(), end))
            changes.append({
                "type": "remove_duplicate_heading",
                "tag": tag,
                "text": text[:80],
                "chapter": current_h1_text[:80],
            })

    # Remove (working backwards)
    fixed = html
    for start, end in reversed(to_remove):
        fixed = fixed[:start] + fixed[end:]

    return fixed, changes


# ── Fix: Double Punctuation ──────────────────────────────────

def fix_double_punctuation(html, dry_run=True):
    """Fix repeated punctuation marks (。。→。 ,,→， etc.)."""
    replacements = [
        ('。。', '。'),
        ('，，', '，'),
        ('、、', '、'),
    ]
    changes = []
    fixed = html
    for old, new in replacements:
        count = fixed.count(old)
        if count > 0:
            fixed = fixed.replace(old, new)
            changes.append({"type": "fix_double_punct", "pattern": old, "replacement": new, "count": count})

    return fixed, changes


# ── Orchestrator ─────────────────────────────────────────────

def check_epub(epub_path):
    """Dry-run: analyze and report all fixable issues without modifying."""
    html = read_epub_html(epub_path)

    print(f"📋 检查 EPUB: {epub_path}\n")

    all_changes = []

    # Merge paragraphs
    _, para_changes = merge_broken_paragraphs(html, dry_run=True)
    if para_changes:
        print(f"📌 断句合并: {len(para_changes)} 处")
        for c in para_changes[:5]:
            print(f"   → {c['original_count']}段→1段: {c['preview']}")
        if len(para_changes) > 5:
            print(f"   ... 还有 {len(para_changes) - 5} 处")
        all_changes.extend(para_changes)
    else:
        print("✅ 断句合并: 无需修复")

    # Dedup headings
    _, hd_changes = remove_duplicate_headings(html, dry_run=True)
    if hd_changes:
        print(f"\n📌 标题去重: {len(hd_changes)} 处")
        for c in hd_changes:
            print(f"   → 删除 <{c['tag']}>{c['text']}</{c['tag']}> (章: {c['chapter']})")
        all_changes.extend(hd_changes)
    else:
        print("\n✅ 标题去重: 无需修复")

    # Double punctuation
    _, dp_changes = fix_double_punctuation(html, dry_run=True)
    if dp_changes:
        print(f"\n📌 重复标点: {sum(c['count'] for c in dp_changes)} 处")
        for c in dp_changes:
            print(f"   → '{c['pattern']}'→'{c['replacement']}': {c['count']} 次")
        all_changes.extend(dp_changes)
    else:
        print("\n✅ 重复标点: 无需修复")

    total = len(all_changes)
    print(f"\n{'='*50}")
    print(f"总计: {total} 处可修复问题")
    if total > 0:
        print(f"执行修复: python3 format_fix.py fix {epub_path} --output fixed.epub")


def fix_epub(epub_path, output_path, merge_paras=True, dedup_headings=True, fix_punct=True):
    """Apply fixes to EPUB."""
    html = read_epub_html(epub_path)
    all_changes = []

    if merge_paras:
        html, changes = merge_broken_paragraphs(html, dry_run=False)
        all_changes.extend(changes)
        print(f"✅ 合并断句: {len(changes)} 处")

    if dedup_headings:
        html, changes = remove_duplicate_headings(html, dry_run=False)
        all_changes.extend(changes)
        print(f"✅ 标题去重: {len(changes)} 处")

    if fix_punct:
        html, changes = fix_double_punctuation(html, dry_run=False)
        all_changes.extend(changes)
        print(f"✅ 重复标点: {sum(c['count'] for c in changes)} 处")

    write_epub_html(epub_path, html, output_path)
    print(f"\n📦 输出: {output_path}")
    print(f"   共修复 {len(all_changes)} 处问题")


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="EPUB Post-Processing Fixes")
    sub = parser.add_subparsers(dest="command", required=True)

    # check (dry-run)
    p_check = sub.add_parser("check", help="Dry-run: preview fixable issues")
    p_check.add_argument("epub", help="Path to EPUB file")

    # fix
    p_fix = sub.add_parser("fix", help="Apply fixes")
    p_fix.add_argument("epub", help="Path to input EPUB")
    p_fix.add_argument("--output", "-o", required=True, help="Output EPUB path")
    p_fix.add_argument("--no-merge-paragraphs", action="store_true", help="Skip paragraph merging")
    p_fix.add_argument("--no-dedup-headings", action="store_true", help="Skip heading dedup")
    p_fix.add_argument("--no-fix-punctuation", action="store_true", help="Skip punctuation fix")

    args = parser.parse_args()

    if args.command == "check":
        check_epub(args.epub)

    elif args.command == "fix":
        fix_epub(
            args.epub,
            args.output,
            merge_paras=not args.no_merge_paragraphs,
            dedup_headings=not args.no_dedup_headings,
            fix_punct=not args.no_fix_punctuation,
        )


if __name__ == "__main__":
    main()
