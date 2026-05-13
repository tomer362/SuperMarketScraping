from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChainDefinition:
    key: str
    label: str
    scraper_module: str
    enabled: bool = True
    status: str = "active"
    unavailable_reason: str | None = None
    accent: str = "slate"


CHAIN_DEFINITIONS: list[ChainDefinition] = [
    ChainDefinition(
        key="shufersal",
        label="שופרסל",
        scraper_module="scrapers.shufersal.shufersal",
        accent="rose",
    ),
    ChainDefinition(
        key="tivtaam",
        label="טיב טעם",
        scraper_module="scrapers.tivtaam.tivtaam",
        accent="orange",
    ),
    ChainDefinition(
        key="carrefour",
        label="קרפור",
        scraper_module="scrapers.carrefour.carrefour",
        accent="blue",
    ),
    ChainDefinition(
        key="machsanei",
        label="מחסני השוק",
        scraper_module="scrapers.machsanei_hashook.machsanei_hashook",
        accent="amber",
    ),
    ChainDefinition(
        key="ramilevi",
        label="רמי לוי",
        scraper_module="scrapers.ramilevi.ramilevi",
        accent="emerald",
    ),
    ChainDefinition(
        key="keshet",
        label="קשת טעמים",
        scraper_module="scrapers.keshet.keshet",
        accent="fuchsia",
    ),
    ChainDefinition(
        key="quik",
        label="קוויק",
        scraper_module="scrapers.quik.quik",
        accent="cyan",
    ),
    ChainDefinition(
        key="victory",
        label="ויקטורי",
        scraper_module="scrapers.victory.victory",
        accent="teal",
    ),
    ChainDefinition(
        key="ybitan",
        label="יינות ביתן",
        scraper_module="scrapers.ybitan.ybitan",
        accent="indigo",
    ),
    ChainDefinition(
        key="yochananof",
        label="יוחננוף",
        scraper_module="scrapers.yochananof.yochananof",
        accent="violet",
    ),
]

CHAIN_MAP = {chain.key: chain for chain in CHAIN_DEFINITIONS}


def get_chain_definition(chain_key: str) -> ChainDefinition:
    return CHAIN_MAP[chain_key]


def iter_active_chains() -> list[ChainDefinition]:
    return [chain for chain in CHAIN_DEFINITIONS if chain.enabled]


def iter_public_chains() -> list[ChainDefinition]:
    return list(CHAIN_DEFINITIONS)
