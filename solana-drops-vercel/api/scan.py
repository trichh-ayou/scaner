# api/scan.py
from __future__ import annotations
import os
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from .scanner import scan_once
app = FastAPI(title="Solana Drops — Scan API")
def _cfg():
    return {
        "VOLUME_MIN": float(os.getenv("VOLUME_MIN", "100000")),
        "VOLUME_MAX": float(os.getenv("VOLUME_MAX", "100000000")),
        "PRICE_THRESHOLD_PCT": float(os.getenv("PRICE_THRESHOLD_PCT", "85")),
        "LIQ_MIN_USD": float(os.getenv("LIQ_MIN_USD", "20000")),
        "MCAP_MIN": float(os.getenv("MCAP_MIN", "2000000")),
        "MCAP_MAX": float(os.getenv("MCAP_MAX", "15000000")),
        "SEARCH_SEEDS": os.getenv("SEARCH_SEEDS","sol,usdc,usdt,ray,raydium,jup,orca,bonk,wif,pyth,jto,usd,step,mngo,heli,st,ra,ju,or,wi,bo,py,so,na,li,fi,me,go,mo,hy,lp"),
        "SEARCH_MIN_Q_LEN": int(os.getenv("SEARCH_MIN_Q_LEN", "2")),
        "MAX_PAGES": int(os.getenv("MAX_PAGES", "8")),
        "PAGE_DELAY_S": float(os.getenv("PAGE_DELAY_S", "0.2")),
        "ATH_FALLBACK_USE_POOLMAX": os.getenv("ATH_FALLBACK_USE_POOLMAX", "0").strip().lower() in {"1","true","yes","y","on"},
        "SOLANATRACKER_API_KEY": os.getenv("SOLANATRACKER_API_KEY",""),
    }
@app.post("/")
def scan(pages: int | None = Query(default=None, ge=1, le=40)):
    cfg = _cfg()
    res = scan_once(
        volume_min=cfg["VOLUME_MIN"],
        volume_max=cfg["VOLUME_MAX"],
        price_threshold_pct=cfg["PRICE_THRESHOLD_PCT"],
        liq_min_usd=cfg["LIQ_MIN_USD"],
        mcap_min=cfg["MCAP_MIN"],
        mcap_max=cfg["MCAP_MAX"],
        seeds_from_env=cfg["SEARCH_SEEDS"],
        max_pages=pages or cfg["MAX_PAGES"],
        min_seed_len=cfg["SEARCH_MIN_Q_LEN"],
        page_delay_s=cfg["PAGE_DELAY_S"],
        st_api_key=cfg["SOLANATRACKER_API_KEY"],
        use_proxy_poolmax=cfg["ATH_FALLBACK_USE_POOLMAX"],
    )
    return JSONResponse(res)
