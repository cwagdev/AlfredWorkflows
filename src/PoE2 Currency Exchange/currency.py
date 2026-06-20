#!/usr/bin/env python3
"""Alfred Script Filter: PoE2 currency exchange lookup via poe2scout.com.

Usage from Alfred (keyword `poe2`):
    poe2 divine                -> price of Divine Orb (in Exalted, with Divine ref)
    poe2 mirror in divine      -> Mirror priced in Divine Orbs
    poe2 chaos in exalt        -> Chaos priced in Exalted
    poe2 @fragments breach     -> search the "fragments" category instead

All currency `currentPrice` values returned by poe2scout are denominated in the
league's base currency (Exalted Orb), so an exchange rate between any two items
A and B is simply price(A) / price(B). We fetch the whole category once, cache it
for a few minutes, then filter / convert locally so typing stays instant.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

API_BASE = os.environ.get("API_BASE", "https://poe2scout.com/api").rstrip("/")
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "AlfredWorkflow-poe2-currency (https://github.com/cwagdev/AlfredWorkflows)",
)
DEFAULT_CATEGORY = os.environ.get("CATEGORY", "currency").strip() or "currency"
LEAGUE_OVERRIDE = os.environ.get("LEAGUE", "").strip()
CACHE_TTL = int(os.environ.get("CACHE_TTL", "600"))  # seconds
HTTP_TIMEOUT = 12


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def cache_dir():
    d = os.environ.get("alfred_workflow_cache") or os.path.join(
        os.path.expanduser("~"), ".cache", "poe2-currency"
    )
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = "/tmp"
    return d


def cache_read(name):
    path = os.path.join(cache_dir(), name)
    try:
        if time.time() - os.path.getmtime(path) < CACHE_TTL:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except (OSError, ValueError):
        pass
    return None


def cache_write(name, data):
    try:
        with open(os.path.join(cache_dir(), name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def g(d, *keys, default=None):
    """Get the first present key (handles camelCase / snake_case drift)."""
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
    return default


def fmt(n):
    if n is None:
        return "?"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return str(n)
    if n == 0:
        return "0"
    a = abs(n)
    if a >= 1000:
        return f"{n:,.0f}"
    if a >= 100:
        return f"{n:.1f}"
    if a >= 1:
        return f"{n:.2f}"
    if a >= 0.01:
        return f"{n:.3f}"
    return f"{n:.5f}".rstrip("0").rstrip(".")


def alfred(items, rerun=None):
    out = {"items": items}
    if rerun:
        out["rerun"] = rerun
    sys.stdout.write(json.dumps(out))


def error_item(title, subtitle=""):
    alfred([{"title": title, "subtitle": subtitle, "valid": False, "icon": {"path": "icon.png"}}])
    sys.exit(0)


# ---------------------------------------------------------------------------
# data access
# ---------------------------------------------------------------------------
def resolve_league():
    if LEAGUE_OVERRIDE:
        return LEAGUE_OVERRIDE
    cached = cache_read("league.json")
    if cached:
        return cached
    leagues = http_json(f"{API_BASE}/leagues")
    if isinstance(leagues, dict):  # some shapes wrap the list
        leagues = g(leagues, "leagues", "items", "data", default=leagues)
    if not isinstance(leagues, list) or not leagues:
        raise RuntimeError("Unexpected /leagues response")

    def name_of(lg):
        return g(lg, "value", "name", "id", "leagueId", default="")

    # Prefer an explicit "current" flag.
    current = [lg for lg in leagues if g(lg, "isCurrent", "is_current", "current")]
    if current:
        league = name_of(current[0])
    else:
        # Otherwise the first temp league that isn't a permanent one.
        perm = {"standard", "hardcore", "ssf standard", "ssf hardcore"}
        temp = [lg for lg in leagues if name_of(lg).lower() not in perm]
        league = name_of((temp or leagues)[0])
    if not league:
        raise RuntimeError("Could not determine current league")
    cache_write("league.json", league)
    return league


def fetch_category(league, category):
    safe = re.sub(r"[^a-z0-9]+", "_", f"{league}_{category}".lower())
    cached = cache_read(f"cur_{safe}.json")
    if cached is not None:
        return cached

    items, page, max_pages = [], 1, 8
    while page <= max_pages:
        params = {"page": str(page), "perPage": "200"}
        if league:
            params["league"] = league
        url = f"{API_BASE}/items/currency/{urllib.parse.quote(category)}?{urllib.parse.urlencode(params)}"
        data = http_json(url)
        chunk = data.get("items") if isinstance(data, dict) else data
        if not isinstance(chunk, list):
            chunk = []
        items.extend(chunk)
        pages = g(data, "pages", "totalPages", default=1) if isinstance(data, dict) else 1
        try:
            pages = int(pages)
        except (TypeError, ValueError):
            pages = 1
        if page >= pages or not chunk:
            break
        page += 1

    cache_write(f"cur_{safe}.json", items)
    return items


# ---------------------------------------------------------------------------
# matching / conversion
# ---------------------------------------------------------------------------
def name_of_item(it):
    return g(it, "text", "currencyTypeName", "name", "apiId", "api_id", default="")


def price_of(it):
    p = g(it, "currentPrice", "current_price", "chaosEquivalent", "price")
    try:
        return float(p) if p is not None else None
    except (TypeError, ValueError):
        return None


def rank(it, term):
    """Lower is better."""
    name = name_of_item(it).lower()
    api = str(g(it, "apiId", "api_id", default="")).lower()
    t = term.lower()
    if not t:
        return 5
    if api == t or name == t:
        return 0
    if name.startswith(t) or api.startswith(t):
        return 1
    if t in name or t in api:
        return 2
    # token subset match
    if all(tok in name for tok in t.split()):
        return 3
    return 9


def find_best(items, term):
    cand = [it for it in items if rank(it, term) < 9 and price_of(it) is not None]
    if not cand:
        return None
    cand.sort(key=lambda it: (rank(it, term), -(price_of(it) or 0)))
    return cand[0]


def main():
    raw = (sys.argv[1] if len(sys.argv) > 1 else "").strip()

    category = DEFAULT_CATEGORY
    m = re.match(r"^@(\S+)\s*(.*)$", raw)
    if m:
        category, raw = m.group(1), m.group(2).strip()

    ref_term = None
    parts = re.split(r"\s+in\s+", raw, maxsplit=1)
    search = parts[0].strip()
    if len(parts) == 2 and parts[1].strip():
        ref_term = parts[1].strip()

    try:
        league = resolve_league()
    except Exception as exc:  # noqa: BLE001
        error_item("Couldn't load the current league", f"{exc} — check your connection")
        return

    try:
        items = fetch_category(league, category)
    except Exception as exc:  # noqa: BLE001
        error_item(f"Couldn't load '{category}' prices", str(exc))
        return

    if not items:
        error_item(f"No items found in category '{category}'", f"League: {league}")
        return

    # Reference currency for conversion (default = base = Exalted).
    ref_price, ref_label = 1.0, "Exalted Orb"
    if ref_term:
        ref_item = find_best(items, ref_term)
        if ref_item and price_of(ref_item):
            ref_price = price_of(ref_item)
            ref_label = name_of_item(ref_item)
        else:
            ref_label = "Exalted Orb (couldn't match '%s')" % ref_term

    # Helper to also show a Divine equivalent in the default view.
    divine = next(
        (it for it in items if str(g(it, "apiId", "api_id", default="")).lower() in ("divine", "divine-orb")
         or name_of_item(it).lower() == "divine orb"),
        None,
    )
    divine_price = price_of(divine) if divine else None

    # Select & rank the items to display.
    matched = [it for it in items if (not search or rank(it, search) < 9) and price_of(it) is not None]
    matched.sort(key=lambda it: (rank(it, search), -(price_of(it) or 0)))
    matched = matched[:25] if search else matched[:25]

    if not matched:
        error_item(f"No match for '{search}'", f"Category: {category} · League: {league}")
        return

    results = []
    for it in matched:
        name = name_of_item(it)
        exalted = price_of(it)
        converted = exalted / ref_price if ref_price else exalted
        arg = f"{converted:.6f}".rstrip("0").rstrip(".")

        if ref_term:
            sub = f"1 {name} = {fmt(converted)} {ref_label}   •   {league}"
            large = f"1 {name} = {fmt(converted)} {ref_label}"
        else:
            extra = ""
            if divine_price and divine_price > 0 and name.lower() != "divine orb":
                extra = f"   ({fmt(exalted / divine_price)} Divine)"
            sub = f"1 {name} = {fmt(exalted)} Exalted{extra}   •   {league}"
            large = f"1 {name}\n  = {fmt(exalted)} Exalted" + (f"\n  = {fmt(exalted / divine_price)} Divine" if divine_price else "")

        results.append({
            "uid": str(g(it, "apiId", "api_id", default=name)),
            "title": f"{name} — {fmt(converted)} {ref_label.split(' (')[0] if ref_term else 'Exalted'}",
            "subtitle": sub,
            "arg": arg,
            "valid": True,
            "icon": {"path": "icon.png"},
            "text": {"copy": arg, "largetype": large},
            "mods": {
                "cmd": {"valid": True, "arg": name, "subtitle": f"Copy name: {name}"},
            },
        })

    alfred(results)


if __name__ == "__main__":
    main()
