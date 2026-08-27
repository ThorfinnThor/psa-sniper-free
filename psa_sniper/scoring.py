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
    title_n = normalize_text(listing.title)

    title_psa10 = any(
        marker in title_n for marker in ("psa 10", "psa10", "gem mt 10", "gem mint 10")
    )
    if title_psa10:
        score += 2
        reasons.append("PSA 10 im Listing angegeben")

    if cert_number:
        score += 1
        reasons.append(f"PSA-Cert erkannt ({cert_source or 'Listing'})")

    overlap = identity_overlap(listing, cert)
    cert_trusted = True
    if cert and is_psa10(cert.grade):
        score += 2
        reasons.append("PSA-Cert bestätigt GEM MT 10")
    elif cert and cert.grade:
        score -= 20
        cert_trusted = False
        warnings.append(f"Cert-Grade ist {cert.grade}, nicht PSA 10")

    if cert and cert_source and cert_source.startswith("OCR"):
        confidence = cert_confidence or 0.0
        if confidence < 0.7 and overlap == 0:
            cert_trusted = False
            score -= 7
            warnings.append("OCR-Cert passt nicht plausibel zum Listing; POP/Preis werden ignoriert")
        elif overlap == 0:
            score -= 2
            warnings.append("OCR-Cert hat keine erkennbare Titelüberschneidung")
        else:
            reasons.append("OCR-Cert passt inhaltlich zum Listing")

    if cert and cert_trusted and cert.population is not None:
        pop = cert.population
        if pop <= 3:
            score += 5
            reasons.append(f"sehr niedrige PSA-10-Population: {pop}")
        elif pop <= 10:
            score += 4
            reasons.append(f"niedrige PSA-10-Population: {pop}")
        elif pop <= 25:
            score += 3
            reasons.append(f"PSA-10-Population: {pop}")
        elif pop <= 50:
            score += 1
            reasons.append(f"moderat niedrige PSA-10-Population: {pop}")

    gaps = info_gap(listing, cert if cert_trusted else None)
    if gaps:
        score += min(4, len(gaps))
        reasons.extend(gaps)

    if not any(term in title_n for term in HYPE_TERMS):
        score += 1
        reasons.append("Verkäufer vermarktet keinen Low-Pop-/Investment-Hype")
    else:
        score -= 2
        warnings.append("Verkäufer bewirbt Seltenheit bereits aktiv")

    if len(listing.title.split()) <= 8:
        score += 1
        reasons.append("kurzer bzw. informationsarmer Titel")

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
        score += 3
        reasons.append("trifft einen konfigurierten Prioritätsbegriff")
    elif demand_terms and any(normalize_text(term) in identity_n for term in demand_terms):
        score += 1
        reasons.append("erkennbare Nachfrage-/Sammlerrelevanz")

    if cert and cert.year:
        year_match = re.search(r"(?:19|20)\d{2}", cert.year)
        if year_match:
            card_year = int(year_match.group(0))
            current_year = datetime.now(timezone.utc).year
            if card_year >= current_year - 1:
                score -= 2
                warnings.append("sehr neue Karte: Population kann noch schnell steigen")
            elif card_year <= current_year - 4:
                score += 1
                reasons.append("ältere/reifere Population")

    if listing.pure_auction:
        score -= 3
        warnings.append("reine Auktion: aktueller Preis ist kein Sofortkauf-Fehlpreis")

    if listing.seller_feedback_percentage is not None:
        if listing.seller_feedback_percentage < 95:
            score -= 4
            warnings.append("Verkäuferbewertung unter 95 %")
        elif listing.seller_feedback_percentage < 98:
            score -= 2
            warnings.append("Verkäuferbewertung unter 98 %")
    if listing.seller_feedback_score is not None and listing.seller_feedback_score < 10:
        score -= 1
        warnings.append("sehr wenige Verkäuferbewertungen")

    discount_pct: float | None = None
    market_raw = market_value_listing_currency
    if isinstance(market_raw, Money):
        market_raw = MarketValue(market_raw, "manueller/Legacy-Preisindikator", "hoch", 1)
    market = market_raw if cert_trusted else None
    acquisition = listing.total_cost
    if market and acquisition and market.money.value > 0 and not listing.pure_auction:
        discount_pct = 1.0 - acquisition.value / market.money.value
        if market.confidence == "hoch":
            points = (7, 5, 3)
        elif market.confidence == "mittel":
            points = (5, 4, 2)
        else:
            points = (3, 2, 1)
        if discount_pct >= 0.40:
            score += points[0]
            reasons.append(f"Gesamtkosten ca. {discount_pct:.0%} unter Vergleichswert")
        elif discount_pct >= 0.25:
            score += points[1]
            reasons.append(f"Gesamtkosten ca. {discount_pct:.0%} unter Vergleichswert")
        elif discount_pct >= 0.15:
            score += points[2]
            reasons.append(f"Gesamtkosten ca. {discount_pct:.0%} unter Vergleichswert")
        elif discount_pct <= -0.10:
            penalty = _overprice_penalty(market.confidence, discount_pct)
            score -= penalty
            warnings.append(f"Gesamtkosten ca. {-discount_pct:.0%} über dem Preisindikator")
            # A high-confidence market signal should be a hard gate for a sniper.
            if market.confidence == "hoch" and discount_pct <= -0.25:
                score = min(score, 5)
            elif market.confidence == "mittel" and discount_pct <= -0.50:
                score = min(score, 6)
        if market.confidence == "niedrig":
            warnings.append("Preisvergleich basiert nur auf PSA Estimate, nicht auf mehreren Sales")

    if listing.shipping and listing.price and listing.shipping.currency == listing.price.currency:
        if listing.price.value > 0 and listing.shipping.value / listing.price.value >= 0.25:
            score -= 1
            warnings.append("hohe Versandkosten im Verhältnis zum Kartenpreis")

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
    )
