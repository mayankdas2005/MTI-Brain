import json
from pathlib import Path
from app.core.logger import logger

_PROFILE_PATH = Path(__file__).resolve().parent.parent.parent.parent / "join_key_profile.json"
_INDEX: dict[tuple, dict] = {}


def _load() -> None:
    data = json.loads(_PROFILE_PATH.read_text())
    for p in data["profiles"]:
        fwd = (p["from_table"], p["from_col"], p["to_table"], p["to_col"])
        rev = (p["to_table"], p["to_col"], p["from_table"], p["from_col"])
        _INDEX[fwd] = p
        if rev not in _INDEX:
            _INDEX[rev] = p
    logger.info("join_key_filter | loaded | pairs={}", len(_INDEX))


_load()


def get_verdict(from_table: str, from_col: str, to_table: str, to_col: str) -> str:
    e = _INDEX.get((from_table, from_col, to_table, to_col))
    return e["verdict"] if e else "unknown"


def _get_block_type(entry: dict) -> str:
    """Classify a dangerous profile entry.

    dead_join — join produces 0 rows (null column or zero value overlap).
                Table should be removed when this is the only path.
    fan_out   — join multiplies rows (critical fanout_score).
                Table is needed but must use pre-agg CTE, not a direct JOIN.
    """
    reason = (entry.get("verdict_reason") or "").lower()
    if "all_null" in reason or "zero value overlap" in reason:
        return "dead_join"
    if entry.get("cross_stats") is None:
        return "dead_join"
    return "fan_out"


def _parse_clause(clause: str) -> tuple | None:
    """Parse 'lpp.a.col1 = lpp.b.col2' -> (lpp.a, col1, lpp.b, col2)."""
    parts = [p.strip() for p in clause.split("=")]
    if len(parts) != 2:
        return None

    def split_fqn(s: str) -> tuple[str | None, str | None]:
        i = s.rfind(".")
        return (s[:i], s[i + 1:]) if i != -1 else (None, None)

    lt, lc = split_fqn(parts[0])
    rt, rc = split_fqn(parts[1])
    return (lt, lc, rt, rc) if all([lt, lc, rt, rc]) else None


def _clause_verdict_and_type(clause: str) -> tuple[str, str]:
    """Return (verdict, block_type) for a clause. block_type only meaningful when verdict='dangerous'."""
    parsed = _parse_clause(clause)
    if not parsed:
        return "unknown", ""
    entry = _INDEX.get(parsed)
    if not entry:
        return "unknown", ""
    v = entry["verdict"]
    btype = _get_block_type(entry) if v == "dangerous" else ""
    return v, btype


def is_clause_safe(clause: str) -> tuple[bool, str]:
    """Return (safe, reason). safe=False only when verdict='dangerous' AND block_type='dead_join'."""
    v, btype = _clause_verdict_and_type(clause)
    if v == "dangerous" and btype == "dead_join":
        parsed = _parse_clause(clause)
        if parsed:
            lt, lc, rt, rc = parsed
            return False, f"{lt}.{lc}={rt}.{rc} verdict=dangerous/dead_join"
    return True, ""


def filter_join_paths(paths: list[dict]) -> tuple[list[dict], list[dict]]:
    """Filter join paths by profile verdict.

    Rules:
      - Any clause with verdict=dangerous/dead_join → block the entire path.
      - Any clause with verdict=dangerous/fan_out (no dead_join) → allow path,
        annotate with _join_annotation='pre_agg_required' and _path_verdict='fan_out'.
      - safe/caution/unknown → allow with _path_verdict set accordingly.

    Returns (allowed, blocked).
    """
    allowed, blocked = [], []
    for path in paths:
        dead_reasons: list[str] = []
        has_fanout = False
        has_caution = False
        has_safe = False

        for clause in (path.get("join_clauses") or []):
            v, btype = _clause_verdict_and_type(clause)
            if v == "dangerous":
                parsed = _parse_clause(clause)
                label = f"{parsed[0]}.{parsed[1]}={parsed[2]}.{parsed[3]}" if parsed else clause
                if btype == "dead_join":
                    dead_reasons.append(f"{label} verdict=dangerous/dead_join")
                else:
                    has_fanout = True
            elif v == "caution":
                has_caution = True
            elif v == "safe":
                has_safe = True

        if dead_reasons:
            blocked.append({**path, "_blocked_by": dead_reasons, "_block_type": "dead_join"})
        elif has_fanout:
            allowed.append({**path, "_join_annotation": "pre_agg_required", "_path_verdict": "fan_out"})
        elif has_caution:
            allowed.append({**path, "_path_verdict": "caution"})
        elif has_safe:
            allowed.append({**path, "_path_verdict": "safe"})
        else:
            allowed.append({**path, "_path_verdict": "unknown"})

    return allowed, blocked
