#!/usr/bin/env bash
# One-command installer for Tin nhanh 247 on a Debian/Ubuntu VPS.
#
#   curl -fsSL https://raw.githubusercontent.com/Harry-Kien/AutoTinTuc/main/deploy/install.sh | sudo bash
#
# Idempotent: safe to re-run to update. It never overwrites an existing .env and
# never touches state under storage/. It deliberately stops short of two steps
# that need a human: `openclaw onboard` (browser sign-in) and filling in .env.
set -euo pipefail

REPO="https://github.com/Harry-Kien/AutoTinTuc.git"
APP_DIR="/opt/AutoTinTuc"
APP_USER="fastnews"
NODE_MAJOR="22"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[!] %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Chay bang root: sudo bash install.sh"
command -v apt-get >/dev/null || die "Script nay danh cho Debian/Ubuntu."

say "1/7  Goi he thong"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3 >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Can Python 3.10 tro len (code dung cu phap dict | None)."
echo "    python3 $(python3 -c 'import platform;print(platform.python_version())')"

say "2/7  Node.js ${NODE_MAJOR}.x"
if command -v node >/dev/null && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge "$NODE_MAJOR" ]; then
  echo "    da co $(node -v)"
else
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
  echo "    da cai $(node -v)"
fi

say "3/7  OpenClaw"
if command -v openclaw >/dev/null; then
  echo "    da co $(openclaw --version 2>/dev/null | head -1)"
else
  npm install -g openclaw >/dev/null 2>&1 || die "Cai OpenClaw that bai."
  echo "    da cai $(openclaw --version 2>/dev/null | head -1)"
fi

say "4/7  Tai khoan he thong va ma nguon"
id -u "$APP_USER" >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin "$APP_USER"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch --quiet origin main
  git -C "$APP_DIR" reset --quiet --hard origin/main
  echo "    da cap nhat len $(git -C "$APP_DIR" rev-parse --short HEAD)"
else
  git clone --quiet "$REPO" "$APP_DIR"
  echo "    da clone $(git -C "$APP_DIR" rev-parse --short HEAD)"
fi

say "5/7  Thu muc, quyen va .env"
mkdir -p "$APP_DIR"/{logs,storage/fastnews247,outputs/fastnews247}
if [ -f "$APP_DIR/.env" ]; then
  echo "    .env da ton tai - giu nguyen"
else
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  warn ".env vua duoc tao tu mau - BAN PHAI dien gia tri vao"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"
chmod +x "$APP_DIR/deploy/run_once.sh"

say "6/7  Kiem tra truoc khi bat"
cd "$APP_DIR"
if python3 -c "import sys; sys.path.insert(0,'scripts'); import fastnews247_mvp as b; print('    OpenClaw CLI:', ' '.join(b.resolve_openclaw_cli()))" 2>/dev/null; then
  :
else
  warn "Chua tim thay OpenClaw CLI. Dat OPENCLAW_CLI trong .env neu cai o cho la."
fi
for t in test_fastnews247_quality test_openclaw_bridge test_editorial_modes; do
  if PYTHONIOENCODING=utf-8 python3 "scripts/$t.py" >/dev/null 2>&1; then
    echo "    $t: PASS"
  else
    warn "$t: FAIL"
  fi
done

say "7/7  systemd"
install -m 644 "$APP_DIR/deploy/fastnews247.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/fastnews247.timer" /etc/systemd/system/
systemctl daemon-reload
echo "    da cai unit (chua bat - xem buoc con lai ben duoi)"

cat <<EOF

────────────────────────────────────────────────────────────
Da cai xong phan tu dong duoc. Con 3 viec CAN BAN lam:

1. Dang nhap OpenClaw (can trinh duyet, khong tu dong duoc):
     openclaw onboard
     openclaw channels status        # phai thay Telegram OK

2. Dien $APP_DIR/.env
     FASTNEWS247_TELEGRAM_CHANNEL_ID=@fastnews247vn
     (token chi can khi dung mode "direct")

3. Thu gui mot tin that, roi moi bat lich:
     cd $APP_DIR
     sudo -u $APP_USER python3 scripts/fastnews247_mvp.py --test-telegram "ping"

     # chay OK thi bat:
     systemctl enable --now fastnews247.timer

Theo doi:
     systemctl list-timers fastnews247.timer
     journalctl -u fastnews247.service -f
     tail -f $APP_DIR/logs/fastnews247_scheduler.log
────────────────────────────────────────────────────────────
EOF
