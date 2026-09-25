# -*- coding: utf-8 -*-
"""JevChat 开发版启动器。

PyInstaller 只冻结 Python 运行时和第三方依赖；真正业务代码 main.py / app / core
全部留在 exe 外面。以后改业务代码只覆盖这些文件，运行时不用重打。
"""
from __future__ import annotations

import os
import runpy
import sys
import traceback
import ctypes


def _root() -> str:
    return os.path.dirname(sys.executable)


def _message(text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "JevChat 开发版", 0x10)
    except Exception:
        pass


def main() -> None:
    root = _root()
    entry = os.path.join(root, "main.py")
    if not os.path.isfile(entry):
        _message("缺少 main.py。请运行“更新开发源码.cmd”，或重新下载开发环境。")
        return

    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        runpy.run_path(entry, run_name="__main__")
    except SystemExit:
        raise
    except Exception:
        detail = traceback.format_exc()
        _message("启动失败。\n\n" + detail[-3000:])


if __name__ == "__main__":
    main()
