from app.modules.monitoring.query_plan import build_query_plan


def test_query_plan_groups_dedupes_and_excludes():
    plan = build_query_plan(
        brand_name="Djezzy", brand_aliases=["جازي", "djezy", "DJEZZY", "  djezy "],
        products=[("Hayla", ["هايلة", "hayla"])],
        competitors=[("Ooredoo", ["اوريدو"])],
        include_keywords=["djezzy 4g"], exclude_keywords=["djezzy fc", "Djezzy FC"],
    )
    assert [(g.entity_type, g.entity_name) for g in plan] == [
        ("brand", "Djezzy"), ("product", "Hayla"), ("competitor", "Ooredoo")]
    brand = plan[0]
    assert brand.terms == ["Djezzy", "جازي", "djezy", "djezzy 4g"]
    assert brand.exclude == ["djezzy fc"]
    assert plan[1].terms == ["Hayla", "هايلة"]
    expected = '("Djezzy" OR "جازي" OR "djezy" OR "djezzy 4g") -"djezzy fc"'
    assert brand.as_search_string() == expected
    assert plan[2].to_dict()["search_string"].startswith('("Ooredoo" OR "اوريدو")')


def test_query_plan_without_catalog():
    plan = build_query_plan(brand_name="Cevital", brand_aliases=[], products=[],
                            competitors=[], include_keywords=[], exclude_keywords=[])
    assert len(plan) == 1 and plan[0].as_search_string() == '("Cevital")'
