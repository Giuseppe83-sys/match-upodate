#!/usr/bin/env python3
"""
Betstorm matches updater: esecuzione autonoma.

Flusso: Perplexity (fixture) -> OpenAI (pronostici) -> quote (odds.json o fallback)
-> schedine easy/medium/hard -> upload matches.json e predictions_history.json su Supabase.
Non piazza scommesse: produce solo dati e suggerimenti.

Variabili d'ambiente:
  PERPLEXITY_API_KEY, OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  opzionali: SUPABASE_BUCKET (default betstorm-data), DAYS_AHEAD (default 7, 1-14),
             OPENAI_MODEL (default gpt-4o-mini), PERPLEXITY_MODEL (default sonar)
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
PPLX_KEY = os.environ["PERPLEXITY_API_KEY"]
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
PPLX_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar")
DAYS_AHEAD = max(1, min(14, int(os.getenv("DAYS_AHEAD", "7"))))

PCT_MIN, PCT_MAX = 55, 92
HISTORY_DAYS = 90
STAKES = [5, 10, 20]
SLIP_SIZES = {"easy": 3, "medium": 5, "hard": 7}
MARGIN = 0.94  # margine bookmaker simulato per le quote fallback


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
            time.sleep(2 ** attempt)


def extract_json(text):
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


def sb_upload(path, data):
    r = http(
        "POST",
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}",
        headers={**sb_headers(), "Content-Type": "application/json", "x-upsert": "true"},
        data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
    )
    r.raise_for_status()
    log.info("Caricato %s", path)


# ---------------------------------------------------------------- competizioni
def active_competitions(today):
    """Hint sulle competizioni probabilmente attive in base al mese."""
    m = today.month
    comps = []
    if m >= 8 or m <= 5:
        comps += ["Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1"]
    if m >= 9 or m <= 5:
        comps += ["Champions League", "Europa League", "Conference League"]
    if m in (1, 2, 3, 4, 5):
        comps += ["Coppa Italia"]
    if today.year == 2026 and 6 <= m <= 7:
        comps = ["Mondiali 2026"]
    return comps or ["principali campionati e coppe"]


# ---------------------------------------------------------------- Perplexity
def fetch_fixtures(start, end):
    comps = ", ".join(active_competitions(start))
    prompt = (
        f"Elenca le partite di calcio in programma dal {start:%Y-%m-%d} al {end:%Y-%m-%d} "
        f"nelle seguenti competizioni: {comps}. Rispondi SOLO con JSON valido nel formato "
        '{"fixtures":[{"home":"","away":"","league":"","when":"YYYY-MM-DD HH:MM"}]}. '
        "Orari in fuso Europe/Rome. Niente testo extra."
    )
    r = http(
        "POST",
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {PPLX_KEY}"},
        json={"model": PPLX_MODEL, "messages": [{"role": "user", "content": prompt}]},
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    fixtures = extract_json(content).get("fixtures", [])
    log.info("Fixture trovate: %d", len(fixtures))
    return fixtures


# ---------------------------------------------------------------- OpenAI
def predict(fixtures):
    prompt = (
        "Sei un analista di pronostici calcistici. Per ogni partita scegli UN tipo di bet "
        "(es. 1, X, 2, 1X, X2, Over 2.5, Under 2.5, Goal, NoGoal), stima la probabilità in "
        "percentuale intera (pct), una spiegazione breve in italiano (why, max 160 caratteri) "
        "e un H2H sintetico (h2h, max 80 caratteri). Rispondi SOLO con JSON: "
        '{"predictions":[{"home":"","away":"","bet":"","pct":0,"why":"","h2h":""}]}.\n'
        f"Partite: {json.dumps(fixtures, ensure_ascii=False)}"
    )
    r = http(
        "POST",
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_KEY}"},
        json={
            "model": OPENAI_MODEL,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"]).get("predictions", [])


# ---------------------------------------------------------------- normalizzazione e quote
def key(home, away):
    return f"{home}|{away}".strip().lower()


def merge_and_normalize(fixtures, preds):
    meta = {key(f["home"], f["away"]): f for f in fixtures if f.get("home") and f.get("away")}
    out = []
    for p in preds:
        f = meta.get(key(p.get("home", ""), p.get("away", "")))
        if not f:
            continue
        try:
            pct = int(round(float(p["pct"])))
        except (KeyError, ValueError, TypeError):
            continue
        out.append({
            "home": f["home"],
            "away": f["away"],
            "league": f.get("league", ""),
            "when": f.get("when", ""),
            "bet": p.get("bet", ""),
            "pct": max(PCT_MIN, min(PCT_MAX, pct)),
            "why": p.get("why", ""),
            "h2h": p.get("h2h", ""),
        })
    out.sort(key=lambda x: (-x["pct"], x["when"]))
    return out


def attach_odds(matches, odds_data):
    """odds.json accettato come {"home|away": 1.85} oppure {"home|away": {"odd": 1.85}}
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
    history = {
        "entries": entries,
        "stats": {
            "days_tracked": len(entries),
            "total_matches": sum(e["matches_count"] for e in entries),
        },
    }
    sb_upload("predictions_history.json", history)


# ---------------------------------------------------------------- main
def main():
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=DAYS_AHEAD)
    log.info("Aggiornamento %s -> %s", f"{now:%Y-%m-%d}", f"{end:%Y-%m-%d}")

    fixtures = fetch_fixtures(now, end)
    if not fixtures:
        log.error("Nessuna fixture: interrompo senza sovrascrivere i dati")
        return 1

    matches = merge_and_normalize(fixtures, predict(fixtures))
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
