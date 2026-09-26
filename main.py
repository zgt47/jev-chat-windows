# -*- coding: utf-8 -*-
"""父进程：只管界面。截图 + OCR 在 app/worker.py 的子进程里跑，队列里收新消息 →
冒出新的对方消息才调 engine → 悬浮窗给 3 条候选 → 人工填入或按设置自动发送。静默期零调用。
上下文、结果、聊天记录都按会话名（子进程 OCR 头部标题得来）分开存，切会话不串味。

    pip install rapidocr-onnxruntime numpy windows-capture PySide6-Fluent-Widgets
两个模型（判断 Jev / 起草语言模型）的来源和 key 在独立设置页填写，不用改代码。IDE 里直接 Run。
"""
import ctypes
import multiprocessing
import queue
import threading
import traceback
from collections import deque

from app import chat_history, chat_profiles, settings, update, worker
from app.services import analysis_service, auto_send_policy
from app.capture import find_wechat_hwnd
from app.fill import fill, send
from app.overlay import Overlay
from app.version import VERSION

# {会话名: {history, result, rev, target, senders}}：每个会话各自的上下文、上次结果和版本号，互不串味
# history 里是 [(who, text, name)]，engine 只认 her/me，name 是群里的发言人（单聊/自己说的是 None）；
# 只是缓冲区，实际喂模型几条由设置里的「参考上下文」决定
# senders：这个群里发过言的人，去重、最近的排最前；target：用户挑的回复对象（None = 跟着最近那个走）
chats = {}
state = {"area": None, "busy": False, "rerun": None, "hwnd": None, "chat": "",
         "pending_send": None}
results = queue.Queue()
update_result = queue.Queue()  # 独立小队列，别跟 results 的 (kind, r, title, revision) 形状搅在一起

_SINGLE_MUTEX_NAME = r"Local\JevChat-Windows-SingleInstance-v1"
_SINGLE_EVENT_NAME = r"Local\JevChat-Windows-Activate-v1"
_single_mutex = None
_single_event = None


def init_single_instance():
    """只允许一个 Jev 主实例。

    第二次启动时不创建新 UI，而是：
    1. 通知已运行实例展开；
    2. 尝试把它直接提到前台；
    3. 当前这个新进程立即结束。
    """
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.SetEvent.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]

    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]

    event_handle = kernel32.CreateEventW(None, False, False, _SINGLE_EVENT_NAME)
    kernel32.SetLastError(0)
    mutex_handle = kernel32.CreateMutexW(None, False, _SINGLE_MUTEX_NAME)
    already_running = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS

    if already_running:
        if event_handle:
            kernel32.SetEvent(ctypes.c_void_p(event_handle))

        # 用户刚刚主动双击了第二次启动，借这个前台资格把旧窗口提起来。
        hwnd = user32.FindWindowW(None, "JevChat-Windows")
        if hwnd:
            hwnd_ptr = ctypes.c_void_p(hwnd)
            user32.ShowWindow(hwnd_ptr, 9)  # SW_RESTORE
            user32.BringWindowToTop(hwnd_ptr)
            user32.SetForegroundWindow(hwnd_ptr)

        if mutex_handle:
            kernel32.CloseHandle(ctypes.c_void_p(mutex_handle))
        if event_handle:
            kernel32.CloseHandle(ctypes.c_void_p(event_handle))
        return False, None, None

    return True, mutex_handle, event_handle


def close_single_instance_handles():
    kernel32 = ctypes.windll.kernel32
    for handle in (_single_event, _single_mutex):
        if handle:
            try:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
            except Exception:
                pass


def check_activate_request():
    """主循环里无阻塞检查：第二次启动 JevChat-Dev.exe 时展开旧实例。"""
    if not _single_event:
        return
    if ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(_single_event), 0) == 0:
        ov.show_main_window()


def chat_of(title):
    if title in chats:
        return chats[title]
    saved = chat_history.load(title, settings.history_limit()) if settings.record_history() else []
    history = deque(saved, maxlen=max(60, settings.history_limit()))
    senders = []
    for who, text, name in reversed(saved):
        if who == "her" and name and name not in senders:
            senders.append(name)
    chats[title] = {"history": history, "result": None, "rev": 0,
                    "target": None, "senders": senders}
    return chats[title]


def target_of(title):
    """这个会话现在的回复对象：私聊强制 None；群聊/自动识别才从发言人里选。"""
    if chat_profiles.get(title).get("chat_type", "auto") == "private":
        return None
    chat = chat_of(title)
    if chat["target"] in chat["senders"]:
        return chat["target"]
    return chat["senders"][0] if chat["senders"] else None


def fill_reply(text):
    if state["hwnd"] is None:  # 子进程重开过，hwnd 可能换了，用最新的
        raise RuntimeError("未找到聊天窗口，请确认已经打开")
    if state["area"] is None:
        raise RuntimeError("输入区域尚不可用，请确认聊天窗口可见（不要最小化）")
    if settings.reply_target() and ov.at_prefix_enabled():
        target = target_of(ov.current_chat())  # 填进去的是界面上正看着的那个会话的对象
        if target:
            text = f"@{target} " + text  # 纯文本，微信不认成真正的 @，只是让群里看得出在跟谁说
    fill(state["hwnd"], state["area"], text)


def queue_auto_send(title, result, revision):
    """客服模式：把是否能自动发送交给独立策略模块判断。"""
    chat = chat_of(title)
    plan = auto_send_policy.evaluate(
        enabled=settings.auto_send(),
        title=title,
        active_title=state["chat"],
        visible_title=ov.current_chat(),
        chat_state=chat,
        profile=chat_profiles.get(title),
        result=result,
    )
    if not plan.allowed:
        if plan.status:
            ov.set_status(plan.status, plan.status_kind)
        return

    token = (title, revision, plan.text)
    state["pending_send"] = token
    delay = settings.auto_send_delay()
    ov.set_status(f"客服自动发送：{delay} 秒后发送推荐回复；新消息到来会自动取消。", "warning")
    ov.after(delay * 1000, lambda t=token: perform_auto_send(t))


def perform_auto_send(token):
    """延迟结束后再次核对；具体有效性规则由独立策略模块负责。"""
    if state.get("pending_send") != token:
        return
    state["pending_send"] = None

    title, revision, text = token
    chat = chat_of(title)
    plan = auto_send_policy.still_valid(
        enabled=settings.auto_send(),
        title=title,
        active_title=state["chat"],
        visible_title=ov.current_chat(),
        revision=revision,
        current_revision=chat["rev"],
        history=chat["history"],
        hwnd_available=state["hwnd"] is not None,
        area_available=state["area"] is not None,
    )
    if not plan.allowed:
        if plan.status:
            ov.set_status(plan.status, plan.status_kind)
        return

    try:
        fill_reply(text)
        # 给微信一次重绘机会，确保粘贴内容已经进入输入框。
        import time
        time.sleep(0.15)
        send(state["hwnd"], state["area"])
    except Exception as exc:
        ov.set_status("自动发送失败，已停止本次发送；请人工确认。", "error")
        ov.log(f"[自动发送失败] {type(exc).__name__}: {exc}")
        return

    ov.set_status("已自动发送推荐回复。", "success")


def spawn_worker():
    """开一个采集子进程，它跟着 capture_on 走：置位=采集，清掉=暂停。"""
    p = multiprocessing.Process(target=worker.run,
                                args=(q, state["hwnd"], capture_on, debug_on), daemon=True)
    p.start()
    return p


def set_debug(on):
    """调试视图开关：开 → 开窗 + 置位（子进程这才开始送帧，一帧 2~3MB）；关 → 清掉 + 收窗。"""
    global dbg
    if not on:
        debug_on.clear()
        if dbg is not None:
            dbg.hide()
        return
    if dbg is None:
        from app.debugwin import DebugWindow

        dbg = DebugWindow(on_close=on_debug_closed)
    dbg.show()
    debug_on.set()


def on_debug_closed():
    """用户直接关了调试窗 = 把开关也关了，否则设置页显示开着但没窗。"""
    debug_on.clear()
    ov.set_debug_switch(False)
    settings.save(debug_view_on=False)


def on_toggle_capture(on):
    """标题栏开关。启动时没找到微信就没有子进程，这会儿再找一次，找到了才真开得起来。"""
    global child
    if not on:
        capture_on.clear()
        return
    if child is None:
        try:
            state["hwnd"] = find_wechat_hwnd()
        except RuntimeError:
            ov.set_capture(False, "未找到聊天窗口，打开后再开启采集")
            return
        child = spawn_worker()
    capture_on.set()


def analyze_bg(msgs, title, revision, reply_to=None):
    """后台线程只调用应用分析服务；Qt 主线程只负责收结果和更新界面。"""
    try:
        result = analysis_service.analyze_conversation(title, msgs, reply_to)
        results.put(("ok", result, title, revision))
    except Exception as e:
        results.put(("err", f"分析失败: {e}", title, revision))


def check_update_bg():
    """启动时后台查一次新版本，跟 analyze_bg 一个套路：网络调用在线程里，UI 只在 tick() 里动。"""
    r = update.check_latest(VERSION)
    if r:
        update_result.put(r)


def start_analyze(title, msgs):
    if not settings.has_jev_key():
        ov.set_status("请先在设置中配置模型", "warning")
        return
    if not settings.has_llm_key():
        ov.set_status(f"起草来源 {settings.draft_provider_name()} 没填密钥，去设置里补上", "warning")
        return
    state["busy"] = True
    ov.set_busy(True)
    reply_to = target_of(title) if settings.reply_target() else None  # 开关关着就是今天的行为
    threading.Thread(target=analyze_bg, args=(msgs, title, chat_of(title)["rev"], reply_to),
                     daemon=True).start()


def on_target_change(title, name):
    """用户挑了回复对象：记下来，这个会话里有对方的话就照新对象重跑一次。"""
    chat = chat_of(title)
    chat["target"] = name
    msgs = list(chat["history"])
    if not any(m[0] == "her" for m in msgs):
        return
    if state["busy"]:
        state["rerun"] = (title, msgs)
        ov.set_busy(True)
    else:
        start_analyze(title, msgs)


def clear_generated_replies(title):
    """清掉当前会话的 AI 建议，但保留聊天记录；旧结果不再恢复出来。"""
    if not title:
        return
    if state["busy"]:
        ov.set_status("正在生成中，完成后再清空。", "warning")
        return
    chat = chat_of(title)
    chat["result"] = None
    chat["rev"] += 1
    state["rerun"] = None
    if title == ov.current_chat():
        ov.clear_suggestions()


def regenerate_replies(title):
    """手动分析当前会话：直接用现有聊天记录重新跑完整判断、起草和排序。"""
    if not title:
        return
    if state["busy"]:
        ov.set_status("上一轮还在生成，请稍等。", "warning")
        return
    if state["chat"] and title != state["chat"]:
        ov.set_status("正在浏览其他会话，切回这个聊天后再分析。", "warning")
        return
    chat = chat_of(title)
    msgs = list(chat["history"])
    if not any(m[0] == "her" for m in msgs):
        ov.set_status("当前还没有识别到对方消息，暂时不能重新生成。", "warning")
        return
    chat["result"] = None
    chat["rev"] += 1
    state["rerun"] = None
    ov.set_status("正在重新分析当前对话…", "busy")
    start_analyze(title, msgs)


def drain():
    """把子进程队列里攒的东西全收掉。"""
    global child
    while True:
        try:
            msg = q.get_nowait()
        except queue.Empty:
            return
        kind = msg[0]
        if kind == "area":  # 只是窗口挪了位置，坐标跟着更新，别的什么都不用动
            state["area"] = msg[1]
            continue
        if kind == "chat":  # 微信切了会话，界面跟过去（用户正浏览别的会话时也跟，微信是准的）
            state["chat"] = msg[1]
            ov.set_chat(msg[1])
            continue
        if kind == "debug":  # 调试视图的一帧；窗口不在就直接丢掉
            if dbg is not None:
                dbg.show_packet(msg[1])
            continue
        if kind == "status":  # 单帧识别失败/报错，提示一下就好，别把正在跑的分析和已知坐标清掉
            ov.set_status(msg[1], "warning")
            ov.log(msg[1])
            continue
        if kind == "paused":  # 子进程确认已暂停
            ov.set_capture(False)
            continue
        if kind == "resumed":  # 子进程重新开始采集
            ov.set_capture(True)
            continue
        if kind == "dead":  # 采集彻底停了（微信关了之类），这才是真的要清状态
            state["area"] = None
            for c in chats.values():  # 在跑的分析作废，回来的结果不再往界面上贴
                c["rev"] += 1
            state["rerun"] = None
            ov.invalidate_replies()
            ov.set_busy(False)
            ov.set_capture(False, msg[1])
            ov.log(msg[1])
            if child is not None:  # 子进程已经不干活了，收掉引用，下次打开开关重开一个
                child.terminate()
                child.join()
                child = None
            continue
        _, title, new, area = msg
        state["area"] = area
        chat = chat_of(title)
        state["pending_send"] = None
        chat["rev"] += 1  # 这个会话有新消息了，它在跑的分析作废
        if title == ov.current_chat():  # 看的是别的会话就别把人家的候选划掉
            ov.invalidate_replies()
        incoming = [(who, text, name) for who, name, text in new]
        existing = list(chat["history"])
        skip = 0
        for n in range(min(len(existing), len(incoming)), 0, -1):
            if existing[-n:] == incoming[:n]:
                skip = n
                break
        fresh = incoming[skip:]
        for who, text, name in fresh:
            chat["history"].append((who, text, name))
            ov.log_message(who, text, name, chat=title)
            if who == "her" and name:
                if name in chat["senders"]:
                    chat["senders"].remove(name)
                chat["senders"].insert(0, name)
        if settings.record_history():
            try:
                chat_history.save(title, list(chat["history"]), max_keep=max(100, settings.history_limit() * 3))
            except Exception:
                pass
        ov.set_targets(title, chat["senders"], target_of(title))
        latest = incoming[-1] if incoming else None
        if latest and latest[0] == "her":
            msgs = list(chat["history"])
            if not settings.chat_allowed(title):
                state["rerun"] = None
                ov.set_busy(False)
                ov.set_status("已收到新消息，但当前会话不在自动分析白名单；可手动分析。", "idle")
            elif not settings.auto_analyze():
                state["rerun"] = None
                ov.set_busy(False)
                ov.set_status("已收到新消息；自动分析已关闭，可点「分析当前对话」。", "idle")
            elif state["busy"]:
                state["rerun"] = (title, msgs)
                ov.set_busy(True)
            else:
                start_analyze(title, msgs)
        else:
            state["rerun"] = None
            ov.set_busy(False)
            ov.set_status("你已回复，等待对方的新消息")


def tick():
    try:
        check_activate_request()
        drain()
        while not update_result.empty():
            latest, url = update_result.get()
            ov.set_update(latest, url)
        while not results.empty():
            kind, r, title, revision = results.get()
            state["busy"] = False
            if state["rerun"]:  # 分析期间又来了新消息，接着跑最新的
                (t, msgs), state["rerun"] = state["rerun"], None
                start_analyze(t, msgs)
                continue
            if revision != chat_of(title)["rev"]:  # 这个会话后来又说话了，这份结果过期了
                ov.set_busy(False)
                continue
            if kind == "ok":
                chat_of(title)["result"] = r  # 先存着；正看着这个会话才立刻贴上去
                if title == ov.current_chat():
                    ov.show(r)
                    queue_auto_send(title, r, revision)
                else:
                    ov.set_busy(False)
            else:
                ov.set_busy(False)
                ov.set_status("生成失败，请检查网络和服务设置；新消息到来后会重试。", "error")
                ov.log(r)
    except Exception:
        traceback.print_exc()  # 一帧出错不退出
    ov.after(50, tick)


if __name__ == "__main__":  # Windows 的 spawn 会让子进程重新执行本文件，没这行就无限套娃开进程
    multiprocessing.freeze_support()  # 打包成 exe 后 spawn 出来的子进程会重跑一遍 exe，没这行就无限弹界面

    _is_primary, _single_mutex, _single_event = init_single_instance()
    if not _is_primary:
        raise SystemExit(0)

    ctypes.windll.user32.SetProcessDPIAware()
    q = multiprocessing.Queue()
    capture_on = multiprocessing.Event()  # 父子进程共用的开关，置位=采集
    debug_on = multiprocessing.Event()  # 同上，置位=子进程往队列里送整帧给调试窗
    ov = Overlay(on_fill=fill_reply, on_toggle_capture=on_toggle_capture,
                 on_target_change=on_target_change, on_toggle_debug=set_debug,
                 on_regenerate=regenerate_replies,
                 result_of=lambda t: chats.get(t, {}).get("result"))
    child = dbg = None
    try:
        state["hwnd"] = find_wechat_hwnd()
    except RuntimeError:
            ov.set_capture(False, "未找到聊天窗口，打开后再开启采集")
    else:
        capture_on.set()
        child = spawn_worker()
    if settings.debug_view():  # 上次开着就直接开回来
        set_debug(True)
    if not settings.has_jev_key():
        ov.set_status("请先在设置中配置模型", "warning")
        ov.after(0, ov.open_settings)
    if settings.check_update() and update.parse_version(VERSION):  # 开发版没有版本号，不查也不烦源码用户
        threading.Thread(target=check_update_bg, daemon=True).start()
    ov.after(50, tick)
    try:
        ov.run()
    finally:
        if child is not None:
            child.terminate()
        close_single_instance_handles()
