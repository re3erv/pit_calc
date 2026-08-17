# dxf_geometry.py
import ezdxf
import numpy as np
import math

def is_closed_polyline(poly, tol=1e-6):
    if len(poly) < 3:
        return False
    first = poly[0][:2]
    last = poly[-1][:2]
    return np.linalg.norm(np.array(first) - np.array(last)) < tol

def is_circle_like(poly, tolerance=0.05):
    """
    Проверяет, похожа ли замкнутая полилиния на окружность.
    Если да, возвращает (cx, cy, r_avg), иначе None.
    """
    if len(poly) < 8:
        return None

    # Центр – среднее арифметическое всех точек
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)

    # Средний радиус
    r_avg = sum(math.hypot(p[0] - cx, p[1] - cy) for p in poly) / len(poly)
    if r_avg < 1e-6:
        return None

    # Проверяем отклонение от среднего радиуса
    for p in poly:
        r = math.hypot(p[0] - cx, p[1] - cy)
        if abs(r - r_avg) / r_avg > tolerance:
            return None

    return cx, cy, r_avg


def load_dxf_geometry(file_path, load_mesh=True, load_polylines=True, load_circles=True):
    """
    Загружает DXF и извлекает:
      - mesh: объект Mesh (для объёмов) — только если load_mesh=True
      - polylines: список полилиний — только если load_polylines=True
      - circles: список кругов — только если load_circles=True
    """
    x, y, z = [], [], []
    polylines = []
    circles = []

    try:
        doc = ezdxf.readfile(file_path)

        # масштаб
        units = doc.header.get('$INSUNITS', 0)
        if units == 1:      # дюймы
            scale = 0.0254
        elif units == 2:    # футы
            scale = 0.3048
        elif units == 4:    # мм
            scale = 0.001
        elif units == 5:    # см
            scale = 0.01
        else:
            scale = 1.0

        for e in doc.modelspace():
            t = e.dxftype()

            # Точки рельефа: нужны только если load_mesh=True
            if load_mesh:
                if t == 'POINT':
                    loc = e.dxf.location
                    x.append(loc.x * scale)
                    y.append(loc.y * scale)
                    z.append(loc.z * scale)

                elif t == 'LINE':
                    x.extend([e.dxf.start.x * scale, e.dxf.end.x * scale])
                    y.extend([e.dxf.start.y * scale, e.dxf.end.y * scale])
                    z.extend([e.dxf.start.z * scale, e.dxf.end.z * scale])

            # Полилинии
            if load_polylines and t in ('LWPOLYLINE', 'POLYLINE'):
                if t == 'LWPOLYLINE':
                    elev = e.dxf.get('elevation', 0) * scale
                    pts = e.get_points('xy')
                    poly = [(p[0] * scale, p[1] * scale, elev) for p in pts]
                else:  # POLYLINE
                    poly = []
                    for v in e.vertices:
                        loc = v.dxf.location
                        poly.append((loc.x * scale, loc.y * scale, loc.z * scale))

                # Если включена загрузка кругов и полилиний, проверяем, не окружность ли это
                if load_circles:
                    circle_info = is_circle_like(poly)
                    if circle_info is not None:
                        cx, cy, r_avg = circle_info
                        z_avg = sum(p[2] for p in poly) / len(poly) if poly else 0.0
                        circles.append({'center': (cx, cy, z_avg), 'radius': r_avg})
                        continue  # эта полилиния стала кругом, не добавляем в polylines
                # Обычная полилиния
                polylines.append(poly)

            # Настоящие круги
            if load_circles and t == 'CIRCLE':
                center = e.dxf.center
                radius = e.dxf.radius * scale
                circles.append({
                    'center': (center.x * scale, center.y * scale, center.z * scale),
                    'radius': radius
                })

        # Создаём mesh только если нужно
        mesh = None
        if load_mesh and len(x) >= 3:
            from .mesh import Mesh
            mesh = Mesh(np.array(x), np.array(y), np.array(z))
            mesh.file_path = file_path

        return mesh, polylines, circles
    except Exception as e:
        raise RuntimeError(f"Ошибка чтения DXF: {e}")