#!/usr/bin/env python3
"""Traceable semantic indexing for architectural CAD views."""

from __future__ import annotations

import re
from typing import Any


SCALE_RE = re.compile(r"^1\s*[:：]\s*\d+(?:\.\d+)?$", re.I)
RANGE_RE = re.compile(r"^([A-Za-z0-9]+)\s*[-－—~～至]\s*([A-Za-z0-9]+)$")
LEVEL_RE = re.compile(r"^(?:±|\+|-)?\d+\.\d{3}$")
DECIMAL_RE = re.compile(r"^\d+(?:\.\d+)?$")
CARDINALS = {
    "南": "south",
    "北": "north",
    "东": "east",
    "西": "west",
    "SOUTH": "south",
    "NORTH": "north",
    "EAST": "east",
    "WEST": "west",
}


def _inside(bounds: dict[str, float], item: dict[str, Any]) -> bool:
    return (
        bounds["xmin"] <= float(item["x"]) <= bounds["xmax"]
        and bounds["ymin"] <= float(item["y"]) <= bounds["ymax"]
    )


def _text_id(item: dict[str, Any]) -> str:
    return str(item.get("handle") or f"TEXT@{float(item['x']):.3f},{float(item['y']):.3f}")


def _fragment(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text_id(item),
        "text": str(item["text"]),
        "x": float(item["x"]),
        "y": float(item["y"]),
        "layer": str(item.get("layer") or ""),
    }


def compose_title(
    anchor: dict[str, Any],
    texts: list[dict[str, Any]],
    frame_bounds: dict[str, float],
) -> dict[str, Any]:
    """Join a role-bearing title with a traceable axis/section prefix."""
    raw = str(anchor["text"])
    height = max(float(anchor.get("height") or 0.0), 1.0)
    frame_width = max(frame_bounds["xmax"] - frame_bounds["xmin"], 1.0)
    baseline_tolerance = max(height * 0.35, frame_width * 1e-6)
    search_radius = max(height * 12.0, frame_width * 0.12)
    same_line = [
        item for item in texts
        if item is not anchor
        and abs(float(item["y"]) - float(anchor["y"])) <= baseline_tolerance
        and abs(float(item["x"]) - float(anchor["x"])) <= search_radius
        and str(item.get("layer") or "") == str(anchor.get("layer") or "")
    ]
    left_prefixes = [
        item for item in same_line
        if float(item["x"]) < float(anchor["x"])
        and (
            RANGE_RE.fullmatch(str(item["text"]).strip())
            or ("标高" in raw and DECIMAL_RE.fullmatch(str(item["text"]).strip()))
        )
    ]
    prefix = max(left_prefixes, key=lambda item: float(item["x"]), default=None)
    scales = [item for item in same_line if SCALE_RE.fullmatch(str(item["text"]).strip())]
    scale = min(scales, key=lambda item: abs(float(item["x"]) - float(anchor["x"])), default=None)
    fragments = [prefix, anchor] if prefix is not None else [anchor]
    canonical = "".join(str(item["text"]).strip() for item in fragments)
    generic_directional_title = re.fullmatch(r"(?:轴)?立面图|剖面图", raw.strip()) is not None
    return {
        "raw": raw,
        "canonical": canonical,
        "confidence": "high" if canonical != raw or not generic_directional_title else "medium",
        "fragments": [_fragment(item) for item in fragments],
        "scale": str(scale["text"]).replace("：", ":") if scale is not None else "",
    }


def direction_from_title(title: str, role: str) -> dict[str, Any]:
    upper = title.upper()
    for token, value in CARDINALS.items():
        if token in upper:
            return {
                "kind": "cardinal",
                "key": value,
                "from_axis": "",
                "to_axis": "",
                "basis": "canonical_title",
                "confidence": "high",
            }
    prefix = title.split("轴", 1)[0] if "轴" in title else title.split("剖面", 1)[0]
    match = RANGE_RE.fullmatch(prefix.strip())
    if match:
        first, second = match.groups()
        kind = "section_line" if role == "section" else "axis_range"
        return {
            "kind": kind,
            "key": f"{first}_to_{second}",
            "from_axis": first,
            "to_axis": second,
            "basis": "canonical_title_fragments",
            "confidence": "high",
        }
    return {
        "kind": "unknown",
        "key": "",
        "from_axis": "",
        "to_axis": "",
        "basis": "none",
        "confidence": "low",
    }


def qa_view_key(title: str, role: str, direction: dict[str, Any], view_id: str) -> str:
    if role == "roof_plan":
        return "roof_plan"
    if role == "plan":
        level_cut = re.search(r"(\d+(?:\.\d+)?)\s*标高", title)
        if level_cut:
            level_key = level_cut.group(1).replace(".", "_")
            return f"plan_cut_level_{level_key}"
        floor_tokens = (
            (r"首层|一层|1F|FIRST", "plan_1f"),
            (r"二层|2F|SECOND", "plan_2f"),
            (r"三层|3F|THIRD", "plan_3f"),
            (r"四层|4F|FOURTH", "plan_4f"),
        )
        for pattern, key in floor_tokens:
            if re.search(pattern, title, re.I):
                return key
        return f"plan_{view_id.lower().replace('-', '_')}"
    if role == "elevation" and direction.get("key"):
        if direction.get("kind") == "cardinal":
            return f"{direction['key']}_elevation"
        return f"elevation_axis_{direction['key'].lower()}"
    if role == "section" and direction.get("key"):
        return f"section_{direction['key'].lower()}"
    return f"{role}_{view_id.lower().replace('-', '_')}"


def _axis_inventory(texts: list[dict[str, Any]]) -> dict[str, Any]:
    def is_axis_label(value: str) -> bool:
        # Architectural grid bubbles normally use a short alphabetic label or
        # a one/two-digit integer.  Longer numeric strings on AXIS layers are
        # commonly dimensions or offsets and must not contaminate topology.
        return re.fullmatch(r"(?:[A-Za-z]{1,3}|\d{1,2})", value) is not None

    candidates = [
        item for item in texts
        if "AXIS" in str(item.get("layer") or "").upper()
        and is_axis_label(str(item["text"]).strip())
    ]
    labels = sorted(
        set(str(item["text"]).strip() for item in candidates),
        key=lambda value: (0, int(value)) if value.isdigit() else (1, value.upper()),
    )
    numeric = [value for value in labels if value.isdigit()]
    alphabetic = [value for value in labels if value.isalpha()]
    terminal = []
    for family in (numeric, alphabetic):
        if family:
            terminal.extend((family[0], family[-1]))
    terminal = list(dict.fromkeys(terminal))
    return {
        "labels": labels,
        "terminal_labels": terminal,
        "evidence_text_ids": [_text_id(item) for item in candidates],
    }


def _level_inventory(texts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    levels = []
    seen: set[tuple[str, float, float]] = set()
    for item in texts:
        value = str(item["text"]).strip().replace("＋", "+").replace("－", "-")
        if not LEVEL_RE.fullmatch(value):
            continue
        key = (value, round(float(item["x"]), 3), round(float(item["y"]), 3))
        if key in seen:
            continue
        seen.add(key)
        levels.append({
            "value": value,
            "x": float(item["x"]),
            "y": float(item["y"]),
            "evidence_text_id": _text_id(item),
        })
    return levels


def build_semantic_index(
    view: dict[str, Any],
    all_texts: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    bounds = view["bounds"]
    texts = [item for item in all_texts if _inside(bounds, item)]
    role = str(view.get("role") or "unknown")
    title = str(view.get("title") or "")
    direction = direction_from_title(title, role)
    axes = _axis_inventory(texts)
    dimensions = [item for item in manifest.get("entities", []) if item.get("entity_type") == "DIMENSION"]
    blockers = []
    if role in {"unknown", "mixed"}:
        blockers.append("unresolved_model_driving_role")
    if role in {"elevation", "section"} and direction["confidence"] == "low":
        blockers.append("missing_elevation_or_section_direction_key")
    if not view.get("title_evidence", {}).get("canonical"):
        blockers.append("missing_canonical_title")
    return {
        "title": view.get("title_evidence") or {},
        "role": {
            "value": role,
            "basis": "canonical_title",
            "confidence": "high" if role not in {"unknown", "mixed"} else "low",
        },
        "direction": direction,
        "axis": axes,
        "levels": _level_inventory(texts),
        "dimensions": {
            "count": len(dimensions),
            "evidence_handles": [str(item.get("handle")) for item in dimensions],
        },
        "annotation_text_count": len(texts),
        "qa_view": qa_view_key(title, role, direction, str(view.get("id") or "view")),
        "status": "blocked" if blockers else "candidate",
        "blocking_reasons": blockers,
    }


def summarize_views(views: list[dict[str, Any]]) -> dict[str, Any]:
    inventory_views = [view for view in views if view.get("include", True)]
    model_views = [view for view in inventory_views if view.get("role") in {"plan", "roof_plan", "elevation", "section"}]
    qa_keys = [str(view.get("qa_view") or "") for view in model_views]
    duplicate_keys = sorted({key for key in qa_keys if key and qa_keys.count(key) > 1})
    direction_keys = [
        str((view.get("semantic_index") or {}).get("direction", {}).get("key") or "")
        for view in model_views if view.get("role") in {"elevation", "section"}
    ]
    all_titles_canonical = all(bool((view.get("title_evidence") or {}).get("canonical")) for view in model_views)
    all_model_roles_resolved = all(view.get("role") not in {"unknown", "mixed"} for view in inventory_views)
    all_directional = all(direction_keys)
    return {
        "model_view_count": len(model_views),
        "qa_view_keys": qa_keys,
        "duplicate_qa_view_keys": duplicate_keys,
        "direction_keys": direction_keys,
        "all_titles_canonical": all_titles_canonical,
        "all_model_roles_resolved": all_model_roles_resolved,
        "all_elevations_and_sections_directional": all_directional,
        "status": "blocked" if duplicate_keys or not all_titles_canonical or not all_model_roles_resolved or not all_directional else "candidate",
    }
