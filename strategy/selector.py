"""
Stock Selection Orchestrator — Main Controller

Flow:
1. Fetch all A-share real-time quotes
2. Hard filter (ST/suspended/price-change/turnover/cap)
3. Enrich finance data (market cap + turnover) + secondary filter
4. Fetch today's hot concept sectors
5. Batch fetch candidate K-lines
6. Multi-factor scoring -> Top 5
7. Output results
"""

import logging
import time
from typing import List, Dict

import pandas as pd

from config import KLINE_DAYS, KLINE_DAYS_QUICK

logger = logging.getLogger(__name__)


class StockSelector:
    """Afternoon Stock Selector — Main Controller"""

    def __init__(self):
        self.fetcher = None
        self.concept_analyzer = None
        self.scorer = None
        self._initialized = False

    def _init_modules(self):
        if self._initialized:
            return
        from data.fetcher import MarketDataFetcher
        from indicators.concept import ConceptAnalyzer
        from strategy.scorer import MultiFactorScorer

        self.fetcher = MarketDataFetcher()
        self.concept_analyzer = ConceptAnalyzer(fetcher=self.fetcher)
        self.scorer = MultiFactorScorer(
            fetcher=self.fetcher,
            concept_analyzer=self.concept_analyzer,
        )
        self._initialized = True

    # ------------------------------------------------------------------
    # Main Flow
    # ------------------------------------------------------------------
    def run(self, quick: bool = False) -> List[Dict]:
        self._init_modules()
        kline_days = KLINE_DAYS_QUICK if quick else KLINE_DAYS
        start_time = time.time()
        mode_str = "Quick" if quick else "Standard"

        logger.info("=" * 50)
        logger.info(f"  A-Share Stock Selector — {mode_str} Mode")
        logger.info("=" * 50)

        # ---- Step 1: Fetch all A-share quotes ----
        logger.info("[1/7] Fetching all A-share real-time quotes...")
        quotes_df = self.fetcher.fetch_realtime_quotes()
        if quotes_df is None or len(quotes_df) == 0:
            logger.error("Failed to fetch quotes, exiting.")
            return []
        logger.info(f"  Total market: {len(quotes_df)} stocks")

        # ---- Step 2: Hard filter ----
        logger.info("[2/7] Applying hard filters...")
        from strategy.filter import apply_all_filters
        filtered_df = apply_all_filters(quotes_df)
        if len(filtered_df) == 0:
            logger.warning("No candidates after filtering. Try relaxing conditions.")
            return []
        logger.info(f"  After filtering: {len(filtered_df)} stocks")

        # ---- Step 3: Enrich finance data & secondary filter ----
        if len(filtered_df) <= 500:
            logger.info("[3/7] Enriching finance data (market cap + turnover)...")
            enriched_df = self.fetcher.enrich_finance_info(filtered_df)

            # Secondary filter with now-available turnover & market_cap
            if 'turnover' in enriched_df.columns and enriched_df['turnover'].notna().sum() > 10:
                enriched_df = enriched_df[
                    (enriched_df['turnover'].isna()) |
                    ((enriched_df['turnover'] >= 2.0) & (enriched_df['turnover'] <= 15.0))
                ]
            if 'market_cap' in enriched_df.columns and enriched_df['market_cap'].notna().sum() > 10:
                enriched_df = enriched_df[
                    (enriched_df['market_cap'].isna()) |
                    ((enriched_df['market_cap'] >= 20) & (enriched_df['market_cap'] <= 1000))
                ]
            logger.info(f"  After enrichment+filter: {len(enriched_df)} stocks")
            filtered_df = enriched_df.reset_index(drop=True)
        else:
            logger.info(f"[3/7] Many candidates ({len(filtered_df)}), skipping enrichment")

        # ---- Step 4: Fetch hot concepts ----
        logger.info("[4/7] Fetching today's hot concept sectors...")
        concept_top_n = 10 if quick else 20
        self.concept_analyzer.get_hot_concepts(top_n=concept_top_n)

        # ---- Step 5: Batch fetch K-lines ----
        logger.info("[5/7] Batch fetching candidate K-lines...")
        candidates = filtered_df['code'].astype(str).str.zfill(6).tolist()
        klines = self.fetcher.fetch_batch_klines(candidates, days=kline_days)
        logger.info(f"  K-lines ready: {len(klines)} stocks")

        # ---- Step 6: Multi-factor scoring ----
        logger.info("[6/7] Multi-factor scoring...")
        ranked = self.scorer.rank_stocks(filtered_df, klines)

        # ---- Step 7: Output ----
        logger.info("[7/7] Generating report...")
        top_n = min(5, len(ranked))

        # Concept diversity: avoid Top 5 all from same concept
        top_stocks = self._ensure_concept_diversity(ranked[:20], top_n)

        from output.reporter import Reporter
        reporter = Reporter()
        reporter.print_formatted(top_stocks)
        reporter.save_to_csv(top_stocks)

        elapsed = time.time() - start_time
        logger.info(f"\nSelection complete. Time elapsed: {elapsed:.1f}s")
        return top_stocks

    def run_quick(self) -> List[Dict]:
        """Quick mode shortcut (fewer K-line days, fewer concepts)."""
        return self.run(quick=True)

    # ------------------------------------------------------------------
    # Concept Diversity
    # ------------------------------------------------------------------
    def _ensure_concept_diversity(self, ranked: List[Dict], top_n: int = 5) -> List[Dict]:
        """
        Ensure Top N stocks cover diverse concepts.
        All candidates are from the top-ranked pool, no cold stocks.
        """
        if len(ranked) <= top_n:
            return ranked

        selected = []
        used_concept_combos = set()

        for stock in ranked:
            if len(selected) >= top_n:
                break

            raw = stock.get("raw_indicators", {})
            concept_raw = raw.get("concept", {})
            hot_concepts = tuple(concept_raw.get("hot_concepts", []))

            if hot_concepts and hot_concepts in used_concept_combos:
                continue

            selected.append(stock)
            if hot_concepts:
                used_concept_combos.add(hot_concepts)

        # Fill remaining slots from rank order
        if len(selected) < top_n:
            for stock in ranked:
                if stock not in selected and len(selected) < top_n:
                    selected.append(stock)

        return selected[:top_n]
