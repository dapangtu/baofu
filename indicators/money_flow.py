"""
资金流向分析模块

负责：
- 获取个股主力资金净流入
- 结合板块资金流向综合评分
"""

import logging
from typing import Dict, Optional

from config import MONEY_FLOW_PARAMS

logger = logging.getLogger(__name__)


def calc_money_flow_score(
    stock_flow: Optional[dict],
    sector_flow: Optional[dict] = None,
) -> Dict:
    """
    综合资金流向评分（0~1）。

    评分逻辑:
    - 主力净流入 > inflow_strong (5000万) → score 0.8~1.0
    - 主力净流入 > inflow_moderate (1000万) → score 0.5~0.7
    - 主力净流入 > 0 → score 0.3~0.5
    - 主力净流出 → score 0.0~0.2
    - 板块资金流入方向加成 → ±0.1

    Parameters
    ----------
    stock_flow : dict or None
        个股资金流向，来自 MarketDataFetcher.fetch_stock_money_flow()
    sector_flow : dict or None
        板块资金流向（暂未使用，预留接口）

    Returns
    -------
    dict
        {
            "score": 0.0~1.0,
            "signal": 1|0|-1,
            "detail": "描述",
            "main_net_inflow": 主力净流入(万元),
            ...
        }
    """
    if stock_flow is None:
        return {
            "score": 0.1,
            "signal": 0,
            "detail": "无资金流向数据",
            "main_net_inflow": 0,
        }

    main_inflow = float(stock_flow.get("main_net_inflow", 0) or 0)

    # East Money money flow API returns raw value (yuan)
    # Convert to 万元 for display
    inflow_w = main_inflow / 1e4 if main_inflow else 0

    # Based on main net inflow amount
    if inflow_w >= MONEY_FLOW_PARAMS["inflow_strong"]:
        ratio = min(inflow_w / 50000, 1.0)
        score = 0.8 + ratio * 0.2
        signal = 1
        detail = f"主力强力净流入 {inflow_w:.0f}万元"
    elif inflow_w >= MONEY_FLOW_PARAMS["inflow_moderate"]:
        ratio = (inflow_w - MONEY_FLOW_PARAMS["inflow_moderate"]) / \
                (MONEY_FLOW_PARAMS["inflow_strong"] - MONEY_FLOW_PARAMS["inflow_moderate"])
        score = 0.5 + ratio * 0.3
        signal = 1
        detail = f"主力温和净流入 {inflow_w:.0f}万元"
    elif inflow_w > 0:
        score = 0.3 + (inflow_w / MONEY_FLOW_PARAMS["inflow_moderate"]) * 0.2
        signal = 0
        detail = f"主力小额净流入 {inflow_w:.0f}万元"
    elif inflow_w > -MONEY_FLOW_PARAMS["inflow_moderate"]:
        score = 0.15
        signal = 0
        detail = f"主力小额净流出 {inflow_w:.0f}万元"
    else:
        score = max(0.0, 0.1 - min(abs(inflow_w) / 50000, 0.1))
        signal = -1
        detail = f"主力显著净流出 {inflow_w:.0f}万元"

    return {
        "score": round(score, 4),
        "signal": signal,
        "detail": detail,
        "main_net_inflow": inflow_w,
        "super_large_net": (stock_flow.get("super_large_net", 0) or 0) / 1e4,
        "large_net": (stock_flow.get("large_net", 0) or 0) / 1e4,
    }
