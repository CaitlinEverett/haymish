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


@dataclass
class Rule:
    name: str
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

    def rule(self, name: str) -> Rule:
        for r in self.rules:
            if r.name == name:
                return r
        raise KeyError(name)


VALID_QUERY_KEYS = {
    "screenshot", "selfie", "favorite", "hidden", "movie", "screen_recording",
    "min_age_days", "max_age_days", "albums", "exclude_albums", "keywords",
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


def _parse_rule(name: str, raw: dict) -> Rule:
    unknown = {k for k in raw if k not in VALID_RULE_KEYS}
    if unknown:
        raise ConfigError(f"[rule.{name}] has unknown keys: {sorted(unknown)}")
    query = raw.get("query", {})
    bad = {k for k in query if k not in VALID_QUERY_KEYS}
    if bad:
        raise ConfigError(f"[rule.{name}].query has unknown keys: {sorted(bad)} (valid: {sorted(VALID_QUERY_KEYS)})")
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

    rules = [_parse_rule(name, body) for name, body in raw.get("rule", {}).items()]
    names = {r.name for r in rules}
    for r in rules:
        for dep in r.exclude_matched_by:
            if dep not in names:
                raise ConfigError(f"[rule.{r.name}].exclude_matched_by references unknown rule {dep!r}")
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
