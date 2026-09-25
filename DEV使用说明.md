# JevChat-Windows 开发环境

这个分支专门用于前期调试，不替代 main 正式发布版。

## 第一次使用

1. 到 GitHub Actions 下载 `JevChat-Windows-Dev` 构建产物。
2. 解压到固定目录，例如：

```text
D:\JevChat-Windows-Dev\
```

3. 双击：

```text
JevChat-Dev.exe
```

不需要另外安装 Python。

## 目录作用

```text
JevChat-Windows-Dev/
├─ JevChat-Dev.exe          启动器，基本不需要更换
├─ runtime/                 Python 3.11 + 所有大型依赖，基本不需要更换
├─ main.py                  外置业务源码，可直接替换
├─ app/                     外置业务源码，可直接替换
├─ core/                    外置业务源码，可直接替换
├─ 更新开发源码.cmd         一键拉取本分支最新源码
├─ 更新开发源码.ps1
├─ config.json              运行后生成，全局设置；更新源码不会删除
└─ chat_profiles.json       运行后生成，会话关系；更新源码不会删除
```

## 以后怎么测试新修改

程序关闭后，双击：

```text
更新开发源码.cmd
```

它只会从 `dev-external-source` 下载并替换：

- `main.py`
- `app/`
- `core/`

不会动：

- `runtime/`
- `config.json`
- `chat_profiles.json`

更新完重新双击 `JevChat-Dev.exe` 即可。

## 什么时候才需要重新下载完整开发环境

只有这些情况才需要重新构建运行环境：

- requirements.txt 依赖发生变化
- Python 版本变化
- PySide6 / RapidOCR / onnxruntime 等底层依赖变化
- 启动器本身变化

普通界面、逻辑、API 适配、提示词修改，都只更新源码即可。

## 分支约定

- `main`：正式发布版，继续 PyInstaller 完整打包
- `dev-external-source`：开发调试版，运行环境固定，业务源码外置
