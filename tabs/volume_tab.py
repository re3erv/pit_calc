# tabs/volume_tab.py
import os
import numpy as np
import matplotlib.pyplot as plt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QTextEdit, QSplitter,
                             QFrame, QGridLayout, QFileDialog, QMessageBox)
from PyQt5.QtCore import Qt
from widgets import MplCanvas, HeightSearchThread, VolumeDataLoaderThread
from core.volume_core import compute_volume_by_contour
from utils import timed
from core.dxf_geometry import load_dxf_geometry  # или отдельная функция для mesh
from widgets import DataLoaderThread  # потребуется адаптировать поток
from core.height_search import VolumeCache

class VolumeTab(QWidget):
    def __init__(self, parent_app):
        super().__init__()
        self.parent_app = parent_app
        self.current_models = []
        self.current_colorbar = None
        self._init_ui()
        self.mesh = None
        self.volume_cache = VolumeCache()
        self.data_loader = None
        self.height_thread = None
        self.is_loading = False
        self.dxf_file = ""

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Строка выбора файла
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("DXF файл:"))
        self.file_edit = QLineEdit()
        self.file_edit.textChanged.connect(self._on_file_changed)
        file_layout.addWidget(self.file_edit, 1)
        browse_btn = QPushButton("Обзор")
        browse_btn.clicked.connect(self._browse_file)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout)

        # Блоки расчёта объёма и поиска высоты
        calc_layout = QHBoxLayout()

        # Объём по отметке
        left_frame = QFrame()
        left_frame.setFrameStyle(QFrame.Box)
        left_inner = QHBoxLayout(left_frame)
        left_inner.addWidget(QLabel("Отметка Z:"))
        self.z_edit = QLineEdit("1200.0")
        left_inner.addWidget(self.z_edit, 1)
        self.calc_btn = QPushButton("Рассчитать объём")
        self.calc_btn.clicked.connect(self._calculate_volume)
        self.calc_btn.setEnabled(False)
        left_inner.addWidget(self.calc_btn)
        calc_layout.addWidget(left_frame)

        # Поиск высоты
        right_frame = QFrame()
        right_frame.setFrameStyle(QFrame.Box)
        right_inner = QVBoxLayout(right_frame)
        target_layout = QHBoxLayout()
        target_layout.addWidget(QLabel("Целевой объём (м³):"))
        self.volumes_edit = QLineEdit("1000000")
        target_layout.addWidget(self.volumes_edit, 1)
        self.height_btn = QPushButton("Найти высоту")
        self.height_btn.clicked.connect(self._find_height)
        self.height_btn.setEnabled(False)
        target_layout.addWidget(self.height_btn)
        right_inner.addLayout(target_layout)
        h_tol_layout = QHBoxLayout()
        h_tol_layout.addWidget(QLabel("Точность по высоте (м):"))
        self.height_tol_edit = QLineEdit("0.001")
        h_tol_layout.addWidget(self.height_tol_edit, 1)
        right_inner.addLayout(h_tol_layout)
        v_tol_layout = QHBoxLayout()
        v_tol_layout.addWidget(QLabel("Точность по объёму (м³):"))
        self.volume_tol_edit = QLineEdit("")
        v_tol_layout.addWidget(self.volume_tol_edit, 1)
        right_inner.addLayout(v_tol_layout)
        calc_layout.addWidget(right_frame)
        layout.addLayout(calc_layout)

        # Сплиттер: график | гистограмма и лог
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # Левый график поверхности
        self.canvas = MplCanvas(self, width=10, height=8)
        self.orig_ax_pos = self.canvas.ax.get_position()
        left_w = QWidget()
        left_l = QVBoxLayout(left_w)
        left_l.addWidget(self.canvas)
        splitter.addWidget(left_w)

        # Правая часть
        right_splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(right_splitter)

        # Гистограмма
        self.canvas2 = MplCanvas(self, width=5, height=3)
        hist_widget = QWidget()
        hist_layout = QVBoxLayout(hist_widget)
        hist_layout.addWidget(self.canvas2)
        right_splitter.addWidget(hist_widget)

        # Текстовые поля
        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        text_layout.addWidget(QLabel("Лог:"))
        text_layout.addWidget(self.info_text)
        text_layout.addWidget(QLabel("Результаты:"))
        text_layout.addWidget(self.result_text)
        right_splitter.addWidget(text_widget)

        splitter.setSizes([800, 400])

    def _on_file_changed(self):
        path = self.file_edit.text().strip()
        if os.path.exists(path):
            self.dxf_file = path
            self._load_data()

    def _load_data(self):
        if not self.dxf_file or self.is_loading:
            return
        self.is_loading = True
        self.data_loader = VolumeDataLoaderThread(self.dxf_file)  # отдельный поток
        self.data_loader.progress_signal.connect(self._log)
        self.data_loader.finished_signal.connect(self._on_data_load_finished)
        self.data_loader.data_signal.connect(self._on_data_loaded)
        self.data_loader.start()

    def _on_data_loaded(self, mesh):
        self.mesh = mesh
        self.volume_cache.set_file(mesh.file_path)
        self.calc_btn.setEnabled(True)
        self.height_btn.setEnabled(True)
        self.plot_surface(z_level=None)

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите DXF", "", "DXF (*.dxf)")
        if path:
            self.file_edit.setText(path)

    def update_after_load(self):
        self.calc_btn.setEnabled(self.mesh is not None)
        self.height_btn.setEnabled(self.mesh is not None)

    @timed
    def _calculate_volume(self):
        if not self.mesh:
            return
        try:
            z_level = float(self.z_edit.text().replace(',', '.'))
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Некорректное значение Z")
            return
        total_volume, models = compute_volume_by_contour(
            self.mesh, z_level, return_details=True
        )
        self.current_models = models
        self.current_models = models
        self._log(f"🔢 Вычислено: Z={z_level:.3f} → V={total_volume:.1f} м³")
        self.result_text.setPlainText(f"Объём выемки при Z={z_level:.3f} м: {total_volume:.1f} м³")
        self.plot_surface(z_level, models)

    @timed
    def _find_height(self):
        if not self.mesh:
            return
        text = self.volumes_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "Ошибка", "Введите объём")
            return
        try:
            volumes = [float(v.strip().replace(',', '.')) for v in text.split(',')]
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Некорректный формат объёма")
            return
        height_tol = float(self.height_tol_edit.text().replace(',', '.')) or 0.001
        volume_tol_text = self.volume_tol_edit.text().strip()
        volume_tol = float(volume_tol_text.replace(',', '.')) if volume_tol_text else None

        self.height_btn.setEnabled(False)
        self._log(f"⏳ Поиск высоты для {len(volumes)} объёмов...")
        self.height_thread = HeightSearchThread(
            self.mesh,
            volumes,
            self.volume_cache,
            height_tol,
            volume_tol
        )
        self.height_thread.progress_signal.connect(self._log)
        self.height_thread.finished_signal.connect(self._on_height_finished)
        self.height_thread.result_signal.connect(self._on_height_result)
        self.height_thread.start()

    def _on_height_finished(self, success):
        self.height_btn.setEnabled(True)
        if not success:
            QMessageBox.warning(self, "Ошибка", "Сбой при поиске высоты")

    def _on_height_result(self, data):
        lines = ["Результаты поиска высоты:"]
        for i, det in enumerate(data['details']):
            if np.isnan(det['z']):
                lines.append(f"Объём {det['volume']:.1f} м³: превышает максимальный объём")
            else:
                lines.append(
                    f"Объём {det['volume']:.1f} м³ → Z = {det['z']:.6f} м "
                    f"(итераций: {det['iterations']}, откл: {det['error']*100:.4f}%)"
                )
                lines.append(
                    f"    Точность: по высоте интервал {det['height_interval']:.6f} м "
                    f"(задан {det['height_tol']:.6f} м), по объёму разница {det['volume_diff']:.1f} м³"
                )
        lines.append("")
        lines.append(data['cache_stats'])
        self.result_text.setPlainText("\n".join(lines))
        self._log("✅ Поиск завершён.")

    def _log(self, msg):
        self.info_text.append(msg)
        self.info_text.verticalScrollBar().setValue(self.info_text.verticalScrollBar().maximum())
        self.parent_app._log(msg)   # выводим в главное окно/консоль

    @timed
    def plot_surface(self, z_level=None, models=None):
        if not self.mesh:
            return
        ax = self.canvas.ax
        if self.current_colorbar is not None:
            try:
                self.current_colorbar.remove()
            except:
                pass
            self.current_colorbar = None
            if hasattr(self, 'orig_ax_pos'):
                ax.set_position(self.orig_ax_pos)
        ax.clear()
        x = self.mesh.x
        y = self.mesh.y
        z = self.mesh.z
        tri = self.mesh.tri
        if z_level is None:
            sc = ax.scatter(x, y, c=z, s=2, cmap='viridis')
            self.current_colorbar = self.canvas.fig.colorbar(sc, ax=ax, label='Z, м')
            ax.set_title('Точки поверхности')
        else:
            depths = z_level - z
            depths_clipped = np.clip(depths, 0, None)
            tri_depths = depths_clipped[tri].mean(axis=1)
            tpc = ax.tripcolor(x, y, tri, facecolors=tri_depths,
                               cmap='Blues', edgecolors='gray', linewidths=0.1)
            self.current_colorbar = self.canvas.fig.colorbar(tpc, ax=ax, label='Глубина, м')
            ax.set_title(f'Объём под плоскостью Z={z_level:.3f} м')
            if models:
                for m in models:
                    c = np.vstack([m['contour'], m['contour'][0]])
                    ax.plot(c[:, 0], c[:, 1], 'black', linewidth=1.5, alpha=0.8)
                    if m['volume'] > 100:
                        center = np.mean(m['contour'], axis=0)
                        ax.text(center[0], center[1],
                                f'K{m["index"]}: {m["volume"]:.0f} м³',
                                fontsize=8, ha='center', va='center', weight='bold',
                                bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))
        ax.set_xlabel('X')
        ax.set_ylabel('Y')
        ax.axis('equal')
        ax.grid(True, linestyle='--', alpha=0.3)
        self.canvas.draw()

    def _on_data_load_finished(self, success):
        self.is_loading = False
        if not success:
            QMessageBox.warning(self, "Ошибка", "Сбой при загрузке данных")