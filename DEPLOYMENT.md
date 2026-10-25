# PlantWise V4 — Production Deployment Runbook

## Overview
This runbook guides the operations and DevOps team in deploying PlantWise V4 to a production-grade Linux environment (Ubuntu 22.04 LTS / Debian 12 / Docker / Railway / AWS ECS).

---

## 1. System Requirements & Architecture

- **OS**: Ubuntu 22.04 LTS / Debian 12
- **Python**: 3.11+ (tested on Python 3.11, 3.12, 3.14)
- **Database**: PostgreSQL 15+ with `uuid-ossp` or native gen_random_uuid()
- **WSGI Server**: Gunicorn 21+ with `gevent` or `gthread` workers
- **Reverse Proxy**: Nginx 1.22+ or Caddy 2.7+ (with automatic Let's Encrypt SSL/TLS)
- **Object / Disk Storage**: Local persistent mount or S3-compatible bucket for `/static/uploads`

---

## 2. Production Environment Variables Matrix

Create a secure `.env` file on the production server (never commit to git):

| Variable Name | Required | Default / Example | Purpose / Security Notes |
| :--- | :---: | :--- | :--- |
| `SECRET_KEY` | **YES** | `python -c "import secrets; print(secrets.token_hex(32))"` | Cryptographic signing key for session cookies. App refuses to start without it. |
| `DATABASE_URL` | **YES** (or DB_* vars)| `postgresql://user:pass@host:5432/crop_advisory` | PostgreSQL connection string. Railway sets this automatically. |
| `DB_HOST` | Fallback | `127.0.0.1` | Database server IP or hostname. |
| `DB_PORT` | Fallback | `5432` | Database port. |
| `DB_NAME` | Fallback | `crop_advisory` | PostgreSQL database name. |
| `DB_USER` | Fallback | `plantwise_prod` | Dedicated non-superuser database account. |
| `DB_PASSWORD` | Fallback | `[SECURE_STRONG_PASSWORD]` | Strong password. Zero hardcoded fallbacks in source code. |
| `OPENROUTER_API_KEY` | Recommended | `sk-or-v1-...` | OpenRouter AI vision & assistant API key. App falls back cleanly to local KB if omitted. |
| `PORT` | Optional | `5000` | Port for WSGI server to bind. |
| `SESSION_COOKIE_SECURE`| Production | `True` | Enforces HTTPS-only cookies in production. |
| `MAX_CONTENT_LENGTH` | Built-in | `10485760` (10 MB) | Caps upload payloads to protect against memory exhaustion. |

---

## 3. Database Migration Sequence

PlantWise uses sequential, idempotent SQL migrations. Apply them in order:

```bash
# Step 1: Ensure database exists
sudo -u postgres psql -c "CREATE DATABASE crop_advisory;"

# Step 2: Apply base schema (if fresh installation)
python migrate_to_postgres.py

# Step 3: Apply migrations in order
psql -d crop_advisory -f migrations/001_add_users_farms_marketplace.sql
psql -d crop_advisory -f migrations/002_add_calendar_and_alerts.sql
psql -d crop_advisory -f migrations/003_performance_indexes.sql

# Step 4: Fix sequence alignment
python fix_sequences.py

# Step 5: Seed or verify admin credentials
python seed_admin.py
```

### Rollback Procedure
If rollback is required for performance indexes:
```sql
DROP INDEX IF EXISTS idx_diagnosis_sessions_ref;
DROP INDEX IF EXISTS idx_diagnosis_sessions_created;
DROP INDEX IF EXISTS idx_sub_counties_county;
DROP INDEX IF EXISTS idx_ai_messages_conv;
DROP INDEX IF EXISTS idx_diagnosis_feedback_ref;
DROP INDEX IF EXISTS idx_advisory_sessions_county;
DROP INDEX IF EXISTS idx_advisory_sessions_created;
```

---

## 4. Production WSGI & Process Supervision (Systemd & Gunicorn)

### 4.1 Systemd Service Unit (`/etc/systemd/system/plantwise.service`)
```ini
[Unit]
Description=PlantWise V4 Agricultural Decision Platform
After=network.target postgresql.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/plantwise-v4
Environment="PATH=/var/www/plantwise-v4/venv/bin"
EnvironmentFile=/var/www/plantwise-v4/.env
ExecStart=/var/www/plantwise-v4/venv/bin/gunicorn \
    --workers 4 \
    --worker-class gthread \
    --threads 4 \
    --timeout 60 \
    --bind 127.0.0.1:5000 \
    --access-logfile /var/log/plantwise/access.log \
    --error-logfile /var/log/plantwise/error.log \
    app:app

Restart=always
RestartSec=5
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=plantwise

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo mkdir -p /var/log/plantwise
sudo chown www-data:www-data /var/log/plantwise
sudo systemctl daemon-reload
sudo systemctl enable plantwise
sudo systemctl start plantwise
```

---

## 5. Reverse Proxy Configuration (Nginx & SSL)

`/etc/nginx/sites-available/plantwise.conf`:
```nginx
server {
    listen 80;
    server_name app.plantwise.co.ke;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name app.plantwise.co.ke;

    # SSL Certificates (managed via certbot --nginx)
    ssl_certificate /etc/letsencrypt/live/app.plantwise.co.ke/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.plantwise.co.ke/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Security Headers (OWASP Top 10)
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; img-src 'self' data: https:; script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com;" always;

    # Max upload limit
    client_max_body_size 10M;

    # Static file caching
    location /static/ {
        alias /var/www/plantwise-v4/static/;
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    # Reverse proxy to Gunicorn WSGI
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Health check bypass
    location /healthz {
        proxy_pass http://127.0.0.1:5000/healthz;
        access_log off;
    }
}
```

---

## 6. Liveness Probe & Monitoring
- **Healthcheck URL**: `https://app.plantwise.co.ke/healthz`
  - Expected HTTP Code: `200 OK`
  - Response Body: `{"status": "healthy", "db": "connected"}`
- Configure Prometheus / Uptime Kuma to ping `/healthz` every 30 seconds.
