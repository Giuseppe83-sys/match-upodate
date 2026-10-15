#!/usr/bin/env python3
"""BetStorm: bollettino informativo di quote reali (invio disattivato per default).

Per inviare: BETSTORM_TELEGRAM_SEND=1, dopo verifica del workflow.
Il file telegram_last_sent.json evita invii ripetuti dello stesso aggiornamento.
"""
import hashlib
import html
import json
import logging
import os
from datetime import datetime, timezone, timedelta

import requests
from betstorm_common import download, upload

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
LOG = logging.getLogger('betstorm-telegram')
FEEDS = [('calcio', 'odds.json'), ('tennis', 'tennis.json'), ('basket', 'basketball.json')]
STATE_FILE = 'telegram_last_sent.json'
MAX_AGE_HOURS = int(os.getenv('TELEGRAM_MAX_AGE_HOURS', '24'))
MAX_EVENTS = max(1, min(int(os.getenv('TELEGRAM_MAX_MATCHES', '8')), 15))


def parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        # betstorm_odds.py salva updated_at senza timezone, ma in UTC.
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def get_fresh_events(now):
    events = []
    stamps = {}
    for sport, filename in FEEDS:
        data = download(filename, {})
        if not isinstance(data, dict):
            LOG.warning('%s: formato non valido', filename)
            continue
        updated = parse_time(data.get('updated_at'))
        age = (now - updated) if updated else None
        if age is None or age < timedelta(0) or age > timedelta(hours=MAX_AGE_HOURS):
            LOG.warning('%s: aggiornamento assente o non recente, escluso', filename)
            continue
        stamps[sport] = updated
        for match in data.get('matches', []):
            if not isinstance(match, dict):
                continue
            start = parse_time(match.get('start'))
            if not start or start <= now:
                continue
            home, away = match.get('home'), match.get('away')
            if not isinstance(home, str) or not home.strip() or not isinstance(away, str) or not away.strip():
                continue
            quotes = []
            odds = match.get('odds') or {}
            for key in (('1', 'X', '2') if sport == 'calcio' else ('1', '2')):
                cell = odds.get(key)
                if not isinstance(cell, dict):
                    continue
                try:
                    value = float(cell.get('value', 0))
                except (TypeError, ValueError):
                    continue
                bookmaker = cell.get('bookmaker')
                if value <= 1 or not isinstance(bookmaker, str) or not bookmaker.strip():
                    continue
                label = home if key == '1' else away if key == '2' else 'Pareggio'
                quotes.append((label, value, bookmaker))
            if quotes:
                events.append((start, sport, match, quotes))
    events.sort(key=lambda item: (item[0], item[1], item[2]['home'], item[2]['away']))
    return events[:MAX_EVENTS], stamps


def build_message(events, stamps):
    rows = ['<b>BetStorm | Quote rilevate dai bookmaker</b>',
            '<i>Dati informativi, non pronostici né probabilità di vincita.</i>', '']
    for start, sport, match, quotes in events:
        rows.append(f"<b>{html.escape(match['home'])} – {html.escape(match['away'])}</b>")
        league = html.escape(str(match.get('league') or sport.title()))
        rows.append(f"{league} · {start.strftime('%d/%m/%Y %H:%M')} UTC")
        for label, value, bookmaker in quotes:
            rows.append(f"{html.escape(label)}: <b>{value:.2f}</b> · {html.escape(bookmaker)}")
        rows.append('')
    updates = ', '.join(f"{sport} {stamp.strftime('%d/%m %H:%M')} UTC" for sport, stamp in stamps.items())
    rows.append(f"<i>Rilevazione: {html.escape(updates)}. Le quote possono cambiare.</i>")
    rows.append('<i>18+ · Gioca responsabilmente. Nessuna vincita è garantita.</i>')
    return '\n'.join(rows)


def main():
    now = datetime.now(timezone.utc)
    events, stamps = get_fresh_events(now)
    if not events:
        LOG.info('Nessun evento futuro con quote recenti: nessun invio')
        return 0
    message = build_message(events, stamps)
    if len(message) > 4096:
        raise RuntimeError('Messaggio Telegram troppo lungo: ridurre TELEGRAM_MAX_MATCHES')
    digest = hashlib.sha256(message.encode('utf-8')).hexdigest()
    previous = download(STATE_FILE, {})
    if isinstance(previous, dict) and previous.get('digest') == digest:
        LOG.info('Bollettino già inviato: nessun duplicato')
        return 0
    if os.getenv('BETSTORM_TELEGRAM_SEND') != '1':
        LOG.info('ANTEPRIMA: invio disattivato (BETSTORM_TELEGRAM_SEND non impostato a 1)')
        print(message)
        return 0
    token = os.environ['TELEGRAM_BOT_TOKEN']
    chat = os.environ['TELEGRAM_CHAT_ID']
    response = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat, 'text': message, 'parse_mode': 'HTML', 'disable_web_page_preview': True},
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get('ok'):
        raise RuntimeError('Telegram non ha confermato l’invio')
    upload(STATE_FILE, {'digest': digest, 'sent_at': now.isoformat()})
    LOG.info('Bollettino inviato e registrato (%d eventi)', len(events))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
