"""shimpzmanifest — the Shimpz app manifest (`shimpz.app.toml`): parse, validate, normalise.

ONE declarative file per app, at the project root. It states what the app IS (name/title/summary) and
what it NEEDS: native integrations that must be enabled, other Shimpz apps it depends on, and service APIs
it reuses. The toolchain reads it HERE (shimpz-new writes it, shimpz-app resolves it, the panel surfaces it) so
a developer declares everything in one place and the EXISTING machinery is driven from it — never a
parallel system:

    [needs].native  → checked against the marketplace enable-state (integrations.json / .env)
    [needs].apps    → app→app dependency: install the app if missing (the one net-new primitive)
    [needs].calls   → compiles straight into `shimpz-app deploy --calls` (sync reach, R128)
    [config]        → the app's OWN env keys, an app-scoped allowlist on top of the infra keys

This module is a stateless leaf (stdlib `tomllib` only) so shimpz-app, shimpz-new and the admin panel can all
import it without pulling in each other's plane. It validates SHAPE and NAMING; a resolver with live
access (shimpz-app) validates SEMANTICS (is that native integration actually enabled, is that app deployed).
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "shimpz.app.toml"

# A project/app slug — identical to shimpz-new's NAME_RE, so the manifest name IS the deploy identity.
NAME_RE = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*\Z")
# A `--calls` target — the exact charset drivers/apps/validate.py::validate_calls accepts.
CALL_RE = re.compile(r"[A-Za-z0-9_-]{1,40}\Z")
# An app config key — UPPER_SNAKE (it becomes an env var alongside the app's own .env).
CONFIG_KEY_RE = re.compile(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*\Z")
# A [provides].skill path — a safe project-relative `.md` file (no traversal, no absolute path). The
# deploy copies it into the brain's skills dir, so it must never escape the project (path-injection).
SKILL_PATH_RE = re.compile(r"[A-Za-z0-9._/-]+\.md\Z")
# A [grants].consume topic — a FOREIGN bus topic `<project>.<name>` this app requests read access to.
GRANT_TOPIC_RE = re.compile(r"[a-z0-9_]+\.[a-z0-9_.-]+\Z")
# A [[run]] step's deploy name (this app or one of its role-services, e.g. `<app>-backend`).
RUN_NAME_RE = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*\Z")
# The binaries a [[run]] step may launch — mirrors drivers/apps/validate.py::ALLOWED_ENTRYPOINT_BINS
# (+ shimpz_static, the static-frontend server). `shimpz-app install` only ever launches one of these, so a
# manifest can't smuggle an arbitrary command through the installer (defense-in-depth; deploy re-checks).
RUN_BINS = frozenset({"uv", "uvicorn", "python", "python3", "pnpm", "node", "shimpz_static"})
# A [billing].currency — a lowercase 3-letter ISO-4217 code (usd/brl/eur…).
CURRENCY_RE = re.compile(r"[a-z]{3}\Z")
# A [needs].egress host — a lowercase DNS hostname the app declares it may reach (deny-by-default egress).
EGRESS_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+\Z")

# Native integrations an app may depend on — the marketplace CAPABILITY groups.
# MIRROR of apps/admin/backend/catalog.py's CAPABILITY-category groups (drift-locked in the test).
NATIVE_GROUPS = frozenset({"openai", "storage-r2", "cloudflare", "github", "proxy", "extra-models", "shimpzpay"})

# The marketplace publish namespace: an app that requests a publish/dns grant is PINNED to this subdomain
# under its own name — <name>.grid.shimpz.com — and nothing else (the enforced scope of the grant).
GRID_SUFFIX = "grid.shimpz.com"

# Config keys an app must NOT declare: the infra keys the deploy pipeline manages for it (mirror of
# drivers/apps/validate.py::ALLOWED_ENV_KEYS) — redeclaring them would fight the platform.
RESERVED_CONFIG_KEYS = frozenset(
    {
        "PORT",
        "HOST",
        "DATABASE_URL",
        "SHIMPZ_BUS_BROKERS",
        "SHIMPZ_BUS_SASL_USERNAME",
        "SHIMPZ_BUS_SASL_PASSWORD",
        "SHIMPZ_BUS_SASL_MECHANISM",
        "SECRET_KEY",
    }
)

# Global secrets an app must NEVER redeclare as its own config (mirror of the non-`SHIMPZ_` entries in
# drivers/apps/validate.py::FORBIDDEN_ENV_KEYS; the `SHIMPZ_`-prefixed ones are already refused). An
# app REQUESTS a capability with [needs].native and lets Shimpz hold the secret — it never carries one.
FORBIDDEN_CONFIG_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "VOICE_TOOLS_OPENAI_KEY",
        "GITHUB_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
    }
)

# Payment-processor credentials an app must NEVER carry — payment is STRUCTURALLY locked to ShimpzPay
# (a paid app declares [billing] and charges via the pay-driver; it never holds a processor key).
# This is defense-in-depth atop the real lock (L2, the app-egress pin): a curated list of the common
# processors' credential env-var names. Kept in sync with shimpz-app's _FORBIDDEN_PAYMENT_ENV_KEYS.
PAYMENT_KEYS = frozenset(
    {
        "STRIPE_API_KEY", "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET",
        "STRIPE_RESTRICTED_KEY", "PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "PAYPAL_SECRET",
        "BRAINTREE_PRIVATE_KEY", "ADYEN_API_KEY", "RAZORPAY_KEY_SECRET", "MERCADOPAGO_ACCESS_TOKEN",
        "PAGARME_API_KEY", "MOLLIE_API_KEY", "PADDLE_API_KEY", "LEMONSQUEEZY_API_KEY", "SQUARE_ACCESS_TOKEN",
    }
)

# The ShimpzPay host — the ONLY payment host an app may reach, and only when it declared [billing]
# (added to its egress allowlist by effective_egress). It is never listed manually in [needs].egress.
PAY_HOST = "pay.shimpz.com"
# Payment-processor API hosts an app may NOT declare in [needs].egress — payment is locked to ShimpzPay.
# (PAY_HOST is included so it can't be named directly either; a paid app reaches it via [billing].)
PAYMENT_HOSTS = frozenset(
    {
        "api.stripe.com", "js.stripe.com", "checkout.stripe.com", "api.paypal.com", "api-m.paypal.com",
        "api.braintreegateway.com", "api.adyen.com", "checkout.adyen.com", "api.razorpay.com",
        "api.mercadopago.com", "api.pagar.me", "api.mollie.com", "api.paddle.com", "api.lemonsqueezy.com",
        "connect.squareup.com", PAY_HOST,
    }
)

_TOP_KEYS = frozenset(
    {"name", "title", "version", "summary", "needs", "grants", "provides", "billing", "run", "config"}
)
_NEEDS_KEYS = frozenset({"native", "apps", "calls", "egress"})
_PROVIDES_KEYS = frozenset({"skill"})
_RUN_ITEM_KEYS = frozenset({"name", "port", "command"})
# The permissions an app may request. CORE RULE: only grants that are ACTUALLY ENFORCED live here — a
# declared-but-unenforced permission would be false security. `consume` (a foreign bus topic) is enforced
# by a narrow Redpanda ACL (shimpz-bus grant / bus-driver). `publish`/`dns` are enforced by pinning the
# app to <name>.grid.shimpz.com in the publish path (shimpz-publish scope guard). `telegram` (a per-app
# approval channel in the live gateway) joins this set only once its enforcement ships.
_GRANTS_KEYS = frozenset({"consume", "publish", "dns"})
# [billing] — a paid app declares its price and is STRUCTURALLY locked to ShimpzPay: `pay` accepts ONLY
# "shimpzpay" (the platform is the sole rail — pay.shimpz.com; any other value is refused, no false
# payment), mirroring grants' "own"-only. Enforced further by L1 (no processor key in config) + L2 (app
# egress can't reach a processor) + L3 (the pay-driver holds the merchant secret).
_BILLING_KEYS = frozenset({"pay", "price", "currency", "period"})
_BILLING_PERIODS = frozenset({"monthly", "yearly", "once"})
_CONFIG_ITEM_KEYS = frozenset({"default", "secret", "help"})


class ManifestError(ValueError):
    """A `shimpz.app.toml` that is missing, malformed, or breaks a naming/shape rule (fail-loud)."""


@dataclass(frozen=True)
class ConfigItem:
    """One `[config]` entry: a per-app env var the installer prompts for."""

    key: str
    default: str = ""
    secret: bool = False
    help: str = ""


@dataclass(frozen=True)
class RunStep:
    """One `[[run]]` step: how `shimpz-app install` deploys one service of the app (name/port/command)."""

    name: str
    port: int
    command: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    """A parsed, validated `shimpz.app.toml`. `needs`/`config` are empty when the sections are absent."""

    name: str
    title: str
    version: str
    summary: str
    native: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    egress: tuple[str, ...] = ()  # [needs].egress — external hosts the app may reach (deny-by-default allowlist)
    provides_skill: str = ""  # [provides].skill — project-relative path to the SKILL.md the app ships ("" = none)
    grants_consume: tuple[str, ...] = ()  # [grants].consume — foreign bus topics this app may read (enforced ACL)
    grants_publish: bool = False  # [grants].publish="own" — may publish ONLY its own <name>.grid.shimpz.com
    grants_dns: bool = False  # [grants].dns="own" — may manage DNS ONLY under its own <name>.grid.shimpz.com
    billing_pay: str = ""  # [billing].pay="shimpzpay" when the app is paid ("" = free) — ShimpzPay is the only rail
    billing_price: float = 0.0  # [billing].price — amount charged per period (0.0 = free)
    billing_currency: str = ""  # [billing].currency — lowercase ISO code ("" = free)
    billing_period: str = ""  # [billing].period — monthly|yearly|once ("" = free)
    run: tuple[RunStep, ...] = ()  # [[run]] — how `shimpz-app install` deploys the app's service(s)
    config: tuple[ConfigItem, ...] = field(default_factory=tuple)

    @property
    def config_keys(self) -> frozenset[str]:
        """The app's own config env keys — the app-scoped allowlist the deploy adds to the infra keys."""
        return frozenset(c.key for c in self.config)


@dataclass(frozen=True)
class Plan:
    """The deploy plan for ONE app: what to install first, what to wire, what's blocking.

    `install_order` = the app→app dependencies that aren't deployed yet, dependency-FIRST (topological)
    so each is up before whatever needs it. `calls` = the app's own sync reach (→ `--calls`).
    `missing_native` = required native integrations that aren't enabled — a hard block on the deploy.
    """

    install_order: tuple[str, ...]
    calls: tuple[str, ...]
    missing_native: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """True when nothing blocks the deploy (every required native is enabled; deps get installed)."""
        return not self.missing_native


def missing_native(m: Manifest, enabled) -> tuple[str, ...]:
    """The manifest's required native integrations that are NOT in `enabled` (order-preserving)."""
    have = frozenset(enabled)
    return tuple(g for g in m.native if g not in have)


def app_domain(name: str) -> str:
    """The one FQDN an app holding a publish/dns grant is pinned to: <name>.grid.shimpz.com."""
    return f"{name}.{GRID_SUFFIX}"


def effective_egress(m: Manifest) -> frozenset[str]:
    """Hosts the app's egress proxy must allow — the deny-by-default allowlist the L2 proxy enforces.

    Its declared `[needs].egress`, plus `pay.shimpz.com` iff the app is paid ([billing] present); an app
    reaches nothing else. A free app with no declared egress gets the empty set (no internet at all).
    """
    hosts = set(m.egress)
    if m.billing_pay:
        hosts.add(PAY_HOST)
    return frozenset(hosts)


def publish_scope_ok(name: str, fqdn: str, m: Manifest) -> bool:
    """Enforce a publish/dns grant's scope — whether app `name` (manifest `m`) may publish/manage `fqdn`.

    An app that DECLARES `[grants].publish`/`dns` is PINNED to its own `<name>.grid.shimpz.com` — any
    other fqdn is refused (it can't publish itself elsewhere or hijack another app's subdomain). An app
    that declares NEITHER is not governed by this grant (existing owner-driven publish behavior stands),
    so this returns True — the grant CONSTRAINS, it doesn't silently forbid apps that never asked for it.
    """
    if not (m.grants_publish or m.grants_dns):
        return True
    return fqdn == app_domain(name)


def resolve(root: Manifest, *, load, deployed, enabled=frozenset()) -> Plan:
    """Plan `root`'s deploy: topological install-order for its app-deps + the deploy blockers.

    `load(app_name) -> Manifest` fetches a dependency app's manifest (from the workspace); `deployed` is
    the set of already-running app names; `enabled` is the set of enabled native integrations. Missing
    app-deps come out dependency-FIRST (transitive); a dependency cycle raises ManifestError. The native
    and call checks are the ROOT's own — each dep re-resolves its own needs when it deploys.
    """
    deployed = frozenset(deployed)
    order: list[str] = []
    done: set[str] = set()
    stack: list[str] = []

    def visit(name: str) -> None:
        if name in deployed or name in done:
            return  # already up (with its own deps) or already planned
        if name in stack:
            raise ManifestError(f"app dependency cycle: {' -> '.join([*stack[stack.index(name) :], name])}")
        stack.append(name)
        for dep in load(name).apps:
            visit(dep)
        stack.pop()
        done.add(name)
        order.append(name)

    for dep in root.apps:
        visit(dep)
    return Plan(tuple(order), root.calls, missing_native(root, enabled))


def find(project_dir) -> Path | None:
    """Locate `shimpz.app.toml` at the project root, or None if the app doesn't declare one (yet)."""
    p = Path(project_dir) / MANIFEST_NAME
    return p if p.is_file() else None


def load(path) -> Manifest:
    """Parse + validate the manifest at `path`. Missing file / bad TOML / rule break → ManifestError."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as e:
        raise ManifestError(f"cannot read {p}: {e}") from None
    return _from_toml(raw, str(p))


def parse(text: str) -> Manifest:
    """Parse + validate manifest TOML from a string (the test/echo path)."""
    return _from_toml(text.encode("utf-8") if isinstance(text, str) else text, "<string>")


def _from_toml(raw: bytes, where: str) -> Manifest:
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ManifestError(f"{where} is not valid TOML: {e}") from None

    _reject_unknown(data, _TOP_KEYS, where, "top level")

    name = _req_str(data, "name", where)
    if not NAME_RE.match(name):
        raise ManifestError(f"{where}: name {name!r} must be a kebab-case slug (a-z, 0-9, -), starting a-z")
    title = _opt_str(data, "title", where) or name
    version = _opt_str(data, "version", where) or "0.1.0"
    summary = _opt_str(data, "summary", where)

    needs = data.get("needs", {})
    if not isinstance(needs, dict):
        raise ManifestError(f"{where}: [needs] must be a table")
    _reject_unknown(needs, _NEEDS_KEYS, where, "[needs]")

    native = _str_list(needs, "native", where)
    for g in native:
        if g not in NATIVE_GROUPS:
            raise ManifestError(f"{where}: needs.native has unknown integration {g!r} — one of {sorted(NATIVE_GROUPS)}")
    apps = _dedup(_slug_list(needs, "apps", where, NAME_RE, name))
    calls = _dedup(_slug_list(needs, "calls", where, CALL_RE, name))
    egress = _egress(needs, where)

    provides_skill = _provides_skill(data.get("provides", {}), where)
    grants_consume, grants_publish, grants_dns = _grants(data.get("grants", {}), where)
    billing_pay, billing_price, billing_currency, billing_period = _billing(data.get("billing", {}), where)
    run = _run(data.get("run", []), where)
    config = _config(data.get("config", {}), where)
    return Manifest(
        name, title, version, summary, tuple(native), apps, calls, egress, provides_skill,
        grants_consume, grants_publish, grants_dns,
        billing_pay, billing_price, billing_currency, billing_period, run, config
    )


def _run(raw, where) -> tuple[RunStep, ...]:
    """Parse `[[run]]` — how `shimpz-app install` deploys this app.

    Each step: a slug name, a port, and a command whose first token is an allowed binary (so the
    installer can't be tricked into an arbitrary launch; `shimpz-app deploy` re-validates too).
    """
    if not isinstance(raw, list):
        raise ManifestError(f"{where}: [[run]] must be an array of tables")
    steps = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ManifestError(f"{where}: [[run]] entry {i} must be a table")
        _reject_unknown(item, _RUN_ITEM_KEYS, where, f"[[run]] entry {i}")
        name = item.get("name")
        if not isinstance(name, str) or not RUN_NAME_RE.match(name):
            raise ManifestError(f"{where}: [[run]] entry {i} name {name!r} must be a kebab-case slug")
        port = item.get("port")
        if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
            raise ManifestError(f"{where}: [[run]] entry {i} port {port!r} must be an integer 1-65535")
        command = item.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command):
            raise ManifestError(f"{where}: [[run]] entry {i} command must be a non-empty list of non-empty strings")
        if command[0] not in RUN_BINS:
            raise ManifestError(
                f"{where}: [[run]] entry {i} command must start with one of {sorted(RUN_BINS)}: {command[0]!r}"
            )
        steps.append(RunStep(name, port, tuple(command)))
    return tuple(steps)


def _own_grant(raw, key, where) -> bool:
    """Parse an `[grants].<key>` scope grant: absent → False, the literal string "own" → True, else raise.

    The ONLY accepted value is "own" (may act on the app's OWN <name>.grid.shimpz.com). We reject any
    other value up front rather than silently widen scope — an unknown scope is fail-closed, not "any".
    """
    if key not in raw:
        return False
    v = raw[key]
    if v != "own":
        raise ManifestError(f"{where}: grants.{key} must be \"own\" (its own <name>.{GRID_SUFFIX}); got {v!r}")
    return True


def _grants(raw, where) -> tuple[tuple[str, ...], bool, bool]:
    """The `[grants]` an app requests (only enforced grants are accepted — see `_GRANTS_KEYS`).

    Returns (consume_topics, publish, dns): `consume` = foreign bus topics the app may read (each
    `<project>.<topic>`); `publish`/`dns` = whether the app is granted (and thereby PINNED) to publish /
    manage DNS for its own <name>.grid.shimpz.com.
    """
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: [grants] must be a table")
    _reject_unknown(raw, _GRANTS_KEYS, where, "[grants]")
    consume = _str_list(raw, "consume", where)
    for t in consume:
        if not GRANT_TOPIC_RE.match(t):
            raise ManifestError(f"{where}: grants.consume topic {t!r} must be '<project>.<topic>' (a foreign topic)")
    return _dedup(consume), _own_grant(raw, "publish", where), _own_grant(raw, "dns", where)


def _billing(raw, where) -> tuple[str, float, str, str]:
    """Parse `[billing]` — the app's price and its ENFORCED payment rail.

    Absent → free ("", 0.0, "", ""). Present → the ONLY accepted `pay` is "shimpzpay" (the platform is
    the sole rail — any other value is refused, mirroring grants' "own"-only, so an app can never declare
    its own processor). A paid app must give price > 0, a lowercase ISO currency (default "usd"), and a
    known period. Returns (pay, price, currency, period).
    """
    if not raw:
        return "", 0.0, "", ""
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: [billing] must be a table")
    _reject_unknown(raw, _BILLING_KEYS, where, "[billing]")
    pay = raw.get("pay")
    if pay != "shimpzpay":
        raise ManifestError(
            f'{where}: billing.pay must be "shimpzpay" — the only payment rail is ShimpzPay '
            f"(pay.shimpz.com); got {pay!r}"
        )
    price = raw.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
        raise ManifestError(f"{where}: billing.price must be a number > 0; got {price!r}")
    currency = raw.get("currency", "usd")
    if not isinstance(currency, str) or not CURRENCY_RE.match(currency):
        raise ManifestError(f"{where}: billing.currency must be a lowercase 3-letter ISO code; got {currency!r}")
    period = raw.get("period")
    if period not in _BILLING_PERIODS:
        raise ManifestError(f"{where}: billing.period must be one of {sorted(_BILLING_PERIODS)}; got {period!r}")
    return "shimpzpay", float(price), currency, period


def _egress(needs, where) -> tuple[str, ...]:
    """Parse `[needs].egress` — the external hosts an app may reach (deny-by-default egress allowlist).

    Each must be a valid lowercase hostname. A payment-processor host (and pay.shimpz.com itself) is
    REFUSED: payment is locked to ShimpzPay, so a paid app declares [billing] (its allowlist then
    includes pay.shimpz.com via effective_egress) and never names a processor host directly.
    """
    hosts = _str_list(needs, "egress", where)
    for h in hosts:
        if not EGRESS_HOST_RE.match(h):
            raise ManifestError(f"{where}: needs.egress host {h!r} must be a lowercase hostname (e.g. api.example.com)")
        if h in PAYMENT_HOSTS:
            raise ManifestError(
                f"{where}: needs.egress host {h!r} is a payment host — an app can't reach a processor "
                f"directly; declare [billing] and charge via ShimpzPay (pay.shimpz.com)"
            )
    return _dedup(hosts)


def _provides_skill(raw, where) -> str:
    """The [provides].skill path an app ships (empty when absent), validated as a safe relative path.

    No absolute path and no `..` traversal — the deploy copies it verbatim into the brain's skills dir.
    """
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: [provides] must be a table")
    _reject_unknown(raw, _PROVIDES_KEYS, where, "[provides]")
    skill = _opt_str(raw, "skill", where)
    if not skill:
        return ""
    if skill.startswith("/") or ".." in skill.split("/") or not SKILL_PATH_RE.match(skill):
        raise ManifestError(
            f"{where}: provides.skill must be a project-relative .md path (no '..', no leading '/'): {skill!r}"
        )
    return skill


def _config(raw, where) -> tuple[ConfigItem, ...]:
    if not isinstance(raw, dict):
        raise ManifestError(f"{where}: [config] must be a table")
    items = []
    for key, spec in raw.items():
        if not CONFIG_KEY_RE.match(key):
            raise ManifestError(f"{where}: config key {key!r} must be UPPER_SNAKE_CASE")
        if key in RESERVED_CONFIG_KEYS:
            raise ManifestError(f"{where}: config key {key!r} is reserved by the platform — pick another name")
        if key.startswith("SHIMPZ_"):
            raise ManifestError(f"{where}: config key {key!r} — the SHIMPZ_ prefix is reserved for Shimpz itself")
        if key in FORBIDDEN_CONFIG_KEYS:
            raise ManifestError(
                f"{where}: config key {key!r} is a Shimpz-managed secret — request the capability with "
                f"[needs].native instead of carrying the secret yourself"
            )
        if key in PAYMENT_KEYS:
            raise ManifestError(
                f"{where}: config key {key!r} is a payment-processor credential — an app cannot take "
                f"payment directly; declare [billing] and charge via ShimpzPay (pay.shimpz.com)"
            )
        # shorthand: KEY = "default value"  ⇢  {default = "..."}
        if isinstance(spec, str):
            items.append(ConfigItem(key, default=spec))
            continue
        if not isinstance(spec, dict):
            raise ManifestError(f"{where}: config {key!r} must be a string default or a table")
        _reject_unknown(spec, _CONFIG_ITEM_KEYS, where, f"config.{key}")
        default = spec.get("default", "")
        if not isinstance(default, str):
            raise ManifestError(f"{where}: config.{key}.default must be a string")
        if not isinstance(spec.get("secret", False), bool):
            raise ManifestError(f"{where}: config.{key}.secret must be true/false")
        items.append(
            ConfigItem(key, default=default, secret=bool(spec.get("secret", False)), help=str(spec.get("help", "")))
        )
    return tuple(items)


# ── small typed getters (fail-loud on the wrong TOML type) ──────────────────────────────────────────
def _req_str(data, key, where) -> str:
    if key not in data:
        raise ManifestError(f"{where}: missing required key {key!r}")
    return _opt_str(data, key, where)


def _opt_str(data, key, where) -> str:
    v = data.get(key, "")
    if not isinstance(v, str):
        raise ManifestError(f"{where}: {key} must be a string")
    return v.strip()


def _str_list(data, key, where) -> list[str]:
    v = data.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ManifestError(f"{where}: needs.{key} must be a list of strings")
    return [x.strip() for x in v]


def _slug_list(data, key, where, pattern, own_name) -> list[str]:
    out = _str_list(data, key, where)
    for x in out:
        if not pattern.match(x):
            raise ManifestError(f"{where}: needs.{key} entry {x!r} is not a valid app name")
        if x == own_name:
            raise ManifestError(f"{where}: needs.{key} cannot include the app itself ({x!r})")
    return out


def _dedup(items) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))  # order-preserving de-dup (matches validate_calls)


def _reject_unknown(table, allowed, where, scope) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ManifestError(f"{where}: unknown {scope} key(s) {unknown} — allowed: {sorted(allowed)}")
