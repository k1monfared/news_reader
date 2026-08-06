#!/bin/bash
# Source env vars (cron doesn't source .bashrc)
source /home/k1/public/terminal/bash_scripts/bashrc.private
# OpenCode Zen API key, read from opencode's credential store (single source of truth)
export OPENCODE_API_KEY="$(python3 -c "import json; print(json.load(open('/home/k1/.local/share/opencode/auth.json')).get('opencode-go', {}).get('key', ''))" 2>/dev/null)"
cd /home/k1/public/news_reader
/home/k1/anaconda3/bin/python run_pipeline.py >> logs/cron.log 2>&1
