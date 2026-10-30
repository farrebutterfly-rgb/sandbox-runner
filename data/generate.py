"""Regenerate data/orders.csv (dataset orders-v1). Deterministic: same seed, same bytes.

Run: python3 data/generate.py
"""
import csv
import io
import random
from pathlib import Path

SEED = 6
REGIONS = ["north", "south", "east", "west"]
PRODUCTS = {"bracket": 42.50, "hinge": 18.00, "rail": 97.25, "clamp": 11.75}


def rows():
    rng = random.Random(SEED)
    for i in range(1, 61):
        product = rng.choice(sorted(PRODUCTS))
        yield {
            "order_id": f"ORD-{i:03d}",
            "region": rng.choice(REGIONS),
            "product": product,
            "units": rng.randint(1, 40),
            "unit_price": f"{PRODUCTS[product]:.2f}",
        }


def render() -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["order_id", "region", "product", "units", "unit_price"], lineterminator="\n")
    w.writeheader()
    w.writerows(rows())
    return buf.getvalue()


if __name__ == "__main__":
    out = Path(__file__).with_name("orders.csv")
    out.write_text(render())
    print(f"wrote {out}")
