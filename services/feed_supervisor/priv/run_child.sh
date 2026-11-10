#!/bin/sh
# run_child.sh CMD [ARGS...] -- run CMD for the feed supervisor without orphans.
#
# * CMD runs in its own session/process group (setsid; a background job of a
#   non-interactive shell is never a group leader, so setsid execs in place and
#   $! is CMD's pid == its pgid). A group signal reaches CMD and its children.
# * The first stdout line is "__FEED_CHILD_PID__ <pgid>" so the supervisor can
#   SIGKILL the whole group if SIGTERM is ignored.
# * SIGTERM/SIGINT/SIGHUP to this wrapper are forwarded to the group.
# * stdin is the Erlang port's pipe. It closes when the port is closed or the
#   BEAM dies (even by SIGKILL), and then the group is TERMed, then KILLed
#   after FEED_KILL_GRACE_S. That is what keeps children from orphaning.
# * Exits with CMD's exit status.
# keep the port pipe on fd 3: a background job's stdin is forced to /dev/null
exec 3<&0
setsid "$@" </dev/null 3<&- &
child=$!
echo "__FEED_CHILD_PID__ $child"

# group first; the bare pid covers the instant before setsid has run
sig() { kill -"$1" -"$child" 2>/dev/null || kill -"$1" "$child" 2>/dev/null; }
term() { sig TERM; }
trap term TERM INT HUP

( while read -r _l; do :; done
  sig TERM
  sleep "${FEED_KILL_GRACE_S:-10}"
  sig KILL ) <&3 >/dev/null 2>&1 &
watcher=$!
exec 3<&-

rc=0
while :; do
  wait "$child"
  rc=$?
  kill -0 "$child" 2>/dev/null || break
done
kill "$watcher" 2>/dev/null
exit "$rc"
