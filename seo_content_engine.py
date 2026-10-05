#!/usr/bin/env python3
"""Genera un articolo SEO BetStorm da dati reali già presenti in Supabase. Salva sempre in articles.json; WordPress è opzionale."""
import os, json, re, requests
from datetime import datetime, timezone
from betstorm_common import download, upload
KEY=os.environ['GEMINI_API_KEY']; MODEL=os.getenv('GEMINI_MODEL','gemini-3.5-flash-lite'); MAX=int(os.getenv('MAX_ARTICLES','60'))

def gemini(prompt):
    r=requests.post(f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent',headers={'x-goog-api-key':KEY,'Content-Type':'application/json'},json={'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'responseMimeType':'application/json','temperature':0.35}},timeout=120); r.raise_for_status(); t=r.json()['candidates'][0]['content']['parts'][0]['text']; return json.loads(re.search(r'\{.*\}',t,re.S).group())
def main():
    data=download('matches.json',{}); matches=(data.get('matches') or [])[:12]; store=download('articles.json',{'articles':[]}); old=store.get('articles',[])
    if not matches: print('no_matches_available'); return 0
    existing=[{'title':a.get('article_title'),'slug':a.get('slug')} for a in old[-30:]]
    prompt=f'''Sei l'editor SEO di BetStorm. Crea UN articolo originale in italiano basato SOLO sui dati forniti. Non inventare quote, risultati o statistiche. Obiettivo: utilità per l'utente e ricerca organica, senza keyword stuffing. Includi title <=60 caratteri, slug, meta_description <=155, primary_keyword, search_intent, excerpt, content_html con un solo H1, H2/H3, tabella o elenco quando utile, FAQ finale, link interni come placeholder /pronostici e /quote, e disclaimer 18+ sul gioco responsabile. Non promettere vincite. Evita cannibalizzazione con gli articoli esistenti. Rispondi SOLO JSON con article_title,slug,meta_description,primary_keyword,search_intent,excerpt,content_html.\nARTICOLI ESISTENTI:{json.dumps(existing,ensure_ascii=False)}\nDATI:{json.dumps(matches,ensure_ascii=False)}'''
    a=gemini(prompt); required={'article_title','slug','meta_description','primary_keyword','search_intent','excerpt','content_html'}
    if not required.issubset(a): raise RuntimeError('Output SEO incompleto')
    if len(a['meta_description'])>155: a['meta_description']=a['meta_description'][:152].rstrip()+'...'
    if any(x.get('slug')==a['slug'] for x in old): print('duplicate_slug'); return 0
    a['generated_at']=datetime.now(timezone.utc).isoformat(); a['status']='stored'; old.append(a); store={'articles':old[-MAX:]}; upload('articles.json',store)
    print(json.dumps({'status':'stored','slug':a['slug'],'title':a['article_title']},ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
