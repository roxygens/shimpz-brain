# shellcheck shell=bash
# shimpzdriver.sh — thin HTTP client for shimpz-driver (SECURITY_ENGINEERING_PLAN.md 1.1).
# shimpz-app/shimpz-route/shimpz-publish call this instead of supervisorctl/local Caddy — `shimpz-brain` never
# touches Docker or Caddy's admin API directly, only this restricted, audited HTTP API.
#
# Sourced as: . "${SHIMPZ_LIB:-/opt/shimpz-lib}/shimpzdriver.sh"   (definitions only, no side effects)

SHIMPZ_DRIVER_URL="${SHIMPZ_DRIVER_URL:-http://shimpz-driver:7070}"
SHIMPZ_DRIVER_TOKEN_FILE="${SHIMPZ_DRIVER_TOKEN_FILE:-/run/shimpz-driver/token}"

_driver_token(){
  cat "$SHIMPZ_DRIVER_TOKEN_FILE" 2>/dev/null \
    || { echo "shimpzdriver: cannot read driver token ($SHIMPZ_DRIVER_TOKEN_FILE)" >&2; return 1; }
}

# _driver <method> <path> [json-body]  → prints the response body on 2xx; on a non-2xx status
# prints "method path -> HTTP code: body" to stderr and returns 1, so callers under `set -e`
# fail loudly instead of silently deploying against a rejected request.
_driver(){
  local method="$1" path="$2" body="${3:-}" token auth_config raw code resp
  token=$(_driver_token) || return 1
  # Keep both the bearer and request JSON outside curl's argv. The fixed fd/config path and stdin
  # remain visible, but their contents do not appear in /proc/<curl>/cmdline.
  auth_config=$(printf 'header = "Authorization: Bearer %s"\n' "$token")
  if [ -n "$body" ]; then
    raw=$(curl -sS -w '\n%{http_code}' -X "$method" "$SHIMPZ_DRIVER_URL$path" \
      --config /dev/fd/3 -H 'Content-Type: application/json' --data-binary @- \
      3<<<"$auth_config" <<<"$body")
  else
    raw=$(curl -sS -w '\n%{http_code}' -X "$method" "$SHIMPZ_DRIVER_URL$path" \
      --config /dev/fd/3 3<<<"$auth_config")
  fi
  code="${raw##*$'\n'}"; resp="${raw%$'\n'*}"
  case "$code" in
    2??) printf '%s' "$resp"; return 0 ;;
    *) echo "shimpzdriver: $method $path -> HTTP $code: $resp" >&2; return 1 ;;
  esac
}

# First token of the run command decides the runtime image — matches validate.py's
# ALLOWED_IMAGES keys exactly (only "python"/"node" exist; everything else is python, since a
# static build is served via `python -m http.server`, not its own runtime).
_driver_image_kind(){
  case "$1" in node|pnpm) echo node ;; *) echo python ;; esac
}

# _driver_deploy <name> <port> <persist:0|1> <worker:0|1> <env-json-object> <calls-csv> <egress-csv> -- <argv...>
# worker=1 tells the driver's OWN health check (blue-green cutover gate) to confirm "stays
# running" instead of probing HTTP — a --worker app has no HTTP surface by contract, and the
# driver's transactional redeploy runs a real health check BEFORE ever cutting traffic
# over, so it must know which kind of check applies (SECURITY_ENGINEERING_PLAN.md item 2).
# calls-csv = the app's DECLARED `shimpzbus.call` targets ("" for none): the driver wires each
# provider into this app's network — an undeclared cross-service call fails DNS by design.
# egress-csv = the app's effective_egress (external hosts it may reach, "" for none): the driver's
# L2 lock (when active) allows the app's proxy exactly these hosts — deny-by-default otherwise.
_driver_deploy(){
  local name="$1" port="$2" persist="$3" worker="$4" envjson="$5" callscsv="$6" egresscsv="$7"; shift 7
  [ "${1:-}" = "--" ] && shift
  local kind entrypoint persist_bool worker_bool callsjson egressjson body
  kind=$(_driver_image_kind "$1")
  entrypoint=$(printf '%s\n' "$@" | jq -R . | jq -s .)
  [ "$persist" = 1 ] && persist_bool=true || persist_bool=false
  [ "$worker" = 1 ] && worker_bool=true || worker_bool=false
  if [ -n "$callscsv" ]; then
    callsjson=$(printf '%s' "$callscsv" | jq -R 'split(",") | map(select(length > 0))')
  else
    callsjson="[]"
  fi
  if [ -n "$egresscsv" ]; then
    egressjson=$(printf '%s' "$egresscsv" | jq -R 'split(",") | map(select(length > 0))')
  else
    egressjson="[]"
  fi
  body=$(jq -n --arg k "$kind" --argjson ep "$entrypoint" --argjson port "$port" \
    --argjson env "$envjson" --argjson persist "$persist_bool" --argjson worker "$worker_bool" \
    --argjson calls "$callsjson" --argjson egress "$egressjson" \
    '{image_kind:$k, entrypoint:$ep, port:($port|tonumber), env:$env, persist:$persist, worker:$worker, calls:$calls, egress:$egress}')
  _driver POST "/v1/apps/$name/deploy" "$body"
}

_driver_lifecycle(){ _driver POST "/v1/apps/$1/$2"; }      # $2 = stop|start|restart
_driver_status(){ _driver GET "/v1/apps/$1/status"; }
# lines must be NUMERIC before it reaches the query string — a literal flag (e.g. "--lines")
# used to travel as ?lines=--lines and die server-side as an opaque audit "error" (Round 125).
_driver_logs(){
  case "${2:-80}" in ''|*[!0-9]*)
    echo "shimpzdriver: logs <app> [lines] — lines must be a number, got '${2:-}'" >&2; return 2;; esac
  _driver GET "/v1/apps/$1/logs?lines=${2:-80}"
}
_driver_health(){ _driver GET "/v1/apps/$1/health"; }
_driver_rm(){  # $1=name $2=purge_volume(0|1)
  local q=""; [ "${2:-0}" = 1 ] && q="?purge_volume=1"
  _driver DELETE "/v1/apps/$1$q"
}

# _driver_route_apply <fqdn> <web-target> <web-port> [api-target] [api-port] [ws-target] [ws-port]
# web/api/ws routinely point at THREE DIFFERENT app containers (a fullstack project's static
# front, API backend, and ws gateway are three separate `shimpz-app deploy`s) — never assume one.
_driver_route_apply(){
  local fqdn="$1" wt="$2" wp="$3" at="${4:-}" ap="${5:-}" st="${6:-}" sp="${7:-}" body
  body=$(jq -n --arg f "$fqdn" --arg wt "$wt" --argjson wp "$wp" \
    --arg at "${at:-null}" --argjson ap "${ap:-null}" --arg st "${st:-null}" --argjson sp "${sp:-null}" \
    '{fqdn:$f, web_target:$wt, web_port:$wp,
      api_target:(if $at=="null" then null else $at end), api_port:$ap,
      ws_target:(if $st=="null" then null else $st end), ws_port:$sp}')
  _driver POST "/v1/routes/apply" "$body"
}
_driver_route_del(){ _driver DELETE "/v1/routes/$1"; }
_driver_route_list(){ _driver GET "/v1/routes"; }
_driver_apps(){ _driver GET "/v1/apps"; }

# _driver_app_for_port <port>  → prints the app name that owns this port (empty if none) — the
# ONLY way `shimpz-brain` (no Docker access of its own) resolves port→app for building route targets.
_driver_app_for_port(){
  _driver_apps 2>/dev/null | jq -r --arg p "$1" '.apps[] | select(.port == $p) | .name' | head -1
}
