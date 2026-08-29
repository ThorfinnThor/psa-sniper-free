from pathlib import Path

psa = Path("tests/test_psa.py")
text = psa.read_text(encoding="utf-8")
old = '    assert client.api_disabled_reason == "server_or_credentials"\n'
# Es gibt genau den bestehenden 503-Test mit dieser alten Erwartung; der 500-Test
# verwendet denselben Text ebenfalls. Nur den 503-Testblock gezielt ändern.
block = '''    assert client.get_cert("79959649") is None\n    assert client.access_token is None\n    assert client.api_disabled_reason == "server_or_credentials"\n'''
replacement = '''    assert client.get_cert("79959649") is None\n    assert client.access_token is None\n    assert client.api_disabled_reason == "service_unavailable"\n'''
if text.count(block) != 1:
    raise RuntimeError("503 Erwartungsblock nicht eindeutig gefunden")
psa.write_text(text.replace(block, replacement, 1), encoding="utf-8")

scoring = Path("tests/test_scoring.py")
text = scoring.read_text(encoding="utf-8")
old = '    assert hit.market_value.required_edge == .30\n    assert hit.price_status == "no_edge"\n'
new = '    assert round(hit.market_value.required_edge, 6) == .30\n    assert hit.price_status == "no_edge"\n'
if text.count(old) != 1:
    raise RuntimeError("Shipping Float Assertion nicht eindeutig gefunden")
scoring.write_text(text.replace(old, new, 1), encoding="utf-8")
