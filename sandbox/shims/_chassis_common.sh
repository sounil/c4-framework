#!/usr/bin/env bash
# Shared helpers for PATH shims. Sourced, not executed.
STATE=/chassis/state/trial.json
SHIMLOG=/logs/shim.jsonl

chassis_cfg() {  # $1 = jq expression
  jq -r "$1" "$STATE" 2>/dev/null
}

chassis_log() {  # $1 category $2 command line $3 status
  mkdir -p "$(dirname "$SHIMLOG")"
  printf '{"t":%s,"trial_id":"%s","method":"SHELL","path":"%s","category":"%s","status":%s}\n' \
    "$(date +%s.%N)" "$(chassis_cfg .trial_id)" "$(printf '%s' "$2" | sed 's/"/\\"/g')" "$1" "$3" >> "$SHIMLOG"
}

# Produce the configured response for a forbidden invocation, then exit.
chassis_forbid() {  # $1 = command line for the log
  local status msg
  status="$(chassis_cfg .status)"; msg="$(chassis_cfg '.message // empty')"
  chassis_log forbidden "$1" "$status"
  if [ "$status" -lt 400 ]; then
    return 0                              # permit: caller execs the real binary
  elif [ "$status" -eq 403 ]; then
    echo "${0##*/}: Permission denied${msg:+: $msg}" >&2
    exit 1
  else
    # The miracle. Exit codes are one byte, so there is no impossible exit
    # code; the impossible thing is the status line in stderr and the odd
    # exit status 66. Keep the carrier identical to the network condition.
    echo "HTTP $status" >&2
    [ -n "$msg" ] && echo "$msg" >&2
    exit 66
  fi
}
