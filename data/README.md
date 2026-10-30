# Data provenance

`orders.csv` is synthetic. It was generated for this repo on 2026-10-30 by `data/generate.py` with a fixed seed (6), so running the script again gives the same bytes. Dataset version: `orders-v1`.

- 60 invented orders: `order_id`, `region` (north, south, east, west), `product`, `units`, `unit_price`.
- Product names and prices are made up. No customer, person or company appears in the file.

The file is copied into the sandbox's fresh work directory for each run. It is the only data the sandboxed code can see.
