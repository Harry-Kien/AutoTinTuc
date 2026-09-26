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
QUIET_HOURS_LIMIT=3       # no post for this long -> problem
WEEK_QUOTA_FLOOR=20       # percent
TOKEN_DAYS_FLOOR=3

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

# --- chu ky that bai -------------------------------------------------------
# Mot chu ky co the dang duoc tin roi van thoat ma loi, nghia la co tin KHAC
# khong gui duoc. Kieu hong nay tung keo dai mot tieng ma nguong "12 gio khong
# co tin" khong he bat duoc, vi van co tin di qua.
fails=$(journalctl --user -u fastnews247.service --since "2 hours ago" --no-pager -o cat 2>/dev/null | grep -c "Failed with result")
note "chu ky loi 2h qua" "$fails"
[ "$fails" -ge 2 ] && problems+=("$fails chu ky thoat ma loi trong 2 gio - co tin khong gui duoc")

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

# --- tai khoan subscription cho bac du phong ------------------------------
# openclaw khong doc duoc han muc tuan cua OpenAI ("Unsupported provider"),
# nen bot dung so tai khoan OAuth khong bi cooldown lam tin hieu thay the.
usable=$(printf '%s' "$models" | grep -oP 'openai:[^=,| ]+=OAuth \([^)]*\)(?! \[cooldown)' | wc -l)
note "tai khoan OAuth dung duoc" "$usable"
quota_file="$BOT_DIR/storage/fastnews247/subscription_quota.json"
mkdir -p "$(dirname "$quota_file")"
printf '{"at": %s, "usableProfiles": %s}\n' "$(date +%s)" "$usable" > "$quota_file.tmp" \
  && mv "$quota_file.tmp" "$quota_file"

# --- chi phi API va loi key -----------------------------------------------
ledger_report=$(python3 - "$BOT_DIR" <<'PY'
import datetime as dt, json, sys, time
root = sys.argv[1]
try:
    config = json.load(open(f"{root}/config/fastnews247.sources.json", encoding="utf-8"))
    ledger = json.load(open(f"{root}/storage/fastnews247/llm_ledger.json", encoding="utf-8"))
except (OSError, ValueError):
    print("note|chi phi API|chua co so ghi")
    sys.exit(0)
api = config.get("editorial", {}).get("openai", {})
today = dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).date().isoformat()
day = ledger.get("days", {}).get(today, {})
spend, cap = float(day.get("spendUsd", 0)), float(api.get("dailyBudgetUsd", 0))
tiers = ", ".join(f"{k} {v}" for k, v in sorted(day.get("byTier", {}).items())) or "chua goi"
print(f"note|chi phi API hom nay|{spend:.2f}/{cap:.2f} USD ({tiers})")
if cap and spend >= cap:
    print("problem|Da cham tran chi phi API hom nay - dang dung du phong")
if float(ledger.get("apiDisabledUntil", 0) or 0) > time.time():
    print(f"problem|OpenAI API dang bi tat ({ledger.get('lastApiError', '?')}) - kiem tra key/so du")
if day.get("errors", {}).get("no-api-key"):
    print("problem|Chua co OPENAI_API_KEY trong .env - bot dang dung dich may")
if float(ledger.get("subscriptionPausedUntil", 0) or 0) > time.time():
    print(f"note|subscription|tam dung ({ledger.get('lastSubscriptionError', '?')}) - dang dung API")
if float(ledger.get("apiPausedUntil", 0) or 0) > time.time():
    print(f"note|OpenAI API|tam dung ({ledger.get('lastApiPauseReason', '?')}) - dang dung du phong")
fatal = {"invalid_api_key", "insufficient_quota", "account_deactivated",
         "billing_hard_limit_reached", "billing_not_active"}
refused = sorted(k for k in day.get("errors", {})
                 if k in fatal or k.startswith("http-401") or k.startswith("http-403"))
if refused:
    print(f"problem|OpenAI API key bi tu choi hom nay ({', '.join(refused)}) - kiem tra key/so du")
PY
)
while IFS='|' read -r kind first second; do
  case "$kind" in
    note) note "$first" "$second" ;;
    problem) problems+=("$first") ;;
  esac
done <<< "$ledger_report"

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

send_alert() {
  local msg="$1" token
  token=$(sed -n 's/^FASTNEWS247_TELEGRAM_BOT_TOKEN=//p' "$BOT_DIR/.env" 2>/dev/null | head -1 | tr -d '\r"')
  if [ -n "$token" ]; then
    # The URL carries the token, so it goes to curl on stdin, not on argv.
    if printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$token" \
        | curl -s -m 20 --config - --data-urlencode "chat_id=$ALERT_TARGET" \
               --data-urlencode "text=$msg" | grep -q '"ok":true'; then
      return 0
    fi
  fi
  # Fallback through the gateway, for when the Bot API itself is the problem.
  openclaw message send --channel telegram --target "$ALERT_TARGET" --message "$msg" >/dev/null 2>&1
}

if [ "${1:-}" = "--alert" ] && [ -n "$ALERT_TARGET" ]; then
  msg="Tin nhanh 247 - canh bao $(date '+%d/%m %H:%M')"
  for p in "${problems[@]}"; do msg="$msg"$'\n'"- $p"; done
  send_alert "$msg" \
    && echo "      (da gui canh bao toi $ALERT_TARGET)" \
    || echo "      (GUI CANH BAO THAT BAI)"
fi
exit 1
