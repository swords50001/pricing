"""Command line utilities for batch price lookup."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable, List, Tuple

from .model import ClothingPriceModel, SearchResult


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch search clothing prices by brand and title")
    parser.add_argument(
        "queries",
        type=Path,
        help="Path to a CSV file containing brand,title columns for the queries",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.45,
        help="Minimum score threshold required to return a price match. Lower returns more results but with lower confidence.",
    )
    parser.add_argument(
        "--base-url",
        default="https://dummyjson.com/products/search",
        help="Remote product search endpoint that will be queried for prices.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of products to request from the search API per query.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Timeout (in seconds) for each HTTP request to the product search API.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write JSON results. If omitted results are printed to stdout.",
    )
    return parser.parse_args(argv)


def load_queries(path: Path) -> List[Tuple[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required_columns = {"brand", "title"}
        missing = required_columns - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Query file is missing required columns: {', '.join(sorted(missing))}")
        return [
            (row["brand"].strip(), row["title"].strip())
            for row in reader
            if row.get("brand") and row.get("title")
        ]


def results_to_dict(results: List[SearchResult | None]) -> List[dict]:
    serialised = []
    for result in results:
        if result is None:
            serialised.append({"brand": None, "title": None, "price": None, "score": None})
        else:
            serialised.append(
                {
                    "brand": result.brand,
                    "title": result.title,
                    "price": result.price,
                    "score": round(result.score, 3),
                }
            )
    return serialised


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    queries = load_queries(args.queries)
    if not queries:
        raise SystemExit("No queries loaded from the provided file")

    model = ClothingPriceModel(
        base_url=args.base_url,
        limit=args.limit,
        timeout=args.timeout,
    )
    try:
        results = model.batch_search(queries, min_score=args.min_score)
    finally:
        model.close()
    payload = results_to_dict(results)
    output_text = json.dumps(payload, indent=2)

    if args.output:
        args.output.write_text(output_text, encoding="utf-8")
    else:
        print(output_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
