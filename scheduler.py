"""
定时任务调度器

每个交易日 14:50 自动触发尾盘选股。
"""

import logging
import time
from datetime import datetime

logger = logging.getLogger(__name__)


class TradingScheduler:
    """交易日定时调度器"""

    def __init__(self, selector):
        """
        Parameters
        ----------
        selector : StockSelector
            选股器实例
        """
        self.selector = selector
        self._scheduler = None
        self._running = False

    # ------------------------------------------------------------------
    # 交易日判断
    # ------------------------------------------------------------------
    def is_trading_day(self) -> bool:
        """
        判断今天是否为A股交易日。

        逻辑:
        1. 必须是周一到周五
        2. 排除法定节假日（通过 akshare 交易日历查询）
        3. 如果 akshare 不可用，仅排除周末
        """
        today = datetime.now()

        # 周末一定不是交易日
        if today.weekday() >= 5:
            return False

        # 尝试通过 akshare 获取交易日历
        try:
            import akshare as ak
            today_str = today.strftime("%Y%m%d")
            trade_cal = ak.tool_trade_date_hist_sina()
            if trade_cal is not None and len(trade_cal) > 0:
                # trade_date 列包含所有交易日
                trade_dates = trade_cal['trade_date'].astype(str).tolist()
                return today_str in trade_dates
        except Exception as e:
            logger.debug(f"交易日历查询失败（将仅排除周末）: {e}")

        # 兜底：仅排除周末
        return True

    # ------------------------------------------------------------------
    # 调度控制
    # ------------------------------------------------------------------
    def start(self):
        """启动定时调度器（阻塞运行）"""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            logger.error("APScheduler 未安装，无法启动定时任务。请运行: pip install APScheduler")
            return

        self._scheduler = BackgroundScheduler()
        self._running = True

        # 每个交易日 14:50 触发
        # cron: 分=50, 时=14, 周一~周五
        self._scheduler.add_job(
            self._tick,
            CronTrigger(minute=50, hour=14, day_of_week='mon-fri'),
            id='daily_stock_select',
            name='尾盘选股',
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info("=" * 50)
        logger.info("  定时调度器已启动")
        logger.info("  计划: 每个交易日 14:50 自动执行尾盘选股")
        logger.info("  按 Ctrl+C 停止")
        logger.info("=" * 50)

        # 首次启动时检查今天是否为交易日
        if self.is_trading_day():
            now = datetime.now()
            # 如果当前时间已过 14:50，提示今天可能已错过
            if now.hour > 14 or (now.hour == 14 and now.minute >= 50):
                logger.info(f"当前时间 {now.strftime('%H:%M')}，今天已过选股时间。将等待下一个交易日。")
            else:
                logger.info(f"今天为交易日，将在 14:50 执行选股（当前时间: {now.strftime('%H:%M')}）")
        else:
            logger.info("今天非交易日，调度器将在下一个交易日执行。")

        # 阻塞主线程
        try:
            while self._running:
                time.sleep(60)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            logger.info("调度器已停止")

    # ------------------------------------------------------------------
    # 任务执行
    # ------------------------------------------------------------------
    def _tick(self):
        """调度器触发时的执行函数"""
        logger.info("")
        logger.info("=" * 50)
        logger.info(f"  ⏰ 定时触发 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 50)

        # 双重保底：执行前再检查一次交易日
        if not self.is_trading_day():
            logger.info("今天非交易日，跳过选股。")
            return

        try:
            self.selector.run()
        except Exception as e:
            logger.error(f"选股过程异常: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # 手动触发
    # ------------------------------------------------------------------
    def run_once(self, quick: bool = False):
        """手动执行一次选股（不启动调度器）"""
        return self.selector.run(quick=quick)
