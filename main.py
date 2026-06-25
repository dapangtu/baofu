"""
A-Share Afternoon Stock Selector
=================================

Multi-factor model: Technical (50%) + Money Flow (25%) + Concept (25%)

Usage:
  python main.py --web          Start web dashboard (open browser to view)
  python main.py --once         Run once (full mode)
  python main.py --quick        Quick test mode (fewer data)
"""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    for name in ("mootdx", "apscheduler", "urllib3", "werkzeug"):
        logging.getLogger(name).setLevel(logging.WARNING)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="A-Share Stock Selector — web dashboard or CLI"
    )
    parser.add_argument("--web", action="store_true",
                        help="Start web dashboard (this is the default)")
    parser.add_argument("--once", action="store_true",
                        help="Run once in terminal (full mode)")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode (use with --once or --web)")
    parser.add_argument("--port", type=int, default=5000,
                        help="Web server port (default: 5000)")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("main")

    if args.once:
        # ---- CLI once-off ----
        from strategy.selector import StockSelector
        selector = StockSelector()
        top_stocks = selector.run(quick=args.quick)
        if top_stocks:
            logger.info(f"\nDone! Recommended {len(top_stocks)} stocks.")
        else:
            logger.warning("\nNo stocks matched filters.")
    elif args.web or not args.once:
        # ---- Web mode (default) ----
        from web_server import start_server
        start_server(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
