# mesh.py
import numpy as np
import ezdxf
from scipy.spatial import Delaunay
        
class Mesh:
    """
    Каркас поверхности: хранит координаты точек, триангуляцию по XY.
    """
    created_count = 0

    def __init__(self, x, y, z):
        Mesh.created_count += 1
        print(f"Mesh created #{Mesh.created_count}, points={len(x)}")
        self.x = np.array(x, dtype=float)
        self.y = np.array(y, dtype=float)
        self.z = np.array(z, dtype=float)

        if len(self.x) < 3:
            raise ValueError("Для триангуляции необходимо минимум 3 точки")

        self.points_xy = np.column_stack((self.x, self.y))
        self.points_3d = np.column_stack((self.x, self.y, self.z))
        self.tri = Delaunay(self.points_xy).simplices
        self.z_min = float(np.min(self.z))
        self.z_max = float(np.max(self.z))

    @classmethod
    def from_dxf(cls, file_path):
        x, y, z = load_dxf(file_path)
        if not x:
            raise RuntimeError("Файл не содержит точек")
        return cls(x, y, z)


def load_dxf(file_path):
    """
    Читает DXF и возвращает списки координат x, y, z.
    Поддерживаются типы: POINT, LINE, LWPOLYLINE, POLYLINE.
    """
    x, y, z = [], [], []
    try:
        doc = ezdxf.readfile(file_path)
        for e in doc.modelspace():
            t = e.dxftype()
            if t == 'POINT':
                loc = e.dxf.location
                x.append(loc.x); y.append(loc.y); z.append(loc.z)
            elif t == 'LINE':
                x.extend([e.dxf.start.x, e.dxf.end.x])
                y.extend([e.dxf.start.y, e.dxf.end.y])
                z.extend([e.dxf.start.z, e.dxf.end.z])
            elif t in ('LWPOLYLINE', 'POLYLINE'):
                if t == 'LWPOLYLINE':
                    elev = e.dxf.get('elevation', 0)
                    pts = e.get_points('xy')
                    for p in pts:
                        x.append(p[0]); y.append(p[1]); z.append(elev)
                else:  # POLYLINE
                    for v in e.vertices:
                        loc = v.dxf.location
                        x.append(loc.x); y.append(loc.y); z.append(loc.z)
        return x, y, z
    except Exception as e:
        raise RuntimeError(f"Ошибка чтения DXF: {e}")