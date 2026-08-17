# widgets.py
import sys
import numpy as np
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from PyQt5.QtCore import QThread, pyqtSignal
from utils import timed

class MplCanvas(FigureCanvas):
    def __init__(self, parent=None, width=5, height=4, dpi=100):
        self.fig, self.ax = plt.subplots(figsize=(width, height), dpi=dpi)
        super().__init__(self.fig)
        self.setParent(parent)


class DataLoaderThread(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)
    data_signal = pyqtSignal(object, object, object)  # mesh, polylines, circles

    def __init__(self, dxf_file):
        super().__init__()
        self.dxf_file = dxf_file

    @timed
    def run(self):
        try:
            from core.dxf_geometry import load_dxf_geometry
            self.progress_signal.emit("⏳ Загрузка DXF...")
            mesh, polylines, circles = load_dxf_geometry(self.dxf_file)
            self.progress_signal.emit(f"✅ Загружено {len(mesh.x) if mesh else 0} точек")
            self.progress_signal.emit(f"✅ Полилиний: {len(polylines)}, кругов: {len(circles)}")
            self.progress_signal.emit("⏳ Триангуляция...")
            self.data_signal.emit(mesh, polylines, circles)
            self.finished_signal.emit(True)
        except Exception as e:
            self.progress_signal.emit(f"❌ Ошибка загрузки: {e}")
            self.finished_signal.emit(False)


class HeightSearchThread(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)
    result_signal = pyqtSignal(dict)

    def __init__(self, mesh, target_volumes, volume_cache, height_tol, volume_tol):
        super().__init__()
        self.mesh = mesh
        self.target_volumes = target_volumes
        self.volume_cache = volume_cache
        self.height_tol = height_tol
        self.volume_tol = volume_tol
    
    @timed
    def run(self):
        try:
            from core.height_search import HeightSearch
            searcher = HeightSearch(
                self.mesh,
                self.target_volumes,
                volume_cache=self.volume_cache,
                height_tol=self.height_tol,
                volume_tol=self.volume_tol,
                progress_callback=lambda msg: self.progress_signal.emit(msg)
            )
            result = searcher.search()
            self.result_signal.emit(result)
            self.finished_signal.emit(True)
        except Exception as e:
            self.progress_signal.emit(f"❌ Ошибка поиска высоты: {e}")
            self.finished_signal.emit(False)

class VolumeDataLoaderThread(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)
    data_signal = pyqtSignal(object)   # Mesh

    def __init__(self, dxf_file):
        super().__init__()
        self.dxf_file = dxf_file

    def run(self):
        try:
            self.progress_signal.emit("⏳ Загрузка DXF для объёмов...")
            from core.dxf_geometry import load_dxf_geometry
            mesh, _, _ = load_dxf_geometry(
                self.dxf_file,
                load_mesh=True,
                load_polylines=False,
                load_circles=False
            )
            self.progress_signal.emit(f"✅ Загружено {len(mesh.x) if mesh else 0} точек")
            self.data_signal.emit(mesh)
            self.finished_signal.emit(True)
        except Exception as e:
            self.progress_signal.emit(f"❌ Ошибка загрузки: {e}")
            self.finished_signal.emit(False)


class HydraulicDataLoaderThread(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)
    data_signal = pyqtSignal(object, object)   # polylines, circles

    def __init__(self, dxf_file):
        super().__init__()
        self.dxf_file = dxf_file

    def run(self):
        try:
            self.progress_signal.emit("⏳ Загрузка DXF для гидравлики...")
            from core.dxf_geometry import load_dxf_geometry
            _, polylines, circles = load_dxf_geometry(
                self.dxf_file,
                load_mesh=False,
                load_polylines=True,
                load_circles=True
            )
            self.progress_signal.emit(f"✅ Полилиний: {len(polylines)}, кругов: {len(circles)}")
            self.data_signal.emit(polylines, circles)
            self.finished_signal.emit(True)
        except Exception as e:
            self.progress_signal.emit(f"❌ Ошибка загрузки: {e}")
            self.finished_signal.emit(False)