#!/usr/bin/env python3
"""Prepara e invia privatamente un link di disiscrizione a un solo iscritto di prova.

Non stampa mai token, link o indirizzo email nei log.
Non esegue modifiche senza BETSTORM_PREPARE_UNSUBSCRIBE=1.
"""

import hashlib
import os
import secrets

import requests


def main():
    if os.getenv("BETSTORM_PREPARE_UNSUBSCRIBE") != "1":
        print("Modalità protetta: nessuna modifica eseguita.")
        return

    email = os.getenv("NEWSLETTER_TEST_EMAIL", "").strip().lower()
    if not email or "@" not in email:
        raise RuntimeError("NEWSLETTER_TEST_EMAIL non configurata")

    base = os.environ["SUPABASE_URL"].rstrip("/")
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    resend_key = os.environ["RESEND_API_KEY"]
    sender = os.environ["RESEND_FROM_EMAIL"]

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    url = f"{base}/rest/v1/newsletter"

    response = requests.get(
        url,
        headers=headers,
        params={
            "select": "id,confirmed,unsubscribed,unsubscribe_token_hash",
            "email": f"eq.{email}",
            "limit": "2",
        },
        timeout=25,
    )
    response.raise_for_status()
    rows = response.json()

    if len(rows) != 1:
        raise RuntimeError("Iscrizione di prova non trovata o non univoca")

    row = rows[0]
    if row["confirmed"] is not True:
        raise RuntimeError("Email non confermata")
    if row["unsubscribed"] is not False:
        raise RuntimeError("Email già disiscritta")
    if row["unsubscribe_token_hash"] is not None:
        raise RuntimeError("Token già presente: operazione bloccata per non invalidarlo")

    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    update = requests.patch(
        url,
        headers={**headers, "Prefer": "return=representation"},
        params={
            "id": f"eq.{row['id']}",
            "confirmed": "eq.true",
            "unsubscribed": "eq.false",
            "unsubscribe_token_hash": "is.null",
        },
        json={"unsubscribe_token_hash": token_hash},
        timeout=25,
    )
    update.raise_for_status()
    updated = update.json()
    if len(updated) != 1:
        raise RuntimeError("Iscrizione modificata nel frattempo: nessun token salvato")

    link = f"https://www.betstorm.it/api/newsletter-unsubscribe?token={token}"
    body = (
        "<h2>BetStorm — prova disiscrizione</h2>"
        "<p>Questo messaggio contiene il tuo link personale per provare "
        "la disiscrizione dalla newsletter.</p>"
        f'<p><a href="{link}">Apri la pagina di disiscrizione</a></p>'
        "<p>Aprire il link non disiscrive automaticamente: "
        "la pagina richiede una conferma esplicita.</p>"
        "<p>Non inoltrare questo messaggio ad altre persone.</p>"
    )
    sent = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {resend_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": sender,
            "to": [email],
            "subject": "BetStorm — link di disiscrizione di prova",
            "html": body,
        },
        timeout=60,
    )
    if not sent.ok:
        # Il token rimane registrato: non rigenerarlo automaticamente.
        raise RuntimeError(
            f"Invio email non riuscito (HTTP {sent.status_code}). "
            "Token registrato: richiede verifica manuale."
        )

    print("Token registrato e link inviato via email al solo destinatario di prova.")
    print("Nessun token, link o indirizzo email mostrato nei log.")


if __name__ == "__main__":
    main()
