# Cape Fish Check

When and where to fish within an hour of Cape Town: an hour-by-hour rating for light-tackle shore spots in the
sea, estuaries and fresh water, with the tide, moon and solunar times, a tactics panel that changes with the
conditions, and the permits, size and bag limits and closed seasons for every fish. Free, no app, no account.

**Open it:** https://ciaranbigwood.github.io/cape-fish-check/

## On your phone

Open the address above, then put it on your home screen so it opens like an app:

- **iPhone:** in Safari, tap the Share button, then **Add to Home Screen**.
- **Android:** in Chrome, tap the ⋮ menu, then **Add to Home screen** or **Install app**.

The page remembers the last forecast, so it still opens at the water with no signal.

## How it stays up to date

A scheduled GitHub Action rebuilds the page every 3 hours with a fresh forecast from
[Open-Meteo](https://open-meteo.com/). Your browser also fetches the forecast directly when the saved one is more
than about 3½ hours old, or when you press **Refresh**. Ratings, tides, the moon and the tactics are all worked
out in the browser from the raw forecast, the spot data and the fish profiles.

## What decides the rating

Each fish at each spot gets a score for every hour; the spot's score is the best of them.

- **Sea and estuary spots:** tide stage and flow (the model's sea level), springs or neaps (from the moon), dawn
  and dusk, wind, sea state against the spot's shelter, the barometer trend, cloud, and sea temperature (with a
  penalty when upwelling drops it fast).
- **Freshwater spots:** water temperature (estimated from three weeks of air temperature), light, wind, the
  barometer trend and the season. The moon counts only as a little night light.
- **Solunar periods** are shown on every chart but do not move any score: the evidence for them is weak.
- A fish in its closed season is not rated. Hours with a dangerous swell for the stand score near zero.

## Change a spot or a fish

The data lives in three files. Open one on GitHub, click the pencil icon, edit, and commit; the site rebuilds
itself in a couple of minutes. `build.py` checks them and stops with a clear message if something is wrong.

| File | What it holds |
|---|---|
| [`spots.json`](spots.json) | Every spot: position, water, drive times, which way it faces, shelter, tide preference, rules, and the fish caught there |
| [`species.json`](species.json) | Every fish: season, water-temperature range, how light, sea, tide, wind and pressure affect it, tactics by condition, and the legal limits |
| [`rules.json`](rules.json) | Permits, general rules and the status of angling at each place, with sources |

### Spot fields

| Field | Meaning |
|---|---|
| `water` | `sea`, `estuary` or `fresh` (the three-way filter) |
| `style` | `light-tackle`, or `rock-and-surf` for heavier spots added later |
| `mine` | `true` puts the spot in **My spots** for a first-time visitor; after that each browser keeps its own list |
| `lat`, `lon`, `access` | The usual stand, and optionally the car park (`access` with its own `lat`, `lon`, `note`) for Directions |
| `drive` | Minutes from Westlake and from Green Point (road routing without traffic) |
| `facing` | Bearing from the stand out over the water; a wind from that direction is onshore |
| `shelter` | `exposed`, `semi`, `sheltered` or `enclosed`: how much open-sea swell reaches the water |
| `stand` | `rock`, `wall`, `beach` or `bank`: sets the swell height that counts as dangerous |
| `body` | `sea`, `lagoon`, `vlei`, `dam`, `river` or `stream`: sets how fast the freshwater temperature estimate follows the air |
| `tide_pref`, `tidal` | The tide local anglers prefer; `tidal: false` for a vlei whose mouth is mostly closed |
| `open_months`, `open_hours` | A spot's own season (e.g. a City dam open October to April) and gate hours |
| `legal_status` | `permit`, `permit-and-local`, or `unclear` (shown as a warning on the card, with `legal_note`) |
| `species` | Each fish here: `key` (from species.json), `abundance` (`common`, `regular`, `occasional`, `rare`), best `months`, `size`, `method`, `bait`, `sources` |

### Tactics

Each fish in `species.json` has a list of tactics. A tactic applies when all of its `when` conditions hold
(`light`, `sea`, `tide`, `range`, `wind`, `wind_rel`, `temp`, `clarity`, `trend`, `months`, `water`); the page shows
the one that names the most conditions, and the one with an empty `when` is the fallback. In `stand`, `{downwind}`
and `{upwind}` become the actual bank for the hour's wind.

## Build it yourself

```
python build.py            # writes _site/ with a fresh forecast
python -m http.server 8000 --directory _site
```

Then open http://localhost:8000. `--no-fetch` reuses the forecast already in `_site/`. Python 3.9 or later,
standard library only.

| File | What it is |
|---|---|
| `template.html` | The page: layout, styles, the rating engine and the app code |
| `build.py` | Checks the data, fetches the forecast and assembles `_site/` |
| `sw.js`, `manifest.webmanifest`, `assets/` | Offline support, home-screen install and icons |
| `.github/workflows/deploy.yml` | Rebuilds and publishes every 3 hours and on every change |

## Credits and limits

- Weather and marine data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0), free for non-commercial use.
- Moon phase, moonrise, moonset and solunar times are computed in the browser from standard low-precision
  astronomical formulas (within about ten minutes of published tables).
- Map by [Leaflet](https://leafletjs.com/) (BSD-2-Clause, see `LICENSE-leaflet.txt`) with tiles ©
  [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors. Directions open Google Maps.
- Spots, fish and tactics were compiled from angling sources (Sealine, The Mission, Jacita, the District Mail
  angling column, the Cape Piscatorial Society and others) and the rules from the Government Gazette, DFFE,
  CapeNature, SANParks and the City of Cape Town, each listed with its source on the page.
- Ratings are a model, not a fishing report. Know the rules before you keep a fish, and look after yourself on the
  rocks: never turn your back on the sea.
