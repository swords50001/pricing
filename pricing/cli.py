"""Command line utilities for batch price lookup."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .model import ClothingPriceModel, SearchResult


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Batch search clothing prices by brand and title. "
            "By default the tool searches the open web via DuckDuckGo and extracts "
            "prices from product pages using schema.org structured data."
        )
    )
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

    filter_group = parser.add_argument_group(
        "domain filter (optional)",
        "Restrict the web search to specific store domains. "
        "When omitted the search covers the entire open web.",
    )
    filter_group.add_argument(
        "--domain",
        dest="domains",
        action="append",
        metavar="DOMAIN",
        help=(
            "Restrict results to this store domain (e.g. www.nordstrom.com). "
            "May be repeated to include multiple stores."
        ),
    )
    filter_group.add_argument(
        "--domains-file",
        type=Path,
        metavar="FILE",
        help="Path to a text file with one store domain per line.",
    )

    legacy_group = parser.add_argument_group(
        "legacy single-endpoint mode",
        "Query a single JSON product-search API instead of crawling the web. "
        "Deprecated: prefer the default web-search mode.",
    )
    legacy_group.add_argument(
        "--base-url",
        default=None,
        metavar="URL",
        help=(
            "[Deprecated] Remote product search endpoint that returns a JSON "
            "``products`` list. Disables web crawling when supplied."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of product pages to fetch per query.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Timeout (in seconds) for each HTTP request.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write JSON results. If omitted results are printed to stdout.",
    )
    return parser.parse_args(argv)


def _resolve_domains(args: argparse.Namespace) -> Optional[List[str]]:
    """Return an explicit domain filter list, or *None* for unrestricted search."""
    domains: List[str] = args.domains or []

    if args.domains_file is not None:
        try:
            for line in args.domains_file.read_text(encoding="utf-8").splitlines():
                domain = line.strip()
                if domain and not domain.startswith("#"):
                    domains.append(domain)
        except OSError as exc:
            raise SystemExit(f"Cannot read domains file: {exc}") from exc

    return domains if domains else None


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

    if args.base_url is not None:
        # Legacy single-endpoint mode
        model = ClothingPriceModel(
            base_url=args.base_url,
            web_search=False,
            limit=args.limit,
            timeout=args.timeout,
        )
    else:
        # Web-search mode (default): optionally filtered to specific domains
        model = ClothingPriceModel(
            domains=_resolve_domains(args),
            web_search=True,
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
