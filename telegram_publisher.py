#!/usr/bin/env python3
import os, html, requests
from betstorm_common import download
TOKEN=os.environ['TELEGRAM_BOT_TOKEN']; CHAT=os.environ['TELEGRAM_CHAT_ID']; MAX=int(os.getenv('TELEGRAM_MAX_MATCHES','10'))

def main():
    data=download('matches.json',{})
    matches=(data.get('matches') or [])[:MAX]
    if not matches: return 0
    rows=['<b>⚡ BetStorm — pronostici</b>','']
    for m in matches:
        rows += [f"<b>{html.escape(m['home'])} – {html.escape(m['away'])}</b>",f"Pronostico: <b>{html.escape(str(m.get('bet','n.d.')))}</b> · Confidenza: {int(m.get('pct',0))}% · Quota: {m.get('quota','n.d.')}",html.escape(str(m.get('why',''))),'']
    rows += ['<i>18+ · Gioca responsabilmente. I pronostici non garantiscono vincite.</i>']
    r=requests.post(f'https://api.telegram.org/bot{TOKEN}/sendMessage',json={'chat_id':CHAT,'text':'\n'.join(rows),'parse_mode':'HTML','disable_web_page_preview':True},timeout=60)
    r.raise_for_status(); print(r.json()); return 0
if __name__=='__main__': raise SystemExit(main())
