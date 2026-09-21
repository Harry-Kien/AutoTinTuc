#!/usr/bin/env bash
# Tin nhanh 247 - health check.
#
# Catches the failure modes that are silent: the bot keeps exiting 0 while
# quietly publishing nothing, OAuth quota runs out, a token expires, or the
# gateway dies. Prints a report always; exits 1 when something needs
# attention, so cron can decide whether to alert.
#
# Usage:
#   healthcheck.sh              report only
#   healthcheck.sh --alert      also send a Telegram DM when something is wrong
set -uo pipefail

export PATH="$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

BOT_DIR="$HOME/AutoTinTuc"
ALERT_TARGET="${FASTNEWS247_ALERT_TARGET:-}"
QUIET_HOURS_LIMIT=12      # no post for this long -> problem
WEEK_QUOTA_FLOOR=15       # percent
TOKEN_DAYS_FLOOR=2

problems=()
note() { printf '  %-26s %s\n' "$1" "$2"; }

echo "=== Tin nhanh 247 - $(date '+%Y-%m-%d %H:%M %Z') ==="

# --- dang tin -------------------------------------------------------------
posts24=$(journalctl --user -u fastnews247.service --since "24 hours ago" --no-pager -o cat 2>/dev/null | grep -c "Posting live")
cycles24=$(journalctl --user -u fastnews247.service --since "24 hours ago" --no-pager -o cat 2>/dev/null | grep -c "Drafts:")
note "tin dang 24h" "$posts24 (qua $cycles24 chu ky)"

last_epoch=$(journalctl --user -u fastnews247.service --no-pager -o short-unix 2>/dev/null | grep "Posting live" | tail -1 | cut -d. -f1)
if [ -n "${last_epoch:-}" ]; then
  hours=$(( ( $(date +%s) - last_epoch ) / 3600 ))
  note "tin gan nhat" "${hours}h truoc"
  [ "$hours" -ge "$QUIET_HOURS_LIMIT" ] && problems+=("Khong dang tin nao trong ${hours} gio")
else
  problems+=("Chua tung dang tin nao")
fi

# --- dich vu --------------------------------------------------------------
gw=$(curl -s -f -m 10 http://127.0.0.1:18789/healthz 2>/dev/null)
note "gateway" "${gw:-KHONG PHAN HOI}"
[ -z "$gw" ] && problems+=("Gateway khong phan hoi")

timer=$(systemctl --user is-active fastnews247.timer 2>/dev/null)
note "timer" "$timer"
[ "$timer" != "active" ] && problems+=("Timer khong chay (trang thai: $timer)")

# --- han muc va token -----------------------------------------------------
models=$(timeout 200 openclaw models status 2>/dev/null)

week=$(printf '%s' "$models" | grep -oP 'Week \K[0-9]+(?=% left)' | head -1)
if [ -n "${week:-}" ]; then
  note "han muc tuan" "${week}% con lai"
  [ "$week" -lt "$WEEK_QUOTA_FLOOR" ] && problems+=("Han muc tuan chi con ${week}%")
else
  note "han muc tuan" "khong doc duoc (bo qua)"
fi

soonest=""
while read -r d; do
  [ -z "$d" ] && continue
  if [ -z "$soonest" ] || [ "$d" -lt "$soonest" ]; then soonest="$d"; fi
done < <(printf '%s' "$models" | grep -oP 'expires in \K[0-9]+(?=d)')
if [ -n "${soonest:-}" ]; then
  note "token het han som nhat" "${soonest} ngay nua"
  [ "$soonest" -le "$TOKEN_DAYS_FLOOR" ] && problems+=("Token OAuth het han trong ${soonest} ngay - can dang nhap lai")
else
  note "token" "khong doc duoc (bo qua)"
fi

if printf '%s' "$models" | grep -q "status=missing"; then
  problems+=("Runtime auth = missing - model khong dung duoc")
fi

# --- may chu --------------------------------------------------------------
note "dia trong" "$(df -h / | awk 'NR==2{print $4}')"
disk_pct=$(df / | awk 'NR==2{print $5}' | tr -d '%')
[ "$disk_pct" -ge 90 ] && problems+=("Dia day ${disk_pct}%")
note "sao luu" "$(ls -1 "$HOME"/backups/*.tar.gz 2>/dev/null | wc -l) ban"
[ "$(find "$HOME"/backups -name '*.tar.gz' -mtime -2 2>/dev/null | wc -l)" -eq 0 ] && problems+=("Khong co ban sao luu nao trong 2 ngay")

# --- ket luan -------------------------------------------------------------
echo
if [ ${#problems[@]} -eq 0 ]; then
  echo "  ==> BINH THUONG"
  exit 0
fi

echo "  ==> CO VAN DE:"
for p in "${problems[@]}"; do echo "      - $p"; done

if [ "${1:-}" = "--alert" ] && [ -n "$ALERT_TARGET" ]; then
  msg="Tin nhanh 247 - canh bao $(date '+%d/%m %H:%M')"
  for p in "${problems[@]}"; do msg="$msg"$'\n'"- $p"; done
  openclaw message send --channel telegram --target "$ALERT_TARGET" --message "$msg" >/dev/null 2>&1 \
    && echo "      (da gui canh bao toi $ALERT_TARGET)" \
    || echo "      (GUI CANH BAO THAT BAI)"
fi
exit 1
