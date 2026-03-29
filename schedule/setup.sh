#!/bin/bash
# Install cron job for 8am Pacific (15:00 UTC)
(crontab -l 2>/dev/null; echo "0 15 * * * /home/k1/public/news_reader/schedule/run_news_reader.sh") | crontab -

# Copy anacron daily job as backup
sudo cp /home/k1/public/news_reader/schedule/run_news_reader.sh /etc/cron.daily/news-reader
sudo chmod +x /etc/cron.daily/news-reader

echo "Cron job installed: 0 15 * * * (8am Pacific)"
echo "Anacron backup installed: /etc/cron.daily/news-reader"
