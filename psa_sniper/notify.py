from __future__ import annotations

import os
from typing import Any

import requests

from .config import load_settings
from .ebay import EbayClient
from .live_check import refresh_hit_for_purchase
from .models import ScoredHit


def _money_text(obj: Any) -> str:
    return f"{obj.value:.2f} {obj.currency}" if obj else "–"


def render_alert(hit: ScoredHit) -> str:
    listing = hit.listing
    cert = hit.cert
    lines = [f"🔥 PSA SNIPER — SCORE {hit.score}", "", listing.title]
    if listing.price:
        lines.append(f"eBay: {_money_text(listing.price)}")
    if listing.shipping:
        lines.append(f"Versand: {_money_text(listing.shipping)}")
    if listing.total_cost and listing.shipping:
        lines.append(f"Gesamt: {_money_text(listing.total_cost)}")
    if cert:
        identity = " | ".join(
            value
            for value in (
                cert.year,
                cert.brand_title,
                cert.subject,
                f"#{cert.card_number}" if cert.card_number else None,
                cert.variety,
            )
            if value
        )
        if identity:
            lines.append(identity)
        if cert.population is not None:
            lines.append(f"PSA-10-POP: {cert.population}")
    if hit.market_value:
        lines.append(
            f"Preisindikator: {_money_text(hit.market_value.money)} "
            f"({hit.market_value.source}, {hit.market_value.confidence})"
        )
    if hit.discount_pct is not None:
        lines.append(f"Abstand: {hit.discount_pct:.0%} günstiger")
    lines.append("")
    lines.extend(f"✓ {reason}" for reason in hit.reasons[:7])
    lines.extend(f"⚠ {warning}" for warning in hit.warnings[:4])
    lines.extend(["", listing.url])
    return "\n".join(lines)


def configured_channels() -> set[str]:
    channels: set[str] = set()
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"):
        channels.add("telegram")
    if os.getenv("DISCORD_WEBHOOK_URL"):
        channels.add("discord")
    return channels


def notify_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message[:4000],
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        return response.ok
    except requests.RequestException:
        return False


def notify_discord(hit: ScoredHit) -> bool:
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return False
    listing = hit.listing
    cert = hit.cert
    fields = []
    if listing.total_cost:
        fields.append(
            {"name": "Gesamtkosten", "value": _money_text(listing.total_cost), "inline": True}
        )
    if cert and cert.population is not None:
        fields.append({"name": "PSA-10-POP", "value": str(cert.population), "inline": True})
    if hit.discount_pct is not None:
        fields.append({"name": "Abstand", "value": f"{hit.discount_pct:.0%}", "inline": True})
    description = "\n".join(
        [*(f"✓ {x}" for x in hit.reasons[:5]), *(f"⚠ {x}" for x in hit.warnings[:3])]
    )
    embed: dict[str, Any] = {
        "title": f"Score {hit.score} · {listing.title}"[:250],
        "url": listing.url,
        "description": description[:3500],
        "fields": fields,
    }
    if listing.image_urls:
        embed["thumbnail"] = {"url": listing.image_urls[0]}
    try:
        response = requests.post(webhook, json={"embeds": [embed]}, timeout=20)
        return response.ok
    except requests.RequestException:
        return False


def _live_verified(hit: ScoredHit) -> ScoredHit | None:
    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    settings = load_settings()
    ebay = EbayClient(
        client_id,
        client_secret,
        environment=str(settings.get("environment", "production")),
        marketplace_id=str(settings.get("marketplace_id", "EBAY_DE")),
        delivery_country=str(settings.get("delivery_country", "DE")),
        buyer_postal_code=str(settings.get("buyer_postal_code", "")),
        delay_seconds=0,
        max_calls=2,
    )
    refreshed, status = refresh_hit_for_purchase(hit, ebay, settings)
    return refreshed if status == "active" else None


def notify(hit: ScoredHit) -> dict[str, bool]:
    channels = configured_channels()
    if not channels:
        return {}
    # Never alert from a stale stored price. A current eBay COMPACT check must
    # still satisfy the score and source-specific price-edge gate.
    verified = _live_verified(hit)
    if verified is None:
        return {channel: False for channel in channels}
    message = render_alert(verified)
    result: dict[str, bool] = {}
    if "telegram" in channels:
        result["telegram"] = notify_telegram(message)
    if "discord" in channels:
        result["discord"] = notify_discord(verified)
    return result
