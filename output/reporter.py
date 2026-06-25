"""
Result Output Module (English text to avoid GBK encoding issues on Windows)
"""

import os
import csv
import logging
from datetime import datetime
from typing import List, Dict

from config import HISTORY_DIR

logger = logging.getLogger(__name__)


class Reporter:
    """Stock selection result reporter"""

    def __init__(self):
        os.makedirs(HISTORY_DIR, exist_ok=True)

    def print_formatted(self, ranked_stocks: List[Dict], top_n: int = 5):
        """Print boxed report to console (ASCII-safe)."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        width = 62

        print()
        print("=" * width)
        print(f"  A-Share Afternoon Stock Selector — {now}")
        print("=" * width)

        if not ranked_stocks:
            print("  (no stocks match filters)")
            print("=" * width)
            return

        for i, stock in enumerate(ranked_stocks[:top_n]):
            sym = stock.get("symbol", "?")
            name = stock.get("name") or sym
            score = stock.get("total_score", 0)

            print(f"  [{i+1}] {sym} {name}   Score: {score:.1f}")
            print(f"      Tech:{stock.get('tech_score',0):.0f}  "
                  f"Money:{stock.get('money_score',0):.0f}  "
                  f"Concept:{stock.get('concept_score',0):.0f}")
            for detail in stock.get("detail_items", [])[:5]:
                print(f"      {detail}")
            if i < min(top_n, len(ranked_stocks)) - 1:
                print("  " + "-" * 58)

        print("=" * width)
        print()

    def print_summary(self, ranked_stocks: List[Dict], top_n: int = 5):
        """Compact output (one line per stock)."""
        if not ranked_stocks:
            print("[no stocks]")
            return
        for i, stock in enumerate(ranked_stocks[:top_n]):
            print(f"{i+1}. {stock['symbol']} {stock.get('name','?'):<8s} "
                  f"Total:{stock['total_score']:.1f} "
                  f"(T:{stock['tech_score']:.0f} M:{stock['money_score']:.0f} "
                  f"C:{stock['concept_score']:.0f})")

    def save_to_csv(self, ranked_stocks: List[Dict]):
        """Append results to CSV file."""
        if not ranked_stocks:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        filepath = os.path.join(HISTORY_DIR, f"{today}.csv")
        file_exists = os.path.exists(filepath)

        rows = []
        for rank, stock in enumerate(ranked_stocks, 1):
            raw = stock.get("raw_indicators", {})
            concept_raw = raw.get("concept", {})
            money_raw = raw.get("money", {})

            rows.append({
                "rank": rank,
                "symbol": stock.get("symbol", ""),
                "name": stock.get("name", ""),
                "total_score": stock.get("total_score", 0),
                "tech_score": stock.get("tech_score", 0),
                "money_score": stock.get("money_score", 0),
                "concept_score": stock.get("concept_score", 0),
                "hot_concepts": "|".join(concept_raw.get("hot_concepts", [])),
                "main_inflow_wan": money_raw.get("main_net_inflow", 0),
                "detail": " | ".join(stock.get("detail_items", [])),
            })

        fieldnames = ["rank", "symbol", "name", "total_score", "tech_score",
                      "money_score", "concept_score", "hot_concepts",
                      "main_inflow_wan", "detail"]

        try:
            with open(filepath, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                writer.writerows(rows)
            logger.info(f"Results saved to: {filepath}")
        except Exception as e:
            logger.error(f"CSV save failed: {e}")
