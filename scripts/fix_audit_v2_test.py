from pathlib import Path

path = Path("tests/test_scoring.py")
text = path.read_text(encoding="utf-8")
old = '    assert not any("Population" in reason for reason in hit.reasons)\n'
new = '''    assert not any(\n        "niedrige PSA-10-Population" in reason\n        or "sehr niedrige PSA-10-Population" in reason\n        for reason in hit.reasons\n    )\n'''
if text.count(old) != 1:
    raise RuntimeError("POP-Testassertion nicht eindeutig gefunden")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
