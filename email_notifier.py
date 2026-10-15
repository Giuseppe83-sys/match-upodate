#!/usr/bin/env python3
"""BetStorm newsletter informativa basata sulle quote rilevate.

Predefinito: anteprima nei log, SENZA invio.
Per inviare: BETSTORM_EMAIL_SEND=1, con credenziali Resend e destinatari configurati.
"""
import html
import os
from datetime import datetime, timedelta, timezone

import requests
from betstorm_common import download

FEEDS = (("Calcio", "odds.json"), ("Tennis", "tennis.json"), ("Basket", "basketball.json"))
MAX_EVENTS = max(1, min(int(os.getenv("NEWSLETTER_MAX_EVENTS", "9")), 20))
MAX_AGE_HOURS = 24


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
    if not recipients:
        raise RuntimeError("Invio bloccato: NEWSLETTER_RECIPIENTS vuoto")
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": sender, "to": recipients, "subject": subject, "html": body},
        timeout=60,
    )
    response.raise_for_status()
    print(f"Email inviata tramite Resend a {len(recipients)} destinatari")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
