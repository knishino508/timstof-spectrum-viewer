# spectrum_viewer_dia_5.py
#
# 状態管理をシンプルに再設計:
#   current_frame_idx      : フレーム配列上のインデックス（TIC位置兼用）
#   current_type           : 'ms1' or 'ms2'
#   current_scan           : ms1: 0=ALL, 1..N=Scan番号
#                            ms2 統合モード: 1..N=Precursorインデックス（1始まり）
#                            ms2 生モード  : 1..M=現在PrecursorのScanオフセット（1始まり）
#   yellow_band            : (center, half_band) or None
#   ms2_raw_mode           : False=統合モード, True=生（Raw scan）モード
#   ms2_raw_precursor_idx  : 生モード中の現在Precursorインデックス（0始まり）
#
# キー操作:
#   →/←          : 次/前フレーム、scan=0(ms1) or 1(ms2)にリセット
#   Ctrl+→/←     : 次/前MS1フレームへ、scan=0
#   ↓/↑          : 次/前scan（端でフレームまたぎ）
#                   生モード時: Precursor内Scanを移動、末尾で次Precursorへ
#   Ctrl+↓/↑     : MS1 scanをスキップして次/前のMS2へ
#   ESC           : scan=0（ms1 ALLに戻す）

import sys
from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QSplitter, QStatusBar,
    QGroupBox, QCheckBox, QFrame, QSlider, QComboBox,
    QListWidget, QListWidgetItem, QLineEdit, QSizePolicy,
    QRadioButton, QButtonGroup, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QKeyEvent, QShortcut, QKeySequence
import pyqtgraph as pg


# ================================================================
#  Custom ViewBox
# ================================================================
class SpectrumViewBox(pg.ViewBox):
    def __init__(self, *args, **kwargs):
        self.reset_callback = kwargs.pop('reset_callback', None)
        super().__init__(*args, **kwargs)

    def mouseDoubleClickEvent(self, ev):
        if self.reset_callback is not None:
            self.reset_callback()
            ev.accept()
        else:
            super().mouseDoubleClickEvent(ev)

    def autoRange(self, padding=None, items=None, item=None):
        if self.reset_callback is not None:
            self.reset_callback()
        else:
            super().autoRange(padding=padding, items=items, item=item)


# ── Colour palette ─────────────────────────────────────────────────
C_MS1_ALL  = '#1565C0'
C_MS1_SCAN = '#E53935'
C_MS1_BG   = (180, 180, 180, 120)
C_MS2      = '#E65100'


# ================================================================
#  Settings Panel
# ================================================================
class SettingsPanel(QWidget):
    changed = pyqtSignal()

    CHK_STYLE = """
        QCheckBox::indicator {
            width: 14px; height: 14px;
            border: 2px solid #666; border-radius: 2px;
            background-color: white;
        }
        QCheckBox::indicator:unchecked:hover { border: 2px solid #1565C0; }
        QCheckBox::indicator:checked {
            border: 2px solid #1565C0; background-color: #1565C0;
        }
        QCheckBox::indicator:checked:hover {
            border: 2px solid #0d47a1; background-color: #0d47a1;
        }
    """

    def _make_checkbox(self, label, checked):
        chk = QCheckBox(label)
        chk.setChecked(checked)
        chk.setStyleSheet(self.CHK_STYLE)
        chk.stateChanged.connect(self.changed.emit)
        return chk

    def _make_slider(self, mn, mx, val, tick_interval, layout, on_change):
        sld = QSlider(Qt.Orientation.Horizontal)
        sld.setMinimum(mn); sld.setMaximum(mx); sld.setValue(val)
        sld.setTickPosition(QSlider.TickPosition.TicksBelow)
        sld.setTickInterval(tick_interval)
        sld.valueChanged.connect(on_change)
        layout.addWidget(sld)
        return sld

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #f5f5f5;")
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        title = QLabel("⚙ Settings")
        title.setStyleSheet("font-weight: bold; font-size: 13px; color: #333;")
        root.addWidget(title)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ccc;"); root.addWidget(sep)

        # MS1
        g1 = QGroupBox("MS1 Spectrum"); l1 = QVBoxLayout(g1)
        self.chk_ms1_bg = self._make_checkbox(
            "Grey background in Scan mode", settings.get('ms1_show_bg', True))
        self.chk_ms1_keep_scale = self._make_checkbox(
            "Keep X scale on frame change", settings.get('ms1_keep_scale', True))
        self.chk_ms1_avg_mode = self._make_checkbox(
            "Block scan mode (100 scans)", settings.get('ms1_avg_mode', False))
        l1.addWidget(self.chk_ms1_bg)
        l1.addWidget(self.chk_ms1_keep_scale)
        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("color: #ddd;"); l1.addWidget(sep1)
        l1.addWidget(self.chk_ms1_avg_mode)
        root.addWidget(g1)

        # MS2
        g2 = QGroupBox("MS2 Spectrum"); l2 = QVBoxLayout(g2)
        self.chk_ms2_keep_scale = self._make_checkbox(
            "Keep X/Y scale on frame change", settings.get('ms2_keep_scale', False))
        self.chk_accumulate_bands = self._make_checkbox(
            "Accumulate precursor bands", settings.get('accumulate_bands', False))
        self.chk_ms2_raw_mode = self._make_checkbox(
            "Raw scan mode", settings.get('ms2_raw_mode', False))
        l2.addWidget(self.chk_ms2_keep_scale)
        l2.addWidget(self.chk_accumulate_bands)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #ddd;"); l2.addWidget(sep2)
        l2.addWidget(self.chk_ms2_raw_mode)
        root.addWidget(g2)

        # Key speed
        g3 = QGroupBox("Key Hold Speed"); l3 = QVBoxLayout(g3)
        self.cmb_key_speed = QComboBox()
        for label, ms in [("Very Fast  (50ms)", 50), ("Fast       (100ms)", 100),
                           ("Normal     (200ms)", 200), ("Slow       (300ms)", 300)]:
            self.cmb_key_speed.addItem(label, ms)
        self.cmb_key_speed.setCurrentIndex(2)
        self.cmb_key_speed.currentIndexChanged.connect(self.changed.emit)
        l3.addWidget(self.cmb_key_speed); root.addWidget(g3)

        # Peak labels
        g4 = QGroupBox("Peak Labels (m/z)"); l4 = QVBoxLayout(g4)
        self.chk_labels = self._make_checkbox(
            "Show labels", settings.get('labels_enabled', True))
        l4.addWidget(self.chk_labels)

        def _row(text, val_text):
            h = QHBoxLayout(); h.addWidget(QLabel(text))
            lbl = QLabel(val_text)
            lbl.setStyleSheet("color: #1565C0; font-weight: bold;")
            h.addStretch(); h.addWidget(lbl); l4.addLayout(h)
            return lbl

        self.lbl_threshold = _row("Threshold:", f"{settings.get('label_threshold', 5)}%")
        self.sld_threshold = self._make_slider(
            0, 100, settings.get('label_threshold', 5), 10, l4,
            lambda v: (self.lbl_threshold.setText(f"{v}%"), self.changed.emit()))

        self.lbl_spacing = _row("Min spacing:", f"{settings.get('label_spacing', 1)}%")
        self.sld_spacing = self._make_slider(
            1, 20, settings.get('label_spacing', 1), 5, l4,
            lambda v: (self.lbl_spacing.setText(f"{v}%"), self.changed.emit()))

        self.lbl_max_labels = _row("Max labels:", f"{settings.get('label_max', 20)}")
        self.sld_max_labels = self._make_slider(
            1, 50, settings.get('label_max', 20), 10, l4,
            lambda v: (self.lbl_max_labels.setText(f"{v}"), self.changed.emit()))

        self.lbl_font_size = _row("Font size:", f"{settings.get('label_font_size', 7)}pt")
        self.sld_font_size = self._make_slider(
            6, 16, settings.get('label_font_size', 7), 2, l4,
            lambda v: (self.lbl_font_size.setText(f"{v}pt"), self.changed.emit()))

        root.addWidget(g4)
        root.addStretch()

    def get_settings(self) -> dict:
        return {
            'ms1_show_bg':     self.chk_ms1_bg.isChecked(),
            'ms1_keep_scale':  self.chk_ms1_keep_scale.isChecked(),
            'ms1_avg_mode':    self.chk_ms1_avg_mode.isChecked(),
            'ms2_keep_scale':  self.chk_ms2_keep_scale.isChecked(),
            'ms2_raw_mode':    self.chk_ms2_raw_mode.isChecked(),
            'accumulate_bands':self.chk_accumulate_bands.isChecked(),
            'key_interval':    self.cmb_key_speed.currentData(),
            'labels_enabled':  self.chk_labels.isChecked(),
            'label_threshold': self.sld_threshold.value(),
            'label_spacing':   self.sld_spacing.value(),
            'label_max':       self.sld_max_labels.value(),
            'label_font_size': self.sld_font_size.value(),
        }


# ================================================================
#  MS2 List Panel
# ================================================================
class _NoScrollListWidget(QListWidget):
    """クリック時に先読みスクロールを抑制するQListWidget。"""
    def __init__(self, on_mouse_press, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_mouse_press = on_mouse_press

    def mousePressEvent(self, event):
        self._on_mouse_press()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._on_mouse_press()  # リリース後もフラグをリセット


class MS2ListPanel(QWidget):
    """MS2スペクトル一覧パネル。上部フィルター + 下部リスト。"""

    # シグナル: クリックされたエントリを通知 (frame_idx, prec_scan)
    entry_selected = pyqtSignal(int, int)
    # シグナル: Filterボタンが押された (mz_center_str, rt_center_str, intensity_min)
    update_requested = pyqtSignal(str, str, float)
    # シグナル: ページ変更時にTIC範囲を通知 (rt_min, rt_max)
    page_changed = pyqtSignal(float, float)

    FIELD_STYLE = """
        QLineEdit {
            border: 1px solid #bbb;
            border-radius: 3px;
            padding: 2px 4px;
            background: white;
            font-size: 11px;
        }
        QLineEdit:focus {
            border: 1px solid #1565C0;
        }
    """
    BTN_STYLE = """
        QPushButton {
            background: #1565C0;
            color: white;
            border: none;
            border-radius: 3px;
            padding: 4px 0px;
            font-size: 11px;
            font-weight: bold;
        }
        QPushButton:hover  { background: #1976D2; }
        QPushButton:pressed{ background: #0D47A1; }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: #f5f5f5;")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── タイトル ──────────────────────────────────────────────
        title = QLabel("☰ MS2 List")
        title.setStyleSheet("font-weight: bold; font-size: 13px; color: #333;")
        root.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #ccc;")
        root.addWidget(sep)

        # ── フィルターグループ（上段1割） ─────────────────────────
        filter_group = QGroupBox("Filter")
        filter_group.setStyleSheet("QGroupBox { font-size: 11px; }")
        fl = QVBoxLayout(filter_group)
        fl.setSpacing(4)

        # m/z フィルター
        mz_label = QLabel("Precursor m/z")
        mz_label.setStyleSheet("font-size: 10px; color: #555;")
        fl.addWidget(mz_label)

        mz_row = QHBoxLayout()
        mz_row.setSpacing(4)
        self.mz_center = QLineEdit()
        self.mz_center.setPlaceholderText("例: 584.3 or 500:700")
        self.mz_center.setStyleSheet(self.FIELD_STYLE)
        self.mz_center.returnPressed.connect(self._on_update)
        mz_row.addWidget(self.mz_center)
        fl.addLayout(mz_row)

        # RT フィルター
        rt_label = QLabel("RT (min)")
        rt_label.setStyleSheet("font-size: 10px; color: #555;")
        fl.addWidget(rt_label)

        rt_row = QHBoxLayout()
        rt_row.setSpacing(4)
        self.rt_center = QLineEdit()
        self.rt_center.setPlaceholderText("例: 10.5 or 5:15")
        self.rt_center.setStyleSheet(self.FIELD_STYLE)
        self.rt_center.returnPressed.connect(self._on_update)
        rt_row.addWidget(self.rt_center)
        fl.addLayout(rt_row)

        # Intensity フィルター（トグルボタン風ラジオボタン）
        int_label = QLabel("Intensity (min)")
        int_label.setStyleSheet("font-size: 10px; color: #555;")
        fl.addWidget(int_label)

        TOGGLE_STYLE = """
            QRadioButton {
                font-size: 10px;
                color: #555;
                spacing: 0px;
                padding: 2px 6px;
                border: 1px solid #bbb;
                border-radius: 3px;
                background: #f5f5f5;
            }
            QRadioButton:checked {
                color: white;
                background: #1565C0;
                border: 1px solid #1565C0;
            }
            QRadioButton:hover {
                border: 1px solid #1565C0;
                background: #e3f0ff;
            }
            QRadioButton:checked:hover {
                background: #1976D2;
            }
            QRadioButton::indicator {
                width: 0px;
                height: 0px;
                image: none;
                border: none;
                background: none;
            }
        """

        self._int_btn_group = QButtonGroup(self)
        self._int_thresholds = [
            ("ALL",  0),
            (">1e4", 1e4),
            (">1e5", 1e5),
            (">1e6", 1e6),
        ]
        int_row = QHBoxLayout()
        int_row.setSpacing(3)
        for i, (label, val) in enumerate(self._int_thresholds):
            rb = QRadioButton(label)
            rb.setStyleSheet(TOGGLE_STYLE)
            rb.setChecked(i == 0)
            rb.toggled.connect(self._on_update)
            self._int_btn_group.addButton(rb, i)
            int_row.addWidget(rb)
        int_row.addStretch()
        fl.addLayout(int_row)

        root.addWidget(filter_group)

        # ── ヘッダー行 ─────────────────────────────────────────────
        hdr = QLabel(" Inten.     RT(m)  m/z         z   1/K₀")
        hdr.setStyleSheet("font-size: 10px; color: #666; font-family: monospace;")
        root.addWidget(hdr)

        # ── リスト（1列・高速描画） ────────────────────────────────
        self.list_widget = _NoScrollListWidget(self._on_mouse_event)
        self.list_widget.setStyleSheet("""
            QListWidget {
                font-size: 11px;
                font-family: monospace;
                border: 1px solid #ccc;
                background: white;
            }
            QListWidget::item {
                padding: 2px 4px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background: #BBDEFB;
                color: #0D47A1;
            }
            QListWidget::item:hover {
                background: #E3F2FD;
            }
        """)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.currentItemChanged.connect(self._on_current_item_changed)
        # Enterキーで選択中アイテムにジャンプ
        enter_sc = QShortcut(QKeySequence(Qt.Key.Key_Return), self.list_widget)
        enter_sc.activated.connect(self._on_enter_pressed)
        enter_sc2 = QShortcut(QKeySequence(Qt.Key.Key_Enter), self.list_widget)
        enter_sc2.activated.connect(self._on_enter_pressed)
        self.list_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.list_widget, stretch=1)

        # ── ページネーション ───────────────────────────────────────
        page_row = QHBoxLayout()
        page_row.setSpacing(2)

        BTN_STYLE_SM = self.BTN_STYLE + "QPushButton { padding: 2px 5px; font-size: 11px; }"

        self.prev10_btn = QPushButton("<<")
        self.prev_btn   = QPushButton("<")
        self.next_btn   = QPushButton(">")
        self.next10_btn = QPushButton(">>")

        for btn in (self.prev10_btn, self.prev_btn, self.next_btn, self.next10_btn):
            btn.setFixedHeight(22)
            btn.setFixedWidth(28)
            btn.setStyleSheet(self.BTN_STYLE)

        self.prev10_btn.clicked.connect(lambda: self._on_jump_page(-10))
        self.prev_btn.clicked.connect(lambda: self._on_jump_page(-1))
        self.next_btn.clicked.connect(lambda: self._on_jump_page(1))
        self.next10_btn.clicked.connect(lambda: self._on_jump_page(10))

        self.page_label = QLabel("")
        self.page_label.setStyleSheet("font-size: 10px; color: #555;")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        page_row.addWidget(self.prev10_btn)
        page_row.addWidget(self.prev_btn)
        page_row.addWidget(self.page_label, stretch=1)
        page_row.addWidget(self.next_btn)
        page_row.addWidget(self.next10_btn)
        root.addLayout(page_row)

        # エントリ数表示
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("font-size: 10px; color: #888;")
        root.addWidget(self.count_label)

        # 内部データ
        self._entries       = []
        self._page          = 0       # 現在ページ（0始まり）
        self._clicked       = False   # クリック中フラグ（先読みスクロール抑制用）
        self.PAGE_SIZE      = 5000

    # ── 公開API ───────────────────────────────────────────────────
    def set_entries(self, entries: list):
        """ms2_index（list of dict）をセットして1ページ目を表示する。"""
        self._entries = entries
        self._page    = 0
        self._show_page()

    def clear(self):
        self._entries = []
        self._page    = 0
        self.list_widget.clear()
        self.count_label.setText("")
        self.page_label.setText("")
        for btn in (self.prev10_btn, self.prev_btn, self.next_btn, self.next10_btn):
            btn.setEnabled(False)
        self.page_changed.emit(0.0, 0.0)

    def show_building_message(self):
        """インデックス構築中メッセージをリスト欄に表示する。"""
        self.list_widget.clear()
        item = QListWidgetItem("  MS2 index 構築中...")
        item.setForeground(pg.mkColor('#888888'))
        self.list_widget.addItem(item)
        self.count_label.setText("")
        self.page_label.setText("")

    # ── 内部 ──────────────────────────────────────────────────────
    def _on_update(self):
        """Enterキー/ラジオボタン変更: フィルター値をシグナルで通知する。"""
        checked_id = self._int_btn_group.checkedId()
        int_min = self._int_thresholds[checked_id][1] if checked_id >= 0 else 0
        self.update_requested.emit(
            self.mz_center.text().strip(),
            self.rt_center.text().strip(),
            float(int_min),
        )

    def _on_jump_page(self, delta: int):
        """ページをdelta分移動する（負=前、正=次）。"""
        total_pages = max(1, (len(self._entries) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        new_page = max(0, min(self._page + delta, total_pages - 1))
        if new_page != self._page:
            self._page = new_page
            self._show_page()

    def _show_page(self):
        """現在ページのエントリを描画してページUI・TIC範囲を更新する。"""
        n      = len(self._entries)
        ps     = self.PAGE_SIZE
        start  = self._page * ps
        end    = min(start + ps, n)
        page_entries = self._entries[start:end]

        self._populate(page_entries)

        # ページUI更新
        total_pages = max(1, (n + ps - 1) // ps)
        self.page_label.setText(f"{self._page + 1} / {total_pages}")
        self.prev_btn.setEnabled(self._page > 0)
        self.prev10_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page < total_pages - 1)
        self.next10_btn.setEnabled(self._page < total_pages - 1)
        self.count_label.setText(
            f"{start + 1:,}–{end:,} / {n:,} entries"
        )

        # TIC範囲シグナル: RT順先頭・末尾をそのまま使う（計算不要）
        if page_entries:
            rt_min = page_entries[0]['rt']
            rt_max = page_entries[-1]['rt']
            self.page_changed.emit(rt_min, rt_max)
        else:
            self.page_changed.emit(0.0, 0.0)

    def _populate(self, entries: list):
        """エントリリストをQListWidgetに1行テキストで表示する。"""
        self.list_widget.clear()
        for e in entries:
            intensity  = e.get('intensity', float('nan'))
            rt         = e.get('rt',        float('nan'))
            mz         = e.get('mz',        float('nan'))
            ch         = e.get('charge',    0)
            im         = e.get('im',        float('nan'))
            charge_str = f"{ch}+" if ch > 0 else "?"
            int_str    = f"{intensity:.1e}" if not np.isnan(intensity) else " n/a  "
            text = f"{int_str}  {rt:5.2f}  {mz:9.4f}  {charge_str:<3} {im:.3f}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, e)
            self.list_widget.addItem(item)

    def _on_mouse_event(self):
        """マウス操作中は先読みスクロールを抑制する。"""
        self._clicked = True
        QTimer.singleShot(0, lambda: setattr(self, '_clicked', False))

    def _on_item_clicked(self, item: QListWidgetItem):
        """クリック: そのエントリにジャンプする。"""
        e = item.data(Qt.ItemDataRole.UserRole)
        if e is not None:
            self.entry_selected.emit(e['frame_idx'], e['prec_scan'])

    def _on_enter_pressed(self):
        """Enterキー: 現在選択中のアイテムにジャンプ。"""
        item = self.list_widget.currentItem()
        if item:
            self._on_item_clicked(item)

    def _on_current_item_changed(self, current: QListWidgetItem, previous):
        """選択変更時: クリック以外（キーボード移動）のみ3行先読みスクロール。"""
        if current is None or self._clicked:
            return
        total = self.list_widget.count()
        curr_row = self.list_widget.row(current)
        prev_row = self.list_widget.row(previous) if previous else curr_row

        offset = 3 if curr_row >= prev_row else -3
        target_row = max(0, min(curr_row + offset, total - 1))
        target_item = self.list_widget.item(target_row)
        if target_item:
            self.list_widget.scrollToItem(
                target_item, QAbstractItemView.ScrollHint.EnsureVisible
            )


# ================================================================
# ================================================================
#  描画ヘルパー
# ================================================================
def stem_item(mz, intensity, color, width=1.0):
    if len(mz) == 0:
        return pg.PlotDataItem()
    x = np.repeat(mz, 2)
    y = np.zeros(len(mz) * 2)
    y[1::2] = intensity
    return pg.PlotDataItem(x, y, connect='pairs', pen=pg.mkPen(color=color, width=width))


def add_peak_labels(plot, mz, intensity, threshold_pct, min_spacing_pct,
                    max_labels=20, color='#333333', font_size=7):
    if len(mz) == 0:
        return
    max_int = intensity.max()
    if max_int <= 0:
        return
    x_min, x_max = plot.vb.viewRange()[0]
    min_spacing_da = (x_max - x_min) * min_spacing_pct / 100.0
    threshold = max_int * threshold_pct / 100.0
    mask = intensity >= threshold
    mz_f, int_f = mz[mask], intensity[mask]
    order = np.argsort(int_f)[::-1]
    mz_f, int_f = mz_f[order], int_f[order]
    placed_x = []
    for x_val, y_val in zip(mz_f, int_f):
        if len(placed_x) >= max_labels:
            break
        if any(abs(x_val - px) < min_spacing_da for px in placed_x):
            continue
        lbl = pg.TextItem(text=f"{x_val:.4f}", color=color, anchor=(0.5, 1.0))
        lbl.setPos(x_val, y_val)
        lbl.setFont(pg.QtGui.QFont("Arial", font_size))
        plot.addItem(lbl)
        placed_x.append(x_val)


# ================================================================
#  SpectrumViewer
# ================================================================
class SpectrumViewer(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spectrum Viewer")
        self.setGeometry(150, 150, 1300, 900)
        self.setMinimumSize(900, 700)

        # ── ローデータ ────────────────────────────────────────────────
        self.D                  = None
        self.all_frame_ids      = None
        self.all_frame_rt       = None
        self.all_frame_type     = None
        self.all_frame_tic      = None
        self.all_frame_bpi      = None
        self.pasef_info         = {}       # {frame_id: [entry, ...]}
        self.ms2_index          = None      # None=未構築, list=構築済み
        self.acquisition_mode   = 'Unknown'
        self.ms2_frame_type_val = 8
        self.global_mz_min      = None
        self.global_mz_max      = None

        # ================================================================
        #  シンプル状態（4変数）
        # ================================================================
        self.current_frame_idx = 0      # all_frame_ids上のインデックス（TIC位置）
        self.current_type      = 'ms1'  # 'ms1' or 'ms2'
        self.current_scan      = 0      # ms1: 0=ALL, 1..N / ms2統合: 1..N / ms2生: 1..M
        self.yellow_band       = None   # (center, half_band) or None

        # 生モード用状態
        self.ms2_raw_precursor_idx = 0  # 生モード中の現在Precursorインデックス（0始まり）

        # MS1 averaged mode 用
        self._ms1_avg_block_scans = np.array([])

        # ラベル再描画用（ズーム連動）
        self._ms1_label_mz  = np.array([])
        self._ms1_label_int = np.array([])
        self._ms2_label_mz  = np.array([])
        self._ms2_label_int = np.array([])

        # 蓄積黄色バー
        self._band_keys  = set()
        self._band_items = []

        # Settings
        self.settings = {
            'ms1_show_bg':     True,
            'ms1_keep_scale':  True,
            'ms1_avg_mode':    False,
            'ms2_keep_scale':  False,
            'ms2_raw_mode':    False,
            'accumulate_bands':False,
            'key_interval':    200,
            'labels_enabled':  True,
            'label_threshold': 5,
            'label_spacing':   1,
            'label_max':       20,
            'label_font_size': 7,
        }

        self._build_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._key_action = None
        self._key_timer  = QTimer(self)
        self._key_timer.setInterval(200)
        self._key_timer.timeout.connect(self._on_key_timer)

    # ================================================================
    #  UI構築
    # ================================================================
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 4, 4)
        root.setSpacing(4)

        # ── ツールバー ──────────────────────────────────────────────
        toolbar = QHBoxLayout()
        self.load_btn = QPushButton("Load .d File")
        self.load_btn.setFixedWidth(120)
        self.load_btn.clicked.connect(self.load_file)
        toolbar.addWidget(self.load_btn)
        toolbar.addSpacing(16)

        self.info_label = QLabel("No file loaded")
        self.info_label.setStyleSheet("color: #444; font-size: 12px;")
        toolbar.addWidget(self.info_label)
        toolbar.addStretch()

        self.settings_btn = QPushButton("⚙ Settings")
        self.settings_btn.setFixedWidth(90)
        self.settings_btn.setCheckable(True)
        self.settings_btn.clicked.connect(self._toggle_settings)
        toolbar.addWidget(self.settings_btn)
        toolbar.addSpacing(4)

        self.ms2list_btn = QPushButton("☰ MS2 List")
        self.ms2list_btn.setFixedWidth(90)
        self.ms2list_btn.setCheckable(True)
        self.ms2list_btn.setEnabled(False)
        self.ms2list_btn.clicked.connect(self._toggle_ms2list)
        toolbar.addWidget(self.ms2list_btn)
        toolbar.addSpacing(8)

        hint = QLabel("←→:Frame  Ctrl+←→:MS1  ↓↑:Scan  Ctrl+↓↑:MS1scan skip  ESC:ALL  TIC:click")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        toolbar.addWidget(hint)
        root.addLayout(toolbar)

        # ── メインエリア ────────────────────────────────────────────
        main_area = QHBoxLayout()
        main_area.setSpacing(0)
        main_area.setContentsMargins(0, 0, 0, 0)

        # スペクトルエリアとサイドパネルを水平Splitterで分割
        self.h_splitter = QSplitter(Qt.Orientation.Horizontal)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # TIC
        tic_w = pg.GraphicsLayoutWidget()
        tic_w.setBackground('w')
        self.tic_plot = tic_w.addPlot()
        self.tic_plot.setLabel('left', 'Intensity')
        self.tic_plot.setLabel('bottom', 'Retention Time (min)')
        self.tic_plot.showGrid(x=True, y=True, alpha=0.3)
        self.tic_plot.setTitle("TIC (MS1)", size="11pt")
        self.tic_plot.vb.setMouseEnabled(x=True, y=False)
        self.vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(color='r', width=1.5, style=Qt.PenStyle.DashLine)
        )
        self.tic_plot.addItem(self.vline)
        # MS2 Listのページ範囲をTICに薄赤で表示
        self.page_region = pg.LinearRegionItem(
            values=[0, 1],
            brush=pg.mkBrush(150, 150, 150, 60),
            pen=pg.mkPen(120, 120, 120, 120, width=1),
            movable=False
        )
        self.page_region.setZValue(5)
        self.page_region.setVisible(False)
        self.tic_plot.addItem(self.page_region)
        self.tic_plot.scene().sigMouseClicked.connect(self._on_tic_clicked)
        splitter.addWidget(tic_w)

        # 2段目: MS1スペクトル(7) + モビログラム(3)
        row2 = QSplitter(Qt.Orientation.Horizontal)

        ms1_outer, ms1_w = self._make_plot_widget()
        self.ms1_glw = ms1_w
        ms1_vb = SpectrumViewBox(
            reset_callback=lambda: self._reset_ms1_view())
        self.ms1_plot = ms1_w.addPlot(viewBox=ms1_vb)
        self.ms1_plot.setLabel('left', 'Intensity')
        self.ms1_plot.setLabel('bottom', 'm/z (Da)')
        self.ms1_plot.showGrid(x=True, y=True, alpha=0.3)
        self.ms1_plot.setTitle("MS1 Spectrum", size="11pt")
        self.ms1_plot.vb.setMouseEnabled(x=True, y=False)
        self.ms1_plot.vb.sigXRangeChanged.connect(self._on_ms1_xrange_changed)
        self._add_y_buttons(ms1_outer,
                            lambda: self._scale_y(self.ms1_plot, 0.5),
                            lambda: self._scale_y(self.ms1_plot, 2.0),
                            lambda: self._reset_ms1_view())
        row2.addWidget(ms1_outer)

        mob_outer, mob_w = self._make_plot_widget()
        self.mob_plot = mob_w.addPlot()
        self.mob_plot.setLabel('left', '1/K\u2080 (V\u00b7s/cm\u00b2)')
        self.mob_plot.setLabel('bottom', 'Max Intensity per Scan')
        self.mob_plot.showGrid(x=True, y=True, alpha=0.3)
        self.mob_plot.setTitle("Mobilogram", size="11pt")
        self.mob_plot.vb.setMouseEnabled(x=False, y=True)
        self.mob_plot.scene().sigMouseClicked.connect(self._on_mobilogram_clicked)
        self._add_y_buttons(mob_outer,
                            lambda: self._scale_y(self.mob_plot, 0.5),
                            lambda: self._scale_y(self.mob_plot, 2.0),
                            lambda: self.mob_plot.enableAutoRange())
        row2.addWidget(mob_outer)
        row2.setSizes([700, 300])
        splitter.addWidget(row2)

        # 3段目: MS1ズーム(3) + MS2スペクトル(7)
        row3 = QSplitter(Qt.Orientation.Horizontal)

        zoom_outer, zoom_w = self._make_plot_widget()
        self.ms1_zoom_plot = zoom_w.addPlot()
        self.ms1_zoom_plot.setLabel('left', 'Intensity')
        self.ms1_zoom_plot.setLabel('bottom', 'm/z (Da)')
        self.ms1_zoom_plot.showGrid(x=True, y=True, alpha=0.3)
        self.ms1_zoom_plot.setTitle("Precursor zoom", size="11pt")
        self.ms1_zoom_plot.vb.setMouseEnabled(x=True, y=False)
        self._add_y_buttons(zoom_outer,
                            lambda: self._scale_y(self.ms1_zoom_plot, 0.5),
                            lambda: self._scale_y(self.ms1_zoom_plot, 2.0),
                            lambda: self.ms1_zoom_plot.enableAutoRange(axis='y'))
        row3.addWidget(zoom_outer)

        ms2_outer, ms2_w = self._make_plot_widget()
        self.ms2_glw = ms2_w
        ms2_vb = SpectrumViewBox(
            reset_callback=lambda: self._reset_ms2_view())
        self.ms2_plot = ms2_w.addPlot(viewBox=ms2_vb)
        self.ms2_plot.setLabel('left', 'Intensity')
        self.ms2_plot.setLabel('bottom', 'm/z (Da)')
        self.ms2_plot.showGrid(x=True, y=True, alpha=0.3)
        self.ms2_plot.setTitle("MS2 Spectrum", size="11pt")
        self.ms2_plot.vb.setMouseEnabled(x=True, y=False)
        self.ms2_plot.vb.sigXRangeChanged.connect(self._on_ms2_xrange_changed)
        self._add_y_buttons(ms2_outer,
                            lambda: self._scale_y(self.ms2_plot, 0.5),
                            lambda: self._scale_y(self.ms2_plot, 2.0),
                            lambda: self._reset_ms2_view())
        row3.addWidget(ms2_outer)
        row3.setSizes([300, 700])
        splitter.addWidget(row3)

        splitter.setSizes([180, 300, 300])
        self.h_splitter.addWidget(splitter)

        self.settings_panel = SettingsPanel(self.settings)
        self.settings_panel.hide()
        self.settings_panel.changed.connect(self._on_settings_changed)
        self.h_splitter.addWidget(self.settings_panel)

        self.ms2list_panel = MS2ListPanel()
        self.ms2list_panel.hide()
        self.ms2list_panel.entry_selected.connect(self._on_ms2list_entry_selected)
        self.ms2list_panel.update_requested.connect(self._on_ms2list_update)
        self.ms2list_panel.page_changed.connect(self._on_ms2list_page_changed)
        self.h_splitter.addWidget(self.ms2list_panel)

        # スペクトルエリアを優先的に広げる
        self.h_splitter.setStretchFactor(0, 1)
        self.h_splitter.setStretchFactor(1, 0)
        self.h_splitter.setStretchFactor(2, 0)

        main_area.addWidget(self.h_splitter)

        root.addLayout(main_area)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Ready  |  Load a .d file to start")

    def _make_plot_widget(self):
        """外側QWidget + GraphicsLayoutWidgetのペアを返す。"""
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        glw = pg.GraphicsLayoutWidget()
        glw.setBackground('w')
        # ボタン行は後で_add_y_buttonsで追加
        outer._plot_layout = layout
        outer._glw = glw
        return outer, glw

    def _add_y_buttons(self, outer, on_up, on_down, on_reset):
        btn_row = QHBoxLayout()
        for label, cb in [("Y ÷2", on_up), ("Y ×2", on_down), ("Y Reset", on_reset)]:
            btn = QPushButton(label)
            btn.setFixedHeight(22); btn.setFixedWidth(60)
            btn.clicked.connect(cb)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        outer._plot_layout.addLayout(btn_row)
        outer._plot_layout.addWidget(outer._glw)

    # ================================================================
    #  Settings
    # ================================================================
    def _toggle_settings(self, checked):
        """Settingsパネルをトグル。MS2 Listと排他。"""
        if checked:
            self.ms2list_panel.hide()
            self.ms2list_btn.setChecked(False)
        self.settings_panel.setVisible(checked)

    def _toggle_ms2list(self, checked):
        """MS2 Listパネルをトグル。Settingsと排他。
        表示時はRaw scan modeを強制的にOFFにする。
        初回表示時はインデックス構築メッセージを出してから全件表示する。"""
        if checked:
            self.settings_panel.hide()
            self.settings_btn.setChecked(False)
            # Raw scan modeを強制OFF
            if self.settings.get('ms2_raw_mode', False):
                self.settings['ms2_raw_mode'] = False
                self.settings_panel.chk_ms2_raw_mode.setChecked(False)
                if self.D is not None and self.current_type == 'ms2':
                    self._switch_ms2_mode(False)
            self.ms2list_panel.setVisible(True)
            # 初回のみ：構築中メッセージを表示してから構築→全件表示
            if self.ms2_index is None:
                self.ms2list_panel.show_building_message()
                QApplication.processEvents()
                self._build_ms2_index()
                self.ms2list_panel.set_entries(self.ms2_index)
        else:
            self.ms2list_panel.setVisible(False)
            self.page_region.setVisible(False)  # TIC範囲を非表示

    def _on_ms2list_page_changed(self, rt_min: float, rt_max: float):
        """ページ切り替え時にTICの薄灰色範囲を更新する。"""
        if rt_min == 0.0 and rt_max == 0.0:
            self.page_region.setVisible(False)
            return
        self.page_region.setRegion([rt_min, rt_max])
        self.page_region.setVisible(True)

    def _on_ms2list_entry_selected(self, frame_idx: int, prec_scan: int):
        """MS2 Listでエントリがクリックされたときにそのフレームへジャンプ。
        _goto()を呼んだあと、直前MS1フレームを探してMS1パネルを明示的に再描画する。
        """
        if self.D is None:
            return

        # まずMS2フレームへジャンプ（通常通り）
        self._goto(frame_idx, scan=prec_scan)

        # 直前MS1フレームを探す
        ms1_idx = None
        for i in range(frame_idx - 1, -1, -1):
            if int(self.all_frame_type[i]) == 0:
                ms1_idx = i
                break

        if ms1_idx is None:
            return

        # MS1パネルだけ直前MS1フレームの内容で再描画する
        # current_frame_idxはMS2のまま変えない
        saved_frame_idx  = self.current_frame_idx
        saved_type       = self.current_type
        saved_scan       = self.current_scan

        self.current_frame_idx = ms1_idx
        self.current_type      = 'ms1'
        self.current_scan      = 0      # ALL表示
        self._redraw_ms1()
        self._update_vline()    # TIC赤線を直前MS1のRT位置に移動

        # 状態をMS2に戻す
        self.current_frame_idx = saved_frame_idx
        self.current_type      = saved_type
        self.current_scan      = saved_scan

        # 黄色バーとズームパネルを再描画
        self._redraw_ms1_for_ms2()

    def _on_ms2list_update(self, mz_text: str, rt_text: str, int_min: float = 0):
        """MS2 List Filter: ms2_indexにフィルターをかけてリストを更新する。
        インデックス未構築の場合は何もしない（初回構築は_toggle_ms2listが担当）。
        入力形式:
          単一値  "584.3"   → 中央値 ± 20ppm (m/z) / ±10min (RT)
          範囲    "500:700" → 500以上700以下
        """
        if self.D is None or self.ms2_index is None:
            return

        entries = self.ms2_index

        # Intensityフィルター（ラジオボタン）
        if int_min > 0:
            entries = [e for e in entries if e.get('intensity', 0) >= int_min]

        # m/zフィルター（単一値: ±20 ppm、範囲: min:max）
        if mz_text:
            if ':' in mz_text:
                try:
                    lo, hi = mz_text.split(':', 1)
                    mz_lo, mz_hi = float(lo.strip()), float(hi.strip())
                    entries = [e for e in entries if mz_lo <= e['mz'] <= mz_hi]
                except ValueError:
                    self.status.showMessage("MS2 List: m/z の範囲指定が無効です（例: 500:700）")
                    return
            else:
                try:
                    mz_center = float(mz_text)
                    ppm_half  = mz_center * 20e-6
                    entries = [e for e in entries
                               if abs(e['mz'] - mz_center) <= ppm_half]
                except ValueError:
                    self.status.showMessage("MS2 List: m/z に無効な値が入力されています")
                    return

        # RTフィルター（単一値: ±10 min、範囲: min:max）
        if rt_text:
            if ':' in rt_text:
                try:
                    lo, hi = rt_text.split(':', 1)
                    rt_lo, rt_hi = float(lo.strip()), float(hi.strip())
                    entries = [e for e in entries if rt_lo <= e['rt'] <= rt_hi]
                except ValueError:
                    self.status.showMessage("MS2 List: RT の範囲指定が無効です（例: 5:15）")
                    return
            else:
                try:
                    rt_center = float(rt_text)
                    entries = [e for e in entries
                               if abs(e['rt'] - rt_center) <= 10.0]
                except ValueError:
                    self.status.showMessage("MS2 List: RT に無効な値が入力されています")
                    return

        self.ms2list_panel.set_entries(entries)
        self.status.showMessage(f"MS2 List: {len(entries):,} entries")

    def _on_settings_changed(self):
        prev_acc     = self.settings.get('accumulate_bands', False)
        prev_raw     = self.settings.get('ms2_raw_mode', False)
        prev_avg_ms1 = self.settings.get('ms1_avg_mode', False)
        self.settings = self.settings_panel.get_settings()
        self._key_timer.setInterval(self.settings.get('key_interval', 200))

        new_raw     = self.settings.get('ms2_raw_mode', False)
        new_avg_ms1 = self.settings.get('ms1_avg_mode', False)

        if prev_acc and not self.settings.get('accumulate_bands', False):
            self._clear_bands()

        if self.D is not None:
            if new_raw != prev_raw and self.current_type == 'ms2':
                self._switch_ms2_mode(new_raw)
            elif new_avg_ms1 != prev_avg_ms1 and self.current_type == 'ms1':
                self._switch_ms1_avg_mode(new_avg_ms1)
            else:
                self._redraw()

    def _switch_ms1_avg_mode(self, entering_avg: bool):
        """MS1 averaged mode 切り替え時の状態遷移と再描画。"""
        frame_id = int(self.all_frame_ids[self.current_frame_idx])
        scans    = self._get_ms1_scans(frame_id)
        n        = len(scans)
        N        = 100  # ブロックサイズ固定

        if entering_avg:
            # 生/ALL → 統合: current_scan が示す生スキャンが含まれるブロックへ
            if self.current_scan == 0:
                self.current_scan = 1  # ALLからならブロック1へ
            else:
                # 現在の生スキャンインデックス(0始まり)からブロック番号を計算
                self.current_scan = (self.current_scan - 1) // N + 1
        else:
            # 統合 → 生: 現在ブロックの先頭スキャンへ
            block_idx = self.current_scan - 1  # 0始まり
            raw_scan_idx = block_idx * N  # 0始まりのスキャンインデックス
            self.current_scan = min(raw_scan_idx + 1, n) if n > 0 else 1

        self._redraw()

    def _switch_ms2_mode(self, entering_raw: bool):
        """MS2モード切り替え時の状態遷移と再描画。"""
        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
        prec_list = self._get_ms2_precursors(frame_id)
        if not prec_list:
            self._redraw()
            return

        if entering_raw:
            # 統合 → 生: current_scan（Precursorインデックス）を保存し、
            #            そのPrecursorの先頭Scanへ
            self.ms2_raw_precursor_idx = max(0, min(
                self.current_scan - 1, len(prec_list) - 1))
            self.current_scan = 1  # Precursor内の先頭Scan
        else:
            # 生 → 統合: 現在のPrecursorインデックスを復元
            self.current_scan = self.ms2_raw_precursor_idx + 1
            self.ms2_raw_precursor_idx = 0

        self._redraw()

    # ================================================================
    #  データアクセス（直接クエリ）
    # ================================================================
    def _get_ms1_all(self, frame_id):
        """MS1フレームの全スキャン合算データを返す。"""
        data = self.D.query(frame_id, columns=('mz', 'intensity'))
        return data['mz'], data['intensity'].astype(np.float64)

    def _get_ms1_scans(self, frame_id):
        """MS1フレームのスキャン番号リスト（ソート済み）を返す。"""
        data = self.D.query(frame_id, columns=('scan',))
        return np.unique(data['scan'])

    def _get_ms2_precursors(self, frame_id):
        """MS2フレームのPrecursorリストをpasef_infoから返す。"""
        return sorted(self.pasef_info.get(frame_id, []), key=lambda x: x[0])

    def _get_mobilogram(self, frame_id):
        """モビログラム用 (inv_mob, max_intensity) を返す。
        各Scan内の最大intensityをX軸値として使用。"""
        data      = self.D.query(frame_id, columns=('scan', 'inv_ion_mobility', 'intensity'))
        scans_raw = data['scan']
        mob_raw   = data['inv_ion_mobility']
        ints_raw  = data['intensity'].astype(np.float64)
        if len(scans_raw) == 0:
            return np.array([]), np.array([])
        unique_scans, inv_idx = np.unique(scans_raw, return_inverse=True)
        max_int = np.zeros(len(unique_scans), dtype=np.float64)
        inv_mob = np.zeros(len(unique_scans), dtype=np.float64)
        for i in range(len(unique_scans)):
            mask       = inv_idx == i
            max_int[i] = ints_raw[mask].max()
            inv_mob[i] = mob_raw[mask][0]   # 同一scan内は同じ値
        return inv_mob, max_int

    def _get_ms2_spectrum(self, frame_id, scan_begin, scan_end):
        """MS2のscan範囲を合算してスペクトルを返す。"""
        data    = self.D.query(frame_id, columns=('scan', 'mz', 'intensity'))
        mask    = (data['scan'] >= scan_begin) & (data['scan'] <= scan_end)
        mz_raw  = data['mz'][mask]
        int_raw = data['intensity'][mask].astype(np.float64)
        if len(mz_raw) == 0:
            return np.array([]), np.array([])
        mz_round       = np.round(mz_raw, 4)
        unique_mz, inv = np.unique(mz_round, return_inverse=True)
        summed         = np.zeros(len(unique_mz), dtype=np.float64)
        np.add.at(summed, inv, int_raw)
        return unique_mz, summed

    @staticmethod
    def _unpack_precursor(entry):
        sb, se, mono_mz, largest_mz, avg_mz, iso_mz, iso_w, ce, charge, prec_int = entry
        if not np.isnan(mono_mz):
            return sb, se, mono_mz, "mono", iso_mz, iso_w, ce, charge
        elif not np.isnan(largest_mz):
            return sb, se, largest_mz, "largest", iso_mz, iso_w, ce, charge
        elif not np.isnan(avg_mz):
            return sb, se, avg_mz, "avg", iso_mz, iso_w, ce, charge
        else:
            return sb, se, iso_mz, "iso", iso_mz, iso_w, ce, charge

    # ================================================================
    #  フレームインデックス検索
    # ================================================================
    def _next_frame(self, from_idx, type_filter=None):
        """from_idxより後の次フレームのインデックスを返す。
        type_filter: None=全て, 'ms1'=MS1のみ, 'ms2'=MS2のみ"""
        for i in range(from_idx + 1, len(self.all_frame_ids)):
            t = int(self.all_frame_type[i])
            if type_filter is None:
                return i
            elif type_filter == 'ms1' and t == 0:
                return i
            elif type_filter == 'ms2' and t != 0:
                return i
        return None

    def _prev_frame(self, from_idx, type_filter=None):
        """from_idxより前の次フレームのインデックスを返す。"""
        for i in range(from_idx - 1, -1, -1):
            t = int(self.all_frame_type[i])
            if type_filter is None:
                return i
            elif type_filter == 'ms1' and t == 0:
                return i
            elif type_filter == 'ms2' and t != 0:
                return i
        return None

    # ================================================================
    #  状態遷移（シンプル4変数を更新して描画）
    # ================================================================
    def _goto(self, frame_idx, scan=None, clear_bands=False):
        """フレームに移動して状態をセット・描画する中心メソッド。
        scan=None → フレームタイプに応じたデフォルト（ms1:0, ms2:1）
        clear_bands=True → 描画後にバンドをクリアしてMS1を再描画"""
        self.current_frame_idx = frame_idx
        frame_type = int(self.all_frame_type[frame_idx])
        self.current_type = 'ms2' if frame_type != 0 else 'ms1'

        if scan is None:
            if self.current_type == 'ms1':
                self.current_scan = 0
            elif self.settings.get('ms2_raw_mode', False):
                self.current_scan = 1
            else:
                self.current_scan = 1
        else:
            self.current_scan = scan

        # yellow_bandはMS2のときに_redrawで更新、MS1のときはクリア
        if self.current_type == 'ms1':
            self.yellow_band = None
            self._clear_bands()
            self.ms2_raw_precursor_idx = 0  # 生モード用Precursorインデックスをリセット

        self._update_vline()
        self._update_panel_highlight()
        self._redraw()

        # バンドクリア指示があれば描画後にクリアしてMS1を再描画
        if clear_bands and self.current_type == 'ms2':
            self._clear_bands()
            self._redraw_ms1()

        self._update_status()

    # ================================================================
    #  描画（_gotoから呼ばれる単一エントリポイント）
    # ================================================================
    def _redraw(self):
        if self.current_type == 'ms1':
            self._redraw_ms1()
        else:
            self._redraw_ms2()
        self._redraw_mobilogram()

    def _redraw_ms1(self):
        if self.settings.get('ms1_avg_mode', False) and self.current_scan != 0:
            self._redraw_ms1_averaged()
            return

        frame_id = int(self.all_frame_ids[self.current_frame_idx])
        rt       = float(self.all_frame_rt[self.current_frame_idx])
        keep     = self.settings.get('ms1_keep_scale', True)

        # X軸保存
        saved_x = self.ms1_plot.vb.viewRange()[0] if keep else None

        self.ms1_plot.clear()
        if self.settings.get('accumulate_bands', False):
            self._redraw_all_bands()

        all_mz, all_int = self._get_ms1_all(frame_id)

        if self.current_scan == 0:
            # ALL モード
            if len(all_mz) > 0:
                self.ms1_plot.addItem(stem_item(all_mz, all_int, C_MS1_ALL))
            mz_lbl, int_lbl = all_mz, all_int
            n = len(all_mz)
            title = (f"[MS1]  Frame {frame_id}  |  RT = {rt:.3f} min  |  "
                     f"ALL scans  |  peaks = {n:,}")
        else:
            # Scan モード
            scan_num = int(self._get_ms1_scans(frame_id)[self.current_scan - 1])
            if self.settings.get('ms1_show_bg', True) and len(all_mz) > 0:
                self.ms1_plot.addItem(stem_item(all_mz, all_int, C_MS1_BG))
            data  = self.D.query(frame_id, columns=('scan', 'mz', 'intensity'))
            mask  = data['scan'] == scan_num
            mz_s  = data['mz'][mask]
            int_s = data['intensity'][mask].astype(np.float64)
            if len(mz_s) > 0:
                self.ms1_plot.addItem(stem_item(mz_s, int_s, C_MS1_SCAN))
            mz_lbl, int_lbl = mz_s, int_s
            try:
                mob_val = self.D.scan_to_inv_ion_mobility(
                    np.array([scan_num]), np.array([frame_id]))[0]
                mob_str = f"1/K\u2080 = {mob_val:.4f}  |  "
            except Exception:
                mob_str = ""
            scans  = self._get_ms1_scans(frame_id)
            pos    = self.current_scan
            total  = len(scans)
            title  = (f"[MS1]  Frame {frame_id}  |  RT = {rt:.3f} min  |  "
                      f"Scan {scan_num} [{pos}/{total}]  {mob_str}peaks = {len(mz_s):,}")

        # X軸
        if keep and saved_x is not None:
            self.ms1_plot.setXRange(saved_x[0], saved_x[1], padding=0)
        elif self.global_mz_min is not None:
            self.ms1_plot.setXRange(self.global_mz_min, self.global_mz_max, padding=0)
        if self.global_mz_min is not None:
            self.ms1_plot.setLimits(xMin=self.global_mz_min, xMax=self.global_mz_max)
            self.ms1_plot.vb.disableAutoRange(axis='x')

        # Y軸（常にALL最大値基準）
        if len(all_int) > 0:
            self.ms1_plot.setYRange(0, all_int.max() * 1.1, padding=0)

        self.ms1_plot.setTitle(title, size="10pt")

        # ラベル
        self._ms1_label_mz  = mz_lbl if len(mz_lbl) > 0 else np.array([])
        self._ms1_label_int = int_lbl if len(int_lbl) > 0 else np.array([])
        if self.settings.get('labels_enabled', True):
            self._redraw_ms1_labels()

        # 黄色バー（MS2が表示中のときのみ）
        self._draw_precursor_marker()

    def _redraw_ms1_averaged(self):
        """MS1 統合モード: 100スキャンブロックを合算して表示。
        グレー背景=全スキャンALL、青前景=現在ブロック合算。"""
        N        = 100
        frame_id = int(self.all_frame_ids[self.current_frame_idx])
        rt       = float(self.all_frame_rt[self.current_frame_idx])
        keep     = self.settings.get('ms1_keep_scale', True)
        saved_x  = self.ms1_plot.vb.viewRange()[0] if keep else None

        scans = self._get_ms1_scans(frame_id)
        n     = len(scans)
        n_blocks = max(1, (n + N - 1) // N)

        # ブロックインデックスをクランプ
        block_idx = max(0, min(self.current_scan - 1, n_blocks - 1))
        self.current_scan = block_idx + 1

        scan_start_idx = block_idx * N               # 0始まりスキャンインデックス
        scan_end_idx   = min(scan_start_idx + N, n)  # 排他的終端
        block_scans    = scans[scan_start_idx:scan_end_idx]  # 実際のスキャン番号

        self.ms1_plot.clear()
        if self.settings.get('accumulate_bands', False):
            self._redraw_all_bands()

        # グレー背景: 全スキャン合算（ALL）
        all_mz, all_int = self._get_ms1_all(frame_id)
        if len(all_mz) > 0:
            self.ms1_plot.addItem(stem_item(all_mz, all_int, C_MS1_BG))

        # 青前景: ブロック内の全データをそのまま描画（重なり気にせず）
        data = self.D.query(frame_id, columns=('scan', 'mz', 'intensity'))
        mask = np.isin(data['scan'], block_scans)
        mz_raw  = data['mz'][mask]
        int_raw = data['intensity'][mask].astype(np.float64)

        if len(mz_raw) > 0:
            self.ms1_plot.addItem(stem_item(mz_raw, int_raw, C_MS1_ALL))
            # ラベル用: 同一m/zは最大値で代表させる
            mz_round       = np.round(mz_raw, 4)
            unique_mz, inv = np.unique(mz_round, return_inverse=True)
            max_int        = np.zeros(len(unique_mz), dtype=np.float64)
            np.maximum.at(max_int, inv, int_raw)
            mz_lbl, int_lbl = unique_mz, max_int
        else:
            mz_lbl, int_lbl = np.array([]), np.array([])

        # 1/K₀範囲
        try:
            mob_begin = self.D.scan_to_inv_ion_mobility(
                np.array([int(block_scans[0])]),  np.array([frame_id]))[0]
            mob_end   = self.D.scan_to_inv_ion_mobility(
                np.array([int(block_scans[-1])]), np.array([frame_id]))[0]
            mob_str = (f"1/K\u2080 {min(mob_begin,mob_end):.4f}"
                       f"\u2013{max(mob_begin,mob_end):.4f}  |  ")
        except Exception:
            mob_str = ""

        title = (f"[MS1]  Frame {frame_id}  |  RT = {rt:.3f} min  |  "
                 f"Block [{block_idx+1}/{n_blocks}]  "
                 f"Scan {int(block_scans[0])}\u2013{int(block_scans[-1])}  "
                 f"({len(block_scans)} scans)  |  {mob_str}"
                 f"peaks = {len(mz_raw):,}")

        # X軸
        if keep and saved_x is not None:
            self.ms1_plot.setXRange(saved_x[0], saved_x[1], padding=0)
        elif self.global_mz_min is not None:
            self.ms1_plot.setXRange(self.global_mz_min, self.global_mz_max, padding=0)
        if self.global_mz_min is not None:
            self.ms1_plot.setLimits(xMin=self.global_mz_min, xMax=self.global_mz_max)
            self.ms1_plot.vb.disableAutoRange(axis='x')

        # Y軸: ALL最大値基準
        if len(all_int) > 0:
            self.ms1_plot.setYRange(0, all_int.max() * 1.1, padding=0)

        self.ms1_plot.setTitle(title, size="10pt")

        # ラベル
        self._ms1_label_mz  = mz_lbl
        self._ms1_label_int = int_lbl
        if self.settings.get('labels_enabled', True) and len(mz_lbl) > 0:
            self._redraw_ms1_labels()

        # 黄色バー（MS2表示中のみ）
        self._draw_precursor_marker()

        # モビログラムの現在位置マーカー用に中央スキャンを記録
        self._ms1_avg_block_scans = block_scans

    def _redraw_ms2(self):
        if self.settings.get('ms2_raw_mode', False):
            self._redraw_ms2_raw()
            return

        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
        rt        = float(self.all_frame_rt[self.current_frame_idx])
        keep      = self.settings.get('ms2_keep_scale', False)
        saved_x   = self.ms2_plot.vb.viewRange()[0] if keep else None
        saved_y   = self.ms2_plot.vb.viewRange()[1] if keep else None

        self.ms2_plot.clear()

        prec_list = self._get_ms2_precursors(frame_id)
        if not prec_list:
            self.ms2_plot.setTitle(
                f"[MS2]  Frame {frame_id}  |  No precursor info", size="10pt")
            self._redraw_ms1_for_ms2()
            return

        idx = max(0, min(self.current_scan - 1, len(prec_list) - 1))
        self.current_scan = idx + 1   # 範囲クランプ

        sb, se, display_mz, mz_label, iso_mz, iso_w, ce, charge = \
            self._unpack_precursor(prec_list[idx])

        mz_s, int_s = self._get_ms2_spectrum(frame_id, sb, se)
        if len(mz_s) > 0:
            self.ms2_plot.addItem(stem_item(mz_s, int_s, C_MS2))

        # スケール
        if keep and saved_x is not None:
            self.ms2_plot.setXRange(saved_x[0], saved_x[1], padding=0)
            self.ms2_plot.setYRange(max(0, saved_y[0]), saved_y[1], padding=0)
        else:
            if len(mz_s) > 0:
                xpad = (mz_s.max() - mz_s.min()) * 0.05 + 1.0
                self.ms2_plot.setXRange(mz_s.min() - xpad, mz_s.max() + xpad, padding=0)
            elif self.global_mz_min is not None:
                self.ms2_plot.setXRange(self.global_mz_min, self.global_mz_max, padding=0)
            if len(int_s) > 0:
                self.ms2_plot.setYRange(0, int_s.max() * 1.1, padding=0)
        if self.global_mz_min is not None:
            self.ms2_plot.setLimits(xMin=self.global_mz_min, xMax=self.global_mz_max)

        # タイトル
        charge_str = f"({charge}+)" if charge > 0 else ""
        if self.acquisition_mode == 'DIA':
            mz_str  = f"Window Iso {iso_mz:.2f} \u00b1{iso_w/2:.1f} Da"
            ce_str  = f"  CE {ce:.1f} eV" if not np.isnan(ce) else ""
            iso_str = ""
        else:
            if mz_label == "mono":
                mz_str = f"m/z = {display_mz:.4f} {charge_str}"
            elif mz_label == "largest":
                mz_str = f"m/z \u2248 {display_mz:.4f} {charge_str} [Largest]"
            elif mz_label == "avg":
                mz_str = f"m/z \u2248 {display_mz:.4f} {charge_str} [Avg]"
            else:
                mz_str = f"m/z unknown  Iso center {iso_mz:.4f}"
            iso_str = f"  Iso {iso_mz:.2f} \u00b1{iso_w/2:.1f} Da" \
                      if (not np.isnan(iso_mz) and not np.isnan(iso_w)) else ""
            ce_str  = f"  CE {ce:.1f} eV" if not np.isnan(ce) else ""

        unit  = "Window" if self.acquisition_mode == 'DIA' else "Precursor"
        pos   = self.current_scan
        total = len(prec_list)
        self.ms2_plot.setTitle(
            f"[MS2]  Frame {frame_id}  |  RT = {rt:.3f} min  |  "
            f"{unit} [{pos}/{total}]  {mz_str}{iso_str}{ce_str}  |  "
            f"Scan {sb}\u2013{se}  |  peaks = {len(mz_s):,}",
            size="10pt"
        )

        # yellow_band更新
        if mz_label == "mono":
            self.yellow_band = (display_mz, 2.0)
        else:
            center = iso_mz if not np.isnan(iso_mz) else display_mz
            half   = (iso_w / 2.0) if not np.isnan(iso_w) else 2.0
            self.yellow_band = (center, half)

        # ラベル
        self._ms2_label_mz  = mz_s
        self._ms2_label_int = int_s
        if self.settings.get('labels_enabled', True) and len(mz_s) > 0:
            self._redraw_ms2_labels()

        # MS1パネルの黄色バー更新
        self._redraw_ms1_for_ms2()

    def _redraw_ms2_raw(self):
        """生モード: 現在PrecursorのScanを1本ずつ表示。
        グレー背景=そのPrecursor合算、赤前景=現在Scan。"""
        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
        rt        = float(self.all_frame_rt[self.current_frame_idx])
        keep      = self.settings.get('ms2_keep_scale', False)
        saved_x   = self.ms2_plot.vb.viewRange()[0] if keep else None
        saved_y   = self.ms2_plot.vb.viewRange()[1] if keep else None

        self.ms2_plot.clear()

        prec_list = self._get_ms2_precursors(frame_id)
        if not prec_list:
            self.ms2_plot.setTitle(
                f"[MS2 Raw]  Frame {frame_id}  |  No precursor info", size="10pt")
            self._redraw_ms1_for_ms2()
            return

        # Precursorインデックスをクランプ（読み取り専用、状態は変えない）
        pidx = max(0, min(self.ms2_raw_precursor_idx, len(prec_list) - 1))
        self.ms2_raw_precursor_idx = pidx

        sb, se, display_mz, mz_label, iso_mz, iso_w, ce, charge = \
            self._unpack_precursor(prec_list[pidx])

        # このPrecursorのスキャン番号リスト
        scans_in_prec = list(range(int(sb), int(se) + 1))
        n_scans       = len(scans_in_prec)

        # current_scan の安全クランプ（描画クラッシュ防止のみ、ナビゲーションに影響させない）
        scan_offset = max(1, min(self.current_scan, n_scans))
        scan_num = scans_in_prec[scan_offset - 1]

        # 全データを1回だけ取得
        data = self.D.query(frame_id, columns=('scan', 'mz', 'intensity'))

        # グレー背景: Precursor内の全Scanをそのまま重ね書き（合算なし）
        bg_mask = (data['scan'] >= int(sb)) & (data['scan'] <= int(se))
        mz_all  = data['mz'][bg_mask]
        int_all = data['intensity'][bg_mask].astype(np.float64)
        if len(mz_all) > 0:
            self.ms2_plot.addItem(stem_item(mz_all, int_all, C_MS1_BG))

        # 赤前景: 現在Scan単体
        mask = data['scan'] == scan_num
        mz_s    = data['mz'][mask]
        int_s   = data['intensity'][mask].astype(np.float64)
        if len(mz_s) > 0:
            self.ms2_plot.addItem(stem_item(mz_s, int_s, C_MS1_SCAN))

        # スケール（Y軸はグレー背景の全Scan中の最大値基準）
        if keep and saved_x is not None:
            self.ms2_plot.setXRange(saved_x[0], saved_x[1], padding=0)
            self.ms2_plot.setYRange(max(0, saved_y[0]), saved_y[1], padding=0)
        else:
            if len(mz_all) > 0:
                xpad = (mz_all.max() - mz_all.min()) * 0.05 + 1.0
                self.ms2_plot.setXRange(mz_all.min() - xpad, mz_all.max() + xpad, padding=0)
            elif self.global_mz_min is not None:
                self.ms2_plot.setXRange(self.global_mz_min, self.global_mz_max, padding=0)
            if len(int_all) > 0:
                self.ms2_plot.setYRange(0, int_all.max() * 1.1, padding=0)
            elif len(int_s) > 0:
                self.ms2_plot.setYRange(0, int_s.max() * 1.1, padding=0)
        if self.global_mz_min is not None:
            self.ms2_plot.setLimits(xMin=self.global_mz_min, xMax=self.global_mz_max)

        # 1/K₀
        try:
            mob_val = self.D.scan_to_inv_ion_mobility(
                np.array([scan_num]), np.array([frame_id]))[0]
            mob_str = f"  1/K\u2080 = {mob_val:.4f}"
        except Exception:
            mob_str = ""

        # タイトル
        charge_str = f"({charge}+)" if charge > 0 else ""
        if self.acquisition_mode == 'DIA':
            prec_str = f"Iso {iso_mz:.2f} \u00b1{iso_w/2:.1f} Da"
        else:
            if mz_label == "mono":
                prec_str = f"m/z = {display_mz:.4f} {charge_str}"
            elif mz_label in ("largest", "avg"):
                prec_str = f"m/z \u2248 {display_mz:.4f} {charge_str} [{mz_label}]"
            else:
                prec_str = f"Iso center {iso_mz:.4f}"
        unit  = "Window" if self.acquisition_mode == 'DIA' else "Precursor"
        ce_str = f"  CE {ce:.1f} eV" if not np.isnan(ce) else ""
        self.ms2_plot.setTitle(
            f"[MS2 Raw]  Frame {frame_id}  |  RT = {rt:.3f} min  |  "
            f"{unit} [{pidx+1}/{len(prec_list)}]  {prec_str}{ce_str}  |  "
            f"Raw Scan {scan_num} [{scan_offset}/{n_scans}]{mob_str}  |  "
            f"peaks = {len(mz_s):,}",
            size="10pt"
        )

        # yellow_band更新
        if mz_label == "mono":
            self.yellow_band = (display_mz, 2.0)
        else:
            center = iso_mz if not np.isnan(iso_mz) else display_mz
            half   = (iso_w / 2.0) if not np.isnan(iso_w) else 2.0
            self.yellow_band = (center, half)

        # ラベル（現在Scanのみ対象）
        self._ms2_label_mz  = mz_s
        self._ms2_label_int = int_s
        if self.settings.get('labels_enabled', True) and len(mz_s) > 0:
            self._redraw_ms2_labels()

        # MS1パネルの黄色バー更新
        self._redraw_ms1_for_ms2()

    def _redraw_ms1_for_ms2(self):
        """MS2表示中にMS1パネルの黄色バーとズームパネルを更新する。"""
        # MS1パネルの黄色バーを再描画
        self._draw_precursor_marker()

    def _redraw_mobilogram(self):
        """現在フレームのモビログラムを描画。"""
        self.mob_plot.clear()
        frame_id = int(self.all_frame_ids[self.current_frame_idx])

        # MS2フレームのときはMS1パネルのフレームを使う
        if self.current_type == 'ms2':
            # MS1パネルに表示中のフレームIDを参照
            # MS2からは直前のMS1フレームのモビログラムを表示
            ms1_idx = self.current_frame_idx
            for i in range(self.current_frame_idx - 1, -1, -1):
                if int(self.all_frame_type[i]) == 0:
                    ms1_idx = i
                    break
            frame_id = int(self.all_frame_ids[ms1_idx])

        inv_mob, summed = self._get_mobilogram(frame_id)
        if len(inv_mob) == 0:
            self.mob_plot.setTitle("Mobilogram (no data)", size="11pt")
            return

        self.mob_plot.plot(summed, inv_mob,
                           pen=pg.mkPen(color=C_MS1_ALL, width=1.5))

        mob_min, mob_max = float(inv_mob.min()), float(inv_mob.max())
        pad = (mob_max - mob_min) * 0.03 if mob_max > mob_min else 0.05
        self.mob_plot.setYRange(mob_min - pad, mob_max + pad, padding=0)
        self.mob_plot.setXRange(0, float(summed.max()) * 1.1, padding=0)

        # Scanモード時の現在位置マーカー（赤横線）
        title_detail = "ALL scans"
        if self.current_type == 'ms1' and self.current_scan > 0:
            if self.settings.get('ms1_avg_mode', False):
                # Block mode: ブロックの中央スキャンのインデックスを使用
                N         = 100
                n_scans   = len(inv_mob)
                block_idx = self.current_scan - 1
                s_idx     = block_idx * N
                e_idx     = min(s_idx + N, n_scans)
                mid_idx   = (s_idx + e_idx - 1) // 2  # 中央インデックス
                if mid_idx < len(inv_mob):
                    mob_val  = float(inv_mob[mid_idx])
                    scans    = self._get_ms1_scans(frame_id)
                    scan_num = int(scans[mid_idx])
                    hline = pg.InfiniteLine(
                        pos=mob_val, angle=0, movable=False,
                        pen=pg.mkPen(color=C_MS1_SCAN, width=1.5,
                                     style=Qt.PenStyle.DashLine))
                    self.mob_plot.addItem(hline)
                    n_blocks = (n_scans + N - 1) // N
                    s_scan   = int(scans[s_idx])
                    e_scan   = int(scans[e_idx - 1])
                    title_detail = (f"Block [{self.current_scan}/{n_blocks}]  "
                                    f"Scan {s_scan}\u2013{e_scan}  "
                                    f"center 1/K\u2080 = {mob_val:.4f}")
            else:
                scan_idx = self.current_scan - 1
                if scan_idx < len(inv_mob):
                    mob_val  = float(inv_mob[scan_idx])
                    scan_num = int(self._get_ms1_scans(frame_id)[scan_idx])
                    hline = pg.InfiniteLine(
                        pos=mob_val, angle=0, movable=False,
                        pen=pg.mkPen(color=C_MS1_SCAN, width=1.5,
                                     style=Qt.PenStyle.DashLine))
                    self.mob_plot.addItem(hline)
                    title_detail = f"Scan {scan_num}  1/K\u2080 = {mob_val:.4f}"

        self.mob_plot.setTitle(
            f"Mobilogram  Frame {frame_id}  |  {title_detail}", size="10pt")

    # ================================================================
    #  黄色バー・ズームパネル
    # ================================================================
    def _clear_bands(self):
        self._band_keys.clear()
        self._band_items.clear()

    def _make_region_item(self, center, half):
        return pg.LinearRegionItem(
            values=(center - half, center + half),
            brush=pg.mkBrush(255, 220, 0, 80),
            pen=pg.mkPen(255, 200, 0, 150),
            movable=False
        )

    def _redraw_all_bands(self):
        for center, half in self._band_items:
            self.ms1_plot.addItem(self._make_region_item(center, half))

    def _draw_precursor_marker(self):
        """MS1パネルに黄色バンドを描画、ズームパネルを更新。"""
        if self.yellow_band is None or self.current_type == 'ms1':
            self.ms1_zoom_plot.clear()
            self.ms1_zoom_plot.setTitle("Precursor zoom", size="11pt")
            return

        center, half = self.yellow_band
        if np.isnan(center):
            self.ms1_zoom_plot.clear()
            return

        # バンド描画
        acc = self.settings.get('accumulate_bands', False)
        if acc:
            frame_id  = int(self.all_frame_ids[self.current_frame_idx])
            prec_list = self._get_ms2_precursors(frame_id)
            if prec_list:
                # Raw scan mode時はms2_raw_precursor_idx（0始まり）を使う
                # 統合モード時はcurrent_scan - 1（0始まりに変換）
                if self.settings.get('ms2_raw_mode', False):
                    prec_idx = self.ms2_raw_precursor_idx
                else:
                    prec_idx = self.current_scan - 1
                prec_idx = max(0, min(prec_idx, len(prec_list) - 1))
                entry = prec_list[prec_idx]
                key   = (frame_id, entry[0], entry[1])
                if key not in self._band_keys:
                    self._band_keys.add(key)
                    self._band_items.append((center, half))
                    self.ms1_plot.addItem(self._make_region_item(center, half))
        else:
            # 既存バンドを削除して再描画
            for item in list(self.ms1_plot.items):
                if isinstance(item, pg.LinearRegionItem):
                    self.ms1_plot.removeItem(item)
            self.ms1_plot.addItem(self._make_region_item(center, half))

        # ズームパネル
        self.ms1_zoom_plot.clear()
        zoom_half = half + 2.0

        # MS1パネルの現在フレームを参照
        ms1_frame_id = None
        for i in range(self.current_frame_idx - 1, -1, -1):
            if int(self.all_frame_type[i]) == 0:
                ms1_frame_id = int(self.all_frame_ids[i])
                break

        if ms1_frame_id is not None:
            all_mz, all_int = self._get_ms1_all(ms1_frame_id)
            zmask = (all_mz >= center - zoom_half) & (all_mz <= center + zoom_half)
            if zmask.any():
                self.ms1_zoom_plot.addItem(
                    stem_item(all_mz[zmask], all_int[zmask], C_MS1_BG))

        self.ms1_zoom_plot.addItem(self._make_region_item(center, half))
        self.ms1_zoom_plot.setXRange(center - zoom_half, center + zoom_half, padding=0)
        self.ms1_zoom_plot.enableAutoRange(axis='y')

        # ズームタイトル
        frame_id = int(self.all_frame_ids[self.current_frame_idx])
        prec_list = self._get_ms2_precursors(frame_id)
        # Raw scan mode時はms2_raw_precursor_idx、統合モード時はcurrent_scan - 1
        if self.settings.get('ms2_raw_mode', False):
            zoom_prec_idx = self.ms2_raw_precursor_idx
        else:
            zoom_prec_idx = self.current_scan - 1
        if prec_list and 0 <= zoom_prec_idx < len(prec_list):
            _, _, display_mz, mz_label, iso_mz, iso_w, ce, charge = \
                self._unpack_precursor(prec_list[zoom_prec_idx])
            if self.acquisition_mode == 'DIA':
                zoom_title = f"ISO zoom  Iso {iso_mz:.2f} \u00b1{half:.1f} Da"
            else:
                charge_str = f"({charge}+)" if charge > 0 else ""
                if mz_label == "mono":
                    mz_str = f"m/z = {display_mz:.4f} {charge_str}"
                else:
                    mz_str = f"m/z \u2248 {display_mz:.4f} {charge_str} [{mz_label}]"
                zoom_title = f"Precursor zoom  {mz_str}"
            self.ms1_zoom_plot.setTitle(zoom_title, size="9pt")

    # ================================================================
    #  ラベル
    # ================================================================
    def _clear_labels(self, plot):
        for item in list(plot.items):
            if isinstance(item, pg.TextItem):
                plot.removeItem(item)

    def _redraw_ms1_labels(self):
        if not self.settings.get('labels_enabled', True):
            return
        if len(self._ms1_label_mz) == 0:
            return
        self._clear_labels(self.ms1_plot)
        x_min, x_max = self.ms1_plot.vb.viewRange()[0]
        mask = (self._ms1_label_mz >= x_min) & (self._ms1_label_mz <= x_max)
        if mask.any():
            add_peak_labels(
                self.ms1_plot, self._ms1_label_mz[mask], self._ms1_label_int[mask],
                self.settings.get('label_threshold', 5),
                self.settings.get('label_spacing', 1),
                self.settings.get('label_max', 20),
                font_size=self.settings.get('label_font_size', 7))

    def _redraw_ms2_labels(self):
        if not self.settings.get('labels_enabled', True):
            return
        if len(self._ms2_label_mz) == 0:
            return
        self._clear_labels(self.ms2_plot)
        x_min, x_max = self.ms2_plot.vb.viewRange()[0]
        mask = (self._ms2_label_mz >= x_min) & (self._ms2_label_mz <= x_max)
        if mask.any():
            add_peak_labels(
                self.ms2_plot, self._ms2_label_mz[mask], self._ms2_label_int[mask],
                self.settings.get('label_threshold', 5),
                self.settings.get('label_spacing', 1),
                self.settings.get('label_max', 20),
                color='#7f3000',
                font_size=self.settings.get('label_font_size', 7))

    def _on_ms1_xrange_changed(self):
        self._redraw_ms1_labels()

    def _on_ms2_xrange_changed(self):
        self._redraw_ms2_labels()

    # ================================================================
    #  スケールリセット
    # ================================================================
    def _scale_y(self, plot, factor):
        _, (y_min, y_max) = plot.vb.viewRange()
        plot.setYRange(0, max(y_max * factor, 1.0), padding=0)

    def _reset_ms1_view(self):
        if self.global_mz_min is not None:
            self.ms1_plot.setXRange(self.global_mz_min, self.global_mz_max, padding=0)
        if self.D is None:
            return
        frame_id = int(self.all_frame_ids[self.current_frame_idx])
        if self.current_type == 'ms1':
            _, all_int = self._get_ms1_all(frame_id)
            if len(all_int) > 0:
                self.ms1_plot.setYRange(0, all_int.max() * 1.1, padding=0)

    def _reset_ms2_view(self):
        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
        prec_list = self._get_ms2_precursors(frame_id)
        if self.settings.get('ms2_raw_mode', False):
            # 生モード: グレー背景（合算）の最大値でリセット
            if prec_list:
                pidx = max(0, min(self.ms2_raw_precursor_idx, len(prec_list) - 1))
                sb, se = prec_list[pidx][0], prec_list[pidx][1]
                _, int_all = self._get_ms2_spectrum(frame_id, sb, se)
                if len(int_all) > 0:
                    self.ms2_plot.setYRange(0, int_all.max() * 1.1, padding=0)
        elif prec_list and 0 < self.current_scan <= len(prec_list):
            sb, se = prec_list[self.current_scan - 1][:2]
            _, int_s = self._get_ms2_spectrum(frame_id, sb, se)
            if len(int_s) > 0:
                self.ms2_plot.setYRange(0, int_s.max() * 1.1, padding=0)
        if self.global_mz_min is not None:
            self.ms2_plot.setXRange(self.global_mz_min, self.global_mz_max, padding=0)

    # ================================================================
    #  パネルハイライト
    # ================================================================
    def _update_panel_highlight(self):
        ACTIVE   = "border: 2px solid #1565C0;"
        INACTIVE = "border: 2px solid #cccccc;"
        self.ms1_glw.setStyleSheet(ACTIVE   if self.current_type == 'ms1' else INACTIVE)
        self.ms2_glw.setStyleSheet(ACTIVE   if self.current_type == 'ms2' else INACTIVE)

    # ================================================================
    #  TIC
    # ================================================================
    def _draw_tic(self):
        self.tic_plot.clear()
        ms1_mask = self.all_frame_type == 0
        ms1_rt   = self.all_frame_rt[ms1_mask]
        ms1_tic  = self.all_frame_tic[ms1_mask].astype(np.float64)

        # TIC（黒）
        self.tic_plot.plot(ms1_rt, ms1_tic,
                           pen=pg.mkPen(color='k', width=1.5))

        # BPI（青）: TIC最大値の半分にスケールして重ね書き
        if self.all_frame_bpi is not None:
            ms1_bpi = self.all_frame_bpi[ms1_mask].astype(np.float64)
            if ms1_bpi.max() > 0 and ms1_tic.max() > 0:
                bpi_scaled = ms1_bpi / ms1_bpi.max() * ms1_tic.max() * 0.5
                self.tic_plot.plot(ms1_rt, bpi_scaled,
                                   pen=pg.mkPen(color=C_MS1_ALL, width=1.2))

        self.tic_plot.addItem(self.vline)
        self.vline.setZValue(10)
        # page_regionもclear()で消えるので再追加（vlineの下に置く）
        self.tic_plot.addItem(self.page_region)
        self.page_region.setZValue(5)
        self.tic_plot.setXRange(
            self.all_frame_rt[0], self.all_frame_rt[-1], padding=0.01)
        self.tic_plot.autoRange()
        self._update_vline()

    def _update_vline(self):
        if self.all_frame_rt is None or self.all_frame_type is None:
            return
        idx = self.current_frame_idx
        if idx < 0 or idx >= len(self.all_frame_type):
            return
        if int(self.all_frame_type[idx]) == 0:
            self.vline.setPos(self.all_frame_rt[idx])
        # MS2のときは直前MS1の位置を維持（変更しない）

    # ================================================================
    #  ステータスバー
    # ================================================================
    def _update_status(self):
        if self.all_frame_ids is None:
            return
        idx      = self.current_frame_idx
        frame_id = int(self.all_frame_ids[idx])
        rt       = float(self.all_frame_rt[idx])
        tag      = "MS2" if self.current_type == 'ms2' else "MS1"

        if self.current_type == 'ms2':
            prec_list = self._get_ms2_precursors(frame_id)
            if self.settings.get('ms2_raw_mode', False):
                # 生モード
                if prec_list:
                    pidx  = max(0, min(self.ms2_raw_precursor_idx, len(prec_list) - 1))
                    sb, se, display_mz, mz_label, iso_mz, iso_w, ce, charge = \
                        self._unpack_precursor(prec_list[pidx])
                    n_scan   = int(se) - int(sb) + 1
                    scan_num = int(sb) + self.current_scan - 1
                    try:
                        mob_val = self.D.scan_to_inv_ion_mobility(
                            np.array([scan_num]), np.array([frame_id]))[0]
                        mob_str = f"  1/K\u2080 = {mob_val:.4f}"
                    except Exception:
                        mob_str = ""
                    ce_str = f"  CE {ce:.1f} eV" if not np.isnan(ce) else ""
                    if self.acquisition_mode == 'DIA':
                        mz_str = f"Iso {iso_mz:.2f} \u00b1{iso_w/2:.1f} Da"
                    else:
                        cs = f"({charge}+)" if charge > 0 else ""
                        mz_str = f"m/z = {display_mz:.4f} {cs}" if mz_label == "mono" \
                                 else f"m/z \u2248 {display_mz:.4f} {cs} [{mz_label}]"
                    detail = (f"[Raw] Prec [{pidx+1}/{len(prec_list)}]  {mz_str}{ce_str}  "
                              f"Scan {scan_num} [{self.current_scan}/{n_scan}]{mob_str}")
                else:
                    detail = "No precursor info"
            elif prec_list and 0 < self.current_scan <= len(prec_list):
                sb, se, display_mz, mz_label, iso_mz, iso_w, ce, charge = \
                    self._unpack_precursor(prec_list[self.current_scan - 1])
                pos = self.current_scan; total = len(prec_list)
                try:
                    m0 = self.D.scan_to_inv_ion_mobility(
                        np.array([sb]), np.array([frame_id]))[0]
                    m1 = self.D.scan_to_inv_ion_mobility(
                        np.array([se]), np.array([frame_id]))[0]
                    mob_str = f"  1/K\u2080 {min(m0,m1):.4f}\u2013{max(m0,m1):.4f}"
                except Exception:
                    mob_str = ""
                ce_str = f"  CE {ce:.1f} eV" if not np.isnan(ce) else ""
                if self.acquisition_mode == 'DIA':
                    mz_str = f"Iso {iso_mz:.2f} \u00b1{iso_w/2:.1f} Da"
                else:
                    cs = f"({charge}+)" if charge > 0 else ""
                    mz_str = f"m/z = {display_mz:.4f} {cs}" if mz_label == "mono" \
                             else f"m/z \u2248 {display_mz:.4f} {cs} [{mz_label}]"
                detail = (f"Precursor [{pos}/{total}]  {mz_str}{ce_str}  "
                          f"Scan {sb}\u2013{se}{mob_str}")
            else:
                detail = "No precursor info"
        else:
            if self.current_scan == 0:
                detail = "ALL scans  |  ↓: enter scan mode"
            elif self.settings.get('ms1_avg_mode', False):
                scans    = self._get_ms1_scans(frame_id)
                n        = len(scans)
                N        = 100
                n_blocks = self._ms1_block_count(frame_id)
                block_idx      = max(0, min(self.current_scan - 1, n_blocks - 1))
                scan_start_idx = block_idx * N
                scan_end_idx   = min(scan_start_idx + N, n)
                block_scans    = scans[scan_start_idx:scan_end_idx]
                try:
                    m0 = self.D.scan_to_inv_ion_mobility(
                        np.array([int(block_scans[0])]),  np.array([frame_id]))[0]
                    m1 = self.D.scan_to_inv_ion_mobility(
                        np.array([int(block_scans[-1])]), np.array([frame_id]))[0]
                    mob_str = (f"  1/K\u2080 {min(m0,m1):.4f}"
                               f"\u2013{max(m0,m1):.4f}")
                except Exception:
                    mob_str = ""
                detail = (f"[Block] [{self.current_scan}/{n_blocks}]  "
                          f"Scan {int(block_scans[0])}\u2013{int(block_scans[-1])}"
                          f" ({len(block_scans)} scans){mob_str}"
                          f"  |  ↑↓: move  |  ESC: ALL")
            else:
                scans    = self._get_ms1_scans(frame_id)
                scan_num = int(scans[self.current_scan - 1])
                try:
                    inv_mob = self.D.scan_to_inv_ion_mobility(
                        np.array([scan_num]), np.array([frame_id]))[0]
                    mob_str = f"  1/K\u2080 = {inv_mob:.4f}"
                except Exception:
                    mob_str = ""
                detail = (f"Scan {scan_num} [{self.current_scan}/{len(scans)}]{mob_str}"
                          f"  |  ↑↓: move  |  ESC: ALL")

        self.status.showMessage(
            f"[{tag}]  Frame {frame_id}  |  RT: {rt:.4f} min  |  {detail}"
            f"  |  ←→: Frame  Ctrl+←→: MS1  ↓↑: Scan  Ctrl+↓↑: MS1scan skip")

    # ================================================================
    #  MS2インデックス構築・MS2 Listロジック
    # ================================================================
    def _build_ms2_index(self):
        """pasef_infoからms2_indexをフラットリストとして構築する。
        scan_to_inv_ion_mobilityを全Precursor分まとめて1回呼ぶことで高速化。
        """
        frame_id_to_idx = {int(fid): i for i, fid in enumerate(self.all_frame_ids)}

        frame_idxs = []
        prec_scans = []
        rts        = []
        mzs        = []
        charges    = []
        intensities= []
        mid_scans  = []
        frame_ids  = []

        for frame_id, entries in self.pasef_info.items():
            fidx = frame_id_to_idx.get(frame_id)
            if fidx is None:
                continue
            rt = float(self.all_frame_rt[fidx])

            for prec_scan, entry in enumerate(entries, start=1):
                sb, se, mono_mz, largest_mz, avg_mz, iso_mz, iso_w, ce, charge, prec_int = entry

                if not np.isnan(mono_mz):
                    mz = mono_mz
                elif not np.isnan(largest_mz):
                    mz = largest_mz
                elif not np.isnan(avg_mz):
                    mz = avg_mz
                else:
                    mz = iso_mz

                frame_idxs.append(fidx)
                prec_scans.append(prec_scan)
                rts.append(rt)
                mzs.append(float(mz))
                charges.append(int(charge))
                intensities.append(float(prec_int))
                mid_scans.append((int(sb) + int(se)) // 2)
                frame_ids.append(frame_id)

        if not frame_idxs:
            self.ms2_index = []
            return

        # IM変換: 全件まとめて1回呼ぶ
        try:
            all_im = self.D.scan_to_inv_ion_mobility(
                np.array(mid_scans, dtype=np.int32),
                np.array(frame_ids,  dtype=np.int32),
            ).tolist()
        except Exception:
            all_im = [float('nan')] * len(frame_idxs)

        index = [
            {
                'frame_idx': frame_idxs[i],
                'prec_scan': prec_scans[i],
                'rt':        rts[i],
                'mz':        mzs[i],
                'charge':    charges[i],
                'intensity': intensities[i],
                'im':        all_im[i],
            }
            for i in range(len(frame_idxs))
        ]

        index.sort(key=lambda e: (e['rt'], e['mz']))
        self.ms2_index = index

    # ================================================================
    #  ファイル読み込み
    # ================================================================
    def load_file(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select .d Folder", "", QFileDialog.Option.ShowDirsOnly)
        if not folder:
            return
        path = Path(folder)
        if path.suffix != '.d':
            self.status.showMessage("Error: Please select a .d folder")
            return

        self.status.showMessage("Loading...")
        QApplication.processEvents()

        try:
            from opentimspy.opentims import OpenTIMS
            self.D = OpenTIMS(path)

            frames = self.D.frames
            order  = np.argsort(frames['Id'])
            self.all_frame_ids  = frames['Id'][order]
            self.all_frame_rt   = frames['Time'][order] / 60.0
            self.all_frame_type = frames['MsMsType'][order]
            self.all_frame_tic  = frames['SummedIntensities'][order]
            # BPI: framesテーブルのMaxIntensityカラムから直接取得
            try:
                self.all_frame_bpi = frames['MaxIntensity'][order]
            except (KeyError, TypeError):
                self.all_frame_bpi = None

            # DDA/DIA判定 + メタデータ
            self.pasef_info = {}
            conn = self.D.get_sql_connection()
            cur  = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            db_tables = {r[0] for r in cur.fetchall()}

            if 'PasefFrameMsMsInfo' in db_tables:
                self.acquisition_mode   = 'DDA'
                self.ms2_frame_type_val = 8
                cur.execute("""
                    SELECT pf.Frame, pf.ScanNumBegin, pf.ScanNumEnd,
                           pf.IsolationMz, pf.IsolationWidth, pf.CollisionEnergy,
                           p.MonoisotopicMz, p.LargestPeakMz, p.AverageMz, p.Charge,
                           p.Intensity
                    FROM PasefFrameMsMsInfo pf
                    JOIN Precursors p ON pf.Precursor = p.Id
                    ORDER BY pf.Frame, pf.ScanNumBegin
                """)
                for row in cur.fetchall():
                    fid, sb, se, iso_mz, iso_w, ce, mono_mz, largest_mz, avg_mz, ch, prec_int = row
                    def _f(v): return float(v) if v is not None else float('nan')
                    self.pasef_info.setdefault(int(fid), []).append((
                        int(sb), int(se),
                        _f(mono_mz), _f(largest_mz), _f(avg_mz),
                        _f(iso_mz), _f(iso_w), _f(ce),
                        int(ch) if ch is not None else 0,
                        _f(prec_int)
                    ))

            elif 'DiaFrameMsMsInfo' in db_tables:
                self.acquisition_mode   = 'DIA'
                self.ms2_frame_type_val = 9
                cur.execute("""
                    SELECT WindowGroup, ScanNumBegin, ScanNumEnd,
                           IsolationMz, IsolationWidth, CollisionEnergy
                    FROM DiaFrameMsMsWindows ORDER BY WindowGroup, ScanNumBegin
                """)
                wg_dict = {}
                for wg, sb, se, iso_mz, iso_w, ce in cur.fetchall():
                    def _f(v): return float(v) if v is not None else float('nan')
                    wg_dict.setdefault(int(wg), []).append(
                        (int(sb), int(se), _f(iso_mz), _f(iso_w), _f(ce)))
                cur.execute("SELECT Frame, WindowGroup FROM DiaFrameMsMsInfo ORDER BY Frame")
                for fid, wg in cur.fetchall():
                    for sb, se, iso_mz, iso_w, ce in wg_dict.get(int(wg), []):
                        self.pasef_info.setdefault(int(fid), []).append((
                            sb, se,
                            float('nan'), float('nan'), float('nan'),
                            iso_mz, iso_w, ce, 0,
                            float('nan')  # prec_int（DIAには存在しないためnan）
                        ))
            else:
                self.acquisition_mode   = 'MS1Only'
                self.ms2_frame_type_val = -1

            # Global m/z range
            self.global_mz_min = self.global_mz_max = None
            try:
                cur.execute("""SELECT Key, Value FROM GlobalMetadata
                               WHERE Key IN ('MzAcqRangeLower','MzAcqRangeUpper')""")
                for key, val in cur.fetchall():
                    if key == 'MzAcqRangeLower':
                        self.global_mz_min = float(val)
                    elif key == 'MzAcqRangeUpper':
                        self.global_mz_max = float(val)
            except Exception:
                pass

            n_ms1 = int((self.all_frame_type == 0).sum())
            n_ms2 = int((self.all_frame_type != 0).sum())
            self.info_label.setText(
                f"{path.name}  |  [{self.acquisition_mode}]  |  "
                f"MS1: {n_ms1:,}  MS2: {n_ms2:,}  |  "
                f"RT: {self.all_frame_rt[0]:.2f} – {self.all_frame_rt[-1]:.2f} min"
            )

            # X軸初期化
            if self.global_mz_min is not None:
                for plot in (self.ms1_plot, self.ms2_plot):
                    plot.setXRange(self.global_mz_min, self.global_mz_max, padding=0)
                    plot.setLimits(xMin=self.global_mz_min, xMax=self.global_mz_max)
                    plot.vb.disableAutoRange(axis='x')

            # ── パネルリセット（新ファイル読み込み時） ────────────────
            self.settings_panel.hide()
            self.settings_btn.setChecked(False)
            self.ms2list_panel.hide()
            self.ms2list_panel.clear()
            self.ms2list_btn.setChecked(False)

            # DDAのみMS2インデックスを構築（初回Updateまで遅延）
            self.ms2_index = None

            # DDAのみMS2 Listボタンを有効化
            is_dda = (self.acquisition_mode == 'DDA')
            self.ms2list_btn.setEnabled(is_dda)
            self.ms2list_btn.setToolTip(
                "" if is_dda else f"MS2 List is only available for DDA data ({self.acquisition_mode})"
            )

            self._draw_tic()

            # 最初のMS1フレームへ
            first_ms1 = next(
                (i for i, t in enumerate(self.all_frame_type) if int(t) == 0), 0)
            self._goto(first_ms1, scan=0)

        except Exception as e:
            import traceback
            self.status.showMessage(f"Load error: {e}")
            traceback.print_exc()

    # ================================================================
    #  TICクリック
    # ================================================================
    def _on_tic_clicked(self, event):
        if self.D is None or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.scenePos()
        if not self.tic_plot.sceneBoundingRect().contains(pos):
            return
        clicked_rt     = self.tic_plot.vb.mapSceneToView(pos).x()
        ms1_mask       = self.all_frame_type == 0
        ms1_rt         = self.all_frame_rt[ms1_mask]
        ms1_idx_in_all = np.where(ms1_mask)[0]
        if len(ms1_rt) == 0:
            return
        nearest = int(np.argmin(np.abs(ms1_rt - clicked_rt)))
        self._goto(int(ms1_idx_in_all[nearest]), scan=0)

    # ================================================================
    #  モビログラムクリック
    # ================================================================
    def _on_mobilogram_clicked(self, event):
        if self.D is None or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.scenePos()
        if not self.mob_plot.sceneBoundingRect().contains(pos):
            return

        # 現在MS1フレームのモビログラムを参照
        if self.current_type == 'ms2':
            for i in range(self.current_frame_idx - 1, -1, -1):
                if int(self.all_frame_type[i]) == 0:
                    frame_idx = i
                    break
            else:
                return
        else:
            frame_idx = self.current_frame_idx

        frame_id        = int(self.all_frame_ids[frame_idx])
        inv_mob, _      = self._get_mobilogram(frame_id)
        if len(inv_mob) == 0:
            return

        clicked_mob = self.mob_plot.vb.mapSceneToView(pos).y()
        nearest_idx = int(np.argmin(np.abs(inv_mob - clicked_mob)))
        # nearest_idx は scan_listのインデックス → current_scan = nearest_idx + 1
        self._goto(frame_idx, scan=nearest_idx + 1)

    # ================================================================
    #  生モード用ナビゲーションヘルパー
    # ================================================================
    def _ms1_block_count(self, frame_id, N=100):
        """MS1 averaged modeのブロック数を返す。"""
        n = len(self._get_ms1_scans(frame_id))
        return max(1, (n + N - 1) // N)
    def _raw_scans_of_current_prec(self):
        """生モードの現在Precursorのスキャン数（M）を返す。"""
        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
        prec_list = self._get_ms2_precursors(frame_id)
        if not prec_list:
            return 0
        pidx = max(0, min(self.ms2_raw_precursor_idx, len(prec_list) - 1))
        sb, se = prec_list[pidx][0], prec_list[pidx][1]
        return int(se) - int(sb) + 1

    def _raw_prec_down(self):
        """生モードでCtrl+↓: Precursor単位で次へ（常に先頭Scanで着地）。
        現Precursor途中/先頭 → 次Precursor先頭。
        現Precursor末尾      → 次フレームのWindow1/先頭Scanへ。"""
        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
        prec_list = self._get_ms2_precursors(frame_id)
        if not prec_list:
            return
        pidx = max(0, min(self.ms2_raw_precursor_idx, len(prec_list) - 1))

        if pidx + 1 < len(prec_list):
            # 次のPrecursor先頭へ（同フレーム内）
            self.ms2_raw_precursor_idx = pidx + 1
            self.current_scan = 1
            self._redraw()
            self._update_status()
        else:
            # フレーム末尾 → 次フレームへ
            idx = self._next_frame(self.current_frame_idx)
            if idx is not None:
                next_type = int(self.all_frame_type[idx])
                if next_type == 0:
                    # 次がMS1 → そのMS1 ALL へ
                    self._goto(idx, scan=0)
                else:
                    # 次がMS2（またはMS1をスキップして次MS2）→ Window1/Scan1へ
                    ms2_idx = self._next_frame(self.current_frame_idx, 'ms2')
                    if ms2_idx is not None:
                        skip = ms2_idx != self.current_frame_idx + 1
                        self.ms2_raw_precursor_idx = 0  # 必ずWindow1から開始
                        self._goto(ms2_idx, scan=1, clear_bands=skip)

    def _raw_prec_up(self):
        """生モードでCtrl+↑: Precursor単位で前へ（常に先頭Scanで着地）。
        現Precursor途中（scan>1） → 現Precursorの先頭Scanに戻る。
        現Precursor先頭（scan=1） → 前Precursor先頭へ。
        フレーム先頭              → 前フレームのWindow1/Scan1へ。"""
        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
        prec_list = self._get_ms2_precursors(frame_id)
        if not prec_list:
            return
        pidx = max(0, min(self.ms2_raw_precursor_idx, len(prec_list) - 1))

        if self.current_scan > 1:
            # 同Precursor内の先頭Scanに戻す
            self.current_scan = 1
            self._redraw()
            self._update_status()
        elif pidx > 0:
            # 前のPrecursor先頭へ（同フレーム内）
            self.ms2_raw_precursor_idx = pidx - 1
            self.current_scan = 1
            self._redraw()
            self._update_status()
        else:
            # フレーム先頭 → 前フレームへ
            idx = self._prev_frame(self.current_frame_idx)
            if idx is not None:
                prev_type = int(self.all_frame_type[idx])
                if prev_type == 0:
                    # 前がMS1 → そのMS1 ALL へ
                    self._goto(idx, scan=0)
                else:
                    # 前がMS2（またはMS1をスキップして前MS2）→ Window1/Scan1へ
                    ms2_idx = self._prev_frame(self.current_frame_idx, 'ms2')
                    if ms2_idx is not None:
                        skip = ms2_idx != self.current_frame_idx - 1
                        self.ms2_raw_precursor_idx = 0  # 必ずWindow1から開始
                        self._goto(ms2_idx, scan=1, clear_bands=skip)

    def _raw_scan_down(self):
        """生モードで↓: 次Scan → Precursor末尾で次Precursor先頭 → 次フレーム先頭。"""
        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
        prec_list = self._get_ms2_precursors(frame_id)
        if not prec_list:
            return
        pidx   = max(0, min(self.ms2_raw_precursor_idx, len(prec_list) - 1))
        n_scan = self._raw_scans_of_current_prec()

        if self.current_scan < n_scan:
            # Precursor内の次Scanへ
            self.current_scan += 1
            self._redraw()
            self._update_status()
        elif pidx + 1 < len(prec_list):
            # 次のPrecursor先頭へ（同フレーム内）
            self.ms2_raw_precursor_idx = pidx + 1
            self.current_scan = 1
            self._redraw()
            self._update_status()
        else:
            # フレーム末尾 → 次フレームへ
            idx = self._next_frame(self.current_frame_idx)
            if idx is not None:
                self._goto(idx)  # _gotoがms2_raw_precursor_idxをリセット

    def _raw_scan_up(self):
        """生モードで↑: 前Scan → Precursor先頭で前Precursor末尾 → 前フレーム末尾。"""
        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
        prec_list = self._get_ms2_precursors(frame_id)
        if not prec_list:
            return
        pidx = max(0, min(self.ms2_raw_precursor_idx, len(prec_list) - 1))

        if self.current_scan > 1:
            # Precursor内の前Scanへ
            self.current_scan -= 1
            self._redraw()
            self._update_status()
        elif pidx > 0:
            # 前のPrecursor末尾へ（同フレーム内）
            self.ms2_raw_precursor_idx = pidx - 1
            sb_prev = prec_list[pidx - 1][0]
            se_prev = prec_list[pidx - 1][1]
            self.current_scan = int(se_prev) - int(sb_prev) + 1
            self._redraw()
            self._update_status()
        else:
            # フレーム先頭 → 前フレームへ
            idx = self._prev_frame(self.current_frame_idx)
            if idx is not None:
                t = int(self.all_frame_type[idx])
                if t == 0:
                    fid   = int(self.all_frame_ids[idx])
                    scans = self._get_ms1_scans(fid)
                    self._goto(idx, scan=len(scans))
                else:
                    # 前MS2フレームの最後のPrecursorの最後のScan
                    fid   = int(self.all_frame_ids[idx])
                    precs = self._get_ms2_precursors(fid)
                    if precs:
                        self.ms2_raw_precursor_idx = len(precs) - 1
                        sb_l, se_l = precs[-1][0], precs[-1][1]
                        n = int(se_l) - int(sb_l) + 1
                        self._goto(idx, scan=n)
                    else:
                        self._goto(idx)

    # ================================================================
    #  キーボード操作
    # ================================================================
    def _make_key_action(self, key, modifiers):
        if self.D is None:
            return None

        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        ct   = self.current_type
        cs   = self.current_scan
        cidx = self.current_frame_idx

        # ── → ──────────────────────────────────────────────────────
        if key == Qt.Key.Key_Right:
            if ctrl:
                # Ctrl+→ : 次MS1フレームへ（scan=0）
                def act():
                    idx = self._next_frame(self.current_frame_idx, 'ms1')
                    if idx is not None:
                        self._goto(idx, scan=0)
            else:
                # → : 次フレームへ（scan=デフォルト）
                def act():
                    idx = self._next_frame(self.current_frame_idx)
                    if idx is not None:
                        self._goto(idx)

        # ── ← ──────────────────────────────────────────────────────
        elif key == Qt.Key.Key_Left:
            if ctrl:
                def act():
                    idx = self._prev_frame(self.current_frame_idx, 'ms1')
                    if idx is not None:
                        self._goto(idx, scan=0)
            else:
                def act():
                    idx = self._prev_frame(self.current_frame_idx)
                    if idx is not None:
                        self._goto(idx)

        # ── ↓ ──────────────────────────────────────────────────────
        elif key == Qt.Key.Key_Down:
            if ctrl:
                # Ctrl+↓ : MS1 scanのみスキップして進む
                #   MS1 scan=0(ALL) → 直後MS2 scan=1へ
                #   MS1 scan>=1     → scan=0(ALL)に戻す
                #   MS2 precursor移動中 → 通常↓と同じ
                #   MS2末尾→次がMS1 → そのMS1 scan=0(ALL)へ
                #   MS2末尾→次がMS2 → 次MS2 scan=1へ（MS1スキップ、bands clear）
                def act():
                    if self.current_type == 'ms1':
                        if self.current_scan >= 1:
                            # Scanモード中 → ALLに戻す
                            self._goto(self.current_frame_idx, scan=0)
                        else:
                            # ALL → 直後MS2へ
                            idx = self._next_frame(self.current_frame_idx, 'ms2')
                            if idx is not None:
                                skip = idx != self.current_frame_idx + 1
                                self._goto(idx, scan=1, clear_bands=skip)
                    else:
                        if self.settings.get('ms2_raw_mode', False):
                            self._raw_prec_down()
                        else:
                            frame_id  = int(self.all_frame_ids[self.current_frame_idx])
                            prec_list = self._get_ms2_precursors(frame_id)
                            if self.current_scan < len(prec_list):
                                # MS2内はprecursor移動（↓と同じ）
                                self._goto(self.current_frame_idx,
                                           scan=self.current_scan + 1)
                            else:
                                # MS2末尾 → 次フレームへ
                                idx = self._next_frame(self.current_frame_idx)
                                if idx is not None:
                                    next_type = int(self.all_frame_type[idx])
                                    if next_type == 0:
                                        # 次がMS1 → そのMS1 scan=0(ALL)へ
                                        self._goto(idx, scan=0)
                                    else:
                                        # 次がMS2 → MS1をスキップして次MS2へ
                                        ms2_idx = self._next_frame(self.current_frame_idx, 'ms2')
                                        if ms2_idx is not None:
                                            skip = ms2_idx != self.current_frame_idx + 1
                                            self._goto(ms2_idx, scan=1, clear_bands=skip)
            else:
                def act():
                    if self.current_type == 'ms1':
                        frame_id = int(self.all_frame_ids[self.current_frame_idx])
                        if self.settings.get('ms1_avg_mode', False) and self.current_scan > 0:
                            # averaged mode: ブロック単位で進む
                            n_blocks = self._ms1_block_count(frame_id)
                            if self.current_scan < n_blocks:
                                self._goto(self.current_frame_idx,
                                           scan=self.current_scan + 1)
                            else:
                                idx = self._next_frame(self.current_frame_idx)
                                if idx is not None:
                                    self._goto(idx)
                        else:
                            scans = self._get_ms1_scans(frame_id)
                            if self.current_scan < len(scans):
                                self._goto(self.current_frame_idx,
                                           scan=self.current_scan + 1)
                            else:
                                idx = self._next_frame(self.current_frame_idx)
                                if idx is not None:
                                    self._goto(idx)
                    elif self.settings.get('ms2_raw_mode', False):
                        self._raw_scan_down()
                    else:
                        frame_id  = int(self.all_frame_ids[self.current_frame_idx])
                        prec_list = self._get_ms2_precursors(frame_id)
                        if self.current_scan < len(prec_list):
                            self._goto(self.current_frame_idx,
                                       scan=self.current_scan + 1)
                        else:
                            idx = self._next_frame(self.current_frame_idx)
                            if idx is not None:
                                self._goto(idx)

        # ── ↑ ──────────────────────────────────────────────────────
        elif key == Qt.Key.Key_Up:
            if ctrl:
                # Ctrl+↑ : MS1 scanをスキップして戻る
                # MS2にいる → precursor後退（先頭で前MS2末尾へ）
                # MS1にいる → 直前のMS2末尾へ
                # 移動先がframe_idx-1でない（MS1やMS2をスキップ）→ バンドクリア
                def act():
                    if self.current_type == 'ms1':
                        if self.current_scan >= 1:
                            # Scanモード中 → ALLに戻す
                            self._goto(self.current_frame_idx, scan=0)
                        else:
                            # ALL → 直前MS2末尾へ
                            idx = self._prev_frame(self.current_frame_idx, 'ms2')
                            if idx is not None:
                                skip  = idx != self.current_frame_idx - 1
                                fid   = int(self.all_frame_ids[idx])
                                precs = self._get_ms2_precursors(fid)
                                self._goto(idx, scan=max(1, len(precs)), clear_bands=skip)
                    else:
                        if self.settings.get('ms2_raw_mode', False):
                            self._raw_prec_up()
                        elif self.current_scan > 1:
                            # MS2内はprecursor移動（↑と同じ）
                            self._goto(self.current_frame_idx,
                                       scan=self.current_scan - 1)
                        else:
                            # MS2先頭 → 前フレームへ
                            idx = self._prev_frame(self.current_frame_idx)
                            if idx is not None:
                                prev_type = int(self.all_frame_type[idx])
                                if prev_type == 0:
                                    # 前がMS1 → そのMS1 scan=0(ALL)へ
                                    self._goto(idx, scan=0)
                                else:
                                    # 前がMS2 → MS1をスキップして前MS2末尾へ
                                    ms2_idx = self._prev_frame(self.current_frame_idx, 'ms2')
                                    if ms2_idx is not None:
                                        skip  = ms2_idx != self.current_frame_idx - 1
                                        fid   = int(self.all_frame_ids[ms2_idx])
                                        precs = self._get_ms2_precursors(fid)
                                        self._goto(ms2_idx, scan=max(1, len(precs)), clear_bands=skip)
            else:
                def act():
                    if self.current_type == 'ms1':
                        if self.current_scan > 1:
                            self._goto(self.current_frame_idx,
                                       scan=self.current_scan - 1)
                        elif self.current_scan == 1:
                            # scan=1 → ALLに戻る
                            self._goto(self.current_frame_idx, scan=0)
                        else:
                            # scan=0(ALL) → 前フレームの末尾へ
                            idx = self._prev_frame(self.current_frame_idx)
                            if idx is not None:
                                t = int(self.all_frame_type[idx])
                                if t == 0:
                                    fid   = int(self.all_frame_ids[idx])
                                    scans = self._get_ms1_scans(fid)
                                    self._goto(idx, scan=len(scans))
                                else:
                                    fid   = int(self.all_frame_ids[idx])
                                    precs = self._get_ms2_precursors(fid)
                                    self._goto(idx, scan=max(1, len(precs)))
                    elif self.settings.get('ms2_raw_mode', False):
                        self._raw_scan_up()
                    else:
                        if self.current_scan > 1:
                            self._goto(self.current_frame_idx,
                                       scan=self.current_scan - 1)
                        else:
                            # scan=1(先頭) → 前フレームの末尾へ
                            idx = self._prev_frame(self.current_frame_idx)
                            if idx is not None:
                                t = int(self.all_frame_type[idx])
                                if t == 0:
                                    fid   = int(self.all_frame_ids[idx])
                                    if self.settings.get('ms1_avg_mode', False):
                                        self._goto(idx, scan=self._ms1_block_count(fid))
                                    else:
                                        scans = self._get_ms1_scans(fid)
                                        self._goto(idx, scan=len(scans))
                                else:
                                    fid   = int(self.all_frame_ids[idx])
                                    precs = self._get_ms2_precursors(fid)
                                    self._goto(idx, scan=max(1, len(precs)))

        # ── ESC ─────────────────────────────────────────────────────
        elif key == Qt.Key.Key_Escape:
            def act():
                if self.current_type == 'ms1' and self.current_scan != 0:
                    self._goto(self.current_frame_idx, scan=0)
        else:
            return None

        return act

    def keyPressEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            return
        action = self._make_key_action(event.key(), event.modifiers())
        if action is None:
            super().keyPressEvent(event)
            return
        self._key_action = action
        action()
        self._key_timer.start()

    def keyReleaseEvent(self, event: QKeyEvent):
        if event.isAutoRepeat():
            return
        self._key_timer.stop()
        self._key_action = None

    def _on_key_timer(self):
        if self._key_action is not None:
            self._key_action()


# ================================================================
#  Entry point
# ================================================================
def main():
    app = QApplication(sys.argv)
    viewer = SpectrumViewer()
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
