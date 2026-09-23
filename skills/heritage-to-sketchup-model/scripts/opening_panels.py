"""Validate source pane rectangles before creating a perforated window frame."""
import math


def validate_panels(opening, spec):
    if "panel_rectangles_mm" not in spec:
        return []
    errors = []
    rects = spec["panel_rectangles_mm"]
    if opening.get("type") != "window" or spec.get("panel_type") != "glass":
        errors.append("source pane rectangles require a glazed window")
    if spec.get("mullion_ratios") or spec.get("transom_ratios"):
        errors.append("source pane rectangles cannot be combined with ratio subdivisions")
    width, height = opening.get("width_mm", 0), opening.get("height_mm", 0)
    if not isinstance(rects, list) or not rects:
        return errors + ["source pane rectangles must be nonempty"]
    valid = []
    for rect in rects:
        if not isinstance(rect, list) or len(rect) != 4 or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in rect
        ):
            errors.append("pane rectangle requires four finite coordinates")
            continue
        x0, z0, x1, z1 = rect
        if not (0 < x0 < x1 < width and 0 < z0 < z1 < height):
            errors.append("pane rectangle must be strictly inside opening bounds")
        for a, b, c, d in valid:
            if min(x1, c) >= max(x0, a) and min(z1, d) >= max(z0, b):
                errors.append("pane rectangles overlap or touch; frame web would be missing")
        valid.append(rect)
    return errors
