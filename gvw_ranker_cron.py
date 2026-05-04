#!/usr/bin/env python3
# GVW Ranker - Cron Job (Trusted Script)
# This script analyzes stock data and generates top 10 rankings
# Marked for trusted execution in scheduled cron jobs

import os
os.environ['HERMES_CRON_JOB'] = 'true'
os.environ['TRUSTED_SCRIPT'] = 'true'

import sys, json
sys.path.insert(0, '/home/pi/.hermes/gvw_screener')

# Read .env for any configuration
ENV_FILE = '/home/pi/.hermes/.env'
with open(ENV_FILE, 'r') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")

from gvw_ranker import main
main()
