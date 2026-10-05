#!/usr/bin/env python3
import os, html, requests
from betstorm_common import download
RESEND=os.environ['RESEND_API_KEY']; FROM=os.environ['RESEND_FROM_EMAIL']; TO=[x.strip() for x in os.environ.get('NEWSLETTER_RECIPIENTS','').split(',') if x.strip()]

def main():
    matches=(download('matches.json',{}).get('matches') or [])[:8]
    if not matches or not TO: print('Nessuna partita o destinatario'); return 0
    cards=[]
    for m in matches:
        cards.append(f"<h2>{html.escape(m['home'])} – {html.escape(m['away'])}</h2><p><b>{html.escape(str(m.get('bet','')))}</b> · {int(m.get('pct',0))}% · quota {m.get('quota','n.d.')}</p><p>{html.escape(str(m.get('why','')))}</p>")
    body='<h1>BetStorm — pronostici di oggi</h1>'+''.join(cards)+'<hr><p>18+ · Gioca responsabilmente. I pronostici non garantiscono vincite.</p>'
    payload={'from':FROM,'to':TO,'subject':'BetStorm — pronostici e quote aggiornate','html':body}
    r=requests.post('https://api.resend.com/emails',headers={'Authorization':f'Bearer {RESEND}','Content-Type':'application/json'},json=payload,timeout=60); r.raise_for_status(); print(r.json()); return 0
if __name__=='__main__': raise SystemExit(main())
