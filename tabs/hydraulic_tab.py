# tabs/hydraulic_tab.py
import os
import math
import numpy as np
import matplotlib
matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
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
        self.profile_canvas = MplCanvas(self, width=5, height=4)
        left_layout.addWidget(QLabel("План"))
        left_layout.addWidget(self.plan_canvas)
        left_layout.addWidget(QLabel("Продольный профиль"))
        left_layout.addWidget(self.profile_canvas)
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

        # Допуск привязки кругов
        snap_layout = QHBoxLayout()
        snap_layout.addWidget(QLabel("Допуск привязки, м:"))
        self.snap_tol_edit = QLineEdit("0.5")
        self.snap_tol_edit.setFixedWidth(80)
        self.snap_tol_edit.editingFinished.connect(self._on_tolerance_changed)
        snap_layout.addWidget(self.snap_tol_edit)
        snap_layout.addStretch()
        right_layout.addLayout(snap_layout)

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

        right_layout.addLayout(pipe_grid)

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

    def _on_file_changed_from_hydraulic(self):
        path = self.hydraulic_file_edit.text().strip()
        if os.path.exists(path):
            self.dxf_file = path
            self._load_data()

    def _on_data_loaded(self, polylines, circles):
        self.polylines = polylines
        self.circles = circles
        self.update_after_load()
        self.on_tab_activated()   # если вкладка активна, отрисовать

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
            selected_idx = self.poly_combo.currentData()
            if selected_idx is None or selected_idx < 0 or selected_idx >= len(self.polylines):
                return

            show_all = self.show_all_checkbox.isChecked()
            has_result = (self.last_hydraulic_result is not None and
                          self.last_result_poly_index == selected_idx)

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
                            color = self._get_loss_color(seg['total_loss'], max_loss)
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
                        color = self._get_loss_color(seg['total_loss'], max_loss)
                        ax_plan.plot([x0, x1], [y0, y1], color=color, linewidth=3,
                                    solid_capstyle='round')
                else:
                    ax_plan.plot(x_vals, y_vals, 'o-', color='red', markersize=3,
                                linewidth=2, label=f'P{selected_idx+1}')
                # Подписи узлов остаются
                step = max(1, len(poly) // 20)
                for i in range(0, len(poly), step):
                    ax_plan.annotate(f'{i}', (x_vals[i], y_vals[i]),
                                    textcoords="offset points", xytext=(0,5),
                                    ha='center', fontsize=8)

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
            ax_plan.axis('equal')
            ax_plan.grid(True, linestyle='--', alpha=0.3)
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
                max_loss = max(seg['total_loss'] for seg in segments) if segments else 1e-9
                for seg in segments:
                    i = seg['index']
                    d0, d1 = dists[i], dists[i+1]
                    z0, z1 = z_vals[i], z_vals[i+1]
                    color = self._get_loss_color(seg['total_loss'], max_loss)
                    ax_prof.plot([d0, d1], [z0, z1], color=color, linewidth=2)
                    # Подпись потерь на значимых участках
                    if seg['total_loss'] > 0.3 * max_loss:
                        ax_prof.text((d0+d1)/2, (z0+z1)/2,
                                    f"{seg['total_loss']:.2f} м",
                                    fontsize=7, color='red', ha='center', va='bottom')
            else:
                ax_prof.plot(dists, z_vals, 'o-', color='blue', markersize=3)
            # Подписи узлов (можно оставить, чтобы не путать с подписями потерь)
            for i in range(0, len(poly), step):
                ax_prof.annotate(f'{i}', (dists[i], z_vals[i]),
                                textcoords="offset points", xytext=(0,5), ha='center', fontsize=8)
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

            p_in_head = None
            p_out_head = None
            fittings = []
            for row in range(self.circles_table.rowCount()):
                obj_item = self.circles_table.item(row, 0)
                if obj_item is None:
                    continue
                circle_idx = obj_item.data(Qt.UserRole)
                if circle_idx is None or circle_idx >= len(self.circles):
                    continue
                combo = self.circles_table.cellWidget(row, 1)
                obj_type = combo.currentText() if combo else 'всас'
                if obj_type == 'всас':
                    nap_item = self.circles_table.item(row, 3)
                    if nap_item and nap_item.text().strip():
                        try:
                            p_in_head = float(nap_item.text().replace(',', '.'))
                        except ValueError:
                            p_in_head = 0.0
                elif obj_type == 'выпуск':
                    nap_item = self.circles_table.item(row, 3)
                    if nap_item and nap_item.text().strip():
                        try:
                            p_out_head = float(nap_item.text().replace(',', '.'))
                        except ValueError:
                            p_out_head = 1.0                
                zeta_item = self.circles_table.item(row, 2)
                try:
                    zeta = float(zeta_item.text().replace(',', '.'))
                except:
                    zeta = get_zeta_for_type(obj_type)
                circle = self.circles[circle_idx]
                min_dist, _, seg_idx, t = closest_point_on_polyline(circle['center'][:2], poly)
                if min_dist < circle['radius'] * 2:
                    station = seg_idx + 1 if t > 0.5 else seg_idx
                    fittings.append({'station': station, 'k': zeta})

            if p_in_head is None:
                p_in_head = 0.0
            if p_out_head is None:
                p_out_head = 1.0

            result = calculate_pipeline(poly, pipe_params, flow, fluid_temp=temp,
                                        check_pn=check_pn, pn=pn,
                                        min_angle_deg=min_angle,
                                        fittings=fittings,
                                        p_in_head=p_in_head,
                                        p_out_head=p_out_head)

            self.last_hydraulic_result = result
            self.last_result_poly_index = idx
            self._update_polyline_display()   # добавить эту строку
            
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

            report += "\n--- Расчётные формулы ---\n"
            report += f"1) Расход и скорость:\n"
            report += f"   V = Q / (π·d²/4)\n"
            report += f"   d = {d:.6f} м, Q = {flow:.6f} м³/с\n"
            report += f"   V = {flow:.6f} / (3.1416 · {d:.6f}² / 4) = {V:.3f} м/с\n\n"
            report += f"2) Потери на трение (Дарси–Вейсбах):\n"
            report += f"   h_тр = λ · (L/d) · (V²/(2g))\n"
            report += f"   λ = {lambda_:.4f}, L = {L:.2f} м, d = {d:.6f} м, V = {V:.3f} м/с, g = 9.81 м/с²\n"
            report += f"   h_тр = {lambda_:.4f} · ({L:.2f}/{d:.6f}) · ({V:.3f}²/(2·9.81)) = {result['total_friction_loss']:.3f} м\n\n"
            report += f"3) Местные сопротивления:\n"
            report += f"   h_м = Σ ζ · (V²/(2g))\n"
            report += f"   Σ ζ = {result['total_zeta']:.3f}, V = {V:.3f} м/с\n"
            report += f"   h_м = {result['total_zeta']:.3f} · ({V:.3f}²/(2·9.81)) = {result['total_local_loss']:.3f} м\n\n"
            report += f"4) Полный требуемый напор насоса:\n"
            report += f"   H_треб = (Z_выход − Z_вход) + H_своб + h_тр + h_м\n"
            report += f"   Z_вход = {poly[0][2]:.2f} м, Z_выход = {poly[-1][2]:.2f} м, H_своб = {p_out_head:.2f} м\n"
            report += f"   h_тр = {result['total_friction_loss']:.3f} м, h_м = {result['total_local_loss']:.3f} м\n"
            report += f"   H_треб = ({poly[-1][2]:.2f} − {poly[0][2]:.2f}) + {p_out_head:.2f} + {result['total_friction_loss']:.3f} + {result['total_local_loss']:.3f} = {result['required_head']:.3f} м\n"
            report += "\n"

            report += f"Длина: {result['total_length']:.2f} м\n"
            report += f"Внутренний диаметр: {result['inner_diameter']*1000:.1f} мм\n"
            report += f"Скорость: {result['velocity']:.2f} м/с\n"
            report += f"Число Рейнольдса: {result['reynolds']:.0f}\n"
            report += f"Суммарные потери: {result['total_head_loss']:.2f} м\n"
            report += f"  - на трение по длине: {result.get('total_friction_loss', 0):.2f} м\n"
            report += f"  - местные: {result.get('total_local_loss', 0):.2f} м\n"
            report += f"Требуемый напор насоса: {result['required_head']:.2f} м\n\n"

            if result['warnings']:
                report += "Предупреждения:\n"
                for w in result['warnings']:
                    report += f"  - {w}\n"
            else:
                report += "Предупреждений нет.\n"

            report += "\nЭпюра напоров (узел, Z, напор, избыт. давление):\n"
            for i, (pt, head) in enumerate(zip(result['points'], result['station_heads'])):
                p_excess = head - pt[2]
                report += f"  {i}: Z={pt[2]:.2f} м, H={head:.2f} м, P_изб={p_excess:.2f} м\n"

            self.hydraulic_result_text.setPlainText(report)
            self._log("Гидравлический расчёт завершён.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Ошибка расчёта: {e}")

    @timed
    def _update_circles_table(self):
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
            combo.addItems(['всас', 'выпуск', 'обратный клапан', 'задвижка', 'переход диаметров'])
            combo.setCurrentIndex(0)
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

    def _update_zeta_from_type(self, row, combo):
        type_text = combo.currentText()
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

    def _get_loss_color(self, loss, max_loss):
        """Возвращает цвет для потерь относительно max_loss."""
        if max_loss <= 0:
            return 'green'
        ratio = loss / max_loss
        if ratio < 0.1:
            return 'green'
        elif ratio < 0.3:
            return 'yellow'
        else:
            return 'red'