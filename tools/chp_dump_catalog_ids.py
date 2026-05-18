#!/usr/bin/env python3
"""Build a full CHP compare-ready product ID catalog from sitemap pages.

This script enumerates CHP sitemap pages, extracts product identifier segments
from product links, de-duplicates IDs globally, and writes artifacts for later
validation.

Usage:
    python3 tools/chp_dump_catalog_ids.py
    python3 tools/chp_dump_catalog_ids.py --concurrency 10
    python3 tools/chp_dump_catalog_ids.py --max-pages 100
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import ssl
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

import aiohttp

BASE_URL = "https://chp.co.il"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://chp.co.il/",
}

SITEMAP_PAGE_RE = re.compile(r"/sitemap/(\d+)")
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', flags=re.IGNORECASE)
LOC_RE = re.compile(r"<loc>([^<]+)</loc>", flags=re.IGNORECASE)

# Generic ID form used by CHP product segments in links.
PRODUCT_ID_SEGMENT_RE = re.compile(r"^[A-Za-z0-9]+_[A-Za-z0-9]+$")


@dataclass
class PageResult:
    page: int
    status: int
    html_len: int
    ids: set[str]
    error: str = ""


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _iter_links(html: str) -> Iterable[str]:
    """Yield link strings from HTML, covering both HTML and XML-like loc tags."""
    seen: set[str] = set()
    for link in HREF_RE.findall(html):
        if link and link not in seen:
            seen.add(link)
            yield link
    for link in LOC_RE.findall(html):
        if link and link not in seen:
            seen.add(link)
            yield link


def _candidate_product_id_from_link(link: str) -> str | None:
    """Return a product-ID-like path segment from one sitemap link.

    CHP product links generally end with `.../<product_id>/<page_num>`.
    We only trust this suffix structure to avoid picking unrelated underscore
    tokens from other URL segments.
    """
    parsed = urlparse(link)
    path = parsed.path if parsed.scheme else link
    decoded = unquote(path)
    segments = [s.strip() for s in decoded.split("/") if s.strip()]
    if len(segments) >= 2:
        tail = segments[-1]
        prev = segments[-2]
        if tail.isdigit() and PRODUCT_ID_SEGMENT_RE.fullmatch(prev):
            return prev
    return None


def extract_product_ids_from_sitemap_html(html: str) -> set[str]:
    """Extract product IDs from one sitemap page's HTML."""
    ids: set[str] = set()
    for link in _iter_links(html):
        candidate = _candidate_product_id_from_link(link)
        if candidate:
            ids.add(candidate)
    return ids


async def fetch_text_with_retry(
    session: aiohttp.ClientSession,
    url: str,
    *,
    retries: int,
    timeout_seconds: int,
) -> tuple[int, str]:
    """Fetch URL text with basic retry/backoff.

    Returns:
        (status_code, text)
    """
    last_exc: Exception | None = None
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, headers=HEADERS, timeout=timeout) as resp:
                text = await resp.text(errors="replace")
                if resp.status == 200:
                    return resp.status, text
                if 500 <= resp.status < 600 and attempt < retries:
                    await asyncio.sleep(0.8 * attempt)
                    continue
                return resp.status, text
        except Exception as exc:  # pragma: no cover - network variance
            last_exc = exc
            if attempt < retries:
                await asyncio.sleep(0.8 * attempt)
                continue
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Failed to fetch {url}")


async def fetch_page(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    page: int,
    *,
    retries: int,
    timeout_seconds: int,
) -> PageResult:
    """Fetch one `/sitemap/{page}` and extract product IDs."""
    url = f"{BASE_URL}/sitemap/{page}"
    try:
        async with sem:
            status, html = await fetch_text_with_retry(
                session,
                url,
                retries=retries,
                timeout_seconds=timeout_seconds,
            )
        ids = extract_product_ids_from_sitemap_html(html) if status == 200 else set()
        return PageResult(page=page, status=status, html_len=len(html), ids=ids)
    except Exception as exc:  # pragma: no cover - network variance
        return PageResult(page=page, status=0, html_len=0, ids=set(), error=str(exc))


async def discover_sitemap_pages(
    session: aiohttp.ClientSession,
    *,
    retries: int,
    timeout_seconds: int,
) -> list[int]:
    """Discover sitemap page numbers from `/sitemap` root page."""
    status, html = await fetch_text_with_retry(
        session,
        f"{BASE_URL}/sitemap",
        retries=retries,
        timeout_seconds=timeout_seconds,
    )
    if status != 200:
        return []
    pages = sorted({int(p) for p in SITEMAP_PAGE_RE.findall(html)})
    return pages


def write_artifacts(
    out_dir: Path,
    run_id: str,
    *,
    discovered_pages: list[int],
    crawled_pages: list[int],
    page_results: list[PageResult],
    ids_sorted: list[str],
    started_at: str,
    duration_seconds: float,
) -> dict[str, str]:
    """Write output files and return their absolute paths."""
    out_dir.mkdir(parents=True, exist_ok=True)

    ids_txt = out_dir / f"chp_catalog_ids_{run_id}.txt"
    ids_json = out_dir / f"chp_catalog_ids_{run_id}.json"
    pages_csv = out_dir / f"chp_catalog_page_stats_{run_id}.csv"
    summary_json = out_dir / f"chp_catalog_summary_{run_id}.json"

    ids_txt.write_text("\n".join(ids_sorted) + "\n", encoding="utf-8")

    prefix_counts = Counter(i.split("_", 1)[0] for i in ids_sorted if "_" in i)
    failed_pages = [r.page for r in page_results if r.status != 200 or r.error]

    ids_payload = {
        "source": f"{BASE_URL}/sitemap",
        "generated_at_utc": _now_utc_iso(),
        "total_unique_ids": len(ids_sorted),
        "ids": ids_sorted,
    }
    ids_json.write_text(
        json.dumps(ids_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with pages_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["page", "status", "html_len", "ids_found", "error"])
        for result in sorted(page_results, key=lambda r: r.page):
            writer.writerow(
                [
                    result.page,
                    result.status,
                    result.html_len,
                    len(result.ids),
                    result.error,
                ]
            )

    summary = {
        "source": f"{BASE_URL}/sitemap",
        "run_id": run_id,
        "started_at_utc": started_at,
        "finished_at_utc": _now_utc_iso(),
        "duration_seconds": round(duration_seconds, 2),
        "sitemap_pages_discovered": len(discovered_pages),
        "sitemap_page_min": min(discovered_pages) if discovered_pages else None,
        "sitemap_page_max": max(discovered_pages) if discovered_pages else None,
        "sitemap_pages_crawled": len(crawled_pages),
        "sitemap_pages_failed": len(failed_pages),
        "failed_pages": failed_pages,
        "total_unique_ids": len(ids_sorted),
        "id_prefix_counts": dict(sorted(prefix_counts.items())),
        "artifacts": {
            "ids_txt": str(ids_txt.resolve()),
            "ids_json": str(ids_json.resolve()),
            "pages_csv": str(pages_csv.resolve()),
            "summary_json": str(summary_json.resolve()),
        },
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "ids_txt": str(ids_txt.resolve()),
        "ids_json": str(ids_json.resolve()),
        "pages_csv": str(pages_csv.resolve()),
        "summary_json": str(summary_json.resolve()),
    }


async def async_main(args: argparse.Namespace) -> int:
    started_at = _now_utc_iso()
    t0 = time.monotonic()

    ssl_ctx = ssl.create_default_context()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx, limit=max(args.concurrency + 4, 12))
    sem = asyncio.Semaphore(args.concurrency)

    async with aiohttp.ClientSession(connector=connector) as session:
        discovered_pages = await discover_sitemap_pages(
            session,
            retries=args.retries,
            timeout_seconds=args.timeout,
        )

        if discovered_pages:
            pages = sorted(set(discovered_pages) | {1})
        else:
            pages = list(range(1, args.fallback_pages + 1))

        if args.max_pages > 0:
            pages = pages[: args.max_pages]

        if not pages:
            print("No sitemap pages discovered; nothing to crawl.", file=sys.stderr)
            return 2

        print(
            f"Discovered {len(discovered_pages)} sitemap pages; crawling {len(pages)} pages with concurrency={args.concurrency}..."
        )

        tasks = [
            asyncio.create_task(
                fetch_page(
                    session,
                    sem,
                    page,
                    retries=args.retries,
                    timeout_seconds=args.timeout,
                )
            )
            for page in pages
        ]

        page_results_map: dict[int, PageResult] = {}
        all_ids: set[str] = set()
        completed = 0
        for task in asyncio.as_completed(tasks):
            result = await task
            page_results_map[result.page] = result
            all_ids.update(result.ids)
            completed += 1
            if completed % 100 == 0 or completed == len(tasks):
                print(
                    f"  progress {completed}/{len(tasks)} pages, unique IDs: {len(all_ids)}"
                )

        # Recovery passes for transient failures (timeouts/5xx).
        for pass_num in range(1, args.recovery_passes + 1):
            failed_pages = sorted(
                page
                for page, result in page_results_map.items()
                if result.status != 200 or result.error
            )
            if not failed_pages:
                break

            print(
                f"Recovery pass {pass_num}/{args.recovery_passes}: retrying {len(failed_pages)} failed pages "
                f"with concurrency={args.recovery_concurrency}..."
            )
            recovery_sem = asyncio.Semaphore(args.recovery_concurrency)
            recovery_tasks = [
                asyncio.create_task(
                    fetch_page(
                        session,
                        recovery_sem,
                        page,
                        retries=args.recovery_retries,
                        timeout_seconds=args.timeout,
                    )
                )
                for page in failed_pages
            ]

            recovered = 0
            for task in asyncio.as_completed(recovery_tasks):
                result = await task
                old = page_results_map.get(result.page)
                page_results_map[result.page] = result
                if old is not None and (old.status != 200 or old.error):
                    if result.status == 200 and not result.error:
                        recovered += 1
                all_ids.update(result.ids)

            print(
                f"  recovery pass {pass_num}: recovered {recovered}/{len(failed_pages)} pages, "
                f"unique IDs now {len(all_ids)}"
            )

    page_results = list(page_results_map.values())

    ids_sorted = sorted(all_ids)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    artifacts = write_artifacts(
        out_dir=Path(args.out_dir),
        run_id=run_id,
        discovered_pages=discovered_pages,
        crawled_pages=pages,
        page_results=page_results,
        ids_sorted=ids_sorted,
        started_at=started_at,
        duration_seconds=time.monotonic() - t0,
    )

    failed = [r for r in page_results if r.status != 200 or r.error]
    print("Done.")
    print(f"  unique IDs: {len(ids_sorted)}")
    print(f"  failed pages: {len(failed)}")
    print("  artifacts:")
    for key, value in artifacts.items():
        print(f"    - {key}: {value}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Enumerate CHP product IDs from sitemap pages and write validation artifacts."
        )
    )
    parser.add_argument(
        "--out-dir",
        default="output_dir/chp",
        help="Directory where artifacts are written (default: output_dir/chp)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Concurrent sitemap page fetches (default: 8)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retries per request (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Per-request timeout seconds (default: 45)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Limit to first N discovered pages for test runs (default: 0 means all)",
    )
    parser.add_argument(
        "--fallback-pages",
        type=int,
        default=1473,
        help="Fallback page count if /sitemap root discovery fails (default: 1473)",
    )
    parser.add_argument(
        "--recovery-passes",
        type=int,
        default=2,
        help="Extra retry rounds over failed pages (default: 2)",
    )
    parser.add_argument(
        "--recovery-concurrency",
        type=int,
        default=2,
        help="Concurrency used in recovery passes (default: 2)",
    )
    parser.add_argument(
        "--recovery-retries",
        type=int,
        default=5,
        help="Retries per failed page in recovery passes (default: 5)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.concurrency < 1:
        print("--concurrency must be >= 1", file=sys.stderr)
        return 2
    if args.retries < 1:
        print("--retries must be >= 1", file=sys.stderr)
        return 2
    if args.recovery_passes < 0:
        print("--recovery-passes must be >= 0", file=sys.stderr)
        return 2
    if args.recovery_concurrency < 1:
        print("--recovery-concurrency must be >= 1", file=sys.stderr)
        return 2
    if args.recovery_retries < 1:
        print("--recovery-retries must be >= 1", file=sys.stderr)
        return 2
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
