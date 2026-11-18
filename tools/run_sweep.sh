#!/bin/zsh
# One sweep: read the job sources, fill in posting text, rebuild the dashboard.
# Called by the widget and by Claude runs. Only one copy runs at a time; exits 75 if another holds the lock.
cd "$(dirname "$0")/.." || exit 1
PY=/opt/anaconda3/bin/python3
[ -x "$PY" ] || PY=/usr/bin/python3
mkdir -p data
if [ -z "$RADAR_LOCKED" ]; then
  RADAR_LOCKED=1 exec /usr/bin/lockf -s -t 0 data/.sweep.lock /bin/zsh "$0" "$@"
fi
rc=0
{
  echo "== $(date '+%Y-%m-%d %H:%M') sweep"
  "$PY" tools/sweep_boards.py || rc=$?
  "$PY" tools/fetch_details.py || rc=$?
  "$PY" tools/search.py || true          # Tavily, at most once per 20 h; skipped without a key
  "$PY" tools/triage.py --new || true    # Tier-1 cards; skipped without model keys
  "$PY" tools/build_dashboard.py || rc=$?
  echo "== exit $rc"
} >> data/sweep.out 2>&1
tail -n 400 data/sweep.out > data/sweep.out.tmp && mv data/sweep.out.tmp data/sweep.out
exit $rc
