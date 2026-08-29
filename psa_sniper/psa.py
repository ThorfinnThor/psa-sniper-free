from __future__ import annotations

import re
import time
from collections.abc import Iterable
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Money, PSACertInfo
from .util import normalize_text, parse_int

PSA_API_URL = "https://api.psacard.com/publicapi/cert/GetByCertNumber/{cert}"
PSA_CERT_URL = "https://www.psacard.com/cert/{cert}/psa"
PSA_TOKEN_TEST_CERT = "67205095"


class PSABudgetExceeded(RuntimeError):
    pass


class PSAClient:
    def __init__(
        self,
        *,
        access_token: str | None = None,
        web_fallback: bool = True,
        delay_seconds: float = 0.8,
        max_calls: int = 8,
    ) -> None:
        self.access_token = access_token.strip() if access_token else None
        self.web_fallback = web_fallback
        self.delay_seconds = max(0.0, delay_seconds)
        self.max_calls = max_calls
        self.calls_made = 0
        self.web_rate_limited = False
        self.api_rate_limited = False
        self.api_auth_status = "nicht_getestet"
        self.api_successes = 0
        self.api_failure_streak = 0
        self.api_disabled_reason: str | None = None
        self.session = requests.Session()
        # PSA documents HTTP 500 as commonly meaning invalid credentials. Do not
        # blindly retry 500; it is handled by the circuit breaker below.
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=1.0,
            status_forcelist=(502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; psa-sniper-free/1.0; personal research)",
                "Accept-Language": "en-US,en;q=0.8",
            }
        )

    def _disable_api(self, reason: str) -> None:
        self.api_disabled_reason = reason
        self.access_token = None

    def _spend_call(self) -> None:
        if self.calls_made >= self.max_calls:
            raise PSABudgetExceeded(f"PSA-Call-Budget von {self.max_calls} ist ausgeschöpft")
        self.calls_made += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)

    def validate_access_token(self, cert_number: str = PSA_TOKEN_TEST_CERT) -> str:
        if not self.access_token:
            self.api_auth_status = "nicht_konfiguriert"
            return self.api_auth_status
        try:
            self._get_api(cert_number)
        except PSABudgetExceeded:
            self.api_auth_status = "budget"
        return self.api_auth_status

    def get_cert(self, cert_number: str) -> PSACertInfo | None:
        if self.access_token and not self.api_rate_limited:
            info = self._get_api(cert_number)
            if info and info.valid:
                return info
        if self.web_fallback and not self.web_rate_limited:
            info = self._get_web(cert_number)
            if info and info.valid:
                return info
        return None

    def enrich_market_data(self, cert: PSACertInfo) -> PSACertInfo:
        if cert.recent_sales or cert.estimate:
            return cert
        if not self.web_fallback or self.web_rate_limited:
            return cert
        info = self._get_web(cert.cert_number)
        if not info or not info.valid:
            return cert
        return PSACertInfo(
            cert_number=cert.cert_number,
            valid=cert.valid or info.valid,
            grade=cert.grade or info.grade,
            year=cert.year or info.year,
            brand_title=cert.brand_title or info.brand_title,
            subject=cert.subject or info.subject,
            card_number=cert.card_number or info.card_number,
            category=cert.category or info.category,
            variety=cert.variety or info.variety,
            population=cert.population if cert.population is not None else info.population,
            population_higher=(cert.population_higher if cert.population_higher is not None else info.population_higher),
            estimate=info.estimate,
            recent_sales=info.recent_sales,
            source_url=cert.source_url or info.source_url,
            data_source=(
                "PSA Public API + öffentliche PSA-Cert-Seite"
                if cert.data_source
                else info.data_source
            ),
        )

    def _get_api(self, cert_number: str) -> PSACertInfo | None:
        was_initial_validation = self.api_auth_status == "nicht_getestet"
        self._spend_call()
        try:
            response = self.session.get(
                PSA_API_URL.format(cert=cert_number),
                headers={"Authorization": f"bearer {self.access_token}", "Accept": "application/json"},
                timeout=30,
            )
        except requests.RequestException:
            self.api_auth_status = "netzwerkfehler"
            self.api_failure_streak += 1
            if self.api_failure_streak >= 2:
                self._disable_api("network")
            return None
        if response.status_code in {401, 403}:
            self.api_auth_status = "abgelehnt"
            self.api_failure_streak += 1
            self._disable_api("auth")
            return None
        if response.status_code == 429:
            self.api_auth_status = "rate_limited"
            self.api_rate_limited = True
            self._disable_api("rate_limit")
            return None
        if response.status_code >= 500:
            self.api_auth_status = "servicefehler"
            self.api_failure_streak += 1
            # PSA says 500 usually indicates invalid credentials. On the initial
            # token validation, disable immediately; during a run, two consecutive
            # 5xx responses open the circuit breaker.
            if was_initial_validation or self.api_failure_streak >= 2:
                self._disable_api("server_or_credentials")
            return None
        if response.status_code >= 400:
            self.api_auth_status = "http_fehler"
            self.api_failure_streak += 1
            if self.api_failure_streak >= 2:
                self._disable_api("http")
            return None

        self.api_failure_streak = 0
        self.api_auth_status = "ok"
        try:
            payload = response.json()
        except ValueError:
            return None
        info = parse_psa_api_json(cert_number, payload)
        info.source_url = PSA_API_URL.format(cert=cert_number)
        info.data_source = "PSA Public API"
        if info.valid:
            self.api_successes += 1
        return info

    def _get_web(self, cert_number: str) -> PSACertInfo | None:
        self._spend_call()
        url = PSA_CERT_URL.format(cert=cert_number)
        try:
            response = self.session.get(url, timeout=30, allow_redirects=True)
        except requests.RequestException:
            return None
        if response.status_code == 429:
            self.web_rate_limited = True
            return None
        if response.status_code in {403, 404} or response.status_code >= 500:
            return None
        if response.status_code >= 400:
            return None
        info = parse_psa_cert_html(cert_number, response.text)
        info.source_url = response.url
        info.data_source = "öffentliche PSA-Cert-Seite"
        return info


def _walk_values(obj: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield normalize_text(str(key)).replace(" ", ""), value
            yield from _walk_values(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk_values(value)


def _first(payload: Any, keys: set[str]) -> Any:
    for key, value in _walk_values(payload):
        if key in keys and value is not None and value != "":
            return value
    return None


def parse_psa_api_json(cert_number: str, payload: Any) -> PSACertInfo:
    valid_raw = _first(payload, {"isvalidrequest", "valid", "isvalid"})
    server_message = normalize_text(_string(_first(payload, {"servermessage", "message"})))
    returned_cert = _first(payload, {"certnumber", "certno", "certificationnumber"})
    grade = _string(_first(payload, {"grade", "itemgrade", "gradevalue"}))
    year = _string(_first(payload, {"year"}))
    brand_title = _string(_first(payload, {"brand", "brandtitle", "title"}))
    subject = _string(_first(payload, {"subject", "player", "cardname"}))
    card_number = _string(_first(payload, {"cardnumber", "cardno"}))
    has_identity = any((returned_cert, grade, year, brand_title, subject, card_number))
    valid = bool(has_identity)
    if isinstance(valid_raw, bool) and not valid_raw:
        valid = False
    if "no data" in server_message or "invalid" in server_message:
        valid = False
    if returned_cert and str(returned_cert).strip() != cert_number:
        valid = False
    return PSACertInfo(
        cert_number=cert_number,
        valid=valid,
        grade=grade,
        year=year,
        brand_title=brand_title,
        subject=subject,
        card_number=card_number,
        category=_string(_first(payload, {"category"})),
        variety=_string(_first(payload, {"variety", "varietypedigree", "pedigree"})),
        population=parse_int(_first(payload, {"population", "totalpopulation", "poppulation"})),
        population_higher=parse_int(_first(payload, {"populationhigher", "totalhigherpopulation", "pophigher"})),
    )


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_lines(soup: BeautifulSoup) -> list[str]:
    return [line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()]


def _value_after(lines: list[str], label: str) -> str | None:
    target = label.casefold()
    for index, line in enumerate(lines):
        if line.casefold() == target:
            for candidate in lines[index + 1 : index + 6]:
                if candidate.casefold() != target:
                    return candidate.strip()
    return None


def _parse_money(value: str | None) -> Money | None:
    if not value or value.strip() in {"-", "—", "–"}:
        return None
    patterns = [
        (r"US\$\s*([\d,]+(?:\.\d{1,2})?)", "USD"),
        (r"\$\s*([\d,]+(?:\.\d{1,2})?)", "USD"),
        (r"€\s*([\d.]+(?:,\d{1,2})?|[\d,]+(?:\.\d{1,2})?)", "EUR"),
        (r"£\s*([\d,]+(?:\.\d{1,2})?)", "GBP"),
    ]
    for pattern, currency in patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        raw = match.group(1)
        if currency == "EUR" and "," in raw:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
        try:
            return Money(float(raw), currency)
        except ValueError:
            return None
    return None


def _recent_sales(text: str, max_sales: int = 8) -> list[Money]:
    marker = "Sales of Similar Items"
    if marker not in text:
        return []
    section = text.split(marker, 1)[1]
    terminators = (f"\n{marker}", "\nSet Registry", "\nNote:", "\nCompany")
    cut_positions = [pos for term in terminators if (pos := section.find(term)) >= 0]
    if cut_positions:
        section = section[: min(cut_positions)]
    token_pattern = re.compile(
        r"US\$\s*[\d,]+(?:\.\d{1,2})?|\$\s*[\d,]+(?:\.\d{1,2})?|"
        r"€\s*[\d.,]+|£\s*[\d,]+(?:\.\d{1,2})?"
    )
    found: list[Money] = []
    for token in token_pattern.findall(section):
        money = _parse_money(token)
        if money:
            found.append(money)
        if len(found) >= max_sales:
            break
    return found


def parse_psa_cert_html(cert_number: str, html: str) -> PSACertInfo:
    soup = BeautifulSoup(html, "html.parser")
    lines = _clean_lines(soup)
    text = "\n".join(lines)
    valid = (
        "According to the PSA database" in text
        or f"#{cert_number}" in text
        or _value_after(lines, "Cert Number") == cert_number
    )
    return PSACertInfo(
        cert_number=cert_number,
        valid=valid,
        grade=_value_after(lines, "Item Grade"),
        year=_value_after(lines, "Year"),
        brand_title=_value_after(lines, "Brand/Title"),
        subject=_value_after(lines, "Subject"),
        card_number=_value_after(lines, "Card Number"),
        category=_value_after(lines, "Category"),
        variety=_value_after(lines, "Variety/Pedigree"),
        population=parse_int(_value_after(lines, "PSA Population")),
        population_higher=parse_int(_value_after(lines, "PSA Pop Higher")),
        estimate=_parse_money(_value_after(lines, "PSA Estimate")),
        recent_sales=_recent_sales(text),
    )
