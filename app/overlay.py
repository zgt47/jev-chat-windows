# -*- coding: utf-8 -*-
"""浅色置顶回复助手：回复建议和独立设置页。发送始终由用户确认。"""
import threading
import time
from datetime import datetime
from math import isfinite
from types import SimpleNamespace

from PySide6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QMenu, QPushButton, QSlider, QSizeGrip, QSizePolicy,
    QStackedWidget, QStyle, QSystemTrayIcon, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CardWidget, CheckBox, ComboBox, EditableComboBox, FluentIcon as FIF,
    HyperlinkButton, IndeterminateProgressBar, LineEdit, PasswordLineEdit, PlainTextEdit,
    PrimaryPushButton, PushButton, ScrollArea, SpinBox, SwitchButton, Theme, TransparentToolButton,
    setCustomStyleSheet, setFont, setTheme, setThemeColor,
)

from app import chat_profiles, knowledge, settings
from app.version import VERSION
from core import jev_client, llm, providers
from core.questions import CHOICE_LABELS

_LOG_LINES = 300
_MUTED = "#68776f"
_GREEN = "#18794e"
_RELATIONSHIPS = [
    ("恋人", "romantic partners"), ("朋友", "friends"), ("同事", "colleagues"),
    ("家人", "family"), ("自定义", None),
]
_CHAT_TYPES = [
    ("自动识别", "auto"), ("私聊", "private"), ("群聊", "group"),
]
_STYLE_PRESETS = [
    ("自然克制", "正常、克制、短句、不装熟"),
    ("简短直接", "简短直接，少解释，不硬接话，不主动延伸"),
    ("轻松随意", "轻松随意，像正常聊天，不刻意热情，不装熟"),
    ("自定义", None),
]


def _choice(answers, name):
    return CHOICE_LABELS[name].get((answers.get(name) or {}).get("choice"), "暂未判断")


def _tray_icon(color=_GREEN, size=64):
    """托盘专用：大号白色 J，16px 缩放后也能辨认。"""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    margin = max(2, size // 16)
    painter.drawRoundedRect(
        margin, margin, size - margin * 2, size - margin * 2,
        size * 0.26, size * 0.26
    )
    painter.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", max(12, int(size * 0.58)), QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect().adjusted(0, -1, 0, 1), Qt.AlignCenter, "J")
    painter.end()
    return QIcon(pixmap)


class _FitCombo(ComboBox):
    """长名字不撑开窄布局。按钮上按当前宽度省略；条目仍是全文，findText 靠它。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def setText(self, text):
        self._full = text or ""
        QPushButton.setText(self, self._elide(self._full))
        if self._full and self.text() != self._full:
            self.setToolTip(self._full)

    def minimumSizeHint(self):
        hint = QPushButton.minimumSizeHint(self)
        return QSize(48, hint.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        shown = self._elide(self._full)
        if shown != self.text():
            QPushButton.setText(self, shown)
        if self._full and shown != self._full:
            self.setToolTip(self._full)

    def _elide(self, text):
        # 右侧箭头大约 28px。还没排上版时先按一个窄宽度省略，避免最小宽度被整句名字撑开。
        avail = self.width() - 36 if self.width() > 64 else 120
        return self.fontMetrics().elidedText(text, Qt.ElideRight, max(24, avail))



class _NoWheelSpinBox(SpinBox):
    """鼠标滚轮只滚页面，不意外改数字。"""
    def wheelEvent(self, event):
        event.ignore()


class _NoWheelSlider(QSlider):
    """透明度必须主动拖动/点击调整，滚轮只留给外层页面。"""
    def wheelEvent(self, event):
        event.ignore()


def _label(text="", size=14, color=None, bold=False, parent=None):
    label = BodyLabel(text, parent)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    setFont(label, size, QFont.DemiBold if bold else QFont.Normal)
    if color:
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(label, qss, qss)
    return label


def _tool(icon, title, callback, parent=None):
    button = TransparentToolButton(icon, parent)
    button.setFixedSize(32, 32)
    button.setToolTip(title)
    button.setAccessibleName(title)
    button.clicked.connect(callback)
    return button


class _Surface(CardWidget):
    def __init__(self, parent=None, accent=False):
        self.accent = accent
        super().__init__(parent)
        self.setBorderRadius(12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def _normalBackgroundColor(self):
        return QColor("#edf7f0" if self.accent else "#ffffff")

    def _hoverBackgroundColor(self):
        return self._normalBackgroundColor()

    def _pressedBackgroundColor(self):
        return self._normalBackgroundColor()


class _Fetched(QObject):
    """取模型列表的后台线程 → 主线程。"""
    done = Signal(object, list, str)


class _Tested(QObject):
    """接口实测结果 → 主线程。"""
    done = Signal(object, bool, str)


class _TitleBar(QWidget):
    """只有标题栏可拖动；拖出屏幕过多时自动收成悬浮球。"""
    def __init__(self, owner, parent):
        super().__init__(parent)
        self.owner = owner
        self._drag = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.window().pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag)
            if self.owner._main_window_dragged_too_far(event.globalPosition().toPoint()):
                self._drag = None
                self.owner._collapse_page()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None
        if not self.owner._collapsed:
            self.owner._keep_main_window_recoverable()
            self.owner._save_window_state()
        super().mouseReleaseEvent(event)


class _MainWindow(QWidget):
    """主窗：关闭按钮收进托盘；最小化交给 Windows 正常处理。"""
    def __init__(self, relayout, on_close):
        super().__init__()
        self._relayout = relayout
        self._on_close = on_close

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout(event.size().width(), event.size().height())

    def closeEvent(self, event):
        self._on_close(event)


class _BubbleWindow(QWidget):
    """独立悬浮球窗口：透明背景 + 抗锯齿绘制，不再裁主窗口。"""
    def __init__(self, owner):
        super().__init__(None)
        self.owner = owner
        self._press = None
        self._start = None
        self._moved = False
        self._color = QColor(_GREEN)

        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFixedSize(64, 64)
        self.setToolTip("左键打开 Jev；拖动可移动；右键可退出")

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(2, 2, 60, 60)

        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 13, QFont.DemiBold))
        painter.drawText(self.rect(), Qt.AlignCenter, "Jev")
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.owner._show_bubble_menu(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._start = self.pos()
            self._moved = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.LeftButton:
            now = event.globalPosition().toPoint()
            delta = now - self._press
            if delta.manhattanLength() > 4:
                self._moved = True
                wanted = self._start + delta
                self.move(self.owner._clamp_bubble_pos(wanted, now))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press is not None:
            moved = self._moved
            self._press = self._start = None
            self._moved = False
            if moved:
                self.owner._snap_bubble_to_edge(animated=True)
            else:
                self.owner._expand_page()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ReplyCard(_Surface):
    def __init__(self, owner, index, recommended=False, number=1, score=None):
        super().__init__(accent=recommended)
        box = QVBoxLayout(self)
        self.box = box
        box.setSpacing(10)
        top = QHBoxLayout()
        label = "推荐回复" if recommended else f"备选 {number}"
        if score is not None:
            label += f" · {round(score * 100)}%"
        top.addWidget(_label(label, 12, _GREEN if recommended else _MUTED, True))
        self.copyButton = _tool(FIF.COPY, "复制这条回复", lambda: owner._copy(index), self)
        self.copyButton.setFixedSize(24, 24)
        top.addWidget(self.copyButton)
        box.addLayout(top)
        self.text = _label(owner.cands[index], 15)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.addWidget(self.text)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.fillButton = (PrimaryPushButton if recommended else PushButton)("填入", self)
        self.fillButton.setAccessibleName(f"填入{'推荐回复' if recommended else f'备选 {number}'}")
        self.fillButton.clicked.connect(lambda: owner._fill(index))
        bottom.addWidget(self.fillButton)
        box.addLayout(bottom)
        self.set_compact(owner._compact)

    def set_available(self, enabled):
        self.fillButton.setEnabled(enabled)
        self.copyButton.setEnabled(enabled)

    def set_compact(self, compact):
        self.box.setContentsMargins(12, 8, 12, 8) if compact else self.box.setContentsMargins(16, 12, 16, 12)
        self.fillButton.setMinimumWidth(80 if compact else 100)


class Overlay:
    def __init__(self, on_fill, on_toggle_capture=None, on_target_change=None, result_of=None,
                 on_toggle_debug=None, on_regenerate=None):
        """result_of(会话名) → 那个会话上次的结果或 None；切着看别的会话时用它把旧结果放回来。
        on_target_change(会话名, 人名) → 用户在群里挑了回复对象。
        on_toggle_debug(开不开) → 开关调试视图那个独立窗口。"""
        self.app = QApplication.instance() or QApplication([])
        setTheme(Theme.LIGHT)
        setThemeColor(_GREEN, save=False)
        self.on_fill = on_fill
        self.on_toggle_capture = on_toggle_capture
        self.on_target_change = on_target_change
        self.on_toggle_debug = on_toggle_debug
        self.on_regenerate = on_regenerate
        self.result_of = result_of
        self.cands = []
        self.cards = []
        self._busy = False
        self._current = False
        self._compact = None  # 断点模式：None 保证 _relayout 第一次调用必定生效
        self._pageLayouts = []
        self._hintLabels = []
        self.feeds = {}  # {会话名: [排好版的记录]}
        self.counts = {}  # {会话名: 消息条数}
        self.hers = {}  # {会话名: 对方最近一句}
        self.targets = {}  # {会话名: ([发言人], 当前回复对象)}
        self._chat = ""  # 微信当前开着的会话
        self._shown = ""  # 界面上正在看的会话（浏览时和上面不一样）
        self._force_quit = False
        self._tray_notice_shown = False
        self._bubble_color = _GREEN
        self._trayIcon = _tray_icon(self._bubble_color)
        self._geometryAnimation = None
        self._bubbleAnimation = None
        self.app.setQuitOnLastWindowClosed(False)
        self.win = _MainWindow(self._relayout, self._close_requested)
        self.bubble = _BubbleWindow(self)
        self.win.setObjectName("assistantWindow")
        self.win.setWindowTitle("JevChat-Windows")
        self.win.setWindowIcon(self._trayIcon)
        flags = Qt.Window | Qt.FramelessWindowHint
        if settings.always_on_top():
            flags |= Qt.WindowStaysOnTopHint
        self.win.setWindowFlags(flags)
        self.win.setStyleSheet(
            "QWidget#assistantWindow { background: #f5f7f6; border: 1px solid #dce3de; border-radius: 14px; }"
        )
        self.win.setMinimumWidth(320)
        self.win.setMaximumWidth(640)
        outer = QVBoxLayout(self.win)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)
        header = _TitleBar(self, self.win)
        self.header = header
        title = QHBoxLayout(header)
        title.setContentsMargins(18, 12, 10, 10)
        title.setSpacing(8)
        self.titleLayout = title
        self.nameLabel = _label("Jev", 20, "#233c2f", True)
        self.nameLabel.setFixedWidth(40)
        self.nameLabel.setAttribute(Qt.WA_TransparentForMouseEvents)
        title.addWidget(self.nameLabel)
        title.addStretch(1)
        self.captureSwitch = SwitchButton(header)
        self.captureSwitch.setOnText("采集中")
        self.captureSwitch.setOffText("已暂停")
        self.captureSwitch.setToolTip("开启或暂停采集")
        self.captureSwitch.setAccessibleName("开启或暂停采集")
        self.captureSwitch.setChecked(True)
        self.captureSwitch.checkedChanged.connect(self._capture_toggled)
        title.addWidget(self.captureSwitch)
        self.topmostSwitch = SwitchButton(header)
        self.topmostSwitch.setOnText("置顶")
        self.topmostSwitch.setOffText("置顶")
        self.topmostSwitch.setToolTip("开启时窗口始终在最前；关闭后按普通窗口显示")
        self.topmostSwitch.setAccessibleName("窗口置顶")
        self.topmostSwitch.setChecked(settings.always_on_top())
        self.topmostSwitch.checkedChanged.connect(self._topmost_toggled)
        title.addWidget(self.topmostSwitch)
        self.settingsButton = _tool(FIF.SETTING, "全局设置", self.open_settings, header)
        title.addWidget(self.settingsButton)
        self.closeButton = _tool(FIF.CLOSE, "收起为悬浮球", self._collapse_page, header)
        title.addWidget(self.closeButton)
        outer.addWidget(header)
        self.updateBar = QWidget(self.win)
        update_row = QHBoxLayout(self.updateBar)
        update_row.setContentsMargins(18, 4, 8, 4)
        update_row.setSpacing(8)
        self.updateLabel = _label("", 12, _GREEN, True)
        update_row.addWidget(self.updateLabel, 1)
        self.updateLink = HyperlinkButton("", "去下载", self.updateBar)
        self.updateLink.setFixedHeight(24)
        update_row.addWidget(self.updateLink)
        closeUpdate = TransparentToolButton(FIF.CLOSE, self.updateBar)
        closeUpdate.setFixedSize(20, 20)
        closeUpdate.setToolTip("关闭更新提示")
        closeUpdate.setAccessibleName("关闭更新提示")
        closeUpdate.clicked.connect(lambda: self.updateBar.hide())
        update_row.addWidget(closeUpdate)
        self.updateBar.setFixedHeight(32)
        self.updateBar.hide()
        outer.addWidget(self.updateBar)
        self.pages = QStackedWidget(self.win)
        outer.addWidget(self.pages, 1)
        self._build_home()
        self._build_profile()
        self._build_knowledge()
        self._build_settings()
        self._refresh_profile_summary()
        self.footerBar = QWidget(self.win)
        footer = QHBoxLayout(self.footerBar)
        footer.setContentsMargins(20, 9, 8, 8)
        footer.addWidget(_label(f"仅填入输入框 · 发送由你确认 · v{VERSION}", 11, _MUTED), 1)
        grip = QSizeGrip(self.win)
        grip.setFixedSize(16, 16)
        footer.addWidget(grip, 0, Qt.AlignBottom)
        outer.addWidget(self.footerBar)
        self._collapsed = False
        self._expandedSize = None
        self._updateWasVisible = False
        screen = self.app.primaryScreen().availableGeometry()
        self._normalMinHeight = min(360, screen.height() - 32)
        self.win.setMinimumHeight(self._normalMinHeight)
        saved = settings.window_state()
        width = min(640, max(320, int(saved.get("w", min(440, screen.width() - 32)))))
        height = min(screen.height() - 48, max(self._normalMinHeight, int(saved.get("h", min(820, screen.height() - 48)))))
        self.win.resize(width, height)
        x = int(saved.get("x", screen.right() - width - 20))
        y = int(saved.get("y", screen.top() + 24))
        x = max(screen.left(), min(x, screen.right() - width + 1))
        y = max(screen.top(), min(y, screen.bottom() - height + 1))
        self.win.move(x, y)
        self._apply_transparency()
        self._setup_tray()
        self._relayout(self.win.width(), self.win.height())  # resizeEvent 补不到构造时这一次
        self.set_status("等待新消息" if settings.has_key() else "需要配置模型",
                        "idle" if settings.has_key() else "warning")
        self.win.show()

    def _scroll_page(self):
        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setAutoFillBackground(False)
        content = QWidget()
        content.setObjectName("pageContent")
        content.setStyleSheet("QWidget#pageContent { background: transparent; }")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 8, 20, 12)
        layout.setSpacing(14)
        scroll.setWidget(content)
        self.pages.addWidget(scroll)
        self._pageLayouts.append(layout)
        return scroll, layout

    def _relayout(self, w, h):
        """宽度跨过断点才重新摆布局（省事）；高度每次都重算，反正只是设个定高。"""
        compact = w < 400
        if compact != self._compact:
            self._compact = compact
            self._apply_compact(compact)
        self.feed.setFixedHeight(max(100, min(240, int(h * 0.25))))

    def _apply_compact(self, compact):
        """紧凑/常规两套间距和可见性；断点没变时不会被调用。"""
        self.captureSwitch.setOnText("" if compact else "采集中")
        self.captureSwitch.setOffText("" if compact else "已暂停")
        for label in self._hintLabels:
            label.setVisible(not compact)
        self.referenceNote.setVisible(bool(self.cands) and not compact)
        self._sync_model_fields()
        margins = (12, 8, 12, 12) if compact else (20, 8, 20, 12)
        for layout in self._pageLayouts:
            layout.setContentsMargins(*margins)
        for card in self.cards:
            card.set_compact(compact)

    def _build_home(self):
        self.home, body = self._scroll_page()
        heading = QHBoxLayout()
        heading.addWidget(_label("回复建议", 23, "#24382d", True), 1)
        self.updated = _label("", 11, _MUTED)
        self.updated.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        heading.addWidget(self.updated)
        body.addLayout(heading)

        reply_actions = QHBoxLayout()
        reply_actions.setSpacing(8)
        reply_actions.addStretch(1)
        self.manualAnalyzeButton = PrimaryPushButton("分析当前对话")
        self.manualAnalyzeButton.setToolTip("用当前聊天记录重新执行 Jev 判断、起草和排序")
        self.manualAnalyzeButton.clicked.connect(self._regenerate_clicked)
        self.manualAnalyzeButton.setEnabled(False)
        reply_actions.addWidget(self.manualAnalyzeButton)
        body.addLayout(reply_actions)

        chat_row = QHBoxLayout()
        chat_row.setSpacing(8)
        prefix = _label("当前会话", 12, _MUTED)
        prefix.setFixedWidth(56)
        chat_row.addWidget(prefix)
        self.chatBox = _FitCombo()
        self.chatBox.setPlaceholderText("尚未识别到会话")
        self.chatBox.setAccessibleName("当前会话")
        self.chatBox.setToolTip("聊天窗口切到哪个会话这里就跟到哪个；也可以自己选一个，只看它的记录和建议")
        self.chatBox.currentIndexChanged.connect(self._on_chat_selected)
        chat_row.addWidget(self.chatBox, 1)
        self.chatFollow = _label("", 11, _MUTED)
        self.chatFollow.setFixedWidth(52)
        self.chatFollow.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        chat_row.addWidget(self.chatFollow)
        body.addLayout(chat_row)
        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)
        self.profileSummary = _label("关系：尚未识别到会话", 12, _MUTED)
        profile_row.addWidget(self.profileSummary, 1)
        self.profileButton = PushButton("会话关系")
        self.profileButton.setToolTip("给当前聊天对象单独设置关系和说话风格")
        self.profileButton.clicked.connect(self.open_profile)
        profile_row.addWidget(self.profileButton)
        self.knowledgeButton = PushButton("知识库")
        self.knowledgeButton.setToolTip("管理会按关键词或常驻规则带入分析的本地笔记")
        self.knowledgeButton.clicked.connect(self.open_knowledge)
        profile_row.addWidget(self.knowledgeButton)
        body.addLayout(profile_row)
        self.targetRow = QWidget()  # 只有开了「群聊指定回复对象」且这个会话是群聊才露出来
        target_row = QHBoxLayout(self.targetRow)
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(8)
        target_prefix = _label("回复对象", 12, _MUTED)
        target_prefix.setFixedWidth(56)
        target_row.addWidget(target_prefix)
        self.targetBox = _FitCombo()
        self.targetBox.setAccessibleName("回复对象")
        self.targetBox.setToolTip("三条候选都按这个人来写；不选就跟着最近说话的那位")
        self.targetBox.currentIndexChanged.connect(self._on_target_selected)
        target_row.addWidget(self.targetBox, 1)
        self.atCheck = CheckBox("填入时带 @")
        self.atCheck.setChecked(True)
        self.atCheck.setToolTip("填入时在开头加「@名字 」。只是普通文字，不会变成真正的 @")
        target_row.addWidget(self.atCheck)
        self.targetRow.hide()
        body.addWidget(self.targetRow)
        self.status = _label("", 12, _MUTED)
        body.addWidget(self.status)
        self.progress = IndeterminateProgressBar()
        self.progress.setFixedHeight(3)
        self.progress.hide()
        body.addWidget(self.progress)
        self.context = QWidget()
        context_box = QVBoxLayout(self.context)
        context_box.setContentsMargins(0, 0, 0, 0)
        context_box.setSpacing(5)
        context_box.addWidget(_label("对方最近说", 11, _MUTED))
        self.latest = _label("", 14, "#42574a")
        self.latest.setTextInteractionFlags(Qt.TextSelectableByMouse)
        context_box.addWidget(self.latest)
        self.context.hide()
        body.addWidget(self.context)

        self.insight = _Surface()
        insight_box = QVBoxLayout(self.insight)
        insight_box.setContentsMargins(14, 12, 14, 12)
        insight_box.setSpacing(7)
        row = QHBoxLayout()
        self.insightTitle = _label("对话参考", 12, _MUTED)
        row.addWidget(self.insightTitle, 1)
        self.tension = _label("", 11)
        self.tension.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.tension)
        insight_box.addLayout(row)
        self.summary = _label("", 14, "#304c3c", True)
        insight_box.addWidget(self.summary)
        self.intent = _label("", 12, _MUTED)
        insight_box.addWidget(self.intent)
        self.judgmentExtra = _label("", 12, _MUTED)
        insight_box.addWidget(self.judgmentExtra)
        self.insight.setToolTip("根据当前聊天片段推测，可能理解有偏差。危险度为 0–9，并显示 Jev 的把握度。")
        self.insight.hide()
        body.addWidget(self.insight)

        self.empty = _Surface()
        empty_box = QVBoxLayout(self.empty)
        empty_box.setContentsMargins(24, 36, 24, 36)
        empty_box.setSpacing(14)
        symbol = _label("…", 30, _GREEN, True)
        symbol.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(symbol)
        self.emptyTitle = _label("等待对方的新消息", 17, "#304c3c", True)
        self.emptyTitle.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(self.emptyTitle)
        self.emptyHint = _label("保持聊天窗口打开。\n收到新消息后，回复建议会出现在这里。", 13, _MUTED)
        self.emptyHint.setAlignment(Qt.AlignCenter)
        empty_box.addWidget(self.emptyHint)
        self.setupButton = PrimaryPushButton("前往设置")
        self.setupButton.clicked.connect(self.open_settings)
        self.setupButton.setVisible(not settings.has_key())
        empty_box.addWidget(self.setupButton, 0, Qt.AlignHCenter)
        if not settings.has_key():
            self.emptyTitle.setText("先设置，再开始")
            self.emptyHint.setText("配置模型和关系背景，\n让建议更贴近你们的对话。")
        body.addWidget(self.empty)
        self.replyBox = QVBoxLayout()
        self.replyBox.setSpacing(10)
        body.addLayout(self.replyBox)
        self.referenceNote = _label("AI 建议仅供参考，按你的语气调整后再发送。", 11, _MUTED)
        self.referenceNote.hide()
        body.addWidget(self.referenceNote)

        self.historyButton = PushButton(FIF.HISTORY, "聊天记录")
        self.historyButton.clicked.connect(self._toggle_history)
        self.historyButton.setAccessibleName("展开或收起聊天记录")
        body.addWidget(self.historyButton)
        self.feed = PlainTextEdit()
        self.feed.setReadOnly(True)
        self.feed.setPlaceholderText("识别到的聊天内容会显示在这里")
        self.feed.setMaximumBlockCount(_LOG_LINES)
        self.feed.setFixedHeight(160)
        self.feed.hide()
        body.addWidget(self.feed)
        self._history_title()
        body.addStretch(1)

    def _build_profile(self):
        """会话关系独立页：只管理当前聊天对象，不混进全局设置。"""
        self.profilePage, body = self._scroll_page()
        heading = QHBoxLayout()
        heading.addWidget(_tool(FIF.RETURN, "返回回复建议", self._back_home))
        heading.addWidget(_label("会话关系", 23, "#24382d", True), 1)
        body.addLayout(heading)
        body.addWidget(_label(
            "关系和说话风格按聊天对象分别保存，只影响当前会话。",
            13, _MUTED
        ))

        profile = _Surface()
        box = QVBoxLayout(profile)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)

        box.addWidget(_label("当前会话", 13, _MUTED))
        self.profileChatLabel = _label("尚未识别到会话", 16, "#304c3c", True)
        box.addWidget(self.profileChatLabel)
        self.profileStateLabel = _label("", 12, _MUTED)
        box.addWidget(self.profileStateLabel)

        relation_label = _label("你们的关系", 13)
        box.addWidget(relation_label)
        self.profileRelationshipBox = ComboBox()
        self.profileRelationshipBox.setMinimumWidth(0)
        self.profileRelationshipBox.addItems([name for name, value in _RELATIONSHIPS])
        self.profileRelationshipBox.setAccessibleName("当前会话的关系")
        relation_label.setBuddy(self.profileRelationshipBox)
        box.addWidget(self.profileRelationshipBox)

        self.profileRelEdit = LineEdit()
        self.profileRelEdit.setPlaceholderText("例如：刚认识的朋友，正在慢慢熟悉")
        self.profileRelEdit.setAccessibleName("当前会话的自定义关系")
        box.addWidget(self.profileRelEdit)
        self.profileRelationshipBox.currentIndexChanged.connect(
            lambda index: self.profileRelEdit.setVisible(_RELATIONSHIPS[index][1] is None)
        )
        box.addWidget(self._hint("帮助助手把握对这个人的称呼、语气和回应分寸。"))

        type_label = _label("会话类型", 13)
        box.addWidget(type_label)
        self.profileChatTypeBox = ComboBox()
        self.profileChatTypeBox.setMinimumWidth(0)
        self.profileChatTypeBox.addItems([name for name, value in _CHAT_TYPES])
        self.profileChatTypeBox.setAccessibleName("当前会话类型")
        self.profileChatTypeBox.setToolTip("自动识别不确定时，可以手动指定私聊或群聊")
        type_label.setBuddy(self.profileChatTypeBox)
        box.addWidget(self.profileChatTypeBox)
        box.addWidget(self._hint("私聊会强制关闭群聊回复对象和 @；群聊会按群聊逻辑处理。"))

        style_label = _label("回复风格", 13)
        box.addWidget(style_label)
        style_tabs = QHBoxLayout()
        style_tabs.setSpacing(6)
        self.profileStyleButtons = []
        for label, value in _STYLE_PRESETS:
            button = PushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, v=value: self._style_tab_changed(v))
            style_tabs.addWidget(button)
            self.profileStyleButtons.append((button, value))
        box.addLayout(style_tabs)
        self.profileStyleEdit = LineEdit()
        self.profileStyleEdit.setPlaceholderText("例如：话少、不用标点、偶尔用 doge、不说客套话")
        self.profileStyleEdit.setAccessibleName("当前会话的自定义回复风格")
        self.profileStyleEdit.hide()
        box.addWidget(self.profileStyleEdit)
        box.addWidget(self._hint("默认「自然克制」：正常、克制、短句、不装熟。也可以选其他风格或自定义。"))

        alias_label = _label("别名（可选，每行一个）", 13)
        box.addWidget(alias_label)
        self.profileAliasesEdit = PlainTextEdit()
        self.profileAliasesEdit.setPlaceholderText("例如：老王\n王经理")
        self.profileAliasesEdit.setFixedHeight(62)
        box.addWidget(self.profileAliasesEdit)

        notes_label = _label("联系人备注（可选）", 13)
        box.addWidget(notes_label)
        self.profileNotesEdit = PlainTextEdit()
        self.profileNotesEdit.setPlaceholderText("例如：不喜欢被催；十月准备换工作；之前答应过周末去吃饭")
        self.profileNotesEdit.setAccessibleName("当前会话联系人备注")
        self.profileNotesEdit.setFixedHeight(86)
        box.addWidget(self.profileNotesEdit)
        box.addWidget(self._hint("只保存在本机；分析这个会话时会作为背景信息提供给 Jev 和起草模型。"))

        body.addWidget(profile)
        self.profileFeedback = _label("", 13, _GREEN)
        self.profileFeedback.hide()
        body.addWidget(self.profileFeedback)

        actions = QHBoxLayout()
        back = PushButton("返回")
        back.clicked.connect(self._back_home)
        actions.addWidget(back)
        actions.addStretch(1)
        self.profileSaveButton = PrimaryPushButton("保存当前会话")
        self.profileSaveButton.clicked.connect(self._save_profile)
        actions.addWidget(self.profileSaveButton)
        body.addLayout(actions)
        body.addStretch(1)

    def _build_knowledge(self):
        self.knowledgePage, body = self._scroll_page()
        heading = QHBoxLayout()
        heading.addWidget(_tool(FIF.RETURN, "返回回复建议", self._back_home))
        heading.addWidget(_label("知识库", 23, "#24382d", True), 1)
        body.addLayout(heading)
        body.addWidget(_label(
            "全部只保存在本机。常驻笔记每次都带；其它笔记在标题或标签命中会话标题/最近消息时带入，最多 5 条。",
            13, _MUTED
        ))

        editor = _Surface()
        box = QVBoxLayout(editor)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(10)

        box.addWidget(_label("已有笔记", 13))
        self.knowledgeList = ComboBox()
        self.knowledgeList.currentIndexChanged.connect(self._knowledge_selected)
        box.addWidget(self.knowledgeList)

        box.addWidget(_label("标题", 13))
        self.knowledgeTitleEdit = LineEdit()
        self.knowledgeTitleEdit.setPlaceholderText("例如：口味忌口")
        box.addWidget(self.knowledgeTitleEdit)

        box.addWidget(_label("标签", 13))
        self.knowledgeTagsEdit = LineEdit()
        self.knowledgeTagsEdit.setPlaceholderText("逗号分隔，例如：吃饭，周末")
        box.addWidget(self.knowledgeTagsEdit)

        box.addWidget(_label("正文", 13))
        self.knowledgeContentEdit = PlainTextEdit()
        self.knowledgeContentEdit.setPlaceholderText("写清楚事实本身，例如：不吃香菜，海鲜过敏")
        self.knowledgeContentEdit.setFixedHeight(110)
        box.addWidget(self.knowledgeContentEdit)

        flag_row = QHBoxLayout()
        self.knowledgeAlwaysCheck = CheckBox("常驻")
        self.knowledgeAlwaysCheck.setToolTip("开启后每次分析都会带上这条")
        flag_row.addWidget(self.knowledgeAlwaysCheck)
        self.knowledgeEnabledCheck = CheckBox("启用")
        self.knowledgeEnabledCheck.setChecked(True)
        flag_row.addWidget(self.knowledgeEnabledCheck)
        flag_row.addStretch(1)
        box.addLayout(flag_row)

        body.addWidget(editor)
        self.knowledgeFeedback = _label("", 12, _GREEN)
        self.knowledgeFeedback.hide()
        body.addWidget(self.knowledgeFeedback)

        actions = QHBoxLayout()
        new_btn = PushButton("新建")
        new_btn.clicked.connect(self._knowledge_new)
        actions.addWidget(new_btn)
        delete_btn = PushButton("删除当前")
        delete_btn.clicked.connect(self._knowledge_delete)
        actions.addWidget(delete_btn)
        actions.addStretch(1)
        save_btn = PrimaryPushButton("保存笔记")
        save_btn.clicked.connect(self._knowledge_save)
        actions.addWidget(save_btn)
        body.addLayout(actions)
        body.addStretch(1)
        self._knowledge_id = None

    def _refresh_knowledge(self, select_id=None):
        items = knowledge.notes()
        self.knowledgeList.blockSignals(True)
        self.knowledgeList.clear()
        self._knowledge_items = items
        self.knowledgeList.addItems([
            (("● " if n["enabled"] else "○ ") + (n["title"] or "（无标题）") + (" · 常驻" if n["always_on"] else ""))
            for n in items
        ])
        self.knowledgeList.blockSignals(False)
        if not items:
            self._knowledge_new()
            return
        index = 0
        if select_id:
            index = next((i for i, n in enumerate(items) if n["id"] == select_id), 0)
        self.knowledgeList.setCurrentIndex(index)
        self._knowledge_selected(index)

    def _knowledge_selected(self, index):
        items = getattr(self, "_knowledge_items", [])
        if not 0 <= index < len(items):
            return
        note = items[index]
        self._knowledge_id = note["id"]
        self.knowledgeTitleEdit.setText(note["title"])
        self.knowledgeTagsEdit.setText("，".join(note["tags"]))
        self.knowledgeContentEdit.setPlainText(note["content"])
        self.knowledgeAlwaysCheck.setChecked(note["always_on"])
        self.knowledgeEnabledCheck.setChecked(note["enabled"])

    def _knowledge_new(self):
        self._knowledge_id = None
        self.knowledgeTitleEdit.clear()
        self.knowledgeTagsEdit.clear()
        self.knowledgeContentEdit.clear()
        self.knowledgeAlwaysCheck.setChecked(False)
        self.knowledgeEnabledCheck.setChecked(True)
        self.knowledgeFeedback.hide()

    def _knowledge_save(self):
        raw_tags = self.knowledgeTagsEdit.text().replace("，", ",").replace("、", ",")
        tags = [x.strip() for x in raw_tags.split(",") if x.strip()]
        try:
            note_id = knowledge.save_note(
                self.knowledgeTitleEdit.text(),
                self.knowledgeContentEdit.toPlainText(),
                tags,
                self.knowledgeAlwaysCheck.isChecked(),
                self.knowledgeEnabledCheck.isChecked(),
                self._knowledge_id,
            )
        except Exception as exc:
            self.knowledgeFeedback.setText("保存失败：" + str(exc))
            self.knowledgeFeedback.show()
            return
        self._refresh_knowledge(note_id)
        self.knowledgeFeedback.setText("已保存")
        self.knowledgeFeedback.show()

    def _knowledge_delete(self):
        if not self._knowledge_id:
            return
        knowledge.delete_note(self._knowledge_id)
        self._refresh_knowledge()
        self.knowledgeFeedback.setText("已删除")
        self.knowledgeFeedback.show()

    def open_knowledge(self):
        self._refresh_knowledge()
        self.pages.setCurrentWidget(self.knowledgePage)
        self.settingsButton.setEnabled(True)

    def _build_settings(self):
        # 设置页单独做“固定头部 + 可滚内容”，返回/保存不再跟着内容滚走。
        self.settingsPage = QWidget()
        page = QVBoxLayout(self.settingsPage)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(0)

        fixed_header = QWidget(self.settingsPage)
        fixed_header.setObjectName("settingsFixedHeader")
        fixed_header.setStyleSheet(
            "QWidget#settingsFixedHeader { background:#f5f7f6; border-bottom:1px solid #e1e7e3; }"
        )
        header_box = QVBoxLayout(fixed_header)
        header_box.setContentsMargins(14, 9, 14, 8)
        header_box.setSpacing(4)

        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(_tool(FIF.RETURN, "返回回复建议", self._back_home))
        heading.addWidget(_label("全局设置", 23, "#24382d", True), 1)
        self.saveButton = PrimaryPushButton("保存全局设置")
        self.saveButton.clicked.connect(self._save)
        heading.addWidget(self.saveButton)
        header_box.addLayout(heading)

        self.settingsFeedback = _label("", 12, _GREEN)
        self.settingsFeedback.hide()
        header_box.addWidget(self.settingsFeedback)
        page.addWidget(fixed_header)

        scroll = ScrollArea(self.settingsPage)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setAutoFillBackground(False)

        content = QWidget()
        content.setObjectName("settingsPageContent")
        content.setStyleSheet("QWidget#settingsPageContent { background: transparent; }")
        body = QVBoxLayout(content)
        body.setContentsMargins(20, 12, 20, 12)
        body.setSpacing(14)
        scroll.setWidget(content)
        page.addWidget(scroll, 1)

        self.pages.addWidget(self.settingsPage)
        self._pageLayouts.append(body)

        body.addWidget(_label(
            "这里的设置对所有会话共用。关系和说话风格请在首页的「会话关系」里单独设置。",
            13, _MUTED
        ))

        preference = _Surface()
        box = QVBoxLayout(preference)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)
        box.addWidget(_label("通用", 16, "#304c3c", True))

        context_label = _label("参考上下文", 13)
        box.addWidget(context_label)
        self.contextBox = _NoWheelSpinBox()
        self.contextBox.setRange(3, 30)
        self.contextBox.setAccessibleName("参考的最近消息条数")
        context_label.setBuddy(self.contextBox)
        box.addWidget(self.contextBox)
        box.addWidget(self._hint(
            "生成和判断时看最近这么多条消息。太少会丢上下文，太多会稀释重点，建议 6–12。"
        ))

        auto_row = QHBoxLayout()
        auto_row.addWidget(_label("对方发消息时自动分析", 13), 1)
        self.autoAnalyzeSwitch = SwitchButton()
        self.autoAnalyzeSwitch.setOnText("开")
        self.autoAnalyzeSwitch.setOffText("关")
        auto_row.addWidget(self.autoAnalyzeSwitch)
        box.addLayout(auto_row)
        box.addWidget(self._hint("关闭后仍会采集聊天，但不会自动调用模型；需要时点首页「分析当前对话」。"))

        box.addWidget(_label("会话白名单", 13))
        self.whitelistEdit = PlainTextEdit()
        self.whitelistEdit.setPlaceholderText("每行一个关键词；留空 = 所有会话\n例如：家人群\n小王")
        self.whitelistEdit.setFixedHeight(76)
        box.addWidget(self.whitelistEdit)
        box.addWidget(self._hint("只限制自动分析；标题包含任一关键词才自动调用模型，手动分析不受限制。"))

        transparency_row = QHBoxLayout()
        transparency_row.addWidget(_label("界面透明度", 13), 1)
        self.transparencyValue = _label("0%", 12, _MUTED)
        self.transparencyValue.setFixedWidth(42)
        self.transparencyValue.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        transparency_row.addWidget(self.transparencyValue)
        box.addLayout(transparency_row)
        self.transparencySlider = _NoWheelSlider(Qt.Horizontal)
        self.transparencySlider.setRange(0, 40)
        self.transparencySlider.setSingleStep(1)
        self.transparencySlider.setPageStep(5)
        self.transparencySlider.setTickInterval(5)
        self.transparencySlider.setToolTip("0% = 完全不透明；拖动时立即预览，保存后才记住")
        self.transparencySlider.valueChanged.connect(self._preview_transparency)
        box.addWidget(self.transparencySlider)
        box.addWidget(self._hint(
            "0% = 完全不透明。拖动时界面立即变化；最高限制 40%，避免透明到看不清。"
        ))

        history_row = QHBoxLayout()
        history_row.addWidget(_label("记录聊天历史（仅本机）", 13), 1)
        self.historySwitch = SwitchButton()
        self.historySwitch.setOnText("开")
        self.historySwitch.setOffText("关")
        history_row.addWidget(self.historySwitch)
        box.addLayout(history_row)
        self.historyLimitBox = _NoWheelSpinBox()
        self.historyLimitBox.setRange(10, 100)
        self.historyLimitBox.setSuffix(" 条")
        box.addWidget(self.historyLimitBox)
        box.addWidget(self._hint("默认关闭。开启后按会话保存最近聊天，重启后仍可给模型参考；文件只在程序目录旁。"))

        target_row = QHBoxLayout()
        target_row.addWidget(_label("群聊指定回复对象", 13), 1)
        self.targetSwitch = SwitchButton()
        self.targetSwitch.setOnText("开")
        self.targetSwitch.setOffText("关")
        self.targetSwitch.setAccessibleName("群聊指定回复对象")
        target_row.addWidget(self.targetSwitch)
        box.addLayout(target_row)
        box.addWidget(self._hint(
            "开了以后群聊里可以选回复给谁，候选会针对 TA 写，填入时可带 @。关了就正常回复。"
        ))

        update_row = QHBoxLayout()
        update_row.addWidget(_label("启动时检查更新", 13), 1)
        self.updateSwitch = SwitchButton()
        self.updateSwitch.setOnText("开")
        self.updateSwitch.setOffText("关")
        self.updateSwitch.setAccessibleName("启动时检查更新")
        update_row.addWidget(self.updateSwitch)
        box.addLayout(update_row)
        box.addWidget(self._hint(
            "只向 GitHub 查最新版本号，不发送任何数据。国内访问 GitHub 慢的话关掉也行。"
        ))

        debug_row = QHBoxLayout()
        debug_row.addWidget(_label("调试视图", 13), 1)
        self.debugSwitch = SwitchButton()
        self.debugSwitch.setOnText("开")
        self.debugSwitch.setOffText("关")
        self.debugSwitch.setAccessibleName("调试视图")
        self.debugSwitch.checkedChanged.connect(self._debug_toggled)
        debug_row.addWidget(self.debugSwitch)
        box.addLayout(debug_row)
        box.addWidget(self._hint(
            "另开一个窗口实时显示截到的画面和识别框：绿 = 我、蓝 = 对方、灰 = 过滤掉的灰字、"
            "红 = 当成图片丢掉、黄 = 小字丢掉。只在内存里画，不存图。"
        ))
        body.addWidget(preference)

        models = _Surface()
        box = QVBoxLayout(models)
        box.setContentsMargins(16, 16, 16, 18)
        box.setSpacing(12)
        box.addWidget(_label("模型", 16, "#304c3c", True))
        self._fetched = _Fetched()
        self._fetched.done.connect(self._models_fetched)
        self._tested = _Tested()
        self._tested.done.connect(self._connection_tested)
        self.jev = self._model_group(box, "判断 · Jev", "jev", providers.JEV_PROVIDERS)
        box.addWidget(self._hint(
            "判断意图、紧张度，并给三条候选排序。OpenRouter 走专用接口，其他预设走 System One。"
        ))
        self.draft = self._model_group(box, "起草 · 语言模型", "draft", providers.DRAFT_PROVIDERS)
        box.addWidget(self._hint(
            "写那三条候选。OpenAI / Anthropic / Gemini 三种接口都走各自官方 SDK。"
            "默认 DeepSeek 官网直连，国内最快。"
        ))

        think_row = QHBoxLayout()
        think_row.addWidget(_label("起草时开启思考模式", 13), 1)
        self.thinkingSwitch = SwitchButton()
        self.thinkingSwitch.setOnText("开")
        self.thinkingSwitch.setOffText("关")
        self.thinkingSwitch.setAccessibleName("起草时开启思考模式")
        think_row.addWidget(self.thinkingSwitch)
        box.addLayout(think_row)
        box.addWidget(self._hint(
            "关：秒回，够用。开：模型先想再写，更斟酌但慢好几倍、贵一些。"
            "只有 " + " / ".join(providers.THINKING) + " 认这个开关。"
        ))
        body.addWidget(models)

        body.addWidget(self._hint("修改完成后，可随时使用顶部固定栏的「保存全局设置」。"))
        body.addStretch(1)
        self._load_settings()

    def _hint(self, text):
        label = _label(text, 12, _MUTED)
        self._hintLabels.append(label)
        return label

    def _model_group(self, box, title, kind, table):
        """一组「来源 / 地址（仅自定义） / 密钥 / 模型」控件。"""
        group = SimpleNamespace(
            kind=kind,
            table=table,
            ids=list(table),
            keyTitle="判断" if kind == "jev" else "起草",
            stored_key=lambda k=kind: (settings.jev_key() if k == "jev" else settings.llm_key()),
        )
        heading = QHBoxLayout()
        heading.addWidget(_label(title, 14, "#304c3c", True), 1)
        group.keyState = _label("", 12, _GREEN)
        group.keyState.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        heading.addWidget(group.keyState)
        box.addLayout(heading)

        source_label = _label("来源", 13)
        box.addWidget(source_label)
        group.providerBox = ComboBox()
        group.providerBox.setMinimumWidth(0)
        group.providerBox.addItems([table[i].name for i in group.ids])
        group.providerBox.setAccessibleName(f"{title} 来源")
        source_label.setBuddy(group.providerBox)
        box.addWidget(group.providerBox)

        group.baseLabel = _label("Base URL", 13)
        box.addWidget(group.baseLabel)
        group.baseEdit = LineEdit()
        group.baseEdit.setPlaceholderText(
            "https://你的服务" if kind == "jev" else "https://你的服务/v1"
        )
        group.baseEdit.setAccessibleName(f"{title} 自定义 Base URL")
        group.baseLabel.setBuddy(group.baseEdit)
        box.addWidget(group.baseEdit)

        key_label = _label("密钥", 13)
        box.addWidget(key_label)
        group.keyEdit = PasswordLineEdit()
        group.keyEdit.setAccessibleName(f"{title} API 密钥")
        key_label.setBuddy(group.keyEdit)
        group.keyEdit.returnPressed.connect(self._save)
        box.addWidget(group.keyEdit)
        box.addWidget(self._hint(
            "上面选哪家就填哪家的密钥；切换来源后重填这一把。" if kind == "jev"
            else "上面选哪家就填哪家的密钥；换来源重填一次，只存这一把。"
        ))

        model_label = _label("模型", 13)
        box.addWidget(model_label)
        row = QHBoxLayout()
        row.setSpacing(8)
        group.modelBox = EditableComboBox()
        group.modelBox.setMinimumWidth(0)
        group.modelBox.setAccessibleName(f"{title} 模型")
        model_label.setBuddy(group.modelBox)
        row.addWidget(group.modelBox, 1)
        group.fetchButton = PushButton("获取模型")
        group.fetchButton.setAccessibleName(f"获取{title}的可用模型列表")
        group.fetchButton.clicked.connect(lambda: self._fetch_models(group))
        row.addWidget(group.fetchButton)
        group.testButton = PushButton("测试")
        group.testButton.setAccessibleName(f"测试{title}接口")
        group.testButton.clicked.connect(lambda: self._test_connection(group))
        row.addWidget(group.testButton)
        box.addLayout(row)
        group.status = _label("", 12, _MUTED)
        box.addWidget(group.status)
        group.providerBox.currentIndexChanged.connect(lambda _: self._provider_changed(group))
        return group

    @staticmethod
    def _provider_of(group):
        return group.ids[max(0, group.providerBox.currentIndex())]

    def _provider_changed(self, group):
        provider = self._provider_of(group)
        saved = settings.jev_provider() if group.kind == "jev" else settings.draft_provider()
        stored = settings.jev_model() if group.kind == "jev" else settings.draft_model()
        group.modelBox.clear()
        group.modelBox.setText(stored if provider == saved else group.table[provider].default)
        if group.kind == "jev":
            if provider in providers.JEV_CUSTOM:
                group.baseEdit.setText(settings.jev_custom_base_url())
            else:
                group.baseEdit.clear()
        else:
            if provider in providers.CUSTOM:
                group.baseEdit.setText(settings.draft_base_url() if saved == provider else "")
            else:
                group.baseEdit.clear()
        group.status.setText("")
        self._sync_model_fields()

    def _sync_model_fields(self):
        for group in (self.jev, self.draft):
            provider = self._provider_of(group)
            name = group.table[provider].name
            configured = bool(group.stored_key())
            group.keyState.setText("已配置" if configured else "未配置")
            group.keyEdit.setPlaceholderText(
                "已配置，留空保留" if configured else f"输入 {name} API 密钥")
            if self._compact:
                name = group.providerBox.fontMetrics().elidedText(name, Qt.ElideRight, 180)
            group.providerBox.setText(name)

        jev_custom = self._provider_of(self.jev) in providers.JEV_CUSTOM
        self.jev.baseLabel.setVisible(jev_custom)
        self.jev.baseEdit.setVisible(jev_custom)
        draft_custom = self._provider_of(self.draft) in providers.CUSTOM
        self.draft.baseLabel.setVisible(draft_custom)
        self.draft.baseEdit.setVisible(draft_custom)

    def _fetch_models(self, group):
        provider = self._provider_of(group)
        if group.kind == "jev":
            custom = provider in providers.JEV_CUSTOM
        else:
            custom = provider in providers.CUSTOM
        base = group.baseEdit.text().strip() if custom else None
        key = group.keyEdit.text().strip() or group.stored_key()
        if not key:
            group.status.setText("先填密钥")
            return
        if custom and not base:
            group.status.setText("先填 Base URL")
            return
        group.status.setText("获取中…")
        group.fetchButton.setEnabled(False)
        threading.Thread(
            target=lambda: self._list_models(group, provider, key, base),
            daemon=True,
        ).start()

    def _list_models(self, group, provider, key, base):
        try:
            if group.kind == "jev":
                models = jev_client.list_models(provider, key, base_url=base)
            else:
                spec = providers.DRAFT_PROVIDERS[provider]
                models = llm.list_models(spec.protocol, base or spec.base, key, headers=spec.headers)
                if spec.keep:
                    models = [m for m in models if spec.keep(m)]
            reason = "" if models else "这个来源没有返回模型列表，可直接手动输入模型名后保存"
        except Exception as exc:
            models, reason = [], str(exc)[:160]
        self._fetched.done.emit(group, models, reason)

    def _models_fetched(self, group, models, reason):
        group.fetchButton.setEnabled(True)
        if not models:
            group.status.setText(reason or "获取失败；如果知道模型名，可以直接手动输入并保存")
            return
        current = group.modelBox.text().strip()
        group.modelBox.clear()
        group.modelBox.addItems(models)
        if current in models:
            group.modelBox.setCurrentIndex(models.index(current))
        else:
            group.modelBox.setText(current)
        group.status.setText(f"共 {len(models)} 个")

    def _test_connection(self, group):
        provider = self._provider_of(group)
        custom = provider in (providers.JEV_CUSTOM if group.kind == "jev" else providers.CUSTOM)
        base = group.baseEdit.text().strip() if custom else None
        key = group.keyEdit.text().strip() or group.stored_key()
        model = group.modelBox.text().strip()
        if not key:
            group.status.setText("先填密钥")
            return
        if not model:
            group.status.setText("先填模型")
            return
        if custom and not base:
            group.status.setText("先填 Base URL")
            return
        group.status.setText("正在实测接口…")
        group.testButton.setEnabled(False)
        threading.Thread(
            target=self._test_connection_bg,
            args=(group, provider, key, model, base),
            daemon=True,
        ).start()

    def _test_connection_bg(self, group, provider, key, model, base):
        start = time.perf_counter()
        try:
            if group.kind == "jev":
                state = {
                    "chat": {
                        "relationship": "friends",
                        "messages": [{"from": "her", "text": "在吗"}],
                        "latest_from": "her",
                        "is_group": False,
                    }
                }
                questions = {
                    "test": {
                        "type": "noul",
                        "instructions": "Is the latest message a direct question?",
                        "criteria": {"true": "It is a direct question.", "false": "It is not a direct question."},
                    }
                }
                # 测试时不能依赖环境变量里旧 key，临时覆盖后再恢复。
                from core.providers import JEV_ENV
                import os
                old = os.environ.get(JEV_ENV)
                os.environ[JEV_ENV] = key
                try:
                    jev_client.ask(state, questions, timeout=12, provider=provider, model=model, base_url=base)
                finally:
                    if old is None:
                        os.environ.pop(JEV_ENV, None)
                    else:
                        os.environ[JEV_ENV] = old
            else:
                spec = providers.DRAFT_PROVIDERS[provider]
                llm.chat(
                    spec.protocol, base or spec.base, key, model,
                    "只做接口连通测试。", ["只回复 OK"],
                    temperature=0.2, max_tokens=12, thinking=False,
                    extra_body=spec.extra(False), headers=spec.headers, timeout=12,
                )
            ms = round((time.perf_counter() - start) * 1000)
            self._tested.done.emit(group, True, f"连接成功 · {ms} ms")
        except Exception as exc:
            self._tested.done.emit(group, False, "连接失败：" + str(exc)[:160])

    def _connection_tested(self, group, ok, message):
        group.testButton.setEnabled(True)
        group.status.setText(message)

    def _set_group(self, group, provider, model):
        group.providerBox.blockSignals(True)
        group.providerBox.setCurrentIndex(group.ids.index(provider))
        group.providerBox.blockSignals(False)
        group.keyEdit.clear()
        group.modelBox.clear()
        group.modelBox.setText(model)
        group.status.setText("")

    def _load_settings(self):
        self.contextBox.setValue(settings.context())
        self.autoAnalyzeSwitch.setChecked(settings.auto_analyze())
        self.whitelistEdit.setPlainText("\n".join(settings.whitelist()))
        self.transparencySlider.blockSignals(True)
        self.transparencySlider.setValue(settings.transparency())
        self.transparencySlider.blockSignals(False)
        self.transparencyValue.setText(f"{settings.transparency()}%")
        self.historySwitch.setChecked(settings.record_history())
        self.historyLimitBox.setValue(settings.history_limit())
        self.targetSwitch.setChecked(settings.reply_target())
        self._set_group(self.jev, settings.jev_provider(), settings.jev_model())
        self._set_group(self.draft, settings.draft_provider(), settings.draft_model())
        self.jev.baseEdit.setText(settings.jev_custom_base_url())
        self.draft.baseEdit.setText(settings.draft_base_url())
        self.thinkingSwitch.setChecked(settings.thinking())
        self.updateSwitch.setChecked(settings.check_update())
        self.set_debug_switch(settings.debug_view())
        self._sync_model_fields()
        self.settingsFeedback.hide()

    def _save(self):
        jev_provider = self._provider_of(self.jev)
        draft_provider = self._provider_of(self.draft)
        jev_base = self.jev.baseEdit.text().strip()
        draft_base = self.draft.baseEdit.text().strip()

        if jev_provider in providers.JEV_CUSTOM and not jev_base:
            self._settings_feedback("自定义 System One 要填 Base URL。", error=True)
            self.jev.baseEdit.setFocus()
            return
        if draft_provider in providers.CUSTOM and not draft_base:
            self._settings_feedback("自定义起草来源要填 Base URL。", error=True)
            self.draft.baseEdit.setFocus()
            return

        for group, provider in ((self.jev, jev_provider), (self.draft, draft_provider)):
            name = group.table[provider].name
            if not group.keyEdit.text().strip() and not group.stored_key():
                self._settings_feedback(f"请先填写 {group.keyTitle} 的 API 密钥。", error=True)
                group.keyEdit.setFocus()
                return
            if not group.modelBox.text().strip():
                self._settings_feedback(f"{name} 请填写模型名称，或先点获取模型。", error=True)
                group.modelBox.setFocus()
                return

        try:
            settings.save(
                context_n=self.contextBox.value(),
                jev_provider_text=jev_provider,
                jev_key_text=self.jev.keyEdit.text().strip() or None,
                jev_model_text=self.jev.modelBox.text().strip(),
                jev_base_url_text=(jev_base if jev_provider in providers.JEV_CUSTOM else None),
                draft_provider_text=draft_provider,
                llm_key_text=self.draft.keyEdit.text().strip() or None,
                draft_model_text=self.draft.modelBox.text().strip(),
                draft_base_url_text=(draft_base if draft_provider in providers.CUSTOM else None),
                reply_target_on=self.targetSwitch.isChecked(),
                auto_analyze_on=self.autoAnalyzeSwitch.isChecked(),
                whitelist_items=self.whitelistEdit.toPlainText().splitlines(),
                transparency_n=self.transparencySlider.value(),
                record_history_on=self.historySwitch.isChecked(),
                history_limit_n=self.historyLimitBox.value(),
                thinking_on=self.thinkingSwitch.isChecked(),
                check_update_on=self.updateSwitch.isChecked(),
            )
        except Exception:
            self._settings_feedback("保存失败，请检查配置文件是否可写后重试。", error=True)
            return

        self._load_settings()
        self._apply_transparency()
        self._render_targets()
        self._settings_feedback("全局设置已保存，将用于下一次回复。")
        self.setupButton.hide()
        if not self.cands and not self._busy:
            self._empty_text()
            self.set_status("设置已就绪，等待新消息", "idle")

    def _load_profile(self):
        chat = self._shown or self._chat
        self.profileChatLabel.setText(chat or "尚未识别到会话")
        self.profileSaveButton.setEnabled(bool(chat))
        profile = chat_profiles.get(chat)

        relationship = profile["relationship"]
        index = next(
            (i for i, (_, value) in enumerate(_RELATIONSHIPS) if value == relationship),
            len(_RELATIONSHIPS) - 1,
        )
        self.profileRelationshipBox.blockSignals(True)
        self.profileRelationshipBox.setCurrentIndex(index)
        self.profileRelationshipBox.blockSignals(False)
        custom = _RELATIONSHIPS[index][1] is None
        self.profileRelEdit.setText(relationship if custom else "")
        self.profileRelEdit.setVisible(custom)

        chat_type = profile.get("chat_type", "auto")
        type_index = next((i for i, (_, value) in enumerate(_CHAT_TYPES) if value == chat_type), 0)
        self.profileChatTypeBox.setCurrentIndex(type_index)

        style = profile.get("style") or _STYLE_PRESETS[0][1]
        preset_index = next((i for i, (_, value) in enumerate(_STYLE_PRESETS) if value == style), len(_STYLE_PRESETS) - 1)
        for i, (button, value) in enumerate(self.profileStyleButtons):
            button.setChecked(i == preset_index)
        custom_style = _STYLE_PRESETS[preset_index][1] is None
        self.profileStyleEdit.setText(style if custom_style else "")
        self.profileStyleEdit.setVisible(custom_style)
        self.profileAliasesEdit.setPlainText("\n".join(profile.get("aliases", [])))
        self.profileNotesEdit.setPlainText(profile.get("notes", ""))

        if not chat:
            state = "先切到一个聊天会话，再保存关系资料。"
        elif profile["saved"]:
            state = "这个会话已有独立关系设置。"
        elif profile["legacy"]:
            state = "这个会话尚未单独设置，当前暂时沿用旧版的全局关系。"
        else:
            state = "这个会话尚未单独设置，当前使用默认关系「朋友」。"
        self.profileStateLabel.setText(state)
        self.profileFeedback.hide()

    def _save_profile(self):
        chat = self._shown or self._chat
        if not chat:
            self._profile_feedback("尚未识别到会话，先切到一个聊天对象。", error=True)
            return

        relationship = _RELATIONSHIPS[self.profileRelationshipBox.currentIndex()][1]
        relationship = relationship or self.profileRelEdit.text().strip()
        if not relationship:
            self._profile_feedback("自定义关系不能为空。", error=True)
            self.profileRelEdit.setFocus()
            return

        chat_type = _CHAT_TYPES[self.profileChatTypeBox.currentIndex()][1]
        style = next((value for button, value in self.profileStyleButtons if button.isChecked()), _STYLE_PRESETS[0][1])
        if style is None:
            style = self.profileStyleEdit.text().strip()
            if not style:
                self._profile_feedback("自定义回复风格不能为空。", error=True)
                self.profileStyleEdit.setFocus()
                return

        try:
            chat_profiles.save(
                chat, relationship, style, chat_type,
                self.profileNotesEdit.toPlainText().strip(),
                self.profileAliasesEdit.toPlainText().splitlines(),
            )
        except Exception:
            self._profile_feedback("保存失败，请检查程序目录是否可写。", error=True)
            return

        self._load_profile()
        self._refresh_profile_summary()
        self._render_targets()
        self._profile_feedback(f"已保存「{chat}」的会话关系。")
        self.set_status("会话关系已保存，将用于下一次回复", "success")

    def _style_tab_changed(self, value):
        """回复风格选项卡：预设直接用；选自定义才显示输入框。"""
        for button, preset in self.profileStyleButtons:
            button.setChecked(preset == value)
        self.profileStyleEdit.setVisible(value is None)
        if value is None:
            self.profileStyleEdit.setFocus()

    def _profile_feedback(self, text, error=False):
        color = "#b44832" if error else _GREEN
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(self.profileFeedback, qss, qss)
        self.profileFeedback.setText(text)
        self.profileFeedback.show()

    def _refresh_profile_summary(self):
        chat = self._shown or self._chat
        if not chat:
            self.profileSummary.setText("关系：尚未识别到会话")
            self.profileButton.setEnabled(False)
            return
        self.profileButton.setEnabled(True)
        profile = chat_profiles.get(chat)
        relationship = profile["relationship"]
        name = next((label for label, value in _RELATIONSHIPS if value == relationship), relationship)
        type_name = next((label for label, value in _CHAT_TYPES if value == profile.get("chat_type", "auto")), "自动识别")
        suffix = "" if profile["saved"] else " · 未单独设置"
        self.profileSummary.setText(f"关系：{name} · {type_name}{suffix}")

    def open_profile(self):
        self._load_profile()
        self.pages.setCurrentWidget(self.profilePage)
        self.settingsButton.setEnabled(True)
        self.profileRelationshipBox.setFocus()

    def _debug_toggled(self, on):
        """调试视图独立于「保存设置」：拨一下就开窗/收窗，顺手落盘，重启还在。"""
        settings.save(debug_view_on=on)
        if self.on_toggle_debug:
            self.on_toggle_debug(on)

    def set_debug_switch(self, on):
        """调试窗被用户直接关掉时把开关拨回去；屏蔽信号，免得又回调一圈。"""
        self.debugSwitch.blockSignals(True)
        self.debugSwitch.setChecked(on)
        self.debugSwitch.blockSignals(False)

    def _settings_feedback(self, text, error=False):
        color = "#b44832" if error else _GREEN
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(self.settingsFeedback, qss, qss)
        self.settingsFeedback.setText(text)
        self.settingsFeedback.show()

    def open_settings(self):
        if self.pages.currentWidget() != self.settingsPage:
            self._load_settings()
        self.pages.setCurrentWidget(self.settingsPage)
        self.settingsButton.setEnabled(False)
        (self.contextBox if settings.has_key() else self.jev.keyEdit).setFocus()

    def _back_home(self):
        if self.pages.currentWidget() == self.settingsPage:
            self._apply_transparency(settings.transparency())
        self.jev.keyEdit.clear()
        self.draft.keyEdit.clear()
        self.pages.setCurrentWidget(self.home)
        self.settingsButton.setEnabled(True)

    def _sync_reply_actions(self):
        """首页只保留一个明确动作：分析当前对话。"""
        has_chat = bool(self._shown or self._chat)
        browsing = bool(self._chat) and self._shown != self._chat
        self.manualAnalyzeButton.setEnabled(has_chat and not browsing and not self._busy)

    def _regenerate_clicked(self):
        chat = self._shown or self._chat
        if not chat or self._busy:
            return
        if self._chat and chat != self._chat:
            self.set_status("正在浏览其他会话，切回这个聊天后再分析。", "warning")
            return
        if self.on_regenerate:
            self.on_regenerate(chat)

    def clear_suggestions(self, status_text="建议已清空，可直接重新生成。"):
        """只清当前显示的 AI 结果；聊天记录、会话关系和采集内容都保留。"""
        self.cands = []
        self._current = False
        self._clear_cards()
        self.insight.hide()
        self.referenceNote.hide()
        self.empty.show()
        self.updated.setText("")
        self.emptyTitle.setText("回复建议已清空")
        self.emptyHint.setText("聊天记录还在。\n点上方「分析当前对话」即可重新分析。")
        self.setupButton.hide()
        self.set_status(status_text, "idle")
        self._sync_reply_actions()

    def _fill(self, index):
        if self._busy or not self._current or index >= len(self.cands):
            return
        try:
            self.on_fill(self.cands[index])
        except Exception as e:
            # 状态栏保持友好文案；真实原因和压缩堆栈进聊天记录，认得出是哪一步炸的
            import traceback
            self.set_status("未能填入，请确认聊天窗口可用后重试，或复制回复。", "error")
            self.log(f"[填入失败] {type(e).__name__}: {e}")
            self.log(f"[填入失败堆栈] {' '.join(traceback.format_exc().split())[:300]}")
            return
        self.set_status("已尝试填入，请确认内容后发送。", "success")

    def _copy(self, index):
        if self._busy or not self._current or index >= len(self.cands):
            return
        self.app.clipboard().setText(self.cands[index])
        self.set_status("回复已复制，可粘贴并修改。", "success")

    def _topmost_toggled(self, on):
        """运行中切换是否始终置顶，并保存到全局设置。"""
        settings.save(always_on_top_on=on)
        pos = self.win.pos()
        self.win.setWindowFlag(Qt.WindowStaysOnTopHint, bool(on))
        self.win.show()
        self.win.move(pos)

    def _screen_for_point(self, point):
        """按点选择显示器；点在屏幕外时选距离最近的显示器。"""
        screen = self.app.screenAt(point)
        if screen is not None:
            return screen

        best = None
        best_dist = None
        for candidate in self.app.screens():
            g = candidate.availableGeometry()
            dx = max(g.left() - point.x(), 0, point.x() - g.right())
            dy = max(g.top() - point.y(), 0, point.y() - g.bottom())
            dist = dx * dx + dy * dy
            if best_dist is None or dist < best_dist:
                best, best_dist = candidate, dist
        return best or self.app.primaryScreen()

    @staticmethod
    def _clamp_to_geometry(pos, size, geometry, margin=6):
        """把完整窗口夹进显示器可用区。"""
        left = geometry.left() + margin
        top = geometry.top() + margin
        right = max(left, geometry.right() - size.width() - margin + 1)
        bottom = max(top, geometry.bottom() - size.height() - margin + 1)
        return QPoint(
            max(left, min(pos.x(), right)),
            max(top, min(pos.y(), bottom)),
        )

    def _clamp_bubble_pos(self, wanted, cursor=None):
        """拖动过程中球保持完整可见；松手再吸附半隐藏。"""
        probe = cursor or QPoint(
            wanted.x() + self.bubble.width() // 2,
            wanted.y() + self.bubble.height() // 2,
        )
        screen = self._screen_for_point(probe)
        return self._clamp_to_geometry(
            wanted, self.bubble.size(), screen.availableGeometry(), margin=4
        )

    def _bubble_snap_pos(self, pos, screen, force=False):
        """靠近左右边缘才吸附并半隐藏；离边缘较远就保持自由位置。"""
        area = screen.availableGeometry()
        size = self.bubble.size()
        half = size.width() // 2

        # 先保证自由位置完整可见。
        free = self._clamp_to_geometry(pos, size, area, margin=4)
        center_x = free.x() + size.width() // 2
        left_distance = center_x - area.left()
        right_distance = area.right() - center_x

        snap_distance = 72
        if not force and min(left_distance, right_distance) > snap_distance:
            return free

        if left_distance <= right_distance:
            x = area.left() - half
        else:
            x = area.right() - half + 1

        y = max(
            area.top() + 6,
            min(free.y(), area.bottom() - size.height() - 6 + 1),
        )
        return QPoint(x, y)

    def _save_bubble_state(self):
        try:
            pos = self.bubble.pos()
            settings.save_bubble_state(pos.x(), pos.y())
        except Exception:
            pass

    def _snap_bubble_to_edge(self, animated=False):
        if not self._collapsed:
            return
        center = self.bubble.frameGeometry().center()
        screen = self.bubble.screen() or self._screen_for_point(center)
        target = self._bubble_snap_pos(self.bubble.pos(), screen)
        if animated and target != self.bubble.pos():
            if self._bubbleAnimation is not None:
                self._bubbleAnimation.stop()
            anim = QPropertyAnimation(self.bubble, b"pos", self.bubble)
            anim.setDuration(120)
            anim.setStartValue(self.bubble.pos())
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(self._save_bubble_state)
            self._bubbleAnimation = anim
            anim.start()
        else:
            self.bubble.move(target)
            self._save_bubble_state()

    def _restored_bubble_pos(self):
        """优先恢复上一次悬浮球位置；自由位置保持自由，贴边位置保持贴边。"""
        saved = settings.bubble_state()
        if "x" in saved and "y" in saved:
            pos = QPoint(int(saved["x"]), int(saved["y"]))
            probe = QPoint(pos.x() + 31, pos.y() + 31)
            screen = self._screen_for_point(probe)
            area = screen.availableGeometry()
            half = self.bubble.width() // 2

            # 旧位置本身已经是半隐藏吸附状态，原样贴回对应边。
            if pos.x() <= area.left():
                return QPoint(
                    area.left() - half,
                    max(area.top() + 6, min(pos.y(), area.bottom() - self.bubble.height() - 5)),
                )
            if pos.x() + self.bubble.width() >= area.right():
                return QPoint(
                    area.right() - half + 1,
                    max(area.top() + 6, min(pos.y(), area.bottom() - self.bubble.height() - 5)),
                )

            return self._clamp_to_geometry(pos, self.bubble.size(), area, margin=4)

        # 第一次进入悬浮球，默认吸附到距离主窗口最近的一边。
        screen = self._screen_for_point(self.win.frameGeometry().center())
        return self._bubble_snap_pos(self.win.pos(), screen, force=True)

    def _expanded_pos_from_bubble(self, bubble_pos, bubble_size, target_size, screen):
        """悬浮球在左边就贴左展开，在右边就贴右展开；纵向围绕球展开并自动夹回屏幕。"""
        area = screen.availableGeometry()
        bubble_center = QPoint(
            bubble_pos.x() + bubble_size.width() // 2,
            bubble_pos.y() + bubble_size.height() // 2,
        )

        if bubble_center.x() <= area.center().x():
            x = area.left() + 8
        else:
            x = area.right() - target_size.width() - 8 + 1

        y = bubble_center.y() - target_size.height() // 2
        return self._clamp_to_geometry(
            QPoint(x, y), target_size, area, margin=8
        )

    def _animate_geometry(self, start_rect, end_rect, duration=180, on_finished=None):
        if self._geometryAnimation is not None:
            self._geometryAnimation.stop()
        anim = QPropertyAnimation(self.win, b"geometry", self.win)
        anim.setDuration(duration)
        anim.setStartValue(start_rect)
        anim.setEndValue(end_rect)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        if on_finished is not None:
            anim.finished.connect(on_finished)
        self._geometryAnimation = anim
        anim.start()

    def _animate_opacity(self, end_opacity, duration=115, on_finished=None):
        if self._geometryAnimation is not None:
            self._geometryAnimation.stop()
        anim = QPropertyAnimation(self.win, b"windowOpacity", self.win)
        anim.setDuration(duration)
        anim.setStartValue(self.win.windowOpacity())
        anim.setEndValue(float(end_opacity))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        if on_finished is not None:
            anim.finished.connect(on_finished)
        self._geometryAnimation = anim
        anim.start()

    def _main_window_dragged_too_far(self, cursor):
        """主窗被拖出当前屏幕太多时，自动收成悬浮球，避免彻底丢失。"""
        if self._collapsed:
            return False
        screen = self._screen_for_point(cursor)
        area = screen.availableGeometry()
        frame = self.win.frameGeometry()

        visible = frame.intersected(area)
        visible_area = max(0, visible.width()) * max(0, visible.height())
        total_area = max(1, frame.width() * frame.height())
        return visible_area / total_area < 0.55

    def _keep_main_window_recoverable(self):
        """正常拖动结束后至少保证完整主窗仍在当前屏幕可用区。"""
        if self._collapsed:
            return
        screen = self._screen_for_point(self.win.frameGeometry().center())
        self.win.move(
            self._clamp_to_geometry(
                self.win.pos(), self.win.size(), screen.availableGeometry(), margin=8
            )
        )

    def _save_window_state(self):
        """主窗口和悬浮球位置分开保存。"""
        size = self.win.size()
        pos = self.win.pos()
        try:
            settings.save_window_state(pos.x(), pos.y(), size.width(), size.height())
        except Exception:
            pass

    def _collapse_page(self):
        """右上角 × / Alt+F4：主界面收成独立悬浮球。"""
        if self._collapsed:
            return
        self._save_window_state()
        self._expandedSize = self.win.size()
        self._collapsed = True

        self.win.hide()
        self.bubble.set_color(self._bubble_color)
        self.bubble.move(self._restored_bubble_pos())
        self.bubble.show()
        self.bubble.raise_()
        self._save_bubble_state()

    def _expand_page(self):
        """点击悬浮球：隐藏球，在其所在屏幕内平滑显示主界面。"""
        if not self._collapsed:
            return

        bubble_pos = self.bubble.pos()
        bubble_size = self.bubble.size()
        bubble_center = self.bubble.frameGeometry().center()
        screen = self.bubble.screen() or self._screen_for_point(bubble_center)

        target_size = self._expandedSize or self.win.size()
        target_pos = self._expanded_pos_from_bubble(
            bubble_pos, bubble_size, target_size, screen
        )

        self._collapsed = False
        self.bubble.hide()

        target_opacity = 1.0 - settings.transparency() / 100.0
        self.win.setGeometry(QRect(target_pos, target_size))
        self.win.setWindowOpacity(max(0.62, target_opacity - 0.22))
        self.win.showNormal()
        self.win.raise_()
        self.win.activateWindow()
        self._animate_opacity(target_opacity, duration=150, on_finished=self._save_window_state)

    def _preview_transparency(self, value):
        self.transparencyValue.setText(f"{value}%")
        self._apply_transparency(value)

    def _apply_transparency(self, value=None):
        """0 = 完全不透明；40 = 40% 透明。透明只影响视觉，不启用鼠标穿透。"""
        value = settings.transparency() if value is None else max(0, min(40, int(value)))
        self.win.setWindowOpacity(1.0 - value / 100.0)

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self._trayIcon, self.win)
        self.tray.setToolTip("Jev")
        menu = QMenu()
        show_action = QAction("显示主窗口", menu)
        show_action.triggered.connect(self._show_from_tray)
        menu.addAction(show_action)
        bubble_action = QAction("收起为悬浮球", menu)
        bubble_action.triggered.connect(self._show_as_bubble)
        menu.addAction(bubble_action)
        menu.addSeparator()
        quit_action = QAction("退出 Jev", menu)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_from_tray()
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None
        )
        self.tray.show()

    def _close_requested(self, event):
        if self._force_quit:
            event.accept()
            return
        event.ignore()
        self._collapse_page()

    def _show_from_tray(self):
        if self._collapsed:
            self._expand_page()
            return
        self.win.showNormal()
        self._keep_main_window_recoverable()
        self.win.raise_()
        self.win.activateWindow()

    def show_main_window(self):
        """供单实例激活使用：托盘、最小化、悬浮球状态都恢复成完整主界面。"""
        self._show_from_tray()

    def _show_as_bubble(self):
        if not self._collapsed:
            self._collapse_page()
        else:
            self.bubble.show()
            self.bubble.raise_()

    def _show_bubble_menu(self, global_pos):
        menu = QMenu(self.bubble)
        exit_action = QAction("退出 Jev", menu)
        exit_action.triggered.connect(self._quit_app)
        menu.addAction(exit_action)
        menu.exec(global_pos)

    def _quit_app(self):
        self._force_quit = True
        self._save_window_state()
        if hasattr(self, "bubble"):
            self.bubble.hide()
        if hasattr(self, "tray"):
            self.tray.hide()
        self.app.quit()

    def _capture_toggled(self, on):
        """用户自己拨的开关：界面先改，再通知父进程去开/停采集。"""
        self._capture_text(on)
        if self.on_toggle_capture:
            self.on_toggle_capture(on)

    def set_update(self, latest, url):
        """main.py 后台线程查到比当前新的版本才会调这个。只显示版本号和 Release 链接，别的什么都没有。"""
        self.updateLabel.setText(f"有新版本 v{latest}")
        self.updateLink.setUrl(url)
        self.updateBar.show()

    def set_capture(self, on, reason=""):
        """父进程回报的状态：只改界面，不回调（不然和父进程来回打架）。reason 为空用默认说明。"""
        self.captureSwitch.blockSignals(True)
        self.captureSwitch.setChecked(on)
        self.captureSwitch.blockSignals(False)
        self._capture_text(on, reason)

    def _capture_text(self, on, reason=""):
        """开关状态对应的状态行和空态文案。已有的候选不受影响，暂停了照样能填入/复制。"""
        configured = settings.has_key()
        if not on:
            self.set_status(reason or "采集已暂停，聊天内容不再读取", "warning")
        elif configured:
            self.set_status("等待新消息", "idle")
        else:
            self.set_status("请先在设置中配置模型", "warning")
        if self._busy or self.cands:  # 正在生成或已有候选时，空态卡片本来就看不见
            return
        if not on:
            self.emptyTitle.setText("采集已暂停")
            self.emptyHint.setText("聊天内容暂时不再读取。\n打开标题栏的开关，继续接收新消息。")
            self.setupButton.setVisible(not configured)
        else:
            self._empty_text()

    def set_busy(self, busy):
        self._busy = busy
        self.progress.setVisible(busy)
        if busy:
            self.invalidate_replies()
            self.progress.start()
            self.set_status("正在根据新消息整理回复…", "busy")
            if not self.cands:
                self.emptyTitle.setText("正在想一句合适的回复")
                self.emptyHint.setText("正在结合上下文生成建议，稍等一下。")
                self.setupButton.hide()
        else:
            self.progress.stop()
            if not self.cands:
                self._empty_text()
        for card in self.cards:
            card.set_available(self._current and not busy)
        self._sync_reply_actions()

    def _empty_text(self):
        """空态卡片的默认文案，配好没配好两套说法。"""
        configured = settings.has_key()
        self.emptyTitle.setText("等待对方的新消息" if configured else "先设置，再开始")
        self.emptyHint.setText("保持聊天窗口打开。\n收到新消息后，回复建议会出现在这里。"
                               if configured else "配置模型和关系背景，\n让建议更贴近你们的对话。")
        self.setupButton.setVisible(not configured)

    def invalidate_replies(self):
        self._current = False
        if self.cands:
            self.updated.setText("上次建议")
        for card in self.cards:
            card.set_available(False)
        self._sync_reply_actions()

    def set_status(self, text, kind="idle"):
        colors = {"idle": _MUTED, "busy": _GREEN, "success": _GREEN,
                  "warning": "#93611d", "error": "#b44832"}
        markers = {"idle": "●", "busy": "●", "success": "✓", "warning": "!", "error": "!"}
        qss = f"BodyLabel {{ color: {colors.get(kind, _MUTED)}; background: transparent; }}"
        setCustomStyleSheet(self.status, qss, qss)
        self.status.setText(f"{markers.get(kind, '●')}  {text}")
        if kind == "error" and self._busy:
            self.set_busy(False)
        if kind == "error" and not self.cands:
            self.emptyTitle.setText("暂时没有可用的回复")
            self.emptyHint.setText("请按上方提示处理。收到新的对方消息后会再次尝试。")
            self.setupButton.setVisible(not settings.has_key())

    def _toggle_history(self):
        self.feed.setVisible(self.feed.isHidden())
        self._history_title()

    def _history_title(self):
        action = "展开" if self.feed.isHidden() else "收起"
        count = self.counts.get(self._shown, 0)
        self.historyButton.setText(f"{action}聊天记录" + (f" · {count}" if count else ""))

    def log(self, line):
        """采集状态行：只进正在看的那个会话，不按会话存。"""
        bar = self.feed.verticalScrollBar()
        follow = self.feed.isHidden() or bar.value() >= bar.maximum() - 4
        self.feed.appendPlainText(line)
        if follow:
            bar.setValue(bar.maximum())

    def log_message(self, who, text, name="", timestamp=None, chat=None):
        """按会话存一份；只有正在看的那个会往显示区里写。"""
        chat = chat or self._shown
        speaker = (name or "对方") if who == "her" else "我"
        timestamp = timestamp or datetime.now().strftime("%H:%M")
        self.counts[chat] = self.counts.get(chat, 0) + 1
        lines = self.feeds.setdefault(chat, [])
        lines.append(f"{timestamp}  {speaker}\n{text}\n")
        del lines[:-_LOG_LINES]
        if who == "her":
            self.hers[chat] = text
        self._add_chat(chat)
        if chat != self._shown:
            return
        self.log(lines[-1])
        if who == "her":
            self._show_latest(text)
        self._history_title()

    def _show_latest(self, text):
        self.latest.setText(text if len(text) <= 120 else text[:120] + "…")
        self.latest.setToolTip(text)
        self.context.show()

    def current_chat(self):
        """界面上正在看的会话（不一定是微信当前开着的那个）。"""
        return self._shown

    def set_chat(self, title):
        """微信切到了哪个会话：登记进下拉框并自动跟过去，不触发用户选择的回调。"""
        if not title:
            return
        browsing = self._shown != self._chat  # 正看着的就是它、但之前是「浏览中」：也得重画，把填入放开
        self._chat = title
        self._add_chat(title)
        if title != self._shown or browsing:
            self.chatBox.blockSignals(True)
            self.chatBox.setCurrentIndex(self.chatBox.findText(title))
            self.chatBox.blockSignals(False)
            self._switch_to(title)
        self._follow_text()

    def _add_chat(self, title):
        """新会话自动进下拉框；addItem 添第一条时会自己选中，别让它触发切换。"""
        if not title or self.chatBox.findText(title) >= 0:
            return
        self.chatBox.blockSignals(True)
        self.chatBox.addItem(title)
        self.chatBox.blockSignals(False)

    def _on_chat_selected(self, index):
        """用户自己挑了一个会话：只换看的内容，微信那边不动。"""
        title = self.chatBox.itemText(index)
        if title and title != self._shown:
            self._switch_to(title)

    def _switch_to(self, title):
        """换正在看的会话：记录、对方最近说、条数、上次的建议一起换过去。"""
        self._shown = title
        self.feed.clear()
        for line in self.feeds.get(title, []):
            self.feed.appendPlainText(line)
        her = self.hers.get(title)
        if her:
            self._show_latest(her)
        else:
            self.context.hide()
        self._history_title()
        self._follow_text()
        self._render_targets()
        self._refresh_profile_summary()
        self.show_cached(self.result_of(title) if self.result_of else None)

    def set_targets(self, chat, senders, current):
        """某个会话的发言人名单（最近的在前）和当前回复对象；正看着它才重画。"""
        self.targets[chat] = (list(senders), current)
        if chat == self._shown:
            self._render_targets()

    def _render_targets(self):
        """开关关着、或这个会话没有发言人（单聊），这一行就不出现。
        重填下拉框时屏蔽信号，别把自己的填充当成用户挑的。"""
        senders, current = self.targets.get(self._shown, ([], None))
        chat_type = chat_profiles.get(self._shown).get("chat_type", "auto")
        if chat_type == "private":
            visible = False
        else:
            visible = bool(senders) and settings.reply_target()
        self.targetRow.setVisible(visible)
        if not visible:
            return
        self.targetBox.blockSignals(True)
        self.targetBox.clear()
        self.targetBox.addItems(senders)
        self.targetBox.setCurrentIndex(senders.index(current) if current in senders else 0)
        self.targetBox.blockSignals(False)

    def _on_target_selected(self, index):
        """用户挑了回复对象。浏览别的会话时改的就是那个会话的对象——记录、候选也都按会话走，口径一致。"""
        name = self.targetBox.itemText(index)
        if not name:
            return
        senders, _ = self.targets.get(self._shown, ([], None))
        self.targets[self._shown] = (senders, name)
        self.set_status(f"按「{name}」重新生成…", "busy")
        if self.on_target_change:
            self.on_target_change(self._shown, name)

    def at_prefix_enabled(self):
        """填入时要不要带「@名字 」前缀（只记在界面上，不落盘）。"""
        return self.atCheck.isChecked()

    def _follow_text(self):
        self.chatFollow.setText(("跟随" if self._shown == self._chat else "浏览中") if self._chat else "")
        self._sync_reply_actions()

    def show_cached(self, result):
        """把某个会话上次的结果放回界面；没有就回到空态。浏览别的会话时只给看不给填——
        微信当前开着的不是它，填进去就串会话了。"""
        if result:
            self.show(result)
        else:
            self.cands = []
            self._clear_cards()
            self.insight.hide()
            self.referenceNote.hide()
            self.empty.show()
            self.updated.setText("")
            self._empty_text()
            self._sync_reply_actions()
        if self._shown != self._chat:
            self.invalidate_replies()
            self.set_status(f"正在浏览「{self._shown}」，只看不填；切回这个会话才能用。")

    def show(self, result):
        """按推荐顺序展示，按钮始终绑定 candidates 的原始索引。"""
        self.cands = result["candidates"]
        self.set_busy(False)
        self._current = bool(self.cands)
        self._clear_cards()
        best = result.get("best_index", 0)
        if best not in range(len(self.cands)):
            best = 0
        raw_scores = result.get("scores") or []
        scores = [raw_scores[i] if i < len(raw_scores) else 0.0 for i in range(len(self.cands))]
        # 按概率降序排，推荐位（API 给的 choice）强制第一，同分按原索引
        order = sorted(range(len(self.cands)), key=lambda i: (i != best, -(scores[i] or 0), i))
        for position, index in enumerate(order):
            card = _ReplyCard(self, index, recommended=index == best, number=position, score=scores[index])
            self.replyBox.addWidget(card)
            self.cards.append(card)
        reply_to = result.get("reply_to")
        context_bits = []
        if reply_to:
            context_bits.append(f"回复给 {reply_to}")
        if result.get("knowledge_count"):
            context_bits.append(f"知识库 {result['knowledge_count']} 条")
        self.insightTitle.setText("对话参考" + (" · " + " · ".join(context_bits) if context_bits else ""))
        answers = result.get("answers") or {}

        def conf(name):
            value = (answers.get(name) or {}).get("confidence")
            return f" · 把握 {round(value * 100)}%" if isinstance(value, (int, float)) and 0 <= value <= 1 else ""

        self.summary.setText("建议：" + _choice(answers, "best_action") + conf("best_action"))
        self.intent.setText(
            "可能意图 · " + _choice(answers, "true_intent") + conf("true_intent") +
            "\n可能需要 · " + _choice(answers, "she_needs") + conf("she_needs")
        )

        extra = []
        should = (answers.get("should_reply_now") or {}).get("noul")
        if isinstance(should, (int, float)) and 0 <= should <= 1:
            extra.append(("可给实质内容" if should >= 0.5 else "先别给实质内容") + f" · {round((should if should >= 0.5 else 1-should) * 100)}%")
        literal = (answers.get("literal_question") or {}).get("noul")
        if isinstance(literal, (int, float)) and 0 <= literal <= 1:
            extra.append(("偏字面意思" if literal >= 0.5 else "可能有潜台词") + f" · {round((literal if literal >= 0.5 else 1-literal) * 100)}%")
        resolved = (answers.get("tension_resolved") or {}).get("noul")
        if isinstance(resolved, (int, float)) and 0 <= resolved <= 1:
            extra.append(("紧张已缓解" if resolved >= 0.5 else "紧张未缓解") + f" · {round((resolved if resolved >= 0.5 else 1-resolved) * 100)}%")
        self.judgmentExtra.setText("  ·  ".join(extra))

        danger = answers.get("danger_level") or {}
        score = danger.get("score")
        dconf = danger.get("confidence")
        valid_score = isinstance(score, (int, float)) and isfinite(score) and 0 <= score <= 9
        dconf_text = f" · 把握 {round(dconf * 100)}%" if isinstance(dconf, (int, float)) and 0 <= dconf <= 1 else ""
        self.tension.setText(f"危险 {score:.0f}/9{dconf_text}" if valid_score else "危险度待判断")
        color = "#996819" if valid_score and score >= 3 else _MUTED
        if valid_score and score >= 6:
            color = "#b44832"
        self._set_bubble_danger(score if valid_score else None)
        qss = f"BodyLabel {{ color: {color}; background: transparent; }}"
        setCustomStyleSheet(self.tension, qss, qss)
        self.empty.setVisible(not self.cands)
        self.insight.setVisible(bool(self.cands))
        self.referenceNote.setVisible(bool(self.cands) and not self._compact)
        self.updated.setText(datetime.now().strftime("%H:%M") + " 更新")
        if self.cands:
            self.set_status("建议已更新，选一句适合你的回复", "success")
        else:
            self.set_status("未生成可用回复，请等待下一条新消息。", "error")
        self._sync_reply_actions()

    def _set_bubble_danger(self, score):
        color = "#18794e"
        if isinstance(score, (int, float)) and score >= 6:
            color = "#b44832"
        elif isinstance(score, (int, float)) and score >= 3:
            color = "#b07a21"

        self._bubble_color = color
        self.bubble.set_color(color)

        self._trayIcon = _tray_icon(color)
        self.win.setWindowIcon(self._trayIcon)
        if hasattr(self, "tray"):
            self.tray.setIcon(self._trayIcon)

    def _clear_cards(self):
        for card in self.cards:
            self.replyBox.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = []

    def after(self, ms, fn):
        QTimer.singleShot(ms, fn)

    def run(self):
        self.app.exec()
