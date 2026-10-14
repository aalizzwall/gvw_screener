import os
import unittest
from datetime import datetime
from gvw_bot.heartbeat import HeartbeatManager
from gvw_bot.scheduler import GVWScheduler, ScheduledJob

class TestBotAndScheduler(unittest.TestCase):

    def setUp(self):
        self.test_hb_file = os.path.join(os.path.dirname(__file__), "test_heartbeat.json")
        self.hb = HeartbeatManager(heartbeat_file=self.test_hb_file)

    def tearDown(self):
        if os.path.exists(self.test_hb_file):
            try:
                os.remove(self.test_hb_file)
            except Exception:
                pass

    def test_heartbeat_lifecycle(self):
        self.hb.write_heartbeat(status="running")
        status = self.hb.check_health(max_lag_seconds=60)
        self.assertTrue(status.get("healthy"))
        self.assertEqual(status["data"]["status"], "running")

    def test_job_trigger_logic(self):
        job = ScheduledJob(
            name="test_job",
            func=lambda: True,
            interval_minutes=5,
            weekdays_only=False
        )
        now = datetime.now()
        # Should run because last_run is None
        self.assertTrue(job.should_run(now))

        job.last_run = now
        # Should not run immediately after
        self.assertFalse(job.should_run(now))

    def test_scheduler_jobs_setup(self):
        scheduler = GVWScheduler(heartbeat_mgr=self.hb)
        job_names = [j.name for j in scheduler.jobs]
        self.assertIn("intraday_monitor", job_names)
        self.assertIn("daily_collection", job_names)
        self.assertIn("daily_ranking", job_names)
        self.assertIn("daily_report", job_names)

if __name__ == "__main__":
    unittest.main()

