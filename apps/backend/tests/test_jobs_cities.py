"""The city fold, away from the database.

These pin the three failure modes the raw `JobFacets.city` column has, all
of them measured against the live catalogue rather than imagined: two
spellings of one city, a segment that is a site code rather than a place,
and several cities in one row.
"""

from app.services.jobs.cities import (
    canonical_cities,
    canonical_name,
    fold_city_counts,
    stored_values_for,
)


def test_an_alias_folds_onto_the_canonical_spelling():
    # 38 rows said Bengaluru and 35 said Bangalore in one sample of 3,000.
    assert canonical_cities("Bangalore") == ["Bengaluru"]
    assert canonical_cities("Bengaluru") == ["Bengaluru"]


def test_case_only_variants_are_one_city():
    assert canonical_cities("TAIPEI") == ["Taipei"]
    assert canonical_cities("taipei") == ["Taipei"]


def test_mixed_case_is_left_alone():
    """Title-casing an employer's own typography would be a downgrade."""
    assert canonical_cities("McLean") == ["McLean"]


def test_site_codes_are_not_cities():
    # Micron and Intel both publish these in the city position. enrich has
    # already split on "," and "-", so what arrives here is one segment.
    assert canonical_cities("Fab 10N/X") == []
    assert canonical_cities("Main Site") == []
    assert canonical_cities("RA3") == []


def test_work_arrangements_are_not_cities():
    assert canonical_cities("Hybrid") == []
    assert canonical_cities("Remote") == []
    assert canonical_cities("Multiple Locations") == []


def test_a_parenthetical_arrangement_is_stripped():
    # "Singapore (Hybrid)" -> "Singapore" -> dropped as a country name.
    assert canonical_cities("Singapore (Hybrid)") == []


def test_a_country_name_is_not_a_city_within_itself():
    assert canonical_cities("Singapore") == []
    assert canonical_cities("Hong Kong") == []


def test_short_codes_are_dropped():
    for code in ["sg", "SGP", "GB", "TPE"]:
        assert canonical_cities(code) == [], code


def test_a_multi_city_row_names_every_city():
    assert canonical_cities("Bengaluru; Gurugram; Mumbai") == [
        "Bengaluru",
        "Gurugram",
        "Mumbai",
    ]


def test_counts_fold_together_and_sort_by_size():
    folded = fold_city_counts(
        [("Bengaluru", 38), ("Bangalore", 35), ("Gurugram", 50), ("Fab 10A", 7)]
    )
    assert folded == [("Bengaluru", 73), ("Gurugram", 50)]


def test_ties_break_by_name_not_by_row_order():
    """Two calls must not order a tie differently."""
    assert fold_city_counts([("Osaka", 4), ("Hiroshima", 4)]) == [
        ("Hiroshima", 4),
        ("Osaka", 4),
    ]


def test_canonical_name_resolves_an_alias():
    assert canonical_name("bangalore") == "Bengaluru"
    assert canonical_name("Taipei") == "Taipei"
    assert canonical_name("   ") is None


def test_stored_values_cover_aliases_and_multi_city_rows():
    stored = ["Bengaluru", "Bangalore", "Bengaluru; Gurugram", "New York", "Austin"]
    assert stored_values_for("Bengaluru", stored) == [
        "Bengaluru",
        "Bangalore",
        "Bengaluru; Gurugram",
    ]


def test_stored_values_do_not_reach_a_longer_city_name():
    """The bug a LIKE would have shipped: York must not find New York."""
    assert stored_values_for("York", ["New York", "Yorkshire"]) == []
    assert stored_values_for("New York", ["New York", "Yorkshire"]) == ["New York"]


def test_what_reaches_the_fold_is_one_segment_not_a_location_string():
    """The contract between enrich and this module, pinned.

    `enrich.location_facets` splits the ATS location on "," and "-" and
    stores the first usable piece, so `JobFacets.city` holds one segment and
    the fold never has to parse "Mountain View, CA, USA". Checked against
    2,000 live postings: not one stored city contains a comma.

    This test exists because the opposite is easy to assume from the outside
    — feed this module a whole location string and it looks broken. If
    enrich's separators ever change, this is what should fail first, before
    the picker starts showing "Hsinchu" and "Hsinchu, Taiwan" as two cities.
    """
    from app.services.jobs.enrich import location_facets

    for raw, expected in [
        ("Mountain View, CA, USA", "Mountain View"),
        ("Hsinchu, Taiwan", "Hsinchu"),
        ("Boise, ID - Main Site", "Boise"),
        ("Santa Clara, CA", "Santa Clara"),
    ]:
        stored = location_facets(raw, source="greenhouse", remote=None).city
        assert stored == expected, raw
        assert "," not in (stored or ""), raw
        assert canonical_cities(stored) == [expected], raw


def test_no_alias_key_contains_a_comma():
    """An alias is matched against one segment, so a comma key is dead code.

    The table was first written from raw ATS location strings ("New York,
    NY"), which is the wrong domain: enrich splits on the comma before
    storing. Those entries looked reasonable and could never fire.
    """
    from app.services.jobs.enrich import default_taxonomy

    offenders = [key for key in default_taxonomy().city_aliases if "," in key]
    assert offenders == [], offenders


def test_work_arrangements_the_live_catalogue_actually_emits():
    """Seen in the picker on deploy day, in the UK and Taiwan lists.

    "Office Based" and "Home based" are how several boards fill the city
    position when there is no city. They are dropped at read time, so the
    fix needs no re-derivation -- which is the point of folding here rather
    than in enrich.
    """
    for raw in ["Office Based", "Home based", "Home-Based", "Field Based", "Virtual"]:
        assert canonical_cities(raw) == [], raw


def test_a_home_nation_is_not_a_city():
    """"England" is a country-ish answer to "which city", not a city."""
    assert canonical_cities("England") == []
    assert canonical_cities("Scotland") == []


# --- CJK-spelled cities ----------------------------------------------------
# `_MIN_ASCII_CHARS` counted CHARACTERS, and a Chinese city name is two of
# them, so every CJK-spelled city in the catalogue folded to nothing and was
# invisible in the picker. Taiwan and China are a large share of this
# catalogue, so this was not a corner case. No backfill: the fold runs at
# read time over a GROUP BY, so the next request is already right.


def test_a_cjk_city_name_is_not_too_short_to_be_a_city():
    """Two characters is a whole city name, not an airport code."""
    assert canonical_cities("台北") == ["Taipei"]
    assert canonical_cities("上海") == ["Shanghai"]
    assert canonical_cities("高雄") == ["Kaohsiung"]


def test_a_cjk_spelling_folds_onto_the_same_city_as_the_latin_one():
    """The whole point: one city, one picker row, counts added together.

    Letting the CJK spelling through as its own entry would have split
    Taipei into "Taipei" and "台北" -- the Bangalore/Bengaluru bug again, in
    a different script. The alias map is checked before the length floor,
    so this is also what makes the two-character names reachable at all.
    """
    assert canonical_cities("臺北") == ["Taipei"]
    assert canonical_cities("台北市") == ["Taipei"]
    assert canonical_cities("新竹市") == ["Hsinchu"]
    assert canonical_cities("新竹") == ["Hsinchu"]
    assert canonical_cities("臺中") == ["Taichung"]
    assert canonical_cities("深圳") == ["Shenzhen"]
    assert fold_city_counts([("Taipei", 40), ("台北", 12), ("臺北", 3)]) == [
        ("Taipei", 55)
    ]


def test_the_science_park_folds_onto_its_city():
    """竹科 is the Hsinchu Science Park -- a site inside Hsinchu, not a rival
    to it. It is a location_countries needle, so without an alias the fix
    would have added it to the picker as its own city."""
    assert canonical_cities("竹科") == ["Hsinchu"]


def test_a_chinese_work_arrangement_is_still_not_a_city():
    """The placeholder list was ASCII-only.

    "居家辦公" and "在家工作" are four characters, so they already cleared the
    old floor; "遠端" and "遠距" are two and would have started clearing it
    with this change. All four are work arrangements, not places.
    """
    for arrangement in ["遠端", "遠距", "居家辦公", "在家工作"]:
        assert canonical_cities(arrangement) == [], arrangement


def test_the_ascii_floor_did_not_move():
    """The reason the floor exists at all is unchanged for Latin script."""
    for code in ["sg", "SGP", "GB", "TPE", "MSB", "RA3"]:
        assert canonical_cities(code) == [], code


def test_a_single_character_is_never_a_city():
    """The floor moved for CJK; it did not disappear."""
    assert canonical_cities("台") == []
    assert canonical_cities("X") == []


def test_a_cjk_city_is_reachable_by_its_latin_name():
    """`?city=Taipei` has to find the postings stored as 台北.

    stored_values_for resolves the filter through the fold rather than
    through SQL, so this follows from the alias -- but it is the behaviour a
    reader actually sees, so it is pinned here and not left implied.
    """
    stored = ["Taipei", "台北", "臺北市", "Hsinchu"]
    assert stored_values_for("Taipei", stored) == ["Taipei", "台北", "臺北市"]
    assert stored_values_for("Hsinchu", stored) == ["Hsinchu"]


def test_an_unlisted_cjk_name_shows_itself_rather_than_vanishing():
    """No CJK needle exists for Japan or Korea in this taxonomy.

    If a board starts posting 東京, the right outcome is a picker row that
    reads 東京 -- findable, with a count of its own -- not a silently
    deleted city. Adding an alias later merges it onto "Tokyo"; that is the
    documented remedy and it needs no backfill.
    """
    assert canonical_cities("東京") == ["東京"]
