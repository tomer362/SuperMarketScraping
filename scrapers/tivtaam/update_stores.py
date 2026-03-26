#!/usr/bin/env python3
"""
update_stores.py  (scrapers/tivtaam/)
======================================
Utility script that fetches the current list of Tiv Taam online branches from
the site's data.js configuration file and rewrites the ONLINE_BRANCHES constant
inside tivtaam.py.

Usage (from project root):
    python3 -m scrapers.tivtaam.update_stores [--dry-run]

Options:
    --dry-run   Print what would be written without modifying tivtaam.py.

How it works:
    1. Downloads the HTML from https://www.tivtaam.co.il to find the data.js URL.
    2. Downloads and parses data.js which contains the full branch list
       (window.sp.frontendData.retailers[0].branches).
    3. Filters for branches where isOnline == true.
    4. Replaces the ONLINE_BRANCHES block in tivtaam.py with the fresh list.
"""

import argparse
import gzip
import json
import re
import sys
import urllib.request
import ssl
from pathlib import Path
from typing import List, Dict, Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TIVTAAM_HOME = "https://www.tivtaam.co.il"
TIVTAAM_PY = Path(__file__).parent / "tivtaam.py"

# Markers in tivtaam.py that delimit the ONLINE_BRANCHES block to replace.
BLOCK_START_MARKER = "ONLINE_BRANCHES: List[Branch] = ["
BLOCK_END_MARKER = "]"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": f"{TIVTAAM_HOME}/",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ssl_context() -> ssl.SSLContext:
    """Return an SSL context, preferring certifi certs when available."""
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        # Fall back to disabling verification so the script works without certifi
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, context=_make_ssl_context()) as resp:
        return resp.read()


def _fetch_text(url: str) -> str:
    raw = _fetch(url)
    # Decompress if gzip-encoded
    try:
        return gzip.decompress(raw).decode("utf-8")
    except OSError:
        return raw.decode("utf-8", errors="replace")


def find_data_js_url(html: str) -> str:
    """Extract the data.js script src from the homepage HTML."""
    match = re.search(
        r'<script[^>]+src=["\']([^"\']*data\.js[^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if not match:
        raise RuntimeError(
            "Could not find data.js URL in the Tiv Taam homepage. "
            "The site may have changed its asset structure."
        )
    url = match.group(1)
    # Make absolute if relative
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = TIVTAAM_HOME + url
    return url


def extract_branches_from_data_js(js_text: str) -> List[Dict[str, Any]]:
    """Parse branches out of the window.sp frontendData blob."""
    # Find the branches JSON array inside the large JS blob.
    # Structure: window.sp = { frontendData: { ... "branches":[...], ... } }
    #
    # We parse each branch object individually to avoid loading the full 3 MB
    # JS string into json.loads (it is not valid JSON — it has trailing commas
    # and other JS-isms).
    #
    # Strategy: locate the "branches":[ array and extract individual {...}
    # objects from it using a simple bracket-depth scanner.

    start_marker = '"branches":['
    idx = js_text.find(start_marker)
    if idx == -1:
        raise RuntimeError(
            "Could not locate the 'branches' array in data.js. "
            "The site may have changed its data format."
        )

    array_start = idx + len(start_marker) - 1  # points at '['
    # Walk forward to find the matching ']'
    depth = 0
    array_end = array_start
    for i, ch in enumerate(js_text[array_start:], start=array_start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                array_end = i
                break

    array_text = js_text[array_start : array_end + 1]  # includes [ and ]

    # Now parse it — but it may contain JS-style trailing commas.
    # Strip trailing commas before ] or }
    array_text_clean = re.sub(r",\s*([}\]])", r"\1", array_text)

    try:
        branches = json.loads(array_text_clean)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to JSON-parse branches array: {exc}\n"
            f"(first 300 chars of array): {array_text[:300]}"
        ) from exc

    return branches


def filter_online_branches(branches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return only branches where isOnline is True."""
    return [b for b in branches if b.get("isOnline") is True]


def branches_to_python_literal(branches: List[Dict[str, Any]]) -> str:
    """Render the branch list as a Python literal suitable for tivtaam.py."""
    lines = []
    for b in branches:
        bid = b.get("id", 0)
        name = b.get("name", "").replace('"', '\\"')
        city = b.get("city", "").replace('"', '\\"')
        # City often contains extra annotations like "(מכבדים סיבוס ותן ביס בסניף)"
        city_clean = re.sub(r"\s*\(.*?\)", "", city).strip()
        location = b.get("location", "").replace('"', '\\"')
        lines.append(
            f'    {{"id": {bid:5d}, "name": "{name}", '
            f'"city": "{city_clean}", "location": "{location}"}},'
        )
    return "\n".join(lines)


def replace_online_branches_block(source: str, new_block_body: str) -> str:
    """Replace the ONLINE_BRANCHES list body in tivtaam.py source."""
    start_idx = source.find(BLOCK_START_MARKER)
    if start_idx == -1:
        raise RuntimeError(f"Could not find '{BLOCK_START_MARKER}' in tivtaam.py")

    # Find the list's opening '['
    bracket_open = start_idx + len(BLOCK_START_MARKER) - 1
    # Walk forward to find the matching ']'
    depth = 0
    bracket_close = bracket_open
    for i, ch in enumerate(source[bracket_open:], start=bracket_open):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                bracket_close = i
                break

    before = source[: bracket_open + 1]  # up to and including '['
    after = source[bracket_close:]  # from ']' onward

    return before + "\n" + new_block_body + "\n" + after


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the new ONLINE_BRANCHES block without modifying tivtaam.py",
    )
    args = parser.parse_args()

    print("Fetching Tiv Taam homepage…")
    homepage_html = _fetch_text(TIVTAAM_HOME)

    print("Locating data.js URL…")
    data_js_url = find_data_js_url(homepage_html)
    print(f"  → {data_js_url}")

    print("Downloading data.js (may be several MB)…")
    data_js_text = _fetch_text(data_js_url)
    print(f"  → {len(data_js_text):,} characters")

    print("Extracting branches…")
    all_branches = extract_branches_from_data_js(data_js_text)
    print(f"  → {len(all_branches)} total branches found")

    online_branches = filter_online_branches(all_branches)
    print(f"  → {len(online_branches)} online branches:")
    for b in online_branches:
        city = re.sub(r"\s*\(.*?\)", "", b.get("city", "")).strip()
        print(f"     id={b['id']:5d}  {b.get('name', ''):30s}  {city}")

    if not online_branches:
        print("WARNING: No online branches found. tivtaam.py will NOT be updated.")
        sys.exit(1)

    new_block_body = branches_to_python_literal(online_branches)

    if args.dry_run:
        print("\n--- New ONLINE_BRANCHES block (dry run) ---")
        print(BLOCK_START_MARKER)
        print(new_block_body)
        print("]")
        return

    # Read, patch, write
    source = TIVTAAM_PY.read_text(encoding="utf-8")
    new_source = replace_online_branches_block(source, new_block_body)

    TIVTAAM_PY.write_text(new_source, encoding="utf-8")
    print(
        f"\ntivtaam.py updated successfully with {len(online_branches)} online branches."
    )


if __name__ == "__main__":
    main()
