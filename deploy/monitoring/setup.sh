#!/bin/bash
# TrackerBundle3 — Monitoring Stack Deploy
# Uptime Kuma + Metabase kurulum scripti
# Çalıştır: bash deploy/monitoring/setup.sh

set -e
echo "=== TrackerBundle3 Monitoring Stack ==="

# ── Docker yüklü değilse yükle ────────────────────────────────────────────────
if ! command -v docker &> /dev/null; then
    echo "Docker yükleniyor..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker ubuntu
    echo "Docker yüklendi. Yeni shell aç: newgrp docker"
fi

# ── Uptime Kuma ───────────────────────────────────────────────────────────────
# Port 3001 — dahili erişim (nginx ile serve edilecek)
echo ""
echo "=== Uptime Kuma başlatılıyor (port 3001) ==="
docker stop uptime-kuma 2>/dev/null || true
docker rm   uptime-kuma 2>/dev/null || true
docker run -d \
    --name uptime-kuma \
    --restart unless-stopped \
    -p 127.0.0.1:3001:3001 \
    -v uptime-kuma-data:/app/data \
    louislam/uptime-kuma:1

echo "✅ Uptime Kuma: http://localhost:3001"
echo "   İlk açılışta kullanıcı adı/şifre belirle"
echo ""
echo "   Telegram bildirimi için:"
echo "   Settings → Notifications → Telegram"
echo "   Bot Token: (TELEGRAM_BOT_TOKEN'dan al)"
echo "   Chat ID:   (TELEGRAM_CHAT_ID'den al)"
echo ""
echo "   İzlenecek servisler:"
echo "   - http://localhost:8000/status (TrakerBundle API)"
echo "   - https://api.groq.com (Groq)"
echo "   - https://api.cerebras.ai (Cerebras)"
echo "   - https://api.openai.com (OpenRouter)"
echo "   - https://generativelanguage.googleapis.com (Gemini)"
echo "   - https://api.ebay.com (eBay)"
echo "   - https://sellingpartnerapi-na.amazon.com (Amazon SP-API)"

# ── Metabase ──────────────────────────────────────────────────────────────────
# Port 3000 — dahili erişim
echo ""
echo "=== Metabase başlatılıyor (port 3000) ==="
docker stop metabase 2>/dev/null || true
docker rm   metabase 2>/dev/null || true

# SQLite dosyasının konumu
DATA_DIR="${HOME}/trackerbundle3/data"

docker run -d \
    --name metabase \
    --restart unless-stopped \
    -p 127.0.0.1:3000:3000 \
    -e "MB_DB_TYPE=h2" \
    -e "MB_DB_FILE=/metabase-data/metabase.db" \
    -v metabase-data:/metabase-data \
    -v "${DATA_DIR}:/tb-data:ro" \
    metabase/metabase:latest

echo "✅ Metabase: http://localhost:3000"
echo "   İlk açılış ~2 dakika sürer"
echo "   Kurulum: email + şifre belirle"
echo ""
echo "   SQLite veritabanı eklemek için:"
echo "   Admin → Databases → Add database"
echo "   Type: SQLite"
echo "   Database file path: /tb-data/scan_history.json  (NOT: JSON dosyası, SQLite değil)"
echo "   *** TrackerBundle flat JSON kullanıyor, SQLite değil ***"
echo "   Alternatif: aşağıdaki FastAPI endpoint'i ekle ve HTTP Data Source olarak kullan"

# ── nginx config (monitoring panel'leri için) ─────────────────────────────────
echo ""
echo "=== Nginx konfigürasyonu ==="
cat > /tmp/tb-monitoring.nginx << 'NGINX'
# /etc/nginx/sites-available/tb-monitoring
# sudo ln -s /etc/nginx/sites-available/tb-monitoring /etc/nginx/sites-enabled/
# sudo nginx -t && sudo systemctl reload nginx

server {
    listen 80;
    server_name _;

    # Mevcut TrackerBundle API
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600;
    }

    # Uptime Kuma — /status path'inden serve
    location /uptime/ {
        rewrite ^/uptime(/.*)$ $1 break;
        proxy_pass         http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
    }

    # Metabase — /dashboard path'inden serve
    location /dashboard/ {
        rewrite ^/dashboard(/.*)$ $1 break;
        proxy_pass       http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_read_timeout 300;
    }
}
NGINX

sudo cp /tmp/tb-monitoring.nginx /etc/nginx/sites-available/tb-monitoring
echo "nginx config /etc/nginx/sites-available/tb-monitoring'e kopyalandı"
echo "Aktive etmek için:"
echo "  sudo ln -sf /etc/nginx/sites-available/tb-monitoring /etc/nginx/sites-enabled/default"
echo "  sudo nginx -t && sudo systemctl reload nginx"
echo ""
echo "Kurulum tamamlandı:"
echo "  Uptime Kuma: http://YOUR_IP/uptime/"
echo "  Metabase:    http://YOUR_IP/dashboard/"
