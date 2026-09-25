# JevChat-Windows 开发环境

这个分支专门用于前期调试，不替代 main 正式发布版。

开发环境采用“精简冻结运行时 + 外置业务源码”：

- `JevChat-Dev.exe + _internal/`：固定运行环境，像正式版一样裁剪
- `main.py / app/ / core/`：放在外面，可直接替换
- 普通功能修改不需要重新打完整包

## 第一次使用

1. 到 GitHub Actions 下载最新成功的 `JevChat-Windows-Dev`。
2. 解压到固定目录，例如 `D:\JevChat-Windows-Dev\`。
3. 双击 `JevChat-Dev.exe`。

不需要另外安装 Python。

## 目录结构

```text
JevChat-Windows-Dev/
├─ JevChat-Dev.exe
├─ _internal/               精简 Python 运行时和第三方依赖
├─ main.py                  外置业务源码
├─ app/                     外置业务源码
├─ core/                    外置业务源码
├─ 更新开发源码.cmd
├─ 更新开发源码.ps1
├─ config.json              运行后生成；更新源码不会删除
└─ chat_profiles.json       运行后生成；更新源码不会删除
```

## 日常测试新修改

关闭程序后双击 `更新开发源码.cmd`。

它只会从 `dev-external-source` 更新：

- `main.py`
- `app/`
- `core/`

不会动：

- `JevChat-Dev.exe`
- `_internal/`
- `config.json`
- `chat_profiles.json`

更新后重新打开 `JevChat-Dev.exe` 即可。

## 什么时候才重新下载完整开发环境

只有这些情况：

- requirements.txt 变化
- Python 版本变化
- PySide6 / RapidOCR / onnxruntime 等底层依赖变化
- 开发版启动器或 dev.spec 变化

普通 UI、逻辑、API、提示词修改，只更新源码。
