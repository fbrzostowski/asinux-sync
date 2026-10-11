from asinux_sync_settings.search import matches


def test_all_words_must_match_in_any_order_ignoring_case():
    assert matches("", "anything")
    assert matches("DOCK pos", "Dash to dock", "dock position")
    assert matches("position dock", "dock position")
    assert not matches("dock cursor", "dock position")


def test_matches_across_several_texts():
    assert matches("theme look", "Theme, icons and cursor", "Look and feel")
