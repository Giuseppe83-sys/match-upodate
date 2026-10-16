
#!/usr/bin/env python3
"""Prepara un link di disiscrizione per un solo iscritto di prova."""

import hashlib
import os
import secrets
from urllib.parse import quote

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

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    url = f"{base}/rest/v1/newsletter"
    params = {
        "select": "id,confirmed,unsubscribed,unsubscribe_token_hash",
        "email": f"eq.{email}",
        "limit": "2",
    }

    response = requests.get(
        url, headers=headers, params=params, timeout=25
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
        raise RuntimeError(
            "Token già presente: operazione bloccata per non invalidarlo"
        )

    token = secrets.token_hex(32)
    token_hash = hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    update = requests.patch(
        url,
        headers={
            **headers,
            "Prefer": "return=representation",
        },
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
        raise RuntimeError(
            "Iscrizione modificata nel frattempo: nessun token salvato"
        )

    link = (
        "https://www.betstorm.it/api/newsletter-unsubscribe"
        "?token=" + quote(token)
    )

    print("Token di prova registrato correttamente.")
    print("Link personale di disiscrizione:")
    print(link)
    print("Conserva il link privatamente. Non condividerlo in chat.")


if __name__ == "__main__":
    main()
