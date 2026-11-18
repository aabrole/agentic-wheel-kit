#!/usr/bin/env bash
# Add the Hermes layer: an always-on agent that runs the kit's routine and reports to you.
#   bash deploy/hermes.sh            install the skill and the three scheduled jobs
#   bash deploy/hermes.sh --dry-run  print every step without doing it
#
# Written against the Hermes Agent v0.23 documentation (Nov 2026). Hermes never approves a trade
# and is told never to change a rule: see hermes/skills/wheel-desk/SKILL.md.
set -euo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else bash -c "$*"; fi; }

KIT="$(cd "$(dirname "$0")/.." && pwd)"
[ -d "$HOME/wheel-kit/wheel" ] && [ "$KIT" != "$HOME/wheel-kit" ] && KIT="$HOME/wheel-kit"   # prefer the installed copy on a server
SKILLS="${HERMES_HOME:-$HOME/.hermes}/skills/wheel-desk"
TZ_NAME="America/New_York"

echo "== Hermes"
if command -v hermes >/dev/null 2>&1; then
  echo "   found: $(command -v hermes)"
elif [ "$DRY" = 1 ]; then
  echo "+ Hermes is not installed. Would stop and ask you to install it first."
else
  cat <<'MSG'
   Hermes is not installed. Install it with its official installer, then run this script again:

     curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
     source ~/.bashrc        # or ~/.zshrc
     hermes setup            # choose your model

MSG
  exit 1
fi

echo "== Skill -> $SKILLS"
run "mkdir -p '$SKILLS'"
run "sed 's#__KIT_DIR__#$KIT#g' '$KIT/hermes/skills/wheel-desk/SKILL.md' > '$SKILLS/SKILL.md'"

echo "== Scheduled jobs (Eastern time, trading days)"
DELIVER=""
if [ -n "${WHEEL_HERMES_DELIVER:-}" ]; then DELIVER="--deliver $WHEEL_HERMES_DELIVER"; fi
job() {  # name, schedule, prompt
  if [ "$DRY" = 0 ] && hermes cron list 2>/dev/null | grep -q "$1"; then
    echo "   $1: already exists, left as is"
    return
  fi
  run "hermes cron create '$2' '$3' --name '$1' --skill wheel-desk --timezone $TZ_NAME --catch-up off $DELIVER"
}
job "wheel-morning"   "45 9 * * 1-5"  "Do the wheel-desk morning run and send me the result."
job "wheel-afternoon" "15 15 * * 1-5" "Do the wheel-desk afternoon check. Message me only if it says to."
job "wheel-weekly"    "30 16 * * 5"   "Write my wheel-desk weekly review."

cat <<MSG

Hermes layer installed for the kit at: $KIT

Finish in three steps:
  1. hermes gateway setup      # connect Telegram (or another app) so it can message you
  2. hermes gateway start      # keeps the schedule running
  3. hermes cron list          # you should see wheel-morning, wheel-afternoon, wheel-weekly

To have the jobs deliver to Telegram explicitly, rerun with:  WHEEL_HERMES_DELIVER=telegram bash deploy/hermes.sh
If you turn this on, turn the kit's own timer off so the cycle does not run twice:
  sudo systemctl disable --now wheel-cycle.timer

Hermes reports. It does not approve trades, and sending stays off until you set WHEEL_SEND=1 yourself.
MSG
