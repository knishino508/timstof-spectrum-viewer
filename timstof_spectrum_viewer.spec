# -*- mode: python ; coding: utf-8 -*-
#
# timsTOF_viewer.spec
# PyInstaller用specファイル
#
# ビルド方法:
#   pyinstaller timsTOF_viewer.spec

import os, importlib

# opentims_bruker_bridge のフォルダを自動検出してdatasに追加
def _find_package_dir(pkg_name):
    spec = importlib.util.find_spec(pkg_name)
    if spec is None:
        raise RuntimeError(f"{pkg_name} が見つかりません。pip install してください。")
    return os.path.dirname(spec.origin)

datas = [
    (_find_package_dir('opentims_bruker_bridge'), 'opentims_bruker_bridge'),
]

block_cipher = None

a = Analysis(
    ['spectrum_viewer_dia_3.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # PyQt6関連
        'PyQt6.QtWidgets',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.sip',

        # pyqtgraph関連（動的インポートが多いため明示）
        'pyqtgraph',
        'pyqtgraph.graphicsItems',
        'pyqtgraph.graphicsItems.ViewBox',
        'pyqtgraph.graphicsItems.PlotItem',
        'pyqtgraph.graphicsItems.InfiniteLine',
        'pyqtgraph.graphicsItems.LinearRegionItem',
        'pyqtgraph.graphicsItems.TextItem',
        'pyqtgraph.Qt',
        'pyqtgraph.Qt.QtCore',
        'pyqtgraph.Qt.QtGui',
        'pyqtgraph.Qt.QtWidgets',

        # numpy
        'numpy',
        'numpy.core',

        # opentimspy関連
        'opentimspy',
        'opentims_bruker_bridge',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 不要なものを除外してサイズ削減
        'matplotlib',
        'tkinter',
        'scipy',
        'pandas',
        'PIL',
        'cv2',
        'IPython',
        'jupyter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

# ── フォルダ形式（推奨・起動が速い）──────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='timsTOF_Viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # コンソールウィンドウを非表示
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # アイコンがあれば 'icon.ico' を指定
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='timsTOF_Viewer',
)

# ── 単一ファイル形式（配布が楽だが起動が遅い）─────────────────────────
# 単一ファイルにしたい場合は上のexe/collをコメントアウトし、以下を有効化:
#
# exe = EXE(
#     pyz,
#     a.scripts,
#     a.binaries,
#     a.zipfiles,
#     a.datas,
#     [],
#     name='timsTOF_Viewer',
#     debug=False,
#     bootloader_ignore_signals=False,
#     strip=False,
#     upx=True,
#     console=False,
#     icon=None,
# )
