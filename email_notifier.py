#!/usr/bin/env python3
"""BetStorm newsletter informativa basata sulle quote rilevate.

Predefinito: anteprima nei log, SENZA invio.
Per inviare: BETSTORM_EMAIL_SEND=1, con credenziali Resend e destinatari configurati.
"""
import html
import os
import hashlib
import json
from datetime import datetime, timedelta, timezone

import requests
from betstorm_common import download

FEEDS = (("Calcio", "odds.json"), ("Tennis", "tennis.json"), ("Basket", "basketball.json"))
MAX_EVENTS = max(1, min(int(os.getenv("NEWSLETTER_MAX_EVENTS", "9")), 20))
MAX_AGE_HOURS = 24
STATE_FILE = "newsletter_send_state.json"


def parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        # Il vecchio updater salva updated_at senza timezone, ma in UTC.
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None


def safe(value):
    return html.escape(str(value or ""), quote=True)


def collect(now):
    items = []
    feed_times = []
    for sport, filename in FEEDS:
        data = download(filename, {})
        if not isinstance(data, dict):
            print(f"Feed {filename} non valido: escluso")
            continue
        updated = parse_time(data.get("updated_at"))
        if updated is None or updated > now + timedelta(minutes=10) or now - updated > timedelta(hours=MAX_AGE_HOURS):
            print(f"Feed {filename} senza data attendibile o troppo vecchio: escluso")
            continue
        feed_times.append((sport, updated))
        for match in data.get("matches") or []:
            if not isinstance(match, dict):
                continue
            start = parse_time(match.get("start"))
            if start is None or start <= now:
                continue
            home, away = match.get("home"), match.get("away")
            if not home or not away:
                continue
            outcomes = []
            odds = match.get("odds") or {}
            if not isinstance(odds, dict):
                continue
            for label in ("1", "X", "2"):
                cell = odds.get(label)
                if not isinstance(cell, dict):
                    continue
                try:
                    value = float(cell.get("value"))
                except (ValueError, TypeError):
                    continue
                bookmaker = str(cell.get("bookmaker") or "").strip()
                if value <= 1 or not bookmaker:
                    continue
                outcomes.append((label, value, bookmaker))
            if len(outcomes) < 2:
                continue
            items.append({"sport": sport, "home": str(home), "away": str(away),
                          "league": str(match.get("league") or sport), "start": start,
                          "outcomes": outcomes, "updated": updated})
    items.sort(key=lambda x: x["start"])
    return items[:MAX_EVENTS], feed_times


def build_html(items, feed_times):
    parts = ["<h1>BetStorm — quote sportive rilevate</h1>",
             "<p>Informazioni sulle quote disponibili al momento della rilevazione. "
             "Le quote possono variare e non costituiscono pronostici né garanzie di risultato.</p>"]
    for item in items:
        parts.append(f"<h2>{safe(item['home'])} – {safe(item['away'])}</h2>")
        parts.append(f"<p>{safe(item['sport'])} · {safe(item['league'])} · "
                     f"Inizio: {item['start'].strftime('%d/%m/%Y %H:%M')} UTC</p>")
        parts.append("<ul>")
        for label, value, bookmaker in item["outcomes"]:
            parts.append(f"<li>Esito {safe(label)}: quota {value:.2f} — {safe(bookmaker)}</li>")
        parts.append("</ul>")
        parts.append(f"<p><small>Rilevazione: {item['updated'].strftime('%d/%m/%Y %H:%M')} UTC</small></p>")
    parts.append("<hr><p><small>Fonti: The Odds API, quote attribuite ai bookmaker indicati. "
                 "Non sono state verificate indipendentemente presso ogni bookmaker. "
                 "18+ · Il gioco può causare dipendenza. Gioca responsabilmente.</small></p>")
    return "\n".join(parts)



def state_url():
    base = os.environ["SUPABASE_URL"].rstrip("/")
    bucket = os.getenv("SUPABASE_BUCKET", "betstorm-data")
    return f"{base}/storage/v1/object/{bucket}/{STATE_FILE}"


def storage_headers():
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return {"Authorization": f"Bearer {key}", "apikey": key}


def read_state():
    response = requests.get(state_url(), headers=storage_headers(), timeout=25)
    if response.status_code == 404:
        return {}
    response.raise_for_status()  # Fail closed: never send when storage is unavailable.
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Stato newsletter non valido: invio bloccato")
    return data


def write_state(state):
    headers = {**storage_headers(), "Content-Type": "application/json", "x-upsert": "true"}
    response = requests.post(state_url(), headers=headers, data=json.dumps(state).encode("utf-8"), timeout=25)
    response.raise_for_status()


def delivery_key(now, body, recipients):
    # One newsletter per UTC calendar day per recipient set, even if odds change.
    recipient_hash = hashlib.sha256("\n".join(sorted(x.lower() for x in recipients)).encode()).hexdigest()
    return now.strftime("%Y-%m-%d") + ":" + recipient_hash


def main():
    now = datetime.now(timezone.utc)
    items, feed_times = collect(now)
    if not items:
        print("Nessun evento futuro con quote recenti: nessuna email preparata o inviata")
        return 0
    subject = "BetStorm — quote sportive aggiornate"
    body = build_html(items, feed_times)
    send = os.getenv("BETSTORM_EMAIL_SEND", "0") == "1"
    if not send:
        print("=== ANTEPRIMA EMAIL: NESSUN INVIO ===")
        print("Oggetto:", subject)
        print("Eventi:", len(items))
        print(body)
        print("=== FINE ANTEPRIMA ===")
        return 0

    api_key = os.environ["RESEND_API_KEY"]
    sender = os.environ["RESEND_FROM_EMAIL"]
    recipients = [x.strip() for x in os.environ.get("NEWSLETTER_RECIPIENTS", "").split(",") if x.strip()]
    if len(recipients) != 1:
        raise RuntimeError("Invio bloccato: per ora e' consentito un solo destinatario")

    key = delivery_key(now, body, recipients)
    state = read_state()
    if state.get("key") == key:
        print("Newsletter gia' inviata o prenotata oggi: nessun nuovo invio")
        return 0

    # Reserve BEFORE contacting Resend. A failed/uncertain send stays blocked
    # until manually investigated: prevents accidental duplicate delivery.
    reservation = {"key": key, "status": "reserved", "reserved_at": now.isoformat()}
    write_state(reservation)
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": sender, "to": recipients, "subject": subject, "html": body},
        timeout=60,
    )
    response.raise_for_status()
    reservation["status"] = "sent"
    reservation["sent_at"] = datetime.now(timezone.utc).isoformat()
    write_state(reservation)
    print("Email inviata tramite Resend a un destinatario; stato registrato su Supabase")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
