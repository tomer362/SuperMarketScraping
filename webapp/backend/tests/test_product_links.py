from __future__ import annotations

from product_links import build_product_url


def test_builds_storai_product_url_from_catalog_product_id():
    assert (
        build_product_url("carrefour", "8349652", "7290004131074", "חלב")
        == "https://www.carrefour.co.il/?catalogProduct=8349652"
    )


def test_builds_shufersal_short_product_url():
    assert (
        build_product_url("shufersal", "P_4131074", "7290004131074", "חלב")
        == "https://www.shufersal.co.il/online/he/p/P_4131074"
    )


def test_builds_rami_levy_detail_url_from_barcode():
    assert (
        build_product_url("ramilevi", "3025", "7290004131074", "חלב")
        == "https://www.rami-levy.co.il/he/online/search?q=7290004131074&item=7290004131074"
    )


def test_builds_yochananof_search_url_from_barcode():
    assert (
        build_product_url("yochananof", "5285", "7290004131074", "חלב")
        == "https://www.yochananof.co.il/category?search=7290004131074"
    )
