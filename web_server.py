"""
Web Dashboard — Flask backend (simplified reliable version)

Uses mootdx exclusively for all data. No East Money API dependency.
"""

import os, sys, json, time, logging, threading
from datetime import datetime, timedelta

import pandas as pd, numpy as np
from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_DIR

app = Flask(__name__)
logger = logging.getLogger("web")

_cache = {"market": {}, "recommend": {}, "selection_running": False}
_lock = threading.Lock()
_selector = None

def get_sel():
    global _selector
    if _selector is None:
        from strategy.selector import StockSelector
        _selector = StockSelector()
    if _selector.fetcher is None:
        _selector._init_modules()
    return _selector

def get_fetcher():
    return get_sel().fetcher

def _summarize_signals(tech_raw):
    return [f"{n}={['bear','neutral','bull'][max(-1,min(1,ind.get('signal',0)))+1]}"
            for n, ind in (tech_raw or {}).items() if ind.get('signal',0) in (-1,1)]


# ===== History price helpers =====
_price_cache = {}
_price_cache_ts = {}

def _lookup_prices(symbols):
    """
    Batch latest-price lookup for many symbols in a single TDX quotes call.
    Returns dict: {symbol: (price, prev_close)}. Uses a 60s cache.
    """
    now = time.time()
    out = {}
    to_fetch = []
    for sym in symbols:
        if sym in _price_cache_ts and now - _price_cache_ts[sym] < 60:
            out[sym] = (_price_cache[sym][0], _price_cache[sym][1])
        else:
            to_fetch.append(sym)

    if to_fetch:
        fetched = {}
        try:
            client = get_fetcher()._get_quotes_client()
            # quotes() caps around 80 per call
            for i in range(0, len(to_fetch), 80):
                batch = to_fetch[i:i + 80]
                q = client.quotes(symbol=batch)
                if q is not None and len(q) > 0:
                    for _, row in q.iterrows():
                        code = str(row.get("code", "")).zfill(6)
                        p = float(row.get("price", 0) or 0)
                        pc = float(row.get("last_close", 0) or 0)
                        if p > 0:
                            fetched[code] = (p, pc if pc > 0 else None)
        except Exception:
            pass

        # Fallback: K-line close for symbols TDX quotes missed
        for sym in to_fetch:
            if sym in fetched:
                continue
            try:
                k = get_fetcher().fetch_daily_kline(sym, days=5)
                if k is not None and len(k) > 0:
                    fetched[sym] = (float(k['close'].iloc[-1]), None)
            except Exception:
                pass

        for sym, val in fetched.items():
            _price_cache[sym] = val + ("",)
            _price_cache_ts[sym] = now
            out[sym] = (val[0], val[1])

    return out


def _lookup_price(symbol):
    """Single-symbol wrapper around batch lookup."""
    res = _lookup_prices([symbol])
    val = res.get(symbol, (None, None))
    return (val[0], val[1], "")


def _calc_return(pick_close, cur_price, cur_prev):
    """Return pct change from pick-day close to current price."""
    if not pick_close or pick_close <= 0 or not cur_price:
        return None
    return round((cur_price - pick_close) / pick_close * 100, 2)

# ===== API =====
@app.route("/")
def index():
    return send_from_directory(os.path.join(os.path.dirname(__file__), "templates"), "index.html")

@app.route("/api/state")
def api_state():
    """Lightweight polling: returns status of backend refresh and last update time."""
    rd = _cache.get("recommend", {})
    return jsonify({
        "running": _cache["selection_running"],
        "updated_at": rd.get("updated_at", ""),
        "stock_count": len(rd.get("stocks", [])),
        "market_updated_at": _cache.get("market", {}).get("updated_at", ""),
    })

@app.route("/api/recommend")
def api_recommend():
    rd = _cache.get("recommend", {})
    return jsonify({
        "stocks": rd.get("stocks", []),
        "updated_at": rd.get("updated_at", ""),
    })

@app.route("/api/market")
def api_market():
    md = _cache.get("market", {})
    return jsonify({
        "indexes": md.get("indexes", []),
        "up_count": md.get("up_count", 0),
        "down_count": md.get("down_count", 0),
        "flat_count": md.get("flat_count", 0),
        "volume_top10": md.get("volume_top10", []),
        "updated_at": md.get("updated_at", ""),
    })

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    if _cache["selection_running"]:
        return jsonify({"status": "already_running"})
    threading.Thread(target=_run_selection, daemon=True).start()
    return jsonify({"status": "started"})

@app.route("/api/picks")
def api_picks():
    """History picks from MySQL, with return since pick date."""
    try:
        from db import query_picks, list_dates
    except Exception as e:
        return jsonify({"stocks": [], "dates": [], "error": f"db: {e}"})

    pick_date = request.args.get("date", "").strip()
    board = request.args.get("board", "").strip()
    symbol = request.args.get("symbol", "").strip()

    try:
        rows = query_picks(pick_date=pick_date or None,
                           board=board or None,
                           symbol=symbol or None)
    except Exception as e:
        return jsonify({"stocks": [], "dates": [], "error": f"query: {e}"})

    # Batch price lookup for unique symbols (single TDX quotes call)
    symbols = sorted({str(r["symbol"]).zfill(6) for r in rows})
    prices = _lookup_prices(symbols)

    stocks = []
    for r in rows:
        sym = str(r["symbol"]).zfill(6)
        pc = float(r["pick_close"]) if r.get("pick_close") else None
        price, prev = prices.get(sym, (None, None))
        stocks.append({
            "pick_date": str(r["pick_date"]),
            "symbol": sym,
            "name": r.get("name") or "",
            "board_name": r.get("board_name") or "",
            "total_score": round(float(r.get("total_score", 0)), 1),
            "tech_score": round(float(r.get("tech_score", 0)), 0),
            "money_score": round(float(r.get("money_score", 0)), 0),
            "concept_score": round(float(r.get("concept_score", 0)), 0),
            "hot_concepts": (r.get("hot_concepts") or "").split("|"),
            "pick_close": pc,
            "main_inflow_wan": (round(float(r["main_inflow_wan"]), 0)
                                if r.get("main_inflow_wan") is not None else None),
            "large_inflow_wan": (round(float(r["large_inflow_wan"]), 0)
                                 if r.get("large_inflow_wan") is not None else None),
            "cur_price": round(price, 2) if price else None,
            "day_change_pct": (round((price - prev) / prev * 100, 2)
                               if price and prev else None),
            "return_pct": _calc_return(pc, price, prev),
            "detail": r.get("detail") or "",
        })

    try:
        dates = list_dates(30)
    except Exception:
        dates = []

    return jsonify({"stocks": stocks, "dates": dates})


# ===== Core selection =====
def _run_selection():
    with _lock:
        if _cache["selection_running"]: return
        _cache["selection_running"] = True
    try:
        sel = get_sel()
        logger.info("Running selection...")
        results = sel.run(quick=True)
        _cache_recommend(results)
        _cache_market_snapshot(sel)
    except Exception as e:
        logger.error(f"Selection failed: {e}", exc_info=True)
    finally:
        with _lock: _cache["selection_running"] = False

def _cache_recommend(results):
    stocks = []
    for i, r in enumerate(results):
        raw = r.get("raw_indicators", {})
        tr = raw.get("tech", {}).get("indicators", {})
        cr = raw.get("concept", {})
        stocks.append({
            "rank": i+1, "symbol": r["symbol"],
            "name": r.get("name") or r["symbol"],
            "total_score": r["total_score"],
            "tech_score": r["tech_score"],
            "money_score": r["money_score"],
            "concept_score": r["concept_score"],
            "hot_concepts": cr.get("hot_concepts", []),
            "detail_items": r.get("detail_items", []),
            "indicators": {"signal_summary": _summarize_signals(tr)},
        })
    _cache["recommend"] = {"stocks": stocks, "updated_at": datetime.now().strftime("%H:%M:%S")}
    logger.info(f"Cached {len(stocks)} recommendations")

def _cache_market_snapshot(sel):
    try:
        fetcher = sel.fetcher
        client = fetcher._get_quotes_client()

        # Index quotes
        idx = client.quotes(symbol=["999999", "399001", "399006"])
        indexes = []
        nm = {"999999": "上证指数", "399001": "深证成指", "399006": "创业板指"}
        if idx is not None:
            for _, r in idx.iterrows():
                c = str(r["code"]); p = float(r.get("price",0) or 0)
                prev = float(r.get("last_close",0) or 0)
                chg = (p-prev)/prev*100 if prev>0 else 0
                indexes.append({"name": nm.get(c,c), "price": round(p,2), "change_pct": round(chg,2)})

        # Sample 200 stocks for up/down
        sl = fetcher.get_stock_code_list()
        ups = downs = flats = 0
        vol10 = []
        if len(sl) > 0:
            qs = client.quotes(symbol=sl['code'].head(200).tolist())
            if qs is not None and len(qs) > 0:
                for _, r in qs.iterrows():
                    p = float(r.get("price",0) or 0)
                    pc = float(r.get("last_close",0) or 0)
                    v = int(float(r.get("vol",0) or 0))
                    if p <= 0 or v <= 0: continue
                    c = (p-pc)/pc*100 if pc>0 else 0
                    if c > 0: ups += 1
                    elif c < 0: downs += 1
                    else: flats += 1

                # Volume Top10
                vdf = qs.copy()
                vdf['price'] = pd.to_numeric(vdf['price'], errors='coerce')
                vdf['last_close'] = pd.to_numeric(vdf['last_close'], errors='coerce')
                vdf['vol'] = pd.to_numeric(vdf['vol'], errors='coerce')
                vdf['chg'] = np.where(vdf['last_close']>0,
                    (vdf['price']-vdf['last_close'])/vdf['last_close']*100, 0)
                top = vdf.nlargest(10, 'vol')
                for _, r in top.iterrows():
                    vol10.append({
                        "code": str(r["code"]), "name": "",
                        "price": round(float(r['price']),2),
                        "change_pct": round(float(r['chg']),2),
                        "volume": int(float(r['vol']))
                    })

        _cache["market"] = {
            "indexes": indexes, "up_count": ups, "down_count": downs,
            "flat_count": flats, "volume_top10": vol10,
            "updated_at": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as e:
        logger.warning(f"Market snapshot failed: {e}")

# ===== Startup =====
def start_server(host="0.0.0.0", port=5000, debug=False):
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"\n{'='*60}\n  A股尾盘选股工具\n  http://localhost:{port}\n{'='*60}\n")
    # BAOFU_NO_AUTORUN=1 skips the startup selection (used for UI dev/testing)
    if not os.environ.get("BAOFU_NO_AUTORUN"):
        threading.Thread(target=_run_selection, daemon=True).start()
    app.run(host=host, port=port, debug=debug, threaded=True)
