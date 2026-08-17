# tabs/hydraulic_tab.py
import os
import math
import numpy as np
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
import json

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QTextEdit, QFileDialog, QMessageBox,
                             QSplitter, QGridLayout, QComboBox, QCheckBox,
                             QTableWidget, QTableWidgetItem)
from PyQt5.QtCore import Qt
from widgets import MplCanvas, HydraulicDataLoaderThread
from core.hydraulic_core import calculate_pipeline
from utils import timed
from data.pipe_catalog import PIPE_CATALOG, get_pipe_type_names, get_default_params, get_steel_options_for_diameter, get_pe_params
from data.zeta_catalog import get_zeta_for_type
from core.geometry_utils import closest_point_on_polyline
from core.utils import parse_float
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar

class HydraulicTab(QWidget):
    def __init__(self, parent_app):
        super().__init__()
        self.parent_app = parent_app
        self._poly_signal_connected = False

        # Словарь типов труб создаём до вызова _init_ui
        self.pipe_catalog = PIPE_CATALOG

        self.polylines = []
        self.circles = []
        self.data_loader = None
        self.is_loading = False
        self.dxf_file = ""
        self._updating_file_edits = False
        self._default_params = {}
        self._default_pn = None   # дефолтное PN, МПа
        self._flow_unit_coeff = 1.0  # м³/с
        self.last_hydraulic_result = None
        self.last_result_poly_index = None
        # Хранилище параметров и ролей для каждой полилинии
        self.polyline_data = {}        
        self.loss_yellow_threshold = 0.01   # потери на метр, м/м
        self.min_pressure_excess = 2.0   # минимальный запас над трубой, м
        self._updating_zoom_sync = False

        self._init_ui()
        
    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Строка загрузки DXF
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("DXF файл:"))
        self.hydraulic_file_edit = QLineEdit()
        self.hydraulic_file_edit.textChanged.connect(self._on_file_changed_from_hydraulic)
        file_layout.addWidget(self.hydraulic_file_edit, 1)
        browse_btn = QPushButton("Обзор")
        browse_btn.clicked.connect(self._browse_file_hydraulic)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout)

        # Основной сплиттер
        main_splitter = QSplitter(Qt.Horizontal)

        # Левая часть: план и профиль
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        self.plan_canvas = MplCanvas(self, width=5, height=4)
        self.plan_toolbar = NavigationToolbar(self.plan_canvas, self)   # <-- добавить
        self.profile_canvas = MplCanvas(self, width=5, height=4)

        left_layout.addWidget(QLabel("План"))
        left_layout.addWidget(self.plan_toolbar)                         # <-- тулбар над планом
        left_layout.addWidget(self.plan_canvas)
        left_layout.addWidget(QLabel("Продольный профиль"))
        left_layout.addWidget(self.profile_canvas)
        left_layout.addWidget(QLabel("Эпюра избыточного давления"))
        self.pressure_hist_canvas = MplCanvas(self, width=5, height=2)
        left_layout.addWidget(self.pressure_hist_canvas)

        main_splitter.addWidget(left_widget)

        # Правая часть
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Выбор полилинии
        poly_layout = QHBoxLayout()
        poly_layout.addWidget(QLabel("Полилиния:"))
        self.poly_combo = QComboBox()
        self.poly_combo.currentIndexChanged.connect(self._on_poly_selected)
        poly_layout.addWidget(self.poly_combo)
        right_layout.addLayout(poly_layout)

        # Чекбокс показа всех полилиний
        self.show_all_checkbox = QCheckBox("Показывать все полилинии")
        self.show_all_checkbox.setChecked(False)
        self.show_all_checkbox.toggled.connect(self._on_show_all_toggled)
        right_layout.addWidget(self.show_all_checkbox)

        # Таблица кругов
        right_layout.addWidget(QLabel("Объекты (круги)"))
        self.circles_table = QTableWidget()
        self.circles_table.setColumnCount(4)
        self.circles_table.setHorizontalHeaderLabels(["Объект", "Тип", "ζ", "Напор, м"])
        self.circles_table.horizontalHeader().setStretchLastSection(True)
        self.circles_table.setMaximumHeight(150)
        right_layout.addWidget(self.circles_table)
        self.save_roles_btn = QPushButton("Сохранить роли объектов")
        self.save_roles_btn.clicked.connect(self._save_roles_to_file)
        right_layout.addWidget(self.save_roles_btn)

        # Параметры трубы
        pipe_grid = QGridLayout()

        # Выбор типа трубы
        pipe_grid.addWidget(QLabel("Тип трубы:"), 0, 0)
        self.pipe_type_combo = QComboBox()
        self.pipe_type_combo.addItems(get_pipe_type_names())
        self.pipe_type_combo.currentIndexChanged.connect(self._on_pipe_type_changed)
        pipe_grid.addWidget(self.pipe_type_combo, 0, 1)

        # Внешний диаметр
        pipe_grid.addWidget(QLabel("Внешний диаметр, м:"), 1, 0)
        outer_d_layout = QHBoxLayout()
        self.outer_d_combo = QComboBox()
        self.outer_d_combo.setEditable(True)
        self.outer_d_combo.currentIndexChanged.connect(self._on_outer_d_changed)
        outer_d_layout.addWidget(self.outer_d_combo)
        self.outer_d_default_label = QLabel()
        outer_d_layout.addWidget(self.outer_d_default_label)
        self.outer_d_reset_btn = QPushButton("↺")
        self.outer_d_reset_btn.setFixedWidth(30)
        self.outer_d_reset_btn.clicked.connect(self._reset_outer_d)
        outer_d_layout.addWidget(self.outer_d_reset_btn)
        pipe_grid.addLayout(outer_d_layout, 1, 1)

        # Толщина стенки
        pipe_grid.addWidget(QLabel("Толщина стенки, м:"), 2, 0)
        wall_layout = QHBoxLayout()
        self.wall_combo = QComboBox()
        self.wall_combo.setEditable(True)
        self.wall_combo.currentIndexChanged.connect(self._on_wall_changed)
        wall_layout.addWidget(self.wall_combo)
        self.wall_default_label = QLabel()
        wall_layout.addWidget(self.wall_default_label)
        self.wall_reset_btn = QPushButton("↺")
        self.wall_reset_btn.setFixedWidth(30)
        self.wall_reset_btn.clicked.connect(self._reset_wall)
        wall_layout.addWidget(self.wall_reset_btn)
        pipe_grid.addLayout(wall_layout, 2, 1)

        # Шероховатость
        pipe_grid.addWidget(QLabel("Шероховатость, м:"), 3, 0)
        rough_layout = QHBoxLayout()
        self.rough_combo = QComboBox()
        self.rough_combo.setEditable(True)
        rough_layout.addWidget(self.rough_combo)
        self.rough_default_label = QLabel()
        rough_layout.addWidget(self.rough_default_label)
        self.rough_reset_btn = QPushButton("↺")
        self.rough_reset_btn.setFixedWidth(30)
        self.rough_reset_btn.clicked.connect(self._reset_rough)
        rough_layout.addWidget(self.rough_reset_btn)
        pipe_grid.addLayout(rough_layout, 3, 1)

        # Мин. радиус гиба
        pipe_grid.addWidget(QLabel("Мин. радиус гиба, м:"), 4, 0)
        r_min_layout = QHBoxLayout()
        self.r_min_combo = QComboBox()
        self.r_min_combo.setEditable(True)
        r_min_layout.addWidget(self.r_min_combo)
        self.r_min_default_label = QLabel()
        r_min_layout.addWidget(self.r_min_default_label)
        self.r_min_reset_btn = QPushButton("↺")
        self.r_min_reset_btn.setFixedWidth(30)
        self.r_min_reset_btn.clicked.connect(self._reset_r_min)
        r_min_layout.addWidget(self.r_min_reset_btn)
        pipe_grid.addLayout(r_min_layout, 4, 1)

        # Температура и угол
        pipe_grid.addWidget(QLabel("Температура, °C:"), 6, 0)
        self.temp_edit = QLineEdit("15")
        pipe_grid.addWidget(self.temp_edit, 6, 1)

        pipe_grid.addWidget(QLabel("Мин. угол поворота, град:"), 7, 0)
        self.min_angle_edit = QLineEdit("1.0")
        pipe_grid.addWidget(self.min_angle_edit, 7, 1)

        # Пороги потерь для цветовой индикации
        # Единая строка для допуска, жёлтого порога и мин. запаса
        misc_layout = QHBoxLayout()
        misc_layout.addWidget(QLabel("Допуск привязки, м:"))
        self.snap_tol_edit = QLineEdit("0.5")
        self.snap_tol_edit.setFixedWidth(60)
        self.snap_tol_edit.editingFinished.connect(self._on_tolerance_changed)
        misc_layout.addWidget(self.snap_tol_edit)
        misc_layout.addSpacing(10)

        misc_layout.addWidget(QLabel("Жёлтый порог, м/м:"))
        self.loss_yellow_edit = QLineEdit("0.01")
        self.loss_yellow_edit.setFixedWidth(60)
        self.loss_yellow_edit.editingFinished.connect(self._update_loss_thresholds)
        misc_layout.addWidget(self.loss_yellow_edit)
        misc_layout.addSpacing(10)

        misc_layout.addWidget(QLabel("Мин. запас, м:"))
        self.min_pressure_edit = QLineEdit(str(self.min_pressure_excess))
        self.min_pressure_edit.setFixedWidth(60)
        misc_layout.addWidget(self.min_pressure_edit)
        self.min_pressure_edit.editingFinished.connect(self._update_min_pressure_excess)

        misc_layout.addStretch()
        right_layout.addLayout(misc_layout)

        # Расход и скорость в одной строке
        flow_speed_layout = QHBoxLayout()

        flow_speed_layout.addWidget(QLabel("Расход:"))

        self.flow_edit = QLineEdit("0.0278")
        self.flow_edit.setFixedWidth(80)
        flow_speed_layout.addWidget(self.flow_edit)

        self.flow_unit_combo = QComboBox()
        self.flow_unit_combo.addItem("м³/с", 1.0)
        self.flow_unit_combo.addItem("м³/ч", 1.0/3600.0)
        self.flow_unit_combo.addItem("м³/сут", 1.0/86400.0)
        self.flow_unit_combo.currentIndexChanged.connect(self._on_flow_unit_changed)
        flow_speed_layout.addWidget(self.flow_unit_combo)

        self.use_velocity_checkbox = QCheckBox("↔")
        self.use_velocity_checkbox.toggled.connect(self._toggle_velocity_input)
        flow_speed_layout.addWidget(self.use_velocity_checkbox)

        flow_speed_layout.addWidget(QLabel("Скорость:"))
        self.velocity_edit = QLineEdit("1.5")
        self.velocity_edit.setFixedWidth(80)
        self.velocity_edit.setEnabled(False)
        flow_speed_layout.addWidget(self.velocity_edit)
        flow_speed_layout.addWidget(QLabel("м/с"))

        pipe_grid.addLayout(flow_speed_layout, 8, 0, 1, 4)

        # PN и чекбокс
        pipe_grid.addWidget(QLabel("Номинальное давление PN, МПа:"), 10, 0)
        pn_layout = QHBoxLayout()
        self.pn_edit = QLineEdit("1.0")
        pn_layout.addWidget(self.pn_edit)
        self.pn_default_label = QLabel()
        pn_layout.addWidget(self.pn_default_label)
        self.pn_reset_btn = QPushButton("↺")
        self.pn_reset_btn.setFixedWidth(30)
        self.pn_reset_btn.clicked.connect(self._reset_pn)
        pn_layout.addWidget(self.pn_reset_btn)
        pipe_grid.addLayout(pn_layout, 10, 1)

        self.check_pn_checkbox = QCheckBox("Проверять превышение PN")
        self.check_pn_checkbox.setChecked(True)
        pipe_grid.addWidget(self.check_pn_checkbox, 11, 0, 1, 2)

        # Кнопки сохранения параметров и проекта
        right_layout.addLayout(pipe_grid)
        save_buttons_layout = QHBoxLayout()
        self.save_params_btn = QPushButton("Сохранить параметры водовода")
        self.save_params_btn.clicked.connect(self._save_params_to_file)
        save_buttons_layout.addWidget(self.save_params_btn)
        self.save_project_btn = QPushButton("Сохранить проект")
        self.save_project_btn.clicked.connect(self._save_project_to_file)
        save_buttons_layout.addWidget(self.save_project_btn)
        self.load_project_btn = QPushButton("Загрузить проект")
        self.load_project_btn.clicked.connect(self._load_project_from_file)
        save_buttons_layout.addWidget(self.load_project_btn)
        right_layout.addLayout(save_buttons_layout)

        reset_params_btn = QPushButton("Сбросить параметры к типу трубы")
        reset_params_btn.clicked.connect(self._on_pipe_type_changed)
        right_layout.addWidget(reset_params_btn)

        self.calc_hydraulic_btn = QPushButton("Рассчитать гидравлику")
        self.calc_hydraulic_btn.clicked.connect(lambda: self._run_hydraulic())
        self.calc_hydraulic_btn.setEnabled(False)
        right_layout.addWidget(self.calc_hydraulic_btn)

        right_layout.addWidget(QLabel("Результаты"))
        self.hydraulic_result_text = QTextEdit()
        self.hydraulic_result_text.setReadOnly(True)
        right_layout.addWidget(self.hydraulic_result_text)

        main_splitter.addWidget(right_widget)
        main_splitter.setSizes([800, 500])
        layout.addWidget(main_splitter)
        # Заполняем поля начальными значениями выбранного типа трубы
        self._on_pipe_type_changed()  
        self._setup_zoom_sync()    

    def _on_file_changed_from_hydraulic(self):
        path = self.hydraulic_file_edit.text().strip()
        if os.path.exists(path):
            self.dxf_file = path
            self._load_data()

    def _on_data_loaded(self, polylines, circles):
        self.polylines = polylines
        self.circles = circles          # <-- сначала задаём круги

        self.polyline_data = {}
        for i in range(len(polylines)):
            self.polyline_data[i] = {
                'params': self._get_current_params(),
                'roles': []
            }

        # Заполняем комбобокс полилиний
        self.update_after_load()

        # Устанавливаем первую полилинию
        self._current_poly_index = 0
        self.poly_combo.blockSignals(True)
        self.poly_combo.setCurrentIndex(0)
        self.poly_combo.blockSignals(False)

        # Теперь вызываем обработчик, который подхватит данные для индекса 0
        self._on_poly_selected()
        self.on_tab_activated()

    def _load_data(self):
        if not self.dxf_file or self.is_loading:
            return
        self.is_loading = True
        self.data_loader = HydraulicDataLoaderThread(self.dxf_file)
        self.data_loader.progress_signal.connect(self._log)
        self.data_loader.finished_signal.connect(self._on_data_load_finished)
        self.data_loader.data_signal.connect(self._on_data_loaded)
        self.data_loader.start()

    def _browse_file_hydraulic(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите DXF", "", "DXF (*.dxf)")
        if path:
            self.hydraulic_file_edit.setText(path)

    def _on_poly_selected(self):
        self.last_hydraulic_result = None
        self.last_result_poly_index = None
        self._update_polyline_display()
        self._update_circles_table()
        if hasattr(self, 'pressure_hist_canvas'):
            self.pressure_hist_canvas.ax.clear()
            self.pressure_hist_canvas.draw()

    def update_after_load(self):
        self.poly_combo.blockSignals(True)
        self.poly_combo.clear()
        for i, poly in enumerate(self.polylines):
            self.poly_combo.addItem(f"Полилиния {i+1} ({len(poly)} точек)", i)
        self.poly_combo.blockSignals(False)

        self.calc_hydraulic_btn.setEnabled(bool(self.polylines))

    def on_tab_activated(self):
        """Вызывается при переключении на вкладку «Гидравлика»."""
        if self.polylines:
            self._update_polyline_display()
            self._update_circles_table()

    @timed
    def _update_polyline_display(self):
        try:
            if not self.polylines:
                return
            ax_plan = self.plan_canvas.ax
            ax_plan.clear()
            self.plan_canvas.ax.callbacks.connect('xlim_changed', self._on_plan_xlim_changed)
            selected_idx = self.poly_combo.currentData()
            if selected_idx is None or selected_idx < 0 or selected_idx >= len(self.polylines):
                return

            show_all = self.show_all_checkbox.isChecked()
            has_result = (self.last_hydraulic_result is not None and
                          self.last_result_poly_index == selected_idx)
            
            pn_head = self._get_pn_head() if has_result else 0.0

            if show_all:
                for idx, poly in enumerate(self.polylines):
                    x_vals = [p[0] for p in poly]
                    y_vals = [p[1] for p in poly]
                    if idx == selected_idx and has_result:
                        segments = self.last_hydraulic_result['segments']
                        max_loss = max(seg['total_loss'] for seg in segments) if segments else 1e-9
                        for seg in segments:
                            i = seg['index']
                            x0, y0 = poly[i][0], poly[i][1]
                            x1, y1 = poly[i+1][0], poly[i+1][1]
                            color = self._get_segment_color(seg, pn_head)
                            ax_plan.plot([x0, x1], [y0, y1], color=color, linewidth=3)
                    else:
                        ax_plan.plot(x_vals, y_vals, color='gray', linewidth=0.5, alpha=0.4)
                # После цикла устанавливаем poly для выбранной полилинии,
                # чтобы профиль и круги использовали именно её
                poly = self.polylines[selected_idx]
            else:
                poly = self.polylines[selected_idx]
                x_vals = [p[0] for p in poly]
                y_vals = [p[1] for p in poly]
                if has_result:
                    # Рисуем сегменты с цветом в зависимости от потерь
                    segments = self.last_hydraulic_result['segments']
                    max_loss = max(seg['total_loss'] for seg in segments) if segments else 1e-9
                    for seg in segments:
                        i = seg['index']
                        x0, y0 = poly[i][0], poly[i][1]
                        x1, y1 = poly[i+1][0], poly[i+1][1]
                        color = self._get_segment_color(seg, pn_head)
                        ax_plan.plot([x0, x1], [y0, y1], color=color, linewidth=3,
                                    solid_capstyle='round')
                else:
                    ax_plan.plot(x_vals, y_vals, 'o-', color='blue', markersize=3,
                                linewidth=2, label=f'P{selected_idx+1}')

                # Номера вершин
                for i, (x, y) in enumerate(zip(x_vals, y_vals)):
                    ax_plan.annotate(str(i), (x, y),
                                     textcoords="offset points", xytext=(0, 5),
                                     ha='center', fontsize=6, color='black')
            # Круги на плане (используем допуск)
            snap_tol = self._get_snap_tolerance()
            for i, circle in enumerate(self.circles):
                min_dist, _, _, _ = closest_point_on_polyline(circle['center'][:2], poly)
                if min_dist < snap_tol:
                    cx, cy, _ = circle['center']
                    r = circle['radius']
                    ax_plan.add_patch(plt.Circle((cx, cy), r, fill=False, edgecolor='blue', linewidth=1))
                    ax_plan.text(cx + r*1.2, cy + r*1.2, f'O{i+1}', fontsize=8, color='blue', ha='center', va='center')

            ax_plan.set_xlabel('X, м')
            ax_plan.set_ylabel('Y, м')
            ax_plan.set_title(f'План трассы (полилиния {selected_idx+1})')
            ax_plan.set_aspect('auto')
            ax_plan.grid(True, linestyle='--', alpha=0.3)
            ax_plan.plot([], [], color='red', label='Превышение PN (м)')
            ax_plan.plot([], [], color='purple', label='Вакуум (м)')
            ax_plan.plot([], [], color='orange', label='Низкий запас (м)')
            ax_plan.plot([], [], color='yellow', label='Местные потери (м)')
            ax_plan.plot([], [], color='green', label='Запас давления (м)')
            ax_plan.legend()         
            self.plan_canvas.draw()

            # Профиль (выбранная полилиния)
            ax_prof = self.profile_canvas.ax
            ax_prof.clear()
            dists = [0.0]
            for i in range(1, len(poly)):
                d = np.linalg.norm(np.array(poly[i]) - np.array(poly[i-1]))
                dists.append(dists[-1] + d)
            z_vals = [p[2] for p in poly]
            if has_result:
                segments = self.last_hydraulic_result['segments']
                for seg in segments:
                    i = seg['index']
                    d0, d1 = dists[i], dists[i+1]
                    z0, z1 = z_vals[i], z_vals[i+1]
                    color = self._get_segment_color(seg, pn_head)
                    ax_prof.plot([d0, d1], [z0, z1], color=color, linewidth=2)

                    # Подпись только если есть что показать
                    label, label_color = self._get_segment_label(seg, pn_head)
                    if label:
                        # Вычисляем угол в экранных (пиксельных) координатах
                        p1_pix = ax_prof.transData.transform((d0, z0))
                        p2_pix = ax_prof.transData.transform((d1, z1))
                        dx_pix = p2_pix[0] - p1_pix[0]
                        dy_pix = p2_pix[1] - p1_pix[1]

                        angle = math.degrees(math.atan2(dy_pix, dx_pix)) + 90

                        # если сегмент визуально горизонтален, оставляем подпись горизонтальной
                        if abs(dy_pix) < 1e-3:
                            angle = 0

                        ax_prof.text((d0+d1)/2, (z0+z1)/2,
                                    label,
                                    fontsize=7, color=label_color,
                                    ha='center', va='bottom',
                                    rotation=angle,
                                    rotation_mode='anchor')
            else:
                ax_prof.plot(dists, z_vals, 'o-', color='blue', markersize=3)
            ax_prof.set_xlabel('Горизонтальное расстояние, м')
            ax_prof.set_ylabel('Отметка Z, м')
            ax_prof.set_title(f'Продольный профиль полилинии {selected_idx+1}')
            ax_prof.grid(True, linestyle='--', alpha=0.3)

            # Круги на профиле
            for i, circle in enumerate(self.circles):
                min_dist, _, seg_idx, t = closest_point_on_polyline(circle['center'][:2], poly)
                if min_dist < snap_tol:
                    proj_dist = 0.0
                    for j in range(seg_idx):
                        proj_dist += np.linalg.norm(np.array(poly[j+1]) - np.array(poly[j]))
                    if seg_idx < len(poly) - 1:
                        seg_vec = np.array(poly[seg_idx+1]) - np.array(poly[seg_idx])
                        proj_dist += t * np.linalg.norm(seg_vec)
                    z_min = min(z_vals)
                    z_max = max(z_vals)
                    ax_prof.plot([proj_dist, proj_dist], [z_min, z_max], '--', color='orange', linewidth=1)
                    ax_prof.text(proj_dist, z_max, f'O{i+1}', fontsize=8, color='orange', ha='center', va='bottom')

            self.profile_canvas.draw()

            if has_result:
                self._on_plan_xlim_changed(self.plan_canvas.ax)
        except Exception as e:
            self._log(f"Ошибка отрисовки: {e}")

    @timed
    def _run_hydraulic(self):
        if not self.polylines:
            QMessageBox.warning(self, "Ошибка", "Нет загруженных полилиний")
            return
        try:
            idx = self.poly_combo.currentData()
            if idx is None or idx >= len(self.polylines):
                QMessageBox.warning(self, "Ошибка", "Выберите полилинию")
                return
            poly = self.polylines[idx]
            n_points = len(poly)

            # Считываем параметры трубы
            outer_d = self._get_combo_value(self.outer_d_combo)
            wall = self._get_combo_value(self.wall_combo)
            rough = self._get_combo_value(self.rough_combo)
            r_min = self._get_combo_value(self.r_min_combo)

            if None in (outer_d, wall, rough, r_min):
                QMessageBox.warning(self, "Ошибка", "Некорректные параметры трубы")
                return

            inner_d = outer_d - 2 * wall
            area = math.pi * inner_d**2 / 4.0

            if self.use_velocity_checkbox.isChecked():
                velocity = float(self.velocity_edit.text().replace(',', '.'))
                flow = velocity * area
            else:
                flow_m3s = float(self.flow_edit.text().replace(',', '.')) * self.flow_unit_combo.currentData()
                flow = flow_m3s

            temp = float(self.temp_edit.text().replace(',', '.'))
            min_angle = float(self.min_angle_edit.text().replace(',', '.'))

            pipe_params = {
                'outer_diameter': outer_d,
                'wall_thickness': wall,
                'roughness': rough,
                'r_min': r_min,
            }

            pn = None
            check_pn = self.check_pn_checkbox.isChecked()
            if check_pn:
                try:
                    pn = float(self.pn_edit.text().replace(',', '.')) * 1e6
                except ValueError:
                    QMessageBox.warning(self, "Ошибка", "Некорректное значение PN")
                    return

            # Собираем объекты и их привязку к вершинам исходной полилинии
            objects_info = []   # (type, vertex_index, zeta, head_value или None)
            inlet_vertex = None
            outlet_vertex = None
            p_in_head = None
            p_out_head = None

            for row in range(self.circles_table.rowCount()):
                obj_item = self.circles_table.item(row, 0)
                if obj_item is None:
                    continue
                circle_idx = obj_item.data(Qt.UserRole)
                if circle_idx is None or circle_idx >= len(self.circles):
                    continue
                combo = self.circles_table.cellWidget(row, 1)
                obj_type = combo.currentText() if combo else ''
                if not obj_type:
                    QMessageBox.warning(self, "Ошибка", "Не для всех объектов задан тип")
                    return

                # Читаем напор для всаса/выпуска
                head_value = None
                if obj_type == 'всас' or obj_type == 'выпуск':
                    nap_item = self.circles_table.item(row, 3)
                    if nap_item and nap_item.text().strip():
                        try:
                            head_value = float(nap_item.text().replace(',', '.'))
                        except ValueError:
                            head_value = None

                # Читаем ζ
                zeta_item = self.circles_table.item(row, 2)
                try:
                    zeta = float(zeta_item.text().replace(',', '.'))
                except:
                    zeta = get_zeta_for_type(obj_type)

                # Определяем ближайшую вершину к центру круга
                circle = self.circles[circle_idx]
                min_dist, _, seg_idx, t = closest_point_on_polyline(circle['center'][:2], poly)
                if min_dist >= circle['radius'] * 2:
                    continue  # объект не привязан к этой полилинии

                if t > 0.5:
                    vertex_index = seg_idx + 1
                else:
                    vertex_index = seg_idx

                if vertex_index >= n_points:
                    vertex_index = n_points - 1

                objects_info.append({
                    'type': obj_type,
                    'vertex_index': vertex_index,
                    'zeta': zeta,
                    'head': head_value
                })

                if obj_type == 'всас':
                    inlet_vertex = vertex_index
                    p_in_head = head_value
                elif obj_type == 'выпуск':
                    outlet_vertex = vertex_index
                    p_out_head = head_value

            # Если всас или выпуск не найдены, устанавливаем значения по умолчанию
            if p_in_head is None:
                p_in_head = 0.0
            if p_out_head is None:
                p_out_head = 1.0

            # Определяем направление
            reverse_needed = False
            if inlet_vertex is not None and outlet_vertex is not None:
                if inlet_vertex > outlet_vertex:
                    reverse_needed = True

            # Разворачиваем полилинию при необходимости
            if reverse_needed:
                poly = list(reversed(poly))
                n_points_rev = len(poly)
                # Пересчитываем vertex_index для объектов
                for obj in objects_info:
                    old_index = obj['vertex_index']
                    new_index = (n_points - 1) - old_index
                    obj['vertex_index'] = new_index

            # Формируем fittings, используя исправленные vertex_index как station
            fittings = []
            for obj in objects_info:
                fittings.append({
                    'station': obj['vertex_index'],
                    'k': obj['zeta'],
                    'obj_type': obj['type']
                })

            # Запускаем расчёт
            result = calculate_pipeline(poly, pipe_params, flow, fluid_temp=temp,
                                        check_pn=check_pn, pn=pn,
                                        min_angle_deg=min_angle,
                                        fittings=fittings,
                                        p_in_head=p_in_head,
                                        p_out_head=p_out_head)

            self.last_hydraulic_result = result
            self.last_result_poly_index = idx
            self._update_polyline_display()   # добавить эту строку
            self._update_pressure_histogram()

            report = f"=== Гидравлический расчёт полилинии {idx+1} ===\n"

            # Формулы с подставленными значениями
            d = result['inner_diameter']
            V = result['velocity']
            L = result['total_length']
            # λ возьмём из первого сегмента (можно усреднить, но для простоты)
            if result['segments']:
                lambda_ = result['segments'][0]['friction_factor']
            else:
                lambda_ = 0.0

            report += f"\n   Внутренний диаметр d = {d:.6f} м\n"
            report += f"   Z_вход = {poly[0][2]:.2f} м, Z_выход = {poly[-1][2]:.2f} м, ΔZ = {poly[-1][2]-poly[0][2]:+.2f} м\n"
            report += f"   Длина L = {L:.2f} м\n"
            report += f"   H_своб = {p_out_head:.2f} м\n"
            report += f"   H_вс = {p_in_head:.2f} м\n"

            report += "\n1) Расход и скорость:\n"
            report += "   V = Q / (π·d²/4)\n"

            if self.use_velocity_checkbox.isChecked():
                # Режим задания скорости
                report += f"   Скорость задана: V = {velocity:.3f} м/с\n"
                report += f"   Q = V·(π·d²/4) = {velocity:.3f} · (3.1416 · {d:.6f}² / 4) = {flow:.6f} м³/с = {flow * 3600:.4f} м³/ч = {flow * 86400:.4f} м³/сут\n"
            else:
                # Режим задания расхода
                flow_input = float(self.flow_edit.text().replace(',', '.'))
                flow_unit_text = self.flow_unit_combo.currentText()
                report += f"   Заданный расход: Q = {flow_input:.6f} {flow_unit_text}, Q = {flow * 3600:.4f} м³/ч, Q = {flow * 86400:.4f} м³/сут\n"
                report += f"   V = Q / (π·d²/4) = {flow:.6f} / (3.1416 · {d:.6f}² / 4) = {V:.3f} м/с\n\n"

            report += f"2) Потери на трение (Дарси–Вейсбах):\n"
            report += f"   h_тр = λ · (L/d) · (V²/(2g))\n"
            report += f"   λ = {lambda_:.4f}, L = {L:.2f} м, d = {d:.6f} м, V = {V:.3f} м/с, g = 9.81 м/с²\n"
            report += f"   h_тр = {lambda_:.4f} · ({L:.2f}/{d:.6f}) · ({V:.3f}²/(2·9.81)) = {result['total_friction_loss']:.3f} м\n\n"

            report += f"3) Местные сопротивления:\n"
            report += f"   h_м = Σ ζ · (V²/(2g))\n"
            report += f"   Σ ζ = {result['total_zeta']:.3f}, V = {V:.3f} м/с\n"
            report += f"   h_м = {result['total_zeta']:.3f} · ({V:.3f}²/(2·9.81)) = {result['total_local_loss']:.3f} м\n\n"
            
            report += f"4) Полный требуемый напор насоса:\n"
            report += f"   H_треб = (Z_выход − Z_вход) + H_своб − H_вс + h_тр + h_м\n"
            report += f"   Z_вход = {poly[0][2]:.2f} м, Z_выход = {poly[-1][2]:.2f} м, H_своб = {p_out_head:.2f} м, H_вс = {p_in_head:.2f} м\n"
            report += f"   h_тр = {result['total_friction_loss']:.3f} м, h_м = {result['total_local_loss']:.3f} м\n"
            report += f"   H_треб = ({poly[-1][2]:.2f} − {poly[0][2]:.2f}) + {p_out_head:.2f} − ({p_in_head:.2f}) + {result['total_friction_loss']:.3f} + {result['total_local_loss']:.3f} = {result['required_head']:.3f} м\n"

            report += f"Длина: {result['total_length']:.2f} м\n"
            report += f"Внутренний диаметр: {result['inner_diameter']*1000:.1f} мм\n"
            report += f"Скорость: {result['velocity']:.2f} м/с\n"
            report += f"Число Рейнольдса: {result['reynolds']:.0f}\n"
            report += f"Суммарные потери: {result['total_head_loss']:.2f} м\n"
            report += f"  - на трение по длине: {result.get('total_friction_loss', 0):.2f} м\n"
            report += f"  - местные: {result.get('total_local_loss', 0):.2f} м\n"
                        # Контрольная сумма слагаемых
            check_sum = (poly[-1][2] - poly[0][2]) + p_out_head - p_in_head + result['total_friction_loss'] + result['total_local_loss']
            report += f"Контрольная сумма: ΔZ + H_своб − H_вс + h_тр + h_м = "
            report += f"({poly[-1][2]:.2f} − {poly[0][2]:.2f}) + {p_out_head:.2f} − {p_in_head:.2f} + {result['total_friction_loss']:.3f} + {result['total_local_loss']:.3f} = {check_sum:.3f} м\n"
            report += f"Совпадение с H_треб: {'✅' if abs(check_sum - result['required_head']) < 0.01 else '❌'}\n\n"
            report += f"Требуемый напор насоса: {result['required_head']:.2f} м\n\n"

            if result['warnings']:
                report += "Предупреждения:\n"
                for w in result['warnings']:
                    report += f"  - {w}\n"
            else:
                report += "Предупреждений нет.\n"

            report += "\nЭпюра напоров (узел, X, Y, Z, H, ΔZ, h_тр, h_м, P_изб):\n"
            for i, (pt, head) in enumerate(zip(result['points'], result['station_heads'])):
                x = pt[0]
                y = pt[1]
                z = pt[2]
                p_excess = head - z

                if i == 0:
                    dz = 0.0
                    h_fric = 0.0
                    h_loc = 0.0
                else:
                    prev_z = result['points'][i-1][2]
                    dz = z - prev_z
                    seg = result['segments'][i-1]
                    h_fric = seg['friction_loss']
                    h_loc = seg['local_loss']

                report += (f"  {i}: X={x:.2f}, Y={y:.2f}, Z={z:.2f} м, H={head:.2f} м, "
                           f"ΔZ={dz:+.2f} м, h_тр={h_fric:.2f} м, h_м={h_loc:.2f} м, "
                           f"P_изб={p_excess:.2f} м\n")

            report += "\nМестные сопротивления по сегментам:\n"
            for seg_idx, sources in result.get('local_sources_by_segment', {}).items():
                if not sources:
                    continue
                report += f"  Сегмент {seg_idx}:\n"
                for src in sources:
                    if src['type'] == 'поворот':
                        report += (f"    - Поворот: угол={src['angle_deg']:.1f}°, "
                                   f"R/D={src['r_over_d']:.2f}, ζ={src['zeta']:.3f}, "
                                   f"h={src['head_loss']:.3f} м\n")
                    else:
                        report += (f"    - {src['obj_type']}: ζ={src['zeta']:.2f}, "
                                   f"h={src['head_loss']:.3f} м\n")
            self.hydraulic_result_text.setPlainText(report)
            self._log("Гидравлический расчёт завершён.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка расчёта: {e}")

    @timed
    def _update_circles_table(self, roles=None):
        self.circles_table.setRowCount(0)
        selected_idx = self.poly_combo.currentData() if self.poly_combo.count() > 0 else None
        if selected_idx is None or selected_idx >= len(self.polylines):
            return
        selected_poly = self.polylines[selected_idx]
        for i, circle in enumerate(self.circles):
            min_dist, _, _, _ = closest_point_on_polyline(circle['center'][:2], selected_poly)
            if min_dist >= self._get_snap_tolerance():
                continue
            row = self.circles_table.rowCount()
            self.circles_table.insertRow(row)
            item = QTableWidgetItem(f"O{i+1}")
            item.setData(Qt.UserRole, i)
            self.circles_table.setItem(row, 0, item)
            combo = QComboBox()
            combo.addItem("")  # пустой тип (не задан)
            combo.addItems(['всас', 'выпуск', 'обратный клапан', 'задвижка', 'переход диаметров'])
            combo.setCurrentIndex(0)   # по умолчанию пустой
            combo.currentIndexChanged.connect(lambda _idx, row=row, combo=combo: self._update_zeta_from_type(row, combo))
            self.circles_table.setCellWidget(row, 1, combo)
            zeta_item = QTableWidgetItem()
            zeta_item.setText(str(get_zeta_for_type(combo.currentText())))
            self.circles_table.setItem(row, 2, zeta_item)
            # Добавляем поле напора для всаса/выпуска
            nap_item = QTableWidgetItem()
            if combo.currentText() == 'всас':
                nap_item.setText("0")   # по умолчанию 0 м подпора
            elif combo.currentText() == 'выпуск':
                nap_item.setText("1")   # по умолчанию 1 м свободный напор
            else:
                nap_item.setText("")    # для других объектов не используется
            self.circles_table.setItem(row, 3, nap_item)
        if roles:
            self._apply_roles_to_table(roles)

    def _update_zeta_from_type(self, row, combo):
        type_text = combo.currentText()
        if type_text == "":
            # очистить ζ
            zeta_item = self.circles_table.item(row, 2)
            if zeta_item is not None:
                zeta_item.setText("")
            return
        if type_text == 'переход диаметров':
            obj_item = self.circles_table.item(row, 0)
            circle_idx = obj_item.data(Qt.UserRole)
            if circle_idx is not None:
                circle = self.circles[circle_idx]
                nearby_count = 0
                for poly in self.polylines:
                    dist, _, _, _ = closest_point_on_polyline(circle['center'][:2], poly)
                    if dist < circle['radius'] * 2:
                        nearby_count += 1
                if nearby_count < 2:
                    QMessageBox.warning(self, "Ошибка",
                                        "Переход диаметров должен соединять две полилинии")
                    combo.blockSignals(True)
                    combo.setCurrentIndex(0)
                    combo.blockSignals(False)
                    return
        zeta = get_zeta_for_type(type_text)
        zeta_item = self.circles_table.item(row, 2)
        if zeta_item is not None:
            zeta_item.setText(str(zeta))

    def _toggle_velocity_input(self, checked):
        self.velocity_edit.setEnabled(checked)
        self.flow_edit.setEnabled(not checked)
        self.flow_unit_combo.setEnabled(not checked)

    def _get_snap_tolerance(self):
        try:
            tol = float(self.snap_tol_edit.text().replace(',', '.'))
            if tol <= 0:
                raise ValueError
            return tol
        except:
            QMessageBox.warning(self, "Ошибка", "Допуск привязки должен быть положительным числом")
            return 0.5  # возвращаем значение по умолчанию

    def _on_tolerance_changed(self):
        self._update_polyline_display()
        self._update_circles_table()

    def _log(self, msg):
        self.parent_app._log(msg)

    def _on_data_load_finished(self, success):
        self.is_loading = False
        if not success:
            QMessageBox.warning(self, "Ошибка", "Сбой при загрузке данных")    
    
    def _on_show_all_toggled(self):
        self._update_polyline_display()

    def _on_pipe_type_changed(self):
        type_name = self.pipe_type_combo.currentText()
        catalog = self.pipe_catalog.get(type_name)
        if not catalog:
            return

        # Обновляем номинальное давление
        pn_default = catalog.get("pn_mpa")
        if pn_default is not None:
            self._default_pn = pn_default
            self.pn_edit.setText(str(pn_default))
            self.pn_default_label.setText(f"по умолч.: {pn_default} МПа")

        self.outer_d_combo.blockSignals(True)
        self.outer_d_combo.clear()
        self.wall_combo.blockSignals(True)
        self.wall_combo.clear()
        self.rough_combo.blockSignals(True)
        self.rough_combo.clear()
        self.r_min_combo.blockSignals(True)
        self.r_min_combo.clear()

        if catalog["sdr"] is not None:
            # ПЭ трубы
            for d in catalog["diameters"]:
                self.outer_d_combo.addItem(f"{d*1000:.0f} мм", d)
            self.outer_d_combo.setCurrentIndex(0)
            self._update_pe_options(catalog["sdr"], catalog["rough"], catalog["r_min_coeff"])
        else:
            # Стальные трубы
            for opt in catalog["options"]:
                self.outer_d_combo.addItem(f"{opt['d']*1000:.0f} мм", opt['d'])
            self.outer_d_combo.setCurrentIndex(0)
            self._update_steel_options(catalog["options"])

        self.outer_d_combo.blockSignals(False)
        self.wall_combo.blockSignals(False)
        self.rough_combo.blockSignals(False)
        self.r_min_combo.blockSignals(False)

    def _on_outer_d_changed(self):
        type_name = self.pipe_type_combo.currentText()
        catalog = self.pipe_catalog.get(type_name)
        if catalog and catalog["sdr"] is not None:
            self._update_pe_options(catalog["sdr"], catalog["rough"], catalog["r_min_coeff"])
        else:
            self._update_steel_options(catalog["options"])

    def _update_pe_options(self, sdr, rough, r_min_coeff):
        d = self.outer_d_combo.currentData()
        if d is None:
            return
        t = d / sdr
        self.wall_combo.clear()
        self.wall_combo.addItem(f"{t*1000:.1f} мм", t)

        self.rough_combo.clear()
        self.rough_combo.addItem(f"{rough*1000:.3f} мм", rough)

        r_min = r_min_coeff * d
        self.r_min_combo.clear()
        self.r_min_combo.addItem(f"{r_min:.2f} м", r_min)

        self._default_params = {
            'outer_d': d,
            'wall': t,
            'rough': rough,
            'r_min': r_min,
        }
        self.outer_d_default_label.setText(f"по умолч.: {d*1000:.0f} мм")
        self.wall_default_label.setText(f"по умолч.: {t*1000:.1f} мм")
        self.rough_default_label.setText(f"по умолч.: {rough*1000:.3f} мм")
        self.r_min_default_label.setText(f"по умолч.: {r_min:.2f} м")

    def _update_steel_options(self, options):
        d = self.outer_d_combo.currentData()
        if d is None:
            return
        filtered = [o for o in options if abs(o["d"] - d) < 1e-9]
        self.wall_combo.clear()
        for o in filtered:
            self.wall_combo.addItem(f"{o['t']*1000:.1f} мм", o['t'])
        self.wall_combo.setEnabled(True)   # разрешаем ручной ввод всегда
        if filtered:
            self.wall_combo.setCurrentIndex(0)
            self._update_steel_finish(d, filtered)
        else:
            self.rough_combo.clear()
            self.r_min_combo.clear()

    def _update_steel_finish(self, d, filtered):
        t = self.wall_combo.currentData()
        if d is None or t is None:
            return
        for o in filtered:
            if abs(o["d"] - d) < 1e-9 and abs(o["t"] - t) < 1e-9:
                self.rough_combo.clear()
                self.rough_combo.addItem(f"{o['rough']*1000:.3f} мм", o['rough'])
                self.r_min_combo.clear()
                self.r_min_combo.addItem(f"{o['r_min']:.2f} м", o['r_min'])

                self._default_params = {
                    'outer_d': o['d'],
                    'wall': o['t'],
                    'rough': o['rough'],
                    'r_min': o['r_min'],
                }
                self.outer_d_default_label.setText(f"по умолч.: {o['d']*1000:.0f} мм")
                self.wall_default_label.setText(f"по умолч.: {o['t']*1000:.1f} мм")
                self.rough_default_label.setText(f"по умолч.: {o['rough']*1000:.3f} мм")
                self.r_min_default_label.setText(f"по умолч.: {o['r_min']:.2f} м")
                break
    
    def _on_wall_changed(self):
        type_name = self.pipe_type_combo.currentText()
        catalog = self.pipe_catalog.get(type_name)
        if not catalog:
            return
        if catalog["sdr"] is None:  # стальные трубы
            d = self.outer_d_combo.currentData()
            if d is not None:
                filtered = [o for o in catalog["options"] if abs(o["d"] - d) < 1e-9]
                self._update_steel_finish(d, filtered)

    def _get_combo_value(self, combo):
        """Возвращает числовое значение из QComboBox, поддерживая ручной ввод."""
        data = combo.currentData()
        if data is not None:
            try:
                return float(data)
            except (ValueError, TypeError):
                pass
        text = combo.currentText().strip().replace(',', '.')
        try:
            return float(text)
        except ValueError:
            return None

    def _set_combo_value(self, combo, value):
        """Устанавливает значение в QComboBox, сохраняя ручной ввод."""
        combo.blockSignals(True)
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(str(value))
        combo.blockSignals(False)

    def _reset_outer_d(self):
        d = self._default_params.get('outer_d')
        if d is not None:
            self._set_combo_value(self.outer_d_combo, d)
            self._on_outer_d_changed()

    def _reset_wall(self):
        w = self._default_params.get('wall')
        if w is not None:
            self._set_combo_value(self.wall_combo, w)
            self._on_wall_changed()

    def _reset_rough(self):
        r = self._default_params.get('rough')
        if r is not None:
            self._set_combo_value(self.rough_combo, r)

    def _reset_r_min(self):
        r = self._default_params.get('r_min')
        if r is not None:
            self._set_combo_value(self.r_min_combo, r)

    def _on_flow_unit_changed(self):
        new_coeff = self.flow_unit_combo.currentData()
        if new_coeff is None:
            return
        try:
            old_value = float(self.flow_edit.text().replace(',', '.'))
        except ValueError:
            old_value = None

        if old_value is not None:
            current_m3s = old_value * self._flow_unit_coeff
            new_value = current_m3s / new_coeff
            self.flow_edit.setText(f"{new_value:.6f}")
        self._flow_unit_coeff = new_coeff
    
    def _reset_pn(self):
        if self._default_pn is not None:
            self.pn_edit.setText(str(self._default_pn))

    def _update_loss_thresholds(self):
        """Считывает пороги потерь из полей и обновляет атрибуты."""
        yellow = parse_float(self.loss_yellow_edit.text(), self.loss_yellow_threshold)
        if yellow is not None:
            self.loss_yellow_threshold = yellow
        if self.last_hydraulic_result is not None:
            self._update_polyline_display()
    
    def _update_pressure_histogram(self):
        """Строит гистограмму избыточного давления по узлам."""
        if not hasattr(self, 'pressure_hist_canvas') or self.last_hydraulic_result is None:
            return
        ax = self.pressure_hist_canvas.ax
        ax.clear()
        result = self.last_hydraulic_result
        points = result['points']
        heads = result['station_heads']
        dists = result['station_distances']

        p_excess = [heads[i] - points[i][2] for i in range(len(points))]

        ax.bar(dists, p_excess, width=max(1.0, (dists[-1] - dists[0]) / len(dists) * 0.8))
        ax.set_xlabel('Расстояние от начала, м')
        ax.set_ylabel('Избыточное давление, м')
        ax.set_title('Эпюра избыточного давления')
        ax.grid(True, linestyle='--', alpha=0.3)
        self.pressure_hist_canvas.draw()
        if hasattr(self, 'plan_canvas') and self.last_hydraulic_result is not None:
            self._on_plan_xlim_changed(self.plan_canvas.ax)
            
    def _get_pn_head(self):
        """Текущее PN в метрах водяного столба."""
        try:
            pn_mpa = parse_float(self.pn_edit.text(), 1.0)
            return pn_mpa * 1e6 / (1000 * 9.81)
        except:
            return 101.94  # PN10

    def _save_roles_to_file(self):
        """Сохраняет роли объектов текущей полилинии в JSON."""
        if self.circles_table.rowCount() == 0:
            QMessageBox.information(self, "Сохранение", "Нет объектов для сохранения")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить роли объектов", "", "JSON (*.json)")
        if not path:
            return
        roles = []
        for row in range(self.circles_table.rowCount()):
            obj_item = self.circles_table.item(row, 0)
            circle_idx = obj_item.data(Qt.UserRole)
            combo = self.circles_table.cellWidget(row, 1)
            obj_type = combo.currentText() if combo else ""
            zeta_item = self.circles_table.item(row, 2)
            zeta = zeta_item.text() if zeta_item else ""
            nap_item = self.circles_table.item(row, 3)
            nap = nap_item.text() if nap_item else ""
            roles.append({
                "circle_index": circle_idx,
                "type": obj_type,
                "zeta": zeta,
                "head": nap,
            })
        data = {
            "polyline_index": self.poly_combo.currentData(),
            "polyline_name": self.poly_combo.currentText(),
            "roles": roles,
        }
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"Роли объектов сохранены в {path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл: {e}")

    def _save_params_to_file(self):
        """Сохраняет параметры текущего водовода в JSON."""
        try:
            data = {
                "polyline_index": self.poly_combo.currentData(),
                "polyline_name": self.poly_combo.currentText(),
                "pipe_type": self.pipe_type_combo.currentText(),
                "outer_diameter": self.outer_d_combo.currentText(),
                "wall_thickness": self.wall_combo.currentText(),
                "roughness": self.rough_combo.currentText(),
                "r_min": self.r_min_combo.currentText(),
                "flow_mode": "velocity" if self.use_velocity_checkbox.isChecked() else "flow",
                "flow_value": self.flow_edit.text(),
                "flow_unit": self.flow_unit_combo.currentText(),
                "velocity_value": self.velocity_edit.text(),
                "temperature": self.temp_edit.text(),
                "min_angle": self.min_angle_edit.text(),
                "pn_mpa": self.pn_edit.text(),
                "loss_yellow": self.loss_yellow_edit.text(),
            }
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить параметры водовода", "", "JSON (*.json)")
            if not path:
                return
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._log(f"Параметры сохранены в {path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл: {e}")

    def _get_current_params(self):
        return {
            'pipe_type': self.pipe_type_combo.currentText(),
            'outer_d': self.outer_d_combo.currentData(),   # числовое значение
            'wall': self.wall_combo.currentData(),
            'rough': self.rough_combo.currentData(),
            'r_min': self.r_min_combo.currentData(),
            'flow_mode': 'velocity' if self.use_velocity_checkbox.isChecked() else 'flow',
            'flow_value': self.flow_edit.text(),
            'flow_unit': self.flow_unit_combo.currentText(),
            'velocity_value': self.velocity_edit.text(),
            'temp': self.temp_edit.text(),
            'min_angle': self.min_angle_edit.text(),
            'pn_mpa': self.pn_edit.text(),
            'loss_yellow': self.loss_yellow_edit.text(),
            'min_pressure': self.min_pressure_excess,
        }

    def _apply_params(self, params):
        """Применяет сохранённые параметры к виджетам."""
        if not params:
            return
        # Устанавливаем тип трубы (и блокируем сигналы, чтобы не перезаполнять комбобоксы лишний раз)
        idx = self.pipe_type_combo.findText(params.get('pipe_type', ''))
        if idx >= 0:
            self.pipe_type_combo.blockSignals(True)
            self.pipe_type_combo.setCurrentIndex(idx)
            self.pipe_type_combo.blockSignals(False)
            self._on_pipe_type_changed()
        # Остальные параметры
        if 'outer_d' in params:
            self._set_combo_value(self.outer_d_combo, params['outer_d'])
        if 'wall' in params:
            self._set_combo_value(self.wall_combo, params['wall'])
        if 'rough' in params:
            self._set_combo_value(self.rough_combo, params['rough'])
        if 'r_min' in params:
            self._set_combo_value(self.r_min_combo, params['r_min'])       
        if 'flow_value' in params:
            self.flow_edit.setText(params['flow_value'])
        if 'flow_unit' in params:
            unit_idx = self.flow_unit_combo.findText(params['flow_unit'])
            if unit_idx >= 0:
                self.flow_unit_combo.blockSignals(True)
                self.flow_unit_combo.setCurrentIndex(unit_idx)
                self.flow_unit_combo.blockSignals(False)
        self.use_velocity_checkbox.blockSignals(True)
        self.use_velocity_checkbox.setChecked(params.get('flow_mode') == 'velocity')
        self.use_velocity_checkbox.blockSignals(False)
        if 'velocity_value' in params:
            self.velocity_edit.setText(params['velocity_value'])
        if 'temp' in params:
            self.temp_edit.setText(params['temp'])
        if 'min_angle' in params:
            self.min_angle_edit.setText(params['min_angle'])
        if 'pn_mpa' in params:
            self.pn_edit.setText(params['pn_mpa'])
        if 'loss_yellow' in params:
            self.loss_yellow_edit.setText(params['loss_yellow'])
        if 'min_pressure' in params:
            self.min_pressure_excess = params['min_pressure']
            if hasattr(self, 'min_pressure_edit'):
                self.min_pressure_edit.setText(str(params['min_pressure']))
        self._toggle_velocity_input(self.use_velocity_checkbox.isChecked())
        self._update_loss_thresholds()

    def _get_current_roles(self):
        """Возвращает список ролей объектов для текущей таблицы."""
        roles = []
        for row in range(self.circles_table.rowCount()):
            obj_item = self.circles_table.item(row, 0)
            circle_idx = obj_item.data(Qt.UserRole)
            combo = self.circles_table.cellWidget(row, 1)
            obj_type = combo.currentText() if combo else ""
            zeta_item = self.circles_table.item(row, 2)
            zeta = zeta_item.text() if zeta_item else ""
            nap_item = self.circles_table.item(row, 3)
            nap = nap_item.text() if nap_item else ""
            roles.append({
                "circle_index": circle_idx,
                "type": obj_type,
                "zeta": zeta,
                "head": nap,
            })
        return roles

    def _apply_roles_to_table(self, roles):
        """Применяет сохранённые роли к уже заполненной таблице."""
        if not roles:
            return
        for row in range(self.circles_table.rowCount()):
            obj_item = self.circles_table.item(row, 0)
            circle_idx = obj_item.data(Qt.UserRole)
            for role in roles:
                if role['circle_index'] == circle_idx:
                    combo = self.circles_table.cellWidget(row, 1)
                    combo.blockSignals(True)
                    type_idx = combo.findText(role['type'])
                    if type_idx >= 0:
                        combo.setCurrentIndex(type_idx)
                    combo.blockSignals(False)
                    zeta_item = self.circles_table.item(row, 2)
                    if zeta_item:
                        if role.get('zeta'):
                            zeta_item.setText(role['zeta'])
                        else:
                            self._update_zeta_from_type(row, combo)
                    nap_item = self.circles_table.item(row, 3)
                    if nap_item:
                        nap_item.setText(role.get('head', ''))
                    break

    def _on_poly_selected(self):
        # Сохраняем данные предыдущей полилинии
        prev_idx = getattr(self, '_current_poly_index', None)
        if prev_idx is not None and prev_idx < len(self.polylines):
            self.polyline_data[prev_idx] = {
                'params': self._get_current_params(),
                'roles': self._get_current_roles(),
            }
        # Загружаем данные новой
        new_idx = self.poly_combo.currentData()
        self._current_poly_index = new_idx

        self.last_hydraulic_result = None
        self.last_result_poly_index = None

        data = self.polyline_data.get(new_idx)
        if data:
            self._apply_params(data.get('params', {}))
            # таблицу заполним после обновления, с ролями
            self._update_circles_table(roles=data.get('roles'))
        else:
            # При первом переключении или отсутствии данных – создаём запись с текущими параметрами
            self.polyline_data[new_idx] = {
                'params': self._get_current_params(),
                'roles': []
            }
            self._update_circles_table()

        self._update_polyline_display()
        if hasattr(self, 'pressure_hist_canvas'):
            self.pressure_hist_canvas.ax.clear()
            self.pressure_hist_canvas.draw()

    def _save_project_to_file(self):
        """Сохраняет параметры и роли всех полилиний в JSON."""
        try:
            # Сохраняем данные текущей полилинии перед экспортом
            if self._current_poly_index is not None:
                self.polyline_data[self._current_poly_index] = {
                    'params': self._get_current_params(),
                    'roles': self._get_current_roles()
                }
            project = {
                'polylines': []
            }
            valid_keys = [k for k in self.polyline_data.keys() if k is not None]
            for idx in sorted(valid_keys):
                entry = self.polyline_data[idx]
                project['polylines'].append({
                    'index': idx,
                    'name': self.poly_combo.itemText(idx) if idx < self.poly_combo.count() else f"P{idx+1}",
                    'params': entry['params'],
                    'roles': entry['roles']
                })
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить проект", "", "JSON (*.json)")
            if not path:
                return
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(project, f, ensure_ascii=False, indent=2)
            self._log(f"Проект сохранён в {path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить проект: {e}")

    def _load_project_from_file(self):
        """Загружает параметры и роли всех полилиний из JSON, сохраняя текущую выбранную."""
        if not self.polylines:
            QMessageBox.information(self, "Загрузка", "Сначала загрузите DXF с полилиниями")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить проект", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                project = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать файл: {e}")
            return

        polylines_data = project.get('polylines', [])
        if len(polylines_data) != len(self.polylines):
            QMessageBox.warning(self, "Ошибка",
                                f"Количество полилиний в проекте ({len(polylines_data)}) "
                                f"не совпадает с текущим ({len(self.polylines)}).")
            return

        # Запоминаем текущую выбранную полилинию
        current_idx = getattr(self, '_current_poly_index', 0)
        if current_idx is None or current_idx < 0 or current_idx >= len(self.polylines):
            current_idx = 0

        # Заполняем polyline_data из файла
        self.polyline_data = {}
        for entry in polylines_data:
            idx = entry.get('index')
            if idx is not None and 0 <= idx < len(self.polylines):
                self.polyline_data[idx] = {
                    'params': entry.get('params', {}),
                    'roles': entry.get('roles', [])
                }

        # Устанавливаем комбобокс на сохранённую полилинию, не вызывая _on_poly_selected
        self.poly_combo.blockSignals(True)
        self.poly_combo.setCurrentIndex(current_idx)
        self.poly_combo.blockSignals(False)

        self._current_poly_index = current_idx

        # Применяем параметры и роли для выбранной полилинии
        if current_idx in self.polyline_data:
            self._apply_params(self.polyline_data[current_idx]['params'])
            self._update_circles_table(roles=self.polyline_data[current_idx]['roles'])
            self._update_polyline_display()
            if hasattr(self, 'pressure_hist_canvas'):
                self.pressure_hist_canvas.ax.clear()
                self.pressure_hist_canvas.draw()
            self._log(f"Проект загружен, выбрана полилиния {current_idx+1}")
        else:
            # Если данных для выбранной нет (например, пустой проект) – создать пустые
            self._apply_params({})
            self._update_circles_table()
            self._update_polyline_display()
            self._log("Проект загружен, но данные для выбранной полилинии отсутствуют")

    def _get_segment_color(self, seg, pn_head):
        """Цвет сегмента:
           - красный: превышение PN
           - фиолетовый: вакуум (p < 0)
           - оранжевый: низкий запас (0 <= p < min_pressure_excess)
           - жёлтый: большие местные потери
           - зелёный: норма
        """
        try:
            idx_start = seg['index']
            idx_end = idx_start + 1
            points = self.last_hydraulic_result['points']
            heads = self.last_hydraulic_result['station_heads']
            z_start = points[idx_start][2]
            z_end = points[idx_end][2]
            p_start = heads[idx_start] - z_start
            p_end = heads[idx_end] - z_end

            # Превышение PN
            if p_start > pn_head or p_end > pn_head:
                return 'red'

            # Вакуум (отрицательное избыточное давление)
            if p_start < 0 or p_end < 0:
                return 'purple'

            # Низкий запас (меньше min_pressure_excess)
            if p_start < self.min_pressure_excess or p_end < self.min_pressure_excess:
                return 'orange'
        except (KeyError, IndexError):
            pass

        # Большие местные потери (удельные)
        if seg['length'] > 0:
            specific_local_loss = seg['local_loss'] / seg['length']
            if specific_local_loss >= self.loss_yellow_threshold:
                return 'yellow'

        return 'green'

    def _update_min_pressure_excess(self):
        """Считывает значение минимального запаса над трубой и обновляет атрибут."""
        value = parse_float(self.min_pressure_edit.text(), self.min_pressure_excess)
        if value is not None:
            self.min_pressure_excess = value
        if self.last_hydraulic_result is not None:
            self._update_polyline_display()

    def _get_segment_label(self, seg, pn_head):
        """Возвращает (текст, цвет) для подписи на сегменте профиля."""
        try:
            idx_start = seg['index']
            idx_end = idx_start + 1
            points = self.last_hydraulic_result['points']
            heads = self.last_hydraulic_result['station_heads']
            z_start = points[idx_start][2]
            z_end = points[idx_end][2]
            p_start = heads[idx_start] - z_start
            p_end = heads[idx_end] - z_end

            # Превышение PN
            if p_start > pn_head or p_end > pn_head:
                # Берём максимальное превышение
                over_pn = max(p_start, p_end) - pn_head
                return f"↑PN {over_pn:.1f} м", 'red'

            # Вакуум (отрицательное избыточное давление)
            if p_start < 0 or p_end < 0:
                vacuum = abs(min(p_start, p_end))
                return f"↓вак {vacuum:.1f} м", 'purple'

            # Низкий запас
            if p_start < self.min_pressure_excess or p_end < self.min_pressure_excess:
                reserve = min(p_start, p_end)
                return f"запас {reserve:.1f} м", 'orange'

            # Большие местные потери (удельные)
            if seg['length'] > 0:
                specific_local_loss = seg['local_loss'] / seg['length']
                if specific_local_loss >= self.loss_yellow_threshold:
                    return f"мест. {seg['local_loss']:.1f} м", 'black'

            # Нормальный участок – показываем запас давления
            reserve = min(p_start, p_end)
            return f"запас {reserve:.1f} м", 'green'
        except (KeyError, IndexError):
            pass
        return "", 'gray'

    def _setup_zoom_sync(self):
        """Настраивает синхронизацию масштабов между планом, профилем и эпюрой."""
        # Используем событие xlim_changed оси плана
        self.plan_canvas.ax.callbacks.connect('xlim_changed', self._on_plan_xlim_changed)

    def _on_plan_xlim_changed(self, ax):
        """Обрабатывает изменение пределов оси X плана и синхронизирует другие графики."""
        if self._updating_zoom_sync:
            return
        self._updating_zoom_sync = True
        try:
            if not self.polylines or self.last_hydraulic_result is None:
                return

            selected_idx = self.poly_combo.currentData()
            if selected_idx is None or selected_idx < 0:
                return

            poly = self.polylines[selected_idx]
            result = self.last_hydraulic_result

            # Получаем текущие пределы X плана
            x_min, x_max = ax.get_xlim()

            # Проходим по точкам полилинии, находим те, что попадают в окно
            dists = result['station_distances']  # расстояния от начала до каждой точки
            points = result['points']

            selected_dists = []
            for d, (x, y, z) in zip(dists, points):
                if x_min <= x <= x_max:
                    selected_dists.append(d)

            if not selected_dists:
                return

            min_dist = min(selected_dists)
            max_dist = max(selected_dists)

            # Применяем новые пределы к профилю
            self.profile_canvas.ax.set_xlim(min_dist, max_dist)

            # Применяем к эпюре избыточного давления
            self.pressure_hist_canvas.ax.set_xlim(min_dist, max_dist)

            # Перерисовываем графики
            self.profile_canvas.draw_idle()
            self.pressure_hist_canvas.draw_idle()
        finally:
            self._updating_zoom_sync = False