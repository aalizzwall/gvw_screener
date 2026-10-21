import os
import sys
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Callable, Dict, Any, List, Optional

# Ensure parent directory is in path so we can import src.*
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from src.core.orchestrator import Orchestrator
from src.analysis.ranker import run_ranking
from src.analysis.intraday import run_intraday_monitoring
from src.reporting.report_engine import generate_daily_report
from src.reporting.telegram_notifier import send_telegram_message
from src.state.alert_subscribers import AlertSubscriptionStore
from gvw_bot.heartbeat import HeartbeatManager

logger = logging.getLogger("gvw.scheduler")

class ScheduledJob:
    def __init__(
        self,
        name: str,
        func: Callable[[], Any],
        interval_minutes: Optional[int] = None,
        daily_time: Optional[str] = None,  # "HH:MM"
        weekdays_only: bool = True,
        active_hours: Optional[tuple] = None, # (start_hour, end_hour)
        grace_minutes: int = 30
    ):
        self.name = name
        self.func = func
        self.interval_minutes = interval_minutes
        self.daily_time = daily_time
        self.weekdays_only = weekdays_only
        self.active_hours = active_hours
        self.grace_minutes = grace_minutes
        self.last_run: Optional[datetime] = None
        self.is_running: bool = False

    def should_run(self, now: datetime) -> bool:
        if self.is_running:
            return False

        if self.weekdays_only and now.weekday() >= 5:
            # 5 = Saturday, 6 = Sunday
            return False

        if self.active_hours:
            start_h, end_h = self.active_hours
            current_time_val = now.hour + now.minute / 60.0
            if not (start_h <= current_time_val <= end_h):
                return False

        if self.daily_time:
            target_h, target_m = map(int, self.daily_time.split(":"))
            target_dt = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
            grace_end = target_dt + timedelta(minutes=self.grace_minutes)
            if target_dt <= now < grace_end:
                if self.last_run is None or self.last_run.date() != now.date():
                    return True
            return False

        if self.interval_minutes:
            if self.last_run is None:
                return True
            elapsed = (now - self.last_run).total_seconds() / 60.0
            return elapsed >= self.interval_minutes

        return False

class GVWScheduler:
    """
    Built-in background scheduler managing periodic collection, ranking,
    reporting, and intraday monitoring.
    """

    def __init__(
        self,
        heartbeat_mgr: Optional[HeartbeatManager] = None,
        alert_store: Optional[AlertSubscriptionStore] = None,
    ):
        self.hb = heartbeat_mgr or HeartbeatManager()
        self.orchestrator = Orchestrator()
        self.alert_store = alert_store or AlertSubscriptionStore()
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self.jobs: List[ScheduledJob] = []
        self._setup_jobs()

    def _setup_jobs(self):
        # 1. Intraday monitoring: Weekdays 09:00 - 13:30, every 10 minutes
        self.jobs.append(ScheduledJob(
            name="intraday_monitor",
            func=self.job_intraday_monitor,
            interval_minutes=10,
            weekdays_only=True,
            active_hours=(9.0, 13.5)
        ))

        # 2. Daily Post-Market Collection: Weekdays at 14:00
        self.jobs.append(ScheduledJob(
            name="daily_collection",
            func=self.job_daily_collection,
            daily_time="14:00",
            weekdays_only=True,
            grace_minutes=45
        ))

        # 3. Daily Ranking Analysis: Weekdays at 15:00
        self.jobs.append(ScheduledJob(
            name="daily_ranking",
            func=self.job_daily_ranking,
            daily_time="15:00",
            weekdays_only=True,
            grace_minutes=30
        ))

        # 4. Daily Post-Market Report: Weekdays at 15:30
        self.jobs.append(ScheduledJob(
            name="daily_report",
            func=self.job_daily_report,
            daily_time="15:30",
            weekdays_only=True,
            grace_minutes=60
        ))

        # 5. Daily Pre-Market Morning Summary: Weekdays at 08:30
        self.jobs.append(ScheduledJob(
            name="morning_briefing",
            func=self.job_morning_briefing,
            daily_time="08:30",
            weekdays_only=True,
            grace_minutes=30
        ))

        # 6. Periodic Oldest-First Sync: Every 60 minutes (always running)
        # Initialize last_run to now so it does not hog resources immediately upon boot
        periodic_sync = ScheduledJob(
            name="periodic_sync",
            func=self.job_periodic_sync,
            interval_minutes=60,
            weekdays_only=False
        )
        periodic_sync.last_run = datetime.now()
        self.jobs.append(periodic_sync)

    # --- Job Implementations ---

    def job_intraday_monitor(self):
        logger.info("[JOB] Running Intraday Monitor...")
        alerts = run_intraday_monitoring()
        if alerts:
            # 盤中突破信號僅推播給有開啟「盤中監控 (on)」的使用者，不推播至頻道或未開啟者
            for chat_id in self.alert_store.get_subscribers():
                for alert in alerts:
                    send_telegram_message(alert, chat_id=chat_id)
        self.hb.update_job_status("intraday_monitor", "ok", message=f"Generated {len(alerts)} alerts")

    def job_daily_collection(self):
        logger.info("[JOB] Running Daily Post-Market Collection...")
        stats = self.orchestrator.run_collection_cycle(batch_ratio=1.0, force=False)
        self.hb.update_job_status("daily_collection", "ok", message=f"Fetched: {stats}")

    def job_daily_ranking(self):
        logger.info("[JOB] Running Daily Screener Ranking...")
        result = run_ranking()
        count = len(result.get("data", []))
        self.hb.update_job_status("daily_ranking", "ok", message=f"Ranked {count} stocks")

    def job_daily_report(self):
        logger.info("[JOB] Generating and sending Post-Market Daily Report...")
        report_text = generate_daily_report()
        sent = send_telegram_message(report_text)
        self.hb.update_job_status("daily_report", "ok" if sent else "warning", message="Sent to Telegram" if sent else "Failed/Not configured")

    def job_morning_briefing(self):
        logger.info("[JOB] Generating Pre-Market Morning Briefing...")
        report_text = generate_daily_report()
        briefing = "🌅 <b>【GVW 晨間選股速報】</b>\n" + report_text
        sent = send_telegram_message(briefing)
        self.hb.update_job_status("morning_briefing", "ok" if sent else "warning")

    def job_periodic_sync(self):
        logger.info("[JOB] Running Periodic Oldest-First Sync (10% batch)...")
        stats = self.orchestrator.run_collection_cycle(batch_ratio=0.1, force=False)
        self.hb.update_job_status("periodic_sync", "ok", message=f"Sync stats: {stats}")

    # --- Scheduler Execution and Loop ---

    def _execute_job(self, job: ScheduledJob):
        logger.info(f"Started scheduled job: {job.name}")
        try:
            job.func()
            logger.info(f"Finished scheduled job: {job.name}")
        except Exception as e:
            logger.error(f"Error executing job {job.name}: {e}", exc_info=True)
            self.hb.update_job_status(job.name, "error", message=str(e))
        finally:
            job.is_running = False

    def trigger_job_async(self, job: ScheduledJob, now: Optional[datetime] = None) -> bool:
        if job.is_running:
            logger.warning(f"Job {job.name} is already running. Skipping trigger.")
            return False
        job.last_run = now or datetime.now()
        job.is_running = True
        try:
            t = threading.Thread(
                target=self._execute_job,
                args=(job,),
                name=f"GVWJob-{job.name}",
                daemon=True
            )
            t.start()
            return True
        except Exception as e:
            job.is_running = False
            logger.error(f"Error starting job thread for {job.name}: {e}", exc_info=True)
            self.hb.update_job_status(job.name, "error", message=str(e))
            return False

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="GVWSchedulerThread")
        self._thread.start()
        logger.info("GVW Background Scheduler started successfully.")

    def stop(self):
        self.running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("GVW Background Scheduler stopped.")

    def _run_loop(self):
        last_hb_time = 0.0
        while self.running:
            now = datetime.now()

            # 1. Update heartbeat every 30 seconds
            if time.time() - last_hb_time >= 30:
                self.hb.write_heartbeat(status="running")
                last_hb_time = time.time()

            # 2. Check and trigger scheduled jobs
            for job in self.jobs:
                if not self.running:
                    break
                try:
                    if job.should_run(now):
                        logger.info(f"Triggering scheduled job: {job.name}")
                        self.trigger_job_async(job, now=now)
                except Exception as e:
                    logger.error(f"Error checking job {job.name}: {e}", exc_info=True)

            time.sleep(10)
