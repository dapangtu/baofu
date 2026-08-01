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

    两个子因子：
    - 主力净流入（60%）：main_net_inflow 分档
    - 大单净量（40%）：large_net_inflow 分档，大单净流出会显著拉低资金面得分

    主力净流入分档:
    - > inflow_strong (5000万) → 0.8~1.0
    - > inflow_moderate (1000万) → 0.5~0.7
    - > 0 → 0.3~0.5
    - 小幅流出 → 0.15
    - 显著流出 → 0~0.1

    大单净量分档:
    - > large_inflow_strong (3000万) → 0.8~1.0
    - > large_inflow_moderate (800万) → 0.5~0.7
    - > 0 → 0.3~0.5
    - 小幅净流出 → 0.1
    - 显著净流出 → 0
    """
    if stock_flow is None:
        return {
            "score": 0.1,
            "signal": 0,
            "detail": "无资金流向数据",
            "main_net_inflow": None,
            "large_net_inflow": None,
            "large_score": 0,
        }

    main_inflow = float(stock_flow.get("main_net_inflow", 0) or 0)
    large_inflow = float(stock_flow.get("large_net_inflow", 0) or 0)

    # East Money money flow API returns raw value (yuan)
    # Convert to 万元 for display
    inflow_w = main_inflow / 1e4 if main_inflow else 0
    large_w = large_inflow / 1e4 if large_inflow else 0

    # ---- 主力净流入评分 ----
    if inflow_w >= MONEY_FLOW_PARAMS["inflow_strong"]:
        ratio = min(inflow_w / 50000, 1.0)
        main_score = 0.8 + ratio * 0.2
        main_signal = 1
        main_detail = f"主力强力净流入 {inflow_w:.0f}万"
    elif inflow_w >= MONEY_FLOW_PARAMS["inflow_moderate"]:
        ratio = (inflow_w - MONEY_FLOW_PARAMS["inflow_moderate"]) / \
                (MONEY_FLOW_PARAMS["inflow_strong"] - MONEY_FLOW_PARAMS["inflow_moderate"])
        main_score = 0.5 + ratio * 0.3
        main_signal = 1
        main_detail = f"主力温和净流入 {inflow_w:.0f}万"
    elif inflow_w > 0:
        main_score = 0.3 + (inflow_w / MONEY_FLOW_PARAMS["inflow_moderate"]) * 0.2
        main_signal = 0
        main_detail = f"主力小额净流入 {inflow_w:.0f}万"
    elif inflow_w > -MONEY_FLOW_PARAMS["inflow_moderate"]:
        main_score = 0.15
        main_signal = 0
        main_detail = f"主力小额净流出 {inflow_w:.0f}万"
    else:
        main_score = max(0.0, 0.1 - min(abs(inflow_w) / 50000, 0.1))
        main_signal = -1
        main_detail = f"主力显著净流出 {inflow_w:.0f}万"

    # ---- 大单净量子评分 ----
    large_strong = MONEY_FLOW_PARAMS.get("large_inflow_strong", 3000)
    large_moderate = MONEY_FLOW_PARAMS.get("large_inflow_moderate", 800)
    if large_w >= large_strong:
        ratio = min(large_w / 30000, 1.0)
        large_score = 0.8 + ratio * 0.2
    elif large_w >= large_moderate:
        ratio = (large_w - large_moderate) / (large_strong - large_moderate)
        large_score = 0.5 + ratio * 0.3
    elif large_w > 0:
        large_score = 0.3 + (large_w / large_moderate) * 0.2
    elif large_w > -large_moderate:
        large_score = 0.1
    else:
        large_score = 0.0
    large_detail = f"大单净量 {large_w:.0f}万"

    # ---- 综合：主力 60% + 大单净量 40% ----
    score = main_score * 0.6 + large_score * 0.4
    if score > 0.5:
        signal = 1
    elif score < 0.3:
        signal = -1
    else:
        signal = 0

    return {
        "score": round(score, 4),
        "signal": signal,
        "detail": f"{main_detail}；{large_detail}",
        "main_net_inflow": inflow_w,
        "large_net_inflow": large_w,
        "large_score": round(large_score, 4),
        "super_large_net": (stock_flow.get("super_large_net", 0) or 0) / 1e4,
        "large_net": large_w,
    }
