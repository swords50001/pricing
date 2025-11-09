"""Simple web portal for uploading clothing queries and exporting price lookups."""
from __future__ import annotations

import argparse
import csv
import html
import io
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import cgi
from typing import Callable, Iterable, List, Optional, Tuple, Any
from urllib.parse import quote

# ---- NEW: FastAPI imports ----
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from .model import ClothingPriceModel, SearchResult
from mangum import Mangum


@dataclass
class PortalConfig:
    """Default configuration for the pricing portal."""

    base_url: str = "https://dummyjson.com/products/search"
    limit: int = 10
    timeout: float = 10.0
    min_score: float = 0.45


ModelFactory = Callable[[str, int, float], ClothingPriceModel]


def _default_model_factory(base_url: str, limit: int, timeout: float) -> ClothingPriceModel:
    return ClothingPriceModel(base_url=base_url, limit=limit, timeout=timeout)


# =============================================================================
# ORIGINAL http.server IMPLEMENTATION (kept for CLI/local use)
# =============================================================================
class _PricingPortalRequestHandler(BaseHTTPRequestHandler):
    """Request handler used by :func:`run` to serve the portal."""

    config: PortalConfig
    model_factory: ModelFactory

    def do_GET(self) -> None:  # noqa: N802
        self._render_page()

    def do_POST(self) -> None:  # noqa: N802
        if not self.headers.get("Content-Type"):
            self._render_page(error="Missing content type header on request.")
            return

        form = cgi.FieldStorage(  # type: ignore[call-arg]
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            },
        )

        form_values = _extract_form_values(form, self.config)
        if isinstance(form_values, str):
            self._render_page(error=form_values)
            return

        if "queries" not in form:
            self._render_page(
                error="Please choose a CSV file containing brand and title columns.",
                form_values=form_values,
            )
            return

        csv_field = form["queries"]
        if isinstance(csv_field, list):  # pragma: no cover - multiple files not expected
            csv_field = csv_field[0]

        file_handle = getattr(csv_field, "file", None)
        if file_handle is None:
            self._render_page(
                error="Please choose a CSV file containing brand and title columns.",
                form_values=form_values,
            )
            return

        try:
            data = file_handle.read()
        except Exception:  # pragma: no cover - defensive fallback
            self._render_page(
                error="Failed to read the uploaded CSV file.",
                form_values=form_values,
            )
            return

        queries_result = _parse_queries_bytes(data)
        if isinstance(queries_result, str):
            self._render_page(error=queries_result, form_values=form_values)
            return

        queries = queries_result
        if not queries:
            self._render_page(
                error="The uploaded CSV did not contain any brand/title rows.",
                form_values=form_values,
            )
            return

        try:
            model = type(self).model_factory(
                form_values["base_url"], form_values["limit"], form_values["timeout"]
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            self._render_page(
                error=f"Failed to initialise price model: {exc}",
                form_values=form_values,
            )
            return

        try:
            results = model.batch_search(queries, min_score=form_values["min_score"])
        finally:
            model.close()

        table_rows, csv_payload = _build_results_output(queries, results)
        download_href = f"data:text/csv;charset=utf-8,{quote(csv_payload)}"

        message = "Price lookup complete. Download the results below."
        self._render_page(
            success=message,
            form_values=form_values,
            table_rows=table_rows,
            csv_payload=csv_payload,
            download_href=download_href,
        )

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        """Silence the default stderr logging for cleaner console output."""
        return None

    def _render_page(
        self,
        *,
        error: Optional[str] = None,
        success: Optional[str] = None,
        form_values: Optional[dict] = None,
        table_rows: Optional[List[Tuple[str, str, str]]] = None,
        csv_payload: Optional[str] = None,
        download_href: Optional[str] = None,
    ) -> None:
        values = form_values or {
            "base_url": self.config.base_url,
            "limit": str(self.config.limit),
            "timeout": str(self.config.timeout),
            "min_score": str(self.config.min_score),
        }

        html_body = _render_template(
            config=self.config,
            values=values,
            error=error,
            success=success,
            table_rows=table_rows,
            csv_payload=csv_payload,
            download_href=download_href,
        )
        body_bytes = html_body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)


def _extract_form_values(form: cgi.FieldStorage, config: PortalConfig) -> dict | str:
    """Normalise form inputs, returning an error string on failure."""

    def _get(name: str, default: str) -> str:
        value = form.getfirst(name)
        if value is None:
            return default
        return str(value).strip() or default

    base_url = _get("base_url", config.base_url)

    def _parse_int(name: str, default: int, minimum: int) -> int | str:
        raw = _get(name, str(default))
        try:
            value = int(raw)
        except ValueError:
            return f"{name.replace('_', ' ').title()} must be an integer."
        if value < minimum:
            return f"{name.replace('_', ' ').title()} must be at least {minimum}."
        return value

    def _parse_float(
        name: str,
        default: float,
        minimum: float,
        *,
        inclusive_minimum: bool = False,
        maximum: Optional[float] = None,
    ) -> float | str:
        raw = _get(name, str(default))
        try:
            value = float(raw)
        except ValueError:
            return f"{name.replace('_', ' ').title()} must be a number."
        if inclusive_minimum:
            if value < minimum:
                return f"{name.replace('_', ' ').title()} must be at least {minimum}."
        else:
            if value <= minimum:
                comparator = "greater than" if minimum == 0 else f"greater than {minimum}"
                return f"{name.replace('_', ' ').title()} must be {comparator}."
        if maximum is not None and value > maximum:
            return f"{name.replace('_', ' ').title()} must be at most {maximum}."
        return value

    limit = _parse_int("limit", config.limit, 1)
    if isinstance(limit, str):
        return limit

    timeout = _parse_float("timeout", config.timeout, 0.0)
    if isinstance(timeout, str):
        return timeout

    min_score = _parse_float(
        "min_score",
        config.min_score,
        0.0,
        inclusive_minimum=True,
        maximum=1.0,
    )
    if isinstance(min_score, str):
        return min_score

    return {
        "base_url": base_url,
        "limit": limit,
        "timeout": timeout,
        "min_score": min_score,
    }


def _parse_queries_bytes(data: bytes) -> List[Tuple[str, str]] | str:
    """Parse uploaded CSV bytes into a list of query tuples."""

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            return "Unable to decode the uploaded CSV. Please use UTF-8 encoding."

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return "Uploaded CSV is missing a header row with brand,title columns."

    required = {"brand", "title"}
    missing = required - {name.strip().lower() for name in reader.fieldnames}
    if missing:
        return "Uploaded CSV is missing required columns: brand,title."

    queries: List[Tuple[str, str]] = []
    for row in reader:
        brand = (row.get("brand") or "").strip()
        title = (row.get("title") or "").strip()
        if brand and title:
            queries.append((brand, title))
    return queries


def _build_results_output(
    queries: Iterable[Tuple[str, str]],
    results: Iterable[Optional[SearchResult]],
) -> Tuple[List[Tuple[str, str, str]], str]:
    rows: List[Tuple[str, str, str]] = []
    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["brand", "title", "price"])

    for (query_brand, query_title), result in zip(queries, results):
        if result is None:
            display_brand = query_brand
            display_title = query_title
            price_text = "Not found"
            csv_price = ""
        else:
            display_brand = result.brand
            display_title = result.title
            price_text = f"${result.price:,.2f}"
            csv_price = f"{result.price:.2f}"
        rows.append((display_brand, display_title, price_text))
        writer.writerow([display_brand, display_title, csv_price])

    return rows, csv_buffer.getvalue()


def _render_template(
    *,
    config: PortalConfig,
    values: dict,
    error: Optional[str],
    success: Optional[str],
    table_rows: Optional[List[Tuple[str, str, str]]],
    csv_payload: Optional[str],
    download_href: Optional[str],
) -> str:
    def _escape(value: object) -> str:
        return html.escape(str(value), quote=True)

    rows_html = ""
    if table_rows:
        row_fragments = []
        for brand, title, price in table_rows:
            row_fragments.append(
                "<tr>"
                f"<td>{_escape(brand)}</td>"
                f"<td>{_escape(title)}</td>"
                f"<td>{_escape(price)}</td>"
                "</tr>"
            )
        rows_html = "\n".join(row_fragments)

    message_html = ""
    if error:
        message_html = (
            f'<div class="message error">{_escape(error)}</div>'
        )
    elif success:
        message_html = (
            f'<div class="message success">{_escape(success)}</div>'
        )

    table_html = ""
    download_html = ""
    csv_preview_html = ""
    if table_rows:
        table_html = (
            "<table class=\"results\">"
            "<thead><tr><th>Brand</th><th>Title</th><th>Price</th></tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            "</table>"
        )
        if download_href:
            download_html = (
                f'<a class="button" download="pricing-results.csv" href="{_escape(download_href)}">'
                "Download CSV"
                "</a>"
            )
        if csv_payload:
            csv_preview_html = (
                "<h2>CSV Preview</h2>"
                f"<textarea readonly rows=\"8\">{_escape(csv_payload)}</textarea>"
            )

    return f"""
<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>Clothing Price Lookup Portal</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2rem auto; max-width: 900px; color: #1f2933; }}
    h1 {{ margin-bottom: 1rem; }}
    form {{ background: #f8fafc; border: 1px solid #d2d6dc; padding: 1.5rem; border-radius: 8px; }}
    label {{ display: block; margin-bottom: 0.5rem; font-weight: 600; }}
    input[type="text"], input[type="number"] {{ width: 100%; padding: 0.5rem; border: 1px solid #cbd5e0; border-radius: 4px; margin-bottom: 1rem; }}
    input[type="file"] {{ margin-bottom: 1rem; }}
    button {{ background: #2563eb; color: white; border: none; padding: 0.75rem 1.5rem; border-radius: 4px; cursor: pointer; }}
    button:hover {{ background: #1d4ed8; }}
    .message {{ margin: 1rem 0; padding: 0.75rem 1rem; border-radius: 4px; }}
    .message.error {{ background: #fee2e2; color: #991b1b; border: 1px solid #fecaca; }}
    .message.success {{ background: #dcfce7; color: #166534; border: 1px solid #bbf7d0; }}
    table.results {{ width: 100%; border-collapse: collapse; margin-top: 1.5rem; }}
    table.results th, table.results td {{ border: 1px solid #e5e7eb; padding: 0.75rem; text-align: left; }}
    table.results thead {{ background: #f1f5f9; }}
    .button {{ display: inline-block; margin-top: 1rem; padding: 0.75rem 1.5rem; background: #059669; color: white; text-decoration: none; border-radius: 4px; }}
    .button:hover {{ background: #047857; }}
    textarea {{ width: 100%; font-family: monospace; margin-top: 0.5rem; }}
  </style>
</head>
<body>
  <h1>Clothing Price Lookup Portal</h1>
  <p>Upload a CSV containing <code>brand</code> and <code>title</code> columns to fetch live pricing data.</p>
  {message_html}
  <form method=\"post\" enctype=\"multipart/form-data\" action=\"/upload\">
    <label for=\"base_url\">Product search API URL</label>
    <input id=\"base_url\" name=\"base_url\" type=\"text\" value=\"{_escape(values['base_url'])}\" />
    <label for=\"limit\">Results per request</label>
    <input id=\"limit\" name=\"limit\" type=\"number\" min=\"1\" value=\"{_escape(values['limit'])}\" />
    <label for=\"timeout\">Request timeout (seconds)</label>
    <input id=\"timeout\" name=\"timeout\" type=\"number\" min=\"1\" step=\"0.1\" value=\"{_escape(values['timeout'])}\" />
    <label for=\"min_score\">Minimum match score (0-1)</label>
    <input id=\"min_score\" name=\"min_score\" type=\"number\" min=\"0\" max=\"1\" step=\"0.05\" value=\"{_escape(values['min_score'])}\" />
    <label for=\"queries\">Upload CSV file</label>
    <input id=\"queries\" name=\"queries\" type=\"file\" accept=\".csv,text/csv\" required />
    <button type=\"submit\">Find Prices</button>
  </form>
  {table_html}
  {download_html}
  {csv_preview_html}
</body>
</html>
"""


def _build_handler(config: PortalConfig, model_factory: ModelFactory) -> type[_PricingPortalRequestHandler]:
    handler_class = _PricingPortalRequestHandler
    handler_class.config = config
    handler_class.model_factory = staticmethod(model_factory)  # type: ignore[assignment]
    return handler_class


def run(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    config: Optional[PortalConfig] = None,
    model_factory: ModelFactory = _default_model_factory,
) -> None:
    """Run the pricing portal HTTP server until interrupted."""

    active_config = config or PortalConfig()
    handler = _build_handler(active_config, model_factory)
    with ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"Serving pricing portal on http://{host}:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down portal...")


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the clothing price lookup portal")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind the portal server to.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind the portal server to.")
    parser.add_argument(
        "--base-url",
        default=PortalConfig.base_url,
        help="Remote product search endpoint that will be queried for prices.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=PortalConfig.limit,
        help="Maximum number of products to request from the search API per query.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=PortalConfig.timeout,
        help="Timeout (in seconds) for each HTTP request to the product search API.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=PortalConfig.min_score,
        help="Minimum score threshold required to return a price match.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    config = PortalConfig(
        base_url=args.base_url,
        limit=args.limit,
        timeout=args.timeout,
        min_score=args.min_score,
    )
    run(host=args.host, port=args.port, config=config)
    return 0


# =============================================================================
# NEW: FastAPI ASGI app for Lambda/Web Adapter
# =============================================================================
app = FastAPI(title="Clothing Price Lookup Portal", version="1.0")

# CORS: open now; restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def form() -> HTMLResponse:
    # Render the bare form with defaults
    cfg = PortalConfig()
    html_text = _render_template(
        config=cfg,
        values={
            "base_url": cfg.base_url,
            "limit": str(cfg.limit),
            "timeout": str(cfg.timeout),
            "min_score": str(cfg.min_score),
        },
        error=None,
        success=None,
        table_rows=None,
        csv_payload=None,
        download_href=None,
    )
    return HTMLResponse(content=html_text)


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    base_url: str = Form(PortalConfig.base_url),
    limit: int = Form(PortalConfig.limit),
    timeout: float = Form(PortalConfig.timeout),
    min_score: float = Form(PortalConfig.min_score),
    queries: UploadFile = File(...),
) -> HTMLResponse:
    # Read uploaded CSV
    data = await queries.read()
    parsed = _parse_queries_bytes(data)
    if isinstance(parsed, str):
        # Render page with error message
        cfg = PortalConfig(base_url=base_url, limit=limit, timeout=timeout, min_score=min_score)
        html_text = _render_template(
            config=cfg,
            values={
                "base_url": base_url,
                "limit": str(limit),
                "timeout": str(timeout),
                "min_score": str(min_score),
            },
            error=parsed,
            success=None,
            table_rows=None,
            csv_payload=None,
            download_href=None,
        )
        return HTMLResponse(content=html_text)

    queries_list = parsed
    if not queries_list:
        cfg = PortalConfig(base_url=base_url, limit=limit, timeout=timeout, min_score=min_score)
        html_text = _render_template(
            config=cfg,
            values={
                "base_url": base_url,
                "limit": str(limit),
                "timeout": str(timeout),
                "min_score": str(min_score),
            },
            error="The uploaded CSV did not contain any brand/title rows.",
            success=None,
            table_rows=None,
            csv_payload=None,
            download_href=None,
        )
        return HTMLResponse(content=html_text)

    # Run model batch search
    model = _default_model_factory(base_url=base_url, limit=limit, timeout=timeout)
    try:
        results = model.batch_search(queries_list, min_score=min_score)
    finally:
        model.close()

    table_rows, csv_payload = _build_results_output(queries_list, results)
    download_href = f"data:text/csv;charset=utf-8,{quote(csv_payload)}"

    cfg = PortalConfig(base_url=base_url, limit=limit, timeout=timeout, min_score=min_score)
    html_text = _render_template(
        config=cfg,
        values={
            "base_url": base_url,
            "limit": str(limit),
            "timeout": str(timeout),
            "min_score": str(min_score),
        },
        error=None,
        success="Price lookup complete. Download the results below.",
        table_rows=table_rows,
        csv_payload=csv_payload,
        download_href=download_href,
    )
    return HTMLResponse(content=html_text)

handler = Mangum(app, lifespan="off")

@app.post("/search", response_class=JSONResponse)
async def search_api(payload: dict) -> JSONResponse:
    """
    JSON API:
      body: { "brand": "Nike", "title": "Air Force 1 Low White" }
      or:   { "pairs": [ ["Nike","Air Force 1"], ["Adidas","Campus 00s"] ] }
    Returns a list of results with price (or null if not found).
    """
    base_url = payload.get("base_url", PortalConfig.base_url)
    limit = int(payload.get("limit", PortalConfig.limit))
    timeout = float(payload.get("timeout", PortalConfig.timeout))
    min_score = float(payload.get("min_score", PortalConfig.min_score))

    pairs: List[Tuple[str, str]]
    if "pairs" in payload and isinstance(payload["pairs"], list):
        pairs = [(str(b), str(t)) for b, t in payload["pairs"] if b and t]
    else:
        brand = str(payload.get("brand", "")).strip()
        title = str(payload.get("title", "")).strip()
        if not (brand and title):
            raise HTTPException(status_code=400, detail="Provide 'brand' and 'title' or 'pairs'.")
        pairs = [(brand, title)]

    model = _default_model_factory(base_url=base_url, limit=limit, timeout=timeout)
    try:
        results = model.batch_search(pairs, min_score=min_score)
    finally:
        model.close()

    out: List[dict[str, Any]] = []
    for (b, t), r in zip(pairs, results):
        if r is None:
            out.append({"brand": b, "title": t, "price": None, "found": False})
        else:
            out.append({"brand": r.brand, "title": r.title, "price": r.price, "found": True})

    return JSONResponse(content={"results": out})


if __name__ == "__main__":  # pragma: no cover - manual execution entrypoint
    raise SystemExit(main())
