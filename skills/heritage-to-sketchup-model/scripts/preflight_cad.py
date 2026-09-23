#!/usr/bin/env python3
"""Basic CAD preflight for DXF files.

This intentionally uses only the Python standard library. It is a readiness
check, not a full CAD parser. The output helps Codex decide what to inspect
next and what to ask the user before generating a SketchUp model.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

from dxf_loading import decode_ascii_dxf


UNIT_NAMES = {
    0: "Unitless",
    1: "Inches",
    2: "Feet",
    3: "Miles",
    4: "Millimeters",
    5: "Centimeters",
    6: "Meters",
    7: "Kilometers",
    8: "Microinches",
    9: "Mils",
    10: "Yards",
    11: "Angstroms",
    12: "Nanometers",
    13: "Microns",
    14: "Decimeters",
    15: "Decameters",
    16: "Hectometers",
    17: "Gigameters",
    18: "Astronomical units",
    19: "Light years",
    20: "Parsecs",
}

INTERESTING_TYPES = {
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "ARC",
    "CIRCLE",
    "INSERT",
    "TEXT",
    "MTEXT",
    "DIMENSION",
    "HATCH",
    "SPLINE",
}


def read_group_pairs(path: Path) -> tuple[list[tuple[str, str]], dict]:
    text, encoding = decode_ascii_dxf(path)
    lines = text.splitlines()
    pairs = []
    index = 0
    while index + 1 < len(lines):
        pairs.append((lines[index].strip(), lines[index + 1].rstrip("\n\r")))
        index += 2
    return pairs, encoding


def numeric(value: str) -> float | None:
    try:
        result = float(value)
    except ValueError:
        return None
    if math.isfinite(result):
        return result
    return None


def parse_header(pairs: list[tuple[str, str]]) -> dict:
    header = {}
    in_header = False
    last_var = None
    for code, value in pairs:
        if code == "0" and value == "SECTION":
            last_var = None
        elif code == "2" and value == "HEADER":
            in_header = True
        elif code == "0" and value == "ENDSEC" and in_header:
            break
        elif in_header and code == "9":
            last_var = value
        elif in_header and last_var:
            header.setdefault(last_var, []).append(value)
    return header


def parse_entities(pairs: list[tuple[str, str]]) -> tuple[list[dict], Counter]:
    entities = []
    counts = Counter()
    in_entities = False
    current = None

    def finish():
        if current and current.get("type") in INTERESTING_TYPES:
            entities.append(current.copy())
            counts[current["type"]] += 1

    for code, value in pairs:
        if code == "0" and value == "SECTION":
            current = None
        elif code == "2" and value == "ENTITIES":
            in_entities = True
        elif code == "0" and value == "ENDSEC" and in_entities:
            finish()
            break
        elif not in_entities:
            continue
        elif code == "0":
            finish()
            current = {"type": value, "values": defaultdict(list)}
        elif current:
            current["values"][code].append(value)
            if code == "8":
                current["layer"] = value
            elif code == "2" and current.get("type") == "INSERT":
                current["block"] = value
            elif code == "1" and current.get("type") in {"TEXT", "MTEXT"}:
                current["text"] = value

    return entities, counts


def extract_points(entity: dict) -> list[tuple[float, float]]:
    values = entity.get("values", {})
    xs = [numeric(v) for v in values.get("10", []) + values.get("11", [])]
    ys = [numeric(v) for v in values.get("20", []) + values.get("21", [])]
    xs = [x for x in xs if x is not None]
    ys = [y for y in ys if y is not None]
    return list(zip(xs, ys))


def likely_role(layer: str, block: str = "", text: str = "") -> str:
    haystack = f"{layer} {block} {text}".upper()
    checks = [
        ("wall", ("WALL", "A-WALL", "墙", "W-")),
        ("column", ("COLUMN", "COL", "柱")),
        ("door", ("DOOR", "DOR", "门")),
        ("window", ("WINDOW", "WIN", "窗")),
        ("stair", ("STAIR", "楼梯")),
        ("axis", ("AXIS", "GRID", "轴")),
        ("dimension", ("DIM", "尺寸")),
        ("text", ("TEXT", "NOTE", "标注")),
    ]
    for role, tokens in checks:
        if any(token in haystack for token in tokens):
            return role
    return "unknown"


def preflight(path: Path) -> dict:
    pairs, source_encoding = read_group_pairs(path)
    header = parse_header(pairs)
    entities, entity_counts = parse_entities(pairs)

    units_raw = None
    if "$INSUNITS" in header and header["$INSUNITS"]:
        try:
            units_raw = int(float(header["$INSUNITS"][-1]))
        except ValueError:
            units_raw = None

    layers = Counter()
    blocks = Counter()
    roles = Counter()
    points = []
    text_samples = []

    for entity in entities:
        layer = entity.get("layer", "Layer0")
        layers[layer] += 1
        block = entity.get("block", "")
        text = entity.get("text", "")
        if block:
            blocks[block] += 1
        if text and len(text_samples) < 20:
            text_samples.append(text[:120])
        roles[likely_role(layer, block, text)] += 1
        points.extend(extract_points(entity))

    extents = None
    if points:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        extents = {
            "min_x": min(xs),
            "min_y": min(ys),
            "max_x": max(xs),
            "max_y": max(ys),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
        }

    warnings = []
    if path.suffix.lower() != ".dxf":
        warnings.append("This preflight expects DXF; use DWG only as a reference or export it to DXF.")
    if units_raw is None or units_raw == 0:
        warnings.append("Drawing units are missing or unitless; confirm whether coordinates are millimeters.")
    elif units_raw != 4:
        warnings.append(f"Drawing units appear to be {UNIT_NAMES.get(units_raw, units_raw)}; confirm conversion before modeling.")
    if not any(role in roles for role in ("wall", "column", "door", "window", "stair")):
        warnings.append("No obvious architectural layers were detected; layer mapping will need manual confirmation.")
    if extents and (extents["width"] > 10_000_000 or extents["height"] > 10_000_000):
        warnings.append("Coordinate extents are very large; the drawing may contain remote objects or site coordinates.")
    if source_encoding["encoding_override"]:
        warnings.append(
            f"DXF declares {source_encoding['declared_codepage']} but its non-ASCII payload is UTF-8; "
            "using a recorded UTF-8 override."
        )

    return {
        "file": str(path),
        "file_size_bytes": path.stat().st_size,
        "format": "DXF",
        "source_encoding": source_encoding,
        "units_code": units_raw,
        "units_name": UNIT_NAMES.get(units_raw, "Unknown"),
        "entity_counts": dict(entity_counts.most_common()),
        "top_layers": dict(layers.most_common(30)),
        "top_blocks": dict(blocks.most_common(20)),
        "likely_roles": dict(roles.most_common()),
        "extents": extents,
        "text_samples": text_samples,
        "warnings": warnings,
        "readiness": "needs_confirmation" if warnings else "ready_for_modeling_plan",
    }


def write_markdown(report: dict, path: Path) -> None:
    lines = [
        "# CAD Preflight Report",
        "",
        f"- File: `{report['file']}`",
        f"- Format: {report['format']}",
        f"- Units: {report['units_name']} ({report['units_code']})",
        f"- Readiness: {report['readiness']}",
        "",
        "## Entity Counts",
        "",
    ]
    for name, count in report["entity_counts"].items():
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Likely Roles", ""])
    for name, count in report["likely_roles"].items():
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Top Layers", ""])
    for name, count in report["top_layers"].items():
        lines.append(f"- `{name}`: {count}")
    if report["warnings"]:
        lines.extend(["", "## Warnings", ""])
        for warning in report["warnings"]:
            lines.append(f"- {warning}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight a DXF file for CAD-to-SketchUp modeling.")
    parser.add_argument("cad_file", type=Path)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    args = parser.parse_args()

    report = preflight(args.cad_file.resolve())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_text = text.encode(encoding, errors="backslashreplace").decode(encoding)
        print(safe_text)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(text + "\n", encoding="utf-8-sig")
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        write_markdown(report, args.out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
