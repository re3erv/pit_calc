import sys
from PyQt5.QtWidgets import QApplication
from visualization import VolumeCalculatorApp

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VolumeCalculatorApp()
    window.show()
    sys.exit(app.exec_())