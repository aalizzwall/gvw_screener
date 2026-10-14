import argparse
import sys
import logging
from src.state.state_manager import StateManager
from src.providers.factory import DataProviderFactory
from src.core.orchestrator import Orchestrator
from src.analysis.ranker import run_ranking
from src.analysis.intraday import run_intraday_monitoring
from src.reporting.report_engine import generate_daily_report
from src.reporting.telegram_notifier import send_telegram_message

logger = logging.getLogger("gvw")

def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

def cmd_collect(args):
    orchestrator = Orchestrator()
    stats = orchestrator.run_collection_cycle(batch_ratio=args.batch_ratio, force=args.force)
    print(f"\nCollection cycle finished: {stats}")

def cmd_rank(args):
    result = run_ranking()
    count = len(result.get('data', []))
    print(f"\nRanking finished: {count} stocks evaluated.")

def cmd_report(args):
    report_text = generate_daily_report()
    print("\n" + report_text)
    if args.send:
        success = send_telegram_message(report_text)
        print(f"Telegram notification: {'Sent ✅' if success else 'Failed / Not Configured ❌'}")

def cmd_monitor(args):
    alerts = run_intraday_monitoring()
    if alerts:
        print(f"\nFound {len(alerts)} alerts:")
        for a in alerts:
            print("----------------")
            print(a)
            if args.send:
                send_telegram_message(a)
    else:
        print("\n✅ No new intraday signals detected.")

def cmd_pipeline(args):
    print("=== Step 1: Data Collection ===")
    orchestrator = Orchestrator()
    orchestrator.run_collection_cycle(batch_ratio=args.batch_ratio, force=args.force)

    print("\n=== Step 2: Ranking Analysis ===")
    run_ranking()

    print("\n=== Step 3: Daily Report ===")
    report_text = generate_daily_report()
    print(report_text)
    if args.send:
        send_telegram_message(report_text)

def cmd_bot(args):
    from gvw_bot.heartbeat import HeartbeatManager
    if args.status:
        hb = HeartbeatManager()
        status = hb.check_health()
        print(f"\nGVW Bot Service Status:")
        print(f"  Healthy: {'✅ Yes' if status.get('healthy') else '❌ No'}")
        if status.get('reason'):
            print(f"  Reason: {status.get('reason')}")
        if status.get('data'):
            d = status['data']
            print(f"  PID: {d.get('pid')}")
            print(f"  Last Heartbeat: {d.get('last_heartbeat')}")
            print(f"  Uptime: {d.get('uptime_seconds', 0)}s")
            print(f"  Jobs: {d.get('jobs')}")
    else:
        from gvw_bot.main import main as run_bot
        run_bot()

def cmd_tools(args):
    if args.tool_name == "force-update":
        orchestrator = Orchestrator()
        orchestrator.force_update_prices()
    elif args.tool_name == "verify-token":
        factory = DataProviderFactory()
        res = factory.finmind.validate_token()
        print("\nFinMind Token Status:")
        for k, v in res.items():
            print(f"  {k}: {v}")
    elif args.tool_name == "check-health":
        factory = DataProviderFactory()
        health = factory.check_availability()
        print("\nProvider Connectivity:")
        for k, v in health.items():
            print(f"  {k}: {'UP ✅' if v else 'DOWN ❌'}")
    else:
        print(f"Unknown tool: {args.tool_name}")

def main():
    parser = argparse.ArgumentParser(description="GVW Screener - Taiwan Stock Screener & Real-Time Monitor")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # collect
    p_collect = subparsers.add_parser("collect", help="Run data collection cycle")
    p_collect.add_argument("--batch-ratio", type=float, default=0.1, help="Ratio of universe to update (default: 0.1)")
    p_collect.add_argument("--force", action="store_true", help="Force update even if TTL not expired")

    # rank
    subparsers.add_parser("rank", help="Run ranking algorithm")

    # report
    p_report = subparsers.add_parser("report", help="Generate daily market report")
    p_report.add_argument("--send", action="store_true", help="Send report to Telegram channel")

    # monitor
    p_monitor = subparsers.add_parser("monitor", help="Run intraday monitoring for top stocks")
    p_monitor.add_argument("--send", action="store_true", help="Send alerts to Telegram channel")

    # pipeline
    p_pipe = subparsers.add_parser("pipeline", help="Run full pipeline: collect -> rank -> report")
    p_pipe.add_argument("--batch-ratio", type=float, default=0.1, help="Ratio of universe to update")
    p_pipe.add_argument("--force", action="store_true", help="Force update data")
    p_pipe.add_argument("--send", action="store_true", help="Send final report to Telegram")

    # bot
    p_bot = subparsers.add_parser("bot", help="Run GVW Bot & Autonomous Scheduler Service daemon")
    p_bot.add_argument("--status", action="store_true", help="Check status of running GVW Bot daemon")

    # tools
    p_tools = subparsers.add_parser("tools", help="Utility tools")
    p_tools.add_argument("tool_name", choices=["force-update", "verify-token", "check-health"], help="Tool to run")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "rank":
        cmd_rank(args)
    elif args.command == "report":
        cmd_report(args)
    elif args.command == "monitor":
        cmd_monitor(args)
    elif args.command == "pipeline":
        cmd_pipeline(args)
    elif args.command == "bot":
        cmd_bot(args)
    elif args.command == "tools":
        cmd_tools(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

