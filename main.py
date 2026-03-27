"""
Supermarket Scraper — Main Orchestrator
=======================================
Usage examples
--------------
# Scrape all supermarkets, all branches:
    python3 main.py

# Scrape only Tiv Taam:
    python3 main.py --supermarkets tivtaam

# Scrape Shufersal and Yochananof:
    python3 main.py --supermarkets shufersal yochananof

# Search for a product by name across all chains:
    python3 main.py --filter-name "חלב"

# Filter by category ID:
    python3 main.py --supermarkets tivtaam --filter-category 90176

# Filter by exact EAN barcode:
    python3 main.py --filter-barcode 7290000066882

# Tune concurrency and retry behaviour:
    python3 main.py --batch-size 50 --max-concurrent 10 --retry-limit 5 --base-retry-delay 2.0

# Scrape specific branches:
    python3 main.py --supermarkets tivtaam --tivtaam-branches 924 929
    python3 main.py --supermarkets carrefour --carrefour-branches 3003 3014
    python3 main.py --supermarkets yochananof --yochananof-stores s82 s63
    python3 main.py --supermarkets machsanei --machsanei-branches 836

# Scrape a single branch of each chain:
    python3 main.py --supermarkets tivtaam --tivtaam-branches 924
    python3 main.py --supermarkets carrefour --carrefour-branches 3014
    python3 main.py --supermarkets machsanei --machsanei-branches 836
    python3 main.py --supermarkets ramilevi --ramilevi-stores 1332
    python3 main.py --supermarkets yochananof --yochananof-stores s82
    python3 main.py --supermarkets keshet --keshet-branches 2725
    python3 main.py --supermarkets quik --quik-branches 3264
    python3 main.py --supermarkets victory --victory-branches 2930
    python3 main.py --supermarkets ybitan --ybitan-branches 960
    python3 main.py --supermarkets shufersal  # no branch filter — global catalogue

# List available branches for each chain and exit:
    python3 main.py --list-branches

# Save results to JSON files:
    python3 main.py --output-dir ./results

# Logging options:
    python3 main.py --log-file scrape.log --log-level DEBUG
    python3 main.py --quiet --log-file scrape.log
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from scrapers.common import ScrapeFilter, ScrapeResult
from utils import ColourFormatter, _is_tty

# ---------------------------------------------------------------------------
# Argument parser helpers
# ---------------------------------------------------------------------------


class _HelpOnErrorParser(argparse.ArgumentParser):
    """ArgumentParser that prints the full help message before any error."""

    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_help(sys.stderr)
        self.exit(2, f"\nerror: {message}\n")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging(
    log_file: Optional[str] = None,
    log_level: str = "INFO",
    console: bool = True,
) -> None:
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in root.handlers[:]:
        root.removeHandler(h)

    if console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(numeric_level)
        ch.setFormatter(ColourFormatter(use_colour=_is_tty()))
        root.addHandler(ch)

    if log_file:
        fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(fh)
        logging.getLogger("main").info("Logging to file: %s", log_file)


logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# Result serialisation helpers
# ---------------------------------------------------------------------------


def _save_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Saved %s", path)


def _result_summary(result: ScrapeResult) -> str:
    chain = result["chain"]
    total = result["products_total"]
    stores = result["stores_scraped"]
    duration = result["duration_seconds"]
    errors = len(result["errors"])
    by_store = ", ".join(
        f"{sid}={len(prods):,}" for sid, prods in result["products_by_store"].items()
    )
    parts = [f"{chain}: {total:,} products from {stores} store(s) in {duration:.1f}s"]
    if by_store:
        parts.append(f"  [{by_store}]")
    if errors:
        parts.append(f"  ({errors} errors)")
    return "\n".join(parts)


def _print_summary_table(all_results: Dict[str, ScrapeResult], elapsed: float) -> None:
    """Print a formatted summary table to stdout."""
    header = (
        f"{'Chain':<16} {'Stores':>6} {'Products':>10} {'Time (s)':>9} {'Errors':>7}"
    )
    divider = "-" * len(header)
    lines = [divider, header, divider]
    total_products = 0
    for chain, r in sorted(all_results.items()):
        total_products += r["products_total"]
        lines.append(
            f"{chain:<16} {r['stores_scraped']:>6} {r['products_total']:>10,}"
            f" {r['duration_seconds']:>9.1f} {len(r['errors']):>7}"
        )
    lines.append(divider)
    lines.append(f"{'TOTAL':<16} {'':>6} {total_products:>10,} {elapsed:>9.1f}")
    lines.append(divider)
    for line in lines:
        logger.info(line)


# ---------------------------------------------------------------------------
# Branch/store selection helpers
# ---------------------------------------------------------------------------


def _resolve_branches(
    raw_args: List[str],
    branch_list: list,
    *,
    id_key: str = "id",
    name_key: str = "name",
) -> list:
    """Resolve a list of raw CLI args to branch/store dicts.

    Each element in *raw_args* can be either:
      - An integer string (e.g. ``"924"``) — matched against ``branch[id_key]``
      - A Hebrew name substring — matched against ``branch[name_key]`` (case-insensitive)

    Duplicates are preserved in the order they first match; the overall output
    order follows *branch_list* order, not the order of *raw_args*.

    Args:
        raw_args:    Raw CLI argument values (strings).
        branch_list: Master list of branch/store dicts to filter from.
        id_key:      Key for the integer ID field (default: ``"id"``).
        name_key:    Key for the display-name field (default: ``"name"``).

    Returns:
        Filtered subset of *branch_list* matching any arg.
    """
    if not raw_args:
        return list(branch_list)

    int_ids: set[int] = set()
    substrings: list[str] = []
    for arg in raw_args:
        try:
            int_ids.add(int(arg))
        except ValueError:
            substrings.append(arg)

    selected: list = []
    for branch in branch_list:
        bid = branch.get(id_key)
        bname = str(branch.get(name_key) or "")
        matched = (bid is not None and bid in int_ids) or any(
            sub in bname for sub in substrings
        )
        if matched and branch not in selected:
            selected.append(branch)
    return selected


# ---------------------------------------------------------------------------
# --list-branches helper
# ---------------------------------------------------------------------------


def _print_branches_and_exit(supermarkets: Optional[List[str]] = None) -> None:
    """Print all known branch IDs/names for each chain and exit.

    Args:
        supermarkets: If provided, only print the listed chains.
    """
    all_chains = supermarkets or _ALL_CHAINS

    print("=== Available branches per chain ===\n")

    if "tivtaam" in all_chains:
        from scrapers.tivtaam.tivtaam import ONLINE_BRANCHES as TT_BRANCHES

        print("tivtaam  (--tivtaam-branches)")
        print("-------")
        for b in TT_BRANCHES:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "carrefour" in all_chains:
        from scrapers.carrefour.carrefour import ONLINE_BRANCHES as CF_BRANCHES

        print("carrefour  (--carrefour-branches)")
        print("---------")
        for b in CF_BRANCHES:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "machsanei" in all_chains:
        from scrapers.machsanei_hashook.machsanei_hashook import (
            ONLINE_BRANCHES as MCK_BRANCHES,
        )

        print("machsanei  (--machsanei-branches)")
        print("----------")
        for b in MCK_BRANCHES:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "ramilevi" in all_chains:
        from scrapers.ramilevi.ramilevi import ONLINE_STORES as RL_STORES

        print("ramilevi  (--ramilevi-stores)")
        print("--------")
        for s in RL_STORES:
            print(f"  {s['id']:>6}  {s['name']}  ({s['city']})")
        print()

    if "yochananof" in all_chains:
        print("yochananof  (--yochananof-stores)")
        print("----------")
        print("  Store codes are fetched dynamically from the API.")
        print("  Run without --yochananof-stores to scrape all stores.")
        print("  Examples: s82 s63 s73 s51")
        print()

    if "shufersal" in all_chains:
        print("shufersal")
        print("---------")
        print("  Shufersal has a single global catalogue (no per-branch selection).")
        print()

    if "keshet" in all_chains:
        from scrapers.keshet.keshet import ONLINE_BRANCHES as KT_BRANCHES

        print("keshet  (--keshet-branches)")
        print("------")
        for b in KT_BRANCHES:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "quik" in all_chains:
        from scrapers.quik.quik import ONLINE_BRANCHES as QK_BRANCHES

        print("quik  (--quik-branches)")
        print("----")
        for b in QK_BRANCHES:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "victory" in all_chains:
        from scrapers.victory.victory import ONLINE_BRANCHES as VC_BRANCHES

        print("victory  (--victory-branches)")
        print("-------")
        for b in VC_BRANCHES:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "ybitan" in all_chains:
        from scrapers.ybitan.ybitan import ONLINE_BRANCHES as YB_BRANCHES

        print("ybitan  (--ybitan-branches)")
        print("------")
        for b in YB_BRANCHES:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    sys.exit(0)


# ---------------------------------------------------------------------------
# --update-branches helper
# ---------------------------------------------------------------------------


async def _update_branches_and_exit(supermarkets: Optional[List[str]] = None) -> None:
    """Fetch live branch lists from each chain's API and print, then exit.

    Args:
        supermarkets: If provided, only query the listed chains.
    """
    all_chains = supermarkets or _ALL_CHAINS
    print("=== Live branch lists from APIs ===\n")

    if "tivtaam" in all_chains:
        from scrapers.tivtaam.tivtaam import update_branches as tt_update

        branches = await tt_update()
        print("tivtaam  (--tivtaam-branches)")
        print("-------")
        for b in branches:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "carrefour" in all_chains:
        from scrapers.carrefour.carrefour import update_branches as cf_update

        branches = await cf_update()
        print("carrefour  (--carrefour-branches)")
        print("---------")
        for b in branches:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "machsanei" in all_chains:
        from scrapers.machsanei_hashook.machsanei_hashook import (
            update_branches as mck_update,
        )

        branches = await mck_update()
        print("machsanei  (--machsanei-branches)")
        print("----------")
        for b in branches:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "ramilevi" in all_chains:
        from scrapers.ramilevi.ramilevi import update_branches as rl_update

        stores = await rl_update()
        print("ramilevi  (--ramilevi-stores)")
        print("--------")
        for s in stores:
            print(f"  {s['id']:>6}  {s['name']}  ({s['city']})")
        print()

    if "yochananof" in all_chains:
        from scrapers.yochananof.yochananof import update_branches as yo_update

        stores = await yo_update()
        print("yochananof  (--yochananof-stores)")
        print("----------")
        for s in stores:
            print(f"  {s['store_code']:>8}  {s['store_name']}")
        print()

    if "shufersal" in all_chains:
        print("shufersal")
        print("---------")
        print("  Shufersal has a single global catalogue (no per-branch selection).")
        print()

    if "keshet" in all_chains:
        from scrapers.keshet.keshet import update_branches as kt_update

        branches = await kt_update()
        print("keshet  (--keshet-branches)")
        print("------")
        for b in branches:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "quik" in all_chains:
        from scrapers.quik.quik import update_branches as qk_update

        branches = await qk_update()
        print("quik  (--quik-branches)")
        print("----")
        for b in branches:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "victory" in all_chains:
        from scrapers.victory.victory import update_branches as vc_update

        branches = await vc_update()
        print("victory  (--victory-branches)")
        print("-------")
        for b in branches:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    if "ybitan" in all_chains:
        from scrapers.ybitan.ybitan import update_branches as yb_update

        branches = await yb_update()
        print("ybitan  (--ybitan-branches)")
        print("------")
        for b in branches:
            print(f"  {b['id']:>6}  {b['name']}  ({b['city']})")
        print()

    sys.exit(0)


# ---------------------------------------------------------------------------
# Scraper wrappers — each returns a ScrapeResult
# ---------------------------------------------------------------------------


async def run_tivtaam(
    branch_ids: Optional[List[str]],
    flt: ScrapeFilter,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_retry_delay: float,
    output_dir: Optional[Path],
) -> ScrapeResult:
    from scrapers.tivtaam.tivtaam import ONLINE_BRANCHES, scrape

    branches = (
        _resolve_branches(branch_ids or [], ONLINE_BRANCHES)
        if branch_ids
        else list(ONLINE_BRANCHES)
    )
    if branch_ids and not branches:
        logger.error(
            "No Tiv Taam branches matched %s. Available: %s",
            branch_ids,
            [f"{b['id']} {b['name']}" for b in ONLINE_BRANCHES],
        )
        from scrapers.common import utc_now_iso

        return ScrapeResult(
            chain="tivtaam",
            stores_scraped=0,
            products_total=0,
            products_by_store={},
            scraped_at=utc_now_iso(),
            duration_seconds=0.0,
            errors=["No matching branches"],
        )

    logger.info(
        "Tiv Taam: scraping %d branch(es): %s",
        len(branches),
        [f"{b['id']} ({b['name']})" for b in branches],
    )
    result = await scrape(
        branches=branches,
        flt=flt,
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        max_retries=max_retries,
        base_retry_delay=base_retry_delay,
    )

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for store_id, products in result["products_by_store"].items():
            branch = next((b for b in branches if str(b["id"]) == store_id), {})
            slug = (
                branch.get("name", store_id)
                .replace(" ", "_")
                .replace('"', "")
                .replace("'", "")
            )
            _save_json(
                products, output_dir / "tivtaam" / f"branch_{store_id}_{slug}_{ts}.json"
            )
        _save_json(
            {
                sid: {"branch_id": sid, "product_count": len(p)}
                for sid, p in result["products_by_store"].items()
            },
            output_dir / "tivtaam" / f"summary_{ts}.json",
        )

    return result


async def run_shufersal(
    flt: ScrapeFilter,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_retry_delay: float,
    output_dir: Optional[Path],
) -> ScrapeResult:
    from scrapers.shufersal.shufersal import scrape

    logger.info("Shufersal: starting scrape…")
    result = await scrape(
        flt=flt,
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        max_retries=max_retries,
        base_retry_delay=base_retry_delay,
    )

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for store_id, products in result["products_by_store"].items():
            _save_json(
                products, output_dir / "shufersal" / f"products_{store_id}_{ts}.json"
            )

    return result


async def run_yochananof(
    store_codes: Optional[List[str]],
    flt: ScrapeFilter,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_retry_delay: float,
    output_dir: Optional[Path],
) -> ScrapeResult:
    import aiohttp
    from scrapers.common import make_ssl_context, utc_now_iso
    from scrapers.yochananof.yochananof import fetch_stores, scrape

    stores = None
    if store_codes:
        connector = aiohttp.TCPConnector(ssl=make_ssl_context())
        async with aiohttp.ClientSession(connector=connector) as session:
            all_stores = await fetch_stores(
                session, max_retries=max_retries, base_delay=base_retry_delay
            )
        stores = [s for s in all_stores if s["store_code"] in store_codes]
        missing = set(store_codes) - {s["store_code"] for s in stores}
        if missing:
            logger.warning(
                "Yochananof store codes not found: %s. Available: %s",
                sorted(missing),
                [s["store_code"] for s in all_stores],
            )
        if not stores:
            logger.error("No valid Yochananof stores to scrape.")
            return ScrapeResult(
                chain="yochananof",
                stores_scraped=0,
                products_total=0,
                products_by_store={},
                scraped_at=utc_now_iso(),
                duration_seconds=0.0,
                errors=["No valid stores"],
            )

    logger.info(
        "Yochananof: scraping %s",
        ", ".join(f"{s['store_code']} ({s['store_name']})" for s in stores)
        if stores
        else "all stores",
    )
    result = await scrape(
        stores=stores,
        flt=flt,
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        max_retries=max_retries,
        base_retry_delay=base_retry_delay,
    )

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for store_code, products in result["products_by_store"].items():
            _save_json(
                products,
                output_dir / "yochananof" / f"store_{store_code}_{ts}.json",
            )
        _save_json(
            {
                sc: {"store_code": sc, "product_count": len(p)}
                for sc, p in result["products_by_store"].items()
            },
            output_dir / "yochananof" / f"summary_{ts}.json",
        )

    return result


async def run_carrefour(
    branch_ids: Optional[List[str]],
    flt: ScrapeFilter,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_retry_delay: float,
    output_dir: Optional[Path],
) -> ScrapeResult:
    from scrapers.carrefour.carrefour import ONLINE_BRANCHES, scrape

    branches = (
        _resolve_branches(branch_ids or [], ONLINE_BRANCHES)
        if branch_ids
        else list(ONLINE_BRANCHES)
    )
    if branch_ids and not branches:
        logger.error(
            "No Carrefour branches matched %s. Available: %s",
            branch_ids,
            [f"{b['id']} {b['name']}" for b in ONLINE_BRANCHES],
        )
        from scrapers.common import utc_now_iso

        return ScrapeResult(
            chain="carrefour",
            stores_scraped=0,
            products_total=0,
            products_by_store={},
            scraped_at=utc_now_iso(),
            duration_seconds=0.0,
            errors=["No matching branches"],
        )

    logger.info(
        "Carrefour: scraping %d branch(es): %s",
        len(branches),
        [f"{b['id']} ({b['name']})" for b in branches],
    )
    result = await scrape(
        branches=branches,
        flt=flt,
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        max_retries=max_retries,
        base_retry_delay=base_retry_delay,
    )

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for store_id, products in result["products_by_store"].items():
            branch = next((b for b in branches if str(b["id"]) == store_id), {})
            slug = (
                branch.get("name", store_id)
                .replace(" ", "_")
                .replace('"', "")
                .replace("'", "")
            )
            _save_json(
                products,
                output_dir / "carrefour" / f"branch_{store_id}_{slug}_{ts}.json",
            )
        _save_json(
            {
                sid: {"branch_id": sid, "product_count": len(p)}
                for sid, p in result["products_by_store"].items()
            },
            output_dir / "carrefour" / f"summary_{ts}.json",
        )

    return result


async def run_machsanei(
    branch_ids: Optional[List[str]],
    flt: ScrapeFilter,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_retry_delay: float,
    output_dir: Optional[Path],
) -> ScrapeResult:
    from scrapers.machsanei_hashook.machsanei_hashook import ONLINE_BRANCHES, scrape

    branches = (
        _resolve_branches(branch_ids or [], ONLINE_BRANCHES)
        if branch_ids
        else list(ONLINE_BRANCHES)
    )
    if branch_ids and not branches:
        logger.error(
            "No Machsanei HaShook branches matched %s. Available: %s",
            branch_ids,
            [f"{b['id']} {b['name']}" for b in ONLINE_BRANCHES],
        )
        from scrapers.common import utc_now_iso

        return ScrapeResult(
            chain="machsanei",
            stores_scraped=0,
            products_total=0,
            products_by_store={},
            scraped_at=utc_now_iso(),
            duration_seconds=0.0,
            errors=["No matching branches"],
        )

    logger.info(
        "Machsanei HaShook: scraping %d branch(es): %s",
        len(branches),
        [f"{b['id']} ({b['name']})" for b in branches],
    )
    result = await scrape(
        branches=branches,
        flt=flt,
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        max_retries=max_retries,
        base_retry_delay=base_retry_delay,
    )

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for store_id, products in result["products_by_store"].items():
            branch = next((b for b in branches if str(b["id"]) == store_id), {})
            slug = (
                branch.get("name", store_id)
                .replace(" ", "_")
                .replace('"', "")
                .replace("'", "")
            )
            _save_json(
                products,
                output_dir / "machsanei" / f"branch_{store_id}_{slug}_{ts}.json",
            )
        _save_json(
            {
                sid: {"branch_id": sid, "product_count": len(p)}
                for sid, p in result["products_by_store"].items()
            },
            output_dir / "machsanei" / f"summary_{ts}.json",
        )

    return result


async def run_ramilevi(
    store_ids: Optional[List[str]],
    flt: ScrapeFilter,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_retry_delay: float,
    output_dir: Optional[Path],
) -> ScrapeResult:
    from scrapers.ramilevi.ramilevi import ONLINE_STORES, scrape

    stores = (
        _resolve_branches(store_ids or [], ONLINE_STORES)
        if store_ids
        else list(ONLINE_STORES)
    )
    if store_ids and not stores:
        logger.error(
            "No Rami Levy stores matched %s. Available: %s",
            store_ids,
            [f"{s['id']} {s['name']}" for s in ONLINE_STORES],
        )
        from scrapers.common import utc_now_iso

        return ScrapeResult(
            chain="ramilevi",
            stores_scraped=0,
            products_total=0,
            products_by_store={},
            scraped_at=utc_now_iso(),
            duration_seconds=0.0,
            errors=["No matching stores"],
        )

    logger.info(
        "Rami Levy: scraping %d store(s): %s",
        len(stores),
        [f"{s['id']} ({s['name']})" for s in stores],
    )
    result = await scrape(
        stores=stores,
        flt=flt,
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        max_retries=max_retries,
        base_retry_delay=base_retry_delay,
    )

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for store_id, products in result["products_by_store"].items():
            store = next((s for s in stores if str(s["id"]) == store_id), {})
            slug = (
                store.get("name", store_id)
                .replace(" ", "_")
                .replace('"', "")
                .replace("'", "")
            )
            _save_json(
                products,
                output_dir / "ramilevi" / f"store_{store_id}_{slug}_{ts}.json",
            )
        _save_json(
            {
                sid: {"store_id": sid, "product_count": len(p)}
                for sid, p in result["products_by_store"].items()
            },
            output_dir / "ramilevi" / f"summary_{ts}.json",
        )

    return result


async def run_zuz_chain(
    chain_name: str,
    module_path: str,
    branch_ids: Optional[List[str]],
    flt: ScrapeFilter,
    batch_size: int,
    max_concurrent: int,
    max_retries: int,
    base_retry_delay: float,
    output_dir: Optional[Path],
) -> ScrapeResult:
    """Generic runner for ZuZ-platform scrapers (keshet, quik, victory, ybitan)."""
    import importlib

    mod = importlib.import_module(module_path)
    ONLINE_BRANCHES = mod.ONLINE_BRANCHES
    scrape = mod.scrape

    branches = (
        _resolve_branches(branch_ids or [], ONLINE_BRANCHES)
        if branch_ids
        else list(ONLINE_BRANCHES)
    )
    if branch_ids and not branches:
        logger.error(
            "No %s branches matched %s. Available: %s",
            chain_name,
            branch_ids,
            [f"{b['id']} {b['name']}" for b in ONLINE_BRANCHES],
        )
        from scrapers.common import utc_now_iso

        return ScrapeResult(
            chain=chain_name,
            stores_scraped=0,
            products_total=0,
            products_by_store={},
            scraped_at=utc_now_iso(),
            duration_seconds=0.0,
            errors=["No matching branches"],
        )

    logger.info(
        "%s: scraping %d branch(es): %s",
        chain_name,
        len(branches),
        [f"{b['id']} ({b['name']})" for b in branches],
    )
    result = await scrape(
        branches=branches,
        flt=flt,
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        max_retries=max_retries,
        base_retry_delay=base_retry_delay,
    )

    if output_dir:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        for store_id, products in result["products_by_store"].items():
            branch = next((b for b in branches if str(b["id"]) == store_id), {})
            slug = (
                branch.get("name", store_id)
                .replace(" ", "_")
                .replace('"', "")
                .replace("'", "")
            )
            _save_json(
                products,
                output_dir / chain_name / f"branch_{store_id}_{slug}_{ts}.json",
            )
        _save_json(
            {
                sid: {"branch_id": sid, "product_count": len(p)}
                for sid, p in result["products_by_store"].items()
            },
            output_dir / chain_name / f"summary_{ts}.json",
        )

    return result


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

_ALL_CHAINS = [
    "tivtaam",
    "shufersal",
    "yochananof",
    "carrefour",
    "machsanei",
    "ramilevi",
    "keshet",
    "quik",
    "victory",
    "ybitan",
]


def _build_parser() -> argparse.ArgumentParser:
    parser = _HelpOnErrorParser(
        prog="python3 main.py",
        description=(
            "Supermarket scraper — fetches product catalogues from Israeli "
            "online supermarkets."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # --- list branches ---
    parser.add_argument(
        "--list-branches",
        action="store_true",
        default=False,
        help=(
            "Print all known branch IDs and names for each chain, then exit. "
            "Use these IDs with --tivtaam-branches, --carrefour-branches, "
            "--machsanei-branches."
        ),
    )

    # --- update branches ---
    parser.add_argument(
        "--update-branches",
        action="store_true",
        default=False,
        help=(
            "Fetch the live branch/store list for each selected chain from their "
            "respective APIs and print the result, then exit. "
            "Respects --supermarkets to limit which chains are queried."
        ),
    )

    # --- supermarket selection ---
    parser.add_argument(
        "--supermarkets",
        nargs="+",
        metavar="NAME",
        choices=_ALL_CHAINS,
        default=_ALL_CHAINS,
        help=(
            "Which supermarkets to scrape. "
            f"Choices: {', '.join(_ALL_CHAINS)}. Default: all."
        ),
    )

    # --- filter options ---
    filter_group = parser.add_argument_group("filter options")
    filter_group.add_argument(
        "--filter-name",
        metavar="TEXT",
        default=None,
        help=(
            "Search products by name/keyword. Uses native API search where "
            "available (Tiv Taam, Carrefour autocomplete; Shufersal q=; "
            "Yochananof GraphQL search; Machsanei HaShook ZuZ search)."
        ),
    )
    filter_group.add_argument(
        "--filter-category",
        metavar="ID_OR_NAME",
        default=None,
        help=(
            "Restrict to products in this category ID. "
            "Accepts a single category ID string."
        ),
    )
    filter_group.add_argument(
        "--filter-barcode",
        metavar="EAN",
        default=None,
        help="Return only products matching this exact EAN barcode.",
    )

    # --- concurrency / retry options ---
    perf_group = parser.add_argument_group("performance options")
    perf_group.add_argument(
        "--batch-size",
        type=int,
        default=100,
        metavar="N",
        help=(
            "Products per paginated API request. "
            "Higher values reduce round-trips but may be rate-limited. Default: 100."
        ),
    )
    perf_group.add_argument(
        "--max-concurrent",
        type=int,
        default=15,
        metavar="N",
        help="Maximum concurrent API requests per store/branch. Default: 15.",
    )
    perf_group.add_argument(
        "--retry-limit",
        type=int,
        default=3,
        metavar="N",
        help="Maximum retry attempts per failed request. Default: 3.",
    )
    perf_group.add_argument(
        "--base-retry-delay",
        type=float,
        default=1.0,
        metavar="SECS",
        help=(
            "Base delay (seconds) for exponential backoff on retries. "
            "Delay = base * 2^attempt, capped at 30s. Default: 1.0."
        ),
    )

    # --- per-chain branch/store selection ---
    chain_group = parser.add_argument_group("chain-specific options")
    chain_group.add_argument(
        "--tivtaam-branches",
        nargs="+",
        type=str,
        metavar="ID_OR_NAME",
        default=None,
        help=(
            "Specific Tiv Taam branch IDs or Hebrew name substrings (e.g. 924 929 or ירושלים). "
            "Use --list-branches to see all IDs. Default: all."
        ),
    )
    chain_group.add_argument(
        "--carrefour-branches",
        nargs="+",
        type=str,
        metavar="ID_OR_NAME",
        default=None,
        help=(
            "Specific Carrefour branch IDs or Hebrew name substrings (e.g. 3003 or תל אביב). "
            "Use --list-branches to see all IDs. Default: all."
        ),
    )
    chain_group.add_argument(
        "--machsanei-branches",
        nargs="+",
        type=str,
        metavar="ID_OR_NAME",
        default=None,
        help=(
            "Specific Machsanei HaShook branch IDs or Hebrew name substrings (e.g. 836 or באר שבע). "
            "Use --list-branches to see all IDs. Default: all."
        ),
    )
    chain_group.add_argument(
        "--yochananof-stores",
        nargs="+",
        metavar="STORE_CODE",
        default=None,
        help="Specific Yochananof store codes (e.g. s82 s63). Default: all.",
    )
    chain_group.add_argument(
        "--ramilevi-stores",
        nargs="+",
        type=str,
        metavar="ID_OR_NAME",
        default=None,
        help=(
            "Specific Rami Levy store IDs or Hebrew name substrings (e.g. 1332 or מודיעין). "
            "Use --list-branches to see all IDs. Default: all."
        ),
    )
    chain_group.add_argument(
        "--keshet-branches",
        nargs="+",
        type=str,
        metavar="ID_OR_NAME",
        default=None,
        help=(
            "Specific Keshet Teamim branch IDs or Hebrew name substrings. "
            "Use --list-branches to see all IDs. Default: all."
        ),
    )
    chain_group.add_argument(
        "--quik-branches",
        nargs="+",
        type=str,
        metavar="ID_OR_NAME",
        default=None,
        help=(
            "Specific Quik branch IDs or Hebrew name substrings. "
            "Use --list-branches to see all IDs. Default: all."
        ),
    )
    chain_group.add_argument(
        "--victory-branches",
        nargs="+",
        type=str,
        metavar="ID_OR_NAME",
        default=None,
        help=(
            "Specific Victory branch IDs or Hebrew name substrings. "
            "Use --list-branches to see all IDs. Default: all."
        ),
    )
    chain_group.add_argument(
        "--ybitan-branches",
        nargs="+",
        type=str,
        metavar="ID_OR_NAME",
        default=None,
        help=(
            "Specific Yenot Bitan branch IDs or Hebrew name substrings. "
            "Use --list-branches to see all IDs. Default: all."
        ),
    )

    # --- output options ---
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default=None,
        help=(
            "Directory to save JSON result files. Subdirectories per supermarket "
            "are created automatically. If omitted, results are only logged."
        ),
    )

    # --- logging options ---
    parser.add_argument(
        "--log-file",
        metavar="FILE",
        default=None,
        help="Path to a log file (always written at DEBUG level).",
    )
    parser.add_argument(
        "--log-level",
        metavar="LEVEL",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log level (default: INFO).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress console output (only log to file if --log-file is set).",
    )

    return parser


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def async_main(args: argparse.Namespace) -> Dict[str, ScrapeResult]:
    output_dir = Path(args.output_dir) if args.output_dir else None
    supermarkets = set(args.supermarkets)

    # Build ScrapeFilter from CLI args
    flt: ScrapeFilter = {}
    if args.filter_name:
        flt["name_query"] = args.filter_name
    if args.filter_category:
        flt["category_ids"] = [args.filter_category]
    if args.filter_barcode:
        flt["barcode"] = args.filter_barcode

    # Performance params
    batch_size: int = args.batch_size
    max_concurrent: int = args.max_concurrent
    max_retries: int = args.retry_limit
    base_retry_delay: float = args.base_retry_delay

    session_start = datetime.now()
    logger.info(
        "=== Scraping session started at %s ===",
        session_start.strftime("%Y-%m-%d %H:%M:%S"),
    )
    logger.info("Supermarkets: %s", ", ".join(sorted(supermarkets)))
    if flt:
        logger.info("Filters: %s", flt)
    logger.info(
        "Performance: batch_size=%d max_concurrent=%d retry_limit=%d base_delay=%.1fs",
        batch_size,
        max_concurrent,
        max_retries,
        base_retry_delay,
    )

    # Build coroutines for every requested chain
    chain_coros: Dict[str, Any] = {}

    if "tivtaam" in supermarkets:
        chain_coros["tivtaam"] = run_tivtaam(
            branch_ids=args.tivtaam_branches,
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    if "shufersal" in supermarkets:
        chain_coros["shufersal"] = run_shufersal(
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    if "yochananof" in supermarkets:
        chain_coros["yochananof"] = run_yochananof(
            store_codes=args.yochananof_stores,
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    if "carrefour" in supermarkets:
        chain_coros["carrefour"] = run_carrefour(
            branch_ids=args.carrefour_branches,
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    if "machsanei" in supermarkets:
        chain_coros["machsanei"] = run_machsanei(
            branch_ids=args.machsanei_branches,
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    if "ramilevi" in supermarkets:
        chain_coros["ramilevi"] = run_ramilevi(
            store_ids=args.ramilevi_stores,
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    if "keshet" in supermarkets:
        chain_coros["keshet"] = run_zuz_chain(
            chain_name="keshet",
            module_path="scrapers.keshet.keshet",
            branch_ids=args.keshet_branches,
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    if "quik" in supermarkets:
        chain_coros["quik"] = run_zuz_chain(
            chain_name="quik",
            module_path="scrapers.quik.quik",
            branch_ids=args.quik_branches,
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    if "victory" in supermarkets:
        chain_coros["victory"] = run_zuz_chain(
            chain_name="victory",
            module_path="scrapers.victory.victory",
            branch_ids=args.victory_branches,
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    if "ybitan" in supermarkets:
        chain_coros["ybitan"] = run_zuz_chain(
            chain_name="ybitan",
            module_path="scrapers.ybitan.ybitan",
            branch_ids=args.ybitan_branches,
            flt=flt,
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_retries=max_retries,
            base_retry_delay=base_retry_delay,
            output_dir=output_dir,
        )

    # Run all chains in parallel using asyncio.TaskGroup
    chain_names = list(chain_coros.keys())
    all_results: Dict[str, ScrapeResult] = {}
    exceptions: Dict[str, Exception] = {}
    t0 = time.monotonic()

    async with asyncio.TaskGroup() as tg:
        tasks = {
            name: tg.create_task(coro, name=name) for name, coro in chain_coros.items()
        }

    elapsed_total = time.monotonic() - t0

    for name, task in tasks.items():
        exc = task.exception()
        if exc is not None:
            logger.error("%s scraper failed: %s", name.title(), exc, exc_info=exc)
            exceptions[name] = exc
        else:
            all_results[name] = task.result()

    logger.info("")
    logger.info("=== Scraping session complete — %.1fs total ===", elapsed_total)
    _print_summary_table(all_results, elapsed_total)

    # When no output directory is specified, dump all products as JSON to stdout
    if not output_dir:
        all_products = []
        for result in all_results.values():
            for products in result["products_by_store"].values():
                all_products.extend(products)
        if all_products:
            json.dump(all_products, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")

    return all_results


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    setup_logging(
        log_file=args.log_file,
        log_level=args.log_level,
        console=not args.quiet,
    )

    if args.list_branches:
        _print_branches_and_exit(supermarkets=list(args.supermarkets))

    if args.update_branches:
        try:
            asyncio.run(_update_branches_and_exit(supermarkets=list(args.supermarkets)))
        except KeyboardInterrupt:
            sys.exit(0)

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(0)
    except Exception as exc:
        logger.critical("Fatal error: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
