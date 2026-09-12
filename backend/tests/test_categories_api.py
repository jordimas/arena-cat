def test_get_categories_returns_catalog_sorted_by_code(client):
    response = client.get("/api/categories")

    assert response.status_code == 200
    categories = response.json()["categories"]
    assert [category["code"] for category in categories] == [
        "correccio",
        "reformulacio",
        "traduccio",
    ]
    assert all(category["evaluation_instructions"] for category in categories)
    assert all(category["evaluation_instructions"].count("\n") == 3 for category in categories)
