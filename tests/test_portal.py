import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

from pricing import PortalConfig
from pricing.model import SearchResult
from pricing import portal


@pytest.mark.parametrize(
    "csv_bytes, expected",
    [
        (b"brand,title\nNike,Pegasus 40\n", [("Nike", "Pegasus 40")]),
        (b"brand,title\n\n", []),
    ],
)
def test_parse_queries_bytes(csv_bytes, expected):
    assert portal._parse_queries_bytes(csv_bytes) == expected


def test_parse_queries_bytes_missing_header():
    assert (
        portal._parse_queries_bytes(b"Nike,Pegasus 40\n")
        == "Uploaded CSV is missing required columns: brand,title."
    )


class _StubModel:
    def __init__(self):
        self.closed = False
        self.received_queries = None
        self.min_score = None

    def batch_search(self, queries, *, min_score):
        self.received_queries = list(queries)
        self.min_score = min_score
        return [
            SearchResult(brand="Nike", title="Pegasus 40", price=120.0, score=0.9),
            None,
        ]

    def close(self):
        self.closed = True


def _multipart_form(boundary, fields, file_field):
    parts = []
    for name, value in fields:
        parts.append(
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
        )
    file_content, filename, content_type = file_field
    parts.append(
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"queries\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n"
        f"{file_content}\r\n"
    )
    parts.append(f"--{boundary}--\r\n")
    return "".join(parts).encode("utf-8")


def test_portal_serves_results():
    config = PortalConfig()
    created_models = []

    def model_factory(base_url, limit, timeout):
        model = _StubModel()
        model.base_url = base_url
        model.limit = limit
        model.timeout = timeout
        created_models.append(model)
        return model

    handler = portal._build_handler(config, model_factory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        boundary = "----pricingboundary"
        csv_text = "brand,title\nNike,Pegasus 40\nAdidas,Ultraboost\n"
        body = _multipart_form(
            boundary,
            [
                ("base_url", "https://example.com/api"),
                ("limit", "5"),
                ("timeout", "3.5"),
                ("min_score", "0.25"),
            ],
            (csv_text, "queries.csv", "text/csv"),
        )

        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
        conn.request(
            "POST",
            "/",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        response = conn.getresponse()
        body_text = response.read().decode("utf-8")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert response.status == 200
    assert "Download CSV" in body_text
    assert "Pegasus 40" in body_text
    assert "Not found" in body_text

    assert created_models, "Expected model factory to be invoked"
    model = created_models[0]
    assert model.base_url == "https://example.com/api"
    assert model.limit == 5
    assert model.timeout == 3.5
    assert model.received_queries == [
        ("Nike", "Pegasus 40"),
        ("Adidas", "Ultraboost"),
    ]
    assert pytest.approx(model.min_score) == 0.25
    assert model.closed is True
