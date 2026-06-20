#!/usr/bin/env python3
"""Alfred Script Filter: PoE2 currency exchange lookup via poe2scout.com.

Usage from Alfred (keyword `poe2`):
    poe2 divine                -> price of Divine Orb (in the base currency)
    poe2 mirror in divine      -> Mirror of Kalandra priced in Divine Orbs
    poe2 chaos in exalt        -> Chaos priced in Exalted
    poe2 @fragments breach     -> search a different currency category

Every currency `CurrentPrice` from poe2scout is denominated in the league base
currency (Exalted Orb), so the exchange rate between any two items A and B is
just price(A) / price(B). We fetch the whole category once, cache it for a few
minutes, then filter / convert locally so typing stays instant.

API reference: https://poe2scout.com/api/swagger  (OpenAPI servers base: /api)
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

API_BASE = os.environ.get("API_BASE", "").strip().rstrip("/")
BASE_CANDIDATES = (
    [API_BASE]
    if API_BASE
    else [
        "https://api.poe2scout.com/api",
        "https://api.poe2scout.com",
        "https://poe2scout.com/api",
    ]
)
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "AlfredWorkflow-poe2-currency (https://github.com/cwagdev/AlfredWorkflows)",
)
DEFAULT_CATEGORY = os.environ.get("CATEGORY", "currency").strip() or "currency"
REALM_OVERRIDE = os.environ.get("REALM", "").strip()
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
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def api_context():
    """Return (base_url, realms_list), auto-detecting the working API base."""
    cached = cache_read("base.json")
    if cached:
        try:
            realms = http_json(f"{cached}/Realms")
            if isinstance(realms, list) and realms:
                return cached, realms
        except Exception:  # noqa: BLE001 - fall through to re-probe
            pass

    errors = []
    for base in BASE_CANDIDATES:
        try:
            realms = http_json(f"{base}/Realms")
            if isinstance(realms, list) and realms:
                cache_write("base.json", base)
                return base, realms
            errors.append(f"{base}/Realms -> unexpected payload")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}/Realms -> {exc}")
    raise RuntimeError("; ".join(errors))


def g(d, *keys, default=None):
    """Case-insensitive lookup tolerant of PascalCase/camelCase/snake_case."""
    if not isinstance(d, dict):
        return default
    lower = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v is not None:
            return v
    return default


def to_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fmt(n):
    n = to_float(n)
    if n is None:
        return "?"
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


def alfred(items):
    sys.stdout.write(json.dumps({"items": items}))


def error_item(title, subtitle=""):
    alfred([{"title": title, "subtitle": subtitle, "valid": False, "icon": {"path": "icon.png"}}])
    sys.exit(0)


# ---------------------------------------------------------------------------
# data access  (real poe2scout API shape)
# ---------------------------------------------------------------------------
def resolve_realm(realms):
    if REALM_OVERRIDE:
        return REALM_OVERRIDE

    def is_poe2(r):
        blob = str(g(r, "value", "realm_api_id", "game_api_id", "label", default="")).lower()
        return "poe2" in blob or "poe 2" in blob

    chosen = next((r for r in realms if is_poe2(r)), realms[0])
    return g(chosen, "value", "realm_api_id", default="poe2")


def resolve_league(base, realm):
    cache_key = f"league_{re.sub(r'[^a-z0-9]+', '_', realm.lower())}.json"
    cached = cache_read(cache_key)
    if cached:
        return cached

    leagues = http_json(f"{base}/{urllib.parse.quote(realm)}/Leagues")
    if not isinstance(leagues, list) or not leagues:
        raise RuntimeError("Unexpected /Leagues response")

    if LEAGUE_OVERRIDE:
        league = next(
            (lg for lg in leagues if str(g(lg, "value", "Value", default="")).lower() == LEAGUE_OVERRIDE.lower()),
            {"Value": LEAGUE_OVERRIDE},
        )
    else:
        league = next((lg for lg in leagues if g(lg, "isCurrent")), None)
        if league is None:
            perm = {"standard", "hardcore", "ssf standard", "ssf hardcore"}
            league = next(
                (lg for lg in leagues if str(g(lg, "value", default="")).lower() not in perm),
                leagues[0],
            )
    if not g(league, "value"):
        raise RuntimeError("Could not determine current league")
    cache_write(cache_key, league)
    return league


def fetch_category(base, realm, league_name, category):
    safe = re.sub(r"[^a-z0-9]+", "_", f"{realm}_{league_name}_{category}".lower())
    cached = cache_read(f"cur_{safe}.json")
    if cached is not None:
        return cached

    base = (
        f"{base}/{urllib.parse.quote(realm)}"
        f"/Leagues/{urllib.parse.quote(league_name)}/Currencies/ByCategory"
    )
    items, page, max_pages = [], 1, 8
    while page <= max_pages:
        params = {"Category": category, "Page": str(page), "PerPage": "250"}
        data = http_json(f"{base}?{urllib.parse.urlencode(params)}")
        chunk = g(data, "items", default=[]) if isinstance(data, dict) else data
        if not isinstance(chunk, list):
            chunk = []
        items.extend(chunk)
        pages = to_float(g(data, "pages", default=1)) or 1
        if page >= pages or not chunk:
            break
        page += 1

    cache_write(f"cur_{safe}.json", items)
    return items


# ---------------------------------------------------------------------------
# matching / conversion
# ---------------------------------------------------------------------------
def name_of_item(it):
    return g(it, "text", "name", "apiId", default="")


def price_of(it):
    return to_float(g(it, "currentPrice"))


def rank(it, term):
    """Lower is better; 9 means no match."""
    name = name_of_item(it).lower()
    api = str(g(it, "apiId", default="")).lower()
    t = term.lower()
    if not t:
        return 5
    if api == t or name == t:
        return 0
    if name.startswith(t) or api.startswith(t):
        return 1
    if t in name or t in api:
        return 2
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
        base, realms = api_context()
        realm = resolve_realm(realms)
        league = resolve_league(base, realm)
    except Exception as exc:  # noqa: BLE001
        error_item("Couldn't reach poe2scout API", str(exc)[:240])
        return

    league_name = g(league, "value", default="")
    base_label = g(league, "baseCurrencyText", default="Exalted Orb")
    league_divine = to_float(g(league, "divinePrice"))

    try:
        items = fetch_category(base, realm, league_name, category)
    except Exception as exc:  # noqa: BLE001
        error_item(f"Couldn't load '{category}' prices", str(exc))
        return

    if not items:
        error_item(f"No items found in category '{category}'", f"League: {league_name}")
        return

    # Reference currency for conversion (default = base currency).
    ref_price, ref_label = 1.0, base_label
    if ref_term:
        ref_item = find_best(items, ref_term)
        if ref_item and price_of(ref_item):
            ref_price = price_of(ref_item)
            ref_label = name_of_item(ref_item)
        else:
            ref_label = f"{base_label} (couldn't match '{ref_term}')"

    # Divine equivalent for the default view (prefer the league's DivinePrice).
    divine_price = league_divine
    if not divine_price:
        dv = next(
            (it for it in items if str(g(it, "apiId", default="")).lower() in ("divine", "divine-orb")
             or name_of_item(it).lower() == "divine orb"),
            None,
        )
        divine_price = price_of(dv) if dv else None

    matched = [it for it in items if (not search or rank(it, search) < 9) and price_of(it) is not None]
    matched.sort(key=lambda it: (rank(it, search), -(price_of(it) or 0)))
    matched = matched[:25]

    if not matched:
        error_item(f"No match for '{search}'", f"Category: {category} · League: {league_name}")
        return

    ref_short = ref_label.split(" (")[0]
    results = []
    for it in matched:
        name = name_of_item(it)
        base_price = price_of(it)
        converted = base_price / ref_price if ref_price else base_price
        arg = f"{converted:.6f}".rstrip("0").rstrip(".")

        if ref_term:
            sub = f"1 {name} = {fmt(converted)} {ref_label}   •   {league_name}"
            large = f"1 {name} = {fmt(converted)} {ref_label}"
        else:
            extra = ""
            if divine_price and divine_price > 0 and name.lower() != "divine orb":
                extra = f"   ({fmt(base_price / divine_price)} Divine)"
            sub = f"1 {name} = {fmt(base_price)} {base_label}{extra}   •   {league_name}"
            large = f"1 {name}\n  = {fmt(base_price)} {base_label}" + (
                f"\n  = {fmt(base_price / divine_price)} Divine" if divine_price else ""
            )

        results.append({
            "uid": str(g(it, "apiId", default=name)),
            "title": f"{name} — {fmt(converted)} {ref_short}",
            "subtitle": sub,
            "arg": arg,
            "valid": True,
            "icon": {"path": "icon.png"},
            "text": {"copy": arg, "largetype": large},
            "mods": {"cmd": {"valid": True, "arg": name, "subtitle": f"Copy name: {name}"}},
        })

    alfred(results)


if __name__ == "__main__":
    main()
