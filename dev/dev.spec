# -*- mode: python ; coding: utf-8 -*-
"""开发版运行时。

只把 Python 解释器和第三方依赖冻结进 JevChat-Dev.exe + _internal；
main.py / app / core 不进包，发布后从 exe 同目录读取，便于直接替换调试。
"""
import os
from PyInstaller.utils.hooks import collect_all

hiddenimports = [
    "numpy", "cv2", "PIL", "yaml", "pyclipper", "shapely",
    "win32api", "win32con", "win32gui", "win32clipboard", "win32process",
]

datas, binaries = [], []
for pkg in (
    "rapidocr_onnxruntime",
    "onnxruntime",
    "qfluentwidgets",
    "windows_capture",
    "openai",
    "typesafe_sdk",
    "anthropic",
    "google.genai",
    "certifi",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

excludes = [
    "tkinter", "matplotlib", "scipy", "pandas",
] + ["PySide6." + m for m in (
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel",
    "QtMultimedia", "QtMultimediaWidgets", "QtCharts", "QtDataVisualization",
    "QtQuick", "QtQuick3D", "QtQuickControls2", "QtQuickWidgets", "QtQuickTest", "QtQml",
    "QtPdf", "QtPdfWidgets", "QtBluetooth", "QtNfc", "QtSensors", "QtSerialPort",
    "QtTest", "QtDesigner", "QtHelp", "QtRemoteObjects", "QtScxml", "QtStateMachine",
    "QtTextToSpeech", "QtPositioning", "QtLocation", "QtSql",
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
)]

a = Analysis(
    ["DevBootstrap.py"],
    pathex=["dev"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JevChat-Dev",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.abspath("docs/icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JevChat-Windows-Dev",
)
