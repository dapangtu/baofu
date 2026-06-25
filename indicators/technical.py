"""
技术指标计算模块

支持的指标: MA均线、MACD、KDJ、BOLL布林带、RSI、量比
每个指标函数返回统一的 dict 格式:
    {
        "signal": 1 | 0 | -1,   # 1=看多, 0=中性, -1=看空
        "score": 0.0 ~ 1.0,     # 标准化评分
        "detail": {...}          # 详细数值
    }
"""

import logging
from typing import Dict, Any

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def calc_ma(df: pd.DataFrame) -> Dict[str, Any]:
    """
    计算移动平均线及多头排列状态。

    判断逻辑:
    - MA5 > MA10 > MA20 → 多头排列，信号=1，分数=1.0
    - MA5 > MA10 但 MA10 < MA20 → 部分多头，信号=1，分数=0.6
    - MA5 < MA20 → 空头排列，信号=-1，分数=0.0
    - 其他 → 中性，信号=0，分数=0.3
    """
    close = df['close'].astype(float)
    ma5 = close.rolling(window=5).mean().iloc[-1]
    ma10 = close.rolling(window=10).mean().iloc[-1]
    ma20 = close.rolling(window=20).mean().iloc[-1]

    detail = {
        "ma5": round(float(ma5), 2) if not pd.isna(ma5) else None,
        "ma10": round(float(ma10), 2) if not pd.isna(ma10) else None,
        "ma20": round(float(ma20), 2) if not pd.isna(ma20) else None,
    }

    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
        return {"signal": 0, "score": 0.0, "detail": detail}

    if ma5 > ma10 > ma20:
        return {"signal": 1, "score": 1.0, "detail": detail}
    elif ma5 > ma10:  # MA5 > MA10 but MA10 < MA20
        return {"signal": 1, "score": 0.6, "detail": detail}
    elif ma5 < ma20:
        return {"signal": -1, "score": 0.0, "detail": detail}
    else:
        return {"signal": 0, "score": 0.3, "detail": detail}


def calc_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, Any]:
    """
    计算 MACD 指标。

    判断逻辑:
    - DIF > DEA 且 MACD柱 > 0 且柱在放大 → 金叉+红柱放大，信号=1，分数=1.0
    - DIF > DEA 且 MACD柱 > 0 但柱在缩小 → 红柱缩小中，信号=1，分数=0.6
    - DIF > DEA 但 MACD柱 < 0 → 零轴下金叉，信号=1，分数=0.7
    - DIF < DEA 且 MACD柱 < 0 → 死叉绿柱，信号=-1，分数=0.0
    - DIF < DEA 但 MACD柱 > 0 → 零轴上死叉，信号=0，分数=0.4
    """
    close = df['close'].astype(float)

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_bar = 2 * (dif - dea)  # MACD 柱

    cur_dif = dif.iloc[-1]
    cur_dea = dea.iloc[-1]
    cur_bar = macd_bar.iloc[-1]
    prev_bar = macd_bar.iloc[-2] if len(macd_bar) >= 2 else 0

    detail = {
        "dif": round(float(cur_dif), 4),
        "dea": round(float(cur_dea), 4),
        "macd_bar": round(float(cur_bar), 4),
    }

    if pd.isna(cur_dif) or pd.isna(cur_dea):
        return {"signal": 0, "score": 0.0, "detail": detail}

    if cur_dif > cur_dea and cur_bar > 0 and cur_bar > prev_bar:
        return {"signal": 1, "score": 1.0, "detail": detail}
    elif cur_dif > cur_dea and cur_bar > 0:
        return {"signal": 1, "score": 0.6, "detail": detail}
    elif cur_dif > cur_dea:  # 零轴下金叉
        return {"signal": 1, "score": 0.7, "detail": detail}
    elif cur_dif < cur_dea and cur_bar < 0:
        return {"signal": -1, "score": 0.0, "detail": detail}
    elif cur_dif < cur_dea:  # 零轴上死叉
        return {"signal": 0, "score": 0.4, "detail": detail}
    else:
        return {"signal": 0, "score": 0.3, "detail": detail}


def calc_kdj(df: pd.DataFrame, n: int = 9) -> Dict[str, Any]:
    """
    计算 KDJ 指标。

    判断逻辑:
    - K上穿D且K<60 → 低位金叉，信号=1，分数=1.0
    - K上穿D但K>=80 → 高位金叉（可能诱多），信号=0，分数=0.5
    - K<20 → 超卖区，信号=1，分数=0.7（等待反弹）
    - K>80 → 超买区，信号=-1，分数=0.2
    - K<D → 死叉中，信号=-1，分数=0.0
    """
    low_list = df['low'].astype(float).rolling(window=n, min_periods=1).min()
    high_list = df['high'].astype(float).rolling(window=n, min_periods=1).max()

    rsv = (df['close'].astype(float) - low_list) / (high_list - low_list + 1e-10) * 100

    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d

    cur_k = k.iloc[-1]
    cur_d = d.iloc[-1]
    cur_j = j.iloc[-1]
    prev_k = k.iloc[-2] if len(k) >= 2 else cur_k
    prev_d = d.iloc[-2] if len(d) >= 2 else cur_d

    detail = {
        "k": round(float(cur_k), 2),
        "d": round(float(cur_d), 2),
        "j": round(float(cur_j), 2),
    }

    if pd.isna(cur_k) or pd.isna(cur_d):
        return {"signal": 0, "score": 0.0, "detail": detail}

    # 金叉判断
    if prev_k <= prev_d and cur_k > cur_d:
        if cur_k < 60:
            return {"signal": 1, "score": 1.0, "detail": detail}  # 低位金叉
        elif cur_k < 80:
            return {"signal": 1, "score": 0.7, "detail": detail}  # 中位金叉
        else:
            return {"signal": 0, "score": 0.5, "detail": detail}  # 高位金叉

    # 死叉判断
    if prev_k >= prev_d and cur_k < cur_d:
        return {"signal": -1, "score": 0.0, "detail": detail}

    # 无交叉，按位置判断
    if cur_k < 20:
        return {"signal": 1, "score": 0.7, "detail": detail}  # 超卖
    elif cur_k > 80:
        return {"signal": -1, "score": 0.2, "detail": detail}  # 超买
    elif cur_k > cur_d:
        return {"signal": 0, "score": 0.5, "detail": detail}  # K在D上方
    else:
        return {"signal": -1, "score": 0.2, "detail": detail}


def calc_boll(df: pd.DataFrame, period: int = 20, std: int = 2) -> Dict[str, Any]:
    """
    计算布林带位置。

    判断逻辑:
    - 收盘价在中轨上方且中轨向上 → 强势，信号=1，分数=1.0
    - 收盘价在中轨上方但中轨走平/向下 → 偏强，信号=0，分数=0.6
    - 收盘价在中轨下方且中轨向下 → 弱势，信号=-1，分数=0.0
    - 收盘价在中轨下方但中轨走平/向上 → 偏弱，信号=0，分数=0.3
    - 收盘价突破上轨 → 超强但可能回调，信号=1，分数=0.8
    - 收盘价跌破下轨 → 超弱，信号=-1，分数=0.1
    """
    close = df['close'].astype(float)
    mid = close.rolling(window=period).mean()
    std_val = close.rolling(window=period).std()
    upper = mid + std * std_val
    lower = mid - std * std_val

    cur_close = close.iloc[-1]
    cur_mid = mid.iloc[-1]
    cur_upper = upper.iloc[-1]
    cur_lower = lower.iloc[-1]
    prev_mid = mid.iloc[-2] if len(mid) >= 2 else cur_mid

    # 计算带宽（归一化波动率）
    bandwidth = (cur_upper - cur_lower) / cur_mid * 100 if cur_mid > 0 else 0

    detail = {
        "upper": round(float(cur_upper), 2),
        "mid": round(float(cur_mid), 2),
        "lower": round(float(cur_lower), 2),
        "bandwidth": round(float(bandwidth), 2),
        "position": "inside",
    }

    if pd.isna(cur_mid):
        return {"signal": 0, "score": 0.0, "detail": detail}

    mid_trend = "up" if cur_mid >= prev_mid else "down"

    if cur_close > cur_upper:
        detail["position"] = "above_upper"
        return {"signal": 1, "score": 0.8, "detail": detail}
    elif cur_close < cur_lower:
        detail["position"] = "below_lower"
        return {"signal": -1, "score": 0.1, "detail": detail}
    elif cur_close > cur_mid:
        detail["position"] = "above_mid"
        if mid_trend == "up":
            return {"signal": 1, "score": 1.0, "detail": detail}
        else:
            return {"signal": 0, "score": 0.6, "detail": detail}
    else:
        detail["position"] = "below_mid"
        if mid_trend == "down":
            return {"signal": -1, "score": 0.0, "detail": detail}
        else:
            return {"signal": 0, "score": 0.3, "detail": detail}


def calc_rsi(df: pd.DataFrame, period: int = 14) -> Dict[str, Any]:
    """
    计算 RSI(14)。

    判断逻辑:
    - RSI 在 40~60 且上升 → 健康上升，信号=1，分数=0.8
    - RSI 在 60~70 且上升 → 强势，信号=1，分数=1.0
    - RSI 在 60~70 但下降 → 高位回落，信号=0，分数=0.5
    - RSI > 70 → 超买，信号=-1，分数=0.2
    - RSI < 30 → 超卖，信号=1，分数=0.6（等待反弹）
    - RSI 在 30~40 且上升 → 低位回升，信号=1，分数=0.7
    """
    close = df['close'].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))

    cur_rsi = rsi.iloc[-1]
    prev_rsi = rsi.iloc[-2] if len(rsi) >= 2 else cur_rsi
    trend = "up" if cur_rsi >= prev_rsi else "down"

    detail = {
        "rsi": round(float(cur_rsi), 2),
        "trend": trend,
    }

    if pd.isna(cur_rsi):
        return {"signal": 0, "score": 0.0, "detail": detail}

    if 60 <= cur_rsi <= 70 and trend == "up":
        return {"signal": 1, "score": 1.0, "detail": detail}
    elif 40 <= cur_rsi < 60 and trend == "up":
        return {"signal": 1, "score": 0.8, "detail": detail}
    elif 30 <= cur_rsi < 40 and trend == "up":
        return {"signal": 1, "score": 0.7, "detail": detail}
    elif cur_rsi < 30:
        return {"signal": 1, "score": 0.6, "detail": detail}  # 超卖反弹概率大
    elif 60 <= cur_rsi <= 70 and trend == "down":
        return {"signal": 0, "score": 0.5, "detail": detail}
    elif cur_rsi > 70:
        return {"signal": -1, "score": 0.2, "detail": detail}
    elif cur_rsi < 40:
        return {"signal": 0, "score": 0.3, "detail": detail}
    else:
        return {"signal": 0, "score": 0.4, "detail": detail}


def calc_volume_ratio(df: pd.DataFrame, period: int = 5) -> Dict[str, Any]:
    """
    计算量比 = 当日成交量 / 近N日(不含当日)均量。

    判断逻辑:
    - 量比 > 2.0 → 显著放量，信号=1，分数=1.0
    - 量比 1.5~2.0 → 温和放量，信号=1，分数=0.7
    - 量比 1.2~1.5 → 轻微放量，信号=0，分数=0.5
    - 量比 0.8~1.2 → 正常，信号=0，分数=0.3
    - 量比 < 0.8 → 缩量，信号=-1，分数=0.0
    """
    volume = df['volume'].astype(float)

    if len(volume) < period + 1:
        return {"signal": 0, "score": 0.3, "detail": {"volume_ratio": None}}

    today_vol = volume.iloc[-1]
    avg_vol = volume.iloc[-(period + 1):-1].mean()
    ratio = today_vol / avg_vol if avg_vol > 0 else 1.0

    detail = {
        "volume_ratio": round(float(ratio), 2),
        "today_volume": int(today_vol),
        "avg_volume": int(avg_vol),
    }

    if ratio >= 2.0:
        return {"signal": 1, "score": 1.0, "detail": detail}
    elif ratio >= 1.5:
        return {"signal": 1, "score": 0.7, "detail": detail}
    elif ratio >= 1.2:
        return {"signal": 0, "score": 0.5, "detail": detail}
    elif ratio >= 0.8:
        return {"signal": 0, "score": 0.3, "detail": detail}
    else:
        return {"signal": -1, "score": 0.0, "detail": detail}


def compute_all_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """
    整合计算所有技术指标，返回汇总 dict。

    Parameters
    ----------
    df : pd.DataFrame
        日K线数据，须含 close, open, high, low, volume 列

    Returns
    -------
    dict
        {
            "indicators": {...},
            "combined_score": 0.0~1.0  # 加权综合技术得分
        }
    """
    from config import TECHNICAL_WEIGHTS

    results = {
        "ma": calc_ma(df),
        "macd": calc_macd(df),
        "kdj": calc_kdj(df),
        "boll": calc_boll(df),
        "rsi": calc_rsi(df),
        "volume_ratio": calc_volume_ratio(df),
    }

    # 按权重计算综合技术得分
    combined = 0.0
    for name, result in results.items():
        weight_key = {
            "ma": "ma_trend",
            "volume_ratio": "volume_ratio",
        }.get(name, name)

        weight = TECHNICAL_WEIGHTS.get(weight_key, 0.1)
        combined += result["score"] * weight

    return {
        "indicators": results,
        "combined_score": round(combined, 4),
    }
