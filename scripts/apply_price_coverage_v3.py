from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"needle not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Card identity: preserve separators and detect high-signal numbers anywhere.
replace_once(
    "psa_sniper/identity.py",
    '    "JP": {"jp", "jpn", "japanese", "japanisch"},',
    '    "JP": {"jp", "jpn", "jap", "japanese", "japanisch"},',
)

replace_once(
    "psa_sniper/identity.py",
    '''def _normalize_card_number(value: str | None) -> str | None:\n    if not value:\n        return None\n    text = normalize_text(value).strip().lstrip("#")\n    match = re.search(r"[a-z0-9]+(?:[\\-_/][a-z0-9]+)?", text)\n    return match.group(0) if match else None\n''',
    '''def _normalize_card_number(value: str | None) -> str | None:\n    if not value:\n        return None\n    # normalize_text() intentionally removes punctuation, but separators such as\n    # P-043 and 237/193 are part of a trading-card number. Preserve them here.\n    text = str(value).strip().casefold().lstrip("#")\n    text = re.sub(r"\\s*([\\-_/])\\s*", r"\\1", text)\n    match = re.search(r"[a-z0-9]+(?:[\\-_/][a-z0-9]+){0,2}", text, re.I)\n    return match.group(0).casefold() if match else None\n\n\ndef _card_number_key(value: str | None) -> str:\n    # P-043, P043 and P 043 are the same identifier for matching purposes.\n    return "".join(_tokens(value or ""))\n''',
)

replace_once(
    "psa_sniper/identity.py",
    '''    value = normalize_text(match.group(1))\n    return None if re.fullmatch(r"(?:19|20)\\d{2}", value) else value\n\n\ndef card_number_from_title(title: str) -> str | None:\n''',
    '''    value = _normalize_card_number(match.group(1))\n    return None if value and re.fullmatch(r"(?:19|20)\\d{2}", value) else value\n\n\ndef _card_number_anywhere(title: str) -> str | None:\n    # High-signal formats commonly occur well before the words "PSA 10".\n    # Examples from real listings: 237/193, P-043, OP01-001, P043.\n    patterns = (\n        r"(?<![a-z0-9])(\\d{1,4}\\s*/\\s*\\d{1,4})(?![a-z0-9])",\n        r"(?<![a-z0-9])([a-z]{1,4}\\d{0,3}\\s*[-_/]\\s*\\d{1,4}[a-z]?)(?![a-z0-9])",\n        r"(?<![a-z0-9])([a-z]{1,4}\\d{2,5})(?![a-z0-9])",\n    )\n    for pattern in patterns:\n        match = re.search(pattern, title, re.I)\n        if match:\n            value = _normalize_card_number(match.group(1))\n            if value:\n                return value\n    return None\n\n\ndef card_number_from_title(title: str) -> str | None:\n''',
)

replace_once(
    "psa_sniper/identity.py",
    '''    explicit_match = re.search(\n        r"(?<![a-z0-9])#\\s*([a-z0-9]+(?:[\\-_/][a-z0-9]+)?)",\n        title,\n        re.I,\n    )\n    explicit = normalize_text(explicit_match.group(1)) if explicit_match else None\n''',
    '''    explicit_match = re.search(\n        r"(?<![a-z0-9])#\\s*([a-z0-9]+(?:[\\-_/][a-z0-9]+){0,2})",\n        title,\n        re.I,\n    )\n    explicit = _normalize_card_number(explicit_match.group(1)) if explicit_match else None\n''',
)

replace_once(
    "psa_sniper/identity.py",
    '''    if explicit:\n        return explicit\n    if near:\n        return near\n\n    match = re.search(\n''',
    '''    if explicit:\n        return explicit\n    if near:\n        return near\n\n    anywhere = _card_number_anywhere(title)\n    if anywhere:\n        return anywhere\n\n    match = re.search(\n''',
)

replace_once(
    "psa_sniper/identity.py",
    '''    value = normalize_text(match.group(1))\n    return None if re.fullmatch(r"(?:19|20)\\d{2}", value) else value\n\n\ndef normalize_language''',
    '''    value = _normalize_card_number(match.group(1))\n    return None if value and re.fullmatch(r"(?:19|20)\\d{2}", value) else value\n\n\ndef normalize_language''',
)

replace_once(
    "psa_sniper/identity.py",
    '''    subjects = _subject_terms(subject_value or "", exclude=card_parts)\n    if not subjects:\n        subjects = _subject_terms(listing.title, exclude=card_parts)\n    if not subjects:\n        return None\n''',
    '''    subjects = _subject_terms(subject_value or "", exclude=card_parts)\n    if not subjects:\n        subjects = _subject_terms(listing.title, exclude=card_parts)\n        # Prefer meaningful words immediately before the card number. This avoids\n        # treating set/franchise words as the subject in titles such as\n        # "Team Rocket's Mewtwo ... 237/193 M2A ... PSA 10".\n        title_n = normalize_text(listing.title)\n        card_n = normalize_text(card_number)\n        position = title_n.find(card_n) if card_n else -1\n        if position > 0:\n            before_number = _subject_terms(title_n[:position], exclude=card_parts)\n            if before_number:\n                subjects = before_number[-2:]\n    if not subjects:\n        return None\n''',
)

replace_once(
    "psa_sniper/identity.py",
    '''def build_identity_queries(identity: PricingIdentity) -> list[str]:\n    subject = " ".join(identity.subjects[:2])\n    precise: list[str] = [subject]\n    if identity.set_code:\n        precise.append(identity.set_code)\n    precise.extend([identity.card_number, "PSA 10"])\n    queries = [" ".join(x for x in precise if x)]\n    if identity.set_code:\n        queries.append(" ".join([subject, identity.card_number, "PSA 10"]))\n    if len(identity.subjects) >= 2:\n        queries.append(" ".join([identity.subjects[0], identity.card_number, "PSA 10"]))\n    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))\n''',
    '''def build_identity_queries(identity: PricingIdentity) -> list[str]:\n    subject = " ".join(identity.subjects[:2])\n    number_variants = [identity.card_number]\n    compact = _card_number_key(identity.card_number)\n    if compact and compact != _card_number_key(number_variants[0].replace("-", "").replace("/", "").replace("_", "")):\n        number_variants.append(compact)\n    elif compact and compact != identity.card_number.replace("-", "").replace("/", "").replace("_", ""):\n        number_variants.append(compact)\n    parts = _tokens(identity.card_number)\n    if "/" in identity.card_number and len(parts) >= 2 and parts[0].isdigit():\n        number_variants.append(parts[0])\n    number_variants = list(dict.fromkeys(value for value in number_variants if value))\n\n    queries: list[str] = []\n    primary = number_variants[0]\n    precise: list[str] = [subject]\n    if identity.set_code:\n        precise.append(identity.set_code)\n    precise.extend([primary, "PSA 10"])\n    queries.append(" ".join(x for x in precise if x))\n    if identity.set_code:\n        queries.append(" ".join([subject, primary, "PSA 10"]))\n    if len(identity.subjects) >= 2:\n        queries.append(" ".join([identity.subjects[0], primary, "PSA 10"]))\n\n    for alternate in number_variants[1:]:\n        if identity.set_code:\n            queries.append(" ".join([subject, identity.set_code, alternate, "PSA 10"]))\n        queries.append(" ".join([identity.subjects[0], alternate, "PSA 10"]))\n    return list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:5]\n''',
)

# Simplify a redundant compact-number condition introduced above.
replace_once(
    "psa_sniper/identity.py",
    '''    compact = _card_number_key(identity.card_number)\n    if compact and compact != _card_number_key(number_variants[0].replace("-", "").replace("/", "").replace("_", "")):\n        number_variants.append(compact)\n    elif compact and compact != identity.card_number.replace("-", "").replace("/", "").replace("_", ""):\n        number_variants.append(compact)\n''',
    '''    compact = _card_number_key(identity.card_number)\n    raw_compact = identity.card_number.replace("-", "").replace("/", "").replace("_", "")\n    if compact and identity.card_number != raw_compact:\n        number_variants.append(compact)\n''',
)

replace_once(
    "psa_sniper/identity.py",
    '''    candidate = pricing_identity_from_listing(listing)\n    if candidate is None or normalize_text(candidate.card_number) != normalize_text(identity.card_number):\n        return 0, False, 0\n''',
    '''    candidate = pricing_identity_from_listing(listing)\n    if candidate is None or _card_number_key(candidate.card_number) != _card_number_key(identity.card_number):\n        return 0, False, 0\n''',
)

# 2) Listing-identity active comps: two tight independent exact comps can be medium confidence.
replace_once(
    "psa_sniper/listing_market.py",
    '''def market_value_from_listing_comps(\n    values: list[Money],\n    *,\n    required_edge: float = 0.25,\n) -> MarketValue | None:\n    anchor = conservative_active_anchor(values)\n    if not anchor:\n        return None\n    clean_values = [m for m in values if m.currency.upper() == anchor.currency.upper() and m.value > 0]\n    sellers = {m.seller_key for m in clean_values if m.seller_key}\n    numbers = sorted(m.value for m in clean_values)\n    med = numbers[len(numbers) // 2] if numbers else 0.0\n    spread = ((numbers[-1] - numbers[0]) / med) if len(numbers) >= 2 and med > 0 else 0.0\n    return MarketValue(\n        anchor,\n        "eBay aktive PSA-10-Comps (Listing-Identität)",\n        "niedrig",\n        len(clean_values),\n        market_type="ebay_active_provisional",\n        required_edge=max(0.25, required_edge),\n        unique_sellers=len(sellers),\n        price_low=min(numbers) if numbers else None,\n        price_high=max(numbers) if numbers else None,\n        dispersion=spread,\n    )\n''',
    '''def market_value_from_listing_comps(\n    values: list[Money],\n    *,\n    required_edge: float = 0.25,\n) -> MarketValue | None:\n    anchor = conservative_active_anchor(values)\n    if not anchor:\n        return None\n    clean_values = [m for m in values if m.currency.upper() == anchor.currency.upper() and m.value > 0]\n    sellers = {m.seller_key for m in clean_values if m.seller_key}\n    numbers = sorted(m.value for m in clean_values)\n    med = numbers[len(numbers) // 2] if numbers else 0.0\n    spread = ((numbers[-1] - numbers[0]) / med) if len(numbers) >= 2 and med > 0 else 0.0\n    max_penalty = max((int(m.match_penalty or 0) for m in clean_values), default=0)\n    min_identity = min((int(m.identity_score or 0) for m in clean_values), default=0)\n\n    sparse_exact = (\n        len(clean_values) == 2\n        and len(sellers) == 2\n        and spread <= 0.18\n        and max_penalty == 0\n        and min_identity >= 7\n    )\n    dense_exact = (\n        len(clean_values) >= 3\n        and len(sellers) >= 3\n        and spread <= 0.30\n        and max_penalty == 0\n        and min_identity >= 6\n    )\n    confidence = "mittel" if sparse_exact or dense_exact else "niedrig"\n\n    # With only two comps, use the cheaper ask rather than their median. The\n    # buyer therefore has to beat both independent exact listings by the gate.\n    if sparse_exact:\n        anchor = Money(float(numbers[0]), anchor.currency)\n\n    source = (\n        "eBay aktive PSA-10-Comps (exakte Listing-Identität)"\n        if confidence == "mittel"\n        else "eBay aktive PSA-10-Comps (Listing-Identität)"\n    )\n    return MarketValue(\n        anchor,\n        source,\n        confidence,\n        len(clean_values),\n        market_type="ebay_active_provisional",\n        required_edge=max(0.25, required_edge),\n        unique_sellers=len(sellers),\n        price_low=min(numbers) if numbers else None,\n        price_high=max(numbers) if numbers else None,\n        dispersion=spread,\n    )\n''',
)

# 3) Low-confidence cache entries must not prevent a fresh comp search.
replace_once(
    "psa_sniper/scanner.py",
    '''                    if cached and cached_market is not None:\n                        market = _prefer_market_value(market, cached_market)\n                    elif market_comp_calls < max_comp_calls:\n                        try:\n''',
    '''                    if cached and cached_market is not None:\n                        market = _prefer_market_value(market, cached_market)\n                    if (not cached or cached_market is None or cached_market.confidence.casefold() == "niedrig") and market_comp_calls < max_comp_calls:\n                        try:\n''',
)

# The same pattern occurs in the listing-identity fallback later in scanner.py.
replace_once(
    "psa_sniper/scanner.py",
    '''                if cached and cached_market is not None:\n                    market = _prefer_market_value(market, cached_market)\n                else:\n                    try:\n                        comp_rows: list[Listing] = []\n''',
    '''                if cached and cached_market is not None:\n                    market = _prefer_market_value(market, cached_market)\n                if not cached or cached_market is None or cached_market.confidence.casefold() == "niedrig":\n                    try:\n                        comp_rows: list[Listing] = []\n''',
)

replace_once(
    "psa_sniper/repricing.py",
    '''                if cached and cached_market is not None:\n                    market = _prefer_market(market, cached_market)\n                else:\n                    try:\n                        _, values = _search_listing_comps(\n''',
    '''                if cached and cached_market is not None:\n                    market = _prefer_market(market, cached_market)\n                if force_refresh or not cached or cached_market is None or cached_market.confidence.casefold() == "niedrig":\n                    try:\n                        _, values = _search_listing_comps(\n''',
)

# 4) Explain exact listing-identity price anchors in the score details.
replace_once(
    "psa_sniper/scoring.py",
    '''        if market.market_type == "ebay_active":\n            if market.sample_size >= 3:\n                adjust(0, f"eBay-Marktanker aus {market.sample_size} exakten aktiven PSA-10-Comps")\n            else:\n                adjust(0, f"nur {market.sample_size} exakte aktive eBay-Comp(s); Preisquelle zu dünn")\n''',
    '''        if market.market_type in {"ebay_active", "ebay_active_provisional"}:\n            if confidence == "mittel" and market.market_type == "ebay_active_provisional":\n                adjust(0, f"eBay-Marktanker aus {market.sample_size} exakten unabhängigen Listing-Comps")\n            elif market.sample_size >= 3:\n                adjust(0, f"eBay-Marktanker aus {market.sample_size} exakten aktiven PSA-10-Comps")\n            else:\n                adjust(0, f"nur {market.sample_size} exakte aktive eBay-Comp(s); Preisquelle zu dünn")\n''',
)

# 5) Tests for the screenshot formats and sparse exact market confidence.
p = Path("tests/test_listing_market.py")
text = p.read_text(encoding="utf-8")
append = r'''


def test_mewtwo_fraction_number_is_detected_far_before_psa():
    row = listing(
        "mewtwo",
        "Pokémon Karte Team Rocket's Mewtwo ex SAR Mega Dream ex 237/193 m2a - Jap Psa 10",
    )
    identity = listing_comp_identity(row)
    assert identity is not None
    assert identity.card_number == "237/193"
    assert "mewtwo" in identity.subjects
    assert identity.set_code == "M2A"
    assert identity.language == "JP"
    queries = build_listing_comp_queries(identity)
    assert any("237/193" in q and "mewtwo" in q.lower() for q in queries)
    assert any("237" in q and "mewtwo" in q.lower() for q in queries)


def test_luffy_promo_code_is_detected_far_before_psa_and_matches_compact_form():
    row = listing(
        "luffy-p043",
        "One Piece Card Monkey D Luffy P-043 Promo Weekly Shonen Jump - PSA 10 GEM MT",
    )
    identity = listing_comp_identity(row)
    assert identity is not None
    assert identity.card_number == "p-043"
    assert identity.subjects[:2] == ("monkey", "luffy")
    assert identity.variant == "PROMO"
    compact = listing("compact", "Monkey D Luffy P043 Promo PSA 10", 250, seller="x")
    assert listing_comp_identity_score(compact, identity)[1] is True


def test_two_tight_independent_exact_listing_comps_are_medium_confidence():
    source = listing("own-ursaring", "Pokemon Ursaring Holo #217 Japanese Neo 2 Crossing The Ruins PSA 10 Gem Mint")
    identity = listing_comp_identity(source)
    assert identity is not None
    rows = [
        listing("u1", "Pokemon Ursaring Holo #217 Japanese Neo 2 Crossing The Ruins PSA 10", 450, seller="seller-a"),
        listing("u2", "Ursaring #217 Japanese Neo 2 Holo PSA 10 Gem Mint", 470, seller="seller-b"),
    ]
    values = exact_active_comps_for_listing(rows, identity, target_currency="EUR", fx=IdentityFX())
    market = market_value_from_listing_comps(values, required_edge=0.25)
    assert market is not None
    assert market.sample_size == 2
    assert market.unique_sellers == 2
    assert market.confidence == "mittel"
    assert market.money.value == 450
    assert market.required_edge == 0.25


def test_sparse_listing_comps_stay_low_if_same_seller_or_wide_spread():
    source = listing("own", "Pikachu #173 SV2A PSA 10 Japanese")
    identity = listing_comp_identity(source)
    assert identity is not None
    same_seller = exact_active_comps_for_listing(
        [
            listing("a", "Pikachu #173 SV2A PSA 10 Japanese", 100, seller="same"),
            listing("b", "Pikachu #173 SV2A PSA 10 Japanese", 105, seller="same"),
        ],
        identity, target_currency="EUR", fx=IdentityFX(),
    )
    assert market_value_from_listing_comps(same_seller).confidence == "niedrig"

    wide = exact_active_comps_for_listing(
        [
            listing("c", "Pikachu #173 SV2A PSA 10 Japanese", 100, seller="c"),
            listing("d", "Pikachu #173 SV2A PSA 10 Japanese", 160, seller="d"),
        ],
        identity, target_currency="EUR", fx=IdentityFX(),
    )
    assert market_value_from_listing_comps(wide).confidence == "niedrig"
'''
if "test_mewtwo_fraction_number_is_detected_far_before_psa" in text:
    raise RuntimeError("tests already patched")
p.write_text(text + append, encoding="utf-8")

# Existing test intentionally asserted that even three exact comps were always weak.
replace_once(
    "tests/test_listing_market.py",
    '''def test_provisional_market_is_always_low_confidence():\n''',
    '''def test_three_independent_exact_listing_comps_can_be_medium_confidence():\n''',
)
replace_once(
    "tests/test_listing_market.py",
    '''    assert market.confidence == "niedrig"\n    assert market.market_type == "ebay_active_provisional"\n    assert market.required_edge == 0.25\n    assert market.unique_sellers == 3\n''',
    '''    assert market.confidence == "mittel"\n    assert market.market_type == "ebay_active_provisional"\n    assert market.required_edge == 0.25\n    assert market.unique_sellers == 3\n''',
)

print("price coverage v3 patch applied")
