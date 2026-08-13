#!/bin/sh
# CodeKeeper primer — SessionStart hook for Claude Code on the web.
#
# Fetches the agent instructions from CodeKeeper and prints them. Claude Code
# injects a SessionStart hook's stdout into the session context, so whatever
# lands on stdout is read before the first message.
#
# Why this file exists at all, when the codekeeper-memory plugin already ships
# the same hook: web session containers are started with
# SKIP_PLUGIN_MARKETPLACE=true, so no marketplace plugin is materialised there
# — no plugin directory, no CLAUDE_PLUGIN_ROOT, and the plugin's hook never
# runs. Account-level skills do sync (CLAUDE_CODE_SYNC_SKILLS=1); plugins do
# not. A hook committed to the repo is the delivery path that survives.
#
# Remote only, on purpose. Where plugins DO load, the plugin's own hook runs
# and this one would print the primer a second time. Gating on
# CLAUDE_CODE_REMOTE keeps exactly one copy on every surface, without a marker
# file that would also suppress the legitimate re-inject after a compact.
#
# stdout is context the agent reads; stderr is a message to the human. Nothing
# but the primer may reach stdout, or the agent reads our error text as if it
# were Amir's instructions. Silence is reserved for HTTP 204 — no instructions
# configured — because a hook that is silent on failure is indistinguishable
# from one that works, which is how a broken URL survives for months.
#
# Every path exits 0. A failure here must never stop a session from starting.

note() { printf 'codekeeper-primer: %s\n' "$1" >&2; }

# Where plugins load, the plugin owns this job.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

[ -n "${CODEKEEPER_PRIMER_URL:-}" ] || { note "CODEKEEPER_PRIMER_URL is not set — primer not loaded."; exit 0; }
[ -n "${CODEKEEPER_PAT:-}" ]        || { note "CODEKEEPER_PAT is not set — primer not loaded."; exit 0; }
command -v curl >/dev/null 2>&1     || { note "curl not found on PATH — primer not loaded."; exit 0; }

# The token is about to be sent to whatever this URL names, so refuse anything
# that is not https. A typo'd http:// would put the PAT on the wire in clear.
case "$CODEKEEPER_PRIMER_URL" in
  https://*) : ;;
  *) note "CODEKEEPER_PRIMER_URL must start with https:// — primer not loaded."; exit 0 ;;
esac

# Run from a directory that is not the repo: a hook fires unattended, and
# nothing here should ever resolve a relative path out of the working tree.
# Before mktemp, not after — a relative TMPDIR would otherwise put the temp
# file inside the repo, which is the thing this line exists to prevent.
cd / || exit 0

body=$(mktemp) || { note "could not create a temp file."; exit 0; }
trap 'rm -f "$body"' EXIT INT TERM

# No -f: the status code is the signal, not curl's exit status. --max-time 15
# sits under the hook's own 20s timeout, with room for a Render cold start: a
# sleeping server must cost seconds, never a hang.
#
# The token goes in through a config on stdin, not through -H. An argument is
# visible in ps and /proc/*/cmdline to anything else on the host for as long
# as curl runs; stdin is not.
#
# The config values must be quoted — a header value contains spaces, and curl
# warns and mangles the header without the quotes. Quoting means the value is
# escape-processed, so any backslash or quote inside the token is escaped
# first. A PAT has neither today, but a token that broke the parse would fail
# authentication with no hint as to why.
#
# --proto/--proto-redir pin the request to https even across a redirect, so a
# redirect cannot walk the Authorization header down to plaintext.
#
# curl's stderr is deliberately NOT discarded: on a connection failure its own
# line names the cause — could not resolve host, connection refused, timed out
# — which the 000 branch below cannot tell apart. It goes to stderr, so it can
# never reach the primer on stdout.
pat_escaped=$(printf '%s' "$CODEKEEPER_PAT" | sed 's/[\\"]/\\&/g')

code=$(
  printf 'header = "Authorization: Bearer %s"\nheader = "Accept: text/plain"\n' "$pat_escaped" |
  curl -K - -sS -o "$body" -w '%{http_code}' --max-time 15 \
    --proto '=https' --proto-redir '=https' \
    "$CODEKEEPER_PRIMER_URL"
) || code="000"

case "$code" in
  200)
    if [ -s "$body" ]; then
      cat "$body"
    else
      note "server returned 200 with an empty body — expected 204 for no instructions."
    fi
    ;;
  204) : ;;
  401|403) note "authentication rejected (HTTP $code) — check CODEKEEPER_PAT." ;;
  404)     note "primer endpoint not found (HTTP 404) — check CODEKEEPER_PRIMER_URL points at the MCP service, not the webapp." ;;
  000)     note "could not reach the primer endpoint (timeout or network error)." ;;
  *)       note "unexpected response (HTTP $code) from the primer endpoint." ;;
esac

exit 0
