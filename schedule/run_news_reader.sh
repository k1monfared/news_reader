#!/bin/bash
# Source env vars (cron doesn't source .bashrc)
source /home/k1/public/terminal/bash_scripts/bashrc.private
export ANTHROPIC_AUTH_TOKEN="$POE_API_KEY"
export ANTHROPIC_BASE_URL="https://api.poe.com"
cd /home/k1/public/news_reader
python run_pipeline.py >> logs/cron.log 2>&1
