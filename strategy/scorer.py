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
        Money flow scoring. If no data available, returns neutral/default score.
        Avoids excessive API calls — only fetches when needed.
        """
        from indicators.money_flow import calc_money_flow_score

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
    def rank_stocks(
        self,
        filtered_df: pd.DataFrame,
        klines: Dict[str, pd.DataFrame],
    ) -> List[Dict]:
        results = []
        total = len(filtered_df)
        logger.info(f"====== Multi-Factor Scoring ({total} stocks) ======")

        for idx, (_, row) in enumerate(filtered_df.iterrows()):
            symbol = str(row.get('code', '')).strip()
            kline = klines.get(symbol)

            if kline is None:
                continue

            result = self.compute_total_score(symbol, kline, row)
            results.append(result)

            if (idx + 1) % 50 == 0:
                logger.info(f"  Scoring: {idx+1}/{total}")

        results.sort(key=lambda x: x["total_score"], reverse=True)

        logger.info(f"====== Scoring Complete — Top 5 ======")
        for i, r in enumerate(results[:5]):
            logger.info(f"  {i+1}. {r['symbol']} {r['name']} Score: {r['total_score']:.1f} "
                        f"(T:{r['tech_score']:.0f} M:{r['money_score']:.0f} C:{r['concept_score']:.0f})")

        return results
