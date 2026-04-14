"""LLM provider 抽象层。

支持 anthropic / openai / ollama，通过 `config/models.yaml` 配置别名。
调用前用 `load_models()` 读一次别名表，然后 `call_llm(alias, system, user)`。

设计：直接依赖各家官方 SDK，不引 litellm（少一层依赖 & 少一个可能的故障点）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None  # type: ignore


@dataclass
class ModelSpec:
    alias: str
    provider: str
    model: str
    max_tokens: int = 8000
    base_url: str | None = None


_MODELS: dict[str, ModelSpec] = {}


def load_models(config_path: Path) -> dict[str, ModelSpec]:
    global _MODELS
    if yaml is None:
        raise RuntimeError("缺 pyyaml 依赖：pip install pyyaml")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    out: dict[str, ModelSpec] = {}
    for alias, spec in raw.items():
        out[alias] = ModelSpec(
            alias=alias,
            provider=spec["provider"],
            model=spec["model"],
            max_tokens=spec.get("max_tokens", 8000),
            base_url=spec.get("base_url"),
        )
    _MODELS = out
    return out


def resolve(alias: str) -> ModelSpec:
    if alias not in _MODELS:
        raise KeyError(f"未知模型别名 {alias!r}，可选：{list(_MODELS.keys())}")
    return _MODELS[alias]


def call_llm(
    alias: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int | None = None,
    temperature: float = 0.3,
    retries: int = 2,
) -> str:
    """调 LLM，返回纯文本。失败重试 `retries` 次（总共 retries+1 次调用）。"""
    spec = resolve(alias)
    max_tokens = max_tokens or spec.max_tokens
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if spec.provider == "anthropic":
                return _call_anthropic(spec, system_prompt, user_content, max_tokens, temperature)
            if spec.provider == "openai":
                return _call_openai(spec, system_prompt, user_content, max_tokens, temperature)
            if spec.provider == "ollama":
                return _call_ollama(spec, system_prompt, user_content, max_tokens, temperature)
            raise ValueError(f"未知 provider {spec.provider!r}")
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败（{retries+1} 次后）：{last_err}") from last_err


def _call_anthropic(spec: ModelSpec, system: str, user: str, max_tokens: int, temperature: float) -> str:
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise RuntimeError("缺 anthropic 依赖：pip install anthropic") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("缺环境变量 ANTHROPIC_API_KEY")
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=spec.model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "".join(parts)


def _call_openai(spec: ModelSpec, system: str, user: str, max_tokens: int, temperature: float) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise RuntimeError("缺 openai 依赖：pip install openai") from e
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("缺环境变量 OPENAI_API_KEY")
    client = OpenAI()
    resp = client.chat.completions.create(
        model=spec.model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def _call_ollama(spec: ModelSpec, system: str, user: str, max_tokens: int, temperature: float) -> str:
    base = spec.base_url or "http://localhost:11434"
    payload = {
        "model": spec.model,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        f"{base}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("message", {}).get("content", "")
