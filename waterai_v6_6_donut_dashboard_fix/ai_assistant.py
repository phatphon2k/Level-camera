"""Optional multi-model RAG adapter for WaterAI.

The app keeps working without any LLM server. When enabled, this adapter tries
configured providers in order and returns the first successful answer. If all
models fail, app.py falls back to the deterministic rule-based chatbot.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional


def _compact_camera(cam: Dict) -> Dict:
    return {
        "id": cam.get("id"),
        "name": cam.get("name"),
        "area": cam.get("area"),
        "enabled": cam.get("enabled"),
        "online": cam.get("online"),
        "level": cam.get("level"),
        "status": cam.get("status"),
        "confidence": cam.get("confidence"),
        "quality_score": cam.get("quality_score"),
        "occlusion_score": cam.get("occlusion_score"),
        "vision_reason": cam.get("vision_reason"),
        "fps": cam.get("fps"),
        "source": cam.get("source"),
        "low_threshold": cam.get("low_threshold"),
        "high_threshold": cam.get("high_threshold"),
    }


def build_rag_context(snapshot: Dict, camera_id: str) -> str:
    cameras = snapshot.get("camera_ai", {}).get("cameras", [])
    selected = snapshot.get("camera_ai", {}).get("selected", {})
    alarms = snapshot.get("alarms", {}).get("active", [])
    scada = snapshot.get("scada", {})
    system = snapshot.get("system", {})
    issues = [
        _compact_camera(cam)
        for cam in cameras
        if cam.get("enabled") and (
            not cam.get("online")
            or str(cam.get("status", "")).upper() in {"LOW", "HIGH"}
            or float(cam.get("quality_score", 100) or 0) < 45
            or float(cam.get("occlusion_score", 0) or 0) > 45
        )
    ][:8]

    context = {
        "system": system,
        "selected_camera": _compact_camera(selected),
        "issue_cameras": issues,
        "active_alarms": alarms[:8],
        "scada": {
            "opcua_running": scada.get("opcua_running"),
            "opcua_endpoint": scada.get("opcua_endpoint"),
            "opcua_namespace": scada.get("opcua_namespace"),
            "tag_path": f"WaterAI/{camera_id}",
            "tag_names": ["WaterLevel", "StatusCode", "StatusText", "CameraOnline", "FPS", "Confidence"],
        },
        "sop": [
            "Neu CameraOnline=false: kiem tra source RTSP/USB, mang, nguon camera va quyen truy cap.",
            "Neu quality_score thap: kiem tra anh sang, lau kinh camera, kiem tra rung/lech focus.",
            "Neu occlusion_score cao: kiem tra vat can trong ROI, nguoi di qua, vat noi hoac che mat nuoc.",
            "Neu confidence thap: khong nen dieu khien tu dong; can xac nhan bang snapshot/trend/history.",
            "Neu LOW/HIGH keo dai: kiem tra bom, van, cam bien phu tro va threshold cau hinh.",
        ],
    }
    return json.dumps(context, ensure_ascii=False, separators=(",", ":"))


def _provider_list(cfg: Dict) -> List[Dict]:
    providers = cfg.get("providers")
    if isinstance(providers, list) and providers:
        return [p for p in providers if isinstance(p, dict) and p.get("model") and p.get("enabled", True)]
    return [{
        "name": cfg.get("provider_name", "ollama-local"),
        "type": "ollama",
        "model": cfg.get("model", "llama3.1:8b"),
        "url": cfg.get("ollama_url"),
        "timeout_sec": cfg.get("timeout_sec"),
    }]


def _build_prompt(message: str, snapshot: Dict, camera_id: str) -> str:
    context = build_rag_context(snapshot, camera_id)
    return (
        "Ban la tro ly van hanh WaterAI cho camera do muc nuoc va SCADA. "
        "Chi dung du lieu CONTEXT, khong bia so lieu. "
        "Tra loi ngan, thuc dung, bang tieng Viet. "
        "Neu du lieu realtime thieu, noi ro la chua co du lieu.\n"
        f"CONTEXT={context}\n"
        f"USER={message}\n"
        "ANSWER="
    )


def _ollama_generate(provider: Dict, prompt: str, cfg: Dict) -> str:
    url = provider.get("url") or cfg.get("ollama_url", "http://127.0.0.1:11434/api/generate")
    timeout = float(provider.get("timeout_sec", cfg.get("timeout_sec", 8)))
    options = {
        "temperature": float(provider.get("temperature", cfg.get("temperature", 0.2))),
        "num_predict": int(provider.get("num_predict", cfg.get("num_predict", 260))),
    }
    body = json.dumps({
        "model": provider["model"],
        "prompt": prompt,
        "stream": False,
        "options": options,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
        answer = str(data.get("response", "")).strip()
        if not answer:
            raise RuntimeError("EMPTY_RESPONSE")
        return answer


def _openai_generate(provider: Dict, prompt: str, cfg: Dict) -> str:
    """Call an OpenAI-compatible Chat Completions endpoint using a server env key."""
    key_env = str(provider.get("api_key_env") or "OPENAI_API_KEY")
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        try:
            key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets", "external_llm_api_key.txt")
            with open(key_file, "r", encoding="utf-8") as key_stream:
                api_key = key_stream.read().strip()
        except OSError:
            pass
    if not api_key:
        raise RuntimeError(f"MISSING_API_KEY:{key_env}")

    url = str(provider.get("url") or "https://api.openai.com/v1/chat/completions")
    timeout = float(provider.get("timeout_sec", cfg.get("timeout_sec", 30)))
    body = json.dumps({
        "model": provider["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(provider.get("temperature", cfg.get("temperature", 0.2))),
        "max_tokens": int(provider.get("max_tokens", cfg.get("num_predict", 260))),
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    choices = data.get("choices") or []
    answer = str((choices[0].get("message") or {}).get("content") or "").strip() if choices else ""
    if not answer:
        raise RuntimeError("EMPTY_RESPONSE")
    return answer


def _anthropic_generate(provider: Dict, prompt: str, cfg: Dict) -> str:
    key_env = str(provider.get("api_key_env") or "ANTHROPIC_API_KEY")
    api_key = os.environ.get(key_env, "").strip()
    if not api_key:
        try:
            key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "secrets", "external_llm_api_key.txt")
            with open(key_file, "r", encoding="utf-8") as key_stream:
                api_key = key_stream.read().strip()
        except OSError:
            pass
    if not api_key:
        raise RuntimeError(f"MISSING_API_KEY:{key_env}")

    url = str(provider.get("url") or "https://api.anthropic.com/v1/messages")
    timeout = float(provider.get("timeout_sec", cfg.get("timeout_sec", 30)))
    body = json.dumps({
        "model": provider["model"],
        "max_tokens": int(provider.get("max_tokens", cfg.get("num_predict", 260))),
        "messages": [{"role": "user", "content": prompt}],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    answer = "\n".join(str(part.get("text", "")) for part in data.get("content", []) if part.get("type") == "text").strip()
    if not answer:
        raise RuntimeError("EMPTY_RESPONSE")
    return answer


def _provider_url(provider: Dict, cfg: Dict) -> str:
    if provider.get("url"):
        return str(provider["url"])
    ptype = str(provider.get("type", "ollama")).lower()
    if ptype == "openai":
        return "https://api.openai.com/v1/chat/completions"
    if ptype == "anthropic":
        return "https://api.anthropic.com/v1/messages"
    return str(cfg.get("ollama_url", "http://127.0.0.1:11434/api/generate"))


def ask_llm_with_failover(message: str, snapshot: Dict, camera_id: str, chatbot_cfg: Dict | None = None) -> Dict:
    cfg = chatbot_cfg or {}
    if not cfg.get("llm_enable", False):
        return {"answer": None, "used": False, "provider": None, "model": None, "trace": [{"status": "disabled"}]}

    prompt = _build_prompt(message, snapshot, camera_id)
    trace = []
    down_urls = set()
    for provider in _provider_list(cfg):
        started = time.time()
        ptype = str(provider.get("type", "ollama")).lower()
        name = provider.get("name") or provider.get("model")
        url = _provider_url(provider, cfg)
        if url in down_urls:
            trace.append({
                "provider": name,
                "model": provider.get("model"),
                "status": "skipped",
                "error": "ENDPOINT_DOWN",
                "latency_ms": 0,
            })
            continue
        try:
            if ptype == "ollama":
                answer = _ollama_generate(provider, prompt, cfg)
            elif ptype in {"openai", "openai_compatible"}:
                answer = _openai_generate(provider, prompt, cfg)
            elif ptype == "anthropic":
                answer = _anthropic_generate(provider, prompt, cfg)
            else:
                raise RuntimeError(f"UNSUPPORTED_PROVIDER:{ptype}")
            trace.append({
                "provider": name,
                "model": provider.get("model"),
                "status": "ok",
                "latency_ms": int((time.time() - started) * 1000),
            })
            return {
                "answer": answer,
                "used": True,
                "provider": name,
                "model": provider.get("model"),
                "trace": trace,
            }
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, RuntimeError) as exc:
            if isinstance(exc, (OSError, urllib.error.URLError)) and not isinstance(exc, urllib.error.HTTPError):
                down_urls.add(url)
            trace.append({
                "provider": name,
                "model": provider.get("model"),
                "status": "error",
                "error": str(exc)[:160],
                "latency_ms": int((time.time() - started) * 1000),
            })

    return {"answer": None, "used": False, "provider": None, "model": None, "trace": trace}


def ask_local_llm(message: str, snapshot: Dict, camera_id: str, chatbot_cfg: Dict | None = None) -> Optional[str]:
    return ask_llm_with_failover(message, snapshot, camera_id, chatbot_cfg).get("answer")


def check_llm_providers(chatbot_cfg: Dict | None = None) -> Dict:
    cfg = chatbot_cfg or {}
    providers = _provider_list(cfg)
    prompt = "Tra loi mot tu: OK"
    results = []
    down_urls = set()

    for provider in providers:
        started = time.time()
        ptype = str(provider.get("type", "ollama")).lower()
        name = provider.get("name") or provider.get("model")
        url = _provider_url(provider, cfg)
        if url in down_urls:
            results.append({
                "provider": name,
                "type": ptype,
                "model": provider.get("model"),
                "status": "skipped",
                "error": "ENDPOINT_DOWN",
                "latency_ms": 0,
            })
            continue

        try:
            if ptype == "ollama":
                answer = _ollama_generate({**provider, "num_predict": 8, "timeout_sec": provider.get("timeout_sec", cfg.get("timeout_sec", 8))}, prompt, cfg)
            elif ptype in {"openai", "openai_compatible"}:
                answer = _openai_generate({**provider, "max_tokens": 8, "timeout_sec": provider.get("timeout_sec", cfg.get("timeout_sec", 30))}, prompt, cfg)
            elif ptype == "anthropic":
                answer = _anthropic_generate({**provider, "max_tokens": 8, "timeout_sec": provider.get("timeout_sec", cfg.get("timeout_sec", 30))}, prompt, cfg)
            else:
                raise RuntimeError(f"UNSUPPORTED_PROVIDER:{ptype}")
            results.append({
                "provider": name,
                "type": ptype,
                "model": provider.get("model"),
                "status": "ok",
                "sample": answer[:80],
                "latency_ms": int((time.time() - started) * 1000),
            })
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, RuntimeError) as exc:
            if isinstance(exc, (OSError, urllib.error.URLError)) and not isinstance(exc, urllib.error.HTTPError):
                down_urls.add(url)
            results.append({
                "provider": name,
                "type": ptype,
                "model": provider.get("model"),
                "status": "error",
                "error": str(exc)[:160],
                "latency_ms": int((time.time() - started) * 1000),
            })

    return {
        "mini_agent_enable": bool(cfg.get("mini_agent_enable", True)),
        "agent_use_llm": bool(cfg.get("agent_use_llm", True)),
        "llm_enable": bool(cfg.get("llm_enable", False)),
        "fallback_to_rules": bool(cfg.get("fallback_to_rules", True)),
        "providers": results,
    }
