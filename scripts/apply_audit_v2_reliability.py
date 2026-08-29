from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str, label: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: erwartet 1 Treffer, gefunden {count} in {path}")
    write(path, text.replace(old, new, 1))


def replace_block(path: str, start_marker: str, end_marker: str, new_block: str, label: str) -> None:
    text = read(path)
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: Startmarker fehlt in {path}")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{label}: Endmarker fehlt in {path}")
    write(path, text[:start] + new_block + text[end:])


# ---------------------------------------------------------------------------
# eBay: jedes echte Browse-HTTP-Attempt wird gegen das Hardcap gezählt.
# Automatische urllib3-Status-Retries würden sonst das Budget unterschätzen.
# ---------------------------------------------------------------------------
ebay = "psa_sniper/ebay.py"
replace_once(
    ebay,
    '''class EbayError(RuntimeError):\n    pass\n\n\nclass EbayBudgetExceeded(EbayError):\n    pass\n''',
    '''class EbayError(RuntimeError):\n    def __init__(\n        self,\n        message: str,\n        *,\n        status_code: int | None = None,\n        retryable: bool = False,\n    ) -> None:\n        super().__init__(message)\n        self.status_code = status_code\n        self.retryable = retryable\n\n    @property\n    def missing(self) -> bool:\n        return self.status_code in {404, 410}\n\n\nclass EbayBudgetExceeded(EbayError):\n    pass\n''',
    "strukturierter EbayError",
)
replace_once(
    ebay,
    '''        retry = Retry(\n            total=3,\n            connect=3,\n            read=3,\n            backoff_factor=0.8,\n            status_forcelist=(429, 500, 502, 503, 504),\n            allowed_methods=frozenset({"GET", "POST"}),\n            respect_retry_after_header=True,\n        )\n        self.session.mount("https://", HTTPAdapter(max_retries=retry))\n''',
    '''        # Keine versteckten Status-Retries: jeder Browse-Versuch muss in\n        # calls_made landen, sonst kann das echte eBay-Tagesbudget überschritten\n        # werden. Retries passieren kontrolliert in _get().\n        retry = Retry(total=0, connect=0, read=0, redirect=0, status=0)\n        self.session.mount("https://", HTTPAdapter(max_retries=retry))\n''',
    "eBay versteckte Retries deaktivieren",
)
new_get = '''    def _get(\n        self,\n        path: str,\n        *,\n        params: dict[str, Any] | None = None,\n        retry_auth: bool = True,\n    ) -> dict[str, Any]:\n        retryable_statuses = {429, 500, 502, 503, 504}\n        status_retries = 0\n        network_retries = 0\n        auth_retry_left = bool(retry_auth)\n\n        while True:\n            if self.calls_made >= self.max_calls:\n                raise EbayBudgetExceeded(f"eBay-Call-Budget von {self.max_calls} ist ausgeschöpft")\n            if self.delay_seconds:\n                time.sleep(self.delay_seconds)\n            self.calls_made += 1\n\n            try:\n                response = self.session.get(\n                    f"{self.api_base}{path}",\n                    headers=self._headers(),\n                    params=params,\n                    timeout=30,\n                )\n            except requests.RequestException as exc:\n                # Konservativ mitzählen: auch ein Netzwerkfehler kann den Server\n                # bereits erreicht haben. Maximal zwei kontrollierte Wiederholungen.\n                if network_retries < 2:\n                    network_retries += 1\n                    time.sleep(min(4.0, 0.5 * (2 ** (network_retries - 1))))\n                    continue\n                raise EbayError(\n                    "eBay Browse API Netzwerkfehler nach Wiederholungen",\n                    retryable=True,\n                ) from exc\n\n            if response.status_code == 401 and auth_retry_left:\n                # Ein abgelaufener App-Token wird einmal erneuert. Der zweite\n                # Browse-Versuch wird ebenfalls sauber als Call gezählt.\n                self.token = None\n                auth_retry_left = False\n                continue\n\n            if response.status_code in retryable_statuses and status_retries < 2:\n                status_retries += 1\n                wait = min(4.0, 0.5 * (2 ** (status_retries - 1)))\n                raw_retry_after = response.headers.get("Retry-After")\n                if raw_retry_after:\n                    try:\n                        wait = min(10.0, max(wait, float(raw_retry_after)))\n                    except (TypeError, ValueError):\n                        pass\n                time.sleep(wait)\n                continue\n\n            if response.status_code >= 400:\n                message = ""\n                try:\n                    body = response.json()\n                    errors = body.get("errors") or []\n                    if errors:\n                        message = str(errors[0].get("message") or errors[0].get("longMessage") or "")\n                except Exception:\n                    message = ""\n                hint = (\n                    " Prüfe außerdem, ob deine App für die Browse API in Production freigeschaltet ist."\n                    if response.status_code in {401, 403}\n                    else ""\n                )\n                raise EbayError(\n                    f"eBay Browse API fehlgeschlagen ({response.status_code})"\n                    + (f": {message}" if message else "")\n                    + hint,\n                    status_code=response.status_code,\n                    retryable=response.status_code in retryable_statuses,\n                )\n\n            try:\n                return response.json()\n            except ValueError as exc:\n                raise EbayError("eBay Browse API lieferte keine gültige JSON-Antwort") from exc\n\n'''
replace_block(ebay, "    def _get(\n", "    def search(\n", new_get, "eBay _get neu")


# ---------------------------------------------------------------------------
# Live-Check: nur echte 404/410 als beendet behandeln. Transiente Fehler dürfen
# ein aktives Angebot nicht aus dem Dashboard löschen.
# ---------------------------------------------------------------------------
live = "psa_sniper/live_check.py"
replace_once(
    live,
    "from .ebay import EbayClient, EbayError\n",
    "from .ebay import EbayBudgetExceeded, EbayClient, EbayError\n",
    "Live-Check Budget import",
)
replace_once(
    live,
    '''    try:\n        live = ebay.get_item(hit.listing.item_id, compact=True)\n    except EbayError:\n        return None, "unavailable"\n''',
    '''    try:\n        live = ebay.get_item(hit.listing.item_id, compact=True)\n    except EbayBudgetExceeded:\n        return None, "budget"\n    except EbayError as exc:\n        if exc.missing:\n            return None, "ended"\n        return None, "check_failed"\n''',
    "Live-Check Fehlerklassifikation",
)


# ---------------------------------------------------------------------------
# Repricing: transiente Live-Fehler nicht als beendet markieren; Refresh darf
# starke PSA-Sales nicht durch schwächere aktive Asking-Prices ersetzen.
# ---------------------------------------------------------------------------
repricing = "psa_sniper/repricing.py"
replace_once(
    repricing,
    '''    expired: int = 0\n    secondary: list[ScoredHit] = field(default_factory=list)\n''',
    '''    expired: int = 0\n    live_errors: int = 0\n    budget_stops: int = 0\n    secondary: list[ScoredHit] = field(default_factory=list)\n''',
    "Repricing Result Fehlerzähler",
)
replace_once(
    repricing,
    '''def _prefer_market(current: MarketValue | None, candidate: MarketValue | None) -> MarketValue | None:\n    if candidate is None:\n        return current\n    return candidate if _market_key(candidate) > _market_key(current) else current\n''',
    '''def _prefer_market(\n    current: MarketValue | None,\n    candidate: MarketValue | None,\n    *,\n    refresh_same_type: bool = False,\n) -> MarketValue | None:\n    if candidate is None:\n        return current\n    if current is None:\n        return candidate\n    if _market_key(candidate) > _market_key(current):\n        return candidate\n    if (\n        refresh_same_type\n        and candidate.market_type == current.market_type\n        and candidate.confidence.casefold() == current.confidence.casefold()\n    ):\n        # Gleiche Quellenklasse: beim fälligen Refresh soll der frische Markt\n        # den alten Wert ersetzen. Eine schwächere Quellenklasse darf dagegen\n        # niemals PSA-Sales oder eine bessere Quelle überschreiben.\n        return candidate\n    return current\n''',
    "Repricing Marktpräferenz",
)
replace_once(
    repricing,
    '''        if row.get("availability_status") in {"ended", "unavailable"}:\n            continue\n''',
    '''        availability = str(row.get("availability_status") or "active")\n        if availability in {"ended", "unavailable"}:\n            continue\n''',
    "Repricing Availability lesen",
)
replace_once(
    repricing,
    '''        # Current buy hits always receive a fresh eBay COMPACT validation before\n        # the dashboard/alerts are finalized.\n        if row.get("is_hit"):\n            priority = 10_000_000_000 + float(row.get("score") or 0) * 1_000_000\n            candidates.append((index, row, priority))\n            continue\n''',
    '''        # Current buy hits and previously failed live checks receive a fresh\n        # COMPACT validation with highest priority.\n        if row.get("is_hit") or availability == "check_failed":\n            priority = 10_000_000_000 + float(row.get("score") or 0) * 1_000_000\n            candidates.append((index, row, priority))\n            continue\n''',
    "Repricing Check-Failed priorisieren",
)
replace_once(
    repricing,
    '''        try:\n            live = ebay.get_item(stored.item_id, compact=True)\n            result.live_rechecks += 1\n        except (EbayError, EbayBudgetExceeded):\n            updated = dict(old)\n            updated["availability_status"] = "unavailable"\n            updated["availability_checked_at"] = now_text\n            updated["is_hit"] = False\n            updated["price_status"] = "unavailable"\n            history[index] = updated\n            result.expired += 1\n            continue\n''',
    '''        try:\n            live = ebay.get_item(stored.item_id, compact=True)\n            result.live_rechecks += 1\n        except EbayBudgetExceeded:\n            result.notes.append("Repricing: Live-Recheck wegen eBay-Budget gestoppt")\n            result.budget_stops += 1\n            break\n        except EbayError as exc:\n            updated = dict(old)\n            updated["availability_checked_at"] = now_text\n            updated["is_hit"] = False\n            if exc.missing:\n                updated["availability_status"] = "unavailable"\n                updated["price_status"] = "unavailable"\n                updated.pop("availability_error", None)\n                result.expired += 1\n            else:\n                updated["availability_status"] = "check_failed"\n                updated["availability_error"] = "temporary"\n                result.live_errors += 1\n                result.notes.append("Repricing: mindestens ein Live-Recheck war vorübergehend nicht möglich")\n            history[index] = updated\n            continue\n''',
    "Repricing Live-Fehler sicher behandeln",
)
replace_once(
    repricing,
    '''                    if force_refresh and comp_market is not None:\n                        market = comp_market\n                    else:\n                        market = _prefer_market(market, comp_market)\n''',
    '''                    market = _prefer_market(\n                        market,\n                        comp_market,\n                        refresh_same_type=force_refresh,\n                    )\n''',
    "Repricing starke Quelle erhalten",
)
replace_once(
    repricing,
    '''        updated["availability_status"] = "active"\n        updated["availability_checked_at"] = now_text\n''',
    '''        updated["availability_status"] = "active"\n        updated.pop("availability_error", None)\n        updated["availability_checked_at"] = now_text\n''',
    "Repricing Livefehler nach Erfolg löschen",
)
replace_once(
    repricing,
    '''            f"{result.live_rechecks} live geprüft, {result.expired} beendet, "\n            f"{len(result.secondary)} sekundär entdeckt, {hit_count} Kauf-Hit(s), "\n''',
    '''            f"{result.live_rechecks} live geprüft, {result.expired} beendet, "\n            f"{result.live_errors} Live-Fehler, {len(result.secondary)} sekundär entdeckt, "\n            f"{hit_count} Kauf-Hit(s), "\n''',
    "Repricing Summary Livefehler",
)
replace_once(
    repricing,
    '''        latest["repricing_expired"] = result.expired\n        latest["secondary_candidates"] = len(result.secondary)\n''',
    '''        latest["repricing_expired"] = result.expired\n        latest["repricing_live_errors"] = result.live_errors\n        latest["repricing_budget_stops"] = result.budget_stops\n        latest["secondary_candidates"] = len(result.secondary)\n''',
    "Repricing Run Metriken Livefehler",
)
replace_once(
    repricing,
    '''            f"beendet={result.expired}; sekundär={len(result.secondary)}; Hits={len(repriced_hits)}; "\n''',
    '''            f"beendet={result.expired}; LiveFehler={result.live_errors}; "\n            f"sekundär={len(result.secondary)}; Hits={len(repriced_hits)}; "\n''',
    "Repricing Note Livefehler",
)
replace_once(
    repricing,
    '''        f"{result.expired} beendet, {len(result.secondary)} sekundär entdeckt, "\n        f"{len(repriced_hits)} Hits, {result.calls} eBay-Calls."\n''',
    '''        f"{result.expired} beendet, {result.live_errors} Live-Fehler, "\n        f"{len(result.secondary)} sekundär entdeckt, {len(repriced_hits)} Hits, "\n        f"{result.calls} eBay-Calls."\n''',
    "Repricing stdout Livefehler",
)


# ---------------------------------------------------------------------------
# Scoring: POP darf nur als PSA-10-POP gewertet werden, wenn PSA 10 wirklich
# durch die Cert-Daten bestätigt ist.
# ---------------------------------------------------------------------------
scoring = "psa_sniper/scoring.py"
replace_once(
    scoring,
    '''    if cert and cert_trusted and cert.population is not None:\n''',
    '''    if cert and cert_trusted and is_psa10(cert.grade) and cert.population is not None:\n''',
    "POP nur bei bestätigtem PSA10",
)


# ---------------------------------------------------------------------------
# Scanner: Kauf-Hit unmittelbar vor Alert/Run-Snapshot nochmals live prüfen.
# Ein temporärer Fehler wird sicherheitshalber nicht als Kauf-Hit veröffentlicht,
# aber auch nicht als beendet archiviert.
# ---------------------------------------------------------------------------
scanner = "psa_sniper/scanner.py"
replace_once(
    scanner,
    "from .listing_market import (\n",
    "from .live_check import refresh_hit_for_purchase\nfrom .listing_market import (\n",
    "Scanner Live-Check import",
)
old = '''    channels = configured_channels()\n    for hit in hits:\n        if is_alerted(state, hit.listing.item_id):\n            continue\n        statuses = notify(hit)\n        if not channels or any(statuses.values()):\n            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True})\n        else:\n            notes.append("Mindestens ein Alert konnte nicht zugestellt werden; Live-Recheck/Alert fehlgeschlagen")\n'''
new = '''    # Ein frisch bewerteter Kauf-Hit wird unmittelbar vor Alert und Snapshot\n    # nochmals live geladen. Preisänderungen oder ein beendetes Angebot können\n    # ihn dadurch noch sicher zu einer Beobachtung herabstufen.\n    live_hits: list[ScoredHit] = []\n    live_demoted: list[ScoredHit] = []\n    for hit in hits:\n        refreshed, live_status = refresh_hit_for_purchase(hit, ebay, settings)\n        if live_status == "active" and refreshed is not None:\n            live_hits.append(refreshed)\n            upsert_history(state, refreshed, threshold)\n            continue\n        if live_status == "no_longer_hit" and refreshed is not None:\n            upsert_history(state, refreshed, threshold)\n            if dashboard_min <= refreshed.score < threshold:\n                live_demoted.append(refreshed)\n            notes.append("Mindestens ein Kauf-Hit wurde nach Live-Preisprüfung zur Beobachtung herabgestuft")\n            continue\n\n        availability = "ended" if live_status == "ended" else "check_failed"\n        for row in state.get("history", []):\n            if isinstance(row, dict) and row.get("item_id") == hit.listing.item_id:\n                row["availability_status"] = availability\n                row["availability_checked_at"] = iso_z(utc_now())\n                row["is_hit"] = False\n                if availability == "ended":\n                    row["price_status"] = "unavailable"\n                    row.pop("availability_error", None)\n                else:\n                    row["availability_error"] = "temporary"\n                break\n        if live_status == "budget":\n            notes.append("Kauf-Hit nicht veröffentlicht: Budget für finalen Live-Recheck erschöpft")\n        else:\n            notes.append("Kauf-Hit nicht veröffentlicht: finaler Live-Recheck vorübergehend fehlgeschlagen")\n\n    hits = live_hits\n    if live_demoted:\n        existing_near = {row.listing.item_id for row in near_hits}\n        near_hits.extend(row for row in live_demoted if row.listing.item_id not in existing_near)\n        near_hits.sort(key=lambda row: row.score, reverse=True)\n\n    channels = configured_channels()\n    for hit in hits:\n        if is_alerted(state, hit.listing.item_id):\n            continue\n        statuses = notify(hit)\n        if not channels or any(statuses.values()):\n            mark_alerted(state, hit.listing.item_id, statuses or {"dashboard": True})\n        else:\n            notes.append("Mindestens ein Alert konnte nicht zugestellt werden")\n'''
replace_once(scanner, old, new, "Scanner finaler Live-Recheck")


# ---------------------------------------------------------------------------
# Dashboard: temporär fehlgeschlagener Live-Check ist KEIN Kauf-Hit, bleibt aber
# sichtbar und wird nicht wie ein tatsächlich beendetes Listing versteckt.
# ---------------------------------------------------------------------------
dashboard = "psa_sniper/dashboard.py"
replace_once(
    dashboard,
    '''def _infer_price_status(row: dict[str, Any]) -> str:\n    if str(row.get("availability_status") or "active") in {"ended", "unavailable"}:\n        return "unavailable"\n''',
    '''def _infer_price_status(row: dict[str, Any]) -> str:\n    availability = str(row.get("availability_status") or "active")\n    if availability in {"ended", "unavailable"}:\n        return "unavailable"\n    if availability == "check_failed":\n        return "live_check_failed"\n''',
    "Dashboard Livecheck Status",
)
replace_once(
    dashboard,
    '''            "  if (status === 'unavailable') {\\n"\n            "    return { tone: 'bad', title: 'Nicht mehr verfügbar', text: 'Das Zielangebot wurde live geprüft und ist beendet oder nicht mehr kaufbar.' };\\n"\n            "  }\\n"\n            "  if (status === 'auction') {",\n''',
    '''            "  if (status === 'unavailable') {\\n"\n            "    return { tone: 'bad', title: 'Nicht mehr verfügbar', text: 'Das Zielangebot wurde live geprüft und ist beendet oder nicht mehr kaufbar.' };\\n"\n            "  }\\n"\n            "  if (status === 'live_check_failed') {\\n"\n            "    return { tone: 'warn', title: 'Live-Recheck fehlgeschlagen', text: 'Der letzte Verfügbarkeitscheck war vorübergehend nicht möglich. Bis zur nächsten erfolgreichen Prüfung ist dies ausdrücklich kein Kauf-Hit.' };\\n"\n            "  }\\n"\n            "  if (status === 'auction') {",\n''',
    "Dashboard Livecheck Erklärung",
)
replace_once(
    dashboard,
    '''    ['Live-Rechecks', run?.repricing_live_rechecks ?? 0, `${run?.repricing_expired ?? 0} beendet / nicht verfügbar`],\n''',
    '''    ['Live-Rechecks', run?.repricing_live_rechecks ?? 0, `${run?.repricing_expired ?? 0} beendet · ${run?.repricing_live_errors ?? 0} temporäre Fehler`],\n''',
    "Dashboard Repricing Livefehler",
)


# ---------------------------------------------------------------------------
# Quota-Allokation: Discovery + Markt-Comps + Repricing müssen zusammen in das
# erlaubte Run-Budget passen, nicht nur jeweils einzeln.
# ---------------------------------------------------------------------------
quota = "psa_sniper/quota.py"
insert_marker = '''def _merge_override(values: dict[str, int]) -> None:\n'''
text = read(quota)
idx = text.find(insert_marker)
if idx < 0:
    raise RuntimeError("Quota helper marker fehlt")
helper = '''def allocate_call_budgets(allowed: int, settings: dict) -> dict[str, int]:\n    allowed = max(0, int(allowed))\n    search = min(max(0, int(settings.get("max_search_calls_per_run", 24))), allowed)\n    remaining = max(0, allowed - search)\n\n    # Bei vollem 575er-Budget ergeben die Caps weiterhin 80 Markt- + 60\n    # Repricing-Calls. Unter Quotendruck werden Wartungspfade proportional\n    # reduziert, damit Discovery-Details nicht komplett verhungern.\n    market = min(\n        max(0, int(settings.get("max_market_comp_calls_per_run", 80))),\n        max(0, round(remaining * 0.18)),\n    )\n    reprice = min(\n        max(0, int(settings.get("max_reprice_comp_calls_per_run", 60))),\n        max(0, round(remaining * 0.12)),\n    )\n    detail = min(\n        max(0, int(settings.get("max_detail_calls_per_run", 470))),\n        max(0, remaining - market - reprice),\n    )\n    return {\n        "search": search,\n        "detail": detail,\n        "market": market,\n        "reprice": reprice,\n    }\n\n\n'''
write(quota, text[:idx] + helper + text[idx:])
old = '''    search_calls = int(settings.get("max_search_calls_per_run", 24))\n    market_budget = min(int(settings.get("max_market_comp_calls_per_run", 80)), max(8, allowed // 4))\n    reprice_budget = min(int(settings.get("max_reprice_comp_calls_per_run", 60)), max(4, allowed // 6))\n    detail_budget = min(\n        int(settings.get("max_detail_calls_per_run", 470)),\n        max(0, allowed - search_calls - max(market_budget, reprice_budget)),\n    )\n    _merge_override(\n        {\n            "max_ebay_calls_per_run": allowed,\n            "max_detail_calls_per_run": detail_budget,\n            "max_market_comp_calls_per_run": market_budget,\n            "max_reprice_comp_calls_per_run": reprice_budget,\n        }\n    )\n'''
new = '''    budgets = allocate_call_budgets(allowed, settings)\n    _merge_override(\n        {\n            "max_ebay_calls_per_run": allowed,\n            "max_search_calls_per_run": budgets["search"],\n            "max_detail_calls_per_run": budgets["detail"],\n            "max_market_comp_calls_per_run": budgets["market"],\n            "max_reprice_comp_calls_per_run": budgets["reprice"],\n        }\n    )\n'''
replace_once(quota, old, new, "Quota Gesamtallokation")


# Doctor soll die konfigurierten Limits/Reserve statt harter 5000 verwenden.
doctor = "psa_sniper/doctor.py"
replace_once(
    doctor,
    '''    daily = max_calls * runs_per_day\n    reserve = 5000 - daily\n    if daily > 5000:\n        level = "FEHLER"\n        ok = False\n    elif daily > 4800:\n        level = "WARNUNG"\n    else:\n        level = "OK"\n''',
    '''    daily = max_calls * runs_per_day\n    daily_limit = int(settings.get("ebay_daily_call_limit", 5000))\n    required_reserve = int(settings.get("ebay_daily_reserve_calls", 350))\n    reserve = daily_limit - daily\n    if daily > daily_limit:\n        level = "FEHLER"\n        ok = False\n    elif reserve < required_reserve:\n        level = "WARNUNG"\n    else:\n        level = "OK"\n''',
    "Doctor dynamisches Tageslimit",
)
replace_once(
    doctor,
    '''            f"Puffer zu 5000: {reserve}",\n''',
    '''            f"Puffer zu {daily_limit}: {reserve} (Zielreserve {required_reserve})",\n''',
    "Doctor Reserve Text",
)


# Scheduler: bei bereits queued/in-progress Scanner-Run keinen weiteren Dispatch
# stapeln. Der 5-Minuten-Heartbeat bleibt unverändert.
scheduler = ".github/workflows/sniper-schedule.yml"
replace_once(
    scheduler,
    '''        run: |\n          set -euo pipefail\n          gh api \\\n''',
    '''        run: |\n          set -euo pipefail\n          in_progress="$(gh api "/repos/$REPO/actions/workflows/sniper.yml/runs?status=in_progress&per_page=1" --jq '.total_count // 0')"\n          queued="$(gh api "/repos/$REPO/actions/workflows/sniper.yml/runs?status=queued&per_page=1" --jq '.total_count // 0')"\n          if (( in_progress > 0 || queued > 0 )); then\n            echo "Scanner läuft bereits oder ist queued; kein doppelter Dispatch."\n            exit 0\n          fi\n          gh api \\\n''',
    "Scheduler Dispatch dedupe",
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
Path("tests/test_ebay_reliability.py").write_text('''from __future__ import annotations\n\nimport pytest\n\nfrom psa_sniper.ebay import EbayBudgetExceeded, EbayClient, EbayError\n\n\nclass Response:\n    def __init__(self, status, payload=None, headers=None):\n        self.status_code = status\n        self._payload = payload if payload is not None else {}\n        self.headers = headers or {}\n\n    def json(self):\n        return self._payload\n\n\nclass SequenceSession:\n    def __init__(self, responses):\n        self.responses = list(responses)\n        self.calls = 0\n\n    def get(self, *args, **kwargs):\n        self.calls += 1\n        return self.responses.pop(0)\n\n\ndef client(max_calls=5):\n    c = EbayClient("id", "secret", delay_seconds=0, max_calls=max_calls)\n    c.token = "token"\n    return c\n\n\ndef test_retry_attempts_are_counted_against_browse_budget(monkeypatch):\n    c = client()\n    c.session = SequenceSession([Response(503), Response(200, {"ok": True})])\n    monkeypatch.setattr("psa_sniper.ebay.time.sleep", lambda *_: None)\n    assert c._get("/x") == {"ok": True}\n    assert c.calls_made == 2\n    assert c.session.calls == 2\n\n\ndef test_retry_cannot_escape_hard_call_cap(monkeypatch):\n    c = client(max_calls=1)\n    c.session = SequenceSession([Response(503), Response(200, {"ok": True})])\n    monkeypatch.setattr("psa_sniper.ebay.time.sleep", lambda *_: None)\n    with pytest.raises(EbayBudgetExceeded):\n        c._get("/x")\n    assert c.calls_made == 1\n\n\ndef test_http_error_exposes_status_and_missing_flag(monkeypatch):\n    c = client()\n    c.session = SequenceSession([Response(404, {"errors": [{"message": "gone"}]})])\n    monkeypatch.setattr("psa_sniper.ebay.time.sleep", lambda *_: None)\n    with pytest.raises(EbayError) as caught:\n        c._get("/item/x")\n    assert caught.value.status_code == 404\n    assert caught.value.missing is True\n''', encoding="utf-8")

# Live-check Tests erweitern.
path = Path("tests/test_live_check.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from psa_sniper.live_check import listing_available, merge_live_listing, refresh_hit_for_purchase\n",
    "from psa_sniper.ebay import EbayBudgetExceeded, EbayError\nfrom psa_sniper.live_check import listing_available, merge_live_listing, refresh_hit_for_purchase\n",
)
text += '''\n\nclass FailingEbay:\n    def __init__(self, exc):\n        self.exc = exc\n    def get_item(self, item_id, *, compact=False):\n        raise self.exc\n\n\ndef _hit():\n    stored = listing(80)\n    return ScoredHit(\n        listing=stored, score=13, reasons=[],\n        market_value=MarketValue(\n            Money(120, "EUR"), "eBay", "mittel", 4,\n            market_type="ebay_active", required_edge=0.20,\n        ),\n        discount_pct=1 - 80 / 120, price_status="verified_edge",\n    )\n\n\ndef test_transient_live_error_is_not_misclassified_as_ended():\n    refreshed, status = refresh_hit_for_purchase(\n        _hit(), FailingEbay(EbayError("temporary", status_code=503, retryable=True)),\n        {"hit_threshold": 11},\n    )\n    assert refreshed is None\n    assert status == "check_failed"\n\n\ndef test_404_live_error_is_ended():\n    _, status = refresh_hit_for_purchase(\n        _hit(), FailingEbay(EbayError("gone", status_code=404)), {"hit_threshold": 11}\n    )\n    assert status == "ended"\n\n\ndef test_live_budget_exhaustion_is_distinct():\n    _, status = refresh_hit_for_purchase(\n        _hit(), FailingEbay(EbayBudgetExceeded("budget")), {"hit_threshold": 11}\n    )\n    assert status == "budget"\n'''
path.write_text(text, encoding="utf-8")

# Repricing Tests an neue Fehlersemantik anpassen und Quellenrefresh absichern.
path = Path("tests/test_repricing.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from psa_sniper.models import Listing, Money\nfrom psa_sniper.repricing import listing_from_history, reprice_state\n",
    "from psa_sniper.models import Listing, MarketValue, Money\nfrom psa_sniper.repricing import _prefer_market, listing_from_history, reprice_state\n",
)
text = text.replace(
    "    def __init__(self, rows, live=None, fail_live=False):\n",
    "    def __init__(self, rows, live=None, fail_live=False, fail_status=None):\n",
)
text = text.replace(
    "        self.fail_live = fail_live\n",
    "        self.fail_live = fail_live\n        self.fail_status = fail_status\n",
)
text = text.replace(
    "            raise EbayError(\"gone\")\n",
    "            raise EbayError(\"gone\", status_code=self.fail_status)\n",
)
text = text.replace(
    "    ebay = FakeEbay([], fail_live=True)\n",
    "    ebay = FakeEbay([], fail_live=True, fail_status=404)\n",
)
text += '''\n\ndef test_repricing_transient_live_error_keeps_listing_for_retry():\n    state = default_state()\n    row = weak_row()\n    row["is_hit"] = True\n    row["price_status"] = "verified_edge"\n    state["history"] = [row]\n    ebay = FakeEbay([], fail_live=True, fail_status=503)\n    result = reprice_state(state, settings(), ebay, IdentityFX(), max_comp_calls=3)\n    assert result.checked == 1\n    assert result.expired == 0\n    assert result.live_errors == 1\n    updated = state["history"][0]\n    assert updated["availability_status"] == "check_failed"\n    assert updated["is_hit"] is False\n    assert updated["price_status"] == "verified_edge"\n\n\ndef test_refresh_never_replaces_psa_sales_with_weaker_active_market():\n    sales = MarketValue(\n        Money(200, "EUR"), "PSA Sales", "hoch", 5,\n        market_type="psa_sales", required_edge=0.10,\n    )\n    active = MarketValue(\n        Money(180, "EUR"), "eBay", "mittel", 5,\n        market_type="ebay_active", required_edge=0.20, unique_sellers=4,\n    )\n    assert _prefer_market(sales, active, refresh_same_type=True) is sales\n\n\ndef test_refresh_replaces_stale_market_of_same_quality_class():\n    old = MarketValue(\n        Money(180, "EUR"), "eBay", "mittel", 5,\n        market_type="ebay_active", required_edge=0.20, unique_sellers=4,\n    )\n    fresh = MarketValue(\n        Money(160, "EUR"), "eBay", "mittel", 5,\n        market_type="ebay_active", required_edge=0.20, unique_sellers=4,\n    )\n    assert _prefer_market(old, fresh, refresh_same_type=True) is fresh\n'''
path.write_text(text, encoding="utf-8")

# POP ohne bestätigten Grade darf keinen PSA10-POP-Bonus geben.
path = Path("tests/test_scoring.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_population_without_confirmed_psa10_grade_gets_no_low_pop_bonus():\n    cert = _cert()\n    cert.grade = None\n    cert.population = 1\n    listing = Listing(\n        item_id="no-grade", title="2021 Bundesliga PSA 10 #16",\n        url="https://example.test/no-grade", price=Money(40, "EUR"),\n        created_at=datetime.now(timezone.utc), buying_options=["FIXED_PRICE"],\n    )\n    hit = score_hit(\n        listing, cert_number=cert.cert_number, cert_source="Item-Specifics",\n        cert=cert, market_value_listing_currency=None,\n        priority_terms=[], demand_terms=[],\n    )\n    assert not any("Population" in reason for reason in hit.reasons)\n'''
path.write_text(text, encoding="utf-8")

Path("tests/test_quota.py").write_text('''from psa_sniper.quota import allocate_call_budgets\n\n\ndef settings():\n    return {\n        "max_search_calls_per_run": 24,\n        "max_detail_calls_per_run": 470,\n        "max_market_comp_calls_per_run": 80,\n        "max_reprice_comp_calls_per_run": 60,\n    }\n\n\ndef test_full_budget_reserves_market_and_repricing_inside_hardcap():\n    value = allocate_call_budgets(575, settings())\n    assert value == {"search": 24, "detail": 411, "market": 80, "reprice": 60}\n    assert sum(value.values()) == 575\n\n\ndef test_low_budget_scales_maintenance_and_preserves_details():\n    value = allocate_call_budgets(100, settings())\n    assert sum(value.values()) <= 100\n    assert value["search"] == 24\n    assert value["detail"] > 0\n    assert value["market"] < 80\n    assert value["reprice"] < 60\n''', encoding="utf-8")

# Dashboard Build muss Live-Recheck-Fehler darstellen.
path = Path("tests/test_crypto_dashboard.py")
text = path.read_text(encoding="utf-8")
text += '''\n\ndef test_live_check_failed_is_visible_but_never_a_hit():\n    state = {\n        "history": [{\n            "item_id": "live-error", "title": "PSA 10",\n            "last_seen_at": "2026-08-29T10:00:00Z",\n            "availability_status": "check_failed",\n            "price_status": "verified_edge", "is_hit": True, "score": 14,\n        }],\n        "runs": [],\n    }\n    row = dashboard_payload(state)["hits"][0]\n    assert row["price_status"] == "live_check_failed"\n    assert row["is_hit"] is False\n'''
path.write_text(text, encoding="utf-8")

print("Audit-v2 reliability patch applied")
