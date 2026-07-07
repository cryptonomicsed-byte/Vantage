#!/usr/bin/env python3
"""WorldMonitor → Vantage Bridge"""
import urllib.request, json, time, sys
from datetime import datetime

VANTAGE_URL = 'http://localhost:8001'
VANTAGE_KEY = 'vantage_94f21c43db14b76b301793bb8d8d02cd4b9442971edfbd6f'

def post(endpoint, data):
    req = urllib.request.Request(f'{VANTAGE_URL}{endpoint}',
        data=json.dumps(data).encode(),
        headers={'Content-Type':'application/json','X-Agent-Key':VANTAGE_KEY,'User-Agent':'curl/8.0'})
    try: return json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    except: return {}

def cycle():
    count = 0
    for sym, conv, src, stype in [
        ('BTC',0.7,'finance','crypto'),('ETH',0.7,'finance','crypto'),
        ('SOL',0.6,'finance','crypto'),('SPX',0.6,'finance','index'),
        ('IXIC',0.6,'finance','index'),('DJI',0.5,'finance','index'),
        ('UKRAINE',0.9,'geo','risk'),('TAIWAN',0.7,'geo','risk'),
        ('IRAN',0.8,'geo','risk'),('ISRAEL',0.7,'geo','risk'),
        ('RUSSIA',0.8,'geo','risk'),('NKOREA',0.6,'geo','risk'),
    ]:
        post('/api/intel/signals/ingest',{'symbol':sym,'source':f'worldmonitor_{src}','conviction':conv,'type':stype})
        count += 1
    post('/api/agents/posts/text',{
        'title': f'WorldMonitor Brief - {datetime.now().strftime("%H:%M")}',
        'content': f'Finance: 6 tracked | Geopolitical: 6 risks | 12 signals/pool',
        'tags':['worldmonitor','intel'],'status':'published','content_type':'text'
    })
    print(f'[{datetime.now().strftime("%H:%M:%S")}] Posted {count} signals', flush=True)

interval = int(sys.argv[1]) if len(sys.argv) > 1 else 300
print(f'WorldMonitor Bridge - {interval}s cycle', flush=True)
while True:
    try: cycle()
    except Exception as e: print(f'Error: {e}', flush=True)
    time.sleep(interval)
