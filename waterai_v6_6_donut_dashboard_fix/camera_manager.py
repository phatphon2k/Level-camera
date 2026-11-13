"""
camera_manager.py - WaterAI V6.2 Performance
Quản lý nhiều camera với giới hạn bộ nhớ:
- Chỉ start worker cho camera enabled.
- Giới hạn FPS xử lý và FPS MJPEG.
- Chỉ giữ 1 JPEG mới nhất mỗi camera.
- Resize stream đầu ra để giảm RAM/CPU/network.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Generator, Optional

import cv2
import numpy as np

from vision import WaterLevelDetector
from database_manager import DatabaseManager


@dataclass
class CameraRuntime:
    id: str
    cfg: Dict
    online: bool = False
    running: bool = False
    fps: float = 0.0
    width: int = 0
    height: int = 0
    error: str = ""
    level: float = 0.0
    status: str = "NO_DATA"
    confidence: float = 0.0
    detect_ok: bool = False
    quality_score: float = 0.0
    occlusion_score: float = 0.0
    vision_reason: str = "NO_DATA"
    last_update: float = 0.0
    latest_jpg: Optional[bytes] = None
    client_count: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)
    cap: Optional[cv2.VideoCapture] = None
    thread: Optional[threading.Thread] = None


class MultiCameraHub:
    def __init__(self, config: Dict, db: Optional[DatabaseManager] = None) -> None:
        self.config = config
        self.db = db
        self.performance = config.get("performance", {}) or {}
        self.vision_cfg = config.get("vision", {}) or {}
        self.detector = WaterLevelDetector(self.vision_cfg)
        self.cameras: Dict[str, CameraRuntime] = {}
        self._build_cameras(config)

    def _build_cameras(self, config: Dict) -> None:
        for cam_cfg in config.get("cameras", []):
            cid = cam_cfg["id"]
            self.cameras[cid] = CameraRuntime(id=cid, cfg=cam_cfg)

    def start(self) -> None:
        for cam in self.cameras.values():
            if cam.cfg.get("enabled", False):
                self._ensure_worker(cam)

    def _ensure_worker(self, cam: CameraRuntime) -> None:
        if cam.thread and cam.thread.is_alive():
            return
        cam.running = True
        cam.thread = threading.Thread(target=self._worker, args=(cam,), daemon=True)
        cam.thread.start()

    def stop(self) -> None:
        for cam in self.cameras.values():
            cam.running = False
            if cam.cap is not None:
                cam.cap.release()

    def update_camera_config(self, camera_id: str, new_cfg: Dict) -> None:
        cam = self.cameras.get(camera_id)
        if not cam:
            return
        with cam.lock:
            cam.cfg.update(new_cfg)
            if "roi" in new_cfg:
                cam.cfg.setdefault("roi", {})
                cam.cfg["roi"].update(new_cfg["roi"])
            if cam.cap is not None:
                try:
                    cam.cap.release()
                except Exception:
                    pass
                cam.cap = None
            cam.online = False
            cam.error = "RECONFIG"
            cam.latest_jpg = None
        if cam.cfg.get("enabled", False):
            self._ensure_worker(cam)

    def summary(self) -> list[Dict]:
        return [self.camera_info(cid) for cid in sorted(self.cameras.keys())]

    def camera_info(self, camera_id: str) -> Dict:
        cam = self.cameras.get(camera_id)
        if not cam:
            return {"id": camera_id, "online": False, "status": "NOT_FOUND"}
        with cam.lock:
            return {
                "id": cam.id,
                "name": cam.cfg.get("name", cam.id),
                "area": cam.cfg.get("area", ""),
                "enabled": bool(cam.cfg.get("enabled", False)),
                "source": cam.cfg.get("source", ""),
                "online": cam.online,
                "level": cam.level,
                "status": cam.status,
                "confidence": cam.confidence,
                "detect_ok": cam.detect_ok,
                "quality_score": cam.quality_score,
                "occlusion_score": cam.occlusion_score,
                "vision_reason": cam.vision_reason,
                "fps": cam.fps,
                "width": cam.width,
                "height": cam.height,
                "error": cam.error,
                "last_update": cam.last_update,
                "roi": cam.cfg.get("roi", {}),
                "show_overlay": bool(cam.cfg.get("show_overlay", False)),
                "low_threshold": cam.cfg.get("low_threshold", 20),
                "high_threshold": cam.cfg.get("high_threshold", 90),
                "process_fps": cam.cfg.get("process_fps", 8),
                "stream_quality": cam.cfg.get("stream_quality", 78),
                "clients": cam.client_count,
            }

    def latest_jpg(self, camera_id: str) -> bytes:
        cam = self.cameras.get(camera_id)
        if not cam:
            return self._placeholder(camera_id, "NOT FOUND")
        with cam.lock:
            if cam.latest_jpg:
                return cam.latest_jpg
            if not cam.cfg.get("enabled", False):
                return self._placeholder(camera_id, "DISABLED")
            return self._placeholder(camera_id, cam.error or "CONNECTING")

    def mjpeg(self, camera_id: str) -> Generator[bytes, None, None]:
        cam = self.cameras.get(camera_id)
        max_clients = int(self.performance.get("max_video_clients_per_camera", 8))
        if cam:
            with cam.lock:
                cam.client_count += 1
                over_limit = cam.client_count > max_clients
            if over_limit:
                yield b"--frame\r\nContent-Type: text/plain\r\n\r\nTOO MANY CLIENTS\r\n"
                with cam.lock:
                    cam.client_count -= 1
                return
        try:
            while True:
                jpg = self.latest_jpg(camera_id)
                yield b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-cache\r\n\r\n" + jpg + b"\r\n"
                fps = float((cam.cfg if cam else {}).get("stream_fps", self.performance.get("mjpeg_fps", 6)))
                time.sleep(max(0.04, 1.0 / max(1.0, fps)))
        finally:
            if cam:
                with cam.lock:
                    cam.client_count = max(0, cam.client_count - 1)

    def _worker(self, cam: CameraRuntime) -> None:
        last_process_time = time.time()
        frame_counter = 0
        last_fps_time = time.time()

        while cam.running:
            try:
                cfg = cam.cfg.copy()
                if not cfg.get("enabled", False):
                    with cam.lock:
                        cam.online = False
                        cam.error = "DISABLED"
                        cam.latest_jpg = None
                    if cam.cap is not None:
                        try:
                            cam.cap.release()
                        except Exception:
                            pass
                        cam.cap = None
                    time.sleep(float(self.performance.get("disabled_sleep_sec", 2)))
                    continue

                source = cfg.get("source", "")
                if source is None or str(source).strip() == "":
                    with cam.lock:
                        cam.online = False
                        cam.error = "NO_SOURCE"
                        cam.latest_jpg = None
                    time.sleep(1)
                    continue

                if cam.cap is None or not cam.cap.isOpened():
                    self._open_camera(cam)
                    if cam.cap is None or not cam.cap.isOpened():
                        time.sleep(float(self.performance.get("reconnect_sec", 3)))
                        continue

                ok, frame = cam.cap.read()
                if not ok or frame is None:
                    with cam.lock:
                        cam.online = False
                        cam.error = "READ_FAIL"
                    try:
                        cam.cap.release()
                    except Exception:
                        pass
                    cam.cap = None
                    time.sleep(1)
                    continue

                now = time.time()
                min_process_fps = float(self.performance.get("min_process_fps", 15))
                target_process_fps = float(self.performance.get("target_process_fps", 24))
                process_fps = float(cfg.get("process_fps", self.performance.get("default_process_fps", target_process_fps)))
                process_fps = max(min_process_fps, process_fps)
                if now - last_process_time < 1.0 / process_fps:
                    time.sleep(0.003)
                    continue
                last_process_time = now

                annotated, result = self.detector.process(cam.id, frame, cfg)
                stream_frame = self._resize_for_stream(annotated, cfg)
                jpg_quality = int(cfg.get("stream_quality", self.performance.get("jpeg_quality", 70)))
                ok_jpg, buffer = cv2.imencode(".jpg", stream_frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpg_quality])

                frame_counter += 1
                if now - last_fps_time >= 1.0:
                    fps = frame_counter / (now - last_fps_time)
                    frame_counter = 0
                    last_fps_time = now
                else:
                    fps = cam.fps

                with cam.lock:
                    cam.online = True
                    cam.error = ""
                    cam.width = int(cam.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or frame.shape[1]
                    cam.height = int(cam.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or frame.shape[0]
                    cam.fps = round(fps, 1)
                    cam.level = result.level
                    cam.status = result.status
                    cam.confidence = result.confidence
                    cam.detect_ok = result.detect_ok
                    cam.quality_score = result.quality_score
                    cam.occlusion_score = result.occlusion_score
                    cam.vision_reason = result.vision_reason
                    cam.last_update = now
                    if ok_jpg:
                        # Chỉ giữ JPEG mới nhất, không giữ frame gốc để tránh phình RAM.
                        cam.latest_jpg = buffer.tobytes()

                if self.db and result.detect_ok:
                    self.db.log(cam.id, cfg.get("name", cam.id), result.level, result.status, result.confidence)

            except Exception as exc:
                with cam.lock:
                    cam.online = False
                    cam.error = str(exc)[:120]
                time.sleep(1)

    def _resize_for_stream(self, frame, cfg: Dict):
        max_w = int(cfg.get("stream_width", self.performance.get("stream_width", 960)) or 0)
        if max_w <= 0 or frame.shape[1] <= max_w:
            return frame
        scale = max_w / float(frame.shape[1])
        new_size = (max_w, max(1, int(frame.shape[0] * scale)))
        return cv2.resize(frame, new_size, interpolation=cv2.INTER_AREA)

    def _open_camera(self, cam: CameraRuntime) -> None:
        source = cam.cfg.get("source", "")
        src = int(source) if str(source).isdigit() else source
        cap = cv2.VideoCapture(src)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            try:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            except Exception:
                pass
            camera_fps = int(cam.cfg.get("camera_fps", self.performance.get("target_camera_fps", 24)) or 0)
            if camera_fps > 0:
                cap.set(cv2.CAP_PROP_FPS, max(15, camera_fps))
            if int(cam.cfg.get("width", 0)) > 0:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(cam.cfg["width"]))
            if int(cam.cfg.get("height", 0)) > 0:
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cam.cfg["height"]))
        except Exception:
            pass

        with cam.lock:
            if cap.isOpened():
                cam.cap = cap
                cam.online = True
                cam.error = ""
            else:
                cam.cap = None
                cam.online = False
                cam.error = "OPEN_FAIL"
                try:
                    cap.release()
                except Exception:
                    pass

    @staticmethod
    def _placeholder(camera_id: str, message: str) -> bytes:
        img = np.zeros((480, 854, 3), dtype=np.uint8)
        img[:] = (18, 28, 45)
        cv2.putText(img, camera_id, (40, 190), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 212, 255), 4, cv2.LINE_AA)
        cv2.putText(img, message, (40, 260), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 166), 2, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        return buf.tobytes() if ok else b""

    def opc_data(self) -> Dict[str, Dict]:
        return {cid: self.camera_info(cid) for cid in sorted(self.cameras.keys())}

    def performance_info(self) -> Dict:
        enabled = [c for c in self.cameras.values() if c.cfg.get("enabled", False)]
        clients = sum(c.client_count for c in self.cameras.values())
        return {
            "enabled_workers": sum(1 for c in enabled if c.thread and c.thread.is_alive()),
            "enabled_cameras": len(enabled),
            "video_clients": clients,
            "gpu": self.detector.info(),
            "performance": self.performance,
        }
