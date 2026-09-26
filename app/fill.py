# -*- coding: utf-8 -*-
"""把候选填进微信输入框；自动客服模式下也可以显式点击发送按钮。"""
import ctypes
import ctypes.wintypes as w
import time

u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32

# 64 位下 ctypes.windll 默认 restype 是 32 位 c_int，而 GlobalAlloc 返回 64 位 HGLOBAL——
# 不声明类型句柄会被截断成垃圾值，GlobalLock(垃圾) 返回 NULL，memmove(NULL,…) 就是
# "access violation writing 0x0"。所有带句柄/指针的函数必须显式声明。
k32.GlobalAlloc.restype = ctypes.c_void_p
k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalLock.argtypes = [ctypes.c_void_p]
k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
k32.GlobalFree.argtypes = [ctypes.c_void_p]
u32.SetClipboardData.restype = ctypes.c_void_p
u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

try:
    u32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
    u32.GetDpiForWindow.restype = ctypes.c_uint
except AttributeError:
    pass


def _dpi_scale(hwnd) -> float:
    """返回窗口 DPI 相对 96 DPI 的缩放倍数。

    WGC 的聊天区域坐标跟窗口像素走，但“输入框内偏移 / 发送按钮边距”
    是按微信界面的逻辑尺寸设计的，必须随 DPI 放大。
    """
    try:
        dpi = int(u32.GetDpiForWindow(hwnd))
        if dpi > 0:
            return max(1.0, min(3.0, dpi / 96.0))
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return 1.0


def _window_rect(hwnd):
    """取与 WGC 尽量一致的窗口扩展边界。"""
    r = w.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)
    ) != 0:
        u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def set_clipboard(text):
    """写剪贴板。剪贴板可能被别的程序占着（剪贴板管理器、截图工具），重试几次。"""
    data = text.encode("utf-16-le") + b"\0\0"
    for attempt in range(10):
        if not u32.OpenClipboard(None):
            time.sleep(0.05)
            continue
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(0x2, len(data))  # GMEM_MOVEABLE
            if not h:
                raise RuntimeError("GlobalAlloc 失败")
            p = k32.GlobalLock(h)
            if not p:
                k32.GlobalFree(h)
                raise RuntimeError("GlobalLock 失败")
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(13, h):  # CF_UNICODETEXT；成功后句柄归系统，不能 Free
                k32.GlobalFree(h)
                raise RuntimeError(f"SetClipboardData 失败 (attempt {attempt})")
            return
        finally:
            u32.CloseClipboard()
    raise RuntimeError("OpenClipboard 连续失败，剪贴板被其他程序占用")


def fill(hwnd, area, text):
    """area = 消息区 (x0, y0, x1, y1)；输入框就在底线 y1 下面。"""
    from app.capture import unminimize

    set_clipboard(text)
    r = _window_rect(hwnd)
    scale = _dpi_scale(hwnd)
    x0, _, _, y1 = area
    # 60 / 40 是 100% DPI 下的逻辑偏移，高 DPI 必须同步放大。
    cx = r.left + x0 + round(60 * scale)
    cy = r.top + y1 + round(40 * scale)
    unminimize(hwnd)

    # SetForegroundWindow 有前台窗口保护，普通后台进程会被拒；AttachThreadInput 绕过
    fg = u32.GetForegroundWindow()
    if fg != hwnd:
        fg_tid = u32.GetWindowThreadProcessId(fg, None)
        our_tid = k32.GetCurrentThreadId()
        u32.AttachThreadInput(our_tid, fg_tid, True)
        u32.SetForegroundWindow(hwnd)
        u32.AttachThreadInput(our_tid, fg_tid, False)
        time.sleep(0.15)  # 给微信一点时间响应前台切换

    old = w.POINT()
    u32.GetCursorPos(ctypes.byref(old))
    u32.SetCursorPos(cx, cy)
    time.sleep(0.05)
    u32.mouse_event(0x2, 0, 0, 0, 0)  # 左键按下
    u32.mouse_event(0x4, 0, 0, 0, 0)  # 抬起
    time.sleep(0.05)
    u32.SetCursorPos(old.x, old.y)
    time.sleep(0.05)
    # 光标移到已有文本的绝对末尾：点击落在文字中间时 caret 会插在中间，
    # 连续多次填入就串行错乱；Ctrl+End 保证新内容永远追加在最后
    u32.keybd_event(0x11, 0, 0, 0)  # Ctrl 按下
    u32.keybd_event(0x23, 0, 0, 0)  # End 按下（VK_END）
    u32.keybd_event(0x23, 0, 2, 0)  # End 抬起
    u32.keybd_event(0x11, 0, 2, 0)  # Ctrl 抬起
    time.sleep(0.05)
    u32.keybd_event(0x11, 0, 0, 0)  # Ctrl
    u32.keybd_event(0x56, 0, 0, 0)  # V
    u32.keybd_event(0x56, 0, 2, 0)
    u32.keybd_event(0x11, 0, 2, 0)
    # 到此为止。发不发、改不改，人来。



def send(hwnd, area):
    """点击微信输入区右下角的“发送”按钮。

    area 与 fill() 相同，来自当前帧识别出的聊天面板坐标。
    不依赖 Enter / Ctrl+Enter 设置，因此比键盘快捷键稳定。
    """
    from app.capture import unminimize

    r = _window_rect(hwnd)
    scale = _dpi_scale(hwnd)

    _, _, x1, y1 = area[:4]
    unminimize(hwnd)

    fg = u32.GetForegroundWindow()
    if fg != hwnd:
        fg_tid = u32.GetWindowThreadProcessId(fg, None)
        our_tid = k32.GetCurrentThreadId()
        u32.AttachThreadInput(our_tid, fg_tid, True)
        u32.SetForegroundWindow(hwnd)
        u32.AttachThreadInput(our_tid, fg_tid, False)
        time.sleep(0.12)

    # “发送”按钮位于聊天面板右下角。
    # x1 / y1 来自 WGC 的真实窗口像素；按钮自身的右/下边距属于 UI 逻辑尺寸，
    # 所以 55 / 34 必须按窗口 DPI 缩放。3200×2000 + 150~200% 缩放时，
    # 旧代码固定减 55/34 会点到按钮右下方，表现为“已经填入但没有发送”。
    sx = r.left + x1 - round(55 * scale)
    sy = r.bottom - round(34 * scale)

    # 最后再夹进输入区右下角，避免极端主题 / 窗口尺寸下点出聊天面板。
    input_top = r.top + y1
    sx = max(r.left + 1, min(sx, r.right - 2))
    sy = max(input_top + round(18 * scale), min(sy, r.bottom - 2))

    old = w.POINT()
    u32.GetCursorPos(ctypes.byref(old))
    u32.SetCursorPos(sx, sy)
    time.sleep(0.06)
    u32.mouse_event(0x2, 0, 0, 0, 0)
    u32.mouse_event(0x4, 0, 0, 0, 0)
    time.sleep(0.06)
    u32.SetCursorPos(old.x, old.y)
