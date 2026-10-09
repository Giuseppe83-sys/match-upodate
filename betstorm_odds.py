#!/usr/bin/env python3
"""
Betstorm odds updater: aggiorna odds.json (calcio), tennis.json e basketball.json
con quote reali da The Odds API (piano gratuito: 500 crediti/mese).

Costo: 1 credito per campionato/torneo per ogni esecuzione (mercato h2h x 1 regione).
Con i default (6 calcio + 2 tennis + 2 basket) sono ~10 crediti al giorno, ~300 al mese.
L'elenco dei campionati attivi (/sports) non costa crediti.

Variabili d'ambiente:
  ODDS_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  opzionali: SUPABASE_BUCKET (betstorm-data), ODDS_DAYS_AHEAD (7), ODDS_REGIONS (eu),
             MAX_KEYS_SOCCER (6), MAX_KEYS_TENNIS (2), MAX_KEYS_BASKET (2), MIN_CREDITS (25)
Non piazza scommesse: produce solo dati.
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("betstorm-odds")

ODDS_KEY = os.environ["ODDS_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
BUCKET = os.getenv("SUPABASE_BUCKET", "betstorm-data")
DAYS_AHEAD = int(os.getenv("ODDS_DAYS_AHEAD", "7"))
REGIONS = os.getenv("ODDS_REGIONS", "eu")
MIN_CREDITS = int(os.getenv("MIN_CREDITS", "25"))
MAX_KEYS = {
    "calcio": int(os.getenv("MAX_KEYS_SOCCER", "6")),
    "tennis": int(os.getenv("MAX_KEYS_TENNIS", "2")),
    "basket": int(os.getenv("MAX_KEYS_BASKET", "2")),
}
BASE = "https://api.the-odds-api.com/v4"

SOCCER_PREFERRED = [
    "soccer_italy_serie_a", "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
    "soccer_france_ligue_one", "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "soccer_brazil_campeonato",
]
BASKET_PREFERRED = ["basketball_nba", "basketball_euroleague", "basketball_wnba"]

# Affiliati di default, usati solo se odds.json nel bucket non li contiene già
DEFAULT_AFFILIATES = [
    {"name": "Betwin360", "color": "#ff8f00", "url": "https://betwin360.it/fwlink/account-registration?father=amboox18"},
    {"name": "Bgame", "color": "#e53935", "url": "https://lp.bgame.it/slotscommesse?ID=imperialdeal_18"},
    {"name": "Betpassion", "color": "#00e676", "url": "https://www.betpassion.it/imperialdeal?promoter=imperialdeal62"},
    {"name": "Vincitu", "color": "#42a5f5", "url": "https://www.vincitu.it/signup?codAffiliato=wabrokerbet76"},
    {"name": "NFS", "color": "#7c4dff", "url": "https://track.padrinopartners.com/visit/?bta=35842&nci=5343&afp10=Network&utm_campaign=imperialdeal_20"},
]


class OutOfCredits(Exception):
    pass


# ---------------------------------------------------------------- Supabase
def sb_headers():
    return {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY}


def sb_download(path):
    r = requests.get(f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}", headers=sb_headers(), timeout=60)
    return r.json() if r.status_code == 200 else None


def sb_upload(path, data):
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}",
        headers={**sb_headers(), "Content-Type": "application/json", "x-upsert": "true",
                 "cache-control": "max-age=60"},
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Upload {path} fallito: {r.status_code} {r.text[:300]}")
    log.info("Caricato %s", path)


# ---------------------------------------------------------------- The Odds API
class Quota:
    remaining = None


def api_get(path, params):
    params = {**params, "apiKey": ODDS_KEY}
    for attempt in range(3):
        r = requests.get(f"{BASE}{path}", params=params, timeout=60)
        rem = r.headers.get("x-requests-remaining")
        if rem is not None:
            Quota.remaining = float(rem)
        if r.status_code in (401, 403):
            raise RuntimeError(f"The Odds API rifiuta la chiave ({r.status_code}): {r.text[:200]}")
        if r.status_code == 429:
            raise OutOfCredits(f"Limite raggiunto: {r.text[:200]}")
        if r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"The Odds API non risponde su {path}")


def pick_keys(active, group):
    keys = [s["key"] for s in active]
    if group == "calcio":
        ordered = [k for k in SOCCER_PREFERRED if k in keys]
    elif group == "basket":
        ordered = [k for k in BASKET_PREFERRED if k in keys]
        ordered += [k for k in keys if k.startswith("basketball_") and k not in ordered]
    else:  # tennis: bilancia ATP e WTA, evitando che i primi slot siano tutti ATP
        t = sorted(k for k in keys if k.startswith("tennis_"))
        atp = [k for k in t if "_atp_" in k]
        wta = [k for k in t if "_wta_" in k]
        other = [k for k in t if k not in atp and k not in wta]

        ordered = []
        # Interleave ATP/WTA: con MAX_KEYS_TENNIS=2 prende 1 ATP + 1 WTA.
        while atp or wta:
            if atp:
                ordered.append(atp.pop(0))
            if wta:
                ordered.append(wta.pop(0))
        ordered.extend(other)
    return ordered[:MAX_KEYS[group]]


def fetch_events(sport_key):
    if Quota.remaining is not None and Quota.remaining < MIN_CREDITS:
        raise OutOfCredits(f"Crediti rimasti ({Quota.remaining:.0f}) sotto la soglia {MIN_CREDITS}")
    data = api_get(f"/sports/{sport_key}/odds",
                   {"regions": REGIONS, "markets": "h2h", "oddsFormat": "decimal", "dateFormat": "iso"})
    log.info("%s: %d eventi | crediti rimasti: %s", sport_key, len(data), Quota.remaining)
    return data


# ---------------------------------------------------------------- trasformazione
def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def to_entry(ev, group, affiliates, counter):
    best = {}
    for bk in ev.get("bookmakers", []):
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h":
                continue
            for o in mk.get("outcomes", []):
                try:
                    price = float(o["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                if o["name"] not in best or price > best[o["name"]][0]:
                    best[o["name"]] = (price, bk.get("title", ""))
    h, a = ev.get("home_team"), ev.get("away_team")
    if h not in best or a not in best:
        return None

    def cell(price_bk):
        price, bk_title = price_bk if price_bk else (0, "")
        aff = affiliates[counter[0] % len(affiliates)]
        counter[0] += 1
        return {"value": round(price, 2), "bookmaker": bk_title, "affiliate": aff}

    odds = {"1": cell(best[h]), "X": cell(best.get("Draw")), "2": cell(best[a])}
    return {
        "home": h, "away": a,
        "league": ev.get("sport_title", ""),
        "sport": group, "start": ev["commence_time"], "odds": odds,
    }


def selections(entries):
    sels = []
    for e in entries:
        vals = {k: v["value"] for k, v in e["odds"].items() if v["value"] > 1}
        if len(vals) < 2:
            continue
        inv = {k: 1 / q for k, q in vals.items()}
        tot = sum(inv.values())
        for k, q in vals.items():
            team = e["home"] if k == "1" else e["away"] if k == "2" else "Pareggio"
            sels.append({"home": e["home"], "away": e["away"], "bet": k, "team": team,
                         "quota": q, "pct": round(inv[k] / tot * 100)})
    return sels


def build_schedine(entries):
    sels = selections(entries)
    used, out = set(), {}
    rules = {"easy": (1.10, 1.70, 2), "medium": (1.25, 2.00, 3), "hard": (2.00, 4.50, 3)}
    for name, (lo, hi, n) in rules.items():
        in_range = sorted((s for s in sels if lo <= s["quota"] <= hi), key=lambda s: -s["pct"])
        # prima le partite non ancora usate da altre schedine, poi (se servono) anche le altre
        cands = [s for s in in_range if (s["home"], s["away"]) not in used] + \
                [s for s in in_range if (s["home"], s["away"]) in used]
        picks, seen = [], set()
        for s in cands:
            m = (s["home"], s["away"])
            if m in seen:
                continue
            seen.add(m)
            if s["quota"] <= 2.0:
                motivo = f"{s['team']} favorito per il mercato (probabilità implicita {s['pct']}%)."
            else:
                motivo = f"{s['team']}: possibile sorpresa, quota {s['quota']:.2f} (implicita {s['pct']}%)."
            picks.append({"home": s["home"], "away": s["away"], "bet": s["bet"],
                          "quota": round(s["quota"], 2), "motivo": motivo, "pct": s["pct"]})
            if len(picks) == n:
                break
        if not picks:
            out[name] = None
            continue
        used |= {(p["home"], p["away"]) for p in picks}
        total, prob = 1.0, 1.0
        for p in picks:
            total *= p["quota"]
            prob *= p["pct"] / 100
        total = round(total, 2)
        out[name] = {"picks": picks, "total_quota": total, "combined_pct": round(prob * 100, 1),
                     "potential_win_10": round(10 * total, 2), "potential_win_25": round(25 * total, 2)}
    return out


# ---------------------------------------------------------------- main
def main():
    now = datetime.now(timezone.utc)
    limit = now + timedelta(days=DAYS_AHEAD)
    current = sb_download("odds.json") or {}
    affiliates = current.get("affiliates") or DEFAULT_AFFILIATES
    counter = [0]

    active = [s for s in api_get("/sports", {}) if s.get("active") and not s.get("has_outrights")]
    log.info("Competizioni attive: %d (richiesta gratuita)", len(active))

    results, failed = {}, False
    for group in ("calcio", "tennis", "basket"):
        keys = pick_keys(active, group)
        log.info("%s -> %s", group, keys)
        if group == "tennis":
            all_tennis = sorted(s["key"] for s in active if s["key"].startswith("tennis_"))
            log.info("Tennis attivi disponibili: %s", all_tennis)
        # Nessuna competizione attiva: feed vuoto valido, non un errore API.
        entries = []
        for k in keys:
            try:
                events = fetch_events(k)
            except OutOfCredits as e:
                log.error("%s", e)
                failed = True
                break
            except Exception as e:
                log.error("%s fallito: %s", k, e)
                failed = True
                continue
            for ev in events:
                try:
                    start = parse_iso(ev["commence_time"])
                except (KeyError, ValueError):
                    continue
                if now <= start <= limit:
                    entry = to_entry(ev, group, affiliates, counter)
                    if entry:
                        entries.append(entry)
            time.sleep(0.3)
        entries.sort(key=lambda e: e["start"])
        results[group] = entries
        if group == "tennis":
            log.info("Tennis: %d match utili entro %d giorni", len(entries), DAYS_AHEAD)

    # Non pubblicare feed parziali quando anche una sola richiesta è fallita.
    # Il workflow GitHub Actions segnalerà l'errore con exit code 1.
    if failed:
        log.error("Aggiornamento incompleto: nessun file pubblicato su Supabase.")
        return 1

    stamp = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if "calcio" in results:
        sb_upload("odds.json", {"matches": results["calcio"], "updated_at": stamp, "affiliates": affiliates})
    for group, fname in (("tennis", "tennis.json"), ("basket", "basketball.json")):
        if group in results:
            sb_upload(fname, {"schedine": build_schedine(results[group]),
                              "matches": results[group], "updated_at": stamp})
    log.info("Fatto. Crediti rimasti: %s", Quota.remaining)
    return 0


if __name__ == "__main__":
    sys.exit(main())
