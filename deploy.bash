#!/bin/bash
# ============================================================
# Deploy script: Django (payday) + Gunicorn + Nginx + systemd
# ------------------------------------------------------------
# Usage:
#   1. Edit the CONFIG section below to match your setup.
#   2. Copy this file to your VPS (scp or paste into nano).
#   3. chmod +x deploy_payday.sh
#   4. sudo ./deploy_payday.sh
#
# Safe to re-run: it will update code, reinstall deps,
# re-run migrations/collectstatic, and restart services.
# ============================================================

set -e  # stop immediately on any error

# ---------------- CONFIG (edit these) ----------------
APP_NAME="payday"                              # used for service name, nginx config name
PROJECT_DIR="/root/workspace/payday"              # where the Django project lives (or will be cloned)
REPO_URL=""                                    # git repo URL, leave blank if code is already on the server
DEPLOY_USER="root"                           # non-root user that owns/runs the app
DOMAIN="payday.app.mvindustrial.in"                 # subdomain to serve
DJANGO_WSGI_MODULE="payday.wsgi:application"   # <project_folder>.wsgi:application
GUNICORN_PORT="8000"
GUNICORN_WORKERS="3"
ENABLE_SSL="yes"                               # "yes" to run certbot automatically, "no" to skip
# -------------------------------------------------------

echo "== Step 1: System packages =="
apt update
apt install -y python3-venv python3-pip nginx git curl

#echo "== Step 2: Project code =="
#if [ ! -d "$PROJECT_DIR" ]; then
#    if [ -n "$REPO_URL" ]; then
#        sudo -u "$DEPLOY_USER" git clone "$REPO_URL" "$PROJECT_DIR"
#    else
#        echo "ERROR: $PROJECT_DIR does not exist and no REPO_URL was given."
#        echo "Either set REPO_URL in the config, or upload your code to $PROJECT_DIR first."
#        exit 1
#    fi
#else
#    if [ -n "$REPO_URL" ]; then
#        echo "Project dir exists, pulling latest changes..."
#        cd "$PROJECT_DIR"
#        sudo -u "$DEPLOY_USER" git pull
#    fi
#fi

cd "$PROJECT_DIR"

echo "== Step 3: Virtualenv + dependencies =="
if [ ! -d "venv" ]; then
    sudo -u "$DEPLOY_USER" python3 -m venv venv
fi
# Run the venv-activated block as DEPLOY_USER (not root), so venv files
# and installed packages stay owned by the correct user.
# NOTE: activation here only affects this subshell — it will not
# leave your interactive terminal activated after the script exits.
sudo -u "$DEPLOY_USER" bash -c "
    cd '$PROJECT_DIR' &&
    echo '== Activate and install ==' &&
    source venv/bin/activate &&
    pip install --upgrade pip &&
    if [ -f requirements.txt ]; then pip install -r requirements.txt; fi &&
    pip install gunicorn &&
    echo '== Step 4: Django migrate + collectstatic ==' &&
    python manage.py migrate --noinput &&
    python manage.py collectstatic --noinput &&
    deactivate
"

echo "== Step 5: systemd service =="
cat > /etc/systemd/system/${APP_NAME}.service <<EOF
[Unit]
Description=Gunicorn daemon for ${APP_NAME} Django app
After=network.target

[Service]
User=${DEPLOY_USER}
Group=www-data
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PROJECT_DIR}/venv/bin/gunicorn \\
          --workers ${GUNICORN_WORKERS} \\
          --bind 127.0.0.1:${GUNICORN_PORT} \\
          ${DJANGO_WSGI_MODULE}

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${APP_NAME}"
systemctl restart "${APP_NAME}"

echo "== Step 6: Nginx config =="
cat > /etc/nginx/sites-available/${APP_NAME} <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location /static/ {
        alias ${PROJECT_DIR}/staticfiles/;
    }

    location /media/ {
        alias ${PROJECT_DIR}/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:${GUNICORN_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

ln -sf /etc/nginx/sites-available/${APP_NAME} /etc/nginx/sites-enabled/${APP_NAME}
nginx -t
systemctl reload nginx

echo "== Step 7: Firewall =="
if command -v ufw >/dev/null 2>&1; then
    ufw allow 'Nginx Full' || true
    ufw allow OpenSSH || true
fi

echo "== Step 8: SSL (Let's Encrypt) =="
if [ "$ENABLE_SSL" = "yes" ]; then
    apt install -y certbot python3-certbot-nginx
    certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m admin@${DOMAIN} || \
        echo "Certbot failed or needs manual input — run 'sudo certbot --nginx -d ${DOMAIN}' manually."
else
    echo "Skipping SSL setup (ENABLE_SSL=no)."
fi

echo "============================================"
echo "Done. Check status with:"
echo "  systemctl status ${APP_NAME}"
echo "  journalctl -u ${APP_NAME} -f"
echo "Visit: http://${DOMAIN} (or https:// if SSL was set up)"
echo "============================================"
