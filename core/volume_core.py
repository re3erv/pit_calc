# volume_core.py
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.path import Path
from scipy.spatial import Delaunay

def compute_volume_by_contour(mesh, level, progress_callback=None, return_details=False):
    """
    Вычисляет объём выемки по отметке level, используя контурный метод.
    Если return_details=True, возвращает (total_volume, models).
    """
    x = mesh.x
    y = mesh.y
    z = mesh.z

    # 1. Поиск контуров уровня
    try:
        fig, ax = plt.subplots()
        cs = ax.tricontour(x, y, z, levels=[level])
        plt.close(fig)

        closed_contours = []
        for line in cs.allsegs[0]:
            if len(line) > 2 and np.linalg.norm(line[0] - line[-1]) < 1.0:
                closed_contours.append(line)
    except Exception as e:
        if progress_callback:
            progress_callback(f"❌ Ошибка построения контуров: {e}")
        if return_details:
            return 0.0, []
        else:
            return 0.0

    if not closed_contours:
        if progress_callback:
            progress_callback("⚠️ Замкнутые контуры не найдены.")
        if return_details:
            return 0.0, []
        else:
            return 0.0

    total_volume = 0.0
    points = np.column_stack((x, y))
    z_arr = np.array(z)
    models = []

    for i, contour in enumerate(closed_contours):
        path = Path(contour)
        mask = path.contains_points(points) & (z_arr < level)
        if not np.any(mask):
            continue

        pts_in = points[mask]
        z_in = z_arr[mask]

        all_pts = np.vstack([pts_in, contour])
        all_z = np.hstack([z_in, np.full(len(contour), level)])

        if len(all_pts) < 3:
            continue

        try:
            tri = Delaunay(all_pts)
        except Exception:
            continue

        vol = 0.0
        depths = []
        valid_simplices = []
        for simplex in tri.simplices:
            tri_pts = all_pts[simplex]
            if path.contains_point(np.mean(tri_pts, axis=0)):
                valid_simplices.append(simplex)
                area = 0.5 * abs(
                    (tri_pts[1, 0] - tri_pts[0, 0]) * (tri_pts[2, 1] - tri_pts[0, 1]) -
                    (tri_pts[2, 0] - tri_pts[0, 0]) * (tri_pts[1, 1] - tri_pts[0, 1])
                )
                avg_depth = np.mean(level - all_z[simplex])
                vol += area * avg_depth
                depths.append(avg_depth)

        if valid_simplices:
            model = {
                'points': all_pts,
                'z': all_z,
                'simplices': np.array(valid_simplices),
                'volume': vol,
                'contour': contour,
                'index': i + 1,
                'depths': depths
            }
            models.append(model)
            total_volume += vol
            if progress_callback:
                progress_callback(f"  Контур {i+1}: объём = {vol:.1f} м³")

    if progress_callback:
        progress_callback(f"  Суммарный объём: {total_volume:.1f} м³")

    if return_details:
        return total_volume, models
    else:
        return total_volume