#!/usr/bin/env python3
"""Cluster connected facade WINDOW primitives into traceable opening bounds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def near(left: dict[str, float], right: dict[str, float], tolerance: float) -> bool:
    return not (
        left["xmax"] + tolerance < right["xmin"]
        or right["xmax"] + tolerance < left["xmin"]
        or left["ymax"] + tolerance < right["ymin"]
        or right["ymax"] + tolerance < left["ymin"]
    )


def union(items: list[dict[str, Any]]) -> dict[str, float]:
    bounds = [item["geometry"]["bounds"] for item in items]
    return {
        "xmin": min(item["xmin"] for item in bounds),
        "ymin": min(item["ymin"] for item in bounds),
        "xmax": max(item["xmax"] for item in bounds),
        "ymax": max(item["ymax"] for item in bounds),
    }


def clusters(items: list[dict[str, Any]], tolerance: float) -> list[list[dict[str, Any]]]:
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    for left in range(len(items)):
        for right in range(left + 1, len(items)):
            if near(items[left]["geometry"]["bounds"], items[right]["geometry"]["bounds"], tolerance):
                join(left, right)
    grouped: dict[int, list[dict[str, Any]]] = {}
    for index, item in enumerate(items):
        grouped.setdefault(find(index), []).append(item)
    return list(grouped.values())


def model_bounds(bounds: dict[str, float], registration: dict[str, Any]) -> dict[str, Any]:
    transform = registration["transform"]
    sx, sy = transform["source_origin"]
    origin = transform["origin"]
    x_axis = transform["x_axis"]
    y_axis = transform["y_axis"]
    scale = float(transform["scale"])

    def point(x: float, y: float) -> list[float]:
        dx, dy = (x - sx) * scale, (y - sy) * scale
        return [origin[i] + x_axis[i] * dx + y_axis[i] * dy for i in range(3)]

    corners = [
        point(bounds["xmin"], bounds["ymin"]), point(bounds["xmax"], bounds["ymin"]),
        point(bounds["xmax"], bounds["ymax"]), point(bounds["xmin"], bounds["ymax"]),
    ]
    horizontal = [(corner[0], corner[1]) for corner in corners]
    return {
        "corners": corners,
        "width_mm": ((bounds["xmax"] - bounds["xmin"]) * scale),
        "height_mm": ((bounds["ymax"] - bounds["ymin"]) * scale),
        "zmin_mm": min(corner[2] for corner in corners),
        "zmax_mm": max(corner[2] for corner in corners),
        "plan_extent": horizontal,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tolerance", type=float, default=2.0)
    parser.add_argument("--min-width", type=float, default=400.0)
    parser.add_argument("--min-height", type=float, default=400.0)
    args = parser.parse_args()
    geometry, topology = load(args.geometry), load(args.topology)
    registrations = {item["source_view_id"]: item for item in topology["registrations"]}
    output: list[dict[str, Any]] = []
    for view_id, registration in registrations.items():
        if registration["role"] != "elevation":
            continue
        items = [item for item in geometry["objects"] if item["region_id"] == view_id and item["role"] == "window"]
        for index, component in enumerate(clusters(items, args.tolerance), 1):
            bounds = union(component)
            mapped = model_bounds(bounds, registration)
            if mapped["width_mm"] < args.min_width or mapped["height_mm"] < args.min_height:
                continue
            output.append({
                "id": f"{view_id}-C{index:03d}", "source_view_id": view_id,
                "source_bounds": bounds, "model": mapped,
                "source_entity_ids": [item["id"] for item in component],
                "primitive_count": len(component),
            })
    result = {"schema": "cad_to_sketchup.facade_opening_candidates.2026-08-12", "candidates": output}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": len(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
