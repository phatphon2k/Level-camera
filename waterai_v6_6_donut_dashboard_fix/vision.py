"""
vision.py - WaterAI V6.2
Computer Vision mức nước cho từng camera.
Tối ưu RAM/CPU, ưu tiên OpenCL/UMat nếu OpenCV hỗ trợ GPU/iGPU.
Lưu ý: opencv-python mặc định thường không có CUDA thật; muốn dùng CUDA phải cài OpenCV build với CUDA.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import cv2
import numpy as np


@dataclass
class VisionResult:
    level: float = 0.0
    status: str = "NO_DATA"
    detect_ok: bool = False
    water_y: int = 0
    confidence: float = 0.0
    quality_score: float = 0.0
    occlusion_score: float = 0.0
    vision_reason: str = "NO_DATA"


class WaterLevelDetector:
    def __init__(self, vision_cfg: Dict | None = None) -> None:
        vision_cfg = vision_cfg or {}
        self.history: Dict[str, list[float]] = {}
        self.stable_level: Dict[str, float] = {}
        self.prev_roi_gray: Dict[str, np.ndarray] = {}
        self.reject_streak: Dict[str, int] = {}
        self.history_len = max(1, int(vision_cfg.get("history_len", 8)))
        self.max_level_jump = float(vision_cfg.get("max_level_jump", 14.0))
        self.motion_ratio_threshold = float(vision_cfg.get("motion_ratio_threshold", 0.18))
        self.motion_mean_threshold = float(vision_cfg.get("motion_mean_threshold", 13.0))
        self.min_accept_confidence = float(vision_cfg.get("min_accept_confidence", 28.0))
        self.empty_roi_contrast_threshold = float(vision_cfg.get("empty_roi_contrast_threshold", 12.0))
        self.empty_roi_edge_mean_threshold = float(vision_cfg.get("empty_roi_edge_mean_threshold", 4.8))
        self.empty_roi_edge_peak_threshold = float(vision_cfg.get("empty_roi_edge_peak_threshold", 9.0))
        self.empty_roi_edge_ratio_threshold = float(vision_cfg.get("empty_roi_edge_ratio_threshold", 0.018))
        self.empty_roi_prominence_threshold = float(vision_cfg.get("empty_roi_prominence_threshold", 1.65))
        self.hold_max_frames = max(1, int(vision_cfg.get("hold_max_frames", 48)))
        self.use_opencl = bool(vision_cfg.get("use_opencl", True))
        self.use_cuda = bool(vision_cfg.get("use_cuda", False))
        self.cuda_available = False
        self.gpu_backend = "CPU"

        try:
            self.cuda_available = hasattr(cv2, "cuda") and cv2.cuda.getCudaEnabledDeviceCount() > 0
        except Exception:
            self.cuda_available = False

        # OpenCL T-API: cv2.UMat. Đây là cách an toàn nhất với opencv-python thông thường.
        try:
            if self.use_opencl and cv2.ocl.haveOpenCL():
                cv2.ocl.setUseOpenCL(True)
                if cv2.ocl.useOpenCL():
                    self.gpu_backend = "OpenCL/UMat"
            else:
                cv2.ocl.setUseOpenCL(False)
        except Exception:
            self.use_opencl = False
            self.gpu_backend = "CPU"

        # Chỉ báo CUDA để hiển thị trạng thái; pipeline chính vẫn dùng UMat/CPU để tránh lỗi build OpenCV.
        if self.cuda_available and self.use_cuda:
            self.gpu_backend = "CUDA available / UMat pipeline"

    def info(self) -> Dict:
        return {
            "gpu_backend": self.gpu_backend,
            "opencl": bool(getattr(cv2, "ocl", None) and cv2.ocl.useOpenCL()),
            "cuda_available": self.cuda_available,
            "history_len": self.history_len,
            "max_level_jump": self.max_level_jump,
            "min_accept_confidence": self.min_accept_confidence,
            "empty_roi_contrast_threshold": self.empty_roi_contrast_threshold,
            "empty_roi_edge_mean_threshold": self.empty_roi_edge_mean_threshold,
        }

    @staticmethod
    def _frame_quality(gray) -> tuple[float, str, Dict]:
        brightness = float(gray.mean())
        contrast = float(gray.std())
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        brightness_score = 100.0 - min(100.0, abs(brightness - 115.0) / 1.15)
        contrast_score = min(100.0, contrast * 3.0)
        blur_score = min(100.0, blur / 2.2)
        quality = max(0.0, min(100.0, brightness_score * 0.35 + contrast_score * 0.30 + blur_score * 0.35))

        reasons = []
        if brightness < 35:
            reasons.append("BAD_LIGHTING_DARK")
        elif brightness > 225:
            reasons.append("BAD_LIGHTING_GLARE")
        if contrast < 10:
            reasons.append("LOW_CONTRAST_DIRTY_LENS")
        if blur < 35:
            reasons.append("BLUR_OR_DIRTY_LENS")
        if not reasons:
            reasons.append("OK")

        metrics = {
            "brightness": round(brightness, 1),
            "contrast": round(contrast, 1),
            "blur": round(blur, 1),
        }
        return round(quality, 1), "|".join(reasons), metrics

    @staticmethod
    def _occlusion_score(gray, edge_img) -> float:
        dark_ratio = float((gray < 28).mean())
        bright_ratio = float((gray > 238).mean())
        low_texture_ratio = float((edge_img < 6).mean())
        blocked = max(dark_ratio, bright_ratio) * 65.0 + max(0.0, low_texture_ratio - 0.62) * 90.0
        return round(max(0.0, min(100.0, blocked)), 1)

    def _motion_score(self, camera_id: str, gray) -> tuple[float, float]:
        small = cv2.resize(gray, (160, 96), interpolation=cv2.INTER_AREA)
        prev = self.prev_roi_gray.get(camera_id)
        self.prev_roi_gray[camera_id] = small
        if prev is None or prev.shape != small.shape:
            return 0.0, 0.0
        diff = cv2.absdiff(small, prev)
        motion_ratio = float((diff > 28).mean())
        motion_mean = float(diff.mean())
        return round(motion_ratio, 3), round(motion_mean, 1)

    def _is_empty_roi(self, gray, edge_img, row_score) -> tuple[bool, str]:
        contrast = float(gray.std())
        edge_mean = float(edge_img.mean())
        edge_peak = float(row_score.max()) if row_score.size else 0.0
        edge_ratio = float((edge_img > 18).mean())
        prominence = edge_peak / (edge_mean + 1e-6)

        flat_background = (
            contrast <= self.empty_roi_contrast_threshold
            and edge_mean <= self.empty_roi_edge_mean_threshold
            and edge_ratio <= self.empty_roi_edge_ratio_threshold
        )
        no_clear_surface = (
            edge_peak <= self.empty_roi_edge_peak_threshold
            or prominence <= self.empty_roi_prominence_threshold
        )

        reason = (
            f"EMPTY_METRICS:c{contrast:.1f}/em{edge_mean:.1f}/"
            f"ep{edge_peak:.1f}/er{edge_ratio:.3f}/pr{prominence:.2f}"
        )
        return bool(flat_background and no_clear_surface), reason

    def process(self, camera_id: str, frame, cam_cfg: Dict) -> Tuple[object, VisionResult]:
        if frame is None:
            return frame, VisionResult(status="NO_FRAME")

        h, w = frame.shape[:2]
        roi_cfg = cam_cfg.get("roi", {}) or {}
        x1 = int(roi_cfg.get("x1", 0))
        y1 = int(roi_cfg.get("y1", 0))
        x2 = int(roi_cfg.get("x2", w))
        y2 = int(roi_cfg.get("y2", h))

        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(x1 + 2, min(x2, w))
        y2 = max(y1 + 2, min(y2, h))

        # Không copy toàn frame nếu không bật overlay. Giảm RAM/CPU rõ khi 20 camera.
        show_overlay = bool(cam_cfg.get("show_overlay", False))
        annotated = frame.copy() if show_overlay else frame

        roi = frame[y1:y2, x1:x2]
        if roi.size == 0 or roi.shape[0] < 10 or roi.shape[1] < 10:
            return annotated, VisionResult(status="BAD_ROI", vision_reason="BAD_ROI")

        try:
            if self.gpu_backend.startswith("OpenCL"):
                roi_u = cv2.UMat(roi)
                gray = cv2.cvtColor(roi_u, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (7, 7), 0)
                sobel = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
                sobel = cv2.convertScaleAbs(sobel).get()
            else:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (7, 7), 0)
                sobel = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
                sobel = cv2.convertScaleAbs(sobel)

            gray_np = gray.get() if hasattr(gray, "get") else gray
            quality_score, quality_reason, quality_metrics = self._frame_quality(gray_np)
            occlusion_score = self._occlusion_score(gray_np, sobel)
            motion_ratio, motion_mean = self._motion_score(camera_id, gray_np)

            row_score = sobel.mean(axis=1)
            empty_roi, empty_reason = self._is_empty_roi(gray_np, sobel, row_score)
            if empty_roi:
                self.history[camera_id] = [0.0]
                self.stable_level[camera_id] = 0.0
                self.reject_streak[camera_id] = 0
                roi_h = max(1, y2 - y1)
                water_y = max(0, roi_h - 1)
                level = 0.0
                status = "LOW"
                reasons = [quality_reason, "EMPTY_ROI", empty_reason]
                if motion_ratio >= self.motion_ratio_threshold or motion_mean >= self.motion_mean_threshold:
                    reasons.append(f"MOTION_IN_ROI:{motion_ratio:.2f}/{motion_mean:.1f}")

                if show_overlay:
                    color = (0, 210, 255)
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 120, 0), 2)
                    cv2.line(annotated, (x1, y1 + water_y), (x2, y1 + water_y), color, 2)
                    cv2.putText(
                        annotated,
                        f"{camera_id} {level:.1f}% {status} EMPTY Q{quality_score:.0f}",
                        (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.85,
                        color,
                        2,
                        cv2.LINE_AA,
                    )

                return annotated, VisionResult(
                    level=level,
                    status=status,
                    detect_ok=True,
                    water_y=water_y,
                    confidence=95.0,
                    quality_score=quality_score,
                    occlusion_score=occlusion_score,
                    vision_reason="|".join(reasons),
                )

            water_y = int(np.argmax(row_score))
            peak = float(row_score[water_y])
            mean = float(row_score.mean()) + 1e-6
            confidence = max(0.0, min(100.0, (peak / mean - 1.0) * 35.0))
            confidence = min(confidence, quality_score)

            reasons = [quality_reason]
            if occlusion_score >= 70:
                reasons.append("OCCLUDED")
            elif occlusion_score >= 45:
                reasons.append("POSSIBLE_OCCLUSION")
            if confidence < 25:
                reasons.append("LOW_CONFIDENCE")

            roi_h = max(1, y2 - y1)
            raw_level = (roi_h - water_y) / roi_h * 100.0
            raw_level = max(0.0, min(100.0, raw_level))

            last_stable = self.stable_level.get(camera_id)
            level_jump = abs(raw_level - last_stable) if last_stable is not None else 0.0
            moving_object = (
                motion_ratio >= self.motion_ratio_threshold
                or motion_mean >= self.motion_mean_threshold
            )
            unstable_detection = (
                occlusion_score >= 45
                or confidence < self.min_accept_confidence
                or (moving_object and level_jump > self.max_level_jump * 0.55)
                or level_jump > self.max_level_jump
            )
            if moving_object:
                reasons.append(f"MOTION_IN_ROI:{motion_ratio:.2f}/{motion_mean:.1f}")

            buf = self.history.setdefault(camera_id, [])
            if unstable_detection and last_stable is not None:
                self.reject_streak[camera_id] = self.reject_streak.get(camera_id, 0) + 1
                if self.reject_streak[camera_id] <= self.hold_max_frames:
                    level = round(last_stable, 1)
                    detect_ok = False
                    reasons.append("INTRUSION_HOLD")
                    confidence = min(confidence, 20.0)
                else:
                    level = raw_level
                    detect_ok = True
                    reasons.append("HOLD_TIMEOUT_ACCEPT")
                    buf.append(raw_level)
                    if len(buf) > self.history_len:
                        del buf[:-self.history_len]
                    self.stable_level[camera_id] = round(sum(buf) / len(buf), 1)
            else:
                self.reject_streak[camera_id] = 0
                buf.append(raw_level)
                if len(buf) > self.history_len:
                    del buf[:-self.history_len]
                level = round(sum(buf) / len(buf), 1)
                self.stable_level[camera_id] = level
                detect_ok = True

            low = float(cam_cfg.get("low_threshold", 20))
            high = float(cam_cfg.get("high_threshold", 90))
            if level < low:
                status = "LOW"
            elif level > high:
                status = "HIGH"
            else:
                status = "NORMAL"

            if show_overlay:
                color = (0, 255, 0)
                if status == "LOW":
                    color = (0, 210, 255)
                elif status == "HIGH":
                    color = (0, 0, 255)
                cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 120, 0), 2)
                cv2.line(annotated, (x1, y1 + water_y), (x2, y1 + water_y), color, 2)
                cv2.putText(
                    annotated,
                    f"{camera_id} {level:.1f}% {status} Q{quality_score:.0f} M{motion_ratio:.2f}",
                    (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.85,
                    color,
                    2,
                    cv2.LINE_AA,
                )

            return annotated, VisionResult(
                level=level,
                status=status,
                detect_ok=detect_ok,
                water_y=water_y,
                confidence=round(confidence, 1),
                quality_score=quality_score,
                occlusion_score=occlusion_score,
                vision_reason="|".join(reasons),
            )
        except Exception:
            return annotated, VisionResult(status="VISION_ERROR", vision_reason="VISION_ERROR")
