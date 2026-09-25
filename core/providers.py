# -*- coding: utf-8 -*-
"""两张来源表：判断模型 Jev / 起草语言模型。纯数据，不联网、不认 key。

判断层分成两类：
1. OpenRouter：走它自己的 /api/alpha/decisions。
2. System One：TypeSafe、博查、Vercel、OpenCode Zen、硅基流动和自定义地址都走同一套请求格式。

全程仍只有两把 key：
- JEV_API_KEY：判断 / 排序
- LLM_API_KEY：起草候选回复

key 不写进 config.json，只存 Windows 用户环境变量。
"""
from __future__ import annotations

import uuid
from collections import namedtuple

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_DECISIONS = "https://openrouter.ai/api/alpha/decisions"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/auth/key"
TYPESAFE_BASE = "https://api.typesafe.ai"

JEV_ENV = "JEV_API_KEY"
LLM_ENV = "LLM_API_KEY"
LEGACY = {JEV_ENV: "OPENROUTER_API_KEY", LLM_ENV: "DEEPSEEK_API_KEY"}

# name: 设置页显示名
# protocol: openrouter / systemone
# base: 服务根地址。System One 客户端会自动补 /v1/systemone
# default: 默认模型 id
# models: 已知模型列表；None = 尝试动态获取，空元组 = 不提供自动列表
_Jev = namedtuple("_Jev", "name protocol base default models", defaults=(None,))

JEV_PROVIDERS = {
    "openrouter": _Jev(
        "OpenRouter",
        "openrouter",
        "https://openrouter.ai/api",
        "typesafe/jev-1.13",
        ("~typesafe/jev-latest", "typesafe/jev-1.13"),
    ),
    "bocha": _Jev(
        "博查 Jev",
        "systemone",
        "https://jev.bocha.cn",
        "bocha-jev-v1",
        ("bocha-jev-v1",),
    ),
    "typesafe": _Jev(
        "TypeSafe 直连",
        "systemone",
        TYPESAFE_BASE,
        "jev-latest",
        None,
    ),
    "vercel": _Jev(
        "Vercel",
        "systemone",
        "https://ai-gateway.vercel.sh/typesafe",
        "typesafe-ai/jev",
        ("typesafe-ai/jev",),
    ),
    "zen": _Jev(
        "OpenCode Zen",
        "systemone",
        "https://opencode.ai/zen",
        "jev-1.13",
        ("jev-1.13", "jev-1.13-free"),
    ),
    "siliconflow_jev": _Jev(
        "硅基流动 System One",
        "systemone",
        "https://api.siliconflow.cn",
        "diffusiongemma",
        ("diffusiongemma", "Kev-4b", "SemIf"),
    ),
    "custom_systemone": _Jev(
        "自定义 · System One",
        "systemone",
        "",
        "",
        (),
    ),
}

# 只有这个判断来源需要用户自己填 Base URL。
JEV_CUSTOM = ("custom_systemone",)

# protocol ∈ {openai, anthropic, gemini}
_Draft = namedtuple("_Draft", "name protocol base default extra headers keep", defaults=(None, None))
_NONE = lambda on: {}  # noqa: E731

_OPENCODE_HEADERS = {
    "x-opencode-session": str(uuid.uuid4()),
    "User-Agent": "jev-chat-windows",
}
_OPENCODE_CHAT = ("deepseek-", "glm-", "kimi-", "mimo-", "longcat-", "hy", "space-bunny-")
_opencode_chat = lambda model_id: model_id.startswith(_OPENCODE_CHAT)  # noqa: E731

DRAFT_PROVIDERS = {
    "deepseek": _Draft(
        "DeepSeek 官网",
        "openai",
        "https://api.deepseek.com",
        "deepseek-flash",
        lambda on: {"thinking": {"type": "enabled" if on else "disabled"}},
    ),
    "openrouter": _Draft(
        "OpenRouter",
        "openai",
        OPENROUTER_BASE,
        "deepseek/deepseek-v4.1-flash",
        lambda on: {"reasoning": {"enabled": on}},
    ),
    "openai": _Draft("OpenAI", "openai", "https://api.openai.com/v1", "", _NONE),
    "moonshot": _Draft("Moonshot (Kimi)", "openai", "https://api.moonshot.cn/v1", "", _NONE),
    "zhipu": _Draft("智谱 GLM", "openai", "https://open.bigmodel.cn/api/paas/v4", "", _NONE),
    "dashscope": _Draft(
        "通义千问",
        "openai",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "",
        _NONE,
    ),
    "siliconflow": _Draft(
        "硅基流动",
        "openai",
        "https://api.siliconflow.cn/v1",
        "",
        _NONE,
    ),
    "opencode": _Draft(
        "OpenCode Go",
        "openai",
        "https://opencode.ai/zen/go/v1",
        "deepseek-v4.1-flash",
        _NONE,
        _OPENCODE_HEADERS,
        _opencode_chat,
    ),
    "anthropic": _Draft("Anthropic", "anthropic", "https://api.anthropic.com", "", _NONE),
    "gemini": _Draft("Google Gemini", "gemini", "", "", _NONE),
    "custom_openai": _Draft("自定义 · OpenAI 兼容", "openai", "", "", _NONE),
    "custom_anthropic": _Draft("自定义 · Anthropic 兼容", "anthropic", "", "", _NONE),
}

# 起草层的自定义来源。
CUSTOM = ("custom_openai", "custom_anthropic")

THINKING = ("DeepSeek", "OpenRouter", "Anthropic", "Gemini")

ENV_VARS = sorted({JEV_ENV, LLM_ENV, *LEGACY.values()})


if __name__ == "__main__":
    assert {p.protocol for p in JEV_PROVIDERS.values()} == {"openrouter", "systemone"}
    assert JEV_PROVIDERS["openrouter"].base == "https://openrouter.ai/api"
    assert JEV_PROVIDERS["zen"].default == "jev-1.13"
    assert JEV_PROVIDERS["siliconflow_jev"].models == ("diffusiongemma", "Kev-4b", "SemIf")
    assert all(not JEV_PROVIDERS[k].base for k in JEV_CUSTOM)

    assert {p.protocol for p in DRAFT_PROVIDERS.values()} == {"openai", "anthropic", "gemini"}
    assert all(p.base or key in CUSTOM or p.protocol == "gemini"
               for key, p in DRAFT_PROVIDERS.items())
    assert all(not DRAFT_PROVIDERS[key].base for key in CUSTOM)
    assert next(iter(DRAFT_PROVIDERS)) == "deepseek"
    assert DRAFT_PROVIDERS["deepseek"].extra(True) == {"thinking": {"type": "enabled"}}
    assert DRAFT_PROVIDERS["deepseek"].extra(False) == {"thinking": {"type": "disabled"}}
    assert DRAFT_PROVIDERS["openrouter"].extra(True) == {"reasoning": {"enabled": True}}
    assert DRAFT_PROVIDERS["moonshot"].extra(True) == {}
    assert DRAFT_PROVIDERS["deepseek"].headers is None and DRAFT_PROVIDERS["deepseek"].keep is None

    go = DRAFT_PROVIDERS["opencode"]
    assert go.protocol == "openai" and go.base == "https://opencode.ai/zen/go/v1"
    assert go.default == "deepseek-v4.1-flash" and go.extra(True) == {}
    uuid.UUID(go.headers["x-opencode-session"])
    assert go.headers["User-Agent"] == "jev-chat-windows" and "key" not in go.headers
    assert go.keep("deepseek-v4.1-flash") and go.keep("glm-5.3") and go.keep("hy3")
    assert not any(go.keep(m) for m in (
        "minimax-m3", "qwen3.8-max", "grok-4.7", "gpt-6-luna", "muse-spark-1.2-contributor"))

    assert ENV_VARS == ["DEEPSEEK_API_KEY", "JEV_API_KEY", "LLM_API_KEY", "OPENROUTER_API_KEY"]
    print("providers ok")
