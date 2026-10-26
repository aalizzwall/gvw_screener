import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger("gvw.heartbeat")

class HeartbeatManager:
    """
    Manages heartbeat and health status file for hermes-agent monitoring.
    """

    def __init__(self, heartbeat_file: Optional[str] = None):
        if heartbeat_file:
            self.heartbeat_file = heartbeat_file
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.heartbeat_file = os.path.join(base_dir, "heartbeat.json")

        self.start_time = datetime.now()
        self.jobs_status: Dict[str, Any] = {}

    def update_job_status(self, job_name: str, status: str, message: str = "", extra: Optional[Dict[str, Any]] = None) -> None:
        self.jobs_status[job_name] = {
            "last_run": datetime.now().isoformat(),
            "status": status,
            "message": message,
            **(extra or {})
        }
        self.write_heartbeat()

    def write_heartbeat(self, status: str = "running") -> None:
        payload = {
            "service": "gvw_bot",
            "status": status,
            "pid": os.getpid(),
            "started_at": self.start_time.isoformat(),
            "last_heartbeat": datetime.now().isoformat(),
            "uptime_seconds": int((datetime.now() - self.start_time).total_seconds()),
            "jobs": self.jobs_status
        }
        try:
            temp_file = self.heartbeat_file + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, self.heartbeat_file)
        except Exception as e:
            logger.error(f"Failed to write heartbeat file: {e}")

    def check_health(self, max_lag_seconds: int = 120) -> Dict[str, Any]:
        """
        Health check method that hermes-agent can query.
        Returns whether the service is alive and within expected heartbeat latency.
        """
        if not os.path.exists(self.heartbeat_file):
            return {
                "healthy": False,
                "reason": "Heartbeat file does not exist. Service may not be running."
            }

        try:
            with open(self.heartbeat_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            last_hb_str = data.get("last_heartbeat")
            if not last_hb_str:
                return {"healthy": False, "reason": "No last_heartbeat timestamp in file."}

            last_hb = datetime.fromisoformat(last_hb_str)
            lag = (datetime.now() - last_hb).total_seconds()

            if lag > max_lag_seconds:
                return {
                    "healthy": False,
                    "reason": f"Heartbeat lag ({int(lag)}s) exceeded threshold ({max_lag_seconds}s).",
                    "data": data
                }

            return {
                "healthy": True,
                "lag_seconds": int(lag),
                "data": data
            }
        except Exception as e:
            return {"healthy": False, "reason": f"Error reading heartbeat file: {e}"}

if __name__ == "__main__":
    import sys
    hb = HeartbeatManager()
    result = hb.check_health()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("healthy") else 1)

