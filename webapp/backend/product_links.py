from __future__ import annotations

from urllib.parse import quote


STORAI_PRODUCT_DOMAINS = {
    "carrefour": "https://www.carrefour.co.il",
    "tivtaam": "https://www.tivtaam.co.il",
    "machsanei": "https://www.mck.co.il",
    "keshet": "https://www.keshet-teamim.co.il",
    "quik": "https://www.quik.co.il",
    "victory": "https://www.victoryonline.co.il",
    "ybitan": "https://www.ybitan.co.il",
}


def build_product_url(
    chain: str,
    product_id: str | None,
    barcode: str | None = None,
    name: str | None = None,
) -> str | None:
    product_id_value = (product_id or "").strip()
    barcode_value = (barcode or "").strip()
    name_value = (name or "").strip()

    if chain in STORAI_PRODUCT_DOMAINS and product_id_value:
        return f"{STORAI_PRODUCT_DOMAINS[chain]}/?catalogProduct={quote(product_id_value)}"

    if chain == "shufersal" and product_id_value:
        return f"https://www.shufersal.co.il/online/he/p/{quote(product_id_value)}"

    if chain == "ramilevi":
        if barcode_value:
            encoded = quote(barcode_value)
            return f"https://www.rami-levy.co.il/he/online/search?q={encoded}&item={encoded}"
        if name_value:
            return f"https://www.rami-levy.co.il/he/online/search?q={quote(name_value)}"

    if chain == "yochananof":
        query = barcode_value or name_value
        if query:
            return f"https://www.yochananof.co.il/category?search={quote(query)}"

    return None
