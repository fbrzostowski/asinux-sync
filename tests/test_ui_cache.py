from asinux_sync import ui_cache


def test_roundtrip_and_missing():
    assert ui_cache.load() is None
    ui_cache.save({"pretty_name": "Filip"}, [{"id": "a", "name": "Asus"}], "2026-10-11T10:00:00+00:00")
    cached = ui_cache.load()
    assert cached["account"]["pretty_name"] == "Filip"
    assert cached["devices"] == [{"id": "a", "name": "Asus"}]
    assert cached["fetched_at"] == "2026-10-11T10:00:00+00:00"


def test_corrupt_cache_is_ignored():
    ui_cache.save({}, [], "x")
    (ui_cache._path()).write_text("{not json")
    assert ui_cache.load() is None
