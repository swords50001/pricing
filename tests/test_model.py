from unittest.mock import MagicMock

import pytest

from pricing.model import ClothingPriceModel, RemoteLookupError


def test_batch_search_returns_online_matches():
    http_get = MagicMock(
        side_effect=[
            {
                "products": [
                    {"brand": "Nike", "title": "Air Zoom Pegasus 40", "price": 130},
                    {"brand": "Adidas", "title": "Ultraboost Light", "price": 190},
                ]
            },
            {
                "products": [
                    {"brand": "Adidas", "title": "Ultraboost Light", "price": 190},
                ]
            },
        ]
    )

    model = ClothingPriceModel(http_get=http_get, limit=5)

    results = model.batch_search([("Nike", "Pegasus 40"), ("Adidas", "Ultraboost")], min_score=0.3)

    assert http_get.call_count == 2
    assert results[0] is not None
    assert results[0].brand == "Nike"
    assert results[0].price == 130
    assert results[1] is not None and results[1].brand == "Adidas"


def test_batch_search_handles_remote_errors():
    http_get = MagicMock(side_effect=RemoteLookupError("boom"))

    model = ClothingPriceModel(http_get=http_get)

    results = model.batch_search([("Brand", "Item")])

    assert results == [None]
    assert http_get.called


@pytest.mark.parametrize(
    "payload,expected_count",
    [
        (
            {
                "products": [
                    {"brand": "Zara", "title": "Textured Blazer", "price": "89.99"},
                    {"brand": "Zara", "title": "", "price": 39},
                    {"brand": "", "title": "Wide Leg Jeans", "price": 59},
                    {"brand": "Zara", "title": "Wide Leg Jeans", "price": "not a number"},
                ]
            },
            1,
        ),
        ({"products": []}, 0),
    ],
)
def test_fetch_products_filters_invalid_entries(payload, expected_count):
    http_get = MagicMock(return_value=payload)

    model = ClothingPriceModel(http_get=http_get)

    products = model._fetch_products("Zara", "Jeans")

    assert len(products) == expected_count
    if products:
        assert products[0].title == "Textured Blazer"

    http_get.assert_called_with(
        model.base_url,
        {"q": "Zara Jeans", "limit": str(model.limit)},
        model.timeout,
    )
