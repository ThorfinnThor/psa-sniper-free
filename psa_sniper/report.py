from __future__ import annotations

from .config import ROOT
from .models import RunStats, ScoredHit

PUBLIC_REPORT = ROOT / "reports" / "summary.md"
PRIVATE_REPORT = ROOT / "reports" / "latest-private.md"


def write_reports(hits: list[ScoredHit], near_hits: list[ScoredHit], stats: RunStats) -> None:
    PUBLIC_REPORT.parent.mkdir(parents=True, exist_ok=True)
    public_lines = [
        "# PSA Sniper · Laufstatus",
        "",
        f"- Start: `{stats.started_at}`",
        f"- Ende: `{stats.completed_at}`",
        f"- Suchabfragen: **{stats.queries_used}**",
        f"- gefundene Listings: **{stats.listings_seen}**",
        f"- frische Listings: **{stats.fresh_listings}**",
        f"- Detailprüfungen: **{stats.detailed_candidates}**",
        f"- eBay-Calls: **{stats.ebay_calls}**",
        f"- PSA-Lookups: **{stats.psa_lookups}**",
        f"- Hits: **{stats.hits}**",
        f"- Beobachtung: **{stats.near_hits}**",
        "",
        "Trefferdetails werden aus Datenschutzgründen nicht in öffentliche Actions-Logs geschrieben.",
    ]
    if stats.notes:
        public_lines += ["", "## Hinweise", ""] + [f"- {note}" for note in stats.notes[:8]]
    PUBLIC_REPORT.write_text("\n".join(public_lines) + "\n", encoding="utf-8")

    private_lines = ["# PSA Sniper · privater Bericht", ""]
    for label, rows in (("Hits", hits), ("Beobachtung", near_hits)):
        private_lines += [f"## {label}", ""]
        if not rows:
            private_lines += ["Keine.", ""]
            continue
        for hit in rows:
            private_lines += [f"### Score {hit.score} — {hit.listing.title}", ""]
            if hit.listing.total_cost:
                private_lines.append(
                    f"- Gesamtkosten: **{hit.listing.total_cost.value:.2f} "
                    f"{hit.listing.total_cost.currency}**"
                )
            if hit.cert and hit.cert.population is not None:
                private_lines.append(f"- PSA-10-POP: **{hit.cert.population}**")
            if hit.market_value:
                private_lines.append(
                    f"- Preisindikator: **{hit.market_value.money.value:.2f} "
                    f"{hit.market_value.money.currency}** ({hit.market_value.source})"
                )
            if hit.discount_pct is not None:
                private_lines.append(f"- Abstand: **{hit.discount_pct:.0%}**")
            private_lines.append(f"- [Auf eBay öffnen]({hit.listing.url})")
            private_lines.append("- Gründe:")
            private_lines.extend(f"  - {reason}" for reason in hit.reasons)
            if hit.warnings:
                private_lines.append("- Warnungen:")
                private_lines.extend(f"  - {warning}" for warning in hit.warnings)
            private_lines.append("")
    PRIVATE_REPORT.write_text("\n".join(private_lines) + "\n", encoding="utf-8")
