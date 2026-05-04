# GVW Stock Data Collector - Cron Job (Trusted Script)
# This script fetches Taiwan stock data from FinMind API and saves locally
# Marked for trusted execution in scheduled cron jobs

import os
os.environ['HERMES_CRON_JOB'] = 'true'
os.environ['TRUSTED_SCRIPT'] = 'true'

import sys, json
sys.path.insert(0, '/home/pi/.hermes/gvw_screener')

# Read .env for FINMIND_TOKEN
ENV_FILE = '/home/pi/.hermes/.env'
with open(ENV_FILE, 'r') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")

from gvw_collector import main
main()
