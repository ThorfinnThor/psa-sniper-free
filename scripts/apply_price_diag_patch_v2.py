from pathlib import Path

# Die V1-Transformation ist korrekt, erwartete bei vier Einrückungs-sensitiven
# Mustern aber je zwei Treffer. Für den Cert-Block existiert jeweils genau einer.
source = Path("scripts/apply_price_diag_patch.py").read_text(encoding="utf-8")
source = source.replace("    count=2,\n", "    count=1,\n")
exec(compile(source, "scripts/apply_price_diag_patch.py", "exec"), {})


def replace_once(path: str, old: str, new: str, label: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: erwartet 1, gefunden {text.count(old)}")
    p.write_text(text.replace(old, new), encoding="utf-8")


scanner = "psa_sniper/scanner.py"
replace_once(
    scanner,
    '''                            rows = ebay.search(comp_query, limit=comp_search_limit, started_after=None, offset=0)\n                            market_comp_calls += 1\n                            comp_rows.extend(rows)\n''',
    '''                            rows = ebay.search(comp_query, limit=comp_search_limit, started_after=None, offset=0)\n                            diag_search_attempted = True\n                            diag_search_rows += len(rows)\n                            market_comp_calls += 1\n                            comp_rows.extend(rows)\n''',
    "Listing erste Comp-Seite",
)
replace_once(
    scanner,
    '''                                rows2 = ebay.search(\n                                    comp_query, limit=comp_search_limit,\n                                    started_after=None, offset=comp_search_limit,\n                                )\n                                market_comp_calls += 1\n                                comp_rows.extend(rows2)\n''',
    '''                                rows2 = ebay.search(\n                                    comp_query, limit=comp_search_limit,\n                                    started_after=None, offset=comp_search_limit,\n                                )\n                                diag_search_attempted = True\n                                diag_search_rows += len(rows2)\n                                market_comp_calls += 1\n                                comp_rows.extend(rows2)\n''',
    "Listing zweite Comp-Seite",
)
replace_once(
    scanner,
    '''                                exclude_item_id=listing.item_id,\n                            )\n                            if (\n''',
    '''                                exclude_item_id=listing.item_id,\n                            )\n                            diag_exact_matches = max(diag_exact_matches, len(values))\n                            if (\n''',
    "Listing erste Exact-Matches",
)
replace_once(
    scanner,
    '''                                    exclude_item_id=listing.item_id,\n                                )\n                            if len(values) >= 3:\n''',
    '''                                    exclude_item_id=listing.item_id,\n                                )\n                                diag_exact_matches = max(diag_exact_matches, len(values))\n                            if len(values) >= 3:\n''',
    "Listing zweite Exact-Matches",
)
