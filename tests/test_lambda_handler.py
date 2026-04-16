from types import SimpleNamespace

import pytest

from pricing.lambda_handler import handler, _serialize_result, _parse_queries
from pricing.model import SearchResult


class _StubModel:
    def __init__(self, *, base_url, limit, timeout):
        self.base_url = base_url
        self.limit = limit
        self.timeout = timeout
        self.closed = False
        self.received_queries = None
        self.received_min_score = None

    def batch_search(self, queries, *, min_score):
        self.received_queries = list(queries)
        self.received_min_score = min_score
        return [
            SearchResult(brand="Nike", title="Pegasus 40", price=130.0, score=0.8),
            None,
        ]

    def close(self):
        self.closed = True


class _ModelFactoryWrapper:
    def __init__(self):
        self.created = []

    def __call__(self, base_url, limit, timeout):
        model = _StubModel(base_url=base_url, limit=limit, timeout=timeout)
        self.created.append(model)
        return model


@pytest.fixture(autouse=True)
def override_model_factory(monkeypatch):
    wrapper = _ModelFactoryWrapper()
    monkeypatch.setattr("pricing.lambda_handler._model_factory", wrapper)
    return wrapper


def test_handler_returns_serialized_results(override_model_factory):
    event = {
        "base_url": "https://example.com/api",
        "limit": 5,
        "timeout": 3.5,
        "min_score": 0.25,
        "queries": [
            {"brand": "Nike", "title": "Pegasus 40"},
            {"brand": "Adidas", "title": "Ultraboost"},
        ],
    }

    response = handler(event, SimpleNamespace())

    assert response == {
        "results": [
            {"brand": "Nike", "title": "Pegasus 40", "price": 130.0, "score": 0.8},
            None,
        ]
    }

    assert override_model_factory.created
    model = override_model_factory.created[0]
    assert model.base_url == "https://example.com/api"
    assert model.limit == 5
    assert model.timeout == 3.5
    assert model.received_queries == [
        ("Nike", "Pegasus 40"),
        ("Adidas", "Ultraboost"),
    ]
    assert pytest.approx(model.received_min_score) == 0.25
    assert model.closed is True


def test_handler_validates_queries():
    with pytest.raises(ValueError):
        handler({"queries": []}, None)

    with pytest.raises(ValueError):
        handler({"queries": [{}]}, None)

    with pytest.raises(ValueError):
        handler({"queries": ["invalid"]}, None)


@pytest.mark.parametrize(
    "value,field,expected",
    [
        (("Nike", "Pegasus"), 0.6, {"brand": "Nike", "title": "Pegasus", "price": 200.0, "score": 0.6}),
        (None, 0.5, None),
    ],
)
def test_serialize_result(value, field, expected):
    if value is None:
        result = None
    else:
        result = SearchResult(brand=value[0], title=value[1], price=200.0, score=field)
    assert _serialize_result(result) == expected


def test_parse_queries_validates_content():
    queries = _parse_queries([
        {"brand": " Nike ", "title": " Pegasus "},
    ])
    assert queries == [("Nike", "Pegasus")]

    with pytest.raises(ValueError):
        _parse_queries([{"brand": "", "title": "Item"}])

    with pytest.raises(ValueError):
        _parse_queries([123])
