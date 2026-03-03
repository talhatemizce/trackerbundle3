#!/bin/bash
# Sunucuda çalıştır: bash deploy.sh
set -e
cd /home/ubuntu/trackerbundle3
echo "⬇️  git pull..."
git pull origin main
echo "🔄 Restart..."
sudo systemctl restart trackerbundle-api trackerbundle-ebay-scheduler trackerbundle-bot
sleep 2
echo "📊 Durum:"
systemctl is-active trackerbundle-api trackerbundle-ebay-scheduler trackerbundle-bot
echo "✅ Tamam! Tarayıcıda Ctrl+Shift+R yap."
