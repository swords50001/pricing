# Pricing Lookup Model

This repository contains a lightweight client for batch searching clothing prices by brand and item title. Instead of relying on static CSV data, the client reaches out to an online product search API (DummyJSON by default) and extracts pricing information for each query.

## Project layout

```
pricing/            # Python package with the core model and CLI helpers
├── __init__.py
├── cli.py          # `python -m pricing.cli` entry point
└── model.py        # ClothingPriceModel implementation

examples/
└── queries.csv     # Example queries for testing the CLI
```

## Usage

Create a virtual environment (optional but recommended) and run the CLI with the provided sample data. The command issues HTTP requests to the configured search API, so ensure you have internet access:

```
python -m pricing.cli examples/queries.csv
```

The command prints a JSON array describing the best match (if any) for each input row. Use the `--output` flag to write the data to disk.

To customise the remote endpoint, request size, or timeout, use the optional flags:

```
python -m pricing.cli my_queries.csv --base-url https://dummyjson.com/products/search --limit 15 --timeout 5
```

The query CSV must contain headers with `brand` and `title` columns.

## Library usage

You can also integrate the model directly into Python code:

```python
from pricing.model import ClothingPriceModel

model = ClothingPriceModel()
queries = [("Nike", "Pegasus 40"), ("Zara", "Wide Leg Jeans")]
results = model.batch_search(queries)
for result in results:
    print(result)
```

Each result is either `None` (no confident match) or a `SearchResult` containing the matching brand, title, price, and similarity score.

## Running tests

Install the dev dependency `pytest` and execute:

```
pip install pytest
pytest
```
