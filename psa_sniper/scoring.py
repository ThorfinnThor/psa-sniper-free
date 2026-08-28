from __future__ import annotations

import re
from datetime import datetime, timezone

from .models import Listing, MarketValue, Money, PSACertInfo, ScoredHit
from .util import has_phrase, median_money, normalize_text

HYPE_TERMS = (
    "low pop",
    "population",
    "pop 1",
    "pop 2",
    "pop 3",
    "investment",
    "invest",
    "ssp",
    "ultra rare",
    "grail",
)

STOPWORDS = {
    "the", "and", "card", "cards", "trading", "game", "edition", "collection",
    "pokemon", "one", "piece", "topps", "panini", "psa", "gem", "mint", "chrome",
}

# A Kauf-Hit must have a usable price signal, not only rarity/title signals.
MIN_VERIFIED_PRICE_EDGE = 0.10
UNVERIFIED_HIT_SCORE_CAP = 10


def is_psa10(grade: str | None) -> bool:
    grade_n = normalize_text(grade)
    return grade_n == "10" or any(
        marker in grade_n for marker in ("gem mt 10", "gem mint 10", "psa 10", "psa10")
    )


def preliminary_score(listing: Listing, priority_terms: list[str] | None = None) -> int:
    title = normalize_text(listing.title)
    score = 0
    if any(marker in title for marker in ("psa 10", "psa10", "gem mt 10", "gem mint 10")):
        score += 4
    if not any(term in title for term in HYPE_TERMS):
        score += 2
    if len(listing.title.split()) <= 9:
        score += 2
    if listing.image_urls:
        score += 1
    if priority_terms and any(normalize_text(term) in title for term in priority_terms):
        score += 3
    if listing.pure_auction:
        score -= 2
    return score


def info_gap(listing: Listing, cert: PSACertInfo | None) -> list[str]:
    if not cert:
        return []
    gaps: list[str] = []
    title = listing.title
    if cert.subject and not has_phrase(title, cert.subject):
        gaps.append("Spieler/Charakter fehlt im Titel")
    if cert.variety and not has_phrase(title, cert.variety):
        gaps.append("Variante/Parallel fehlt im Titel")
    if cert.card_number:
        card = re.escape(cert.card_number)
        if not re.search(rf"(?<![A-Za-z0-9])#?{card}(?![A-Za-z0-9])", title, flags=re.I):
            gaps.append("Kartennummer fehlt im Titel")
    if cert.brand_title and not has_phrase(title, cert.brand_title):
        gaps.append("vollständiges Set/Brand fehlt im Titel")
    return gaps


def identity_overlap(listing: Listing, cert: PSACertInfo | None) -> int:
    if not cert:
        return 0
    title_tokens = set(normalize_text(listing.title).split()) - STOPWORDS
    cert_text = " ".join(
        value
        for value in (
            cert.year,
            cert.brand_title,
            cert.subject,
            cert.card_number,
            cert.variety,
        )
        if value
    )
    cert_tokens = set(normalize_text(cert_text).split()) - STOPWORDS
    meaningful = {token for token in cert_tokens if len(token) >= 2 or token.isdigit()}
    return len(title_tokens & meaningful)


def market_value_from_cert(cert: PSACertInfo | None) -> MarketValue | None:
    if not cert:
        return None
    sales = median_money(cert.recent_sales)
    if sales:
        sample_size = sum(1 for row in cert.recent_sales if row.currency == sales.currency)
        confidence = "hoch" if sample_size >= 3 else "mittel"
        return MarketValue(sales, "PSA ähnliche Verkäufe", confidence, sample_size)
    if cert.estimate:
        return MarketValue(cert.estimate, "PSA Estimate", "niedrig", 0)
    return None


def _overprice_penalty(confidence: str, discount_pct: float) -> int:
    """Return a strong penalty for listings above a usable market indicator."""
    if discount_pct > -0.10:
        return 0
    if confidence == "hoch":
        if discount_pct <= -1.00:
            return 15
        if discount_pct <= -0.50:
            return 12
        if discount_pct <= -0.25:
            return 8
        return 4
    if confidence == "mittel":
        if discount_pct <= -1.00:
            return 12
        if discount_pct <= -0.50:
            return 9
        if discount_pct <= -0.25:
            return 6
        return 3
    if discount_pct <= -1.00:
        return 6
    if discount_pct <= -0.50:
        return 4
    if discount_pct <= -0.25:
        return 3
    return 2


def _gate_label(price_status: str) -> str:
    return {
        "unverified": "Preis-Gate: kein belastbarer Preisindikator – nur Beobachtung",
        "weak_indicator": "Preis-Gate: Preisquelle zu schwach – nur Beobachtung",
        "no_edge": "Preis-Gate: weniger als 10 % bestätigter Preisvorteil – nur Beobachtung",
        "over_market": "Preis-Gate: Angebot liegt über dem Preisindikator – kein Kauf-Hit",
        "auction": "Preis-Gate: Auktion ohne belastbaren Endpreis – nur Beobachtung",
    }.get(price_status, "Preis-Gate: Kaufpreis nicht ausreichend bestätigt – nur Beobachtung")


def score_hit(
    listing: Listing,
    *,
    cert_number: str | None,
    cert_source: str | None,
    cert_confidence: float | None = None,
    cert: PSACertInfo | None = None,
    market_value_listing_currency: MarketValue | Money | None = None,
    priority_terms: list[str] | None = None,
    demand_terms: list[str] | None = None,
) -> ScoredHit:
    score = 0
    reasons: list[str] = []
    warnings: list[str] = []
    score_breakdown: list[dict[str, object]] = []
    title_n = normalize_text(listing.title)

    def adjust(points: int, label: str, kind: str | None = None) -> None:
        nonlocal score
        score += points
        if kind is None:
            kind = "positive" if points > 0 else "negative" if points < 0 else "neutral"
        score_breakdown.append({"points": points, "label": label, "kind": kind})

    title_psa10 = any(
        marker in title_n for marker in ("psa 10", "psa10", "gem mt 10", "gem mint 10")
    )
    if title_psa10:
        adjust(2, "PSA 10 im Listing angegeben")
        reasons.append("PSA 10 im Listing angegeben")

    if cert_number:
        label = f"PSA-Cert erkannt ({cert_source or 'Listing'})"
        adjust(1, label)
        reasons.append(label)

    overlap = identity_overlap(listing, cert)
    cert_trusted = True
    if cert and is_psa10(cert.grade):
        adjust(2, "PSA-Cert bestätigt GEM MT 10")
        reasons.append("PSA-Cert bestätigt GEM MT 10")
    elif cert and cert.grade:
        label = f"Cert-Grade ist {cert.grade}, nicht PSA 10"
        adjust(-20, label)
        cert_trusted = False
        warnings.append(label)

    if cert and cert_source and cert_source.startswith("OCR"):
        confidence = cert_confidence or 0.0
        if confidence < 0.7 and overlap == 0:
            label = "OCR-Cert passt nicht plausibel zum Listing; POP/Preis werden ignoriert"
            adjust(-7, label)
            cert_trusted = False
            warnings.append(label)
        elif overlap == 0:
            label = "OCR-Cert hat keine erkennbare Titelüberschneidung"
            adjust(-2, label)
            warnings.append(label)
        else:
            label = "OCR-Cert passt inhaltlich zum Listing"
            adjust(0, label)
            reasons.append(label)

    if cert and cert_trusted and cert.population is not None:
        pop = cert.population
        if pop <= 3:
            label, points = f"sehr niedrige PSA-10-Population: {pop}", 5
        elif pop <= 10:
            label, points = f"niedrige PSA-10-Population: {pop}", 4
        elif pop <= 25:
            label, points = f"PSA-10-Population: {pop}", 3
        elif pop <= 50:
            label, points = f"moderat niedrige PSA-10-Population: {pop}", 1
        else:
            label, points = "", 0
        if points:
            adjust(points, label)
            reasons.append(label)

    gaps = info_gap(listing, cert if cert_trusted else None)
    for gap in gaps[:4]:
        adjust(1, gap)
    if gaps:
        reasons.extend(gaps)

    if not any(term in title_n for term in HYPE_TERMS):
        label = "Verkäufer vermarktet keinen Low-Pop-/Investment-Hype"
        adjust(1, label)
        reasons.append(label)
    else:
        label = "Verkäufer bewirbt Seltenheit bereits aktiv"
        adjust(-2, label)
        warnings.append(label)

    if len(listing.title.split()) <= 8:
        label = "kurzer bzw. informationsarmer Titel"
        adjust(1, label)
        reasons.append(label)

    identity_text = " ".join(
        value
        for value in (
            listing.title,
            cert.subject if cert else None,
            cert.brand_title if cert else None,
            cert.variety if cert else None,
        )
        if value
    )
    identity_n = normalize_text(identity_text)
    if priority_terms and any(normalize_text(term) in identity_n for term in priority_terms):
        label = "trifft einen konfigurierten Prioritätsbegriff"
        adjust(3, label)
        reasons.append(label)
    elif demand_terms and any(normalize_text(term) in identity_n for term in demand_terms):
        label = "erkennbare Nachfrage-/Sammlerrelevanz"
        adjust(1, label)
        reasons.append(label)

    if cert and cert.year:
        year_match = re.search(r"(?:19|20)\d{2}", cert.year)
        if year_match:
            card_year = int(year_match.group(0))
            current_year = datetime.now(timezone.utc).year
            if card_year >= current_year - 1:
                label = "sehr neue Karte: Population kann noch schnell steigen"
                adjust(-2, label)
                warnings.append(label)
            elif card_year <= current_year - 4:
                label = "ältere/reifere Population"
                adjust(1, label)
                reasons.append(label)

    price_status = "unverified"
    if listing.pure_auction:
        label = "reine Auktion: aktueller Preis ist kein Sofortkauf-Fehlpreis"
        adjust(-3, label)
        warnings.append(label)
        price_status = "auction"

    if listing.seller_feedback_percentage is not None:
        if listing.seller_feedback_percentage < 95:
            label = "Verkäuferbewertung unter 95 %"
            adjust(-4, label)
            warnings.append(label)
        elif listing.seller_feedback_percentage < 98:
            label = "Verkäuferbewertung unter 98 %"
            adjust(-2, label)
            warnings.append(label)
    if listing.seller_feedback_score is not None and listing.seller_feedback_score < 10:
        label = "sehr wenige Verkäuferbewertungen"
        adjust(-1, label)
        warnings.append(label)

    discount_pct: float | None = None
    market_raw = market_value_listing_currency
    if isinstance(market_raw, Money):
        market_raw = MarketValue(market_raw, "manueller/Legacy-Preisindikator", "hoch", 1)
    market = market_raw if cert_trusted else None
    acquisition = listing.total_cost

    if not listing.pure_auction and market and acquisition and market.money.value > 0:
        discount_pct = 1.0 - acquisition.value / market.money.value
        confidence = market.confidence.casefold()
        if confidence in {"hoch", "mittel"} and discount_pct >= MIN_VERIFIED_PRICE_EDGE:
            price_status = "verified_edge"
        elif confidence == "niedrig":
            price_status = "weak_indicator"
        elif discount_pct <= -0.10:
            price_status = "over_market"
        else:
            price_status = "no_edge"

        if confidence == "hoch":
            points = (7, 5, 3)
        elif confidence == "mittel":
            points = (5, 4, 2)
        else:
            points = (3, 2, 1)

        if discount_pct >= 0.40:
            label = f"Gesamtkosten ca. {discount_pct:.0%} unter Vergleichswert"
            adjust(points[0], label)
            reasons.append(label)
        elif discount_pct >= 0.25:
            label = f"Gesamtkosten ca. {discount_pct:.0%} unter Vergleichswert"
            adjust(points[1], label)
            reasons.append(label)
        elif discount_pct >= 0.15:
            label = f"Gesamtkosten ca. {discount_pct:.0%} unter Vergleichswert"
            adjust(points[2], label)
            reasons.append(label)
        elif discount_pct >= MIN_VERIFIED_PRICE_EDGE and confidence in {"hoch", "mittel"}:
            adjust(0, f"Preisvorteil ca. {discount_pct:.0%} bestätigt; unter der Bonusgrenze")
        elif discount_pct <= -0.10:
            penalty = _overprice_penalty(confidence, discount_pct)
            label = f"Gesamtkosten ca. {-discount_pct:.0%} über dem Preisindikator"
            adjust(-penalty, label)
            warnings.append(label)
            if confidence == "hoch" and discount_pct <= -0.25 and score > 5:
                adjust(5 - score, "Hard Gate: deutlich über hoch-vertrauenswürdigem Preisindikator", "gate")
            elif confidence == "mittel" and discount_pct <= -0.50 and score > 6:
                adjust(6 - score, "Hard Gate: deutlich über mittlerem Preisindikator", "gate")
        else:
            adjust(0, "Kein bestätigter Preisvorteil von mindestens 10 %")

        if confidence == "niedrig":
            label = "Preisvergleich basiert nur auf PSA Estimate, nicht auf mehreren Sales"
            warnings.append(label)
            adjust(0, label)
    elif not listing.pure_auction:
        label = "Kein belastbarer Preisindikator verfügbar"
        warnings.append(label)
        adjust(0, label)

    if listing.shipping and listing.price and listing.shipping.currency == listing.price.currency:
        if listing.price.value > 0 and listing.shipping.value / listing.price.value >= 0.25:
            label = "hohe Versandkosten im Verhältnis zum Kartenpreis"
            adjust(-1, label)
            warnings.append(label)

    # Rarity/title quality can make a card interesting, but not a purchase hit on its own.
    # Keep such listings visible as observations while preventing a misleading Hit label.
    if price_status != "verified_edge" and score > UNVERIFIED_HIT_SCORE_CAP:
        adjust(UNVERIFIED_HIT_SCORE_CAP - score, _gate_label(price_status), "gate")

    return ScoredHit(
        listing=listing,
        score=score,
        reasons=reasons,
        warnings=warnings,
        cert=cert,
        cert_number=cert_number,
        cert_source=cert_source,
        cert_confidence=cert_confidence,
        cert_trusted=cert_trusted,
        market_value=market,
        discount_pct=discount_pct,
        price_status=price_status,
        score_breakdown=score_breakdown,
    )
