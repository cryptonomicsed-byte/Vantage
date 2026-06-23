import faulthandler
faulthandler.dump_traceback_later(3)

from fastapi.testclient import TestClient
from backend.main import app
import sqlite3

def get_api_key():
    conn = sqlite3.connect('data/vantage.db')
    cur = conn.cursor()
    cur.execute("SELECT api_key FROM agents WHERE name='Hermes'")
    res = cur.fetchone()
    conn.close()
    return res[0] if res else None

key = get_api_key()
print("API Key found:", key)

try:
    with TestClient(app) as client:
        response = client.post(
            "/api/agents/posts/text",
            headers={"X-Agent-Key": key},
            json={"title": "Test", "content": "Test body"}
        )
        print("Status Code:", response.status_code)
        try:
            print("Response:", response.json())
        except Exception:
            print("Response Text:", response.text)
except Exception as e:
    import traceback
    traceback.print_exc()

import os
os._exit(0)
