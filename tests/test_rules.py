from melophile.dedupe import text_similarity
from melophile.rules import feat_unify, strip_bracketed_junk, whitespace


def fields(changes):
    return {c.field: c.new_value for c in changes}


def test_feat_moves_from_artist_to_title():
    assert fields(feat_unify({"artist": "Drake feat. Rihanna", "title": "Song"})) == {
        "artist": "Drake", "title": "Song feat. Rihanna"}


def test_feat_variants():
    for artist in ("Drake ft. Rihanna", "Drake Featuring Rihanna", "Drake (feat Rihanna)", "Drake FT Rihanna"):
        assert fields(feat_unify({"artist": artist, "title": "Song"})) == {
            "artist": "Drake", "title": "Song feat. Rihanna"}, artist


def test_feat_not_duplicated_in_title():
    f = fields(feat_unify({"artist": "Drake feat. Rihanna", "title": "Song feat. Rihanna"}))
    assert f == {"artist": "Drake"}


def test_feat_title_only_is_made_consistent():
    assert fields(feat_unify({"artist": "Drake", "title": "Song (Ft. Rihanna)"})) == {"title": "Song feat. Rihanna"}


def test_feat_words_inside_other_words_are_left_alone():
    for artist in ("Daft Punk", "Left Boy", "Featherstone", "Swift"):
        assert feat_unify({"artist": artist, "title": "Song"}) == [], artist


def test_feat_with_nobody_named_is_left_for_a_person():
    assert feat_unify({"artist": "Drake feat.", "title": "Song"}) == []
    assert feat_unify({"artist": "feat. Rihanna", "title": "Song"}) == []


def test_no_changes_when_clean():
    assert feat_unify({"artist": "Drake", "title": "Song"}) == []
    assert whitespace({"artist": "Drake"}) == []


def test_junk_removed():
    assert fields(strip_bracketed_junk({"title": "Song (Official Video)"})) == {"title": "Song"}
    assert fields(strip_bracketed_junk({"title": "Song [Lyrics]"})) == {"title": "Song"}
    assert fields(strip_bracketed_junk({"title": "Song (Official Music Video) [HD]"})) == {"title": "Song"}
    assert fields(strip_bracketed_junk({"title": "Song (official lyric video)"})) == {"title": "Song"}


def test_meaningful_brackets_kept():
    for t in ("Song (Remastered 2011)", "Song (Live)", "Song [Demo]", "Song (Officially Yours)"):
        assert strip_bracketed_junk({"title": t}) == [], t


def test_junk_never_empties_a_title():
    assert strip_bracketed_junk({"title": "(Official Video)"}) == []


def test_whitespace_tidied():
    assert fields(whitespace({"artist": "  Drake   ", "title": "Song   Title"})) == {
        "artist": "Drake", "title": "Song Title"}


def test_text_similarity():
    assert text_similarity("Drake feat. Rihanna", "Song (Official Video)", "Drake", "Song") > 0.9
    assert text_similarity("Metallica", "Enter Sandman", "Beyonce", "Halo") < 0.5
    assert text_similarity(None, None, "Drake", "Song") == 0.0
