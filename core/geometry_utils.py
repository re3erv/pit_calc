# core/geometry_utils.py
import numpy as np

def closest_point_on_polyline(point_xy, poly):
    min_dist = float('inf')
    best_proj = None
    best_seg = 0
    best_t = 0.0
    for i in range(len(poly) - 1):
        a = np.array(poly[i][:2])
        b = np.array(poly[i+1][:2])
        p = np.array(point_xy)
        ab = b - a
        if np.linalg.norm(ab) < 1e-12:
            continue
        t = np.dot(p - a, ab) / np.dot(ab, ab)
        t = max(0.0, min(1.0, t))
        proj = a + t * ab
        dist = np.linalg.norm(p - proj)
        if dist < min_dist:
            min_dist = dist
            best_proj = proj
            best_seg = i
            best_t = t
    return min_dist, best_proj, best_seg, best_t