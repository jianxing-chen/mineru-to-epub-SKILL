#!/usr/bin/env python3
"""
MinerU Precision Parse API Client (v4)

Workflow:
  PDF → split (if >200pp) → upload → auto-submit → poll → download ZIP → extract

Usage:
  python3 mineru_api.py process /path/to/book.pdf --api-key sk-xxx --output ./output/
  python3 mineru_api.py process /path/to/book.pdf --api-key sk-xxx --max-pages 200
  python3 mineru_api.py batch part1.pdf part2.pdf --api-key sk-xxx --output ./output/
"""

import os, sys, time, json, random, shutil, zipfile, argparse
from pathlib import Path
from urllib.parse import urlparse

import requests

# ── Constants ────────────────────────────────────────────────

BASE_URL = "https://mineru.net/api/v4"
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB
MAX_PAGES_PER_FILE = 200
DEFAULT_POLL_TIMEOUT = 900  # 15 minutes per task
MAX_BATCH_FILES = 50

# ── API Key Resolution ───────────────────────────────────────

def resolve_api_key(cli_key=None):
    """
    Resolve API key in priority order:
      1. CLI argument (--api-key)
      2. Environment variable MINERU_API_KEY
      3. File ~/.mineru_api_key (first line, whitespace trimmed)
    """
    if cli_key:
        return cli_key

    env_key = os.environ.get("MINERU_API_KEY")
    if env_key:
        return env_key

    key_file = Path.home() / ".mineru_api_key"
    if key_file.exists():
        key = key_file.read_text().strip().split('\n')[0].strip()
        if key:
            return key

    raise RuntimeError(
        "No API key found. Provide it via:\n"
        "  1. --api-key argument\n"
        "  2. Environment variable: export MINERU_API_KEY=sk-xxx\n"
        "  3. File: echo 'sk-xxx' > ~/.mineru_api_key"
    )

# ── Helpers ──────────────────────────────────────────────────

def _api_request(method, path, api_key, **kwargs):
    """Make an API request with Bearer auth. Returns parsed JSON."""
    url = f"{BASE_URL}{path}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {api_key}"
    if "json" in kwargs:
        headers.setdefault("Content-Type", "application/json")

    resp = requests.request(method, url, headers=headers, timeout=kwargs.pop("timeout", 60), **kwargs)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"API error {body['code']}: {body.get('msg', 'unknown')}")
    return body


def _exponential_backoff(attempt, base=2.0, cap=60.0):
    """Return sleep seconds for given attempt number (0-indexed)."""
    delay = base * (2 ** attempt)
    delay = min(delay, cap)
    jitter = random.uniform(0.5, 1.5)
    return delay * jitter


def _count_pdf_pages(file_path):
    """Count pages in a PDF using pikepdf (fast, no rendering)."""
    try:
        import pikepdf
        with pikepdf.open(file_path) as pdf:
            return len(pdf.pages)
    except ImportError:
        pass
    try:
        import pypdf
        with pypdf.PdfReader(file_path) as r:
            return len(r.pages)
    except ImportError:
        pass
    raise ImportError("Need pikepdf or pypdf to count PDF pages. pip install pikepdf")


# ── PDF Splitting ────────────────────────────────────────────

def split_pdf(file_path, max_pages=MAX_PAGES_PER_FILE, output_dir=None):
    """
    Split a PDF into chunks each ≤ max_pages.

    Args:
        file_path: Path to the PDF.
        max_pages: Maximum pages per chunk (default 200, MinerU limit).
        output_dir: Directory for chunks. Default: same dir as input.

    Returns:
        List of Path objects for the chunk files.
    """
    src = Path(file_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"PDF not found: {src}")

    import pikepdf
    doc = pikepdf.open(src)
    total = len(doc.pages)

    if total <= max_pages:
        doc.close()
        return [src]

    out_dir = Path(output_dir) if output_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = src.stem
    chunks = []
    start = 0
    part = 1
    while start < total:
        end = min(start + max_pages, total)
        chunk = pikepdf.new()
        for i in range(start, end):
            chunk.pages.append(doc.pages[i])

        fname = f"{stem}_part{part:02d}.pdf"
        fpath = out_dir / fname
        chunk.save(fpath)
        chunk.close()
        chunks.append(fpath)
        print(f"  Split: {fname} (pages {start+1}-{end}, {end-start} pp)")
        start = end
        part += 1

    doc.close()
    return chunks


# ── Upload ───────────────────────────────────────────────────

def upload_pdf(file_path, api_key):
    """
    Upload a PDF to MinerU via signed URL.

    Steps:
      1. POST /api/v4/file-urls/batch  → get signed upload URL
      2. PUT file bytes to signed URL   → auto-submits the task

    Returns:
        (file_name, batch_id) — needed to find task_id later.
    """
    src = Path(file_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"File not found: {src}")

    fsize = src.stat().st_size
    if fsize > MAX_FILE_SIZE:
        raise ValueError(f"File {src.name} is {fsize/1024/1024:.1f}MB (limit: 200MB)")

    # Step 1: request signed upload URL
    print(f"  Requesting upload URL for {src.name} ({fsize/1024/1024:.1f} MB) ...")
    body = _api_request(
        "POST", "/file-urls/batch",
        api_key=api_key,
        json={"files": [{"name": src.name, "is_ocr": False}]},
        timeout=30,
    )

    batch_id = body["data"]["batch_id"]
    upload_url = body["data"]["file_urls"][0]

    # Step 2: PUT file to signed URL (no Content-Type header per MinerU docs)
    print(f"  Uploading to OSS ...")
    with open(src, "rb") as f:
        put_resp = requests.put(upload_url, data=f, timeout=600)
    put_resp.raise_for_status()

    print(f"  Upload complete. batch_id={batch_id}")
    return src.name, batch_id


# ── Polling ──────────────────────────────────────────────────

def poll_task(task_id, api_key, timeout=DEFAULT_POLL_TIMEOUT):
    """
    Poll GET /api/v4/extract/task/{task_id} until 'done' or 'failed'.

    Returns:
        full_zip_url (str) when done.
    """
    start = time.monotonic()
    attempt = 0

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")

        body = _api_request(
            "GET", f"/extract/task/{task_id}",
            api_key=api_key, timeout=30,
        )
        state = body["data"]["state"]

        if state == "done":
            return body["data"]["full_zip_url"]
        elif state == "failed":
            err = body["data"].get("err_msg", "unknown error")
            raise RuntimeError(f"Task {task_id} failed: {err}")
        else:
            progress = body["data"].get("extract_progress", {})
            done_pg = progress.get("extracted_pages", "?")
            total_pg = progress.get("total_pages", "?")
            print(f"  [{state}] {done_pg}/{total_pg} pages (elapsed {elapsed:.0f}s)")

        time.sleep(_exponential_backoff(attempt))
        attempt += 1


def poll_batch(batch_id, api_key, timeout=DEFAULT_POLL_TIMEOUT * 3):
    """
    Poll GET /api/v4/extract-results/batch/{batch_id} until all tasks done.

    Returns:
        List of {filename, task_id, state, full_zip_url}.
    """
    start = time.monotonic()
    attempt = 0

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            raise TimeoutError(f"Batch {batch_id} did not complete within {timeout}s")

        body = _api_request(
            "GET", f"/extract-results/batch/{batch_id}",
            api_key=api_key, timeout=30,
        )

        results = body["data"].get("extract_results", [])
        states = [r["state"] for r in results]
        done = sum(1 for s in states if s == "done")
        failed = sum(1 for s in states if s == "failed")
        total = len(results)

        print(f"  Batch {batch_id}: {done}/{total} done, {failed} failed (elapsed {elapsed:.0f}s)")

        if failed > 0:
            failed_names = [r["file_name"] for r in results if r["state"] == "failed"]
            raise RuntimeError(f"Batch has {failed} failed tasks: {failed_names}")

        if done == total:
            return results

        time.sleep(_exponential_backoff(attempt))
        attempt += 1


def get_task_id_from_batch(batch_id, filename, api_key, timeout=120):
    """Wait for a batch upload to auto-submit, then extract task_id for a specific file."""
    start = time.monotonic()
    attempt = 0

    while True:
        if time.monotonic() - start > timeout:
            raise TimeoutError(f"Could not get task_id for {filename} from batch {batch_id}")

        body = _api_request(
            "GET", f"/extract-results/batch/{batch_id}",
            api_key=api_key, timeout=30,
        )

        results = body["data"].get("extract_results", [])
        for r in results:
            if r.get("file_name") == filename and r.get("task_id"):
                return r["task_id"]

        time.sleep(_exponential_backoff(attempt, base=1.0, cap=30))
        attempt += 1


# ── Download ─────────────────────────────────────────────────

def download_zip(zip_url, output_dir):
    """
    Download a result ZIP from MinerU CDN and extract it.

    Returns:
        Path to the extracted directory.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"  Downloading results ...")
    resp = requests.get(zip_url, stream=True, timeout=300)
    resp.raise_for_status()

    # Stream to temp file
    tmp_path = out / "_tmp_result.zip"
    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    # Extract
    print(f"  Extracting to {out} ...")
    with zipfile.ZipFile(tmp_path, "r") as zf:
        zf.extractall(out)

    tmp_path.unlink()
    return out


# ── Orchestrator ─────────────────────────────────────────────

def process_pdf(file_path, api_key, output_dir=None, max_pages=MAX_PAGES_PER_FILE):
    """
    End-to-end: validate → split (if needed) → upload → poll → download.

    Args:
        file_path: Path to the PDF.
        api_key: MinerU API key (Bearer token).
        output_dir: Where to save results. Default: {pdf_name}_mineru/
        max_pages: Max pages per chunk (default 200).

    Returns:
        List of output directory paths (one per chunk).
    """
    src = Path(file_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"PDF not found: {src}")

    # Split if needed
    chunks = split_pdf(src, max_pages=max_pages)

    if output_dir is None:
        output_dir = f"{src.stem}_mineru"
    base_out = Path(output_dir)

    if len(chunks) == 1:
        # Single file: simpler flow
        print(f"\n[1/3] Uploading {src.name} ...")
        fname, batch_id = upload_pdf(str(chunks[0]), api_key)

        print(f"\n[2/3] Waiting for task to auto-submit ...")
        task_id = get_task_id_from_batch(batch_id, fname, api_key)
        print(f"  task_id = {task_id}")

        print(f"\n[3/3] Polling until done ...")
        zip_url = poll_task(task_id, api_key)

        print(f"\n[4/4] Downloading results ...")
        result_dir = download_zip(zip_url, base_out)
        print(f"\n✅ Results: {result_dir}")
        return [str(result_dir)]
    else:
        # Multiple chunks: batch upload
        print(f"\n📦 Splitting {src.name} ({_count_pdf_pages(str(src))} pp) → {len(chunks)} chunks")

        # Upload all
        fnames_and_bids = []
        for i, chunk in enumerate(chunks):
            print(f"\n  Uploading chunk {i+1}/{len(chunks)}: {chunk.name}")
            fname, bid = upload_pdf(str(chunk), api_key)
            fnames_and_bids.append((fname, bid))

        # Poll each
        results = []
        for i, (fname, bid) in enumerate(fnames_and_bids):
            out_sub = base_out / f"part{i+1:02d}"
            print(f"\n  Processing chunk {i+1}/{len(chunks)}: {fname}")
            task_id = get_task_id_from_batch(bid, fname, api_key)
            print(f"  task_id = {task_id}")
            zip_url = poll_task(task_id, api_key)
            result_dir = download_zip(zip_url, out_sub)
            results.append(str(result_dir))
            print(f"  ✅ {result_dir}")

        print(f"\n✅ All {len(results)} parts complete in {base_out}")
        return results


def batch_process(pdf_paths, api_key, output_dir=None, max_pages=MAX_PAGES_PER_FILE):
    """
    Process multiple PDFs.

    Args:
        pdf_paths: List of PDF file paths.
        api_key: MinerU API key.
        output_dir: Base output directory.

    Returns:
        List of output directory paths.
    """
    results = []
    for i, pdf_path in enumerate(pdf_paths):
        sub_dir = Path(output_dir) / f"doc{i+1:02d}" if output_dir else None
        results.extend(process_pdf(pdf_path, api_key, output_dir=str(sub_dir) if sub_dir else None, max_pages=max_pages))
    return results


# ── CLI ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MinerU Precision Parse API Client")
    sub = parser.add_subparsers(dest="command", required=True)

    # process
    p_proc = sub.add_parser("process", help="Process a single PDF")
    p_proc.add_argument("pdf", help="Path to PDF file")
    p_proc.add_argument("--api-key", default=None, help="MinerU API key (or set MINERU_API_KEY env / ~/.mineru_api_key)")
    p_proc.add_argument("--output", "-o", help="Output directory")
    p_proc.add_argument("--max-pages", type=int, default=MAX_PAGES_PER_FILE, help="Max pages per chunk")

    # batch
    p_batch = sub.add_parser("batch", help="Process multiple PDFs")
    p_batch.add_argument("pdfs", nargs="+", help="PDF file paths")
    p_batch.add_argument("--api-key", default=None, help="MinerU API key (or set MINERU_API_KEY env / ~/.mineru_api_key)")
    p_batch.add_argument("--output", "-o", default="mineru_batch_output", help="Output directory")
    p_batch.add_argument("--max-pages", type=int, default=MAX_PAGES_PER_FILE, help="Max pages per chunk")

    # split
    p_split = sub.add_parser("split", help="Split a PDF into chunks (no API)")
    p_split.add_argument("pdf", help="Path to PDF file")
    p_split.add_argument("--max-pages", type=int, default=MAX_PAGES_PER_FILE, help="Max pages per chunk")
    p_split.add_argument("--output", "-o", help="Output directory")

    args = parser.parse_args()

    if args.command in ("process", "batch"):
        api_key = resolve_api_key(args.api_key)

    if args.command == "process":
        result = process_pdf(args.pdf, api_key, args.output, args.max_pages)
        print(f"\nOutput: {result}")

    elif args.command == "batch":
        result = batch_process(args.pdfs, api_key, args.output, args.max_pages)
        print(f"\nOutput: {result}")

    elif args.command == "split":
        chunks = split_pdf(args.pdf, args.max_pages, args.output)
        print(f"\nSplit into {len(chunks)} chunks:")
        for c in chunks:
            print(f"  {c}")


if __name__ == "__main__":
    main()
