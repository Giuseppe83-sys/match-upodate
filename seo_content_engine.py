#!/usr/bin/env python3
"""Genera contenuti SEO BetStorm usando esclusivamente dati presenti in Supabase."""
import os, json, re, requests
from datetime import datetime, timezone
from betstorm_common import download, upload

KEY = os.environ['GEMINI_API_KEY']
MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite')
MAX = int(os.getenv('MAX_ARTICLES', '60'))


def gemini(prompt):
    r = requests.post(
        f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent',
        headers={'x-goog-api-key': KEY, 'Content-Type': 'application/json'},
        json={
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {
                'responseMimeType': 'application/json',
                'temperature': 0.25
            }
        },
        timeout=120
    )
    r.raise_for_status()
    text = r.json()['candidates'][0]['content']['parts'][0]['text']
    match = re.search(r'\{.*\}', text, re.S)
    if not match:
        raise RuntimeError('Gemini non ha restituito JSON valido')
    return json.loads(match.group())


def article_title(a):
    return a.get('article_title') or a.get('title') or ''


def normalize(value):
    return re.sub(r'[^a-z0-9]+', '-', (value or '').lower()).strip('-')


def main():
    data = download('matches.json', {})
    matches = (data.get('matches') or [])[:12]

    if not matches:
        print('no_matches_available')
        return 0

    store = download('articles.json', {'articles': []})
    old = store.get('articles', [])

    # La data editoriale deve derivare dai dati delle partite, non dalla data
    # di esecuzione del job. Il modello non deve chiamare "oggi" una data futura.
    existing = [
        {
            'title': article_title(a),
            'slug': a.get('slug', ''),
            'primary_keyword': a.get('primary_keyword', ''),
            'target_date': a.get('target_date', '')
        }
        for a in old[-40:]
    ]

    prompt = f"""
Sei l'editor SEO di BetStorm.

Crea UN SOLO articolo originale in italiano utilizzando ESCLUSIVAMENTE i dati
contenuti in DATI. Non aggiungere conoscenze sportive esterne.

REGOLE OBBLIGATORIE:
1. Non inventare quote, probabilità, ranking, forma recente, statistiche,
   infortuni, risultati, testa-a-testa, classifiche, percentuali o informazioni
   tattiche che non siano esplicitamente presenti in DATI.
2. Se un dato non è presente, omettilo. Non stimarlo.
3. Non presentare una partita futura come se si giocasse "oggi".
   Usa "oggi" nel titolo/testo SOLO se la data della partita coincide realmente
   con la data corrente UTC: {datetime.now(timezone.utc).date().isoformat()}.
4. Se le partite sono future, usa formule come "pronostici del [data]",
   "prossime partite" o "partite in programma".
5. Non promettere vincite e non usare espressioni come "vincita sicura",
   "scelta sicura", "garantito", "profitti continui", "strategia vincente"
   o equivalenti.
6. Distingui chiaramente fatti presenti nei dati da eventuali suggerimenti
   editoriali. Non trasformare una mancanza di dati in un'affermazione.
7. Evita cannibalizzazione e duplicati rispetto ad ARTICOLI_ESISTENTI.
   Non creare un semplice "approfondimento" dello stesso argomento/data per
   aggirare un duplicato.
8. Inserisci un disclaimer 18+ e un richiamo al gioco responsabile.
9. Inserisci link interni placeholder /pronostici e /quote quando pertinenti.
10. Non usare keyword stuffing.

OUTPUT:
Rispondi SOLO con JSON valido contenente:
article_title, slug, meta_description, primary_keyword, search_intent,
excerpt, target_date, content_html.

VINCOLI:
- article_title massimo 60 caratteri.
- meta_description massimo 155 caratteri.
- slug descrittivo e senza suffissi artificiali come "approfondimento",
  "versione-2", "nuovo".
- content_html deve contenere un solo H1, H2/H3, una tabella o elenco quando
  utile e FAQ finale.
- target_date deve essere la data principale effettivamente trattata
  nell'articolo, in formato YYYY-MM-DD, ricavata esclusivamente da DATI.

ARTICOLI_ESISTENTI:
{json.dumps(existing, ensure_ascii=False)}

DATI:
{json.dumps(matches, ensure_ascii=False)}
"""

    a = gemini(prompt)

    required = {
        'article_title', 'slug', 'meta_description', 'primary_keyword',
        'search_intent', 'excerpt', 'target_date', 'content_html'
    }
    if not required.issubset(a):
        raise RuntimeError('Output SEO incompleto')

    a['slug'] = normalize(a['slug'])
    a['target_date'] = str(a['target_date'])[:10]

    if len(a['article_title']) > 60:
        raise RuntimeError('Titolo SEO oltre 60 caratteri')

    if len(a['meta_description']) > 155:
        a['meta_description'] = a['meta_description'][:152].rstrip() + '...'

    # Anti-duplicato forte: slug oppure stesso topic/data.
    new_keyword = normalize(a.get('primary_keyword'))
    for x in old:
        if normalize(x.get('slug')) == a['slug']:
            print('duplicate_slug')
            return 0

        old_date = str(x.get('target_date') or '')[:10]
        old_keyword = normalize(x.get('primary_keyword'))
        old_title = normalize(article_title(x))

        same_date = bool(old_date and old_date == a['target_date'])
        same_topic = bool(
            new_keyword and (
                new_keyword == old_keyword
                or new_keyword in old_title
                or old_keyword in normalize(a['article_title'])
            )
        )

        if same_date and same_topic:
            print('duplicate_topic_date')
            return 0

    # Controlli editoriali finali contro claim pericolosi.
    body = (a['article_title'] + ' ' + a['content_html']).lower()
    forbidden = [
        'vincita sicura',
        'scelta sicura',
        'garantito',
        'profitti continui',
        'strategia vincente'
    ]
    if any(term in body for term in forbidden):
        raise RuntimeError('Contenuto rifiutato: claim non consentito')

    a['generated_at'] = datetime.now(timezone.utc).isoformat()
    a['status'] = 'stored'

    old.append(a)
    store = {'articles': old[-MAX:]}
    upload('articles.json', store)

    print(json.dumps({
        'status': 'stored',
        'slug': a['slug'],
        'title': a['article_title'],
        'target_date': a['target_date']
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
