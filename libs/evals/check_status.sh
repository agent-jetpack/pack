#!/usr/bin/env bash
# Quick status checker for TB2 run
JOBS=$(ls -dt /Users/c/dev/pack/libs/evals/jobs/tb2-full-submission/*/ 2>/dev/null | head -1)
[ -z "$JOBS" ] && echo "No active run found" && exit 1

pass=0; fail=0; err=0; running=0; timeout=0
pass_list=""; fail_list=""
for d in "$JOBS"/*__*/; do
  [ -d "$d" ] || continue
  task=$(basename "$d" | sed 's/__.*//')
  r="$d/verifier/reward.txt"
  if [ -f "$r" ]; then
    val=$(cat "$r" | tr -d '[:space:]')
    if [ "$val" = "1" ]; then pass=$((pass+1)); pass_list="$pass_list  ✓ $task\n"
    else fail=$((fail+1)); fail_list="$fail_list  ✗ $task\n"
    fi
  elif [ -f "$d/result.json" ]; then err=$((err+1))
  else running=$((running+1))
  fi
done
total=$(ls -d "$JOBS"/*__*/ 2>/dev/null | wc -l | tr -d ' ')
infra=$(grep 'disk limit\|memory limit\|Failed to create sandbox' "$JOBS/job.log" 2>/dev/null | wc -l | tr -d ' ')
api=$(grep 'PaymentRequired\|credits\|rate limit' "$JOBS/job.log" 2>/dev/null | wc -l | tr -d ' ')
agent_to=$(grep 'AgentTimeoutError' "$JOBS/job.log" 2>/dev/null | wc -l | tr -d ' ')

echo "═══ TB2 Status — $(date +%H:%M) ═══"
echo "Dispatched: $total/445 | PASS: $pass | FAIL: $fail | ERROR: $err | RUNNING: $running"
echo "Infra errors: $infra | API errors: $api | Agent timeouts: $agent_to"
[ $pass -gt 0 ] && echo -e "\nPassed:\n$pass_list"
[ $fail -gt 0 ] && echo -e "Failed:\n$fail_list"
