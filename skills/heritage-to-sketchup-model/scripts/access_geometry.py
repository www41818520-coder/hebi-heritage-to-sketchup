"""Validate closed, consistently oriented entrance meshes in millimeters."""
from collections import defaultdict
import math


def convex_ramp_shell(outer, upper_edge, lower, upper):
    """outer starts along the wall; upper_edge is measured, not defaulted."""
    pts = [tuple(p)+(lower,) for p in outer] + [tuple(p)+(upper,) for p in upper_edge]
    center = [sum(p[i] for p in pts)/len(pts) for i in range(3)]
    faces = []
    for indices in ((0,1,2,3),(0,1,5,4),(4,5,2,3),(0,4,3),(1,2,5)):
        face = [pts[i] for i in indices]
        a,b,c = face[:3]
        u,v = [b[i]-a[i] for i in range(3)],[c[i]-a[i] for i in range(3)]
        normal = (u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
        if sum(normal[i]*(a[i]-center[i]) for i in range(3))<0:
            face.reverse()
        faces.append([list(p) for p in face])
    return faces


def mesh_errors(record):
    errors, edges = [], defaultdict(list)
    faces = record.get('shell_faces') or []
    volume6 = 0.0
    vertices = []
    for index, raw in enumerate(faces):
        if len(raw) < 3 or any(len(p) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p) for p in raw):
            errors.append(f'face {index}: invalid points')
            continue
        points = [tuple(round(float(v), 5) for v in p) for p in raw]
        vertices.extend(points)
        if len(set(points)) != len(points):
            errors.append(f'face {index}: repeated vertex')
        for a, b in zip(points, points[1:] + points[:1]):
            edges[tuple(sorted((a, b)))].append((index, a, b))
        a = points[0]
        normal = None
        for b, c in zip(points[1:-1], points[2:]):
            cross = (b[1]*c[2]-b[2]*c[1], b[2]*c[0]-b[0]*c[2], b[0]*c[1]-b[1]*c[0])
            volume6 += sum(a[i]*cross[i] for i in range(3))
            u, v = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
            n = (u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])
            size = math.sqrt(sum(x*x for x in n))
            if size > 1e-6:
                normal = tuple(x/size for x in n)
        if normal is None:
            errors.append(f'face {index}: zero area')
        elif any(abs(sum((p[i]-a[i])*normal[i] for i in range(3)))>0.01 for p in points):
            errors.append(f'face {index}: nonplanar')
    neighbors = defaultdict(set)
    for records in edges.values():
        if len(records) != 2 or records[0][1:] != records[1][1:][::-1]:
            errors.append('shell edge is open, nonmanifold, or inconsistently oriented')
            break
        neighbors[records[0][0]].add(records[1][0])
        neighbors[records[1][0]].add(records[0][0])
    seen, pending = set(), [0] if faces else []
    while pending:
        face = pending.pop()
        if face not in seen:
            seen.add(face)
            pending.extend(neighbors[face]-seen)
    if len(seen) != len(faces):
        errors.append('shell must be one connected volume')
    if volume6 <= 0:
        errors.append('shell must have positive outward-oriented volume')
    low, high = record.get('lower_elevation_mm'), record.get('upper_elevation_mm')
    if not isinstance(low,(int,float)) or not isinstance(high,(int,float)) or high <= low:
        errors.append('entrance upper elevation must exceed lower elevation')
    elif vertices and (abs(min(p[2] for p in vertices)-low)>0.01 or abs(max(p[2] for p in vertices)-high)>0.01):
        errors.append('mesh does not match declared entry elevations')
    return list(dict.fromkeys(errors))
