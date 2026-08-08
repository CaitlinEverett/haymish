"""The `haymish ask` compiler: natural language -> a validated, review-gated rule.

The LLM never acts. It emits a JSON plan; this module whitelists its keys, strips
anything destructive, and runs it through the same config validation as a
hand-written rules.toml rule. The resulting ephemeral Rule then flows through the
normal preview -> browser review -> apply_confirmed pipeline, so a hallucinated
plan can at worst *propose* nonsense — the user sees exactly which photos it
matched (with thumbnails) before anything happens.

Hard safety line: plans can file (album/keyword) and hide only. archive/delete
are stripped even if the model emits them — deletion stays in rules.toml plus
the staged confirm-deletes flow, never one prompt away.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from ..config import Config, ConfigError, Rule, _parse_rule
from . import ollama_client
from .ollama_client import AIError

_ALLOWED_PLAN_KEYS = {"name", "description", "query", "semantic", "classify", "file", "hide"}
_ALLOWED_QUERY_KEYS = {"screenshot", "selfie", "favorite", "movie", "screen_recording",
                        "raw", "burst", "live_photo",
                        "min_age_days", "max_age_days", "after", "before",
                        "place", "near", "has_location",
                        "persons", "has_faces",
                        "min_score", "max_failure", "min_rating",
                        "camera", "lens"}

SYSTEM_PROMPT = """You translate a user's photo-cleanup request into a JSON plan for the Haymish photo tool. Output ONLY a JSON object, no other text.

Schema (include only the fields the request needs):
{
  "name": "short-kebab-case-slug",
  "description": "one sentence restating what this plan does",
  "query": {                     // cheap metadata filters, all optional
    "screenshot": true|false,    // photo is a screenshot
    "selfie": true|false,        // front-camera photo
    "favorite": true|false,
    "movie": true|false,         // asset is a video
    "screen_recording": true|false,  // video is a screen recording
    "raw": true|false,           // RAW file
    "burst": true|false,
    "live_photo": true|false,
    "min_age_days": int,         // only photos at least this old (relative)
    "max_age_days": int,         // only photos at most this old (relative)
    "after": "YYYY-MM-DD",       // fixed window — use for a named trip or shoot
    "before": "YYYY-MM-DD",      // EXCLUSIVE: to include June 15, write before "2026-06-16"
    "place": "Chicago",          // substring of the photo's place name
    "near": {"lat": 41.88, "lon": -87.63, "km": 25},
    "has_location": true|false,
    "persons": ["Alice"],        // named people in the photo (any of)
    "has_faces": true|false,
    "min_score": 0.0-1.0,        // Apple's overall quality score — culling
    "max_failure": 0.0-1.0,      // Apple's "failed shot" score; low = good
    "min_rating": 0-5,
    "camera": "iPhone 15 Pro",   // substring of camera make/model
    "lens": "35mm"               // substring of lens model
  },
  "semantic": {                  // content match against the photo index (use for "photos of X")
    "query": "what to look for, as a retrieval phrase",
    "min_score": 0.0-1.0         // similarity cutoff, default 0.35; higher = stricter
  },
  "classify": {                  // per-photo yes/no vision check (use when precision matters)
    "prompt": "Is this ...? Answer only yes or no.",
    "threshold": 0.0-1.0         // default 0.7
  },
  "file": { "album": "Album name or Folder/Album", "keyword": "tag-name" },  // either or both
  "hide": { "after_days": int }  // move off the camera roll once this old (0 = immediately)
}

Rules:
- At least one of query/semantic/classify (something must select photos), and at least one of file/hide (something must happen).
- Prefer query flags when the request names them (screenshots, selfies, ages). Use semantic for content ("recipes", "my dog", "whiteboards"). Add classify only when the request needs a precise judgment call per photo.
- You cannot delete, archive, edit, or export photos. If asked to delete, plan the closest safe action (file + hide) — the description should say deletion isn't something ask can do.
- Album names: reuse an existing album when one obviously fits the request, otherwise create a sensible new name.

Examples:

Request: "put my recipe screenshots in a Recipes album"
{"name":"recipe-screenshots","description":"File screenshots that look like recipes into the Recipes album.","query":{"screenshot":true},"semantic":{"query":"screenshot of a cooking recipe with ingredients or instructions","min_score":0.35},"file":{"album":"Recipes"}}

Request: "hide all my selfies older than a week"
{"name":"hide-old-selfies","description":"Hide selfies more than 7 days old from the camera roll.","query":{"selfie":true,"min_age_days":7},"hide":{"after_days":0}}

Request: "tag photos of my dog"
{"name":"dog-photos","description":"Tag photos of a dog with the keyword 'dog'.","semantic":{"query":"photo of a pet dog","min_score":0.4},"classify":{"prompt":"Is there a dog in this photo? Answer only yes or no.","threshold":0.7},"file":{"keyword":"dog"}}

Request: "get old screen recordings off my roll"
{"name":"old-screen-recordings","description":"File screen recordings older than 2 weeks and hide them from the camera roll.","query":{"screen_recording":true,"min_age_days":14},"file":{"album":"Swept/Screen Recordings"},"hide":{"after_days":0}}

Request: "put every photo from my Chicago trip in March into a trip album"
{"name":"chicago-march","description":"File photos taken near Chicago between March 1 and 8 into a trip album.","query":{"after":"2026-03-01","before":"2026-03-08","near":{"lat":41.88,"lon":-87.63,"km":40}},"file":{"album":"Trips/Chicago March"}}

Request: "tag the good shots from the wedding shoot on June 14 and 15"
{"name":"wedding-picks","description":"Tag well-scored photos from June 14-15 with 'pick'.","query":{"after":"2026-06-14","before":"2026-06-16","min_score":0.7},"file":{"keyword":"pick"}}
(note how `before` is the 16th, not the 15th, so the 15th is included)
"""


@dataclass
class Plan:
    rule: Rule
    raw: dict          # sanitized plan dict — what --save serializes
    description: str


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40] or "ask"


def _call_llm(config: Config, request: str, existing_albums: list[str]) -> str:
    albums_note = ""
    if existing_albums:
        albums_note = "\nExisting albums: " + ", ".join(sorted(existing_albums)[:60])
    prompt = f"{SYSTEM_PROMPT}{albums_note}\n\nRequest: {json.dumps(request)}\n"

    if config.ai_planner_backend == "claude":
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise AIError("planner_backend is 'claude' but the anthropic package isn't "
                          "installed — run `uv sync --extra claude`") from e
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AIError("planner_backend is 'claude' but ANTHROPIC_API_KEY is not set")
        client = Anthropic()
        response = client.messages.create(
            model=config.claude_model, max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")

    return ollama_client.generate(config.ollama_host, config.ai_planner_model,
                                   prompt, format_json=True, think=False, timeout=180)


def _sanitize(plan: dict, request: str) -> dict:
    """Whitelist-filter the model's output down to what a plan may contain.
    Anything else — including archive/delete, however phrased — is dropped."""
    clean: dict = {}
    for key in _ALLOWED_PLAN_KEYS:
        if key in plan:
            clean[key] = plan[key]

    if "query" in clean:
        if not isinstance(clean["query"], dict):
            del clean["query"]
        else:
            clean["query"] = {k: v for k, v in clean["query"].items() if k in _ALLOWED_QUERY_KEYS}
            if not clean["query"]:
                del clean["query"]

    if "semantic" in clean:
        sem = clean["semantic"]
        if not isinstance(sem, dict) or not sem.get("query"):
            del clean["semantic"]
        else:
            clean["semantic"] = {"query": str(sem["query"]),
                                  "min_score": float(sem.get("min_score", 0.35))}

    if "classify" in clean:
        cl = clean["classify"]
        if not isinstance(cl, dict) or not cl.get("prompt"):
            del clean["classify"]
        else:
            clean["classify"] = {"backend": "ollama", "prompt": str(cl["prompt"]),
                                  "threshold": float(cl.get("threshold", 0.7))}

    if "file" in clean:
        f = clean["file"]
        if not isinstance(f, dict) or not (f.get("album") or f.get("keyword")):
            del clean["file"]
        else:
            clean["file"] = {k: str(f[k]) for k in ("album", "keyword") if f.get(k)}

    if "hide" in clean:
        h = clean["hide"]
        if not isinstance(h, dict):
            del clean["hide"]
        else:
            clean["hide"] = {"after_days": int(h.get("after_days", 0))}

    clean["name"] = _slugify(str(clean.get("name") or request))
    clean.setdefault("description", request)
    return clean


def plan_from_prompt(config: Config, request: str, existing_albums: list[str] | None = None,
                     existing_rule_names: set[str] | None = None) -> Plan:
    text = _call_llm(config, request, existing_albums or [])
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError(f"planner did not return valid JSON: {text[:300]}") from e
    if not isinstance(raw, dict):
        raise AIError(f"planner returned {type(raw).__name__}, expected a JSON object")

    plan = _sanitize(raw, request)

    if not (plan.get("query") or plan.get("semantic") or plan.get("classify")):
        raise AIError("plan has no way to select photos (no query, semantic, or classify) — "
                      "try rephrasing the request")
    if not (plan.get("file") or plan.get("hide")):
        raise AIError("plan has no action (no album, keyword, or hide) — say what should "
                      "happen to the matching photos")

    name = plan["name"]
    if existing_rule_names:
        base, n = name, 2
        while name in existing_rule_names:
            name = f"{base}-{n}"
            n += 1
        plan["name"] = name

    description = plan.pop("description")
    rule_dict = {k: v for k, v in plan.items() if k != "name"}
    try:
        rule = _parse_rule(plan["name"], rule_dict)
    except ConfigError as e:
        raise AIError(f"planner produced an invalid rule: {e}") from e

    return Plan(rule=rule, raw={**rule_dict, "name": plan["name"]}, description=description)


def plan_to_toml(plan: Plan) -> str:
    """Serialize a plan as a rules.toml [rule.<name>] block for --save. Manual
    emitter — the structure is small and fixed, not worth a tomli-w dependency."""
    def fmt(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return json.dumps(value)  # JSON string escaping is valid TOML

    name = plan.raw["name"]
    lines = [f"# generated by `haymish ask`: {plan.description}", f"[rule.{name}]"]
    for key in ("query", "semantic", "classify", "file", "hide"):
        if key in plan.raw:
            inner = ", ".join(f"{k} = {fmt(v)}" for k, v in plan.raw[key].items())
            lines.append(f"{key} = {{ {inner} }}")
    return "\n".join(lines) + "\n"
