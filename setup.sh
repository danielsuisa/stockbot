#!/usr/bin/env bash
# InvestorBot setup: Telegram token -> chat id -> GitHub secrets -> test message. Needs bash + curl; gh is optional.
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"  # started as "sh setup.sh"
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n'; printf '%s\n' "$@"; }
ask() {  # ask "prompt" -> $REPLY without whitespace (strips the CR of Windows pastes); EOF aborts
  printf '\n%s ' "$1"
  IFS= read -r REPLY || [ -n "$REPLY" ] || { say "Aborted / בוטל"; exit 1; }
  REPLY=$(printf '%s' "$REPLY" | tr -d '[:space:]')
}
tg() { curl -fsS --max-time 40 "https://api.telegram.org/bot$TOKEN/$1" "${@:2}"; }

say "=== InvestorBot setup / הגדרת הבוט ==="
while :; do
  ask "1) הדביקו את הטוקן שקיבלתם מ-@BotFather / Paste the bot token from @BotFather:"
  TOKEN=$REPLY
  if [[ $TOKEN =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] && ME=$(tg getMe 2>/dev/null); then break; fi
  say "❌ הטוקן לא תקין - העתיקו אותו שוב / Invalid token - copy it again from @BotFather"
done
BOT=$(printf '%s' "$ME" | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')
say "✅ הבוט נמצא / Bot found: @$BOT"

say "2) פתחו בטלגרם את הבוט ולחצו Start (או שלחו לו הודעה כלשהי)" \
    "   Open https://t.me/$BOT in Telegram and press Start / send any message. Waiting up to 2 minutes..."
CHAT=""
for _ in $(seq 24); do  # long polling: returns as soon as a message arrives
  CHAT=$(tg "getUpdates?timeout=5" 2>/dev/null | grep -Eo '"chat":[{]"id":-?[0-9]+' | tail -n 1 \
    | grep -Eo -- '-?[0-9]+$' || true)
  [ -n "$CHAT" ] && break
done
if [ -z "$CHAT" ]; then
  say "לא הגיעה הודעה. פתחו בדפדפן את הכתובת וחפשו את המספר שאחרי \"chat\":{\"id\":" \
      "   No message seen. Open this in a browser and find the number after \"chat\":{\"id\":" \
      "   https://api.telegram.org/bot$TOKEN/getUpdates"
  until [[ $CHAT =~ ^-?[0-9]+$ ]]; do ask "הקלידו את המספר / Type the chat id:"; CHAT=$REPLY; done
fi
say "✅ Chat ID: $CHAT"

EMAIL=""
until [[ $EMAIL =~ ^[^@]+@[^@]+\.[^@]+$ ]]; do
  ask "3) אימייל ליצירת קשר (SEC דורש אותו) / Contact email for the SEC User-Agent:"
  EMAIL=$REPLY
done
UA="InvestorBot $EMAIL"

REPO=""
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)
fi
# secrets go through stdin, not argv
if [ -n "$REPO" ] && printf '%s' "$TOKEN" | gh secret set TG_TOKEN -R "$REPO" \
    && printf '%s' "$CHAT" | gh secret set TG_CHAT_ID -R "$REPO" \
    && printf '%s' "$UA" | gh secret set SEC_UA -R "$REPO"; then
  say "✅ שלושת הסודות נשמרו ב-GitHub / 3 secrets saved to $REPO"
else
  say "4) הוסיפו ב-GitHub שלושה סודות (העתיקו בדיוק) / Add 3 repository secrets on GitHub (copy exactly):" \
      "   Settings → Secrets and variables → Actions → New repository secret" \
      "   Name: TG_TOKEN     Secret: $TOKEN" \
      "   Name: TG_CHAT_ID   Secret: $CHAT" \
      "   Name: SEC_UA       Secret: $UA"
fi

MSG="✅ <b>הבוט מחובר!</b>
מעכשיו יגיעו לכאן התראות יומיות על רכישות של בעלי עניין, ודוח פורנזי לכל טיקר שתשלחו (למשל <code>AAPL</code>).

⚠️ מידע לצורכי מחקר בלבד ואינו ייעוץ השקעות. מקור הנתונים: SEC EDGAR."
if tg sendMessage --data-urlencode "chat_id=$CHAT" --data-urlencode "text=$MSG" -d parse_mode=HTML >/dev/null; then
  say "✅ נשלחה הודעת בדיקה - בדקו בטלגרם / Test message sent - check Telegram"
else
  say "⚠️ הודעת הבדיקה נכשלה - בדקו את ה-chat id / Test message failed - check the chat id"
fi

say "5) אחרון: בלשונית Actions של המאגר אשרו את הפעלת ה-workflows" \
    "   Last step: enable workflows in the repo's Actions tab${REPO:+: https://github.com/$REPO/actions}" \
    "   בדיקה: Actions → Check ticker → Run workflow → AAPL / Test: run \"Check ticker\" with AAPL"
