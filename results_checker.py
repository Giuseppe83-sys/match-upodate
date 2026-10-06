#!/usr/bin/env python3
"""Verifica schedine storiche usando Football-Data. I casi non risolti restano pending."""
import os, requests
from datetime import datetime, timedelta, timezone
from betstorm_common import download, upload, now_iso
FD=os.environ['FOOTBALL_DATA_API_KEY']

def norm(s): return ''.join(c.lower() for c in (s or '') if c.isalnum())
def outcome(score):
    h=score.get('fullTime',{}).get('home'); a=score.get('fullTime',{}).get('away')
    if h is None or a is None: return None
    return ('1' if h>a else '2' if a>h else 'X'), h, a

def bet_ok(bet,res,h,a):
    b=(bet or '').upper().replace(' ','')
    if b in ('1','X','2'): return b==res
    if b=='1X': return res in ('1','X')
    if b=='X2': return res in ('X','2')
    if b=='12': return res in ('1','2')
    if b=='OVER2.5': return h+a>2
    if b=='UNDER2.5': return h+a<3
    if b=='GOAL': return h>0 and a>0
    if b=='NOGOAL': return h==0 or a==0
    return None

def main():
    hist=download('predictions_history.json',{'entries':[]}); entries=hist.get('entries',[])
    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=1)).isoformat()
    end = today.isoformat()    r=requests.get('https://api.football-data.org/v4/matches',headers={'X-Auth-Token':FD},params={'dateFrom':start,'dateTo':end},timeout=60); r.raise_for_status()
    finished=[m for m in r.json().get('matches',[]) if m.get('status')=='FINISHED']
    total=correct=incorrect=0
    for e in entries:
        if e.get('date','') < start: continue
        for slip in (e.get('schedina') or {}).values():
            if not slip: continue
            for p in slip.get('picks',[]):
                total+=1; found=None
                for m in finished:
                    if norm(p.get('home')) in norm(m['homeTeam'].get('name')) and norm(p.get('away')) in norm(m['awayTeam'].get('name')):
                        found=m; break
                if not found: p['result_status']='pending'; continue
                o=outcome(found.get('score',{}))
                if not o: p['result_status']='pending'; continue
                ok=bet_ok(p.get('bet'),*o)
                p['final_score']=f'{o[1]}-{o[2]}'; p['result_status']='correct' if ok is True else 'incorrect' if ok is False else 'pending'
                if ok is True: correct+=1
                elif ok is False: incorrect+=1
    checked=correct+incorrect
    hist['result_stats']={'updated_at':now_iso(),'predictions_seen':total,'checked':checked,'correct':correct,'incorrect':incorrect,'success_rate':round(correct/checked*100,1) if checked else 0}
    upload('predictions_history.json',hist); print(hist['result_stats']); return 0
if __name__=='__main__': raise SystemExit(main())
