#!/usr/bin/env python3
"""Deterministic mixed-view segmentation for architectural CAD frames."""

from __future__ import annotations

from typing import Any


def _span(bounds: dict[str, float], axis: str) -> tuple[float, float]:
    return (bounds[f"{axis}min"], bounds[f"{axis}max"])


def _center(bounds: dict[str, float], axis: str) -> float:
    low, high = _span(bounds, axis)
    return (low + high) / 2.0


def _candidate_positions(low: float, high: float, entities: list[dict[str, Any]], axis: str) -> list[float]:
    if high <= low:
        return []
    width = high - low
    epsilon = width * 1e-5
    candidates = [low + width * index / 80.0 for index in range(1, 80)]
    for entity in entities:
        start, end = _span(entity["bounds"], axis)
        for value in (start - epsilon, start + epsilon, end - epsilon, end + epsilon):
            if low < value < high:
                candidates.append(value)
    return sorted(set(candidates))


def _separator_cost(
    value: float,
    entities: list[dict[str, Any]],
    axis: str,
    preferred: float,
    search_width: float,
) -> tuple[float, list[str], list[str]]:
    protected_crossing: list[str] = []
    all_crossing: list[str] = []
    cost = 0.0
    proximity_band = max(search_width * 0.025, 1e-9)
    for entity in entities:
        low, high = _span(entity["bounds"], axis)
        weight = float(entity.get("weight", 1.0))
        if low < value < high:
            cost += weight * 100.0
            all_crossing.append(str(entity["id"]))
            if entity.get("protected", True):
                protected_crossing.append(str(entity["id"]))
        else:
            distance = min(abs(value - low), abs(value - high))
            if distance < proximity_band:
                cost += weight * (proximity_band - distance) / proximity_band
    cost += abs(value - preferred) / max(search_width, 1e-9) * 0.02
    return cost, sorted(set(protected_crossing)), sorted(set(all_crossing))


def _find_separator(
    low: float,
    high: float,
    entities: list[dict[str, Any]],
    axis: str,
    preferred: float,
) -> dict[str, Any] | None:
    candidates = _candidate_positions(low, high, entities, axis)
    if not candidates:
        return None
    scored = []
    for value in candidates:
        cost, protected_crossing, all_crossing = _separator_cost(value, entities, axis, preferred, high - low)
        scored.append((cost, abs(value - preferred), value, protected_crossing, all_crossing))
    cost, _, value, protected_crossing, all_crossing = min(scored, key=lambda item: (item[0], item[1], item[2]))
    return {
        "axis": axis,
        "value": value,
        "search_interval": {"min": low, "max": high},
        "cost": round(cost, 6),
        "crossing_entity_ids": protected_crossing,
        "context_crossing_entity_ids": sorted(set(all_crossing) - set(protected_crossing)),
        "evidence": "minimum_geometry_crossing_whitespace_valley",
    }


def _title_rows(views: list[dict[str, Any]], frame_bounds: dict[str, float]) -> list[list[dict[str, Any]]]:
    height = max(frame_bounds["ymax"] - frame_bounds["ymin"], 1.0)
    tolerance = height * 0.05
    rows: list[list[dict[str, Any]]] = []
    ordered = sorted(views, key=lambda item: float(item["title_anchor"]["y"]))
    for view in ordered:
        y = float(view["title_anchor"]["y"])
        if not rows:
            rows.append([view])
            continue
        row_y = sum(float(item["title_anchor"]["y"]) for item in rows[-1]) / len(rows[-1])
        if abs(y - row_y) <= tolerance:
            rows[-1].append(view)
        else:
            rows.append([view])
    return rows


def _entity_centers_in_band(
    entities: list[dict[str, Any]], ymin: float, ymax: float
) -> list[dict[str, Any]]:
    return [entity for entity in entities if ymin <= _center(entity["bounds"], "y") <= ymax]


def segment_views(
    views: list[dict[str, Any]],
    frame_bounds: dict[str, float],
    entities: list[dict[str, Any]],
) -> bool:
    """Assign non-overlapping view cells using titles, geometry, and whitespace.

    Return False when there is not enough evidence; the caller must then use a
    clearly marked fallback instead of silently treating title-grid bounds as
    verified geometry.
    """
    if len(views) < 2 or any(not isinstance(view.get("title_anchor"), dict) for view in views):
        return False
    if len(entities) < len(views):
        return False

    rows = _title_rows(views, frame_bounds)
    row_centers = [
        sum(float(item["title_anchor"]["y"]) for item in row) / len(row)
        for row in rows
    ]
    y_edges = [frame_bounds["ymin"]]
    y_separators: list[dict[str, Any]] = []
    for lower, upper in zip(row_centers, row_centers[1:]):
        # Titles conventionally sit below their drawings. Search the complete
        # interval between adjacent titles, preferring the upper title only
        # when geometry evidence gives an equally safe separator.
        separator = _find_separator(
            lower,
            upper,
            entities,
            "y",
            upper,
        )
        if separator is None:
            return False
        y_edges.append(separator["value"])
        y_separators.append(separator)
    y_edges.append(frame_bounds["ymax"])

    for row_index, row in enumerate(rows):
        ordered = sorted(row, key=lambda item: float(item["title_anchor"]["x"]))
        row_entities = _entity_centers_in_band(entities, y_edges[row_index], y_edges[row_index + 1])
        x_edges = [frame_bounds["xmin"]]
        row_separators: list[dict[str, Any]] = []
        anchors = [float(item["title_anchor"]["x"]) for item in ordered]
        for left, right in zip(anchors, anchors[1:]):
            separator = _find_separator(left, right, row_entities, "x", (left + right) / 2.0)
            if separator is None:
                return False
            x_edges.append(separator["value"])
            row_separators.append(separator)
        x_edges.append(frame_bounds["xmax"])

        for column_index, view in enumerate(ordered):
            bounds = {
                "xmin": x_edges[column_index],
                "ymin": y_edges[row_index],
                "xmax": x_edges[column_index + 1],
                "ymax": y_edges[row_index + 1],
            }
            assigned = [
                entity for entity in entities
                if bounds["xmin"] <= _center(entity["bounds"], "x") <= bounds["xmax"]
                and bounds["ymin"] <= _center(entity["bounds"], "y") <= bounds["ymax"]
            ]
            relevant = []
            if column_index > 0:
                relevant.append(row_separators[column_index - 1])
            if column_index < len(row_separators):
                relevant.append(row_separators[column_index])
            if row_index > 0:
                relevant.append(y_separators[row_index - 1])
            if row_index < len(y_separators):
                relevant.append(y_separators[row_index])
            crossing = sorted({
                entity_id
                for separator in relevant
                for entity_id in separator["crossing_entity_ids"]
            })
            context_crossing = sorted({
                entity_id
                for separator in relevant
                for entity_id in separator["context_crossing_entity_ids"]
            })
            confidence = "high"
            blockers: list[str] = []
            if not assigned:
                confidence = "low"
                blockers.append("no_geometry_cluster_assigned")
            elif crossing:
                confidence = "low"
                blockers.append("protected_geometry_crosses_view_separator")
            elif len(assigned) < 3:
                confidence = "medium"
            view["bounds"] = bounds
            view["segmentation_method"] = "title_geometry_whitespace_partition"
            view["segmentation_confidence"] = confidence
            view["segmentation_state"] = "blocked" if blockers else "needs_verification"
            view["segmentation_evidence"] = {
                "assigned_entity_count": len(assigned),
                "assigned_entity_ids": [str(entity["id"]) for entity in assigned],
                "crossing_entity_ids": crossing,
                "context_crossing_entity_ids": context_crossing,
                "separators": relevant,
            }
            if blockers:
                view.setdefault("blocking_reasons", []).extend(blockers)
    return True
