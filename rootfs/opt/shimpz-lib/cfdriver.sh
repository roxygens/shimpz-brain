# shellcheck shell=bash
# cfdriver.sh — thin HTTP client for cf-driver (SECURITY_ENGINEERING_PLAN.md item 3).
# shimpz-publish/shimpz-unpublish/shimpzdetect.sh call this instead of the old permissive `cf` helper —
# `shimpz-brain` never holds SHIMPZ_CF_TOKEN, only this restricted, audited, narrowly-scoped HTTP API.
#
# Sourced as: . "${SHIMPZ_LIB:-/opt/shimpz-lib}/cfdriver.sh"   (definitions only, no side effects)

SHIMPZ_CFDRIVER_URL="${SHIMPZ_CFDRIVER_URL:-http://cf-driver:7071}"
SHIMPZ_CFDRIVER_TOKEN_FILE="${SHIMPZ_CFDRIVER_TOKEN_FILE:-/run/shimpz-cfdriver/token}"

_cfdriver_token(){
  cat "$SHIMPZ_CFDRIVER_TOKEN_FILE" 2>/dev/null \
    || { echo "cfdriver: cannot read cf-driver token ($SHIMPZ_CFDRIVER_TOKEN_FILE)" >&2; return 1; }
}

# _cfdriver <method> <path> [json-body]  → prints the response body on 2xx; on a non-2xx status
# prints "method path -> HTTP code: body" to stderr and returns 1, so callers under `set -e`
# fail loudly instead of silently continuing past a rejected request. Same contract as
# shimpzdriver.sh's own _driver() — deliberately identical shape, two independent sidecars.
_cfdriver(){
  local method="$1" path="$2" body="${3:-}" token auth_config raw code resp
  token=$(_cfdriver_token) || return 1
  # curl reads the bearer from fd 3, never argv. Request JSON likewise travels on stdin: app/domain
  # metadata and future sensitive fields cannot leak through /proc/<curl>/cmdline.
  auth_config=$(printf 'header = "Authorization: Bearer %s"\n' "$token")
  if [ -n "$body" ]; then
    raw=$(curl -sS -w '\n%{http_code}' -X "$method" "$SHIMPZ_CFDRIVER_URL$path" \
      --config /dev/fd/3 -H 'Content-Type: application/json' --data-binary @- \
      3<<<"$auth_config" <<<"$body")
  else
    raw=$(curl -sS -w '\n%{http_code}' -X "$method" "$SHIMPZ_CFDRIVER_URL$path" \
      --config /dev/fd/3 3<<<"$auth_config")
  fi
  code="${raw##*$'\n'}"; resp="${raw%$'\n'*}"
  case "$code" in
    2??) printf '%s' "$resp"; return 0 ;;
    *) echo "cfdriver: $method $path -> HTTP $code: $resp" >&2; return 1 ;;
  esac
}

# _cfdriver_zone_for <fqdn>  → prints "<zone-name> <zone-id>", or nothing (rc 1) when no zone
# matches. SAME output shape as the old shimpzdetect.sh::_zone_for (longest-suffix match, now done
# server-side in drivers/cf/validate.py) so shimpz-publish/shimpz-unpublish need no other changes
# at their call sites.
_cfdriver_zone_for(){
  local fqdn="$1" out
  out=$(_cfdriver GET "/v1/zones/resolve?fqdn=$fqdn") || return 1
  echo "$out" | jq -r '"\(.zone_name) \(.zone_id)"'
}

_cfdriver_tunnel_id(){  # prints the active tunnel id, or nothing (rc 1) when the GET itself failed
                      # (e.g. no active tunnel — cf-driver returns 404) — explicit `|| return 1`
                      # rather than piping straight into jq, so a failed call can never be silently
                      # swallowed into an empty-but-successful pipeline under `set -o pipefail`.
  local out
  out=$(_cfdriver GET "/v1/tunnel") || return 1
  echo "$out" | jq -r '.tunnel_id // empty'
}

# _cfdriver_ingress_upsert <hostname> <service>  → JSON {previous_service, rule_count}. The ENTIRE
# read-modify-write (keep every other rule, replace/add this hostname's rule, preserve the
# catch-all) happens server-side now — shimpz-brain never sees or manipulates the full ingress config.
_cfdriver_ingress_upsert(){
  local hostname="$1" service="$2" body
  body=$(jq -n --arg h "$hostname" --arg s "$service" '{hostname:$h, service:$s}')
  _cfdriver POST "/v1/tunnel/ingress-rule" "$body"
}
_cfdriver_ingress_delete(){ _cfdriver DELETE "/v1/tunnel/ingress-rule/$1"; }  # $1=hostname

# _cfdriver_dns_upsert <fqdn> <type> <content>  → JSON {record_id, created, previous_content, zone}
_cfdriver_dns_upsert(){
  local fqdn="$1" type="$2" content="$3" body
  body=$(jq -n --arg f "$fqdn" --arg t "$type" --arg c "$content" '{fqdn:$f, type:$t, content:$c}')
  _cfdriver POST "/v1/dns/upsert" "$body"
}
_cfdriver_dns_delete(){ _cfdriver DELETE "/v1/dns/record?fqdn=$1&type=$2"; }  # $1=fqdn $2=type

_cfdriver_access_private(){  # $1=fqdn $2=owner_email -> JSON {created, app_id}
  local fqdn="$1" email="$2" body
  body=$(jq -n --arg f "$fqdn" --arg e "$email" '{fqdn:$f, owner_email:$e}')
  _cfdriver POST "/v1/access/private" "$body"
}
_cfdriver_access_public(){  # $1=fqdn -> JSON {removed: [app, ...]}
  _cfdriver POST "/v1/access/public" "$(jq -n --arg f "$1" '{fqdn:$f}')"
}
_cfdriver_access_restore(){  # $1=app-json (a single object THIS driver returned earlier, verbatim)
  _cfdriver POST "/v1/access/restore" "$(jq -n --argjson a "$1" '{app:$a}')"
}
