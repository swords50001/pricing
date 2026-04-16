# Pricing Lookup Model

This repository contains a lightweight client for batch searching clothing prices by brand and item title.  The tool **crawls the open web** by default: it runs a DuckDuckGo search for each query, fetches the top product pages, and extracts prices from schema.org JSON-LD or Open Graph meta tags — no API key required.

## Project layout

```
pricing/            # Python package with the core model and CLI helpers
├── __init__.py
├── cli.py          # `python -m pricing.cli` entry point
├── model.py        # ClothingPriceModel implementation
└── portal.py       # `python -m pricing.portal` web interface

examples/
└── queries.csv     # Example queries for testing the CLI
```

## Usage

Create a virtual environment (optional but recommended) and run the CLI with the provided sample data.  The command issues HTTP requests across the open web, so ensure you have internet access:

```
python -m pricing.cli examples/queries.csv
```

The tool searches DuckDuckGo for each `brand + title`, visits the top result pages, and extracts prices automatically.  Results are printed as a JSON array.  Use `--output` to write results to disk.

### Restricting to specific stores (optional)

Pass `--domain` to narrow the search to one or more stores:

```
python -m pricing.cli examples/queries.csv \
  --domain www.nordstrom.com \
  --domain www.zappos.com
```

Or point at a plain-text file with one domain per line:

```
python -m pricing.cli examples/queries.csv --domains-file my_stores.txt
```

When `--domain` / `--domains-file` is provided, each DuckDuckGo query includes a `site:` restriction so only pages from those stores are visited.

### Adjusting request settings

```
python -m pricing.cli examples/queries.csv --limit 15 --timeout 5
```

`--limit` controls how many result pages are fetched per query.

### Legacy single-endpoint mode

If you still need to query a single JSON product-search API (e.g. DummyJSON), pass `--base-url`.  This disables web crawling entirely:

```
python -m pricing.cli my_queries.csv --base-url https://dummyjson.com/products/search
```

> **Note:** `--base-url` is deprecated.  Prefer the default web-search mode.

The query CSV must contain headers with `brand` and `title` columns.

## Web portal

Launch a lightweight upload portal to process CSV files without touching the command line:

```
python -m pricing.portal --port 8000
```

Open `http://127.0.0.1:8000` in your browser, supply the CSV containing `brand` and `title` columns, and press **Find Prices**.
The portal will call the same search backend as the CLI, show the matched prices in a table, and provide a download link for a fresh
`brand,title,price` CSV.

## Library usage

```python
from pricing.model import ClothingPriceModel

# Default: open-web search (no domain restriction)
model = ClothingPriceModel()
queries = [("Nike", "Pegasus 40"), ("Zara", "Wide Leg Jeans")]
results = model.batch_search(queries)
for result in results:
    print(result)
```

To restrict to specific stores:

```python
model = ClothingPriceModel(domains=["www.zappos.com", "www.nordstrom.com"])
```

To use the legacy single-endpoint JSON API:

```python
model = ClothingPriceModel(web_search=False, base_url="https://dummyjson.com/products/search")
```

Each result is either `None` (no confident match) or a `SearchResult` containing the matching brand, title, price, and similarity score.

## Running tests

Install the dev dependency `pytest` and execute:

```
pip install pytest
pytest
```

## Deploying to AWS Lambda with Docker

The repository includes a `Dockerfile` that targets the AWS Lambda Python 3.11 base
image. Build the container and test it locally with the Lambda Runtime Interface
Emulator:

```bash
docker build -t pricing-lambda .
docker run -p 9000:8080 pricing-lambda
```

Invoke the function by sending an event to the emulator. Each query object must
provide `brand` and `title` fields, and the handler returns a JSON structure with
price lookup results in the same order:

```bash
curl "http://127.0.0.1:9000/2015-03-31/functions/function/invocations" \
  -d '{
        "queries": [
          {"brand": "Nike", "title": "Pegasus 40"},
          {"brand": "Adidas", "title": "Ultraboost"}
        ]
      }'
```

You can optionally include `base_url`, `limit`, `timeout`, or `min_score` keys in
the JSON payload to override the default search configuration before pushing the
image to Amazon ECR and deploying it to Lambda.
