import asyncio
from personal_rail.places import Places


def test_hub_resolution_excludes_same_named_shop_and_normalizes_airport_suffix(
    monkeypatch,
):
    p = Places()

    async def search(name):
        if name == "揭阳潮汕机场":
            return [
                {"name": "揭阳潮汕国际机场", "typecode": "150104", "id": "airport"},
                {"name": "揭阳机场站", "typecode": "150200", "id": "rail"},
            ]
        return [
            {"name": "广州新塘", "typecode": "020000", "id": "shop"},
            {"name": "广州新塘站", "typecode": "150200", "id": "rail"},
        ]

    monkeypatch.setattr(p, "search", search)
    assert asyncio.run(p.resolve_hub("广州新塘"))["id"] == "rail"
    assert asyncio.run(p.resolve_hub("揭阳潮汕机场"))["id"] == "airport"
