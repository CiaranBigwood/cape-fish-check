"""Build the Cape Fish Check site.

  python build.py                    writes _site/: index.html, forecast.json, manifest, service worker, icons
  python build.py --no-fetch         reuses _site/forecast.json instead of calling Open-Meteo
  python build.py --artifact FILE    also writes FILE: one self-contained page with the forecast inside
  --public-url URL                   the site's address, used for share links and link previews
  --repo-url URL                     the GitHub repository, linked in the footer
  --previous URL                     a forecast.json to fall back on if Open-Meteo cannot be reached

Standard library only, so it runs on a bare GitHub Actions runner.
"""
import argparse
import datetime as dt
import hashlib
import http.client
import json
import os
import pathlib
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
SPOTS_FILE = HERE / "spots.json"
SPECIES_FILE = HERE / "species.json"
RULES_FILE = HERE / "rules.json"
LEAFLET_CSS = HERE / "leaflet.min.css"
STATIC = ["manifest.webmanifest"]
ASSETS = HERE / "assets"
CHUNK = 25
MARINE = "https://marine-api.open-meteo.com/v1/marine"
WEATHER = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "cape-fish-check (personal fishing forecast page)"}

WATERS = {"fresh", "estuary", "sea"}
STYLES = {"light-tackle", "rock-and-surf"}
SHELTER = {"exposed", "semi", "sheltered", "enclosed"}
ABUNDANCE = {"common", "regular", "occasional", "rare"}
LEGAL_STATUS = {"permit", "permit-and-local", "unclear"}
TIDE_PREF = {"pushing", "dropping", "high", "low", "mid", "any", "none"}
BODY = {"sea", "lagoon", "vlei", "dam", "river", "stream"}
REQUIRED = ["id", "name", "area", "region", "water", "style", "lat", "lon", "body", "drive", "legal_status",
            "permit", "description", "species", "sources", "confidence"]


def fail(msg):
    sys.exit(msg)


def js_list(src, name):
    """Read a `const NAME = [...]` array of strings out of the page script, so the build and the page
    always ask Open-Meteo for the same variables."""
    m = re.search(r"const %s = \[(.*?)\];" % name, src, re.S)
    if not m:
        fail(f"template.html: {name} not found")
    return re.findall(r"'([^']+)'", m.group(1))


def js_int(src, name):
    m = re.search(r"const %s = (\d+);" % name, src)
    if not m:
        fail(f"template.html: {name} not found")
    return int(m.group(1))


def in_cape(lat, lon):
    return isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and -35.0 < lat < -33.0 and 17.8 < lon < 19.8


def load_species():
    data = json.loads(SPECIES_FILE.read_text(encoding="utf-8"))
    sp = data.get("species") if isinstance(data, dict) else None
    if not isinstance(sp, dict) or not sp:
        fail("species.json must be {\"species\": {key: {...}}}")
    for k, s in sp.items():
        where = f"species.json {k}"
        for f in ("name", "waters", "temp", "light", "present_months", "tactics", "legal"):
            if f not in s:
                fail(f"{where} is missing {f}")
        t = s["temp"]
        if not (t["min"] <= t["ideal_lo"] <= t["ideal_hi"] <= t["max"]):
            fail(f"{where}: temp must run min <= ideal_lo <= ideal_hi <= max, not {t}")
        if not s["tactics"] or not any(not r.get("when") for r in s["tactics"]):
            fail(f"{where} needs a default tactic (one with an empty 'when')")
        for m in s.get("present_months", []) + s.get("peak_months", []):
            if not (isinstance(m, int) and 1 <= m <= 12):
                fail(f"{where}: months must be 1 to 12, not {m!r}")
    return data


def load_spots(species_keys):
    spots = json.loads(SPOTS_FILE.read_text(encoding="utf-8"))
    ids = set()
    for s in spots:
        where = f"spots.json {s.get('id', '?')}"
        missing = [k for k in REQUIRED if k not in s]
        if missing:
            fail(f"{where} is missing {missing}")
        if s["id"] in ids:
            fail(f"spots.json: duplicate id {s['id']}")
        ids.add(s["id"])
        if not re.fullmatch(r"[a-z0-9-]+", s["id"]):
            fail(f"{where}: id must be lowercase letters, digits and hyphens")
        if s["water"] not in WATERS:
            fail(f"{where}: water must be one of {sorted(WATERS)}")
        if s["style"] not in STYLES:
            fail(f"{where}: style must be one of {sorted(STYLES)}")
        if s["body"] not in BODY:
            fail(f"{where}: body must be one of {sorted(BODY)}")
        if s["legal_status"] not in LEGAL_STATUS:
            fail(f"{where}: legal_status must be one of {sorted(LEGAL_STATUS)}")
        if not in_cape(s["lat"], s["lon"]):
            fail(f"{where} has coordinates outside the Cape Town area: {s['lat']}, {s['lon']}")
        a = s.get("access")
        if a is not None and not (isinstance(a, dict) and in_cape(a.get("lat"), a.get("lon"))):
            fail(f"{where} has an access point without a valid lat and lon: {a}")
        if s["water"] != "fresh":
            if not isinstance(s.get("facing"), (int, float)) or not 0 <= s["facing"] < 360:
                fail(f"{where}: sea and estuary spots need facing, the bearing from the stand out over the water (0 to 359)")
            if s.get("shelter") not in SHELTER:
                fail(f"{where}: shelter must be one of {sorted(SHELTER)}")
            if s.get("tide_pref", "any") not in TIDE_PREF:
                fail(f"{where}: tide_pref must be one of {sorted(TIDE_PREF)}")
        d = s["drive"]
        if not isinstance(d, dict) or not all(isinstance(d.get(k), (int, float)) for k in ("westlake_min", "green_point_min")):
            fail(f"{where}: drive needs westlake_min and green_point_min (minutes)")
        if min(d["westlake_min"], d["green_point_min"]) > 60 and not s.get("scope_exception"):
            fail(f"{where} is more than 60 minutes from both Westlake and Green Point; give scope_exception to keep it")
        if not s["species"]:
            fail(f"{where} lists no species")
        for sp in s["species"]:
            if sp.get("key") not in species_keys:
                fail(f"{where}: species key {sp.get('key')!r} is not in species.json")
            if sp.get("abundance") not in ABUNDANCE:
                fail(f"{where}: {sp.get('key')} abundance must be one of {sorted(ABUNDANCE)}")
            if not sp.get("sources"):
                fail(f"{where}: {sp.get('key')} needs at least one source")
        if not s["sources"]:
            fail(f"{where} needs at least one source")
    return spots


def get_json(url, tries=4):
    wait = 20
    for attempt in range(1, tries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                data = json.load(r)
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(data.get("reason", "Open-Meteo error"))
            return data if isinstance(data, list) else [data]
        except (OSError, http.client.HTTPException, ValueError, RuntimeError) as e:
            code = getattr(e, "code", None)
            if attempt == tries or code in (400, 404):
                raise
            print(f"  request failed ({code or e}); retrying in {wait}s", flush=True)
            time.sleep(wait)
            wait *= 2


def chunks(items, n=CHUNK):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def fetch_forecast(spots, cfg):
    """One marine call for the sea and estuary spots, one hourly weather call for every spot (sea cells for
    the coast, land cells inland), and one daily history call for the vleis, dams and rivers, whose water
    temperature is estimated from the last three weeks of air temperature."""
    m_vars, w_vars, w_daily, h_daily = cfg["M_VARS"], cfg["W_VARS"], cfg["W_DAILY"], cfg["H_DAILY"]
    days, past, hist_past = cfg["DAYS"], cfg["PAST_DAYS"], cfg["HIST_DAYS"]
    common = {"timezone": "Africa/Johannesburg", "forecast_days": days}
    coast = [s for s in spots if s["water"] != "fresh"]
    inland_temp = [s for s in spots if s["water"] != "sea"]
    marine, weather, hist = {}, {}, {}

    def latlon(chunk):
        return {"latitude": ",".join(str(s["lat"]) for s in chunk), "longitude": ",".join(str(s["lon"]) for s in chunk)}

    for ch in chunks(coast):
        q = dict(common, **latlon(ch), hourly=",".join(m_vars), past_days=past)
        res = get_json(MARINE + "?" + urllib.parse.urlencode(q, safe=","))
        for s, r in zip(ch, res):
            marine[s["id"]] = r
        time.sleep(1)
    for sel in ("sea", "land"):
        group = [s for s in spots if (s["water"] == "fresh") == (sel == "land")]
        for ch in chunks(group):
            q = dict(common, **latlon(ch), hourly=",".join(w_vars), daily=",".join(w_daily), past_days=past,
                     wind_speed_unit="kmh", cell_selection=sel)
            res = get_json(WEATHER + "?" + urllib.parse.urlencode(q, safe=","))
            for s, r in zip(ch, res):
                weather[s["id"]] = r
            time.sleep(1)
    try:
        for ch in chunks(inland_temp):
            q = dict(common, **latlon(ch), daily=",".join(h_daily), past_days=hist_past, cell_selection="land")
            res = get_json(WEATHER + "?" + urllib.parse.urlencode(q, safe=","), tries=2)
            for s, r in zip(ch, res):
                hist[s["id"]] = r
            time.sleep(1)
    except Exception as e:  # noqa: BLE001 - the page falls back to the hourly air temperature for its water estimate
        print(f"::warning::air-temperature history failed ({e}); freshwater temperatures will use the forecast window only", flush=True)
        hist = {}
    print(f"  fetched marine {len(marine)}, weather {len(weather)}, history {len(hist)}", flush=True)
    if len(weather) != len(spots):
        raise RuntimeError(f"weather for {len(weather)} of {len(spots)} spots")

    first = weather[spots[0]["id"]]
    times = first["hourly"]["time"]
    days_list = first.get("daily", {}).get("time", [])
    hdays = next((h.get("daily", {}).get("time", []) for h in hist.values()), [])
    out = {}
    for s in spots:
        w = weather[s["id"]]
        index = {t: k for k, t in enumerate(w["hourly"]["time"])}

        def pick(arr, idx):
            return [None if (idx.get(t) is None or arr is None) else arr[idx[t]] for t in times]

        rec = {"w": {v: pick(w["hourly"].get(v), index) for v in w_vars},
               "d": {v: w.get("daily", {}).get(v) for v in w_daily}, "grid": {"lat": w.get("latitude"), "lon": w.get("longitude")}}
        if s["id"] in marine:
            m = marine[s["id"]]
            mi = {t: k for k, t in enumerate(m["hourly"]["time"])}
            rec["m"] = {v: pick(m["hourly"].get(v), mi) for v in m_vars}
            rec["mgrid"] = {"lat": m.get("latitude"), "lon": m.get("longitude")}
        if s["id"] in hist:
            h = hist[s["id"]]
            hd = h.get("daily", {})
            rec["h"] = {v: hd.get(v) for v in h_daily} if hd.get("time") == hdays else None
        out[s["id"]] = rec
    with_sl = sum(1 for s in coast if any(x is not None for x in (out[s["id"]].get("m") or {}).get("sea_level_height_msl", [])[:48]))
    if coast and with_sl < 0.7 * len(coast):
        raise RuntimeError(f"only {with_sl} of {len(coast)} coastal spots have a sea-level signal")
    return {"v": 1, "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "times": times, "days": days_list, "hdays": hdays, "spots": out}


def previous_forecast(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        return data if data.get("v") == 1 and data.get("spots") else None
    except Exception as e:  # noqa: BLE001 - any failure just means there is nothing to fall back on
        print(f"  no previous forecast at {url}: {e}", flush=True)
        return None


def inject(html, marker, name, value):
    js = json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html, n = re.subn(r"/\*%s_START\*/.*?/\*%s_END\*/" % (marker, marker),
                      lambda m: f"/*{marker}_START*/\nconst {name} = {js};\n/*{marker}_END*/", html, count=1, flags=re.S)
    if n != 1:
        fail(f"template.html: {marker} markers not found")
    return html


PAGE_DROP_SPOT = {"unverified", "coord_source", "skeptic", "blocked_urls", "drive_hint"}
PAGE_DROP_ENTRY = {"notes"}
PAGE_DROP_SPECIES = {"unverified", "review_points", "spawn_months"}
PAGE_DROP_LEGAL = {"quote"}


def page_data(spots, species):
    """The data files keep the full research record; the page only carries what it shows."""
    slim_spots = []
    for s in spots:
        s2 = {k: v for k, v in s.items() if k not in PAGE_DROP_SPOT}
        s2["species"] = [{k: v for k, v in e.items() if k not in PAGE_DROP_ENTRY} for e in s["species"]]
        slim_spots.append(s2)
    slim_species = {}
    for k, p in species["species"].items():
        p2 = {f: v for f, v in p.items() if f not in PAGE_DROP_SPECIES}
        p2["legal"] = {f: v for f, v in p.get("legal", {}).items() if f not in PAGE_DROP_LEGAL}
        slim_species[k] = p2
    return slim_spots, {"checked": species.get("checked"), "species": slim_species}


def page_parts(spots, species, rules, site_cfg, snapshot_json):
    spots, species = page_data(spots, species)
    html = TEMPLATE.read_text(encoding="utf-8")
    html = inject(html, "SPOTS", "SPOTS", spots)
    html = inject(html, "SPECIES", "SPECIES", species)
    html = inject(html, "RULES", "RULES", rules)
    html = inject(html, "SITE", "SITE", site_cfg)
    css = LEAFLET_CSS.read_text(encoding="utf-8") if LEAFLET_CSS.exists() else ""
    html = html.replace("/*LEAFLET_CSS*/", css, 1)
    html = re.sub(r'(<script id="snapshot" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + snapshot_json + m.group(2), html, count=1, flags=re.S)
    cut = html.index('<header class="top"')
    return html[:cut], html[cut:]


def document(head_part, body_part, public_url, n_spots):
    og = ""
    if public_url:
        og = (f'<meta property="og:url" content="{public_url}">\n'
              f'<meta property="og:image" content="{public_url}assets/og.png">\n'
              '<meta property="og:image:width" content="1200">\n<meta property="og:image:height" content="630">\n'
              '<meta name="twitter:card" content="summary_large_image">\n'
              f'<link rel="canonical" href="{public_url}">\n')
    desc = f"Where to fish near Cape Town: hour-by-hour ratings, tides, moon, tactics and the legal limits for {n_spots} light-tackle shore spots within an hour of the city."
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="{desc}">
<meta name="theme-color" content="#1f6b4e">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" type="image/png" sizes="32x32" href="assets/favicon-32.png">
<link rel="icon" type="image/png" sizes="192x192" href="assets/icon-192.png">
<link rel="apple-touch-icon" href="assets/apple-touch-icon.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Fish Check">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Cape Fish Check">
<meta property="og:title" content="Cape Fish Check">
<meta property="og:description" content="{desc}">
{og}<style>html,body{{margin:0}}[hidden]{{display:none!important}}img{{max-width:100%}}</style>
{head_part}<style>@supports (padding: max(0px)) {{ body {{ padding-left: max(clamp(16px, 3vw, 32px), env(safe-area-inset-left)); padding-right: max(clamp(16px, 3vw, 32px), env(safe-area-inset-right)); }} }}</style>
</head>
<body>
{body_part}
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--artifact")
    ap.add_argument("--public-url", default="")
    ap.add_argument("--repo-url", default="")
    ap.add_argument("--previous", default="")
    a = ap.parse_args()

    public_url = a.public_url.strip()
    if public_url and not public_url.endswith("/"):
        public_url += "/"
    out = (HERE / a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    src = TEMPLATE.read_text(encoding="utf-8")
    cfg = {k: js_list(src, k) for k in ("M_VARS", "W_VARS", "W_DAILY", "H_DAILY")}
    cfg.update({k: js_int(src, k) for k in ("DAYS", "PAST_DAYS", "HIST_DAYS")})
    species = load_species()
    rules = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    spots = load_spots(set(species["species"]))
    print(f"{len(spots)} spots, {len(species['species'])} species; {cfg['DAYS']} days ahead", flush=True)

    forecast = None
    if a.no_fetch:
        f = out / "forecast.json"
        forecast = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        print("  reusing the forecast already in the output folder" if forecast else "  no forecast in the output folder")
    else:
        try:
            print("fetching the forecast ...", flush=True)
            forecast = fetch_forecast(spots, cfg)
        except Exception as e:  # noqa: BLE001 - fall back rather than publish nothing
            print(f"  forecast fetch failed: {e}", flush=True)
            if a.previous:
                forecast = previous_forecast(a.previous) or (time.sleep(10) or previous_forecast(a.previous))
                if forecast:
                    print(f"::warning::Open-Meteo fetch failed ({e}); republishing the forecast from {forecast['fetchedAt']}", flush=True)
                    age_h = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(forecast["fetchedAt"])).total_seconds() / 3600
                    if age_h > 24 and os.environ.get("GITHUB_EVENT_NAME") == "schedule":
                        fail(f"The forecast is {age_h:.0f} hours old and Open-Meteo still fails ({e}). "
                             "Failing so GitHub emails the owner; the live site keeps its last deployment.")
            if forecast is None:
                fail("No forecast: Open-Meteo failed and no previous forecast.json could be loaded. Not publishing.")
    fc_json = json.dumps(forecast, separators=(",", ":"), allow_nan=False) if forecast else None
    if fc_json:
        (out / "forecast.json").write_text(fc_json, encoding="utf-8")
        print(f"  forecast.json {len(fc_json) / 1024:.0f} KB, fetched {forecast['fetchedAt']}", flush=True)

    site = {"publicUrl": public_url, "repoUrl": a.repo_url.strip()}  # no build time: index.html only changes when the data files do
    head_part, body_part = page_parts(spots, species, rules, site, "null")
    index = document(head_part, body_part, public_url, len(spots))
    (out / "index.html").write_text(index, encoding="utf-8")

    for name in STATIC:
        shutil.copy2(HERE / name, out / name)
    if ASSETS.exists():
        shutil.copytree(ASSETS, out / "assets", dirs_exist_ok=True)
    version = hashlib.sha256((index + (HERE / "sw.js").read_text(encoding="utf-8")).encode("utf-8")).hexdigest()[:12]
    (out / "sw.js").write_text((HERE / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", version), encoding="utf-8")
    print(f"wrote {out} (index.html {len(index) / 1024:.0f} KB, service worker {version})", flush=True)

    if a.artifact:
        snap = fc_json.replace("</", "<\\/") if fc_json else "null"
        h, b = page_parts(spots, species, rules, site, snap)
        pathlib.Path(a.artifact).write_text(h + b, encoding="utf-8")
        print(f"wrote {a.artifact} ({pathlib.Path(a.artifact).stat().st_size / 1024:.0f} KB)", flush=True)


if __name__ == "__main__":
    main()
