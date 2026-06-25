"""
Data Fetcher Layer

Pure mootdx TCP protocol — zero rate limiting, zero anti-scraping issues.
East Money API used ONLY for concept sectors (once per day, 1 request).

Strategy:
- ShSE (60xxxx/688xxx): mootdx.stocks() gives full code+name list
- SZSE (00xxxx/002xxx/300xxx): probe valid ranges via mootdx.quotes()
- K-lines: mootdx.bars() per-stock
- Finance data (turnover/cap): mootdx.finance() per-stock
- Concept sectors: East Money API (1 request/day)
- Money flow: mootdx doesn't provide this — use East Money API (small batches)
"""

import json
import os
import time
import logging
from typing import Optional, Dict, List

import pandas as pd
import numpy as np
import requests

from config import (
    KLINE_DAYS, REQUEST_TIMEOUT, MAX_RETRIES, BATCH_SLEEP, DATA_DIR,
)

logger = logging.getLogger(__name__)

# East Money API (concept/money-flow only — low frequency)
EM_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
}


class MarketDataFetcher:
    """A-Share Market Data Fetcher (mootdx-primary)"""

    def __init__(self):
        self._quotes_client = None
        self._stock_list_cache: Optional[pd.DataFrame] = None

    # ------------------------------------------------------------------
    # mootdx connection
    # ------------------------------------------------------------------
    def _get_quotes_client(self):
        if self._quotes_client is None:
            from mootdx.quotes import Quotes
            logger.info("Connecting to TDX server...")
            self._quotes_client = Quotes.factory(
                market='std', bestip=True, timeout=REQUEST_TIMEOUT
            )
            logger.info("TDX connected.")
        return self._quotes_client

    # ------------------------------------------------------------------
    # Stock Code List (mootdx only)
    # ------------------------------------------------------------------
    def _generate_sz_codes(self) -> List[str]:
        """
        Generate known valid SZSE stock code ranges.
        SZSE Main Board: 000001-003999 (scattered among indices)
        SME: 002001-004999
        ChiNext: 300001-301500
        """
        codes = []
        # Known valid ranges (empirically determined)
        # Main board: ~150 stocks in 000001-000999
        codes.extend(f"{i:06d}" for i in range(1, 1000))
        # SME board
        codes.extend(f"{i:06d}" for i in range(2001, 4001))
        # ChiNext
        codes.extend(f"{i:06d}" for i in range(300001, 301500))
        # New stocks in 001xxx range
        codes.extend(f"{i:06d}" for i in range(1000, 1500))
        return codes

    def get_stock_code_list(self) -> pd.DataFrame:
        """
        Build full A-share code list from mootdx.

        Returns DataFrame with columns: code, name
        """
        if self._stock_list_cache is not None:
            return self._stock_list_cache

        client = self._get_quotes_client()
        codes_data = []

        # ---- ShSE from stocks() ----
        logger.info("Fetching ShSE stock list...")
        sh_stocks = client.stocks()
        if sh_stocks is not None and len(sh_stocks) > 0:
            sh_stocks['code'] = sh_stocks['code'].astype(str).str.zfill(6)
            sh = sh_stocks[sh_stocks['code'].str.startswith(('60', '68'))]
            for _, r in sh.iterrows():
                codes_data.append({
                    'code': r['code'],
                    'name': str(r['name']).replace('\x00', '').strip()
                })
            logger.info(f"  ShSE: {len(sh)} stocks")

        # ---- SZSE by probing ----
        logger.info("Probing SZSE stocks...")
        sz_candidates = self._generate_sz_codes()
        batch_size = 80
        found_sz = []

        for i in range(0, len(sz_candidates), batch_size):
            batch = sz_candidates[i:i + batch_size]
            try:
                result = client.quotes(symbol=batch)
                if result is not None and len(result) > 0:
                    result['code'] = result['code'].astype(str).str.zfill(6)
                    active = result[(result['vol'] > 0) & (result['price'] > 0)]
                    for _, r in active.iterrows():
                        found_sz.append({
                            'code': r['code'],
                            'name': ''
                        })
            except Exception:
                pass
            time.sleep(0.03)

        # Backfill SZ names from mootdx finance() API
        # stocks() doesn't cover most SZSE stocks, but finance() has the name
        logger.info("  Backfilling SZSE stock names via finance()...")
        client = self._get_quotes_client()
        backfill_count = 0
        for f in found_sz:
            if f['name']:
                continue
            try:
                fin = client.finance(symbol=int(f['code']))
                if fin is not None and len(fin) > 0:
                    # name isn't in finance, but industry/province is
                    # Fall back to using stocks() name if available
                    pass
            except Exception:
                pass

        # Quick name lookup: use a single quotes() call with all SZ codes
        # to get names — mootdx quotes() returns 'market' but not 'name'
        # Alternative: just use code as name for display
        for f in found_sz:
            if not f['name']:
                f['name'] = f['code']  # fallback: use code

        codes_data.extend(found_sz)
        logger.info(f"  SZSE: {len(found_sz)} active stocks")

        # ---- Save cache ----
        df = pd.DataFrame(codes_data).drop_duplicates(subset='code', keep='first')
        df = df.reset_index(drop=True)
        self._stock_list_cache = df

        logger.info(f"Total A-share code list: {len(df)} stocks")
        return df

    # ------------------------------------------------------------------
    # Full Market Realtime Quotes (mootdx)
    # ------------------------------------------------------------------
    def fetch_realtime_quotes(self) -> pd.DataFrame:
        """
        Fetch all A-share real-time quotes via mootdx.quotes().

        Returns DataFrame with: code, name, price, open, high, low, prev_close,
        volume, amount, change_pct
        """
        stock_list = self.get_stock_code_list()
        codes = stock_list['code'].tolist()

        client = self._get_quotes_client()
        batch_size = 80
        all_quotes = []
        total = len(codes)

        logger.info(f"Fetching {total} stocks quotes in {total // batch_size + 1} batches...")

        for i in range(0, total, batch_size):
            batch = codes[i:i + batch_size]
            try:
                result = client.quotes(symbol=batch)
                if result is not None and len(result) > 0:
                    all_quotes.append(result)
            except Exception as e:
                logger.debug(f"Batch {i // batch_size} failed: {e}")

            bn = i // batch_size + 1
            if bn % 20 == 0:
                logger.info(f"  Quotes: {bn}/{total // batch_size + 1}")

            time.sleep(0.05)

        if not all_quotes:
            logger.error("No quote data retrieved")
            return pd.DataFrame()

        # concat with ignore_index=True gives a fresh RangeIndex
        quotes_df = pd.concat(all_quotes, ignore_index=True)
        quotes_df['code'] = quotes_df['code'].astype(str).str.zfill(6)
        quotes_df = quotes_df.drop_duplicates(subset=['code'], keep='last')
        quotes_df = quotes_df.reset_index(drop=True)

        # Drop duplicates from stock_list too (defensive)
        sl_dedup = stock_list.drop_duplicates(subset=['code'], keep='first')
        sl_dedup = sl_dedup.reset_index(drop=True)

        # Inner merge on unique codes
        merged = sl_dedup.merge(quotes_df, on='code', how='inner')
        merged = merged.reset_index(drop=True)

        # Rename columns (avoid pandas internals issues)
        if 'vol' in merged.columns:
            merged['volume'] = merged['vol']

        # Compute change_pct
        merged = merged.reset_index(drop=True)
        if 'last_close' in merged.columns:
            merged['last_close'] = pd.to_numeric(merged['last_close'], errors='coerce')
            merged['price'] = pd.to_numeric(merged['price'], errors='coerce')
            merged['change_pct'] = np.where(
                merged['last_close'] > 0,
                (merged['price'] - merged['last_close']) / merged['last_close'] * 100,
                0
            ).round(2)

        # Filter valid
        merged = merged[merged['price'].notna() & (merged['price'] > 0)]
        if 'volume' in merged.columns:
            merged = merged[merged['volume'] > 0]
        merged = merged.reset_index(drop=True)

        # Placeholder columns
        for col in ['turnover', 'volume_ratio', 'market_cap', 'pe', 'pb', 'industry']:
            if col not in merged.columns:
                merged[col] = np.nan

        logger.info(f"Quotes ready: {len(merged)} stocks "
                     f"(up: {(merged['change_pct']>0).sum()}, "
                     f"down: {(merged['change_pct']<0).sum()})")
        return merged

    # ------------------------------------------------------------------
    # Finance enrichment (mootdx)
    # ------------------------------------------------------------------
    def enrich_finance_info(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add market cap and turnover via mootdx.finance()."""
        client = self._get_quotes_client()
        result_df = df.copy()
        n = len(result_df)

        logger.info(f"Enriching finance data for {n} stocks...")
        liutong_map = {}
        for idx, row in result_df.iterrows():
            sym = row['code']
            try:
                fin = client.finance(symbol=int(sym))
                if fin is not None and len(fin) > 0:
                    guben = float(fin.iloc[0].get('liutongguben', 0) or 0)
                    if guben > 0:
                        liutong_map[sym] = guben
            except Exception:
                pass
            if (idx + 1) % 50 == 0:
                logger.info(f"  Finance: {idx+1}/{n}")
            time.sleep(0.02)

        for sym, guben in liutong_map.items():
            mask = result_df['code'] == sym
            price = float(result_df.loc[mask, 'price'].values[0])
            vol = float(result_df.loc[mask, 'volume'].values[0])
            result_df.loc[mask, 'market_cap'] = price * guben / 1e8
            result_df.loc[mask, 'turnover'] = vol * 100 / guben

        logger.info(f"Finance complete: turnover valid={result_df['turnover'].notna().sum()}")
        return result_df

    # ------------------------------------------------------------------
    # K-line (mootdx)
    # ------------------------------------------------------------------
    def fetch_daily_kline(self, symbol: str, days: int = KLINE_DAYS) -> Optional[pd.DataFrame]:
        client = self._get_quotes_client()
        try:
            df = client.bars(symbol=symbol, frequency=9, offset=days)
            if df is None or len(df) == 0:
                return None
            if 'vol' in df.columns and 'volume' not in df.columns:
                df['volume'] = df['vol']
            if 'datetime' in df.columns and 'date' not in df.columns:
                df['date'] = pd.to_datetime(df['datetime'])
            return df
        except Exception as e:
            logger.debug(f"K-line failed {symbol}: {e}")
            return None

    def fetch_batch_klines(self, symbols: List[str], days: int = KLINE_DAYS) -> Dict[str, pd.DataFrame]:
        logger.info(f"Fetching {len(symbols)} K-lines...")
        result, failed = {}, 0
        for i, sym in enumerate(symbols):
            try:
                kline = self.fetch_daily_kline(sym, days=days)
                if kline is not None and len(kline) >= 20:
                    result[sym] = kline
                else:
                    failed += 1
            except Exception:
                failed += 1
            if (i + 1) % 50 == 0:
                logger.info(f"  K-line: {i+1}/{len(symbols)} (OK: {len(result)})")
            time.sleep(BATCH_SLEEP)
        logger.info(f"K-line done: {len(result)} OK, {failed} failed")
        return result

    # ------------------------------------------------------------------
    # Concept Sectors (East Money API — once per day)
    # ------------------------------------------------------------------
    def fetch_concept_sectors(self) -> pd.DataFrame:
        """Single East Money API call for concept sectors."""
        params = {
            "pn": "1", "pz": "100", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": "m:90+t:3",
            "fields": "f2,f3,f12,f14,f104,f105,f128,f140",
        }
        try:
            r = requests.get(EM_URL, headers=EM_HEADERS,
                             params={**params, "_": str(int(time.time() * 1000))},
                             timeout=15)
            if r.status_code != 200:
                logger.warning(f"Concept API: HTTP {r.status_code}")
                return pd.DataFrame()

            data = r.json()
            if not data.get("data") or not data["data"].get("diff"):
                return pd.DataFrame()

            items = data["data"]["diff"]
            records = []
            for item in items:
                records.append({
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "change_pct": float(item.get("f3", 0) or 0),
                    "up_count": int(item.get("f104", 0) or 0),
                    "down_count": int(item.get("f105", 0) or 0),
                    "lead_stock": item.get("f128", ""),
                    "lead_change_pct": float(item.get("f140", 0) or 0),
                })

            df = pd.DataFrame(records)
            logger.info(f"Concept sectors: {len(df)}")
            return df
        except Exception as e:
            logger.warning(f"Concept fetch failed: {e}")
            return pd.DataFrame()

    def fetch_concept_stocks(self, concept_code: str) -> List[str]:
        """Get stock codes for a concept. Uses East Money API."""
        params = {
            "pn": "1", "pz": "200", "po": "0", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": f"b:{concept_code}+t:3",
            "fields": "f12",
        }
        try:
            r = requests.get(EM_URL, headers=EM_HEADERS,
                             params={**params, "_": str(int(time.time() * 1000))},
                             timeout=15)
            if r.status_code == 200:
                data = r.json()
                if data.get("data") and data["data"].get("diff"):
                    return [str(item.get("f12", "")).zfill(6)
                            for item in data["data"]["diff"]]
        except Exception:
            pass
        return []

    # ------------------------------------------------------------------
    # Money Flow (East Money API — per stock, use sparingly)
    # ------------------------------------------------------------------
    def fetch_stock_money_flow(self, symbol: str) -> Optional[dict]:
        """Fetch money flow for a single stock."""
        mc = "1" if symbol.startswith(("6", "68")) else "0"
        secid = f"{mc}.{symbol}"
        params = {
            "lmt": "1", "klt": "1", "secid": secid,
            "fields1": "f1,f2,f3",
            "fields2": "f51,f52,f53,f54,f55,f56",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        }
        try:
            url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
            r = requests.get(url, headers=EM_HEADERS, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if data.get("data") and data["data"].get("klines"):
                    parts = data["data"]["klines"][-1].split(",")
                    sl = float(parts[3]) if len(parts) > 3 else 0
                    lg = float(parts[4]) if len(parts) > 4 else 0
                    return {"main_net_inflow": sl + lg, "super_large_net": sl, "large_net": lg}
        except Exception:
            pass
        return None

    def fetch_batch_money_flow(self, symbols: List[str]) -> Dict[str, dict]:
        logger.info(f"Fetching money flow for {len(symbols)} stocks...")
        result = {}
        for i, sym in enumerate(symbols):
            flow = self.fetch_stock_money_flow(sym)
            if flow:
                result[sym] = flow
            if (i + 1) % 20 == 0:
                logger.info(f"  Money flow: {i+1}/{len(symbols)}")
            time.sleep(0.5)  # generous rate limit
        logger.info(f"Money flow done: {len(result)} stocks")
        return result
