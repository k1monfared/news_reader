#!/bin/bash
# Install anacron daily job
sudo cp /home/k1/public/news_reader/schedule/run_news_reader.sh /etc/cron.daily/news-reader
sudo chmod +x /etc/cron.daily/news-reader

echo "Anacron daily job installed: /etc/cron.daily/news-reader"
