from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple


VOICE_LANGUAGES = {
    "vi-VN": "Tieng Viet",
    "en-US": "English",
    "zh-CN": "Tieng Trung",
    "ja-JP": "Tieng Nhat",
    "ko-KR": "Tieng Han",
}

VOICE_STYLES = {
    "male-deep": {"label": "Nam tram", "gender": "male", "rate": 0.96, "pitch": 0.88},
    "male-standard": {"label": "Nam chuan", "gender": "male", "rate": 1.0, "pitch": 0.98},
    "female-natural": {"label": "Nu tu nhien", "gender": "female", "rate": 1.02, "pitch": 1.08},
    "female-warm": {"label": "Nu am", "gender": "female", "rate": 0.98, "pitch": 1.02},
    "female-bright": {"label": "Nu sang", "gender": "female", "rate": 1.06, "pitch": 1.16},
}


class EmbeddedTtsEngine:
    """Project-local TTS runner.

    The app never uses OS/browser voices here. Voice models are resolved from
    tts/voices/manifest.json and executed by a bundled Piper binary.
    """

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir).resolve()
        self.tts_dir = self.base_dir / "tts"
        self.voices_dir = self.tts_dir / "voices"
        self.manifest_file = self.voices_dir / "manifest.json"
        self.bin_dir = self.tts_dir / "bin"
        self.manifest = self._load_manifest()

    def _load_manifest(self) -> Dict:
        if not self.manifest_file.exists():
            return {"voices": []}
        try:
            return json.loads(self.manifest_file.read_text(encoding="utf-8"))
        except Exception:
            return {"voices": []}

    def _piper_executable(self) -> Path | None:
        names = ["piper.exe", "piper"]
        for name in names:
            candidate = self.bin_dir / name
            if candidate.exists():
                return candidate
        return None

    def voices(self) -> List[Dict]:
        rows = []
        for voice in self.manifest.get("voices", []):
            model_path = self.voices_dir / voice.get("model", "")
            config_path = self.voices_dir / voice.get("config", "")
            rows.append({
                **voice,
                "available": model_path.exists() and config_path.exists(),
                "model_path": str(model_path),
                "config_path": str(config_path),
            })
        return rows

    def voice_for(self, language: str, style: str) -> Dict | None:
        language = language if language in VOICE_LANGUAGES else "vi-VN"
        style = style if style in VOICE_STYLES else "female-natural"
        for voice in self.voices():
            if voice.get("language") == language and voice.get("style") == style:
                return voice
        return None

    def status(self) -> Dict:
        exe = self._piper_executable()
        rows = self.voices()
        return {
            "engine": "piper-local",
            "engine_available": bool(exe),
            "engine_path": str(exe) if exe else "",
            "voices_total": len(rows),
            "voices_available": len([v for v in rows if v.get("available")]),
            "voices": [{k: v for k, v in row.items() if k not in {"model_path", "config_path"}} for row in rows],
        }

    def synthesize(self, text: str, language: str, style: str, rate: float = 1.0, pitch: float = 1.0) -> Tuple[bytes, Dict]:
        text = str(text or "").strip()
        if not text:
            raise ValueError("No text to synthesize")
        text = text[:1200]

        exe = self._piper_executable()
        if not exe:
            raise RuntimeError("Chua co tts/bin/piper.exe trong project")

        voice = self.voice_for(language, style)
        if not voice:
            raise RuntimeError(f"Chua khai bao voice {language}/{style}")
        if not voice.get("available"):
            raise RuntimeError(f"Chua co model voice trong project: {voice.get('model')}")

        model_path = voice["model_path"]
        config_path = voice["config_path"]
        length_scale = self._length_scale(rate, voice)
        noise_scale = float(voice.get("noise_scale", 0.667))
        noise_w = float(voice.get("noise_w", 0.8))

        with tempfile.TemporaryDirectory(prefix="waterai_tts_") as tmp:
            wav_path = os.path.join(tmp, "tts.wav")
            cmd = [
                str(exe),
                "--model", model_path,
                "--config", config_path,
                "--output_file", wav_path,
                "--length_scale", f"{length_scale:.3f}",
                "--noise_scale", f"{noise_scale:.3f}",
                "--noise_w", f"{noise_w:.3f}",
            ]
            proc = subprocess.run(
                cmd,
                input=text + "\n",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                cwd=str(self.tts_dir),
            )
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or "Piper TTS error").strip()[:500])
            with open(wav_path, "rb") as f:
                audio = f.read()
        meta = {
            "voice_id": voice.get("id"),
            "language": voice.get("language"),
            "style": voice.get("style"),
            "engine": "piper-local",
        }
        return audio, meta

    @staticmethod
    def _length_scale(rate: float, voice: Dict) -> float:
        # Piper uses lower length_scale for faster speech.
        base = float(voice.get("length_scale", 1.0))
        try:
            rate = max(0.7, min(1.35, float(rate or 1.0)))
        except Exception:
            rate = 1.0
        return max(0.55, min(1.65, base / rate))
