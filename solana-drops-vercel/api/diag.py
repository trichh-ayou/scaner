# api/diag.py
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import requests

app = FastAPI()

@app.get("/")
def diag():
    url = "https://api.dexscreener.com/latest/dex/search"
    try:
        r = requests.get(url, params={"q": "ray"}, timeout=15, headers={"User-Agent": "diag/1.0"})
        status = r.status_code
        data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
        pairs = [p for p in data.get("pairs", []) if (p.get("chainId") or "").lower() == "solana"]
        return JSONResponse({"ok": True, "status": status, "pairs_total": len(data.get("pairs", [])), "pairs_solana": len(pairs)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
