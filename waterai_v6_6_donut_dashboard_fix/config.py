"""
config.py
Quản lý cấu hình WaterAI đa camera.
- Mặc định tạo 20 camera slot.
- Chỉ CAM01 bật webcam 0 để demo, các camera còn lại chờ cấu hình RTSP/HTTP/webcam.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, List


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")


def _default_cameras() -> List[Dict[str, Any]]:
    cams: List[Dict[str, Any]] = []
    for i in range(1, 21):
        cam_id = f"CAM{i:02d}"
        cams.append({
            "id": cam_id,
            "name": f"Bể {i:02d}",
            "area": f"Khu vực {i:02d}",
            "enabled": True if i == 1 else False,
            "source": 0 if i == 1 else "",
            "roi": {"x1": 100, "y1": 80, "x2": 520, "y2": 420},
            "low_threshold": 20,
            "high_threshold": 90,
            "show_overlay": False,
            "process_fps": 24,
            "stream_quality": 78,
            "stream_fps": 15,
            "stream_width": 960,
            "camera_fps": 24,
            "width": 0,
            "height": 0
        })
    return cams


DEFAULT_CONFIG: Dict[str, Any] = {
    "system": {
        "name": "WaterAI Vision System",
        "version": "V6.0-20CAM",
        "language": "vi",
        "theme": "dark",
        "default_camera": "CAM01",
        "max_cameras": 20,
        "log_interval_sec": 5
    },
    "web": {
        "host": "127.0.0.1",
        "port": 5530,
        "threads": 80
    },
    "opcua": {
        "enable": False,
        "endpoint": "opc.tcp://127.0.0.1:4846/freeopcua/server/",
        "namespace": "http://automation.waterai.io",
        "server_name": "WaterAI OPC UA Server"
    },
    "database": {
        "sqlite_enable": True,
        "sqlite_file": "database/history.db"
    },
    "performance": {
        "mjpeg_fps": 15,
        "default_process_fps": 24,
        "min_process_fps": 15,
        "target_process_fps": 24,
        "target_camera_fps": 24,
        "stream_width": 960,
        "jpeg_quality": 70,
        "max_video_clients_per_camera": 32,
        "disabled_sleep_sec": 2,
        "reconnect_sec": 3
    },
    "vision": {
        "history_len": 8,
        "max_level_jump": 14.0,
        "motion_ratio_threshold": 0.18,
        "motion_mean_threshold": 13.0,
        "min_accept_confidence": 28.0,
        "empty_roi_contrast_threshold": 12.0,
        "empty_roi_edge_mean_threshold": 4.8,
        "empty_roi_edge_peak_threshold": 9.0,
        "empty_roi_edge_ratio_threshold": 0.018,
        "empty_roi_prominence_threshold": 1.65,
        "hold_max_frames": 48,
        "use_opencl": True,
        "use_cuda": False
    },
    "auth": {
        "session_secret": "waterai-session-change-me-2026",
        "users": [
            {
                "username": "admin",
                "password_hash": "a3d0d1be4661b6af1e4c5dc222b2a0b524ab8491f419f8e33c56ef4833715941",
                "role": "admin",
                "enabled": True
            }
        ]
    },
    "chatbot": {
        "mini_agent_enable": True,
        "agent_use_llm": True,
        "llm_enable": False,
        "fallback_to_rules": True,
        "ollama_url": "http://127.0.0.1:11434/api/generate",
        "model": "qwen2.5:7b-instruct",
        "timeout_sec": 8,
        "temperature": 0.2,
        "num_predict": 260,
        "providers": [
            {
                "name": "qwen-local",
                "type": "ollama",
                "model": "qwen2.5:7b-instruct",
                "timeout_sec": 8
            },
            {
                "name": "llama-local",
                "type": "ollama",
                "model": "llama3.1:8b",
                "timeout_sec": 8
            },
            {
                "name": "gemma-local",
                "type": "ollama",
                "model": "gemma2:9b",
                "timeout_sec": 8
            },
            {
                "name": "openai-api",
                "type": "openai",
                "model": "gpt-4o-mini",
                "enabled": False,
                "api_key_env": "OPENAI_API_KEY",
                "timeout_sec": 30,
                "max_tokens": 260
            }
        ]
    },
    "cameras": _default_cameras()
}


class Config:
    @staticmethod
    def load() -> Dict[str, Any]:
        if not os.path.exists(CONFIG_FILE):
            Config.save(DEFAULT_CONFIG, merge=False)

        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except Exception:
            loaded = {}

        cfg = Config._merge_defaults(loaded)

        # Tự lưu lại nếu thiếu key mới.
        if cfg != loaded:
            Config.save(cfg, merge=False)

        return cfg

    @staticmethod
    def save(data: Dict[str, Any], merge: bool = True) -> Dict[str, Any]:
        if merge:
            cfg = Config.load()
            cfg = Config._deep_update(cfg, data)
        else:
            cfg = data

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
        return cfg

    @staticmethod
    def get_camera(cfg: Dict[str, Any], camera_id: str) -> Dict[str, Any] | None:
        camera_id = str(camera_id).upper()
        for cam in cfg.get("cameras", []):
            if str(cam.get("id", "")).upper() == camera_id:
                return cam
        return None

    @staticmethod
    def update_camera(camera_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        cfg = Config.load()
        cam = Config.get_camera(cfg, camera_id)
        if cam is None:
            raise ValueError(f"Không tìm thấy camera {camera_id}")

        allowed = {
            "name", "area", "enabled", "source", "roi", "low_threshold",
            "high_threshold", "show_overlay", "process_fps", "stream_quality",
            "stream_fps", "stream_width", "camera_fps", "width", "height"
        }
        for key, value in payload.items():
            if key in allowed:
                if key == "roi" and isinstance(value, dict):
                    cam.setdefault("roi", {})
                    cam["roi"].update(value)
                elif key in {"process_fps", "stream_fps", "camera_fps"}:
                    cam[key] = max(15, int(float(value or 24)))
                else:
                    cam[key] = value

        Config.save(cfg, merge=False)
        return cam

    @staticmethod
    def _merge_defaults(loaded: Dict[str, Any]) -> Dict[str, Any]:
        cfg = deepcopy(DEFAULT_CONFIG)
        cfg = Config._deep_update(cfg, loaded)

        # Đảm bảo đủ 20 camera slot, giữ cấu hình người dùng nếu đã có.
        existing = {str(c.get("id", "")).upper(): c for c in cfg.get("cameras", [])}
        final_cams = []
        for default_cam in _default_cameras():
            cid = default_cam["id"]
            cam = deepcopy(default_cam)
            if cid in existing:
                cam = Config._deep_update(cam, existing[cid])
            final_cams.append(cam)
        cfg["cameras"] = final_cams[:20]
        return cfg

    @staticmethod
    def _deep_update(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
        for key, value in update.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = Config._deep_update(base[key], value)
            else:
                base[key] = value
        return base
