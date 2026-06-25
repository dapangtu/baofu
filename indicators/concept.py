"""
Concept Hotness Analyzer

Uses the industry field (f100) from East Money market quotes
and the hot concept sector list to score stocks.
No per-stock API calls needed — uses data already fetched.
"""

import logging
import json
import os
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import pandas as pd

from config import CONCEPT_PARAMS, DATA_DIR

logger = logging.getLogger(__name__)


class ConceptAnalyzer:
    """Concept hotness analyzer — industry-based, zero extra API calls."""

    def __init__(self, fetcher=None):
        self._fetcher = fetcher
        self._concept_sectors: Optional[pd.DataFrame] = None
        self._hot_concepts: Optional[pd.DataFrame] = None
        self._concept_industry_map: Dict[str, List[str]] = {}
        self._history_file = os.path.join(DATA_DIR, "concept_history.json")

    # ------------------------------------------------------------------
    # Hot Concepts
    # ------------------------------------------------------------------
    def get_hot_concepts(self, top_n: int = None) -> pd.DataFrame:
        """Fetch today's hot concept sectors from East Money API."""
        if top_n is None:
            top_n = CONCEPT_PARAMS["hot_top_n"]

        if self._fetcher is None:
            logger.warning("No fetcher available for concept data")
            return pd.DataFrame()

        df = self._fetcher.fetch_concept_sectors()
        if df is None or len(df) == 0:
            logger.warning("Failed to fetch concept sectors")
            return pd.DataFrame()

        self._concept_sectors = df

        # Standardize columns
        col_map = {
            '板块名称': 'name', '板块代码': 'code', '涨跌幅': 'change_pct',
            '领涨股票': 'lead_stock', '领涨股票-涨跌幅': 'lead_change_pct',
            '上涨家数': 'up_count', '下跌家数': 'down_count',
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        # Ensure numeric
        for col in ['change_pct', 'up_count', 'down_count']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # Compute up ratio
        if 'up_count' in df.columns and 'down_count' in df.columns:
            df['up_ratio'] = df['up_count'] / (df['up_count'] + df['down_count'] + 0.01)
        else:
            df['up_ratio'] = 0.5

        # Hotness score: change_pct(40%) + up_ratio(30%) + lead_stock_change(30%)
        lead_col = 'lead_change_pct' if 'lead_change_pct' in df.columns else 'change_pct'
        df['hot_score'] = (
            df['change_pct'].rank(pct=True, ascending=True) * 0.4 +
            df['up_ratio'].rank(pct=True, ascending=True) * 0.3 +
            df.get(lead_col, df['change_pct']).rank(pct=True, ascending=True) * 0.3
        )

        df = df.sort_values('hot_score', ascending=False).reset_index(drop=True)
        df['rank'] = range(1, len(df) + 1)

        self._hot_concepts = df
        self._save_history(df.head(top_n))

        name_col = 'name' if 'name' in df.columns else '板块名称'
        logger.info(f"Hot Concepts Top {min(top_n, len(df))}:")
        for _, row in df.head(top_n).iterrows():
            logger.info(f"  {row.get('rank','')}. {row.get(name_col,'?')} "
                        f"(chg: {row.get('change_pct',0):.2f}%, score: {row.get('hot_score',0):.3f})")

        return df.head(top_n)

    # ------------------------------------------------------------------
    # Stock Concept Score (industry-based, NO per-stock API call)
    # ------------------------------------------------------------------
    def calc_concept_score(self, symbol: str, industry: str = "") -> Dict:
        """
        Score a stock's concept hotness using its industry field.

        The East Money quotes already include f100=industry.
        We match stock industry keywords against hot concept names.
        """
        if self._hot_concepts is None or len(self._hot_concepts) == 0:
            return {"score": 0.3, "hot_concepts": [], "detail": "Concept data unavailable"}

        name_col = 'name' if 'name' in self._hot_concepts.columns else '板块名称'

        if not industry:
            return {"score": 0.15, "hot_concepts": [], "detail": "No industry data"}

        # Match industry against concept names (keyword matching)
        industries = [i.strip() for i in industry.replace(';', ',').split(',') if i.strip()]
        hot_concepts = self._hot_concepts.copy()

        matched = []
        level_scores = {}  # concept_name -> (rank, score)

        for _, concept_row in hot_concepts.iterrows():
            cname = str(concept_row.get(name_col, ''))
            crank = int(concept_row.get('rank', 999))
            if not cname:
                continue

            # Check keyword overlap
            for ind in industries:
                # Match: concept name contains industry keyword, or industry contains concept keyword
                if len(ind) >= 2 and (ind in cname or cname in ind):
                    matched.append(cname)
                    # Score based on rank: rank 1 -> 0.6, rank 20 -> 0.1
                    concept_score = max(0.05, 0.6 - (crank - 1) * 0.03)
                    if cname not in level_scores or concept_score > level_scores[cname]:
                        level_scores[cname] = concept_score
                    break

        if matched:
            # Take top 3 concept scores
            top_scores = sorted(level_scores.values(), reverse=True)[:3]
            score = min(1.0, sum(top_scores[:2]) * 0.5 + top_scores[0] * 0.5)
            score = max(0.2, score)
            detail = f"Matched hot concepts: {', '.join(matched[:5])}"
        else:
            score = 0.15
            detail = f"Industries [{', '.join(industries[:3])}] unmatched"

        return {"score": round(score, 4), "hot_concepts": matched, "detail": detail}

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def _save_history(self, hot_df: pd.DataFrame):
        today = datetime.now().strftime("%Y-%m-%d")
        history = self._load_history()
        name_col = 'name' if 'name' in hot_df.columns else '板块名称'

        entry = {"date": today, "concepts": []}
        for _, row in hot_df.iterrows():
            entry["concepts"].append({
                "name": str(row.get(name_col, '')),
                "rank": int(row.get('rank', 99)),
                "change_pct": float(row.get('change_pct', 0)),
            })

        if history and history[-1].get("date") == today:
            history[-1] = entry
        else:
            history.append(entry)
        history = history[-10:]

        try:
            os.makedirs(os.path.dirname(self._history_file), exist_ok=True)
            with open(self._history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_history(self) -> list:
        if not os.path.exists(self._history_file):
            return []
        try:
            with open(self._history_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return []
