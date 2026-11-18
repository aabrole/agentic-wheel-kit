#!/usr/bin/env bash
# Install the kit on a fresh Ubuntu 24.04 server (Oracle Cloud Always Free works).
#   bash deploy/install.sh            install
#   bash deploy/install.sh --dry-run  print every step without doing it
#
# Result: the control panel at https://<your-ip-with-dashes>.sslip.io behind a password, reading
# your live account. The timer that reads the account and sends approved orders is installed
# but NOT enabled, and WHEEL_SEND stays 0 until you change it.
set -euo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else bash -c "$*"; fi; }

KIT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${KIT_DEST:-$HOME/wheel-kit}"
ME="$(id -un)"
LOGIN="${KIT_LOGIN:-me}"

echo "== Packages"
run "sudo apt-get update -y"
run "sudo apt-get install -y python3-venv python3-pip rsync curl debian-keyring debian-archive-keyring apt-transport-https gnupg"
if ! command -v caddy >/dev/null 2>&1; then
  run "curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
  run "curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null"
  run "sudo apt-get update -y && sudo apt-get install -y caddy"
fi

echo "== App -> $DEST"
run "mkdir -p '$DEST' && rsync -a --delete --exclude .venv --exclude state --exclude .git --exclude .env --exclude research '$KIT/' '$DEST/'"
run "[ -f '$DEST/.env' ] || (cp '$DEST/.env.example' '$DEST/.env' && chmod 600 '$DEST/.env')"
run "mkdir -p '$DEST/state' && chmod 700 '$DEST/state'"
run "[ -x '$DEST/.venv/bin/python' ] || python3 -m venv '$DEST/.venv'"
run "'$DEST/.venv/bin/pip' install -q --upgrade pip && '$DEST/.venv/bin/pip' install -q -r '$DEST/requirements.txt'"

echo "== Services"
for f in "$KIT"/deploy/systemd/*; do
  name="$(basename "$f")"
  run "sed -e 's#__USER__#$ME#g' -e 's#__DIR__#$DEST#g' '$f' | sudo tee /etc/systemd/system/$name >/dev/null"
done
run "sudo systemctl daemon-reload"
run "sudo systemctl enable --now wheel-web.service"

echo "== Firewall (Ubuntu images on Oracle ship with iptables rules that block 80/443)"
run "sudo iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport 80 -j ACCEPT"
run "sudo iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || sudo iptables -I INPUT 5 -p tcp --dport 443 -j ACCEPT"
run "command -v netfilter-persistent >/dev/null && sudo netfilter-persistent save || true"

echo "== HTTPS + password"
if [ "$DRY" = 1 ]; then
  echo "+ detect public IP, write /etc/caddy/Caddyfile for <ip-with-dashes>.sslip.io with a password, reload caddy"
else
  IP="$(curl -fsS https://api.ipify.org)"
  HOST="${IP//./-}.sslip.io"
  PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  HASH="$(caddy hash-password --plaintext "$PASS")"
  [ -f /etc/caddy/Caddyfile ] && sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-wheel-kit
  sed -e "s#__HOST__#$HOST#" -e "s#__LOGIN__#$LOGIN#" -e "s#__HASH__#$HASH#" "$KIT/deploy/Caddyfile.template" | sudo tee /etc/caddy/Caddyfile >/dev/null
  sudo systemctl reload caddy || sudo systemctl restart caddy
  echo
  echo "Control panel:  https://$HOST"
  echo "Login:          $LOGIN"
  echo "Password:       $PASS     (shown once; save it in your password manager)"
fi

cat <<MSG

Also open ports 80 and 443 in your cloud firewall (Oracle: VCN > Security List > Ingress rules).

Nothing reads your account or sends an order yet. On the server:
  cd $DEST
  nano .env                                         # BROKER, keys
  .venv/bin/python -m wheel.agent connect           # Robinhood only
  .venv/bin/python -m wheel.agent read              # first account read
  sudo systemctl enable --now wheel-cycle.timer     # read the account twice each trading day
  bash deploy/hermes.sh                             # OR let Hermes run the routine and report to you
Orders are only passed to the broker after you set WHEEL_SEND=1 in .env.
MSG
