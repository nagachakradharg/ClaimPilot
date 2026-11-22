"""Mock body-shop estimating (served via the MCP `get_repair_estimates` tool).

Deterministic price book: parts + labor per damage area, scaled by severity and vehicle class.
Two shops quote; settlement uses the lower quote, as adjusters typically do.
"""

from __future__ import annotations

from pydantic import BaseModel

# (keyword, part label, parts USD, labor hours) — first keyword match wins, order matters
PRICE_BOOK: list[tuple[str, str, float, float]] = [
    ("chip", "Windshield chip repair", 40.0, 0.5),
    ("windshield", "Windshield replacement", 320.0, 1.5),
    ("bumper", "Bumper cover + reinforcement", 520.0, 4.0),
    ("hood", "Hood panel", 680.0, 4.5),
    ("fender", "Fender panel", 450.0, 4.0),
    ("quarter panel", "Quarter panel section", 700.0, 7.0),
    ("door", "Door shell + refinish", 750.0, 5.0),
    ("roof", "Roof skin / PDR", 900.0, 8.0),
    ("trunk", "Trunk lid", 600.0, 4.0),
    ("hatch", "Liftgate / hatch", 820.0, 4.5),
    ("liftgate", "Liftgate / hatch", 820.0, 4.5),
    ("rocker", "Rocker panel", 380.0, 5.0),
    ("headlight", "Headlamp assembly", 480.0, 1.0),
    ("taillight", "Tail lamp assembly", 260.0, 0.8),
    ("tail light", "Tail lamp assembly", 260.0, 0.8),
    ("mirror", "Side mirror assembly", 220.0, 1.0),
    ("grille", "Grille", 260.0, 1.0),
    ("radiator", "Radiator + coolant", 520.0, 2.5),
    ("tire", "Tire", 190.0, 0.5),
    ("audio", "Audio head unit", 600.0, 1.5),
    ("dashboard", "Dashboard assembly", 1400.0, 8.0),
    ("engine", "Engine bay components", 3800.0, 12.0),
    ("interior", "Interior water remediation", 2600.0, 14.0),
    ("transmission", "Transmission", 3200.0, 10.0),
]
LABOR_RATE = 62.0  # USD / hour
SEVERITY_FACTOR = {"low": 0.6, "medium": 1.0, "high": 1.8}
CLASS_FACTOR = {"sedan": 1.0, "sport": 1.35, "utility": 1.2}
LUXURY_MAKES = {"BMW", "Mercedes-Benz", "Jaguar", "Porsche", "Ferrari", "Lexus", "Acura", "Saab"}
SHOPS = [("Northside Collision", 1.0), ("Precision Auto Body", 1.08)]


class LineItem(BaseModel):
    damage_area: str
    description: str
    parts: float
    labor: float
    total: float


class ShopQuote(BaseModel):
    shop: str
    total: float
    line_items: list[LineItem]


class RepairEstimates(BaseModel):
    quotes: list[ShopQuote]
    lowest_total: float
    unpriced_areas: list[str]
    chip_repair_only: bool


def _price(area: str) -> tuple[str, float, float] | None:
    a = area.lower()
    for keyword, label, parts, hours in PRICE_BOOK:
        if keyword in a:
            return label, parts, hours * LABOR_RATE
    return None


def estimate_repairs(
    damage_areas: list[str], severity: str, vehicle_category: str, make: str
) -> RepairEstimates:
    factor = SEVERITY_FACTOR.get(severity, 1.0) * CLASS_FACTOR.get(vehicle_category.lower(), 1.0)
    if make in LUXURY_MAKES:
        factor *= 1.3
    priced, unpriced, seen_parts = [], [], set()
    for area in damage_areas:
        p = _price(area)
        if p is None:
            unpriced.append((area, p))
        elif p[0] not in seen_parts:  # "rear bumper" + "rear bumper cover" = one bumper
            seen_parts.add(p[0])
            priced.append((area, p))
    quotes = []
    for shop, shop_factor in SHOPS:
        items = [
            LineItem(
                damage_area=area,
                description=label,
                parts=round(parts * factor * shop_factor, 2),
                labor=round(labor * factor * shop_factor, 2),
                total=round((parts + labor) * factor * shop_factor, 2),
            )
            for area, (label, parts, labor) in priced
        ]
        quotes.append(
            ShopQuote(shop=shop, total=round(sum(i.total for i in items), 2), line_items=items)
        )
    return RepairEstimates(
        quotes=quotes,
        lowest_total=min(q.total for q in quotes),
        unpriced_areas=[a for a, _ in unpriced],
        chip_repair_only=bool(priced) and all("chip" in a.lower() for a, _ in priced),
    )
