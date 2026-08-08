"""Parse and validate ~/.haymish/rules.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .paths import DEFAULT_LIBRARY, DEFAULT_REPORT_DIR, RULES_PATH


class ConfigError(Exception):
    pass


@dataclass
class StageConfig:
    after_days: int = 0


VALID_POSTURES = {"tidy", "archival"}


@dataclass
class Rule:
    name: str
    pack: str | None = None              # which rule pack it came from, None for rules.toml
    query: dict = field(default_factory=dict)
    detector: str | None = None          # named detector (receipts, messages, dupes, junk)
    semantic: dict | None = None         # {query, min_score?, top?} — embedding match on the AI index
    classify: dict | None = None         # {backend, prompt, threshold}
    file: dict | None = None             # {album, keyword}
    hide: StageConfig | None = None
    archive: StageConfig | None = None
    delete: StageConfig | None = None
    exclude_matched_by: list[str] = field(default_factory=list)
    report_only: bool = False
    enabled: bool = True


@dataclass
class Config:
    library: Path
    backup: Path | None
    report_dir: Path
    ollama_host: str
    ollama_model: str
    claude_model: str
    ai_embed_model: str
    ai_vision_model: str
    ai_planner_backend: str              # "ollama" or "claude"
    ai_planner_model: str
    rules: list[Rule]
    source_path: Path
    # Defaulted so adding config fields doesn't break every constructor call.
    # "tidy" reduces clutter and permits delete stages; "archival" refuses them
    # outright, for libraries where a photo is inventory rather than clutter.
    posture: str = "tidy"

    def rule(self, name: str) -> Rule:
        for r in self.rules:
            if r.name == name:
                return r
        raise KeyError(name)


VALID_QUERY_KEYS = {
    # kind
    "screenshot", "selfie", "favorite", "hidden", "movie", "screen_recording",
    "raw", "burst", "live_photo",
    # time — relative (drifts each run) and absolute (a fixed shoot or trip)
    "min_age_days", "max_age_days", "after", "before",
    # organization
    "albums", "exclude_albums", "keywords", "uuids",
    # place
    "place", "near", "has_location",
    # people
    "persons", "has_faces",
    # quality, for culling
    "min_score", "max_failure", "min_rating",
    # capture
    "camera", "lens",
}
VALID_RULE_KEYS = {
    "query", "detector", "semantic", "classify", "file", "hide", "archive", "delete",
    "exclude_matched_by", "report_only", "enabled",
}
KNOWN_DETECTORS = {"receipts", "messages", "dupes", "junk"}


def _stage(raw: dict | None, rule: str, stage: str) -> StageConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or "after_days" not in raw:
        raise ConfigError(f"[rule.{rule}].{stage} must be a table with after_days, e.g. {{ after_days = 14 }}")
    extra = {k for k in raw if k not in {"after_days", "to"}}
    if extra:
        raise ConfigError(f"[rule.{rule}].{stage} has unknown keys: {sorted(extra)}")
    return StageConfig(after_days=int(raw["after_days"]))


def _validate_query_shapes(name: str, query: dict) -> None:
    """Catch malformed query values at load time.

    These filters are cheap to evaluate but easy to get subtly wrong, and a bad
    value fails silently at match time (matching nothing, which reads as "no
    photos qualify" rather than "your rule is broken"). Better to refuse the
    config with a message naming the fix.
    """
    import datetime as dt

    if "near" in query:
        near = query["near"]
        if not isinstance(near, dict) or "lat" not in near or "lon" not in near:
            raise ConfigError(
                f"[rule.{name}].query.near needs lat and lon, e.g. "
                f'near = {{ lat = 41.88, lon = -87.63, km = 25 }}'
            )
        try:
            lat, lon = float(near["lat"]), float(near["lon"])
        except (TypeError, ValueError):
            raise ConfigError(f"[rule.{name}].query.near lat/lon must be numbers") from None
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ConfigError(
                f"[rule.{name}].query.near has out-of-range coordinates "
                f"(lat {lat}, lon {lon}) — lat is -90..90, lon is -180..180"
            )

    for key in ("after", "before"):
        if key in query:
            value = query[key]
            if isinstance(value, (dt.date, dt.datetime)):
                continue
            try:
                dt.datetime.fromisoformat(str(value))
            except ValueError:
                raise ConfigError(
                    f"[rule.{name}].query.{key} must be a date like {key} = 2026-03-01 "
                    f"(unquoted TOML date) or an ISO string — got {value!r}"
                ) from None

    for key in ("min_score", "max_failure"):
        if key in query and not 0 <= float(query[key]) <= 1:
            raise ConfigError(f"[rule.{name}].query.{key} must be between 0 and 1")

    if "min_rating" in query and not 0 <= int(query["min_rating"]) <= 5:
        raise ConfigError(f"[rule.{name}].query.min_rating must be 0-5")

    for key in ("persons", "albums", "exclude_albums", "keywords"):
        if key in query and not isinstance(query[key], list):
            raise ConfigError(f"[rule.{name}].query.{key} must be a list, e.g. {key} = [\"Alice\"]")


def _parse_rule(name: str, raw: dict) -> Rule:
    unknown = {k for k in raw if k not in VALID_RULE_KEYS}
    if unknown:
        raise ConfigError(f"[rule.{name}] has unknown keys: {sorted(unknown)}")
    query = raw.get("query", {})
    bad = {k for k in query if k not in VALID_QUERY_KEYS}
    if bad:
        raise ConfigError(f"[rule.{name}].query has unknown keys: {sorted(bad)} (valid: {sorted(VALID_QUERY_KEYS)})")
    _validate_query_shapes(name, query)
    detector = raw.get("detector")
    if detector is not None and detector not in KNOWN_DETECTORS:
        raise ConfigError(f"[rule.{name}].detector {detector!r} unknown (valid: {sorted(KNOWN_DETECTORS)})")
    semantic = raw.get("semantic")
    if semantic is not None:
        if not isinstance(semantic, dict) or not semantic.get("query"):
            raise ConfigError(
                f"[rule.{name}].semantic must be a table with a query, "
                f'e.g. {{ query = "recipe screenshots", min_score = 0.35 }}'
            )
        bad = {k for k in semantic if k not in {"query", "min_score", "top"}}
        if bad:
            raise ConfigError(f"[rule.{name}].semantic has unknown keys: {sorted(bad)}")
        if "min_score" in semantic and not 0 <= float(semantic["min_score"]) <= 1:
            raise ConfigError(f"[rule.{name}].semantic.min_score must be between 0 and 1")
    classify = raw.get("classify")
    if classify is not None:
        if classify.get("backend") not in {"ollama", "claude", "apple"}:
            raise ConfigError(f"[rule.{name}].classify.backend must be ollama, claude, or apple")
        if "prompt" not in classify:
            raise ConfigError(f"[rule.{name}].classify needs a prompt")
    return Rule(
        name=name,
        query=query,
        detector=detector,
        semantic=semantic,
        classify=classify,
        file=raw.get("file"),
        hide=_stage(raw.get("hide"), name, "hide"),
        archive=_stage(raw.get("archive"), name, "archive"),
        delete=_stage(raw.get("delete"), name, "delete"),
        exclude_matched_by=list(raw.get("exclude_matched_by", [])),
        report_only=bool(raw.get("report_only", False)),
        enabled=bool(raw.get("enabled", True)),
    )


def _load_packs(packs_dir: Path | None = None) -> list[Rule]:
    """Rules contributed by installed packs in ~/.haymish/packs/*.toml.

    Packs exist so a profession's rule set can be installed, tried, and removed
    as one unit -- deleting a file rather than picking apart a merged rules.toml.
    Rule names are namespaced `pack:rule` so two packs (or a pack and your own
    rules) can both define "receipts" without colliding. Inside a pack,
    exclude_matched_by may use bare names; they resolve to that pack first.
    """
    from .paths import PACKS_DIR

    packs_dir = packs_dir or PACKS_DIR
    if not packs_dir.is_dir():
        return []

    rules: list[Rule] = []
    for path in sorted(packs_dir.glob("*.toml")):
        pack_name = path.stem
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"pack {path.name} is not valid TOML: {e}") from None

        if not bool(raw.get("pack", {}).get("enabled", True)):
            continue

        own = set(raw.get("rule", {}))
        for name, body in raw.get("rule", {}).items():
            rule = _parse_rule(name, body)
            rule.pack = pack_name
            rule.name = f"{pack_name}:{name}"
            rule.exclude_matched_by = [
                f"{pack_name}:{dep}" if dep in own else dep
                for dep in rule.exclude_matched_by
            ]
            rules.append(rule)
    return rules


def pack_metadata(path: Path) -> dict:
    """The [pack] table of a pack file — name, description, posture hint."""
    try:
        with open(path, "rb") as f:
            meta = tomllib.load(f).get("pack", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    meta.setdefault("name", path.stem)
    return meta


def load_config(path: Path | None = None) -> Config:
    path = path or RULES_PATH
    if not path.exists():
        raise ConfigError(
            f"No rules file at {path}. Run `haymish init` to install the starter rules."
        )
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    g = raw.get("global", {})
    library = Path(g.get("library", DEFAULT_LIBRARY)).expanduser()
    backup = Path(g["backup"]).expanduser() if "backup" in g else None
    report_dir = Path(g.get("report_dir", DEFAULT_REPORT_DIR)).expanduser()
    ollama = g.get("ollama", {})
    claude = g.get("claude", {})
    ai = g.get("ai", {})
    planner_backend = ai.get("planner_backend", "ollama")
    if planner_backend not in {"ollama", "claude"}:
        raise ConfigError('[global.ai].planner_backend must be "ollama" or "claude"')

    posture = g.get("posture", "tidy")
    if posture not in VALID_POSTURES:
        raise ConfigError(
            f'[global].posture must be one of {sorted(VALID_POSTURES)} — got {posture!r}. '
            f'"tidy" reduces clutter (delete stages allowed); "archival" never deletes.'
        )

    rules = [_parse_rule(name, body) for name, body in raw.get("rule", {}).items()]
    rules += _load_packs()

    names = {r.name for r in rules}
    seen: set[str] = set()
    for r in rules:
        if r.name in seen:
            source = f"pack {r.pack!r}" if r.pack else "rules.toml"
            raise ConfigError(
                f"duplicate rule name {r.name!r} (from {source}) — rename it, or disable "
                f"the conflicting one with enabled = false"
            )
        seen.add(r.name)

    for r in rules:
        where = f"pack {r.pack}" if r.pack else "rules.toml"
        for dep in r.exclude_matched_by:
            if dep not in names:
                raise ConfigError(
                    f"[rule.{r.name}] ({where}).exclude_matched_by references unknown rule {dep!r}"
                )
        if r.delete and posture == "archival":
            # The whole point of archival posture: for a photographer or anyone
            # whose library is client work, an unattended path to deletion is a
            # liability, not a feature. Refuse the config rather than quietly
            # ignoring the stage -- silently dropping it would be worse.
            raise ConfigError(
                f"[rule.{r.name}] ({where}) has a delete stage, but [global].posture is "
                f'"archival" — that posture never deletes. Remove the delete stage, or set '
                f'posture = "tidy" if you do want deletion available.'
            )
        if r.delete and not r.archive:
            raise ConfigError(
                f"[rule.{r.name}] has a delete stage but no archive stage — deletion requires "
                f"a verified backup copy. Add archive = {{ after_days = N }} (N < delete.after_days)."
            )
        if r.delete and r.archive and r.archive.after_days >= r.delete.after_days:
            raise ConfigError(
                f"[rule.{r.name}] archive.after_days ({r.archive.after_days}) must be less than "
                f"delete.after_days ({r.delete.after_days}) — otherwise photos become staged for "
                f"deletion before their archive backup is even due to run."
            )

    return Config(
        library=library,
        backup=backup,
        report_dir=report_dir,
        posture=posture,
        ollama_host=ollama.get("host", "http://localhost:11434"),
        # gemma3:4b is the smallest vision-capable gemma3 — right size for
        # per-photo yes/no classification and captioning at library scale.
        ollama_model=ollama.get("model", "gemma3:4b"),
        claude_model=claude.get("model", "claude-sonnet-5"),
        ai_embed_model=ai.get("embed_model", "qwen3-embedding:8b"),
        ai_vision_model=ai.get("vision_model", ollama.get("model", "gemma3:4b")),
        ai_planner_backend=planner_backend,
        ai_planner_model=ai.get("planner_model", "qwen3.6:27b"),
        rules=rules,
        source_path=path,
    )
