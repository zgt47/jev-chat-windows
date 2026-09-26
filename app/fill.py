# -*- coding: utf-8 -*-
"""把候选填进微信输入框；自动客服模式下可发送。

安全原则：
- 新版微信需要真实前台点击才能聚焦输入框；
- 每次点击前先验证目标屏幕点的根窗口确实是微信；
- 任何遮挡、越界或坐标异常都直接取消，不允许点到其它程序；
- WGC 截图坐标先按实际帧尺寸映射到窗口坐标，高 DPI 不再靠猜缩放倍数。
"""
import ctypes
import ctypes.wintypes as w
import time

u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32

# 剪贴板 64 位句柄声明。
k32.GlobalAlloc.restype = ctypes.c_void_p
k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalLock.argtypes = [ctypes.c_void_p]
k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
k32.GlobalFree.argtypes = [ctypes.c_void_p]
u32.SetClipboardData.restype = ctypes.c_void_p
u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

u32.SendMessageTimeoutW.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t,
    ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t),
]
u32.SendMessageTimeoutW.restype = ctypes.c_size_t
u32.ChildWindowFromPointEx.argtypes = [ctypes.c_void_p, w.POINT, ctypes.c_uint]
u32.ChildWindowFromPointEx.restype = ctypes.c_void_p
u32.ScreenToClient.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.POINT)]
u32.ScreenToClient.restype = ctypes.c_bool
u32.WindowFromPoint.argtypes = [w.POINT]
u32.WindowFromPoint.restype = ctypes.c_void_p
u32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
u32.GetAncestor.restype = ctypes.c_void_p

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
SMTO_ABORTIFHUNG = 0x0002
CWP_SKIPINVISIBLE = 0x0001
CWP_SKIPDISABLED = 0x0002
CWP_SKIPTRANSPARENT = 0x0004
GA_ROOT = 2


def _window_rect(hwnd):
    """窗口扩展边界；与 WGC 捕获窗口尽量使用同一外框。"""
    r = w.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)
    ) != 0:
        u32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def _frame_geometry(hwnd, area):
    """校验 WGC area，并返回 x0,y0,x1,y1,frame_w,frame_h。

    新 worker 会把实际 WGC 帧宽高附在 area 后面。旧格式只作为兼容兜底。
    """
    if area is None or len(area) < 4:
        raise RuntimeError("聊天区域坐标不可用")

    x0, y0, x1, y1 = [int(v) for v in area[:4]]
    r = _window_rect(hwnd)
    win_w = max(1, r.right - r.left)
    win_h = max(1, r.bottom - r.top)

    frame_w = int(area[4]) if len(area) >= 6 else win_w
    frame_h = int(area[5]) if len(area) >= 6 else win_h

    if frame_w < 200 or frame_h < 200:
        raise RuntimeError("聊天窗口尺寸异常")
    if not (0 <= x0 < x1 <= frame_w and 0 <= y0 < y1 < frame_h):
        raise RuntimeError("聊天区域超出窗口边界")

    pane_w = x1 - x0
    input_h = frame_h - y1
    if pane_w < 180:
        raise RuntimeError("聊天面板过窄，拒绝自动点击")
    if input_h < 70 or input_h > frame_h * 0.48:
        raise RuntimeError("输入区域高度异常，拒绝自动点击")

    return r, x0, y0, x1, y1, frame_w, frame_h


def _frame_to_screen(r, frame_w, frame_h, fx, fy):
    """WGC 帧坐标 → 实际屏幕坐标。

    这里按“实际捕获帧 / 实际窗口外框”的比例映射，因此不再猜 125/150/200% DPI。
    """
    win_w = max(1, r.right - r.left)
    win_h = max(1, r.bottom - r.top)
    sx = r.left + round(float(fx) * win_w / frame_w)
    sy = r.top + round(float(fy) * win_h / frame_h)
    return sx, sy


def _foreground(hwnd):
    """把微信带到前台；如果 Windows 拒绝，就停止，不把键盘发给其它程序。"""
    from app.capture import unminimize

    unminimize(hwnd)
    fg = u32.GetForegroundWindow()
    if fg != hwnd:
        fg_tid = u32.GetWindowThreadProcessId(fg, None)
        our_tid = k32.GetCurrentThreadId()
        u32.AttachThreadInput(our_tid, fg_tid, True)
        try:
            u32.SetForegroundWindow(hwnd)
        finally:
            u32.AttachThreadInput(our_tid, fg_tid, False)
        time.sleep(0.10)

    if u32.GetForegroundWindow() != hwnd:
        raise RuntimeError("无法安全切换到微信窗口，已取消操作")


def _target_child(hwnd, sx, sy):
    """从微信窗口内部向下找坐标所在子窗口，不受 Jev 置顶窗口遮挡影响。"""
    target = hwnd
    screen = w.POINT(int(sx), int(sy))

    for _ in range(6):
        pt = w.POINT(screen.x, screen.y)
        if not u32.ScreenToClient(target, ctypes.byref(pt)):
            break
        child = u32.ChildWindowFromPointEx(
            target, pt,
            CWP_SKIPINVISIBLE | CWP_SKIPDISABLED | CWP_SKIPTRANSPARENT,
        )
        if not child or child == target:
            break
        target = child

    pt = w.POINT(screen.x, screen.y)
    if not u32.ScreenToClient(target, ctypes.byref(pt)):
        raise RuntimeError("无法换算微信控件坐标")
    return target, pt.x, pt.y


def _physical_click_wechat(hwnd, sx, sy):
    """受限前台点击：只有屏幕上这个点实际属于微信时才允许点。

    新版微信的输入编辑器不是普通 Win32 子控件，不响应后台 WM_LBUTTONDOWN，
    所以聚焦输入框必须走真实前台点击。但这里先做 WindowFromPoint 根窗口校验，
    若 Jev、聊天列表之外的窗口或其它程序挡在这个点上，就直接拒绝执行。
    """
    r = _window_rect(hwnd)
    if not (r.left <= sx < r.right and r.top <= sy < r.bottom):
        raise RuntimeError("点击点超出微信窗口，已取消操作")

    probe = w.POINT(int(sx), int(sy))
    hit = u32.WindowFromPoint(probe)
    root = u32.GetAncestor(hit, GA_ROOT) if hit else None
    if not root or int(root) != int(hwnd):
        raise RuntimeError("目标位置当前不是微信窗口，已取消操作")

    old = w.POINT()
    u32.GetCursorPos(ctypes.byref(old))
    try:
        u32.SetCursorPos(int(sx), int(sy))
        time.sleep(0.025)
        u32.mouse_event(0x2, 0, 0, 0, 0)
        u32.mouse_event(0x4, 0, 0, 0, 0)
        time.sleep(0.025)
    finally:
        u32.SetCursorPos(old.x, old.y)


def _ctrl_key(vk):
    """仅在微信仍是前台窗口时调用。"""
    u32.keybd_event(0x11, 0, 0, 0)
    u32.keybd_event(vk, 0, 0, 0)
    u32.keybd_event(vk, 0, 2, 0)
    u32.keybd_event(0x11, 0, 2, 0)


def set_clipboard(text):
    """写剪贴板；被其它程序占用时有限重试。"""
    data = text.encode("utf-16-le") + b"\0\0"
    for attempt in range(10):
        if not u32.OpenClipboard(None):
            time.sleep(0.05)
            continue
        try:
            u32.EmptyClipboard()
            h = k32.GlobalAlloc(0x2, len(data))
            if not h:
                raise RuntimeError("GlobalAlloc 失败")
            p = k32.GlobalLock(h)
            if not p:
                k32.GlobalFree(h)
                raise RuntimeError("GlobalLock 失败")
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(13, h):
                k32.GlobalFree(h)
                raise RuntimeError(f"SetClipboardData 失败 (attempt {attempt})")
            return
        finally:
            u32.CloseClipboard()
    raise RuntimeError("剪贴板被其他程序占用")


def fill(hwnd, area, text):
    """安全填入：聚焦微信输入区并粘贴，不移动/点击真实鼠标。"""
    set_clipboard(text)
    r, x0, _, x1, y1, frame_w, frame_h = _frame_geometry(hwnd, area)
    pane_w = x1 - x0
    input_h = frame_h - y1

    # 点在输入区正文位置：横向离聊天面板左边约 12%，纵向在输入区上部 30%。
    fx = x0 + max(36, min(round(pane_w * 0.12), 120))
    fy = y1 + max(34, min(round(input_h * 0.30), 90))
    sx, sy = _frame_to_screen(r, frame_w, frame_h, fx, fy)

    _foreground(hwnd)
    _physical_click_wechat(hwnd, sx, sy)
    time.sleep(0.05)

    if u32.GetForegroundWindow() != hwnd:
        raise RuntimeError("微信失去前台焦点，已取消粘贴")

    _ctrl_key(0x23)  # Ctrl+End
    time.sleep(0.03)
    if u32.GetForegroundWindow() != hwnd:
        raise RuntimeError("微信失去前台焦点，已取消粘贴")
    _ctrl_key(0x56)  # Ctrl+V


def send(hwnd, area):
    """安全发送：只向微信窗口内部的发送按钮位置投递点击消息，不碰真实鼠标。"""
    r, x0, _, x1, y1, frame_w, frame_h = _frame_geometry(hwnd, area)
    pane_w = x1 - x0
    input_h = frame_h - y1

    # 原 100% DPI 下大约是“右 55 / 下 34”。
    # 用输入区实际高度推导 UI 缩放，比 GetDpiForWindow + 固定像素可靠。
    right_margin = max(42, min(round(input_h * 0.37), 140))
    bottom_margin = max(26, min(round(input_h * 0.23), 90))
    fx = x1 - right_margin
    fy = frame_h - bottom_margin

    # 发送点必须严格在“聊天面板 x 范围 + 输入区 y 范围”内。
    if not (x0 + pane_w * 0.55 <= fx < x1 and y1 + input_h * 0.45 <= fy < frame_h):
        raise RuntimeError("发送按钮位置校验失败，已取消自动发送")

    sx, sy = _frame_to_screen(r, frame_w, frame_h, fx, fy)
    _foreground(hwnd)
    _physical_click_wechat(hwnd, sx, sy)
