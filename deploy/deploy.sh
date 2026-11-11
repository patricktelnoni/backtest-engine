#!/usr/bin/env bash
# Server-side deploy step, run by the GitHub Actions deploy job (or by hand) as the deploy user, after the
# repository has been checked out at the wanted commit and the frontend build was rsync'ed to web/dist.new/.
#
#   cd /opt/backtest-platform && git pull && bash deploy/deploy.sh
#
# The sudo commands below are the only ones the deploy user needs; see deploy/sudoers-backtest-deploy.
set -euo pipefail
cd "$(dirname "$0")/.."
APP_DIR=$(pwd)

echo "==> python dependencies"
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e engine -r api/requirements.txt

if [ -d web/dist.new ]; then
  echo "==> frontend: swapping in new build"
  rm -rf web/dist.old
  [ -d web/dist ] && mv web/dist web/dist.old
  mv web/dist.new web/dist
  rm -rf web/dist.old
fi
chmod -R o+rX "$APP_DIR/web/dist" "$APP_DIR/api" "$APP_DIR/engine" "$APP_DIR/.venv"  # service user + nginx read

echo "==> system config"
sudo /usr/bin/install -m 644 "$APP_DIR/deploy/systemd/backtest-api.service" /etc/systemd/system/backtest-api.service
sudo /usr/bin/install -m 644 "$APP_DIR/deploy/systemd/backtest-screener.service" /etc/systemd/system/backtest-screener.service
sudo /usr/bin/install -m 644 "$APP_DIR/deploy/systemd/backtest-screener.timer" /etc/systemd/system/backtest-screener.timer
sudo /usr/bin/install -m 644 "$APP_DIR/deploy/nginx/backtest.conf" /etc/nginx/sites-available/backtest.conf
sudo /usr/bin/systemctl daemon-reload
sudo /usr/bin/systemctl enable --now backtest-screener.timer

echo "==> restart api"
sudo /usr/bin/systemctl restart backtest-api
for i in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8001/api/strategies || true)
  [ "$code" = "401" ] && break   # up: unauthenticated requests are rejected, as designed
  sleep 0.5
done
if [ "${code:-}" != "401" ]; then
  echo "API did not come up (last status: ${code:-none})" >&2
  sudo /usr/bin/systemctl status backtest-api --no-pager || true
  exit 1
fi

if [ ! -f /var/lib/backtest/screener/idx_screener.json ]; then
  echo "==> first screener run (in the background; check: journalctl -u backtest-screener)"
  sudo /usr/bin/systemctl start --no-block backtest-screener.service
fi

echo "==> reload nginx"
sudo /usr/sbin/nginx -t
sudo /usr/bin/systemctl reload nginx
echo "deployed $(git rev-parse --short HEAD)"
