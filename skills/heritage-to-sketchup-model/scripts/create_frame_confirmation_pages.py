#!/usr/bin/env python3
"""Build a loss-aware, hierarchical verification package for architectural CAD."""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

from dxf_loading import read_dxf


RELEASE_DATE = "2026-08-05"
FRAME_TOKENS = ("TITLE", "FRAME", "BORDER", "PUB_TITLE", "图框", "图签")
ROLE_PATTERNS = (
    ("roof_plan", re.compile(r"屋顶|屋面|ROOF", re.I)),
    ("section", re.compile(r"剖面|剖视|SECTION", re.I)),
    ("elevation", re.compile(r"立面|ELEVATION|ELEV\b", re.I)),
    ("plan", re.compile(r"平面|PLAN\b", re.I)),
)
MODEL_DRIVING_ROLES = {"plan", "roof_plan", "elevation", "section"}
TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}
EXPANDABLE_TYPES = {"INSERT", "DIMENSION"}


def reading_entity_contrast(entity: Any, properties: Any) -> None:
    # Apply contrast before rendering: WIPEOUT and text masks must still be
    # allowed to paint the white background, not become black foreground.
    properties.color = "#000000"


def create_reading_frontend(context: Any, recorder: Any, config: Any) -> Any:
    from ezdxf.addons.drawing import Frontend

    class ReadingFrontend(Frontend):
        def draw_entities(self, entities: Any, *, filter_func: Any = None) -> None:
            items = list(entities)
            originals = [item.source_of_copy for item in items]
            # ezdxf's INSERT renderer currently ignores block SORTENTSTABLE.
            # Apply the source table only to a single virtual block expansion.
            if originals and all(item is not None for item in originals):
                owners = {item.dxf.owner for item in originals}
                source = originals[0]
                owner = source.doc.entitydb.get(source.dxf.owner) if source.doc is not None else None
                block = (source.get_layout() if len(owners) == 1 and owner is not None
                         and owner.dxftype() == "BLOCK_RECORD" else None)
                mapping = dict(block.get_redraw_order()) if block is not None else {}
                if mapping:
                    def order(pair: Any) -> int:
                        handle = pair[1].dxf.handle
                        return int(mapping.get(handle, handle), 16) or 0xFFFFFFFFFFFFFFFF
                    items = [pair[0] for pair in sorted(zip(items, originals), key=order)]
            super().draw_entities(items, filter_func=filter_func)

    frontend = ReadingFrontend(context, recorder, config=config)
    frontend.push_property_override_function(reading_entity_contrast)
    return frontend


def clean_text(value: str) -> str:
    value = value.replace("\\P", " ").replace("{", "").replace("}", "")
    value = re.sub(r"\\[A-Za-z][^;]*;", "", value)
    return " ".join(value.split()).strip()


def infer_roles(title: str) -> list[str]:
    roles = [role for role, pattern in ROLE_PATTERNS if pattern.search(title)]
    if "roof_plan" in roles and "plan" in roles:
        roles.remove("plan")
    return roles or ["unknown"]


def infer_role(title: str) -> str:
    roles = infer_roles(title)
    return roles[0] if len(roles) == 1 else "mixed"


def area(bounds: dict[str, float]) -> float:
    return max(bounds["xmax"] - bounds["xmin"], 0.0) * max(bounds["ymax"] - bounds["ymin"], 0.0)


def center(bounds: dict[str, float]) -> tuple[float, float]:
    return ((bounds["xmin"] + bounds["xmax"]) / 2.0, (bounds["ymin"] + bounds["ymax"]) / 2.0)


def point_inside(bounds: dict[str, float], x: float, y: float, tolerance: float = 0.0) -> bool:
    return (
        bounds["xmin"] - tolerance <= x <= bounds["xmax"] + tolerance
        and bounds["ymin"] - tolerance <= y <= bounds["ymax"] + tolerance
    )


def bounds_intersect(a: dict[str, float], b: dict[str, float]) -> bool:
    return not (
        a["xmax"] < b["xmin"]
        or a["xmin"] > b["xmax"]
        or a["ymax"] < b["ymin"]
        or a["ymin"] > b["ymax"]
    )


def contains(outer: dict[str, float], inner: dict[str, float], tolerance: float = 1.0) -> bool:
    return (
        outer["xmin"] <= inner["xmin"] + tolerance
        and outer["ymin"] <= inner["ymin"] + tolerance
        and outer["xmax"] >= inner["xmax"] - tolerance
        and outer["ymax"] >= inner["ymax"] - tolerance
    )


def ezdxf_bounds(entity: Any, cache: Any) -> dict[str, float] | None:
    try:
        from ezdxf import bbox

        extents = bbox.extents([entity], fast=False, cache=cache)
        if not extents.has_data:
            return None
        return {
            "xmin": float(extents.extmin.x),
            "ymin": float(extents.extmin.y),
            "xmax": float(extents.extmax.x),
            "ymax": float(extents.extmax.y),
        }
    except Exception:
        return None


def polyline_points(entity: Any) -> list[tuple[float, float]]:
    if entity.dxftype() == "LWPOLYLINE":
        return [(float(p[0]), float(p[1])) for p in entity.get_points()]
    if entity.dxftype() == "POLYLINE":
        return [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
    return []


def rectangular_bounds(points: list[tuple[float, float]]) -> dict[str, float] | None:
    if len(points) < 4:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    result = {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)}
    width = result["xmax"] - result["xmin"]
    height = result["ymax"] - result["ymin"]
    if width <= 0 or height <= 0:
        return None
    tolerance = max(width, height) * 1e-5 + 0.01
    corners = (
        (result["xmin"], result["ymin"]),
        (result["xmin"], result["ymax"]),
        (result["xmax"], result["ymin"]),
        (result["xmax"], result["ymax"]),
    )
    if not all(any(math.dist(point, corner) <= tolerance for point in points) for corner in corners):
        return None
    if not all(
        abs(x - result["xmin"]) <= tolerance
        or abs(x - result["xmax"]) <= tolerance
        or abs(y - result["ymin"]) <= tolerance
        or abs(y - result["ymax"]) <= tolerance
        for x, y in points
    ):
        return None
    return result


def frameish(value: str) -> bool:
    upper = value.upper()
    return any(token.upper() in upper for token in FRAME_TOKENS)


def line_rectangle_candidates(layout: Any, min_width: float, min_height: float) -> list[dict[str, Any]]:
    by_layer: dict[str, list[tuple[tuple[float, float], tuple[float, float]]]] = defaultdict(list)
    for entity in layout:
        if entity.dxftype() != "LINE":
            continue
        layer = str(getattr(entity.dxf, "layer", ""))
        if not frameish(layer):
            continue
        start = (float(entity.dxf.start.x), float(entity.dxf.start.y))
        end = (float(entity.dxf.end.x), float(entity.dxf.end.y))
        by_layer[layer].append((start, end))

    candidates: list[dict[str, Any]] = []
    for layer, lines in by_layer.items():
        horizontal: dict[tuple[float, float, float], bool] = {}
        vertical: dict[tuple[float, float, float], bool] = {}
        for start, end in lines:
            x1, y1 = start
            x2, y2 = end
            if abs(y1 - y2) <= 0.1:
                horizontal[(round(min(x1, x2), 1), round(max(x1, x2), 1), round(y1, 1))] = True
            elif abs(x1 - x2) <= 0.1:
                vertical[(round(x1, 1), round(min(y1, y2), 1), round(max(y1, y2), 1))] = True
        horizontal_keys = list(horizontal)
        for index, (xmin, xmax, y1) in enumerate(horizontal_keys):
            if xmax - xmin < min_width:
                continue
            for oxmin, oxmax, y2 in horizontal_keys[index + 1 :]:
                if abs(xmin - oxmin) > 0.2 or abs(xmax - oxmax) > 0.2 or abs(y2 - y1) < min_height:
                    continue
                ymin, ymax = sorted((y1, y2))
                if (xmin, ymin, ymax) in vertical and (xmax, ymin, ymax) in vertical:
                    bounds = {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}
                    candidates.append(
                        {
                            "bounds": bounds,
                            "layer": layer,
                            "method": "line_rectangle",
                            "area": area(bounds),
                        }
                    )
    return candidates


def detect_frames_in_layout(
    layout: Any,
    layout_name: str,
    min_width: float,
    min_height: float,
    cache: Any,
) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for entity in layout:
        kind = entity.dxftype()
        layer = str(getattr(entity.dxf, "layer", ""))
        if kind in {"LWPOLYLINE", "POLYLINE"} and frameish(layer):
            bounds = rectangular_bounds(polyline_points(entity))
            method = "closed_polyline"
        elif kind == "INSERT" and (frameish(layer) or frameish(str(getattr(entity.dxf, "name", "")))):
            bounds = ezdxf_bounds(entity, cache)
            method = "title_block_insert"
        else:
            continue
        if bounds is None:
            continue
        width = bounds["xmax"] - bounds["xmin"]
        height = bounds["ymax"] - bounds["ymin"]
        if width >= min_width and height >= min_height:
            raw.append(
                {
                    "bounds": bounds,
                    "layer": layer,
                    "layout": layout_name,
                    "method": method,
                    "area": area(bounds),
                }
            )
    raw.extend(
        {
            **item,
            "layout": layout_name,
        }
        for item in line_rectangle_candidates(layout, min_width, min_height)
    )

    selected: list[dict[str, Any]] = []
    for candidate in sorted(raw, key=lambda item: item["area"], reverse=True):
        candidate_center = center(candidate["bounds"])
        duplicate = False
        for existing in selected:
            existing_center = center(existing["bounds"])
            center_tolerance = max(
                min_width * 0.02,
                min_height * 0.02,
                10.0,
            )
            same_center = math.dist(candidate_center, existing_center) <= center_tolerance
            nested = contains(existing["bounds"], candidate["bounds"], tolerance=center_tolerance)
            if same_center or nested:
                duplicate = True
                break
        if not duplicate:
            selected.append(candidate)
    return selected


def layout_bounds(layout: Any, cache: Any) -> dict[str, float] | None:
    try:
        from ezdxf import bbox

        extents = bbox.extents(layout, fast=False, cache=cache)
        if not extents.has_data:
            return None
        return {
            "xmin": float(extents.extmin.x),
            "ymin": float(extents.extmin.y),
            "xmax": float(extents.extmax.x),
            "ymax": float(extents.extmax.y),
        }
    except Exception:
        return None


def iter_expanded(entity: Any, depth: int = 0) -> Iterator[Any]:
    if depth >= 32 or entity.dxftype() not in EXPANDABLE_TYPES:
        yield entity
        return
    if entity.dxftype() == "INSERT":
        for attrib in getattr(entity, "attribs", []):
            yield attrib
    try:
        children = list(entity.virtual_entities())
    except Exception:
        yield entity
        return
    if not children:
        yield entity
        return
    for child in children:
        yield from iter_expanded(child, depth + 1)


def text_record(entity: Any) -> dict[str, Any] | None:
    if entity.dxftype() not in TEXT_TYPES:
        return None
    insert = getattr(entity.dxf, "insert", None)
    if insert is None:
        return None
    if entity.dxftype() == "MTEXT" and hasattr(entity, "plain_text"):
        raw = entity.plain_text()
    else:
        raw = getattr(entity.dxf, "text", "")
    text = clean_text(str(raw))
    if not text:
        return None
    height = getattr(entity.dxf, "char_height", None)
    if height is None:
        height = getattr(entity.dxf, "height", 0.0)
    return {
        "text": text,
        "x": float(insert.x),
        "y": float(insert.y),
        "height": float(height or 0.0),
        "rotation": float(getattr(entity.dxf, "rotation", 0.0) or 0.0),
        "handle": str(getattr(entity.dxf, "handle", "") or ""),
        "layer": str(getattr(entity.dxf, "layer", "")),
        "entity_type": entity.dxftype(),
    }


def collect_texts(layout: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, float, float]] = set()
    for source in layout:
        for entity in iter_expanded(source):
            record = text_record(entity)
            if record is None:
                continue
            key = (record["text"], round(record["x"], 3), round(record["y"], 3))
            if key not in seen:
                records.append(record)
                seen.add(key)
    return records


def title_records(frame: dict[str, Any], texts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    inside = [
        item
        for item in texts
        if point_inside(frame["bounds"], item["x"], item["y"])
        and any(role in MODEL_DRIVING_ROLES for role in infer_roles(item["text"]))
        and len(item["text"]) <= 32
        and not re.match(r"^(注|说明|备注)\s*[:：]?", item["text"])
        and "详见" not in item["text"]
    ]
    inside.sort(key=lambda item: (-item["y"], item["x"], item["text"]))
    return inside


def sheet_title(frame: dict[str, Any], texts: list[dict[str, Any]]) -> str:
    candidates = title_records(frame, texts)
    frame_layer_titles = [item for item in candidates if item["layer"] == frame["layer"]]
    if frame_layer_titles:
        return frame_layer_titles[-1]["text"]
    return candidates[0]["text"] if candidates else "未识别图名"


def load_view_segmenter() -> Any:
    module_path = Path(__file__).resolve().with_name("view_segmentation.py")
    spec = importlib.util.spec_from_file_location("cad_view_segmentation", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load view segmenter: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.segment_views


def load_cad_semantics() -> Any:
    module_path = Path(__file__).resolve().with_name("cad_semantics.py")
    spec = importlib.util.spec_from_file_location("cad_semantics", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CAD semantics: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def segmentation_entities(
    layout: Any,
    frame: dict[str, Any],
    cache: Any,
) -> list[dict[str, Any]]:
    """Collect geometry evidence while suppressing borders and long shared guides."""
    frame_bounds = frame["bounds"]
    frame_width = max(frame_bounds["xmax"] - frame_bounds["xmin"], 1.0)
    frame_height = max(frame_bounds["ymax"] - frame_bounds["ymin"], 1.0)
    records: list[dict[str, Any]] = []
    for index, entity in enumerate(layout):
        entity_type = entity.dxftype()
        layer = str(getattr(entity.dxf, "layer", ""))
        if entity_type in TEXT_TYPES or layer == frame.get("layer") or frameish(layer):
            continue
        entity_bounds = ezdxf_bounds(entity, cache)
        if entity_bounds is None or not bounds_intersect(frame_bounds, entity_bounds):
            continue
        width = max(entity_bounds["xmax"] - entity_bounds["xmin"], 0.0)
        height = max(entity_bounds["ymax"] - entity_bounds["ymin"], 0.0)
        if width >= frame_width * 0.92 and height >= frame_height * 0.92:
            continue
        if (
            entity_type == "INSERT"
            and height >= frame_height * 0.9
            and entity_bounds["xmin"] >= frame_bounds["xmin"] + frame_width * 0.75
        ):
            # Full-height blocks in the right title strip are sheet metadata,
            # not drawing-view geometry clusters.
            continue
        weight = 1.0
        protected = True
        if entity_type in {"DIMENSION", "LEADER", "MLEADER"}:
            weight = 0.25
            protected = False
        elif (
            entity_type in {"XLINE", "RAY"}
            or (
                (width >= frame_width * 0.75 or height >= frame_height * 0.75)
                and any(token in layer.upper() for token in ("AXIS", "GRID", "DOTE", "DIM", "GUIDE", "定位", "辅助"))
            )
        ):
            # Ground lines, axes, and reference guides may span views. Retain
            # them as context but do not let one guide dominate a separator.
            weight = 0.1
            protected = False
        handle = str(getattr(entity.dxf, "handle", "") or f"INDEX-{index}")
        records.append(
            {
                "id": handle,
                "entity_type": entity_type,
                "layer": layer,
                "bounds": entity_bounds,
                "weight": weight,
                "protected": protected,
            }
        )
    return records


def classify_edge_contacts(
    manifest: dict[str, Any],
    crop_bounds: dict[str, float],
    frame_bounds: dict[str, float],
    context_handles: set[str] | None = None,
) -> dict[str, Any]:
    """Explain only traceable frame context and semantic guide contacts."""
    context_handles = context_handles or set()
    edge_handles = set(manifest.get("edge_touching_handles") or [])
    entities = {str(item.get("handle")): item for item in manifest.get("entities") or []}
    frame_width = max(frame_bounds["xmax"] - frame_bounds["xmin"], 1.0)
    frame_height = max(frame_bounds["ymax"] - frame_bounds["ymin"], 1.0)
    explained: list[str] = []
    explanations: list[dict[str, str]] = []
    for handle in sorted(edge_handles):
        item = entities.get(handle) or {}
        bounds = item.get("bounds") or {}
        width = max(float(bounds.get("xmax", 0)) - float(bounds.get("xmin", 0)), 0.0)
        height = max(float(bounds.get("ymax", 0)) - float(bounds.get("ymin", 0)), 0.0)
        entity_type = str(item.get("entity_type") or "")
        layer = str(item.get("layer") or "")
        expanded = item.get("expanded_entities") or []
        title_layer_count = sum(
            1 for child in expanded
            if frameish(str(child.get("layer") or ""))
        )
        title_layer_ratio = title_layer_count / max(len(expanded), 1)
        reason = ""
        basis = ""
        if handle in context_handles:
            reason = "low_weight_shared_axis_ground_or_reference_line_crosses_verified_separator"
            basis = "segmentation_context_crossing"
        elif entity_type == "INSERT" and (
            (width >= frame_width * 0.9 and height >= frame_height * 0.9)
            or title_layer_ratio >= 0.5
            or (
                height >= frame_height * 0.9
                and float(bounds.get("xmin", 0)) >= frame_bounds["xmin"] + frame_width * 0.75
                and title_layer_count > 0
            )
        ):
            reason = "complete_title_frame_or_sheet_block_intersects_crop_boundary"
            basis = "frame_spanning_insert"
        elif (
            (width >= frame_width * 0.75 or height >= frame_height * 0.75)
            and any(token in layer.upper() for token in ("AXIS", "GRID", "DOTE", "DIM", "GUIDE", "定位", "辅助"))
        ):
            reason = "semantic_axis_dimension_or_reference_guide_reaches_crop_boundary"
            basis = "layer_semantics_and_frame_span"
        if reason:
            explained.append(handle)
            explanations.append({"handle": handle, "reason": reason, "basis": basis})
    unexplained = sorted(edge_handles - set(explained))
    return {
        "status": "needs_verification" if unexplained else "verified",
        "unexplained_handles": unexplained,
        "explained_handles": explained,
        "explanations": explanations,
        "crop_bounds": crop_bounds,
    }


def view_records(
    frame_id: str,
    frame: dict[str, Any],
    texts: list[dict[str, Any]],
    layout: Any | None = None,
    cache: Any | None = None,
) -> list[dict[str, Any]]:
    candidates = title_records(frame, texts)
    inner = [item for item in candidates if item["layer"] != frame["layer"]]
    selected = inner or candidates
    views: list[dict[str, Any]] = []
    semantics = load_cad_semantics()
    for index, item in enumerate(selected, 1):
        title_evidence = semantics.compose_title(item, texts, frame["bounds"])
        canonical_title = title_evidence["canonical"]
        roles = infer_roles(canonical_title)
        views.append(
            {
                "id": f"{frame_id}-V{index:02d}",
                "frame_id": frame_id,
                "title": canonical_title,
                "raw_title": item["text"],
                "title_evidence": title_evidence,
                "role": roles[0] if len(roles) == 1 else "mixed",
                "detected_roles": roles,
                "title_anchor": {"x": item["x"], "y": item["y"]},
                "title_layer": item["layer"],
                "segmentation_state": "needs_verification",
                "verification_state": "needs_verification",
                "include": True,
                "blocking_reasons": ["requires_view_inventory_verification"],
            }
        )
    if views:
        if len(views) == 1:
            assign_view_bounds(views, frame["bounds"])
        elif layout is not None and cache is not None:
            evidence = segmentation_entities(layout, frame, cache)
            segmented = load_view_segmenter()(views, frame["bounds"], evidence)
            if not segmented:
                assign_view_bounds(views, frame["bounds"], fallback=True)
        else:
            assign_view_bounds(views, frame["bounds"], fallback=True)
        return views
    return [
        {
            "id": f"{frame_id}-V01",
            "frame_id": frame_id,
            "title": "未识别图名",
            "role": "unknown",
            "detected_roles": ["unknown"],
            "title_anchor": None,
            "title_layer": "",
            "segmentation_state": "blocked",
            "verification_state": "needs_verification",
            "include": False,
            "blocking_reasons": ["no_model_driving_view_title_detected"],
        }
    ]


def assign_view_bounds(
    views: list[dict[str, Any]],
    frame_bounds: dict[str, float],
    fallback: bool = False,
) -> None:
    """Partition by title anchors; multi-view use is an explicit low-confidence fallback."""
    if len(views) == 1:
        views[0]["bounds"] = dict(frame_bounds)
        views[0]["segmentation_method"] = "single_view_full_frame"
        views[0]["segmentation_confidence"] = "high"
        views[0]["segmentation_evidence"] = {"assigned_entity_count": None, "separators": []}
        return

    height = max(frame_bounds["ymax"] - frame_bounds["ymin"], 1.0)
    row_tolerance = height * 0.05
    anchored = [view for view in views if isinstance(view.get("title_anchor"), dict)]
    if len(anchored) != len(views):
        return

    rows: list[list[dict[str, Any]]] = []
    for view in sorted(anchored, key=lambda item: float(item["title_anchor"]["y"])):
        anchor_y = float(view["title_anchor"]["y"])
        if not rows:
            rows.append([view])
            continue
        row_y = sum(float(item["title_anchor"]["y"]) for item in rows[-1]) / len(rows[-1])
        if abs(anchor_y - row_y) <= row_tolerance:
            rows[-1].append(view)
        else:
            rows.append([view])

    row_centers = [
        sum(float(item["title_anchor"]["y"]) for item in row) / len(row)
        for row in rows
    ]
    # Architectural view titles are normally placed immediately below their
    # owning drawing. The next row's title is therefore the reliable boundary
    # between vertically stacked views; a midpoint can cut the lower drawing.
    y_edges = [frame_bounds["ymin"]]
    y_edges.extend(row_centers[1:])
    y_edges.append(frame_bounds["ymax"])

    for row_index, row in enumerate(rows):
        ordered = sorted(row, key=lambda item: float(item["title_anchor"]["x"]))
        x_centers = [float(item["title_anchor"]["x"]) for item in ordered]
        x_edges = [frame_bounds["xmin"]]
        x_edges.extend((left + right) / 2.0 for left, right in zip(x_centers, x_centers[1:]))
        x_edges.append(frame_bounds["xmax"])
        for column_index, view in enumerate(ordered):
            view["bounds"] = {
                "xmin": x_edges[column_index],
                "ymin": y_edges[row_index],
                "xmax": x_edges[column_index + 1],
                "ymax": y_edges[row_index + 1],
            }
            view["segmentation_method"] = "title_anchor_grid_fallback" if fallback else "title_anchor_grid"
            view["segmentation_confidence"] = "low" if fallback else "medium"
            view["segmentation_state"] = "blocked" if fallback else "needs_verification"
            view["segmentation_evidence"] = {
                "assigned_entity_count": None,
                "separators": [],
                "fallback_reason": "insufficient_geometry_evidence" if fallback else None,
            }
            if fallback:
                view.setdefault("blocking_reasons", []).append("view_segmentation_used_title_only_fallback")


def entity_census(layout: Any, frame_bounds: dict[str, float], cache: Any) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for entity in layout:
        entity_bounds = ezdxf_bounds(entity, cache)
        if entity_bounds is None:
            insert = getattr(entity.dxf, "insert", None)
            if insert is None or not point_inside(frame_bounds, float(insert.x), float(insert.y)):
                continue
        elif not bounds_intersect(frame_bounds, entity_bounds):
            continue
        counts[entity.dxftype()] += 1
        if entity.dxftype() == "INSERT":
            counts["ATTRIB"] += len(getattr(entity, "attribs", []))
    return dict(sorted(counts.items()))


def source_entity_ids(layout: Any, bounds: dict[str, float], cache: Any) -> list[str]:
    """Return stable top-level DXF handles intersecting a frame or view."""
    identifiers: list[str] = []
    for index, entity in enumerate(layout):
        entity_bounds = ezdxf_bounds(entity, cache)
        if entity_bounds is None:
            insert = getattr(entity.dxf, "insert", None)
            if insert is None or not point_inside(bounds, float(insert.x), float(insert.y)):
                continue
        elif not bounds_intersect(bounds, entity_bounds):
            continue
        handle = getattr(entity.dxf, "handle", None)
        identifiers.append(str(handle or f"{layout.name}:{index}"))
    return identifiers


def bounds_dict(extents: Any) -> dict[str, float] | None:
    if extents is None or not getattr(extents, "has_data", False):
        return None
    return {
        "xmin": float(extents.extmin.x),
        "ymin": float(extents.extmin.y),
        "xmax": float(extents.extmax.x),
        "ymax": float(extents.extmax.y),
    }


def entity_belongs_to_frame(entity: Any, bounds: dict[str, float], entity_bounds: dict[str, float] | None) -> bool:
    """Reject neighboring full-sheet blocks whose extents only graze this frame."""
    insert = getattr(entity.dxf, "insert", None)
    if entity_bounds is None:
        return insert is not None and point_inside(bounds, float(insert.x), float(insert.y))
    if not bounds_intersect(bounds, entity_bounds):
        return False
    if entity.dxftype() != "INSERT" or insert is None:
        return True
    if point_inside(bounds, float(insert.x), float(insert.y)):
        return True
    frame_width = max(bounds["xmax"] - bounds["xmin"], 1.0)
    frame_height = max(bounds["ymax"] - bounds["ymin"], 1.0)
    entity_width = entity_bounds["xmax"] - entity_bounds["xmin"]
    entity_height = entity_bounds["ymax"] - entity_bounds["ymin"]
    is_sheet_sized = entity_width >= frame_width * 0.8 and entity_height >= frame_height * 0.8
    return not is_sheet_sized


def expanded_lineage(entity: Any, cache: Any, path: str = "", depth: int = 0) -> list[dict[str, Any]]:
    """Describe recursively expanded block/dimension leaves with source lineage."""
    if depth >= 32:
        return [{"path": path, "entity_type": entity.dxftype(), "status": "depth_limit"}]
    entity_type = entity.dxftype()
    layer = str(getattr(entity.dxf, "layer", ""))
    handle = str(getattr(entity.dxf, "handle", "") or "")
    record = {
        "path": path or handle or entity_type,
        "entity_type": entity_type,
        "handle": handle,
        "layer": layer,
        "bounds": ezdxf_bounds(entity, cache),
        "virtual": bool(getattr(entity, "is_virtual", False)),
    }
    if entity_type == "INSERT":
        record["block_name"] = str(getattr(entity.dxf, "name", ""))
        children: list[Any] = list(getattr(entity, "attribs", []))
        try:
            children.extend(list(entity.virtual_entities()))
        except Exception:
            record["expansion_status"] = "failed"
            return [record]
    elif entity_type == "DIMENSION":
        try:
            children = list(entity.virtual_entities())
        except Exception:
            record["expansion_status"] = "failed"
            return [record]
    else:
        return [record]
    output = [record]
    for index, child in enumerate(children):
        child_type = child.dxftype()
        child_path = f"{record['path']}/{child_type}[{index}]"
        output.extend(expanded_lineage(child, cache, child_path, depth + 1))
    return output


def source_manifest(layout: Any, bounds: dict[str, float], cache: Any) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    for index, entity in enumerate(layout):
        entity_bounds = ezdxf_bounds(entity, cache)
        if not entity_belongs_to_frame(entity, bounds, entity_bounds):
            continue
        handle = str(getattr(entity.dxf, "handle", "") or f"{layout.name}:{index}")
        descendants = expanded_lineage(entity, cache, handle)
        invisible = bool(getattr(entity.dxf, "invisible", 0))
        layer = str(getattr(entity.dxf, "layer", ""))
        layer_record = entity.doc.layers.get(layer) if entity.doc is not None and layer else None
        layer_hidden = bool(layer_record and (layer_record.is_off() or layer_record.is_frozen()))
        text_value = ""
        if entity.dxftype() == "MTEXT" and hasattr(entity, "plain_text"):
            text_value = str(entity.plain_text())
        elif entity.dxftype() in {"TEXT", "ATTRIB", "ATTDEF"}:
            text_value = str(getattr(entity.dxf, "text", ""))
        empty_text = entity.dxftype() in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"} and not text_value.strip()
        entities.append(
            {
                "handle": handle,
                "entity_type": entity.dxftype(),
                "layer": layer,
                "bounds": entity_bounds,
                "text": text_value,
                "expected_visible": not (invisible or layer_hidden or empty_text),
                "visibility_reason": "invisible_entity" if invisible else "hidden_layer" if layer_hidden else "empty_text" if empty_text else "visible",
                "expanded_entity_count": len(descendants),
                "expanded_entities": descendants,
            }
        )
    return entities


def build_render_manifest(
    layout: Any,
    bounds: dict[str, float],
    cache: Any,
    recorder: Any,
    frame_layer: str = "",
    fallback_rendered: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source = source_manifest(layout, bounds, cache)
    fallback_rendered = fallback_rendered or {}
    rendered: dict[str, dict[str, Any]] = {}
    for record in recorder.records:
        handle = str(record.handle or "")
        if not handle:
            continue
        item = rendered.setdefault(handle, {"primitive_count": 0, "bounds": None})
        item["primitive_count"] += 1
        record_bounds = bounds_dict(record.bbox())
        if record_bounds is not None:
            item["bounds"] = union_bounds(item["bounds"], record_bounds)

    missing = []
    skipped = []
    missing_classes: Counter[str] = Counter()
    edge_touching = []
    output_entities = []
    tolerance_x = max((bounds["xmax"] - bounds["xmin"]) * 0.0005, 1e-6)
    tolerance_y = max((bounds["ymax"] - bounds["ymin"]) * 0.0005, 1e-6)
    for entity in source:
        handle = entity["handle"]
        render = rendered.get(handle)
        if render and render["primitive_count"] > 0:
            status = "rendered"
        elif handle in fallback_rendered:
            status = "fallback_rendered"
            render = fallback_rendered[handle]
        elif not entity["expected_visible"]:
            status = "skipped_not_visible"
            skipped.append(handle)
        else:
            status = "missing"
        if status == "missing":
            missing.append(handle)
            missing_classes[entity["entity_type"]] += 1
        entity_bounds = entity.get("bounds")
        if entity_bounds and entity.get("layer") != frame_layer:
            touches = (
                entity_bounds["xmin"] <= bounds["xmin"] + tolerance_x
                or entity_bounds["xmax"] >= bounds["xmax"] - tolerance_x
                or entity_bounds["ymin"] <= bounds["ymin"] + tolerance_y
                or entity_bounds["ymax"] >= bounds["ymax"] - tolerance_y
            )
            if touches:
                edge_touching.append(handle)
        output_entities.append({**entity, "render": render or {"primitive_count": 0, "bounds": None}, "status": status})

    source_handles = {item["handle"] for item in source}
    orphan_render_handles = sorted(handle for handle in rendered if handle not in source_handles)
    expected_count = sum(1 for item in source if item["expected_visible"])
    rendered_count = expected_count - len(missing)
    return {
        "schema": "cad_reader.render_manifest.2026-08-04",
        "layout": layout.name,
        "bounds": bounds,
        "source_entity_count": len(source),
        "expected_visible_entity_count": expected_count,
        "rendered_entity_count": rendered_count,
        "render_primitive_count": len(recorder.records),
        "coverage_ratio": 1.0 if not expected_count else rendered_count / expected_count,
        "missing_handles": sorted(missing),
        "missing_entity_classes": dict(sorted(missing_classes.items())),
        "fallback_rendered_handles": sorted(fallback_rendered),
        "skipped_not_visible_handles": sorted(skipped),
        "orphan_render_handles": orphan_render_handles,
        "edge_touching_handles": sorted(set(edge_touching)),
        "entities": output_entities,
    }


def render_missing_text_fallbacks(
    axis: Any,
    figure: Any,
    layout: Any,
    bounds: dict[str, float],
    cache: Any,
    recorder: Any,
) -> dict[str, dict[str, Any]]:
    """Render visible TEXT/MTEXT skipped by unavailable SHX fonts."""
    from matplotlib import font_manager

    recorded_handles = {str(record.handle) for record in recorder.records if record.handle}
    font_path = None
    for family in ("Microsoft YaHei", "SimSun", "Noto Sans CJK SC", "Arial Unicode MS"):
        try:
            font_path = font_manager.findfont(family, fallback_to_default=False)
            break
        except ValueError:
            continue
    font_properties = font_manager.FontProperties(fname=font_path) if font_path else font_manager.FontProperties()
    data_height = max(bounds["ymax"] - bounds["ymin"], 1.0)
    axis_height_inches = max(figure.get_figheight() * 0.98, 0.1)
    data_per_point = data_height / (axis_height_inches * 72.0)
    output: dict[str, dict[str, Any]] = {}
    for entity in layout:
        entity_type = entity.dxftype()
        if entity_type not in {"TEXT", "MTEXT"}:
            continue
        handle = str(getattr(entity.dxf, "handle", "") or "")
        if not handle or handle in recorded_handles or bool(getattr(entity.dxf, "invisible", 0)):
            continue
        layer_name = str(getattr(entity.dxf, "layer", ""))
        layer_record = entity.doc.layers.get(layer_name) if entity.doc is not None and layer_name else None
        if layer_record and (layer_record.is_off() or layer_record.is_frozen()):
            continue
        entity_bounds = ezdxf_bounds(entity, cache)
        if entity_bounds is None or not bounds_intersect(bounds, entity_bounds):
            continue
        if entity_type == "MTEXT" and hasattr(entity, "plain_text"):
            value = str(entity.plain_text())
            height = float(getattr(entity.dxf, "char_height", 1.0) or 1.0)
        else:
            value = str(getattr(entity.dxf, "text", ""))
            height = float(getattr(entity.dxf, "height", 1.0) or 1.0)
        if not value.strip():
            continue
        insert = getattr(entity.dxf, "insert", None)
        if insert is None:
            continue
        if not point_inside(bounds, float(insert.x), float(insert.y)):
            continue
        font_size = max(2.0, min(48.0, height / data_per_point * 0.82))
        rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
        axis.text(
            float(insert.x),
            float(insert.y),
            value,
            fontsize=font_size,
            fontproperties=font_properties,
            rotation=rotation,
            color="black",
            ha="left",
            va="baseline",
            clip_on=True,
        )
        output[handle] = {
            "primitive_count": 1,
            "bounds": entity_bounds,
            "method": "unicode_font_fallback",
            "font": font_path or "matplotlib_default",
        }
    return output


def union_bounds(left: dict[str, float] | None, right: dict[str, float]) -> dict[str, float]:
    if left is None:
        return dict(right)
    return {
        "xmin": min(left["xmin"], right["xmin"]),
        "ymin": min(left["ymin"], right["ymin"]),
        "xmax": max(left["xmax"], right["xmax"]),
        "ymax": max(left["ymax"], right["ymax"]),
    }


def slice_render_manifest(
    frame_manifest: dict[str, Any],
    bounds: dict[str, float],
    frame_layer: str = "",
) -> dict[str, Any]:
    """Derive exact per-view coverage from one owning-frame render pass."""
    entities = []
    missing_classes: Counter[str] = Counter()
    edge_touching: list[str] = []
    tolerance_x = max((bounds["xmax"] - bounds["xmin"]) * 0.0005, 1e-6)
    tolerance_y = max((bounds["ymax"] - bounds["ymin"]) * 0.0005, 1e-6)
    for entity in frame_manifest.get("entities", []):
        entity_bounds = entity.get("bounds")
        if entity_bounds is None or not bounds_intersect(bounds, entity_bounds):
            continue
        entities.append(entity)
        if entity.get("status") == "missing":
            missing_classes[entity["entity_type"]] += 1
        if entity.get("layer") != frame_layer:
            touches = (
                entity_bounds["xmin"] <= bounds["xmin"] + tolerance_x
                or entity_bounds["xmax"] >= bounds["xmax"] - tolerance_x
                or entity_bounds["ymin"] <= bounds["ymin"] + tolerance_y
                or entity_bounds["ymax"] >= bounds["ymax"] - tolerance_y
            )
            if touches:
                edge_touching.append(entity["handle"])
    expected = [item for item in entities if item.get("expected_visible")]
    missing = [item["handle"] for item in expected if item.get("status") == "missing"]
    fallback = [item["handle"] for item in entities if item.get("status") == "fallback_rendered"]
    skipped = [item["handle"] for item in entities if item.get("status") == "skipped_not_visible"]
    rendered_count = len(expected) - len(missing)
    return {
        "schema": "cad_reader.render_manifest.2026-08-04",
        "layout": frame_manifest.get("layout"),
        "bounds": bounds,
        "derived_from_frame_render": True,
        "source_entity_count": len(entities),
        "expected_visible_entity_count": len(expected),
        "rendered_entity_count": rendered_count,
        "render_primitive_count": sum(int((item.get("render") or {}).get("primitive_count", 0)) for item in entities),
        "coverage_ratio": 1.0 if not expected else rendered_count / len(expected),
        "missing_handles": sorted(missing),
        "missing_entity_classes": dict(sorted(missing_classes.items())),
        "fallback_rendered_handles": sorted(fallback),
        "skipped_not_visible_handles": sorted(skipped),
        "orphan_render_handles": [],
        "edge_touching_handles": sorted(set(edge_touching)),
        "entities": entities,
    }


def write_trace_bounds_svg(manifest: dict[str, Any], path: Path) -> None:
    bounds = manifest["bounds"]
    width = max(bounds["xmax"] - bounds["xmin"], 1.0)
    height = max(bounds["ymax"] - bounds["ymin"], 1.0)
    rows = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{bounds["xmin"]} {-bounds["ymax"]} {width} {height}">',
        '<g fill="none" vector-effect="non-scaling-stroke">',
    ]
    for entity in manifest["entities"]:
        entity_bounds = entity.get("bounds")
        if not entity_bounds:
            continue
        color = "#e11d48" if entity["status"] == "missing" else "#0284c7"
        rect_width = max(entity_bounds["xmax"] - entity_bounds["xmin"], width * 0.0001)
        rect_height = max(entity_bounds["ymax"] - entity_bounds["ymin"], height * 0.0001)
        rows.append(
            f'<rect id="entity-{html.escape(entity["handle"])}" '
            f'data-dxf-type="{html.escape(entity["entity_type"])}" '
            f'data-layer="{html.escape(entity["layer"])}" '
            f'x="{entity_bounds["xmin"]}" y="{-entity_bounds["ymax"]}" '
            f'width="{rect_width}" height="{rect_height}" stroke="{color}" stroke-width="1"/>'
        )
    rows.extend(["</g>", "</svg>"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def render_frame(
    doc: Any,
    layout: Any,
    bounds: dict[str, float],
    cache: Any,
    jpg_path: Path,
    svg_path: Path | None,
    max_width: int,
    max_height: int,
    manifest_path: Path,
    trace_svg_path: Path,
    frame_layer: str = "",
) -> dict[str, Any]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration, HatchPolicy
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.addons.drawing.recorder import Recorder

    width = max(bounds["xmax"] - bounds["xmin"], 1.0)
    height = max(bounds["ymax"] - bounds["ymin"], 1.0)
    scale = min(max_width / width, max_height / height)
    image_width = max(2400, min(max_width, int(width * scale)))
    image_height = max(1600, min(max_height, int(height * scale)))
    dpi = 200
    figure = plt.figure(figsize=(image_width / dpi, image_height / dpi), dpi=dpi, facecolor="white")
    axis = figure.add_axes((0.01, 0.01, 0.98, 0.98), facecolor="white")
    config = Configuration(
        background_policy=BackgroundPolicy.WHITE,
        color_policy=ColorPolicy.COLOR,
        # Reading sheets prioritize boundaries, dimensions, and labels. Dense
        # material hatches can otherwise obscure the geometry they describe.
        hatch_policy=HatchPolicy.SHOW_OUTLINE,
        lineweight_scaling=0.55,
        min_lineweight=1.2,
    )
    def entity_in_frame(entity: Any) -> bool:
        entity_bounds = ezdxf_bounds(entity, cache)
        return entity_belongs_to_frame(entity, bounds, entity_bounds)

    recorder = Recorder()
    frontend = create_reading_frontend(RenderContext(doc), recorder, config)
    frontend.draw_layout(
        layout,
        finalize=False,
        filter_func=entity_in_frame,
    )
    # ezdxf's default backend finalizer resizes the figure to Matplotlib's
    # default inches, silently reducing a requested 6000 px sheet to ~1200 px.
    recorder.player().replay(
        MatplotlibBackend(axis, adjust_figure=False),
    )
    fallback_rendered = render_missing_text_fallbacks(axis, figure, layout, bounds, cache, recorder)
    margin_x = width * 0.005
    margin_y = height * 0.005
    axis.set_xlim(bounds["xmin"] - margin_x, bounds["xmax"] + margin_x)
    axis.set_ylim(bounds["ymin"] - margin_y, bounds["ymax"] + margin_y)
    axis.set_aspect("equal", adjustable="box")
    axis.axis("off")
    jpg_path.parent.mkdir(parents=True, exist_ok=True)
    if svg_path is not None:
        svg_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(jpg_path, dpi=dpi, facecolor="white", pil_kwargs={"quality": 96})
    if svg_path is not None:
        vector_format = svg_path.suffix.lower().lstrip(".") or "pdf"
        figure.savefig(svg_path, format=vector_format, facecolor="white")
    plt.close(figure)
    manifest = build_render_manifest(
        layout,
        bounds,
        cache,
        recorder,
        frame_layer=frame_layer,
        fallback_rendered=fallback_rendered,
    )
    manifest["render_output"] = {
        "pixel_width": image_width,
        "pixel_height": image_height,
        "dpi": dpi,
        "style": "high-contrast-reading",
        "color_policy": "black-entities-white-masks",
        "source_transparency": "forced-opaque",
        "block_draw_order": "source-sortentstable",
        "hatch_policy": "outline-only",
        "lineweight_scaling": 0.55,
        "minimum_lineweight_300th_inch": 1.2,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_trace_bounds_svg(manifest, trace_svg_path)
    return manifest


def iter_layouts(doc: Any) -> Iterable[Any]:
    yield doc.modelspace()
    for layout in doc.layouts:
        if layout.name.lower() == "model" or len(layout) == 0:
            continue
        yield layout


def create_pages(
    cad_file: Path,
    work_dir: Path,
    min_width: float,
    min_height: float,
    max_width: int,
    max_height: int,
    view_vector_masters: bool = False,
    vector_format: str = "pdf",
    view_detail_pages: bool = False,
) -> dict[str, Any]:
    from ezdxf import bbox

    doc, source_encoding = read_dxf(cad_file)
    cache = bbox.Cache()
    layouts = {layout.name: layout for layout in iter_layouts(doc)}
    frames: list[dict[str, Any]] = []
    fallback_frames: list[dict[str, Any]] = []
    layout_texts: dict[str, list[dict[str, Any]]] = {}

    for layout_name, layout in layouts.items():
        layout_texts[layout_name] = collect_texts(layout)
        detected = detect_frames_in_layout(layout, layout_name, min_width, min_height, cache)
        frames.extend(detected)
        if not detected:
            bounds = layout_bounds(layout, cache)
            if bounds is not None:
                fallback_frames.append(
                    {
                        "bounds": bounds,
                        "layer": "",
                        "layout": layout_name,
                        "method": "layout_extents_fallback",
                        "area": area(bounds),
                    }
                )
    if not frames:
        frames = fallback_frames

    frames.sort(key=lambda item: (item["layout"], item["bounds"]["ymin"], item["bounds"]["xmin"]))
    jpg_dir = work_dir / "frame_pages_jpg"
    svg_dir = work_dir / "frame_pages_vector"
    view_jpg_dir = work_dir / "view_pages_jpg"
    view_svg_dir = work_dir / "view_pages_vector"
    manifest_dir = work_dir / "render_manifests"
    trace_dir = work_dir / "trace_bounds_svg"
    frame_records: list[dict[str, Any]] = []
    regions: list[dict[str, Any]] = []

    for index, frame in enumerate(frames, 1):
        frame_id = f"F{index:02d}"
        layout = layouts[frame["layout"]]
        texts = layout_texts[frame["layout"]]
        title = sheet_title(frame, texts)
        views = view_records(frame_id, frame, texts, layout, cache)
        jpg_path = jpg_dir / f"{frame_id}.jpg"
        svg_path = svg_dir / f"{frame_id}.{vector_format}"
        frame_manifest_path = manifest_dir / f"{frame_id}.json"
        frame_trace_path = trace_dir / f"{frame_id}.svg"
        frame_manifest = render_frame(
            doc,
            layout,
            frame["bounds"],
            cache,
            jpg_path,
            svg_path,
            max_width,
            max_height,
            frame_manifest_path,
            frame_trace_path,
            frame_layer=frame["layer"],
        )
        for view in views:
            view_bounds = view.get("bounds")
            if not isinstance(view_bounds, dict):
                continue
            view_jpg_path = view_jpg_dir / f"{view['id']}.jpg"
            view_svg_path = view_svg_dir / f"{view['id']}.{vector_format}" if view_vector_masters else None
            view_manifest_path = manifest_dir / f"{view['id']}.json"
            view_trace_path = trace_dir / f"{view['id']}.svg"
            if view_detail_pages:
                view_manifest = render_frame(
                    doc,
                    layout,
                    view_bounds,
                    cache,
                    view_jpg_path,
                    view_svg_path,
                    max_width,
                    max_height,
                    view_manifest_path,
                    view_trace_path,
                    frame_layer=frame["layer"],
                )
                view["view_preview_jpg"] = str(view_jpg_path)
                if view_svg_path is not None:
                    view["view_vector_master"] = str(view_svg_path)
            else:
                view_manifest = slice_render_manifest(frame_manifest, view_bounds, frame_layer=frame["layer"])
                view_manifest_path.parent.mkdir(parents=True, exist_ok=True)
                view_manifest_path.write_text(json.dumps(view_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                write_trace_bounds_svg(view_manifest, view_trace_path)
            view["render_manifest"] = str(view_manifest_path)
            view["trace_bounds_svg"] = str(view_trace_path)
            view["source_entity_ids"] = [item["handle"] for item in view_manifest["entities"]]
            semantics = load_cad_semantics()
            view["semantic_index"] = semantics.build_semantic_index(view, texts, view_manifest)
            view["qa_view"] = view["semantic_index"]["qa_view"]
            if view["semantic_index"]["status"] == "blocked":
                view["blocking_reasons"].extend(view["semantic_index"]["blocking_reasons"])
            context_handles = set(
                view.get("segmentation_evidence", {}).get("context_crossing_entity_ids") or []
            )
            edge_review = classify_edge_contacts(
                view_manifest,
                view_bounds,
                frame["bounds"],
                context_handles,
            )
            view["vector_coverage"] = {
                "status": "verified" if not view_manifest["missing_handles"] else "blocked",
                "coverage_ratio": view_manifest["coverage_ratio"],
                "missing_handles": view_manifest["missing_handles"],
                "missing_entity_classes": view_manifest["missing_entity_classes"],
                "fallback_rendered_handles": view_manifest["fallback_rendered_handles"],
                "edge_touching_handles": view_manifest["edge_touching_handles"],
                "edge_review": edge_review,
            }
        census = entity_census(layout, frame["bounds"], cache)
        detected_roles = sorted(
            {
                role
                for view in views
                for role in view["detected_roles"]
                if role in MODEL_DRIVING_ROLES
            }
        )
        frame_edge_review = classify_edge_contacts(
            frame_manifest,
            frame["bounds"],
            frame["bounds"],
        )
        frame_record = {
            "id": frame_id,
            "layout": frame["layout"],
            "title": title,
            "role": detected_roles[0] if len(detected_roles) == 1 else "mixed",
            "detected_roles": detected_roles,
            "bounds": frame["bounds"],
            "frame_layer": frame["layer"],
            "detection_method": frame["method"],
            "preview_jpg": str(jpg_path),
            "vector_master": str(svg_path),
            "render_manifest": str(frame_manifest_path),
            "trace_bounds_svg": str(frame_trace_path),
            "entity_census": census,
            "view_count": len(views),
            "mixed_view_types": len(detected_roles) > 1,
            "verification_state": "needs_verification",
            "render_coverage": {
                "status": "blocked" if frame_manifest["missing_handles"] else "needs_visual_review",
                "vector_status": "blocked" if frame_manifest["missing_handles"] else "verified",
                "coverage_ratio": frame_manifest["coverage_ratio"],
                "missing_handles": frame_manifest["missing_handles"],
                "missing_entity_classes": frame_manifest["missing_entity_classes"],
                "fallback_rendered_handles": frame_manifest["fallback_rendered_handles"],
                "edge_touching_handles": frame_manifest["edge_touching_handles"],
                "edge_review": frame_edge_review,
                "checks": {
                    "dimensions_present_when_in_cad": "DIMENSION" not in frame_manifest["missing_entity_classes"],
                    "text_and_attributes_present_when_in_cad": not any(
                        kind in frame_manifest["missing_entity_classes"]
                        for kind in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF")
                    ),
                    "blocks_expanded_or_visually_represented": "INSERT" not in frame_manifest["missing_entity_classes"],
                    "full_border_visible": True,
                    "readable_at_review_resolution": False,
                },
            },
            "views": views,
            "blocking_reasons": ["requires_full_frame_and_render_coverage_verification"],
        }
        frame_records.append(frame_record)
        for view in views:
            region = {
                **view,
                "preview_jpg": str(jpg_path),
                "vector_master": str(svg_path),
                "bounds": view.get("bounds"),
                "qa_view": view.get("qa_view", ""),
                "reading_checks": {
                    "full_frame": False,
                    "title_visible": False,
                    "readable_annotations": False,
                    "axis_bubbles_visible": False,
                    "not_clipped": False,
                    "separated_drawing_type": False,
                    "source_coverage_checked": False,
                    "view_inventory_confirmed": False,
                },
            }
            regions.append(region)

    semantics = load_cad_semantics()
    package = {
        "schema": f"cad_reader.verification.{RELEASE_DATE}",
        "skill_release": RELEASE_DATE,
        "source_file": str(cad_file),
        "source_kind": cad_file.suffix.lower().lstrip("."),
        "source_encoding": source_encoding,
        "units_code": int(doc.header.get("$INSUNITS", 0) or 0),
        "verification_required": True,
        "verification_mode": "one_full_resolution_page_per_frame_plus_view_inventory",
        "render_engine": "ezdxf_drawing_matplotlib",
        "frame_count": len(frame_records),
        "view_count": len(regions),
        "frames": frame_records,
        "regions": regions,
        "semantic_summary": semantics.summarize_views(regions),
        "warnings": [
            "Full-frame pages are authoritative; view entries are an inventory and must not replace the full frame.",
            "Contact sheets are indexes only.",
            "Do not model until every model-driving frame and view inventory is verified.",
        ],
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "frame_candidates.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (work_dir / "region_candidates.json").write_text(
        json.dumps(package, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return package


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create full-frame CAD verification pages and a nested view inventory."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--min-width", type=float, default=50000.0)
    parser.add_argument("--min-height", type=float, default=30000.0)
    parser.add_argument("--max-width", type=int, default=6000)
    parser.add_argument("--max-height", type=int, default=4200)
    parser.add_argument("--view-vector-masters", action="store_true", help="Also export per-view vector masters.")
    parser.add_argument("--vector-format", choices=("pdf", "svg"), default="pdf")
    parser.add_argument("--view-detail-pages", action="store_true", help="Render optional per-view detail JPGs.")
    args = parser.parse_args()
    package = create_pages(
        args.input,
        args.work_dir,
        args.min_width,
        args.min_height,
        args.max_width,
        args.max_height,
        args.view_vector_masters,
        args.vector_format,
        args.view_detail_pages,
    )
    print(
        json.dumps(
            {
                "frames": package["frame_count"],
                "views": package["view_count"],
                "work_dir": str(args.work_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
