"""
Hard Filter Module

Applies basic and parameter-based filters to narrow down candidates.
"""

import logging
from typing import Dict

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def filter_basic(quotes_df: pd.DataFrame) -> pd.DataFrame:
    """Exclude ST/delisted stocks, suspended stocks, abnormal prices."""
    if quotes_df is None or len(quotes_df) == 0:
        return pd.DataFrame()

    df = quotes_df.copy()
    initial_count = len(df)

    # 1. Exclude ST / *ST / delisted
    if 'name' in df.columns:
        st_mask = df['name'].apply(_is_st_stock)
        df = df[~st_mask]
        if st_mask.sum() > 0:
            logger.info(f"  Excluded ST/delisted: {st_mask.sum()}")

    # 2. Exclude suspended (volume=0 or price=0/NaN)
    suspended = pd.Series(False, index=df.index)
    for col in ['volume', 'price']:
        if col in df.columns:
            suspended = suspended | df[col].isna() | (df[col] == 0)
    df = df[~suspended]
    if suspended.sum() > 0:
        logger.info(f"  Excluded suspended: {suspended.sum()}")

    # 3. Exclude abnormal prices (< 0.5 CNY likely problematic)
    if 'price' in df.columns:
        df = df[df['price'] >= 0.5]

    logger.info(f"Basic filter: {initial_count} -> {len(df)} stocks")
    return df


def filter_by_params(quotes_df: pd.DataFrame, params: Dict) -> pd.DataFrame:
    """Filter by parameter thresholds. Skip columns that are all NaN."""
    df = quotes_df.copy()
    before = len(df)

    # --- Change % ---
    if 'change_pct' in df.columns and df['change_pct'].notna().any():
        mask = (
            (df['change_pct'] >= params.get('min_change_pct', 1.0)) &
            (df['change_pct'] <= params.get('max_change_pct', 9.0))
        )
        df = df[mask]
        logger.info(f"  Change% [{params['min_change_pct']}%~{params['max_change_pct']}%]: {len(df)} left")

    # --- Turnover (only if data exists) ---
    if 'turnover' in df.columns and df['turnover'].notna().sum() > 10:
        mask = (
            (df['turnover'] >= params.get('min_turnover', 2.0)) |
            df['turnover'].isna()
        ) & (
            (df['turnover'] <= params.get('max_turnover', 15.0)) |
            df['turnover'].isna()
        )
        df = df[mask]
        logger.info(f"  Turnover [{params['min_turnover']}%~{params['max_turnover']}%]: {len(df)} left")

    # --- Market cap (only if data exists) ---
    if 'market_cap' in df.columns and df['market_cap'].notna().sum() > 10:
        min_cap = params.get('min_market_cap', 20)
        max_cap = params.get('max_market_cap', 1000)
        mask = (
            ((df['market_cap'] >= min_cap) & (df['market_cap'] <= max_cap)) |
            df['market_cap'].isna()
        )
        df = df[mask]
        logger.info(f"  MarketCap [{min_cap}B~{max_cap}B]: {len(df)} left")

    # --- Volume ratio (only if data exists) ---
    if 'volume_ratio' in df.columns and df['volume_ratio'].notna().sum() > 10:
        mask = (df['volume_ratio'] >= params.get('min_volume_ratio', 1.2)) | df['volume_ratio'].isna()
        df = df[mask]
        logger.info(f"  VolumeRatio [>= {params['min_volume_ratio']}]: {len(df)} left")

    logger.info(f"Param filter: {before} -> {len(df)} stocks")
    return df


def apply_all_filters(quotes_df: pd.DataFrame, params: Dict = None) -> pd.DataFrame:
    """Apply all filters in sequence."""
    from config import FILTER_PARAMS

    if params is None:
        params = FILTER_PARAMS

    total = len(quotes_df) if quotes_df is not None else 0
    logger.info(f"====== Hard Filter Start (market: {total} stocks) ======")

    df = filter_basic(quotes_df)
    if len(df) == 0:
        logger.warning("No stocks passed basic filter!")
        return pd.DataFrame()

    df = filter_by_params(df, params)
    if len(df) == 0:
        logger.warning("No stocks passed param filter! Try relaxing conditions.")
        return pd.DataFrame()

    logger.info(f"====== Hard Filter Done: {len(df)} candidates ======")
    return df.reset_index(drop=True)


def _is_st_stock(name: str) -> bool:
    """Check if stock name indicates ST/delisted status."""
    if pd.isna(name):
        return False
    name = str(name).upper().strip()
    return any(kw in name for kw in ['ST', '退', 'PT', 'N', 'C'])
