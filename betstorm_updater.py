#!/usr/bin/env python3
"""
Betstorm matches updater (versione con API gratuite).

Flusso: football-data.org (fixture) -> Gemini (pronostici) -> quote (odds.json o stimate)
-> schedine easy/medium/hard -> upload matches.json e predictions_history.json su Supabase.
Non piazza scommesse: produce solo dati e suggerimenti.

Variabili d'ambiente:
  FOOTBALL_DATA_API_KEY, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  opzionali: SUPABASE_BUCKET (default betstorm-data), DAYS_AHEAD (default 7, 1-14),
             GEMINI_MODEL (default gemini-3.5-flash-lite), MAX_MATCHES (default 60)
"""
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

logging.basicConfig(
    level=os.getenv("LOGLEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("betstorm")

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
BUCKET = os.getenv("SUPABASE_BUCKET", "betstorm-data")
FD_KEY = os.environ["FOOTBALL_DATA_API_KEY"]
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_FALLBACKS = ["gemini-3.5-flash", "gemini-3-flash-preview"]  # provati se il modello dà 404
DAYS_AHEAD = max(1, min(14, int(os.getenv("DAYS_AHEAD", "7"))))
MAX_MATCHES = int(os.getenv("MAX_MATCHES", "60"))

BATCH_SIZE = 20          # partite per chiamata a Gemini
GEMINI_PAUSE = 7         # secondi tra le chiamate (limiti RPM del piano gratuito)
FD_PAUSE = 7             # secondi tra le chiamate a football-data (10 richieste/minuto)
FD_WINDOW_DAYS = 10      # football-data limita l'intervallo di date per richiesta
PCT_MIN, PCT_MAX = 55, 92
HISTORY_DAYS = 90
STAKES = [5, 10, 20]
SLIP_SIZES = {"easy": 3, "medium": 5, "hard": 7}
MARGIN = 0.94            # margine bookmaker simulato per le quote stimate


# ---------------------------------------------------------------- utilità HTTP
def http(method, url, retries=3, **kw):
    for attempt in range(1, retries + 1):
        try:
            r = requests.request(method, url, timeout=120, **kw)
            if r.status_code >= 500 or r.status_code == 429:
                raise requests.HTTPError(f"{r.status_code} {r.text[:200]}")
            return r
        except requests.RequestException as e:
            log.warning("Tentativo %d/%d fallito: %s", attempt, retries, e)
            if attempt == retries:
                raise
            time.sleep(15 * attempt)


def extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Nessun JSON nella risposta")
    return json.loads(m.group(0))


# ---------------------------------------------------------------- Supabase
def sb_headers():
    return {"Authorization": f"Bearer {SUPABASE_KEY}", "apikey": SUPABASE_KEY}


def sb_download(path):
    r = http("GET", f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}", headers=sb_headers())
    if r.status_code == 200:
        return r.json()
    log.info("%s non trovato (%s)", path, r.status_code)
    return None


def sb_create_bucket():
    r = http(
        "POST",
        f"{SUPABASE_URL}/storage/v1/bucket",
        headers={**sb_headers(), "Content-Type": "application/json"},
        json={"id": BUCKET, "name": BUCKET, "public": True},
    )
    log.info("Creazione bucket %s: %s %s", BUCKET, r.status_code, r.text[:200])
    return r.status_code in (200, 201)


def sb_upload(path, data):
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}"
    kw = dict(
        headers={**sb_headers(), "Content-Type": "application/json", "x-upsert": "true"},
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
    )
    r = http("POST", url, **kw)
    if r.status_code != 200 and "bucket not found" in r.text.lower():
        log.warning("Bucket %s non trovato: lo creo (pubblico)", BUCKET)
        if sb_create_bucket():
            r = http("POST", url, **kw)
    if r.status_code != 200:
        raise RuntimeError(f"Upload {path} fallito: {r.status_code} {r.text[:400]}")
    log.info("Caricato %s", path)


# ---------------------------------------------------------------- football-data.org
def fetch_fixtures(start, end):
    """Partite delle competizioni incluse nel tuo piano, in finestre da FD_WINDOW_DAYS."""
    fixtures, cursor, first = [], start, True
    while cursor <= end:
        window_end = min(cursor + timedelta(days=FD_WINDOW_DAYS - 1), end)
        if not first:
            time.sleep(FD_PAUSE)
        first = False
        r = http(
            "GET",
            "https://api.football-data.org/v4/matches",
            headers={"X-Auth-Token": FD_KEY},
            params={"dateFrom": f"{cursor:%Y-%m-%d}", "dateTo": f"{window_end:%Y-%m-%d}"},
        )
        r.raise_for_status()
        for m in r.json().get("matches", []):
            if m.get("status") not in ("SCHEDULED", "TIMED"):
                continue
            fixtures.append({
                "home": m["homeTeam"].get("name") or m["homeTeam"].get("shortName", ""),
                "away": m["awayTeam"].get("name") or m["awayTeam"].get("shortName", ""),
                "league": m.get("competition", {}).get("name", ""),
                "when": m["utcDate"],
            })
        cursor = window_end + timedelta(days=1)
    fixtures = [f for f in fixtures if f["home"] and f["away"]]
    fixtures.sort(key=lambda f: f["when"])
    per_league = {}
    for f in fixtures:
        per_league[f["league"]] = per_league.get(f["league"], 0) + 1
    log.info("Fixture trovate: %d | per competizione: %s", len(fixtures), per_league)
    return fixtures[:MAX_MATCHES]


# ---------------------------------------------------------------- Gemini
def predict_batch(batch):
    items = [{"i": i, "home": f["home"], "away": f["away"], "league": f["league"], "when": f["when"]}
             for i, f in enumerate(batch)]
    prompt = (
        "Sei un analista di pronostici calcistici. Per ogni partita scegli UN tipo di bet "
        "(es. 1, X, 2, 1X, X2, Over 2.5, Under 2.5, Goal, NoGoal), stima la probabilità in "
        "percentuale intera (pct), scrivi una spiegazione breve in italiano (why, max 160 "
        "caratteri) e un H2H sintetico basato solo su ciò che conosci con ragionevole certezza "
        "(h2h, max 80 caratteri; se non sei sicuro scrivi 'n.d.'). Rispondi SOLO con JSON: "
        '{"predictions":[{"i":0,"bet":"","pct":0,"why":"","h2h":""}]}.\n'
        f"Partite: {json.dumps(items, ensure_ascii=False)}"
    )
    global GEMINI_MODEL
    models = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACKS if m != GEMINI_MODEL]
    for model in models:
        r = http(
            "POST",
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
            },
        )
        if r.status_code == 404:
            log.warning("Modello %s non disponibile, provo il successivo", model)
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
        if model != GEMINI_MODEL:
            log.info("Uso il modello %s", model)
            GEMINI_MODEL = model  # lo riuso per i batch successivi
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return extract_json(text).get("predictions", [])
    raise RuntimeError("Nessun modello Gemini disponibile: imposta GEMINI_MODEL")


def predict_all(fixtures):
    matches = []
    for start in range(0, len(fixtures), BATCH_SIZE):
        if start:
            time.sleep(GEMINI_PAUSE)
        batch = fixtures[start:start + BATCH_SIZE]
        try:
            preds = predict_batch(batch)
        except Exception as e:  # un batch fallito non blocca gli altri
            log.error("Batch %d fallito: %s", start // BATCH_SIZE + 1, e)
            continue
        for p in preds:
            try:
                f = batch[int(p["i"])]
                pct = int(round(float(p["pct"])))
            except (KeyError, ValueError, TypeError, IndexError):
                continue
            matches.append({
                **f,
                "bet": str(p.get("bet", "")).strip(),
                "pct": max(PCT_MIN, min(PCT_MAX, pct)),
                "why": p.get("why", ""),
                "h2h": p.get("h2h", ""),
            })
    matches.sort(key=lambda x: (-x["pct"], x["when"]))
    return matches


# ---------------------------------------------------------------- quote e schedine
def key(home, away):
    return f"{home}|{away}".strip().lower()


def attach_odds(matches, odds_data):
    """odds.json accettato come {"home|away": 1.85}, {"home|away": {"odd": 1.85}}
    oppure {"home|away": {"<bet>": 1.85}} (chiavi in minuscolo)."""
    odds_data = odds_data or {}
    for m in matches:
        raw = odds_data.get(key(m["home"], m["away"]))
        odd = None
        if isinstance(raw, (int, float)):
            odd = float(raw)
        elif isinstance(raw, dict):
            v = raw.get("odd", raw.get(m["bet"]))
            odd = float(v) if v else None
        if odd and odd > 1:
            m["odd"], m["odd_source"] = round(odd, 2), "real"
        else:
            m["odd"], m["odd_source"] = max(1.05, round(MARGIN * 100 / m["pct"], 2)), "estimated"


def build_slip(matches):
    slip = {}
    for name, size in SLIP_SIZES.items():
        picks = matches[:size]
        if not picks:
            slip[name] = None
            continue
        total, prob = 1.0, 1.0
        for p in picks:
            total *= p["odd"]
            prob *= p["pct"] / 100
        slip[name] = {
            "picks": [{k: p[k] for k in ("home", "away", "bet", "odd", "pct", "when")} for p in picks],
            "total_odds": round(total, 2),
            "combined_prob_pct": round(prob * 100, 1),
            "potential_wins": {str(s): round(s * total, 2) for s in STAKES},
        }
    return slip


# ---------------------------------------------------------------- storico
def update_history(slip, matches, today):
    history = sb_download("predictions_history.json") or {"entries": []}
    entries = [e for e in history.get("entries", []) if e.get("date") != f"{today:%Y-%m-%d}"]
    entries.append({
        "date": f"{today:%Y-%m-%d}",
        "matches_count": len(matches),
        "avg_pct": round(sum(m["pct"] for m in matches) / len(matches), 1) if matches else 0,
        "schedina": slip,
    })
    cutoff = f"{today - timedelta(days=HISTORY_DAYS):%Y-%m-%d}"
    entries = sorted((e for e in entries if e["date"] >= cutoff), key=lambda e: e["date"])
    sb_upload("predictions_history.json", {
        "entries": entries,
        "stats": {
            "days_tracked": len(entries),
            "total_matches": sum(e["matches_count"] for e in entries),
        },
    })


# ---------------------------------------------------------------- main
def main():
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=DAYS_AHEAD)
    log.info("Aggiornamento %s -> %s", f"{now:%Y-%m-%d}", f"{end:%Y-%m-%d}")

    fixtures = fetch_fixtures(now, end)
    if not fixtures:
        log.error("Nessuna fixture: interrompo senza sovrascrivere i dati")
        return 1

    matches = predict_all(fixtures)
    if not matches:
        log.error("Nessuna predizione valida: interrompo senza sovrascrivere i dati")
        return 1

    attach_odds(matches, sb_download("odds.json"))
    slip = build_slip(matches)

    sb_upload("matches.json", {
        "updated_at": now.isoformat(),
        "status": "ok",
        "days_ahead": DAYS_AHEAD,
        "matches": matches,
        "schedina": slip,
    })
    update_history(slip, matches, now)
    log.info("Fatto: %d partite, schedine generate", len(matches))
    return 0


if __name__ == "__main__":
    sys.exit(main())
