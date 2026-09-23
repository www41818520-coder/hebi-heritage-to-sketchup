#!/usr/bin/env python3
"""Create a reviewable CAD drawing index and region verification package.

The script is intentionally conservative. It extracts title text, nearby axis
labels, and geometry bounds from ASCII DXF group codes, then writes artifacts
that an independent backend pass must verify before topology work starts.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from dxf_loading import decode_ascii_dxf, read_dxf


UNIT_NAMES = {
    0: "Unitless",
    1: "Inches",
    2: "Feet",
    4: "Millimeters",
    5: "Centimeters",
    6: "Meters",
}

TITLE_ROLE_PATTERNS = [
    ("roof_plan", ("屋顶", "ROOF")),
    ("plan", ("平面", "PLAN")),
    ("section", ("剖面", "SECTION")),
    ("elevation", ("立面", "ELEVATION", "ELEV")),
]

GRID_LABEL = re.compile(r"^[A-Z]{1,3}$|^[0-9]{1,3}$|^[A-Z][0-9]{1,3}$", re.I)


@dataclass
class TextEntity:
    text: str
    x: float
    y: float
    layer: str


@dataclass
class GeometryEntity:
    type: str
    layer: str
    points: list[tuple[float, float]]


def read_pairs(path: Path) -> list[tuple[str, str]]:
    raw = path.read_bytes()
    if raw.startswith(b"AutoCAD Binary DXF"):
        raise ValueError("Binary DXF is not supported; export ASCII DXF before indexing.")
    text, _ = decode_ascii_dxf(path)
    lines = text.splitlines()
    return [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]


def numeric(value: str) -> float | None:
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def header_units(pairs: list[tuple[str, str]]) -> int | None:
    for index, (code, value) in enumerate(pairs):
        if code == "9" and value == "$INSUNITS" and index + 1 < len(pairs):
            return int(float(pairs[index + 1][1]))
    return None


def finish_entity(current: dict | None, texts: list[TextEntity], geometry: list[GeometryEntity]) -> None:
    if not current:
        return
    values = current.get("values", {})
    entity_type = current.get("type", "")
    layer = current.get("layer", "Layer0")
    if entity_type in {"TEXT", "MTEXT"}:
        x = first_number(values.get("10", []))
        y = first_number(values.get("20", []))
        text = " ".join(values.get("1", []) + values.get("3", [])).strip()
        if text and x is not None and y is not None:
            texts.append(TextEntity(text=clean_text(text), x=x, y=y, layer=layer))
        return
    points = collect_points(values)
    if len(points) >= 1:
        geometry.append(GeometryEntity(type=entity_type, layer=layer, points=points))


def parse_entities(pairs: list[tuple[str, str]]) -> tuple[list[TextEntity], list[GeometryEntity]]:
    texts: list[TextEntity] = []
    geometry: list[GeometryEntity] = []
    current: dict | None = None
    in_entities = False
    for code, value in pairs:
        if code == "0" and value == "SECTION":
            current = None
        elif code == "2" and value == "ENTITIES":
            in_entities = True
        elif code == "0" and value == "ENDSEC" and in_entities:
            finish_entity(current, texts, geometry)
            break
        elif not in_entities:
            continue
        elif code == "0":
            finish_entity(current, texts, geometry)
            current = {"type": value, "values": {}}
        elif current is not None:
            current.setdefault("values", {}).setdefault(code, []).append(value)
            if code == "8":
                current["layer"] = value
    return texts, geometry


def parse_with_ezdxf(path: Path) -> tuple[int | None, list[TextEntity], list[GeometryEntity]] | None:
    try:
        import ezdxf  # type: ignore
    except Exception:
        return None

    doc, _ = read_dxf(path)
    units_code = int(getattr(doc.header, "get", lambda *_: 0)("$INSUNITS", 0) or 0)
    texts: list[TextEntity] = []
    geometry: list[GeometryEntity] = []
    for entity in doc.modelspace():
        entity_type = entity.dxftype()
        layer = getattr(entity.dxf, "layer", "Layer0")
        if entity_type in {"TEXT", "MTEXT"}:
            insert = getattr(entity.dxf, "insert", None)
            if insert is not None:
                text = entity.plain_text() if hasattr(entity, "plain_text") else getattr(entity.dxf, "text", "")
                texts.append(TextEntity(text=clean_text(str(text)), x=float(insert.x), y=float(insert.y), layer=layer))
        elif entity_type == "LINE":
            start = entity.dxf.start
            end = entity.dxf.end
            geometry.append(GeometryEntity(type=entity_type, layer=layer, points=[(float(start.x), float(start.y)), (float(end.x), float(end.y))]))
        elif entity_type == "LWPOLYLINE":
            points = [(float(point[0]), float(point[1])) for point in entity.get_points()]
            if points:
                geometry.append(GeometryEntity(type=entity_type, layer=layer, points=points))
        elif entity_type == "POLYLINE":
            points = [(float(vertex.dxf.location.x), float(vertex.dxf.location.y)) for vertex in entity.vertices]
            if points:
                geometry.append(GeometryEntity(type=entity_type, layer=layer, points=points))
        elif entity_type == "INSERT":
            insert = entity.dxf.insert
            name = getattr(entity.dxf, "name", "")
            texts.append(TextEntity(text=str(name), x=float(insert.x), y=float(insert.y), layer=layer))
    return units_code, texts, geometry


def clean_text(value: str) -> str:
    value = re.sub(r"\\[A-Za-z][^;]*;", "", value)
    value = value.replace("\\P", " ").replace("{", "").replace("}", "")
    value = re.sub(r"\s+", " ", value).strip()
    return strip_json_unsafe_text(repair_mojibake(value))


def repair_mojibake(value: str) -> str:
    try:
        repaired = value.encode("cp1252", errors="surrogateescape").decode("utf-8")
    except UnicodeError:
        return value
    if any(token in repaired for token in ("平面", "立面", "剖面", "屋顶", "轴")):
        return repaired
    return value


def strip_json_unsafe_text(value: str) -> str:
    return "".join(
        char
        for char in value
        if (char == "\t" or char == "\n" or char == "\r" or ord(char) >= 32)
        and not (0xD800 <= ord(char) <= 0xDFFF)
    )


def first_number(values: list[str]) -> float | None:
    for value in values:
        parsed = numeric(value)
        if parsed is not None:
            return parsed
    return None


def collect_points(values: dict[str, list[str]]) -> list[tuple[float, float]]:
    xs = [numeric(v) for v in values.get("10", []) + values.get("11", [])]
    ys = [numeric(v) for v in values.get("20", []) + values.get("21", [])]
    xs = [x for x in xs if x is not None]
    ys = [y for y in ys if y is not None]
    return list(zip(xs, ys))


def bounds_for_points(points: list[tuple[float, float]]) -> dict[str, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {"xmin": min(xs), "ymin": min(ys), "xmax": max(xs), "ymax": max(ys)}


def merge_bounds(bounds: list[dict[str, float]]) -> dict[str, float]:
    return {
        "xmin": min(b["xmin"] for b in bounds),
        "ymin": min(b["ymin"] for b in bounds),
        "xmax": max(b["xmax"] for b in bounds),
        "ymax": max(b["ymax"] for b in bounds),
    }


def infer_role(text: str) -> str:
    upper = text.upper()
    for role, tokens in TITLE_ROLE_PATTERNS:
        if any(token in upper for token in tokens):
            return role
    return "unknown"


def title_candidates(texts: list[TextEntity]) -> list[TextEntity]:
    return [text for text in texts if infer_role(text.text) != "unknown"]


def region_bounds_for_title(title: TextEntity, titles: list[TextEntity], geometry: list[GeometryEntity]) -> dict[str, float] | None:
    if not geometry:
        return None
    nearest_spacing = min(
        (abs(other.x - title.x) for other in titles if other is not title and abs(other.x - title.x) > 1e-6),
        default=100000.0,
    )
    radius = max(25000.0, nearest_spacing * 0.65)
    candidates = []
    for entity in geometry:
        bounds = bounds_for_points(entity.points)
        cx = (bounds["xmin"] + bounds["xmax"]) / 2.0
        cy = (bounds["ymin"] + bounds["ymax"]) / 2.0
        if abs(cx - title.x) <= radius and abs(cy - title.y) <= max(radius * 2.0, 90000.0):
            candidates.append(bounds)
    if not candidates:
        return None
    merged = merge_bounds(candidates)
    margin = max(5000.0, (merged["xmax"] - merged["xmin"] + merged["ymax"] - merged["ymin"]) * 0.08)
    return {
        "xmin": merged["xmin"] - margin,
        "ymin": merged["ymin"] - margin,
        "xmax": merged["xmax"] + margin,
        "ymax": max(merged["ymax"], title.y) + margin,
    }


def axis_labels_for_region(region_bounds: dict[str, float], texts: list[TextEntity]) -> list[str]:
    labels = []
    for text in texts:
        inside = (
            region_bounds["xmin"] <= text.x <= region_bounds["xmax"]
            and region_bounds["ymin"] <= text.y <= region_bounds["ymax"]
        )
        if inside and GRID_LABEL.match(text.text.strip()):
            labels.append(text.text.strip())
    return sorted(set(labels), key=labels.index)


def make_regions(texts: list[TextEntity], geometry: list[GeometryEntity]) -> list[dict]:
    titles = title_candidates(texts)
    regions = []
    for index, title in enumerate(titles, 1):
        bounds = region_bounds_for_title(title, titles, geometry)
        if bounds is None or bounds["xmax"] <= bounds["xmin"] or bounds["ymax"] <= bounds["ymin"]:
            confidence = "blocked"
            bounds = {"xmin": title.x, "ymin": title.y, "xmax": title.x, "ymax": title.y}
        else:
            confidence = "medium"
        axis_labels = axis_labels_for_region(bounds, texts)
        if not axis_labels:
            confidence = "needs_axis_review"
        regions.append(
            {
                "id": f"R{index:02d}",
                "title": title.text,
                "role": infer_role(title.text),
                "title_layer": title.layer,
                "bounds": bounds,
                "axis_labels": axis_labels,
                "confidence": confidence,
                "verification_state": "needs_verification",
                "blocking_reasons": [] if axis_labels and confidence != "blocked" else ["axis_or_bounds_need_review"],
            }
        )
    return regions


def all_drawing_bounds(texts: list[TextEntity], geometry: list[GeometryEntity]) -> dict[str, float]:
    bounds = [bounds_for_points(entity.points) for entity in geometry]
    bounds.extend({"xmin": t.x, "ymin": t.y, "xmax": t.x, "ymax": t.y} for t in texts)
    return merge_bounds(bounds) if bounds else {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1}


def write_overview_svg(path: Path, drawing_bounds: dict[str, float], regions: list[dict]) -> None:
    width = max(drawing_bounds["xmax"] - drawing_bounds["xmin"], 1.0)
    height = max(drawing_bounds["ymax"] - drawing_bounds["ymin"], 1.0)
    svg_w = 1200
    svg_h = max(300, int(svg_w * height / width))

    def sx(x: float) -> float:
        return (x - drawing_bounds["xmin"]) / width * svg_w

    def sy(y: float) -> float:
        return svg_h - (y - drawing_bounds["ymin"]) / height * svg_h

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,sans-serif;font-size:18px}.region{fill:rgba(200,255,0,0.16);stroke:#0f766e;stroke-width:3}</style>',
    ]
    for region in regions:
        b = region["bounds"]
        x = sx(b["xmin"])
        y = sy(b["ymax"])
        w = max(sx(b["xmax"]) - sx(b["xmin"]), 1)
        h = max(sy(b["ymin"]) - sy(b["ymax"]), 1)
        label = escape(f"{region['id']} {region['role']} {region['title']}")
        lines.append(f'<rect class="region" x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}"/>')
        lines.append(f'<text x="{x + 8:.2f}" y="{y + 24:.2f}" fill="#0f172a">{label}</text>')
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_region_svgs(work_dir: Path, drawing_bounds: dict[str, float], regions: list[dict]) -> None:
    preview_dir = work_dir / "region_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for region in regions:
        write_overview_svg(preview_dir / f"{region['id']}.svg", drawing_bounds, [region])


def bounds_intersect(a: dict[str, float], b: dict[str, float]) -> bool:
    return not (a["xmax"] < b["xmin"] or a["xmin"] > b["xmax"] or a["ymax"] < b["ymin"] or a["ymin"] > b["ymax"])


def write_region_jpgs(work_dir: Path, texts: list[TextEntity], geometry: list[GeometryEntity], regions: list[dict]) -> None:
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return

    preview_dir = work_dir / "region_previews_jpg"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for region in regions:
        b = region["bounds"]
        width = max(float(b["xmax"]) - float(b["xmin"]), 1.0)
        height = max(float(b["ymax"]) - float(b["ymin"]), 1.0)
        image_w = 1800
        image_h = max(900, min(2400, int(image_w * height / width)))
        pad = 40
        scale = min((image_w - pad * 2) / width, (image_h - pad * 2) / height)

        def sx(x: float) -> int:
            return int((x - float(b["xmin"])) * scale + pad)

        def sy(y: float) -> int:
            return int(image_h - ((y - float(b["ymin"])) * scale + pad))

        image = Image.new("RGB", (image_w, image_h), "white")
        draw = ImageDraw.Draw(image)
        for entity in geometry:
            eb = bounds_for_points(entity.points)
            if not bounds_intersect(eb, b):
                continue
            pts = [(sx(x), sy(y)) for x, y in entity.points]
            if len(pts) == 1:
                x, y = pts[0]
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(80, 80, 80))
            else:
                draw.line(pts, fill=(25, 25, 25), width=1)
        for text in texts:
            if float(b["xmin"]) <= text.x <= float(b["xmax"]) and float(b["ymin"]) <= text.y <= float(b["ymax"]):
                x, y = sx(text.x), sy(text.y)
                draw.rectangle((x - 3, y - 3, x + 3, y + 3), fill=(190, 30, 30))
        draw.rectangle((20, 20, image_w - 20, image_h - 20), outline=(15, 118, 110), width=4)
        image.save(preview_dir / f"{region['id']}.jpg", quality=92)


def build_index(input_path: Path, work_dir: Path) -> dict:
    parsed = parse_with_ezdxf(input_path)
    if parsed is None:
        pairs = read_pairs(input_path)
        units_code = header_units(pairs)
        texts, geometry = parse_entities(pairs)
        parser = "ascii_group_code_fallback"
    else:
        units_code, texts, geometry = parsed
        parser = "ezdxf"
        if not texts and not geometry:
            pairs = read_pairs(input_path)
            units_code = header_units(pairs)
            texts, geometry = parse_entities(pairs)
            parser = "ascii_group_code_fallback_after_empty_ezdxf"
    regions = make_regions(texts, geometry)
    drawing_bounds = all_drawing_bounds(texts, geometry)
    work_dir.mkdir(parents=True, exist_ok=True)
    drawing_index = {
        "source_file": str(input_path),
        "parser": parser,
        "units_code": units_code,
        "units_name": UNIT_NAMES.get(units_code, "Unknown"),
        "drawing_bounds": drawing_bounds,
        "text_count": len(texts),
        "geometry_count": len(geometry),
        "title_count": len(regions),
    }
    region_candidates = {
        "source_file": str(input_path),
        "verification_required": True,
        "regions": regions,
    }
    (work_dir / "drawing_index.json").write_text(json.dumps(drawing_index, indent=2, ensure_ascii=False), encoding="utf-8")
    (work_dir / "region_candidates.json").write_text(
        json.dumps(region_candidates, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_overview_svg(work_dir / "confirmation_overview.svg", drawing_bounds, regions)
    write_region_svgs(work_dir, drawing_bounds, regions)
    write_region_jpgs(work_dir, texts, geometry, regions)
    return {"drawing_index": drawing_index, "region_candidates": region_candidates}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create CAD drawing index and region verification artifacts.")
    parser.add_argument("--input", required=True, help="Approved ASCII DXF input path.")
    parser.add_argument("--work-dir", required=True, help="Project work directory for generated artifacts.")
    args = parser.parse_args()
    result = build_index(Path(args.input), Path(args.work_dir))
    print(json.dumps({"regions": len(result["region_candidates"]["regions"]), "work_dir": args.work_dir}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
