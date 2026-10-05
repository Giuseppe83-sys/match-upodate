import json, os, requests
from datetime import datetime, timezone

SUPABASE_URL=os.environ['SUPABASE_URL'].rstrip('/')
SUPABASE_KEY=os.environ['SUPABASE_SERVICE_ROLE_KEY']
BUCKET=os.getenv('SUPABASE_BUCKET','betstorm-data')

def headers(): return {'Authorization':f'Bearer {SUPABASE_KEY}','apikey':SUPABASE_KEY}
def download(path, default=None):
    r=requests.get(f'{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}',headers=headers(),timeout=60)
    return r.json() if r.status_code==200 else default

def upload(path,data):
    r=requests.post(f'{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}',headers={**headers(),'Content-Type':'application/json','x-upsert':'true','cache-control':'max-age=60'},data=json.dumps(data,ensure_ascii=False).encode(),timeout=60)
    r.raise_for_status()

def now_iso(): return datetime.now(timezone.utc).isoformat()
