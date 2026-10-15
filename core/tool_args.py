"""
core/tool_args.py — check a tool call's arguments against its declaration
before anything runs.

WHY
    The Live model sometimes sends a call that does not match the schema it was
    given: a number as a string, a list as a JSON string, a required field
    missing, an enum value in the wrong case. Every handler then has to guess,
    and the guesses differ — one silently falls back to a default, another
    raises, a third does the wrong thing and reports success. The user hears
    "Done." for something that never happened.

    From the BlackStarG5/Mark-LIV fork: validate the whole call first, and if it
    cannot be fixed, run NOTHING and tell the model exactly what to correct. The
    model corrects itself on the next call, in the same turn, at the cost of one
    round trip — instead of the user finding out later.

WHAT IS REPAIRED AND WHAT IS REFUSED
    Harmless spelling differences are repaired in place, because refusing them
    would only cost a round trip for the same outcome: "5" for an INTEGER, 3.0
    for an INTEGER, "true" for a BOOLEAN, a JSON string for an ARRAY or OBJECT,
    a lone value for an ARRAY, an enum value in the wrong case, null meaning
    "not given". Anything that changes meaning is refused: a missing required
    field, a value of the wrong kind, an enum value that is not on the list.
    Properties the schema does not mention are passed through untouched — some
    handlers read extra keys, and refusing them would break working calls.
"""

from __future__ import annotations

import json
import re

_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_TRUE = {"true", "yes", "1", "on"}
_FALSE = {"false", "no", "0", "off"}

# After this many malformed calls to one tool in a row, the model is told to
# stop retrying and tell the user. Two corrections is what the fork settled on:
# a model that cannot form the call in three tries will not on the fourth.
MAX_RETRIES = 2

_streak: dict[str, int] = {}


class _Bad(Exception):
    pass


def _kind(schema: dict) -> str:
    return str(schema.get("type") or "").upper()


def _coerce(value, schema: dict, where: str):
    kind = _kind(schema)
    if kind == "STRING":
        if isinstance(value, str):
            out = value
        elif isinstance(value, bool):
            out = "true" if value else "false"
        elif isinstance(value, (int, float)):
            out = str(value)
        else:
            raise _Bad(f"{where} must be text, got {type(value).__name__}")
    elif kind == "INTEGER":
        if isinstance(value, bool):
            raise _Bad(f"{where} must be a whole number, got true/false")
        if isinstance(value, int):
            out = value
        elif isinstance(value, float) and value.is_integer():
            out = int(value)
        elif isinstance(value, str) and _INT_RE.match(value.strip()):
            out = int(value.strip())
        else:
            raise _Bad(f"{where} must be a whole number, got {value!r}"[:160])
    elif kind == "NUMBER":
        if isinstance(value, bool):
            raise _Bad(f"{where} must be a number, got true/false")
        if isinstance(value, (int, float)):
            out = value
        elif isinstance(value, str) and _NUM_RE.match(value.strip()):
            out = float(value.strip())
        else:
            raise _Bad(f"{where} must be a number, got {value!r}"[:160])
    elif kind == "BOOLEAN":
        if isinstance(value, bool):
            out = value
        elif isinstance(value, (int, float)) and value in (0, 1):
            out = bool(value)
        elif isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
            out = value.strip().lower() in _TRUE
        else:
            raise _Bad(f"{where} must be true or false, got {value!r}"[:160])
    elif kind == "ARRAY":
        if isinstance(value, str) and value.strip().startswith("["):
            try:
                value = json.loads(value)
            except ValueError:
                raise _Bad(f"{where} must be a list; the text given is not valid JSON")
        if not isinstance(value, (list, tuple)):
            value = [value]
        item = schema.get("items") if isinstance(schema.get("items"), dict) else None
        out = [_coerce(v, item, f"{where}[{i}]") if item else v
               for i, v in enumerate(value)]
    elif kind == "OBJECT":
        if isinstance(value, str) and value.strip().startswith("{"):
            try:
                value = json.loads(value)
            except ValueError:
                raise _Bad(f"{where} must be an object; the text given is not valid JSON")
        if not isinstance(value, dict):
            raise _Bad(f"{where} must be an object, got {type(value).__name__}")
        out = _object(value, schema, where)
    else:
        out = value            # no type declared: nothing to check

    enum = schema.get("enum")
    if enum and isinstance(out, str):
        if out not in enum:
            match = next((e for e in enum if str(e).lower() == out.strip().lower()), None)
            if match is None:
                raise _Bad(f"{where} must be one of {', '.join(map(str, enum))}; "
                           f"got {out!r}"[:240])
            out = match
    return out


def _object(args: dict, schema: dict, where: str = "") -> dict:
    props = schema.get("properties") or {}
    out = {}
    for key, value in args.items():
        if value is None:
            continue           # null = not given; "required" below decides
        sub = props.get(key)
        name = f"{where}.{key}" if where else key
        out[key] = _coerce(value, sub, name) if isinstance(sub, dict) else value
    missing = [k for k in schema.get("required") or [] if k not in out]
    if missing:
        prefix = f"{where}." if where else ""
        raise _Bad("missing required " + ", ".join(prefix + m for m in missing))
    return out


def check(args: dict, schema: dict | None) -> tuple[dict, str]:
    """Returns (repaired args, "") or (original args, what is wrong).

    Never raises: a schema this module does not understand lets the call
    through unchanged, because a validator that blocks working tools is worse
    than none."""
    if not isinstance(schema, dict) or _kind(schema) != "OBJECT":
        return args, ""
    try:
        return _object(dict(args or {}), schema), ""
    except _Bad as e:
        return args, str(e)
    except Exception:
        return args, ""


def refusal(tool: str, problem: str) -> str:
    """The tool result for a malformed call, counting consecutive failures."""
    n = _streak.get(tool, 0) + 1
    _streak[tool] = n
    if n > MAX_RETRIES:
        return (f"[ARGS_INVALID] Nothing ran: {problem}. This is malformed call number "
                f"{n} to {tool} in a row, so stop retrying. Tell the user in one short "
                f"sentence that you could not form the request, and ask them to rephrase.")
    return (f"[ARGS_INVALID] Nothing ran: {problem}. Correct the arguments to match the "
            f"tool's parameters and call {tool} again now. Do not tell the user it was "
            f"done.")


def accepted(tool: str) -> None:
    _streak.pop(tool, None)


def reset() -> None:
    """Tests only."""
    _streak.clear()
