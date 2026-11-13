"""
app.py - WaterAI V6 Multi Camera
- 20 camera slots
- Grid/pin camera
- REST API cho web và WinCC Unified
- OPC UA optional cho WinCC Unified
- Giữ các route cũ: /video, /water, /config, /save_config, /tag
"""

from __future__ import annotations

import os
import hashlib
import re
import unicodedata
import uuid
from functools import wraps
from typing import Dict, List

from flask import Flask, Response, jsonify, render_template, request, session
from waitress import serve

from ai_agent import resolve_context_camera, run_mini_agent
from ai_assistant import ask_llm_with_failover, check_llm_providers
from alarm_manager import AlarmManager
from camera_manager import MultiCameraHub
from config import Config, BASE_DIR
from database_manager import DatabaseManager
from opcua_server import OpcUaBackgroundServer
from voice_engine import EmbeddedTtsEngine


cfg = Config.load()

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

app.secret_key = cfg.get("auth", {}).get("session_secret", "waterai-session-change-me-2026")

# Database logger nhẹ.
db_file = os.path.join(BASE_DIR, cfg.get("database", {}).get("sqlite_file", "database/history.db"))
db_cfg = cfg.get("database", {})
db = DatabaseManager(
    db_file,
    cfg.get("system", {}).get("log_interval_sec", 5),
    retention_days=int(db_cfg.get("retention_days", 7)),
    max_rows_per_camera=int(db_cfg.get("max_rows_per_camera", 20000)),
)

hub = MultiCameraHub(cfg, db=db)
hub.start()

alarm = AlarmManager(event_logger=db.log_event)
tts_engine = EmbeddedTtsEngine(BASE_DIR)

opc_server = None
if cfg.get("opcua", {}).get("enable", False):
    opc_server = OpcUaBackgroundServer(
        cfg["opcua"].get("endpoint", "opc.tcp://127.0.0.1:4846/freeopcua/server/"),
        cfg["opcua"].get("namespace", "http://automation.waterai.io"),
        cfg["opcua"].get("server_name", "WaterAI OPC UA Server"),
        hub,
    )
    opc_server.start()



def log_action(message: str, source: str = "WEB", severity: str = "INFO", event_type: str = "action", meta: str | None = None) -> None:
    try:
        user = session.get("username") if request else None
    except RuntimeError:
        user = None
    try:
        db.log_event(event_type, source, message, severity, user, meta)
    except Exception:
        pass

def default_camera_id() -> str:
    return cfg.get("system", {}).get("default_camera", "CAM01")


def normalize_camera_id(camera_id: str | None) -> str:
    if not camera_id:
        return default_camera_id()
    cid = str(camera_id).upper()
    if cid.isdigit():
        cid = f"CAM{int(cid):02d}"
    return cid


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d")


def extract_camera_id(text: str, fallback: str | None = None) -> str:
    raw = str(text or "")
    match = re.search(r"\bcam(?:era)?\s*0*(\d{1,2})\b", raw, re.IGNORECASE)
    if match:
        number = max(1, min(20, int(match.group(1))))
        return f"CAM{number:02d}"
    match = re.search(r"\bCAM\s*0*(\d{1,2})\b", raw, re.IGNORECASE)
    if match:
        number = max(1, min(20, int(match.group(1))))
        return f"CAM{number:02d}"
    return normalize_camera_id(fallback)


def status_code(info: Dict) -> int:
    if not info.get("online", False):
        return 4
    if info.get("status") == "LOW":
        return 1
    if info.get("status") == "NORMAL":
        return 2
    if info.get("status") == "HIGH":
        return 3
    return 0


def diagnose_camera_info(info: Dict) -> Dict:
    cid = info.get("id", "UNKNOWN")
    findings = []
    recommendations = []
    severity = "OK"

    if not info.get("enabled", False):
        findings.append("DISABLED")
        recommendations.append("Camera dang tat trong cau hinh; bat camera neu can giam sat.")
        severity = "INFO"
    elif not info.get("online", False):
        findings.append(info.get("error") or "OFFLINE")
        recommendations.append("Kiem tra nguon camera, URL RTSP/USB, mang, nguon cap va quyen truy cap.")
        severity = "CRITICAL"
    else:
        status = str(info.get("status", "NO_DATA")).upper()
        confidence = float(info.get("confidence", 0.0) or 0.0)
        quality = float(info.get("quality_score", 0.0) or 0.0)
        occlusion = float(info.get("occlusion_score", 0.0) or 0.0)
        reason = str(info.get("vision_reason", "") or "").upper()

        if status in {"LOW", "HIGH"}:
            findings.append(status)
            recommendations.append("Kiem tra bom/van va xac nhan lai nguong low/high threshold.")
            severity = "WARNING"
        if confidence < 30:
            findings.append("LOW_CONFIDENCE")
            recommendations.append("Khong nen dung gia tri nay cho dieu khien tu dong; xem snapshot/trend de xac nhan.")
            severity = "WARNING" if severity == "OK" else severity
        if quality < 45:
            findings.append("LOW_IMAGE_QUALITY")
            recommendations.append("Kiem tra anh sang, rung camera, focus va ve sinh mat kinh.")
            severity = "WARNING" if severity == "OK" else severity
        if occlusion > 45:
            findings.append("POSSIBLE_OCCLUSION")
            recommendations.append("Kiem tra vat can trong ROI, nguoi/vat di qua hoac vat noi che duong nuoc.")
            severity = "WARNING" if severity == "OK" else severity
        if "DIRTY" in reason or "BLUR" in reason:
            findings.append("DIRTY_OR_BLUR_LENS")
            recommendations.append("Lau kinh camera va kiem tra lai tieu cu.")
            severity = "WARNING" if severity == "OK" else severity

    if not findings:
        findings.append("NORMAL")
        recommendations.append("Camera on dinh; tiep tuc giam sat trend va alarm.")

    return {
        "camera_id": cid,
        "severity": severity,
        "findings": list(dict.fromkeys(findings)),
        "recommendations": list(dict.fromkeys(recommendations)),
        "signals": {
            "enabled": bool(info.get("enabled", False)),
            "online": bool(info.get("online", False)),
            "status": info.get("status", "NO_DATA"),
            "level": info.get("level", 0.0),
            "confidence": info.get("confidence", 0.0),
            "quality_score": info.get("quality_score", 0.0),
            "occlusion_score": info.get("occlusion_score", 0.0),
            "vision_reason": info.get("vision_reason", "NO_DATA"),
            "fps": info.get("fps", 0.0),
        },
    }


def build_unified_snapshot(camera_id: str | None = None) -> Dict:
    """Single payload for Camera AI, chatbot, and SCADA/WinCC Unified clients."""
    selected_id = normalize_camera_id(camera_id or default_camera_id())
    items = hub.summary()
    for item in items:
        alarm.update_camera(item)

    enabled = [c for c in items if c.get("enabled")]
    online = [c for c in enabled if c.get("online")]
    active_alarms = alarm.get_active()
    selected = hub.camera_info(selected_id)
    alarm.update_camera(selected)

    tags = []
    for info in items:
        cid = info.get("id", "")
        code = status_code(info)
        tags.append({
            "camera_id": cid,
            "base_path": f"WaterAI/{cid}",
            "rest": f"/tag/{cid}",
            "WaterLevel": float(info.get("level", 0.0) or 0.0),
            "StatusCode": code,
            "StatusText": info.get("status", "NO_DATA"),
            "CameraOnline": bool(info.get("online", False)),
            "FPS": float(info.get("fps", 0.0) or 0.0),
            "Confidence": float(info.get("confidence", 0.0) or 0.0),
        })

    levels = [float(c.get("level", 0.0) or 0.0) for c in online]
    avg_level = sum(levels) / len(levels) if levels else 0.0
    opc_cfg = cfg.get("opcua", {})
    return {
        "system": {
            "name": cfg.get("system", {}).get("name", "WaterAI"),
            "version": cfg.get("system", {}).get("version", "V6"),
            "selected_camera": selected_id,
            "enabled_cameras": len(enabled),
            "online_cameras": len(online),
            "active_alarms": len(active_alarms),
            "average_level": round(avg_level, 2),
        },
        "camera_ai": {
            "selected": selected,
            "cameras": items,
        },
        "scada": {
            "opcua_enabled": bool(opc_cfg.get("enable", True)),
            "opcua_running": bool(opc_server and opc_server.running),
            "opcua_endpoint": opc_cfg.get("endpoint"),
            "opcua_namespace": opc_cfg.get("namespace", "http://automation.waterai.io"),
            "rest_tag_pattern": "/tag/<camera_id>",
            "tags": tags,
        },
        "alarms": {
            "active": active_alarms,
            "history": alarm.get_history(),
        },
    }


def chatbot_reply(message: str, camera_id: str | None = None) -> Dict:
    text = str(message or "").strip()
    lower = normalize_text(text)
    requested_camera = extract_camera_id(text, camera_id)
    data = build_unified_snapshot(requested_camera)
    system = data["system"]
    selected = data["camera_ai"]["selected"]
    diagnosis = diagnose_camera_info(selected)
    cameras = data["camera_ai"]["cameras"]
    scada = data["scada"]
    alarms = data["alarms"]["active"]
    enabled = [c for c in cameras if c.get("enabled")]
    online = [c for c in enabled if c.get("online")]
    offline = [c for c in enabled if not c.get("online")]
    low = [c for c in online if str(c.get("status", "")).upper() == "LOW"]
    high = [c for c in online if str(c.get("status", "")).upper() == "HIGH"]

    def camera_line(cam: Dict) -> str:
        state = "online" if cam.get("online") else "offline"
        level = float(cam.get("level", 0.0) or 0.0)
        return (
            f"{cam.get('id')} - {cam.get('name', 'Camera')}: {level:.1f}%, "
            f"{cam.get('status', 'NO_DATA')}, {state}, FPS {cam.get('fps', 0)}"
        )

    def advice_for(cam: Dict) -> str:
        if not cam.get("enabled"):
            return "Camera đang tắt trong cấu hình. Vào Cài đặt để bật camera và kiểm tra source."
        if not cam.get("online"):
            return "Camera offline. Kiểm tra nguồn RTSP/USB, mạng, quyền truy cập và process_fps."
        status = str(cam.get("status", "")).upper()
        if status == "LOW":
            return "Mực nước thấp. Nên kiểm tra bơm cấp, van vào và ngưỡng low_threshold."
        if status == "HIGH":
            return "Mực nước cao. Nên kiểm tra bơm xả, van ra và ngưỡng high_threshold."
        return "Trạng thái bình thường. Có thể xem trend/history nếu cần kiểm tra dao động."

    if not text:
        answer = "Bạn có thể hỏi: tổng quan hệ thống, CAM01 đang thế nào, camera nào lỗi, alarm hiện tại, OPC UA/SCADA tag, hoặc nên xử lý gì."
    elif any(k in lower for k in ["giup", "help", "hoi duoc gi", "huong dan", "lenh"]):
        answer = (
            "Mình hiểu các nhóm câu hỏi như:\n"
            "- 'Tổng quan hệ thống', 'bao nhiêu camera online?'\n"
            "- 'CAM03 mức nước bao nhiêu?', 'cam 5 có lỗi không?'\n"
            "- 'camera nào offline/cảnh báo?', 'alarm hiện tại'\n"
            "- 'OPC UA endpoint', 'tag SCADA của CAM01'\n"
            "- 'nên xử lý gì cho CAM02?'"
        )
    elif any(k in lower for k in ["xu ly", "khuyen nghi", "nen lam", "goi y", "fix", "sua loi"]):
        answer = camera_line(selected) + "\nKhuyến nghị: " + advice_for(selected)
    elif any(k in lower for k in ["ban cam", "mo cam", "vat can", "che roi", "nhieu", "nguoi di qua", "dong vat", "occlusion", "dirty", "blur"]):
        answer = (
            camera_line(selected)
            + "\nChẩn đoán AI: "
            + ", ".join(diagnosis["findings"])
            + "\nTín hiệu: quality="
            + str(diagnosis["signals"].get("quality_score"))
            + ", occlusion="
            + str(diagnosis["signals"].get("occlusion_score"))
            + ", reason="
            + str(diagnosis["signals"].get("vision_reason"))
            + "\nKhuyến nghị: "
            + " ".join(diagnosis["recommendations"][:3])
        )
    elif any(k in lower for k in ["camera nao", "cam nao", "danh sach loi", "offline", "mat ket noi", "loi"]):
        issues = offline + low + high
        if issues:
            answer = "Các camera cần chú ý:\n" + "\n".join(camera_line(c) for c in issues[:12])
        else:
            answer = "Hiện không thấy camera enabled nào offline hoặc LOW/HIGH."
    elif any(k in lower for k in ["alarm", "canh bao", "bao dong"]):
        if alarms:
            lines = [f"{a.get('source')}: {a.get('message')} ({a.get('severity')})" for a in alarms[:8]]
            answer = "Alarm đang active:\n" + "\n".join(lines)
        else:
            answer = "Hiện không có alarm active."
    elif any(k in lower for k in ["opc", "scada", "wincc", "tag", "plc"]):
        answer = (
            f"OPC UA: {'Running' if scada['opcua_running'] else 'Offline'}\n"
            f"Endpoint: {scada.get('opcua_endpoint') or '--'}\n"
            f"Namespace: {scada.get('opcua_namespace') or '--'}\n"
            f"Object path: WaterAI/{selected.get('id')}\n"
            "Tag mỗi camera: WaterLevel, StatusCode, StatusText, CameraOnline, FPS, Confidence.\n"
            f"REST tag: /tag/{system['selected_camera']}"
        )
    elif any(k in lower for k in ["tong", "summary", "he thong", "system", "online", "trang thai chung", "bao cao"]):
        answer = (
            f"Hệ thống {system['name']} {system['version']}: "
            f"{system['online_cameras']}/{system['enabled_cameras']} camera online, "
            f"{system['active_alarms']} alarm, mức trung bình {system['average_level']:.1f}%.\n"
            f"LOW: {len(low)}, HIGH: {len(high)}, offline: {len(offline)}."
        )
    elif any(k in lower for k in ["camera", "cam", "muc", "level", "nuoc", "be "]):
        answer = camera_line(selected) + "\n" + advice_for(selected)
    elif any(k in lower for k in ["nguong", "threshold", "low", "high", "cai dat"]):
        answer = (
            f"{selected.get('id')} đang đặt ngưỡng thấp {selected.get('low_threshold', '--')}%, "
            f"ngưỡng cao {selected.get('high_threshold', '--')}%, "
            f"process_fps {selected.get('process_fps', '--')}, "
            f"source: {selected.get('source', '--')}."
        )
    else:
        answer = (
            "Mình chưa chắc ý bạn. Bạn có thể hỏi cụ thể hơn, ví dụ: "
            "'CAM01 mức nước bao nhiêu?', 'camera nào offline?', 'OPC UA endpoint?', "
            "hoặc 'nên xử lý gì cho cam 2?'."
        )

    llm_result = ask_llm_with_failover(text, data, system["selected_camera"], cfg.get("chatbot", {}))
    llm_answer = llm_result.get("answer")
    return {
        "answer": polish_chat_answer(llm_answer or answer),
        "fallback_answer": answer,
        "llm_used": bool(llm_result.get("used")),
        "llm_provider": llm_result.get("provider"),
        "llm_model": llm_result.get("model"),
        "llm_trace": llm_result.get("trace", []),
        "diagnosis": diagnosis,
        "snapshot": data,
    }



def polish_chat_answer(answer: str) -> str:
    text = str(answer or "").replace("[rule fallback]", "").strip()
    if any(mark in text for mark in ("Ã", "Ä", "áº", "Æ")):
        try:
            text = text.encode("latin1").decode("utf-8")
        except UnicodeError:
            pass
    replacements = {
        "camera online": "camera online",
        "alarm": "cảnh báo",
        "muc trung binh": "mức trung bình",
        "offline": "offline",
        "quality": "chất lượng ảnh",
        "occlusion": "che khuất",
        "reason": "lý do",
        "Kiem tra": "Kiểm tra",
        "nguon": "nguồn",
        "mang": "mạng",
        "quyen truy cap": "quyền truy cập",
        "khong": "không",
        "dang": "đang",
        "thap": "thấp",
        "cao": "cao",
        "binh thuong": "bình thường",
        "tiep tuc giam sat": "tiếp tục giám sát",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

ROLE_RANK = {"viewer": 1, "operator": 2, "engineer": 3, "admin": 4}


def _password_hash(password: str) -> str:
    return hashlib.sha256(str(password).encode("utf-8")).hexdigest()


def _auth_cfg() -> Dict:
    current = Config.load()
    auth = current.setdefault("auth", {})
    auth.setdefault("session_secret", "waterai-session-change-me-2026")
    auth.setdefault("users", [])
    if not any(u.get("username") == "admin" for u in auth["users"]):
        auth["users"].append({
            "username": "admin",
            "password_hash": _password_hash("cam@2026"),
            "role": "admin",
            "enabled": True,
        })
        Config.save(current, merge=False)
    return auth


def _find_user(username: str):
    username = str(username or "").strip()
    for user in _auth_cfg().get("users", []):
        if user.get("username") == username and user.get("enabled", True):
            return user
    return None


def _current_user() -> Dict:
    username = session.get("username")
    user = _find_user(username) if username else None
    if not user:
        return {"username": "guest", "role": "viewer", "authenticated": False}
    return {"username": user.get("username"), "role": user.get("role", "viewer"), "authenticated": True}


def require_role(min_role: str):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = _current_user()
            if ROLE_RANK.get(user.get("role", "viewer"), 1) < ROLE_RANK.get(min_role, 1):
                return jsonify({"result": "error", "message": "Không đủ quyền", "need_role": min_role}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/camera/<camera_id>")
def camera_page(camera_id):
    return render_template("index.html")


@app.route("/video")
def video_default():
    return video_response(default_camera_id())


@app.route("/video/<camera_id>")
def video_camera(camera_id):
    cid = normalize_camera_id(camera_id)
    return video_response(cid)


def video_response(camera_id):
    return Response(
        hub.mjpeg(camera_id),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        direct_passthrough=True,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/camera/<camera_id>/snapshot")
def camera_snapshot(camera_id):
    """Ảnh JPEG tĩnh dùng cho popup ROI để tránh mở thêm luồng MJPEG dài hạn."""
    cid = normalize_camera_id(camera_id)
    return Response(hub.latest_jpg(cid), mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.route("/water")
def water_default():
    return jsonify(camera_water(default_camera_id()).json)


@app.route("/water/<camera_id>")
def water_camera(camera_id):
    return camera_water(camera_id)


@app.route("/api/camera/<camera_id>")
def camera_water(camera_id):
    cid = normalize_camera_id(camera_id)
    info = hub.camera_info(cid)
    alarm.update_camera(info)
    return jsonify(info)


@app.route("/api/cameras")
def cameras_api():
    items = hub.summary()
    for item in items:
        alarm.update_camera(item)
    return jsonify({"cameras": items, "alarms": alarm.get_active()})


@app.route("/api/grid")
def grid_api():
    return jsonify(hub.summary())


@app.route("/api/history")
def history_api():
    cid = request.args.get("camera_id")
    limit = request.args.get("limit", 300, type=int)
    return jsonify(db.history(normalize_camera_id(cid) if cid else None, limit))


@app.route("/api/events")
def events_api():
    limit = request.args.get("limit", 300, type=int)
    event_type = request.args.get("type")
    return jsonify(db.events(limit, event_type))


@app.route("/api/alarms")
def alarms_api():
    for item in hub.summary():
        alarm.update_camera(item)
    return jsonify({"active": alarm.get_active(), "history": alarm.get_history()})


@app.route("/api/system")
def system_api():
    enabled = [c for c in hub.summary() if c.get("enabled")]
    online = [c for c in enabled if c.get("online")]
    return jsonify({
        "name": cfg.get("system", {}).get("name", "WaterAI"),
        "version": cfg.get("system", {}).get("version", "V6"),
        "enabled_cameras": len(enabled),
        "online_cameras": len(online),
        "opcua": {
            "enable": cfg.get("opcua", {}).get("enable", True),
            "endpoint": cfg.get("opcua", {}).get("endpoint"),
            "running": bool(opc_server and opc_server.running),
            "error": getattr(opc_server, "error", "") if opc_server else "disabled",
        },
        "performance": hub.performance_info(),
    })


@app.route("/api/unified")
def unified_api():
    return jsonify(build_unified_snapshot(request.args.get("camera_id")))




def _chat_suggestion(label: str, message: str) -> Dict:
    return {"label": label, "message": message}


def _cam_level_text(info: Dict) -> str:
    try:
        level = float(info.get("level", 0.0) or 0.0)
    except (TypeError, ValueError):
        level = 0.0
    status = str(info.get("status", "NO_DATA") or "NO_DATA").upper()
    online = "online" if info.get("online") else "offline"
    confidence = float(info.get("confidence", 0.0) or 0.0)
    fps = float(info.get("fps", 0.0) or 0.0)
    return f"{info.get('id', 'CAM??')}: {level:.1f}%, {status}, {online}, FPS {fps:.1f}, tin cậy {confidence:.0f}%"


def _chat_camera_cards(cameras: List[Dict], limit: int = 6) -> str:
    if not cameras:
        return "Chưa có camera phù hợp."
    return "\n".join(f"- {_cam_level_text(cam)}" for cam in cameras[:limit])


def _chat_next_actions(selected_id: str, intent: str = "general") -> List[Dict]:
    base = [
        _chat_suggestion(f"Chẩn đoán {selected_id}", f"Chẩn đoán {selected_id}"),
        _chat_suggestion("Camera lỗi", "Camera nào đang lỗi?"),
        _chat_suggestion("Trend", f"Xem trend {selected_id}"),
        _chat_suggestion("OPC/SCADA", f"OPC UA tag của {selected_id}"),
    ]
    if intent == "alarm":
        return [_chat_suggestion("Cách xử lý", f"Khuyến nghị xử lý {selected_id}"), _chat_suggestion("Alarm active", "Liệt kê alarm active"), _chat_suggestion("History", "Xem lịch sử cảnh báo")] + base[:1]
    if intent == "trend":
        return [_chat_suggestion("Dữ liệu hiện tại", f"Mức nước {selected_id}"), _chat_suggestion("History", "Hướng dẫn xem history"), _chat_suggestion("So sánh camera", "So sánh các camera đang chạy")] + base[:1]
    if intent == "opc":
        return [_chat_suggestion("Tag REST", f"REST tag {selected_id}"), _chat_suggestion("Trạng thái OPC", "OPC UA đang chạy không?"), _chat_suggestion("Camera", f"Mức nước {selected_id}")]
    return base


def chatbot_reply_v2(message: str, camera_id: str | None = None) -> Dict:
    text = str(message or "").strip()
    lower = normalize_text(text)
    selected_id = extract_camera_id(text, camera_id)
    data = build_unified_snapshot(selected_id)
    system = data["system"]
    selected = data["camera_ai"]["selected"]
    cameras = data["camera_ai"]["cameras"]
    alarms = data["alarms"]["active"]
    scada = data["scada"]
    diagnosis = diagnose_camera_info(selected)

    enabled = [c for c in cameras if c.get("enabled")]
    online = [c for c in enabled if c.get("online")]
    offline = [c for c in enabled if not c.get("online")]
    abnormal = [c for c in enabled if (not c.get("online")) or str(c.get("status", "")).upper() in {"LOW", "HIGH"}]
    low = [c for c in enabled if str(c.get("status", "")).upper() == "LOW"]
    high = [c for c in enabled if str(c.get("status", "")).upper() == "HIGH"]

    def pack(answer: str, intent: str = "general", extra: Dict | None = None) -> Dict:
        payload = {
            "answer": polish_chat_answer(answer),
            "fallback_answer": answer,
            "intent": intent,
            "suggestions": _chat_next_actions(system["selected_camera"], intent),
            "llm_used": False,
            "llm_provider": None,
            "llm_model": "rule-v2",
            "llm_trace": [],
            "snapshot": data,
            "diagnosis": diagnosis,
        }
        if extra:
            payload.update(extra)
        return payload

    if not text or any(k in lower for k in ["help", "huong dan", "tro giup", "lenh"]):
        answer = (
            "Anh có thể hỏi trực tiếp theo kiểu vận hành:\n"
            f"- Mức nước {system['selected_camera']} bao nhiêu?\n"
            "- Camera nào đang lỗi?\n"
            "- Khuyến nghị xử lý alarm hiện tại\n"
            "- OPC UA tag của CAM01\n"
            "- Xem trend hoặc history của camera đang chọn"
        )
        return pack(answer, "help", {"suggestions": _chat_next_actions(system["selected_camera"], "general")})

    if any(k in lower for k in ["xin chao", "chao", "hello", "hi ", "hey"]):
        return pack(
            f"WaterAI đang sẵn sàng. Camera đang chọn là {system['selected_camera']}. "
            f"Hiện có {system['online_cameras']}/{system['enabled_cameras']} camera online và {system['active_alarms']} alarm active.",
            "general",
        )

    if any(k in lower for k in ["tong quan", "he thong", "dashboard", "summary", "toan he"]):
        answer = (
            f"Tổng quan hệ thống:\n"
            f"- Camera online: {system['online_cameras']}/{system['enabled_cameras']}\n"
            f"- Alarm active: {system['active_alarms']}\n"
            f"- Mức trung bình: {system['average_level']:.1f}%\n"
            f"- LOW: {len(low)}, HIGH: {len(high)}, OFFLINE: {len(offline)}\n"
            f"Camera đang chọn: {_cam_level_text(selected)}"
        )
        return pack(answer, "general")

    if any(k in lower for k in ["alarm", "canh bao", "loi", "active", "offline", "bat thuong"]):
        if abnormal:
            answer = "Các camera cần chú ý:\n" + _chat_camera_cards(abnormal, 10)
        else:
            answer = "Chưa thấy camera bất thường trong snapshot hiện tại. Tiếp tục theo dõi trend và alarm active."
        if alarms:
            alarm_lines = []
            for item in alarms[:8]:
                alarm_lines.append(f"- {item.get('camera_id', '')}: {item.get('type', '')} {item.get('message', '')}".strip())
            answer += "\n\nAlarm active:\n" + "\n".join(alarm_lines)
        return pack(answer, "alarm")

    if any(k in lower for k in ["xu ly", "khuyen nghi", "chan doan", "diagnose", "kiem tra", "nen lam"]):
        recs = diagnosis.get("recommendations", [])
        findings = ", ".join(diagnosis.get("findings", []))
        answer = (
            f"Chẩn đoán {system['selected_camera']}: {findings}\n"
            f"Tín hiệu: {_cam_level_text(selected)}\n"
            "Khuyến nghị thao tác:\n" + "\n".join(f"- {r}" for r in recs[:5])
        )
        return pack(answer, "alarm")

    if any(k in lower for k in ["opc", "scada", "wincc", "tag", "plc", "ua"]):
        cid = system["selected_camera"]
        tag = next((t for t in scada.get("tags", []) if t.get("camera_id") == cid), {})
        answer = (
            f"OPC UA/SCADA cho {cid}:\n"
            f"- OPC UA: {'Running' if scada.get('opcua_running') else 'Stopped'}\n"
            f"- Endpoint: {scada.get('opcua_endpoint')}\n"
            f"- Namespace: {scada.get('opcua_namespace')}\n"
            f"- Object path: WaterAI/{cid}\n"
            "- Tag chính: WaterLevel, StatusCode, StatusText, CameraOnline, FPS, Confidence\n"
            f"- REST: {tag.get('rest', f'/tag/{cid}')}"
        )
        return pack(answer, "opc")

    if any(k in lower for k in ["trend", "lich su", "history", "do thi", "scroll", "mau"]):
        answer = (
            f"Trend của {system['selected_camera']} đang lấy mẫu 1 giây/lần, trục Y cố định 0-100%.\n"
            "Cách xem lại dữ liệu: tắt Auto scroll hoặc kéo thanh History sang trái/phải. "
            "Bấm Live để quay về dữ liệu mới nhất.\n"
            f"Giá trị hiện tại: {_cam_level_text(selected)}"
        )
        return pack(answer, "trend")

    if any(k in lower for k in ["muc", "nuoc", "level", "camera", "cam"]):
        answer = (
            f"Camera đang chọn: {_cam_level_text(selected)}\n"
            f"Chẩn đoán nhanh: {', '.join(diagnosis.get('findings', []))}\n"
            f"Gợi ý: {diagnosis.get('recommendations', ['Tiếp tục giám sát trend.'])[0]}"
        )
        return pack(answer, "camera")

    if any(k in lower for k in ["so sanh", "tat ca", "camera nao", "danh sach"]):
        target = abnormal or online or enabled
        answer = "Danh sách camera ưu tiên xem:\n" + _chat_camera_cards(target, 12)
        return pack(answer, "general")

    answer = (
        "Mình chưa bắt đúng ý câu hỏi. Anh có thể hỏi theo các nhóm: mức nước, camera lỗi, alarm active, "
        f"trend/history, hoặc OPC UA. Snapshot hiện tại của {system['selected_camera']}: {_cam_level_text(selected)}"
    )
    return pack(answer, "general")


def chatbot_agent_reply(message: str, camera_id: str | None = None, client_context: Dict | None = None) -> Dict:
    text = str(message or "").strip()
    chat_context = session.get("chat_context", {}) if isinstance(session.get("chat_context", {}), dict) else {}
    if isinstance(client_context, dict):
        chat_context = {**client_context, **chat_context}
    resolved_camera_id = resolve_context_camera(text, camera_id, chat_context)
    fallback = chatbot_reply_v2(text, resolved_camera_id)
    chat_cfg = cfg.get("chatbot", {})
    if not chat_cfg.get("mini_agent_enable", True):
        fallback.update({"agent_mode": "disabled-rule-v2"})
        return fallback
    try:
        selected_id = extract_camera_id(text, resolved_camera_id)
        data = build_unified_snapshot(selected_id)
        diagnosis = diagnose_camera_info(data["camera_ai"]["selected"])
        agent = run_mini_agent(
            text,
            data,
            data["system"]["selected_camera"],
            diagnosis,
            chat_cfg,
            fallback_answer=fallback.get("answer", ""),
            fallback_intent=fallback.get("intent", "general"),
            context=chat_context,
        )
        if isinstance(agent.get("context_update"), dict):
            session["chat_context"] = agent["context_update"]
        fallback.update({
            "answer": polish_chat_answer(agent.get("answer") or fallback.get("answer", "")),
            "fallback_answer": fallback.get("answer", ""),
            "intent": agent.get("intent", fallback.get("intent", "general")),
            "suggestions": agent.get("suggestions", fallback.get("suggestions", [])),
            "llm_used": bool(agent.get("llm_used", False)),
            "llm_provider": agent.get("llm_provider"),
            "llm_model": agent.get("llm_model") or "agent-rules",
            "llm_trace": agent.get("llm_trace", []),
            "snapshot": data,
            "diagnosis": diagnosis,
            "agent_mode": agent.get("agent_mode", "mini-agent"),
            "agent_plan": agent.get("agent_plan", []),
            "agent_tools": agent.get("agent_tools", []),
            "agent_observations": agent.get("agent_observations", {}),
            "chat_context": session.get("chat_context", {}),
        })
    except Exception as exc:
        fallback.update({
            "agent_mode": "fallback-rule-v2",
            "agent_error": str(exc)[:180],
        })
    return fallback


def chat_task_title(message: str, camera_id: str | None = None) -> str:
    text = " ".join(str(message or "").strip().split())
    if text:
        return text[:54] + ("..." if len(text) > 54 else "")
    return f"Task vận hành {normalize_camera_id(camera_id)}"


def chat_welcome_payload(task_id: str, camera_id: str | None = None) -> Dict:
    cid = normalize_camera_id(camera_id)
    snapshot = build_unified_snapshot(cid)
    system = snapshot.get("system", {})
    selected = snapshot.get("camera_ai", {}).get("selected", {})
    diagnosis = diagnose_camera_info(selected)
    answer = (
        f"Xin chào, mình đã mở task mới cho {system.get('selected_camera', cid)}. "
        f"Hiện hệ thống có {system.get('online_cameras', 0)}/{system.get('enabled_cameras', 0)} camera online, "
        f"{system.get('active_alarms', 0)} cảnh báo active. "
        f"Camera đang chọn: {_cam_level_text(selected)}\n"
        "Anh có thể hỏi tiếp kiểu: 'cam này ổn không', 'vì sao nó lỗi', 'so sánh cam 1 với cam 2', "
        "'OPC tag của nó', hoặc 'cấu hình camera này'."
    )
    context = {
        "last_camera_id": system.get("selected_camera", cid),
        "last_intent": "welcome",
        "last_user_message": "",
        "last_answer": answer[:360],
    }
    suggestions = [
        _chat_suggestion("Tổng quan", "Tổng quan hệ thống"),
        _chat_suggestion("Chẩn đoán camera", f"Chẩn đoán {system.get('selected_camera', cid)}"),
        _chat_suggestion("Camera lỗi", "Camera nào đang lỗi?"),
        _chat_suggestion("Trend", f"Xem trend {system.get('selected_camera', cid)}"),
        _chat_suggestion("OPC/SCADA", f"OPC UA tag của {system.get('selected_camera', cid)}"),
    ]
    return {
        "task_id": task_id,
        "answer": polish_chat_answer(answer),
        "engine": "WaterAI agent",
        "agent_mode": "welcome-task",
        "intent": "welcome",
        "suggestions": suggestions,
        "snapshot": snapshot,
        "diagnosis": diagnosis,
        "chat_context": context,
    }


def ensure_chat_task(task_id: str | None, camera_id: str | None = None, first_message: str = "", client_context: Dict | None = None) -> str:
    tid = str(task_id or session.get("chat_task_id") or "").strip()
    if not tid:
        tid = uuid.uuid4().hex[:12]
        title = chat_task_title(first_message, camera_id)
        db.create_chat_task(tid, title, normalize_camera_id(camera_id), client_context or {})
    else:
        db.upsert_chat_task(tid, camera_id=normalize_camera_id(camera_id), context=client_context or session.get("chat_context", {}))
    session["chat_task_id"] = tid
    return tid


@app.route("/api/chatbot/tasks", methods=["GET", "POST"])
def chatbot_tasks_api():
    if request.method == "GET":
        limit = request.args.get("limit", 50, type=int)
        return jsonify({"tasks": db.chat_tasks(limit)})

    payload = request.json or {}
    camera_id = normalize_camera_id(payload.get("camera_id") or default_camera_id())
    task_id = uuid.uuid4().hex[:12]
    welcome = chat_welcome_payload(task_id, camera_id)
    title = payload.get("title") or f"Task {camera_id}"
    db.create_chat_task(task_id, title, camera_id, welcome["chat_context"])
    db.log_chat_message(task_id, "bot", welcome["answer"], welcome["engine"], {"intent": "welcome", "suggestions": welcome["suggestions"]})
    session["chat_task_id"] = task_id
    session["chat_context"] = welcome["chat_context"]
    return jsonify({"task": db.chat_task(task_id), "welcome": welcome})


@app.route("/api/chatbot/tasks/<task_id>", methods=["GET", "DELETE"])
def chatbot_task_detail_api(task_id):
    if request.method == "DELETE":
        db.delete_chat_task(task_id)
        if session.get("chat_task_id") == task_id:
            session.pop("chat_task_id", None)
            session.pop("chat_context", None)
        return jsonify({"ok": True})

    task = db.chat_task(task_id)
    if not task:
        return jsonify({"ok": False, "message": "Không tìm thấy task"}), 404
    session["chat_task_id"] = task_id
    if isinstance(task.get("context"), dict):
        session["chat_context"] = task["context"]
    return jsonify({"ok": True, "task": task})

@app.route("/api/chatbot", methods=["POST"])
def chatbot_api():
    payload = request.json or {}
    message = payload.get("message", "")
    camera_id = payload.get("camera_id")
    client_context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    task_id = ensure_chat_task(payload.get("task_id"), camera_id, message, client_context)
    db.log_chat_message(task_id, "user", str(message or ""), "operator", {"camera_id": normalize_camera_id(camera_id)})
    task_state = db.chat_task(task_id, limit=12) or {}
    recent_messages = [
        {"role": item.get("role"), "message": item.get("message")}
        for item in task_state.get("messages", [])[-8:]
    ]
    agent_context = {**client_context, "recent_messages": recent_messages}
    reply = chatbot_agent_reply(message, camera_id, agent_context)
    reply["task_id"] = task_id
    engine = reply.get("llm_model") if reply.get("llm_used") else ("WaterAI agent" if reply.get("agent_mode") in {"context-agent", "mini-agent"} else "WaterAI rule")
    meta = {
        "intent": reply.get("intent"),
        "agent_mode": reply.get("agent_mode"),
        "suggestions": reply.get("suggestions", []),
        "diagnosis": reply.get("diagnosis", {}),
    }
    db.upsert_chat_task(task_id, camera_id=normalize_camera_id(camera_id), context=reply.get("chat_context", {}))
    db.log_chat_message(task_id, "bot", reply.get("answer", ""), engine, meta)
    return jsonify(reply)


@app.route("/api/chatbot/status")
def chatbot_status_api():
    chat_cfg = cfg.get("chatbot", {})
    providers = chat_cfg.get("providers")
    if not isinstance(providers, list) or not providers:
        providers = [{
            "name": chat_cfg.get("provider_name", "ollama-local"),
            "type": "ollama",
            "model": chat_cfg.get("model", "llama3.1:8b"),
            "timeout_sec": chat_cfg.get("timeout_sec", 8),
        }]
    return jsonify({
        "mini_agent_enable": bool(chat_cfg.get("mini_agent_enable", True)),
        "agent_use_llm": bool(chat_cfg.get("agent_use_llm", True)),
        "llm_enable": bool(chat_cfg.get("llm_enable", False)),
        "fallback_to_rules": bool(chat_cfg.get("fallback_to_rules", True)),
        "providers": providers,
    })


@app.route("/api/chatbot/api-key", methods=["GET", "POST", "DELETE"])
def chatbot_api_key_api():
    """Admin-only key management; the secret is never returned to the browser."""
    global cfg
    if _current_user().get("role") != "admin":
        return jsonify({"ok": False, "message": "Chỉ admin mới được quản lý API key"}), 403

    key_file = os.path.join(BASE_DIR, "secrets", "external_llm_api_key.txt")
    if request.method == "GET":
        configured = bool(os.environ.get("OPENAI_API_KEY", "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()) or os.path.isfile(key_file)
        active = next((p for p in cfg.get("chatbot", {}).get("providers", []) if p.get("type") in {"openai", "anthropic"} and p.get("enabled", True)), None)
        return jsonify({"ok": True, "configured": configured, "provider": (active or {}).get("type", "")})

    if request.method == "DELETE":
        try:
            if os.path.isfile(key_file):
                os.remove(key_file)
            for provider in cfg.get("chatbot", {}).get("providers", []):
                if provider.get("type") in {"openai", "anthropic"}:
                    provider["enabled"] = False
            Config.save(cfg, merge=False)
        except OSError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 500
        return jsonify({"ok": True, "configured": bool(os.environ.get("OPENAI_API_KEY", "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip())})

    payload = request.get_json(silent=True) or {}
    api_key = str(payload.get("api_key", "")).strip()
    provider_type = str(payload.get("provider", "")).strip().lower()
    if not provider_type:
        # Older cached browser scripts did not submit a provider choice.
        provider_type = "anthropic" if api_key.startswith("sk-ant-api") else "openai"
    if provider_type not in {"openai", "anthropic"}:
        return jsonify({"ok": False, "message": "Provider không hợp lệ"}), 400
    if not api_key:
        return jsonify({"ok": False, "message": "Hãy nhập API key"}), 400
    if len(api_key) > 500:
        return jsonify({"ok": False, "message": "API key không hợp lệ"}), 400
    if provider_type == "anthropic" and api_key.startswith("sk-ant-oat"):
        return jsonify({"ok": False, "message": "Đây là credential đăng nhập Claude Code, không phải API key cho ứng dụng. Hãy tạo API key trong Anthropic Console."}), 400

    try:
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, "w", encoding="utf-8") as key_stream:
            key_stream.write(api_key)
        chatbot_cfg = cfg.setdefault("chatbot", {})
        chatbot_cfg["llm_enable"] = True
        providers = chatbot_cfg.setdefault("providers", [])
        external_provider = next((p for p in providers if p.get("type") == provider_type), None)
        if external_provider is None:
            external_provider = {
                "name": "anthropic-api" if provider_type == "anthropic" else "openai-api",
                "type": provider_type,
                "model": "claude-sonnet-5" if provider_type == "anthropic" else "gpt-4o-mini",
                "api_key_env": "ANTHROPIC_API_KEY" if provider_type == "anthropic" else "OPENAI_API_KEY",
                "timeout_sec": 30, "max_tokens": 260,
            }
            providers.insert(0, external_provider)
        for provider in providers:
            if provider.get("type") in {"openai", "anthropic"}:
                provider["enabled"] = provider is external_provider
        providers.remove(external_provider)
        providers.insert(0, external_provider)
        external_provider["enabled"] = True
        Config.save(cfg, merge=False)
    except OSError as exc:
        return jsonify({"ok": False, "message": f"Không lưu được API key: {exc}"}), 500
    label = "Anthropic Claude" if provider_type == "anthropic" else "OpenAI"
    return jsonify({"ok": True, "configured": True, "provider": provider_type, "message": f"Đã lưu API key và bật {label} cho chatbot"})


@app.route("/api/chatbot/check_models")
def chatbot_check_models_api():
    return jsonify(check_llm_providers(cfg.get("chatbot", {})))


@app.route("/api/tts/status")
def tts_status_api():
    return jsonify(tts_engine.status())


@app.route("/api/tts", methods=["POST"])
def tts_api():
    payload = request.json or {}
    try:
        audio, meta = tts_engine.synthesize(
            payload.get("text", ""),
            payload.get("language", "vi-VN"),
            payload.get("style", "female-natural"),
            float(payload.get("rate", 1.0) or 1.0),
            float(payload.get("pitch", 1.0) or 1.0),
        )
        headers = {
            "Cache-Control": "no-store",
            "X-WaterAI-TTS-Engine": meta.get("engine", "piper-local"),
            "X-WaterAI-TTS-Voice": meta.get("voice_id", ""),
        }
        return Response(audio, mimetype="audio/wav", headers=headers)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "status": tts_engine.status()}), 503


@app.route("/api/ai/diagnose/<camera_id>")
def ai_diagnose_camera(camera_id):
    cid = normalize_camera_id(camera_id)
    info = hub.camera_info(cid)
    diagnosis = diagnose_camera_info(info)
    return jsonify({"camera": info, "diagnosis": diagnosis})


@app.route("/api/ai/diagnose")
def ai_diagnose_all():
    rows = []
    for info in hub.summary():
        rows.append({"camera_id": info.get("id"), "diagnosis": diagnose_camera_info(info)})
    return jsonify({"items": rows})


@app.route("/api/camera/<camera_id>/config", methods=["GET", "POST"])
def camera_config_api(camera_id):
    cid = normalize_camera_id(camera_id)
    global cfg
    if request.method == "GET":
        cam = Config.get_camera(cfg, cid)
        if cam is None:
            return jsonify({"result": "error", "message": "Không tìm thấy camera"}), 404
        return jsonify(cam)

    user = _current_user()
    if ROLE_RANK.get(user.get("role", "viewer"), 1) < ROLE_RANK.get("engineer", 3):
        return jsonify({"result": "error", "message": "Không đủ quyền"}), 403
    payload = request.json or {}
    try:
        cam = Config.update_camera(cid, payload)
        log_action(f"Cập nhật cấu hình {cid}", cid, "INFO", "config")
        cfg = Config.load()
        hub.update_camera_config(cid, cam)
        return jsonify({"result": "ok", "camera": cam})
    except Exception as exc:
        return jsonify({"result": "error", "message": str(exc)}), 400


@app.route("/api/cameras/config", methods=["GET", "POST"])
def cameras_config_api():
    global cfg
    if request.method == "GET":
        current = Config.load()
        return jsonify({"cameras": current.get("cameras", [])})

    user = _current_user()
    if ROLE_RANK.get(user.get("role", "viewer"), 1) < ROLE_RANK.get("engineer", 3):
        return jsonify({"result": "error", "message": "Không đủ quyền"}), 403

    payload = request.json or {}
    updates = payload.get("updates", [])
    if not isinstance(updates, list) or not updates:
        return jsonify({"result": "error", "message": "Không có camera cần cập nhật"}), 400

    changed = []
    errors = []
    for item in updates:
        if not isinstance(item, dict):
            continue
        cid = normalize_camera_id(item.get("id") or item.get("camera_id"))
        camera_payload = {k: v for k, v in item.items() if k not in {"id", "camera_id"}}
        if not camera_payload:
            continue
        try:
            cam = Config.update_camera(cid, camera_payload)
            log_action(f"Cập nhật hàng loạt {cid}", cid, "INFO", "config")
            hub.update_camera_config(cid, cam)
            changed.append(cid)
        except Exception as exc:
            errors.append({"camera_id": cid, "message": str(exc)})

    cfg = Config.load()
    code = 207 if errors else 200
    return jsonify({"result": "ok" if not errors else "partial", "changed": changed, "errors": errors}), code


# Legacy config route cho các màn hình/JS cũ.
@app.route("/config")
def config_get():
    return jsonify(Config.load())


@app.route("/save_config", methods=["POST"])
def save_config_legacy():
    # Nếu payload có camera_id thì cập nhật camera đó; nếu không thì cập nhật CAM01.
    user = _current_user()
    if ROLE_RANK.get(user.get("role", "viewer"), 1) < ROLE_RANK.get("engineer", 3):
        return jsonify({"result": "error", "message": "Không đủ quyền"}), 403
    payload = request.json or {}
    cid = normalize_camera_id(payload.pop("camera_id", default_camera_id()))
    camera_payload = {}
    roi = {}
    for k in ["roi_x1", "roi_y1", "roi_x2", "roi_y2"]:
        if k in payload:
            roi[k.replace("roi_", "")] = int(payload[k])
    if roi:
        camera_payload["roi"] = roi
    for old, new in [("low_threshold", "low_threshold"), ("high_threshold", "high_threshold"), ("camera_source", "source")]:
        if old in payload:
            camera_payload[new] = payload[old]
    if not camera_payload:
        camera_payload = payload
    try:
        cam = Config.update_camera(cid, camera_payload)
        log_action(f"Lưu cấu hình legacy {cid}", cid, "INFO", "config")
        hub.update_camera_config(cid, cam)
        return jsonify({"result": "ok"})
    except Exception as exc:
        return jsonify({"result": "error", "message": str(exc)}), 400


@app.route("/camera/select", methods=["POST"])
def select_camera_legacy():
    user = _current_user()
    if ROLE_RANK.get(user.get("role", "viewer"), 1) < ROLE_RANK.get("engineer", 3):
        return jsonify({"result": "error", "message": "Không đủ quyền"}), 403
    payload = request.json or {}
    source = payload.get("source", "")
    cid = normalize_camera_id(payload.get("camera_id", default_camera_id()))
    cam = Config.update_camera(cid, {"source": source, "enabled": True})
    log_action(f"Chọn nguồn camera {cid}: {source}", cid, "INFO", "config")
    hub.update_camera_config(cid, cam)
    return jsonify({"result": "ok"})


@app.route("/tag")
def tag_default():
    return tag_camera(default_camera_id())


@app.route("/tag/<camera_id>")
def tag_camera(camera_id):
    cid = normalize_camera_id(camera_id)
    info = hub.camera_info(cid)
    return jsonify({
        "CAMERA_ID": cid,
        "WL_PV": info.get("level", 0.0),
        "WL_STATUS": "GOOD" if info.get("online") else "BAD",
        "WL_STATUS_TEXT": info.get("status", "NO_DATA"),
        "WL_STATUS_CODE": status_code(info),
        "CAMERA_ONLINE": bool(info.get("online")),
        "FPS": info.get("fps", 0.0),
        "CONFIDENCE": info.get("confidence", 0.0),
    })


@app.route("/api/auth/me")
def auth_me():
    return jsonify(_current_user())


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    payload = request.json or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    user = _find_user(username)
    if not user:
        return jsonify({"ok": False, "message": "Sai tài khoản hoặc mật khẩu"}), 401
    stored_hash = user.get("password_hash")
    ok = stored_hash == _password_hash(password)
    if not ok and user.get("password"):
        ok = user.get("password") == password
    if not ok:
        return jsonify({"ok": False, "message": "Sai tài khoản hoặc mật khẩu"}), 401
    session["username"] = username
    log_action(f"Đăng nhập: {username}", "AUTH", "INFO", "auth")
    return jsonify({"ok": True, "user": _current_user()})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    user = session.get("username", "guest")
    log_action(f"Đăng xuất: {user}", "AUTH", "INFO", "auth")
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/users", methods=["GET"])
@require_role("admin")
def users_list():
    users = []
    for u in _auth_cfg().get("users", []):
        users.append({"username": u.get("username"), "role": u.get("role", "viewer"), "enabled": u.get("enabled", True)})
    return jsonify({"users": users})


@app.route("/api/users", methods=["POST"])
@require_role("admin")
def users_upsert():
    payload = request.json or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    role = str(payload.get("role", "viewer"))
    if not username or not password:
        return jsonify({"ok": False, "message": "Thiếu username/password"}), 400
    if role not in ROLE_RANK:
        return jsonify({"ok": False, "message": "Role không hợp lệ"}), 400
    cfg_now = Config.load()
    auth = cfg_now.setdefault("auth", {})
    users = auth.setdefault("users", [])
    found = None
    for u in users:
        if u.get("username") == username:
            found = u
            break
    if found is None:
        found = {"username": username, "enabled": True}
        users.append(found)
    found["password_hash"] = _password_hash(password)
    found.pop("password", None)
    found["role"] = role
    found["enabled"] = True
    Config.save(cfg_now, merge=False)
    log_action(f"Cập nhật tài khoản {username} ({role})", "AUTH", "INFO", "user")
    return jsonify({"ok": True})


@app.route("/api/users/<username>", methods=["DELETE"])
@require_role("admin")
def users_delete(username):
    if username == "admin":
        return jsonify({"ok": False, "message": "Không được xóa admin mặc định"}), 400
    cfg_now = Config.load()
    auth = cfg_now.setdefault("auth", {})
    users = auth.setdefault("users", [])
    auth["users"] = [u for u in users if u.get("username") != username]
    Config.save(cfg_now, merge=False)
    log_action(f"Xóa tài khoản {username}", "AUTH", "WARNING", "user")
    return jsonify({"ok": True})


if __name__ == "__main__":
    web = cfg.get("web", {})
    host = web.get("host", "0.0.0.0")
    port = int(web.get("port", 5530))
    threads = int(web.get("threads", 80))
    if port in {102, 4840, 4890, 5100}:
        raise SystemExit(f"Từ chối dùng cổng PLC/WinCC được bảo vệ cho web server: {port}")
    print("=" * 60)
    print("WaterAI V6 Multi Camera")
    print(f"Web     : http://127.0.0.1:{port}")
    print(f"OPC UA  : {cfg.get('opcua', {}).get('endpoint')}")
    print("Route   : /?cam=CAM01 hoặc /?cam=CAM02")
    print("=" * 60)
    serve(app, host=host, port=port, threads=threads)

