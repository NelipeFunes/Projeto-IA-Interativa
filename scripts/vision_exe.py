"""Ponto de entrada do Vision.exe empacotado (PyInstaller). Ver vision/empacotado.py."""

import sys

from vision.empacotado import principal

if __name__ == "__main__":
    sys.exit(principal())
