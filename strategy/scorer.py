"""
Multi-Factor Scoring Model

Technical (50%) + Money Flow (25%) + Concept Hotness (25%)
"""

import logging
from typing import Dict, Optional, List

import pandas as pd

from config import SCORE_WEIGHTS

logger = logging.getLogger(__name__)


class MultiFactorScorer:
    """Multi-Factor Stock Scorer"""

    def __init__(self, fetcher, concept_analyzer):
        self.fetcher = fetcher
        self.concept = concept_analyzer
        self._money_flow_cache: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Technical Scoring
    # ------------------------------------------------------------------
    def score_technical(self, kline_df: pd.DataFrame) -> Dict:
        from indicators.technical import compute_all_indicators

        if kline_df is None or len(kline_df) < 20:
            return {"score": 0.15, "signal": 0, "detail_items": ["Insufficient K-line data (need >= 20)"]}

        result = compute_all_indicators(kline_df)
        indicators = result["indicators"]

        detail_items = []
        for name, ind in indicators.items():
            if name == "ma":
                d = ind["detail"]
                trend = "Bull" if ind['signal'] == 1 else "Bear" if ind['signal'] == -1 else "Neutral"
                detail_items.append(
                    f"MA: {d.get('ma5','?')}/{d.get('ma10','?')}/{d.get('ma20','?')} {trend}"
                )
            elif name == "macd":
                d = ind["detail"]
                status = "GoldenCross" if ind["signal"] == 1 else "DeadCross" if ind["signal"] == -1 else "Neutral"
                detail_items.append(f"MACD: DIF={d.get('dif',0):.3f}/DEA={d.get('dea',0):.3f} {status}")
            elif name == "kdj":
                d = ind["detail"]
                signal = "GoldenC" if ind["signal"] == 1 else "DeadC" if ind["signal"] == -1 else ""
                detail_items.append(f"KDJ: K={d.get('k','?')}/D={d.get('d','?')}/J={d.get('j','?')} {signal}")
            elif name == "boll":
                d = ind["detail"]
                detail_items.append(f"Boll: pos={d.get('position','?')} mid={d.get('mid','?')}")
            elif name == "rsi":
                d = ind["detail"]
                detail_items.append(f"RSI: {d.get('rsi','?')} ({d.get('trend','?')})")
            elif name == "volume_ratio":
                d = ind["detail"]
                detail_items.append(f"VolRatio: {d.get('volume_ratio','?')}")

        return {
            "score": result["combined_score"],
            "signal": 1 if result["combined_score"] > 0.5 else (0 if result["combined_score"] > 0.3 else -1),
            "detail_items": detail_items,
            "raw": result,
        }

    # ------------------------------------------------------------------
    # Money Flow Scoring
    # ------------------------------------------------------------------
    def score_money_flow(self, symbol: str) -> Dict:
        """
        Money flow scoring. Uses prefetched cache if available; otherwise
        fetches on demand (guarded by the fetcher's circuit breaker).
        """
        from indicators.money_flow import calc_money_flow_score

        if symbol in self._money_flow_cache:
            flow = self._money_flow_cache[symbol]
        else:
            flow = self.fetcher.fetch_stock_money_flow(symbol)
        result = calc_money_flow_score(flow)
        return {
            "score": result["score"],
            "signal": result["signal"],
            "detail_items": [result["detail"]],
            "raw": result,
        }

    # ------------------------------------------------------------------
    # Concept Scoring
    # ------------------------------------------------------------------
    def score_concept(self, symbol: str, industry: str = "") -> Dict:
        """
        Concept hotness scoring. Uses pre-fetched industry data,
        NO extra API calls per stock.
        """
        result = self.concept.calc_concept_score(symbol, industry=industry)
        return {
            "score": result["score"],
            "signal": 1 if result["score"] > 0.3 else 0,
            "detail_items": [result.get("detail", "No concept data")],
            "raw": result,
        }

    # ------------------------------------------------------------------
    # Total Score
    # ------------------------------------------------------------------
    def compute_total_score(
        self,
        symbol: str,
        kline_df: Optional[pd.DataFrame],
        quote_row: Optional[pd.Series] = None,
    ) -> Dict:
        tech = self.score_technical(kline_df)
        money = self.score_money_flow(symbol)

        # Use industry from quote data
        industry = ""
        if quote_row is not None:
            industry = str(quote_row.get('industry', ''))
        concept = self.score_concept(symbol, industry=industry)

        # Weighted total
        total_raw = (
            tech["score"] * SCORE_WEIGHTS["technical"] +
            money["score"] * SCORE_WEIGHTS["money_flow"] +
            concept["score"] * SCORE_WEIGHTS["concept"]
        )
        total_score = round(total_raw * 100, 2)

        name = ""
        if quote_row is not None and 'name' in quote_row.index:
            name = str(quote_row.get('name', ''))

        detail_items = []
        detail_items.extend(tech["detail_items"])
        detail_items.extend(money["detail_items"])
        detail_items.extend(concept["detail_items"])

        return {
            "symbol": symbol,
            "name": name,
            "total_score": total_score,
            "tech_score": round(tech["score"] * 100, 2),
            "money_score": round(money["score"] * 100, 2),
            "concept_score": round(concept["score"] * 100, 2),
            "detail_items": detail_items,
            "raw_indicators": {
                "tech": tech.get("raw", {}),
                "money": money.get("raw", {}),
                "concept": concept.get("raw", {}),
            },
        }

    # ------------------------------------------------------------------
    # Batch Ranking
    # ------------------------------------------------------------------
    def _prefetch_money_flow(self, symbols: List[str], concurrency: int = 6):
        """Concurrently prefetch money flow for candidate symbols into cache.

        Fast-fails when the fetcher's circuit breaker trips (host unreachable),
        so this adds near-zero cost when East Money is unavailable.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        to_fetch = [s for s in symbols if s not in self._money_flow_cache]
        if not to_fetch:
            return
        if not self.fetcher._em_flow_available():
            return  # breaker already open

        logger.info(f"Prefetching money flow for {len(to_fetch)} stocks...")
        done = 0
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = {ex.submit(self.fetcher.fetch_stock_money_flow, s): s
                       for s in to_fetch}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    flow = fut.result()
                    if flow:
                        self._money_flow_cache[sym] = flow
                except Exception:
                    pass
                done += 1
                if not self.fetcher._em_flow_available():
                    break  # breaker tripped; cancel remaining
        logger.info(f"Money flow prefetch done: {len(self._money_flow_cache)} cached")

    def rank_stocks(
        self,
        filtered_df: pd.DataFrame,
        klines: Dict[str, pd.DataFrame],
    ) -> List[Dict]:
        from config import LARGE_ORDER_MIN_NET

        results = []
        total = len(filtered_df)
        filtered_out = 0
        logger.info(f"====== Multi-Factor Scoring ({total} stocks) ======")

        # Prefetch money flow once (concurrent + circuit-breaker protected)
        candidates = [str(c) for c in filtered_df['code'] if klines.get(str(c)) is not None]
        self._prefetch_money_flow(candidates)

        for idx, (_, row) in enumerate(filtered_df.iterrows()):
            symbol = str(row.get('code', '')).strip()
            kline = klines.get(symbol)

            if kline is None:
                continue

            result = self.compute_total_score(symbol, kline, row)

            # ---- Hard filter: 大单净量 ----
            # Skip if large-order net inflow is below threshold (when data exists).
            # Missing data passes through to avoid over-filtering on API failures.
            money_raw = (result.get("raw_indicators") or {}).get("money", {}) or {}
            large_w = money_raw.get("large_net_inflow", None)
            if large_w is not None and large_w < LARGE_ORDER_MIN_NET:
                filtered_out += 1
                continue

            results.append(result)

            if (idx + 1) % 50 == 0:
                logger.info(f"  Scoring: {idx+1}/{total}")

        if filtered_out:
            logger.info(f"  Filtered by 大单净量 (<{LARGE_ORDER_MIN_NET}万): {filtered_out}")

        results.sort(key=lambda x: x["total_score"], reverse=True)

        logger.info(f"====== Scoring Complete — Top 5 ======")
        for i, r in enumerate(results[:5]):
            logger.info(f"  {i+1}. {r['symbol']} {r['name']} Score: {r['total_score']:.1f} "
                        f"(T:{r['tech_score']:.0f} M:{r['money_score']:.0f} C:{r['concept_score']:.0f})")

        return results
