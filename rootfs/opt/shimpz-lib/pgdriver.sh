# shellcheck shell=bash
# pgdriver.sh — thin HTTP client for pg-driver (SECURITY_ENGINEERING_PLAN.md item 2).
# shimpz-db calls this instead of running psql/createdb/dropdb directly as the Postgres superuser —
# `shimpz-brain` never holds SHIMPZ_PG_DSN, only this restricted, audited, narrowly-scoped HTTP API.
#
# Sourced as: . "${SHIMPZ_LIB:-/opt/shimpz-lib}/pgdriver.sh"   (definitions only, no side effects)

SHIMPZ_PGDRIVER_URL="${SHIMPZ_PGDRIVER_URL:-http://pg-driver:7072}"
SHIMPZ_PGDRIVER_TOKEN_FILE="${SHIMPZ_PGDRIVER_TOKEN_FILE:-/run/shimpz-pgdriver/token}"

_pgdriver_token(){
  cat "$SHIMPZ_PGDRIVER_TOKEN_FILE" 2>/dev/null \
    || { echo "pgdriver: cannot read pg-driver token ($SHIMPZ_PGDRIVER_TOKEN_FILE)" >&2; return 1; }
}

# _pgdriver <method> <path> [json-body]  → prints the response body on 2xx; on a non-2xx status
# prints "method path -> HTTP code: body" to stderr and returns 1, so callers under `set -e`
# fail loudly instead of silently continuing past a rejected request. Same contract as
# cfdriver.sh's/shimpzdriver.sh's own client function — deliberately identical shape, independent sidecars.
_pgdriver(){
  local method="$1" path="$2" body="${3:-}" token raw code resp
  token=$(_pgdriver_token) || return 1
  if [ -n "$body" ]; then
    raw=$(curl -sS -w '\n%{http_code}' -X "$method" "$SHIMPZ_PGDRIVER_URL$path" \
      -H "Authorization: Bearer $token" -H 'Content-Type: application/json' -d "$body")
  else
    raw=$(curl -sS -w '\n%{http_code}' -X "$method" "$SHIMPZ_PGDRIVER_URL$path" -H "Authorization: Bearer $token")
  fi
  code="${raw##*$'\n'}"; resp="${raw%$'\n'*}"
  case "$code" in
    2??) printf '%s' "$resp"; return 0 ;;
    *) echo "pgdriver: $method $path -> HTTP $code: $resp" >&2; return 1 ;;
  esac
}

# _pgdriver_create <name>  → JSON {database_url, created}
_pgdriver_create(){
  _pgdriver POST "/v1/db/create" "$(jq -n --arg n "$1" '{name:$n}')"
}

# _pgdriver_url <name>  → prints the bare DATABASE_URL string (not JSON) — this is the project's OWN
# least-privilege proj_<name> credential, never the superuser's; safe for `shimpz-db psql`/callers to
# connect with directly.
_pgdriver_url(){
  local out
  out=$(_pgdriver GET "/v1/db/url?name=$1") || return 1
  echo "$out" | jq -r '.database_url'
}

# _pgdriver_list  → newline-separated proj_* database names
_pgdriver_list(){
  local out
  out=$(_pgdriver GET "/v1/db/list") || return 1
  echo "$out" | jq -r '.databases[]'
}
# _pgdriver_query <name> <sql>  → prints the response JSON {csv, rows, truncated}. READ-ONLY (RO role);
# the brain reads (e.g. leads) via the driver WITHOUT a direct postgres route (SECURITY item 8).
_pgdriver_query(){
  _pgdriver POST "/v1/db/query" "$(jq -n --arg n "$1" --arg s "$2" '{name:$n, sql:$s}')"
}

# _pgdriver_drop <name>  → JSON {dropped}. Callers keep their OWN shimpz-approve gate before calling
# this — the sidecar just executes the drop when asked, same as cf-driver never itself
# prompting for approval.
_pgdriver_drop(){
  _pgdriver POST "/v1/db/drop" "$(jq -n --arg n "$1" '{name:$n}')"
}
