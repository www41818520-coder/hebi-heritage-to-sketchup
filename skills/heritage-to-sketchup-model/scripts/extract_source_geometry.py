#!/usr/bin/env python3
"""Extract review-only source geometry candidates from confirmed CAD regions."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from dxf_loading import read_dxf


SOURCE_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "INSERT", "TEXT", "MTEXT"}
SKIP_TYPES = {"DIMENSION", "HATCH", "WIPEOUT", "IMAGE"}
MODEL_DRIVING_REGION_ROLES = {"plan", "roof_plan", "roof plan", "elevation", "section", "elevation_or_section"}

ROLE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("wall", ("WALL", "A-WALL", "墙", "墙体", "砌体")),
    ("column", ("COLUMN", "COL", "柱", "柱网")),
    ("window", ("WINDOW", "WIN", "窗", "窗户")),
    ("door", ("DOOR", "DOR", "门")),
    ("axis", ("AXIS", "GRID", "轴", "轴线")),
    ("stair", ("STAIR", "楼梯", "梯")),
    ("roof", ("ROOF", "屋顶", "屋面", "屋脊", "檐口")),
    ("structure", ("STRUCT", "BEAM", "梁", "结构")),
    ("ground", ("GROUND", "地坪", "室外地面", "±0.000")),
    ("facade", ("ELEV", "FACADE", "立面", "外立面")),
)

SKIP_LAYER_TOKENS = ("DIM", "HATCH", "TITLE", "PUB_TITLE", "图框", "标题栏", "填充", "标注", "尺寸")


@dataclass
class EntityRecord:
    entity_type: str
    layer: str
    parent_block: str
    points: list[tuple[float, float]]
    text: str = ""
    source_entity_id: str = ""


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def bounds_for_points(points: Iterable[tuple[float, float]]) -> dict[str, float] | None:
    pts = list(points)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)}


def intersects(bounds: dict[str, float], region: dict[str, float], pad: float = 0.0) -> bool:
    return not (
        bounds["xmax"] < region["xmin"] - pad
        or bounds["xmin"] > region["xmax"] + pad
        or bounds["ymax"] < region["ymin"] - pad
        or bounds["ymin"] > region["ymax"] + pad
    )


def region_bounds(region: dict[str, Any]) -> dict[str, float] | None:
    raw = region.get("bounds")
    if not isinstance(raw, dict):
        return None
    try:
        return {key: float(raw[key]) for key in ("xmin", "ymin", "xmax", "ymax")}
    except (KeyError, TypeError, ValueError):
        return None


def confirmed_model_regions(region_package: dict[str, Any]) -> list[dict[str, Any]]:
    regions = []
    for region in region_package.get("regions", []):
        role = str(region.get("role", "")).lower().replace("_", " ")
        state = str(region.get("verification_state", region.get("confirmation_state", ""))).lower()
        if role in MODEL_DRIVING_REGION_ROLES and state in {"verified", "confirmed", "user_confirmed", "accepted"}:
            regions.append(region)
    return regions


def validate_region_segmentation(regions: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for region in regions:
        grouped.setdefault(str(region.get("frame_id") or region.get("id", "")), []).append(region)
    for frame_id, frame_regions in grouped.items():
        if len(frame_regions) < 2:
            continue
        signatures = []
        for region in frame_regions:
            bounds = region_bounds(region)
            if bounds is None:
                raise ValueError(f"{frame_id} has a model-driving view without independent bounds.")
            signatures.append(tuple(round(bounds[key], 6) for key in ("xmin", "ymin", "xmax", "ymax")))
        if len(set(signatures)) != len(signatures):
            raise ValueError(
                f"{frame_id} has multiple model-driving views sharing the same bounds; "
                "regenerate region_candidates.json with per-view segmentation."
            )


def clean_text(value: str) -> str:
    return " ".join(value.replace("\\P", " ").replace("{", "").replace("}", "").split())


def likely_role(entity: EntityRecord) -> str:
    if entity.entity_type in {"TEXT", "MTEXT"}:
        return "text"
    haystack = f"{entity.layer} {entity.parent_block} {entity.text}".upper()
    for role, tokens in ROLE_TOKENS:
        if any(token.upper() in haystack for token in tokens):
            return role
    return "context"


def should_skip(entity: EntityRecord) -> bool:
    if entity.entity_type not in SOURCE_TYPES or entity.entity_type in SKIP_TYPES:
        return True
    upper = entity.layer.upper()
    return any(token.upper() in upper for token in SKIP_LAYER_TOKENS)


def parse_ascii_pairs(path: Path) -> list[tuple[str, str]]:
    raw = path.read_bytes()
    if raw.startswith(b"AutoCAD Binary DXF"):
        raise ValueError("Binary DXF is not supported; export ASCII DXF first.")
    lines = raw.decode("utf-8-sig", errors="replace").splitlines()
    return [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]


def numeric(value: str) -> float | None:
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def finish_ascii_entity(current: dict[str, Any] | None, entities: list[EntityRecord]) -> None:
    if not current:
        return
    entity_type = str(current.get("type", ""))
    values = current.get("values", {})
    layer = str(current.get("layer", "Layer0"))
    if entity_type in {"TEXT", "MTEXT"}:
        x = first_number(values.get("10", []))
        y = first_number(values.get("20", []))
        text = clean_text(" ".join(values.get("1", []) + values.get("3", [])))
        if text and x is not None and y is not None:
            entities.append(EntityRecord(entity_type, layer, "", [(x, y)], text))
        return
    points = collect_points(values)
    if points:
        entities.append(EntityRecord(entity_type, layer, "", points))


def first_number(values: list[str]) -> float | None:
    for value in values:
        result = numeric(value)
        if result is not None:
            return result
    return None


def collect_points(values: dict[str, list[str]]) -> list[tuple[float, float]]:
    xs = [numeric(v) for v in values.get("10", []) + values.get("11", [])]
    ys = [numeric(v) for v in values.get("20", []) + values.get("21", [])]
    return [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]


def parse_ascii_entities(path: Path) -> list[EntityRecord]:
    entities: list[EntityRecord] = []
    current: dict[str, Any] | None = None
    in_entities = False
    for code, value in parse_ascii_pairs(path):
        if code == "0" and value == "SECTION":
            current = None
        elif code == "2" and value == "ENTITIES":
            in_entities = True
        elif code == "0" and value == "ENDSEC" and in_entities:
            finish_ascii_entity(current, entities)
            break
        elif not in_entities:
            continue
        elif code == "0":
            finish_ascii_entity(current, entities)
            current = {"type": value, "values": {}}
        elif current is not None:
            current.setdefault("values", {}).setdefault(code, []).append(value)
            if code == "8":
                current["layer"] = value
    return entities


def ezdxf_entities(path: Path) -> list[EntityRecord] | None:
    try:
        import ezdxf  # type: ignore
    except Exception:
        return None

    doc, _ = read_dxf(path)
    records: list[EntityRecord] = []

    def points_for(entity: Any) -> list[tuple[float, float]]:
        kind = entity.dxftype()
        if kind == "LINE":
            return [(float(entity.dxf.start.x), float(entity.dxf.start.y)), (float(entity.dxf.end.x), float(entity.dxf.end.y))]
        if kind == "LWPOLYLINE":
            return [(float(point[0]), float(point[1])) for point in entity.get_points()]
        if kind == "POLYLINE":
            return [(float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in entity.vertices]
        if kind == "CIRCLE":
            center = entity.dxf.center
            radius = float(entity.dxf.radius)
            return [(float(center.x) - radius, float(center.y) - radius), (float(center.x) + radius, float(center.y) + radius)]
        if kind == "ARC":
            center = entity.dxf.center
            radius = float(entity.dxf.radius)
            start = math.radians(float(entity.dxf.start_angle))
            end = math.radians(float(entity.dxf.end_angle))
            if end < start:
                end += math.tau
            return [
                (
                    float(center.x) + math.cos(start + (end - start) * i / 16) * radius,
                    float(center.y) + math.sin(start + (end - start) * i / 16) * radius,
                )
                for i in range(17)
            ]
        if kind in {"TEXT", "MTEXT", "INSERT"}:
            insert = entity.dxf.insert
            return [(float(insert.x), float(insert.y))]
        return []

    def expanded(
        entity: Any,
        parent_layer: str = "",
        block_path: tuple[str, ...] = (),
        source_entity_id: str = "",
        depth: int = 0,
    ) -> Iterable[tuple[Any, str, str, str]]:
        """Recursively expand nested inserts/dimensions while retaining lineage."""
        own_layer = str(getattr(entity.dxf, "layer", ""))
        effective_layer = parent_layer if own_layer in {"", "0"} and parent_layer else own_layer
        source_entity_id = source_entity_id or str(getattr(entity.dxf, "handle", "") or "")
        if depth >= 32 or entity.dxftype() not in {"INSERT", "DIMENSION"}:
            yield entity, effective_layer, "/".join(block_path), source_entity_id
            return
        next_path = block_path
        if entity.dxftype() == "INSERT":
            block_name = str(getattr(entity.dxf, "name", "") or "")
            if block_name:
                next_path = block_path + (block_name,)
        children: list[Any] = list(getattr(entity, "attribs", [])) if entity.dxftype() == "INSERT" else []
        try:
            children.extend(list(entity.virtual_entities()))
        except Exception:
            yield entity, effective_layer, "/".join(next_path), source_entity_id
            return
        if not children:
            yield entity, effective_layer, "/".join(next_path), source_entity_id
            return
        for child in children:
            yield from expanded(child, effective_layer, next_path, source_entity_id, depth + 1)

    for entity in doc.modelspace():
        for item, parent_layer, parent_block, source_entity_id in expanded(entity):
            kind = item.dxftype()
            layer = str(getattr(item.dxf, "layer", "")) or parent_layer or "Layer0"
            if layer in {"0", ""}:
                layer = parent_layer or layer
            text = ""
            if kind == "TEXT":
                text = clean_text(str(getattr(item.dxf, "text", "")))
            elif kind == "MTEXT":
                text = clean_text(str(item.plain_text() if hasattr(item, "plain_text") else getattr(item, "text", "")))
            points = points_for(item)
            if points:
                records.append(EntityRecord(kind, layer, parent_block, points, text, source_entity_id))
    return records


def extract(cad_file: Path, region_file: Path, max_per_region: int) -> dict[str, Any]:
    region_package = load_json(region_file)
    regions = confirmed_model_regions(region_package)
    validate_region_segmentation(regions)
    entities = ezdxf_entities(cad_file)
    parser = "ezdxf"
    if entities is None:
        entities = parse_ascii_entities(cad_file)
        parser = "ascii_group_code_fallback"

    records: list[dict[str, Any]] = []
    role_counts: Counter[str] = Counter()
    region_counts: Counter[str] = Counter()
    assigned_by_frame: dict[str, set[int]] = {}
    for region in regions:
        rb = region_bounds(region)
        if rb is None:
            continue
        frame_id = str(region.get("frame_id") or region.get("id", ""))
        assigned = assigned_by_frame.setdefault(frame_id, set())
        per_region = 0
        for entity_index, entity in enumerate(entities):
            if entity_index in assigned:
                continue
            if should_skip(entity):
                continue
            bounds = bounds_for_points(entity.points)
            if not bounds:
                continue
            center_x = (bounds["xmin"] + bounds["xmax"]) / 2.0
            center_y = (bounds["ymin"] + bounds["ymax"]) / 2.0
            if not (rb["xmin"] <= center_x <= rb["xmax"] and rb["ymin"] <= center_y <= rb["ymax"]):
                continue
            assigned.add(entity_index)
            per_region += 1
            role = likely_role(entity)
            record = {
                "id": f"src_{region.get('id', 'REGION')}_{per_region:04d}",
                "region_id": region.get("id", ""),
                "region_role": region.get("role", ""),
                "region_title": region.get("title", ""),
                "source_entity_type": entity.entity_type,
                "source_layer": entity.layer,
                "parent_block": entity.parent_block,
                "source_text": entity.text,
                "source_entity_id": entity.source_entity_id,
                "role": role,
                "geometry": {
                    "point_count": len(entity.points),
                    "bounds": bounds,
                    "sample_points": [{"x": x, "y": y} for x, y in entity.points[:8]],
                },
                "confidence": "candidate",
                "evidence": {
                    "recognition_engine": parser,
                    "region_file": str(region_file),
                    "region_verification_state": region.get("verification_state", region.get("confirmation_state", "")),
                },
                "notes": "Review-only source geometry candidate; it cannot drive SketchUp until confirmed.",
            }
            records.append(record)
            role_counts[role] += 1
            region_counts[str(region.get("id", ""))] += 1
            if per_region >= max_per_region:
                break

    return {
        "schema": "cad_to_sketchup.source_geometry.v2",
        "source_file": str(cad_file),
        "region_file": str(region_file),
        "recognition_engine": parser,
        "status": "candidate_from_confirmed_regions",
        "confirmation_required": True,
        "objects": records,
        "region_counts": dict(region_counts.most_common()),
        "role_counts": dict(role_counts.most_common()),
        "warnings": [
            "Records are candidates only and must not drive SketchUp build until confirmed.",
            "Layer names, block names, colors, and repeated placements are evidence signals, not proof.",
        ],
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Source Geometry Candidates",
        "",
        f"- Status: {report['status']}",
        f"- Source file: `{report['source_file']}`",
        f"- Objects: {len(report['objects'])}",
        "",
        "## Role Counts",
        "",
    ]
    for role, count in report["role_counts"].items():
        lines.append(f"- {role}: {count}")
    lines.extend(["", "## Warnings", ""])
    for warning in report["warnings"]:
        lines.append(f"- {warning}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract review-only source geometry candidates.")
    parser.add_argument("--input", required=True, type=Path, help="Approved ASCII DXF input path.")
    parser.add_argument("--regions", required=True, type=Path, help="Confirmed region_candidates.json path.")
    parser.add_argument("--out-json", required=True, type=Path)
    parser.add_argument("--out-md", type=Path)
    parser.add_argument("--max-per-region", type=int, default=500)
    args = parser.parse_args()

    report = extract(args.input, args.regions, args.max_per_region)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.out_md:
        write_markdown(report, args.out_md)
    print(json.dumps({"status": report["status"], "objects": len(report["objects"]), "role_counts": report["role_counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
