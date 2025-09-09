# api/scanner.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import math, os, time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple
import requests
try:
    from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type
except Exception:  # pragma: no cover
    def retry(*a, **k): 
        def d(f): return f
        return d
    def stop_after_attempt(*a, **k): return None
    def wait_exponential_jitter(*a, **k): return None
    def retry_if_exception_type(*a, **k): return None

@dataclass
class PairRow:
    timestamp: str
    tokenName: str
    symbol: str
    baseAddress: str
    pairAddress: str
    priceUsd: float
    athPrice: float
    pctOfAth: float
    volumeH24: float
    liquidityUsd: float
    dexLink: str

def safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    if x is None: return default
    try:
        s = str(x).strip()
        if s.lower() in {'nan','none',''}: return default
        return float(s)
    except Exception:
        return default

DEX_BASE = "https://api.dexscreener.com"
DEX_WEB = "https://dexscreener.com"
CHAIN_ID = "solana"
ENDPOINT_SEARCH = f"{DEX_BASE}/latest/dex/search"
ENDPOINT_PAIR   = f"{DEX_BASE}/latest/dex/pairs/{CHAIN_ID}"
ENDPOINT_TOKENS = f"{DEX_BASE}/tokens/v1/{CHAIN_ID}"
ST_BASE = os.getenv("SOLANATRACKER_BASE", "https://data.solanatracker.io").rstrip("/")

class DexClient:
    def __init__(self, timeout: int = 15):
        self.s = requests.Session()
        self.s.headers.update({"Accept":"application/json","User-Agent":"solana-drops-vercel/1.0"})
        self.timeout = timeout
    @retry(reraise=True, stop=stop_after_attempt(5), wait=wait_exponential_jitter(initial=1,max=15),
           retry=retry_if_exception_type((requests.RequestException,)))
    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        r = self.s.get(url, params=params, timeout=self.timeout)
        if r.status_code in (429,500,502,503,504):
            raise requests.RequestException(f"{r.status_code}")
        r.raise_for_status()
        return r.json()

class STClient:
    def __init__(self, api_key: str, timeout: int = 15):
        import requests
        self.s = requests.Session()
        self.s.headers.update({"Accept":"application/json","x-api-key":api_key.strip(),"User-Agent":"solana-drops-vercel/1.0"})
        self.base = ST_BASE
        self.timeout = timeout
        self.unauthorized = False
    @retry(reraise=True, stop=stop_after_attempt(4), wait=wait_exponential_jitter(initial=1,max=10),
           retry=retry_if_exception_type((requests.RequestException,)))
    def get_token_ath(self, mint: str) -> Optional[float]:
        url = f"{self.base}/tokens/{mint}/ath"
        r = self.s.get(url, timeout=self.timeout)
        if r.status_code == 401: self.unauthorized=True; return None
        if r.status_code == 404: return None
        if r.status_code in (429,500,502,503,504): raise requests.RequestException(f"{r.status_code}")
        r.raise_for_status()
        return safe_float(r.json().get("highest_price"))

DEFAULT_SEEDS = [chr(c)+chr(d) for c in range(ord('a'), ord('z')+1) for d in range(ord('a'), ord('z')+1)]
def _get_seeds(max_pages: int, seeds_from_env: str, min_len: int) -> List[str]:
    if seeds_from_env:
        seeds=[s.strip() for s in seeds_from_env.split(',') if len(s.strip())>=min_len]
    else:
        seeds=[s for s in DEFAULT_SEEDS if len(s)>=min_len]
    return seeds[:max_pages] if max_pages>0 else seeds

def fetch_pairs_page(client: DexClient, page: int, seeds: List[str]) -> List[Dict[str, Any]]:
    if page<1 or page>len(seeds): return []
    seed = seeds[page-1]
    data = client.get_json(ENDPOINT_SEARCH, params={"q": seed})
    raw = data.get("pairs") or []
    return [p for p in raw if (p.get("chainId") or "").lower()==CHAIN_ID]

def select_best_pair_by_token(pairs: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for p in pairs:
        base = ((p.get("baseToken") or {}).get("address") or "").strip()
        if not base: continue
        liq = safe_float(((p.get("liquidity") or {}).get("usd")),0) or 0.0
        vol = safe_float(((p.get("volume") or {}).get("h24")),0) or 0.0
        ex = best.get(base)
        if not ex: best[base]=p; continue
        liq0 = safe_float(((ex.get("liquidity") or {}).get("usd")),0) or 0.0
        vol0 = safe_float(((ex.get("volume") or {}).get("h24")),0) or 0.0
        if liq>liq0 or (liq==liq0 and vol>vol0): best[base]=p
    return best

def _fetch_token_pools(client: DexClient, token_addr: str) -> List[Dict[str, Any]]:
    url=f"{ENDPOINT_TOKENS}/{token_addr}"
    try:
        data = client.get_json(url)
        if isinstance(data, list):
            return [p for p in data if (p.get("chainId") or "").lower()==CHAIN_ID]
        if isinstance(data, dict):
            return [p for p in (data.get("pairs") or []) if (p.get("chainId") or "").lower()==CHAIN_ID]
    except Exception: pass
    return []

def _ensure_ath(dex: DexClient, pair: Dict[str, Any], st: Optional[STClient], use_proxy_poolmax: bool) -> Optional[float]:
    ath = safe_float(((pair.get("allTimeHigh") or {}).get("price")))
    if ath and ath>0: return ath
    pair_addr = (pair.get("pairAddress") or "").strip()
    if pair_addr:
        try:
            details = dex.get_json(f"{ENDPOINT_PAIR}/{pair_addr}")
            lst = details.get("pairs") or []
            if lst:
                a2 = safe_float(((lst[0].get("allTimeHigh") or {}).get("price")))
                if a2 and a2>0: return a2
        except Exception: pass
    base_addr = ((pair.get("baseToken") or {}).get("address") or "").strip()
    tps: List[Dict[str, Any]] = []
    if base_addr:
        tps = _fetch_token_pools(dex, base_addr)
        ats=[safe_float(((tp.get('allTimeHigh') or {}).get('price'))) for tp in tps]
        ats=[a for a in ats if a and a>0]
        if ats: return max(ats)
    if st and base_addr:
        try:
            st_ath = st.get_token_ath(base_addr)
            if st_ath and st_ath>0: return st_ath
        except Exception: pass
    if use_proxy_poolmax and tps:
        prices=[safe_float(tp.get('priceUsd')) for tp in tps]
        prices=[p for p in prices if p and p>0]
        if prices: return max(prices)  # proxy (not historical)
    return None

def _resolve_market_cap(dex: DexClient, pair: Dict[str, Any]) -> Optional[float]:
    m = safe_float(pair.get("marketCap"))
    if m and m>0: return m
    f = safe_float(pair.get("fdv"))
    if f and f>0: return f
    base = ((pair.get("baseToken") or {}).get("address") or "").strip()
    if not base: return None
    tps=_fetch_token_pools(dex, base)
    cands=[]
    for tp in tps:
        m = safe_float(tp.get("marketCap"))
        f = safe_float(tp.get("fdv"))
        if m and m>0: cands.append(m)
        elif f and f>0: cands.append(f)
    return max(cands) if cands else None

def filter_candidates(pairs: Iterable[Dict[str, Any]], *, dex: DexClient,
    vol_min: float, vol_max: float, pct_threshold: float, liq_min_usd: float,
    mcap_min: float, mcap_max: float, st: Optional[STClient], use_proxy_poolmax: bool,
    now_iso: str) -> Tuple[List[PairRow], Dict[str,int]]:
    stats={"mcap_ok":0,"mcap_missing":0,"mcap_out":0,"ath_found":0,"ath_missing":0}
    rows: List[PairRow] = []
    for p in pairs:
        base = (p.get("baseToken") or {})
        token_name=(base.get("name") or "").strip()
        symbol=(base.get("symbol") or "").strip()
        base_addr=(base.get("address") or "").strip()
        pair_addr=(p.get("pairAddress") or "").strip()
        price=safe_float(p.get("priceUsd"))
        vol=safe_float(((p.get("volume") or {}).get("h24")))
        liq=safe_float(((p.get("liquidity") or {}).get("usd")))
        if not base_addr or price is None or vol is None or liq is None: continue
        if vol<vol_min or vol>vol_max: continue
        if liq<liq_min_usd: continue
        mcap=_resolve_market_cap(dex,p)
        if mcap is None: stats["mcap_missing"]+=1; continue
        if not (mcap_min<=mcap<=mcap_max): stats["mcap_out"]+=1; continue
        stats["mcap_ok"]+=1
        ath=safe_float(((p.get("allTimeHigh") or {}).get("price")))
        if ath is None or ath<=0:
            ath=_ensure_ath(dex,p,STClient(os.getenv('SOLANATRACKER_API_KEY','')) if os.getenv('SOLANATRACKER_API_KEY','') else None,use_proxy_poolmax)
        if ath is None or ath<=0: stats["ath_missing"]+=1; continue
        stats["ath_found"]+=1
        pct=float(price)/float(ath)
        if pct <= (pct_threshold/100.0):
            rows.append(PairRow(now_iso, token_name, symbol, base_addr, pair_addr, float(price), float(ath), pct, float(vol), float(liq), f"{DEX_WEB}/{CHAIN_ID}/{pair_addr}" if pair_addr else f"{DEX_WEB}/{CHAIN_ID}"))
    return rows, stats

def scan_once(*, volume_min:float, volume_max:float, price_threshold_pct:float, liq_min_usd:float,
              mcap_min:float, mcap_max:float, seeds_from_env:str, max_pages:int, min_seed_len:int,
              page_delay_s:float, st_api_key:str, use_proxy_poolmax:bool, debug: bool=False)->Dict[str,Any]:
    import datetime as _dt
    dex=DexClient(); st=STClient(st_api_key) if st_api_key else None
    seeds=_get_seeds(max_pages, seeds_from_env, min_seed_len)
    fetched=0; prefiltered=[]; errors=[]
    for idx in range(1,len(seeds)+1):
        seed = seeds[idx-1]
        try:
            chunk = fetch_pairs_page(dex, idx, seeds)
        except Exception as e:
            chunk = []
            errors.append({"seed": seed, "page": idx, "error": str(e)})
        fetched += len(chunk)
        for p in chunk:
            vol=safe_float(((p.get('volume') or {}).get('h24'))); liq=safe_float(((p.get('liquidity') or {}).get('usd')))
            if vol is None or liq is None: continue
            if vol<volume_min or vol>volume_max: continue
            if liq<liq_min_usd: continue
            inline_mcap = safe_float(p.get('marketCap')) or safe_float(p.get('fdv'))
            if inline_mcap is not None and not (mcap_min<=inline_mcap<=mcap_max): continue
            prefiltered.append(p)
        time.sleep(page_delay_s)
    best = select_best_pair_by_token(prefiltered)
    now_iso = _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    rows,stats = filter_candidates(best.values(), dex=dex, vol_min=volume_min, vol_max=volume_max,
                                   pct_threshold=price_threshold_pct, liq_min_usd=liq_min_usd,
                                   mcap_min=mcap_min, mcap_max=mcap_max, st=st,
                                   use_proxy_poolmax=use_proxy_poolmax, now_iso=now_iso)
    stats.update({"search_pairs":fetched, "prefilter":len(prefiltered), "unique_tokens":len(best), "candidates_after_threshold":len(rows)})
    out = {"stats":stats, "rows":[asdict(r) for r in rows], "st_enabled": bool(st_api_key)}
    if debug:
        out["errors"] = errors
        out["used_seeds"] = seeds
    return out

