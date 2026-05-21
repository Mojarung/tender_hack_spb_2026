"""Wildberries basket-CDN shard resolution.

The shard index `XX` is a function of `nm_id // 100000`. Wildberries adds new
shards as the catalog grows, so the table needs occasional extension. If a
shard returns 404, increment XX or fall back to the `images.wbstatic.net`
legacy CDN.

Reference: github.com/Duff89/wildberries_parser + Wildberries Habr posts.
"""

_RANGES: tuple[tuple[int, str], ...] = (
    (143, "01"), (287, "02"), (431, "03"), (719, "04"),
    (1007, "05"), (1061, "06"), (1115, "07"), (1169, "08"),
    (1313, "09"), (1601, "10"), (1655, "11"), (1919, "12"),
    (2045, "13"), (2189, "14"), (2405, "15"), (2621, "16"),
    (2837, "17"), (3053, "18"), (3269, "19"), (3485, "20"),
)


def basket_for(nm_id: int) -> str:
    """Return basket shard `XX` (string) for given Wildberries nm_id."""
    s = nm_id // 100_000
    for upper, basket in _RANGES:
        if s <= upper:
            return basket
    return "21"   # extrapolation; verify with HEAD if 404


def image_url(nm_id: int, idx: int = 1) -> str:
    """Big product image URL on basket CDN."""
    basket = basket_for(nm_id)
    vol = nm_id // 100_000
    part = nm_id // 1_000
    return (
        f"https://basket-{basket}.wbbasket.ru"
        f"/vol{vol}/part{part}/{nm_id}/images/big/{idx}.webp"
    )


def price_history_url(nm_id: int) -> str:
    """Multi-year price history JSON (kopecks). Returns 404 for some items."""
    return f"https://wbx-content-v2.wbstatic.net/price-history/{nm_id}.json"
