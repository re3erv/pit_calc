# visualization.py
import sys
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget,
                             QTabWidget, QFileDialog, QMessageBox)
from PyQt5.QtCore import Qt
from widgets import DataLoaderThread, HeightSearchThread
from core.height_search import VolumeCache
from tabs.volume_tab import VolumeTab
from tabs.hydraulic_tab import HydraulicTab


class VolumeCalculatorApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Расчет объемов выемки")
        self.setGeometry(100, 100, 1200, 800)

        self.current_colorbar = None
        self.current_models = []

        self._init_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.volume_tab = VolumeTab(self)
        self.hydraulic_tab = HydraulicTab(self)

        self.tabs.addTab(self.volume_tab, "Объемы и отметки карьера")
        self.tabs.addTab(self.hydraulic_tab, "Производительность и напоры водоотлива")

        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.tabs.setCurrentIndex(0)

    def _on_tab_changed(self, index):
        if index == 1:  # вкладка «Гидравлика»
            self.hydraulic_tab.on_tab_activated()
            
    def _log(self, msg):
        print(msg)   # просто печать в консоль, без обращения к info_text