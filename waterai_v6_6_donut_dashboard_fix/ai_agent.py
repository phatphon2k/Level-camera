"""Context-aware mini agent for the WaterAI chatbot.

The agent is intentionally read-only. It can inspect the unified snapshot,
diagnosis result, SCADA metadata and model config, but it never writes camera
settings or controls any device.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List

from ai_assistant import ask_llm_with_failover


INTENT_KEYWORDS = {
    "help": ["help", "huong dan", "tro giup", "lenh", "hoi duoc gi", "hoi gi"],
    "greeting": ["xin chao", "chao", "hello", "hi", "hey"],
    "smalltalk": ["ban la ai", "ke chuyen", "tam su", "ngoai le", "ngoai van hanh", "thoi tiet", "tin tuc", "giai thich chung", "noi vui", "cam on"],
    "voice": ["ban nghe toi khong", "nghe toi khong", "voice", "giong noi", "noi chuyen", "doc lai", "noi lai", "noi cham", "tro ly ao"],
    "models": ["model", "llm", "llama", "qwen", "gemma", "ollama", "ai nao", "mo hinh"],
    "scada": ["opc", "scada", "wincc", "tag", "plc", "ua", "unified"],
    "settings": ["cai dat", "cau hinh", "config", "thong so", "tham so", "nguong", "threshold", "fps", "source", "rtsp", "roi", "overlay"],
    "diagnosis": [
        "xu ly", "khuyen nghi", "chan doan", "diagnose", "kiem tra", "nen lam",
        "nhiem", "ban", "mo", "che", "vat can", "nguoi", "dong vat", "nhieu",
        "vi sao", "tai sao", "nguyen nhan", "co on", "on khong", "dang tin",
    ],
    "alarm": ["alarm", "canh bao", "loi", "offline", "bat thuong", "active", "nguy hiem"],
    "trend": ["trend", "lich su", "history", "do thi", "mau", "du lieu cu", "bien dong"],
    "compare": ["so sanh", "tat ca", "camera nao", "danh sach", "top", "cao nhat", "thap nhat", "hon"],
    "system": ["tong quan", "he thong", "dashboard", "summary", "toan he", "tong the"],
    "camera": ["muc", "nuoc", "level", "camera", "cam", "be", "bao nhieu"],
}


FOLLOWUP_WORDS = {
    "no", "nay", "do", "cam do", "camera do", "cam nay", "camera nay",
    "be do", "be nay", "cai do", "cai nay", "vua roi", "con no", "the no",
}


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return value.replace("đ", "d")


def camera_mentions(message: str) -> List[str]:
    raw = str(message or "")
    norm = normalize_text(raw)
    ids: List[str] = []
    patterns = [
        r"\bcam(?:era)?\s*0*(\d{1,2})\b",
        r"\bbe\s*0*(\d{1,2})\b",
        r"\bso\s*0*(\d{1,2})\b",
        r"\b#\s*0*(\d{1,2})\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, norm, re.IGNORECASE):
            number = max(1, min(20, int(match.group(1))))
            cid = f"CAM{number:02d}"
            if cid not in ids:
                ids.append(cid)
    return ids


def resolve_context_camera(message: str, requested_camera_id: str | None, context: Dict | None = None) -> str | None:
    mentions = camera_mentions(message)
    if mentions:
        return mentions[0]
    ctx = context or {}
    norm = normalize_text(message)
    if any(word in norm for word in FOLLOWUP_WORDS):
        return ctx.get("last_camera_id") or requested_camera_id
    return requested_camera_id or ctx.get("last_camera_id")


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _camera_by_id(snapshot: Dict, camera_id: str) -> Dict:
    for cam in snapshot.get("camera_ai", {}).get("cameras", []):
        if cam.get("id") == camera_id:
            return cam
    return snapshot.get("camera_ai", {}).get("selected", {})


def _camera_line(cam: Dict) -> str:
    status = str(cam.get("status") or "NO_DATA").upper()
    online = "online" if cam.get("online") else "offline"
    return (
        f"{cam.get('id', 'CAM??')}: {_num(cam.get('level')):.1f}%, "
        f"{status}, {online}, FPS {_num(cam.get('fps')):.1f}, "
        f"tin cậy {_num(cam.get('confidence')):.0f}%, "
        f"ảnh {_num(cam.get('quality_score'), 0):.0f}, che {_num(cam.get('occlusion_score'), 0):.0f}"
    )


def _suggestion(label: str, message: str) -> Dict:
    return {"label": label, "message": message}


def _friendly_intro(intent: str) -> str:
    if intent in {"diagnosis", "alarm"}:
        return "Mình kiểm tra nhanh cho anh/chị nhé."
    if intent == "system":
        return "Mình tóm tắt hệ thống hiện tại như sau."
    if intent == "settings":
        return "Mình đọc cấu hình hiện tại của camera này."
    if intent == "scada":
        return "Mình kiểm tra phần SCADA/OPC UA."
    return ""


def classify_intent(message: str, context: Dict | None = None, fallback_intent: str = "general") -> str:
    text = normalize_text(message)
    if not text:
        return "help"

    scores = []
    for intent, keys in INTENT_KEYWORDS.items():
        score = 0
        for key in keys:
            if key in text:
                score += 2 if " " in key else 1
        if score:
            scores.append((score, intent))

    if not scores:
        ctx_intent = (context or {}).get("last_intent")
        if any(word in text for word in FOLLOWUP_WORDS) and ctx_intent:
            return ctx_intent
        return fallback_intent or "general"

    priority = {
        "diagnosis": 9,
        "scada": 8,
        "models": 8,
        "settings": 7,
        "alarm": 7,
        "compare": 6,
        "trend": 5,
        "system": 4,
        "camera": 3,
        "help": 2,
        "greeting": 1,
        "voice": 8,
        "smalltalk": 2,
    }
    scores.sort(key=lambda item: (item[0], priority.get(item[1], 0)), reverse=True)
    return scores[0][1]


def _issue_cameras(snapshot: Dict) -> List[Dict]:
    rows = []
    for cam in snapshot.get("camera_ai", {}).get("cameras", []):
        if not cam.get("enabled"):
            continue
        status = str(cam.get("status") or "").upper()
        quality = _num(cam.get("quality_score"), 100.0)
        occlusion = _num(cam.get("occlusion_score"), 0.0)
        confidence = _num(cam.get("confidence"), 0.0)
        if (not cam.get("online")) or status in {"LOW", "HIGH"} or quality < 45 or occlusion > 45 or confidence < 30:
            rows.append(cam)
    rows.sort(key=lambda c: (
        0 if not c.get("online") else 1,
        0 if str(c.get("status") or "").upper() in {"LOW", "HIGH"} else 1,
        _num(c.get("quality_score"), 100.0),
        -_num(c.get("occlusion_score"), 0.0),
    ))
    return rows


def _agent_plan(intent: str, mentions: List[str]) -> List[Dict]:
    tools = ["read_unified_snapshot"]
    if mentions:
        tools.append("resolve_camera_mentions")
    if intent in {"alarm", "diagnosis", "system", "compare", "general"}:
        tools.append("scan_camera_issues")
    if intent in {"camera", "diagnosis", "settings", "trend", "general"}:
        tools.append("diagnose_selected_camera")
    if intent == "compare":
        tools.append("compare_cameras")
    if intent == "scada":
        tools.append("read_scada_tags")
    if intent == "models":
        tools.append("read_model_config")
    if intent in {"diagnosis", "alarm"}:
        tools.append("build_operator_actions")
    return [{"step": i + 1, "tool": tool, "status": "ok"} for i, tool in enumerate(tools)]


def _suggestions(selected_id: str, intent: str) -> List[Dict]:
    base = [
        _suggestion(f"Chẩn đoán {selected_id}", f"Chẩn đoán {selected_id}"),
        _suggestion("Camera lỗi", "Camera nào đang lỗi?"),
        _suggestion("Trend", f"Xem trend {selected_id}"),
        _suggestion("OPC/SCADA", f"OPC UA tag của {selected_id}"),
    ]
    if intent == "alarm":
        return [_suggestion("Cách xử lý", f"Khuyến nghị xử lý {selected_id}"), _suggestion("Alarm active", "Liệt kê alarm active"), _suggestion("Vì sao lỗi?", f"Vì sao {selected_id} lỗi?")] + base[:1]
    if intent == "diagnosis":
        return [_suggestion("Có đáng tin?", f"{selected_id} có đáng tin không?"), _suggestion("Nhiễu ảnh", f"{selected_id} có bị che hoặc bẩn không?"), _suggestion("Cách xử lý", f"Nên xử lý {selected_id} thế nào?")] + base[:1]
    if intent == "settings":
        return [_suggestion("Ngưỡng/FPS", f"Cấu hình {selected_id}"), _suggestion("Bulk camera", "Cách đổi nhiều camera cùng lúc?")] + base[:2]
    if intent == "models":
        return [_suggestion("Kiểm tra model", "Model AI nào dùng được?"), _suggestion("Fallback", "Chatbot tự chuyển model thế nào?")] + base[:2]
    return base


def _operator_health(cam: Dict, diagnosis: Dict) -> str:
    if not cam.get("enabled"):
        return "đang tắt trong cấu hình"
    if not cam.get("online"):
        return "mất kết nối hoặc chưa đọc được nguồn camera"
    status = str(cam.get("status") or "").upper()
    confidence = _num(cam.get("confidence"))
    quality = _num(cam.get("quality_score"), 100)
    occlusion = _num(cam.get("occlusion_score"))
    if status in {"LOW", "HIGH"}:
        return f"có cảnh báo mức {status}"
    if confidence < 30:
        return "độ tin cậy thấp, không nên dùng để điều khiển tự động"
    if quality < 45:
        return "chất lượng ảnh thấp, cần kiểm tra ánh sáng/focus/mặt kính"
    if occlusion > 45:
        return "có dấu hiệu bị che khuất hoặc vật cản trong ROI"
    if diagnosis.get("severity") in {"WARNING", "CRITICAL"}:
        return ", ".join(diagnosis.get("findings", []))
    return "ổn trong snapshot hiện tại"


def _compare_answer(snapshot: Dict, mentions: List[str], selected_id: str) -> str:
    cameras = snapshot.get("camera_ai", {}).get("cameras", [])
    targets = [_camera_by_id(snapshot, cid) for cid in mentions[:4]] if mentions else []
    targets = [cam for cam in targets if cam]
    if len(targets) < 2:
        enabled = [cam for cam in cameras if cam.get("enabled")]
        targets = sorted(enabled, key=lambda c: _num(c.get("level")), reverse=True)[:6]
    if not targets:
        return "Chưa có đủ camera enabled để so sánh."
    lines = "\n".join(f"- {_camera_line(cam)}" for cam in targets)
    highest = max(targets, key=lambda c: _num(c.get("level")))
    lowest = min(targets, key=lambda c: _num(c.get("level")))
    return (
        "So sánh nhanh:\n"
        f"{lines}\n"
        f"Cao nhất: {highest.get('id')} {_num(highest.get('level')):.1f}%. "
        f"Thấp nhất: {lowest.get('id')} {_num(lowest.get('level')):.1f}%."
    )


def _answer_without_llm(message: str, snapshot: Dict, selected_id: str, diagnosis: Dict, intent: str, chatbot_cfg: Dict, fallback_answer: str, context: Dict | None = None) -> str:
    system = snapshot.get("system", {})
    selected = snapshot.get("camera_ai", {}).get("selected", {})
    cameras = snapshot.get("camera_ai", {}).get("cameras", [])
    alarms = snapshot.get("alarms", {}).get("active", [])
    scada = snapshot.get("scada", {})
    mentions = camera_mentions(message)
    enabled = [c for c in cameras if c.get("enabled")]
    online = [c for c in enabled if c.get("online")]
    issues = _issue_cameras(snapshot)
    norm = normalize_text(message)

    if intent == "help":
        return (
            "Anh có thể hỏi tự nhiên, ví dụ: 'cam 1 ổn không', 'vì sao nó lỗi', "
            "'so sánh cam 1 với cam 3', 'camera nào đáng chú ý', 'OPC tag của bể 2', "
            "'ngưỡng và FPS cam này', hoặc hỏi tiếp kiểu 'nó thì sao?'."
        )
    if intent == "greeting":
        return (
            f"WaterAI agent sẵn sàng. {len(online)}/{len(enabled)} camera online, "
            f"{system.get('active_alarms', len(alarms))} cảnh báo active. Camera đang chọn: {_camera_line(selected)}."
        )
    if intent == "smalltalk":
        if chatbot_cfg.get("llm_enable"):
            return (
                "Được chứ. Mình có thể trò chuyện ngoài lề ngắn gọn, nhưng khi câu hỏi liên quan vận hành WaterAI "
                "thì mình sẽ ưu tiên số liệu camera, cảnh báo và an toàn hệ thống trước."
            )
        return (
            "Mình là WaterAI agent. Mình có thể trò chuyện ngoài lề ở mức cơ bản, còn thế mạnh chính là hỗ trợ "
            "camera mức nước, cảnh báo, trend và SCADA. Nếu muốn mình trả lời rộng hơn như trợ lý AI tổng quát, "
            "anh/chị bật LLM local trong cấu hình chatbot nhé."
        )
    if intent == "voice":
        return (
            "Mình đang nghe được nếu trình duyệt đã cấp quyền micro. Anh có thể nói tự nhiên như: "
            "'cam một ổn không', 'camera nào đang lỗi', 'đọc tổng quan', hoặc 'im lặng' để dừng đọc. "
            "Nếu muốn mình đọc phản hồi, bật nút loa cạnh ô nhập."
        )
    if intent == "system":
        return (
            f"{_friendly_intro('system')}\n"
            f"Tổng quan: {len(online)}/{len(enabled)} camera online, "
            f"{system.get('active_alarms', len(alarms))} cảnh báo active, "
            f"mức trung bình {_num(system.get('average_level')):.1f}%. "
            f"Cần chú ý {len(issues)} camera. Camera đang chọn: {_camera_line(selected)}."
        )
    if intent == "compare":
        return _compare_answer(snapshot, mentions, selected_id)
    if intent == "alarm":
        if not issues:
            return "Hiện agent chưa thấy camera bất thường. Vẫn nên theo dõi trend và alarm nếu môi trường đang có nhiễu."
        lines = "\n".join(f"- {_camera_line(cam)}" for cam in issues[:10])
        return "Camera cần chú ý theo ưu tiên:\n" + lines
    if intent == "diagnosis":
        findings = ", ".join(diagnosis.get("findings", []))
        recs = "\n".join(f"- {item}" for item in diagnosis.get("recommendations", [])[:5])
        health = _operator_health(selected, diagnosis)
        prefix = "Đánh giá nhanh" if ("on khong" in norm or "dang tin" in norm) else "Chẩn đoán"
        return f"{_friendly_intro('diagnosis')}\n{prefix} {selected_id}: {health}.\nTín hiệu: {_camera_line(selected)}\nFindings: {findings}\nKhuyến nghị:\n{recs}"
    if intent == "scada":
        tag = next((t for t in scada.get("tags", []) if t.get("camera_id") == selected_id), {})
        return (
            f"{_friendly_intro('scada')}\n"
            f"SCADA/OPC UA {selected_id}: {'Running' if scada.get('opcua_running') else 'Stopped'}.\n"
            f"Endpoint: {scada.get('opcua_endpoint')}\n"
            f"Namespace: {scada.get('opcua_namespace')}\n"
            f"Object path: WaterAI/{selected_id}\n"
            "Tag: WaterLevel, StatusCode, StatusText, CameraOnline, FPS, Confidence\n"
            f"REST: {tag.get('rest', f'/tag/{selected_id}')}"
        )
    if intent == "trend":
        return (
            f"Trend {selected_id}: hiện tại {_camera_line(selected)}. "
            "Nếu mức dao động bất thường, đối chiếu thêm confidence, quality_score, occlusion_score và history trước khi kết luận."
        )
    if intent == "settings":
        roi = selected.get("roi") or {}
        return (
            f"{_friendly_intro('settings')}\n"
            f"Cấu hình {selected_id}: nguồn={selected.get('source') or '--'}, "
            f"ngưỡng thấp={selected.get('low_threshold')}%, ngưỡng cao={selected.get('high_threshold')}%, "
            f"process_fps={selected.get('process_fps')}, stream_fps={selected.get('stream_fps')}, "
            f"overlay={'bật' if selected.get('show_overlay') else 'tắt'}, "
            f"ROI=({roi.get('x1')},{roi.get('y1')})-({roi.get('x2')},{roi.get('y2')})."
        )
    if intent == "models":
        providers = chatbot_cfg.get("providers") or []
        names = ", ".join(f"{p.get('name', p.get('model'))}:{p.get('model')}" for p in providers) or chatbot_cfg.get("model", "--")
        return (
            f"Chuỗi model chatbot: {names}. "
            f"Mini-agent {'bật' if chatbot_cfg.get('mini_agent_enable', True) else 'tắt'}, "
            f"LLM {'bật' if chatbot_cfg.get('llm_enable') else 'tắt'}, "
            f"fallback rule {'bật' if chatbot_cfg.get('fallback_to_rules', True) else 'tắt'}. "
            "Khi model đầu lỗi, agent thử provider kế tiếp; nếu endpoint down thì bỏ qua các provider cùng URL."
        )
    if intent == "camera":
        return (
            f"{_camera_line(selected)}\n"
            f"Đánh giá: {_operator_health(selected, diagnosis)}. "
            f"Gợi ý: {(diagnosis.get('recommendations') or ['Tiếp tục giám sát trend.'])[0]}"
        )
    return fallback_answer or (
        f"Mình hiểu câu này theo ngữ cảnh {selected_id}. Snapshot hiện tại: {_camera_line(selected)}. "
        "Anh có thể hỏi tiếp về nguyên nhân, cách xử lý, trend, cấu hình hoặc SCADA."
    )


def _llm_prompt(message: str, snapshot: Dict, selected_id: str, diagnosis: Dict, plan: List[Dict], draft: str, context: Dict | None) -> str:
    system = snapshot.get("system", {})
    selected = snapshot.get("camera_ai", {}).get("selected", {})
    issues = [_camera_line(cam) for cam in _issue_cameras(snapshot)[:8]]
    return (
        "Bạn là mini agent vận hành WaterAI, nói tiếng Việt thân thiện với người Việt. Xưng 'mình', gọi người dùng là 'anh/chị' khi phù hợp. "
        "Trả lời theo kiểu: kết luận nhanh trước, sau đó 2-4 gạch đầu dòng khuyến nghị. "
        "Với câu hỏi liên quan WaterAI/camera/SCADA, chỉ dùng dữ liệu được cung cấp và không bịa số liệu. "
        "Với câu hỏi ngoài lề không cần dữ liệu realtime, có thể trả lời kiến thức phổ thông ngắn gọn; nếu không chắc, nói rõ giới hạn. "
        "Hiểu câu hỏi theo ngữ cảnh hội thoại, trả lời tiếng Việt tự nhiên, ưu tiên an toàn. Không đề xuất ghi cấu hình hoặc điều khiển PLC.\n"
        f"Context memory: {context or {}}\n"
        f"Câu hỏi: {message}\n"
        f"Camera đang chọn: {selected_id}\n"
        f"Hệ thống: online={system.get('online_cameras')}/{system.get('enabled_cameras')}, alarm={system.get('active_alarms')}, avg={system.get('average_level')}\n"
        f"Selected: {_camera_line(selected)}\n"
        f"Diagnosis: {diagnosis}\n"
        f"Issue cameras: {issues}\n"
        f"Agent plan: {plan}\n"
        f"Draft answer: {draft}\n"
        "Câu trả lời cuối:"
    )


def build_context_update(message: str, selected_id: str, intent: str, answer: str) -> Dict:
    return {
        "last_camera_id": selected_id,
        "last_intent": intent,
        "last_user_message": str(message or "")[:240],
        "last_answer": str(answer or "").replace("\n", " ")[:360],
    }


def run_mini_agent(
    message: str,
    snapshot: Dict,
    selected_id: str,
    diagnosis: Dict,
    chatbot_cfg: Dict | None = None,
    fallback_answer: str = "",
    fallback_intent: str = "general",
    context: Dict | None = None,
) -> Dict:
    cfg = chatbot_cfg or {}
    mentions = camera_mentions(message)
    intent = classify_intent(message, context, fallback_intent)
    plan = _agent_plan(intent, mentions)
    draft = _answer_without_llm(message, snapshot, selected_id, diagnosis, intent, cfg, fallback_answer, context)

    llm = {"answer": None, "used": False, "provider": None, "model": None, "trace": []}
    if cfg.get("llm_enable", False) and cfg.get("agent_use_llm", True):
        prompt = _llm_prompt(message, snapshot, selected_id, diagnosis, plan, draft, context)
        llm = ask_llm_with_failover(prompt, snapshot, selected_id, cfg)

    answer = llm.get("answer") or draft
    return {
        "answer": answer,
        "intent": intent,
        "suggestions": _suggestions(selected_id, intent),
        "agent_mode": "context-agent",
        "agent_plan": plan,
        "agent_tools": [step["tool"] for step in plan],
        "agent_observations": {
            "selected_camera": selected_id,
            "camera_mentions": mentions,
            "issue_count": len(_issue_cameras(snapshot)),
            "context_used": bool(context),
            "llm_attempted": bool(cfg.get("llm_enable", False) and cfg.get("agent_use_llm", True)),
        },
        "context_update": build_context_update(message, selected_id, intent, answer),
        "llm_used": bool(llm.get("used")),
        "llm_provider": llm.get("provider"),
        "llm_model": llm.get("model") or ("agent-rules" if not llm.get("used") else None),
        "llm_trace": llm.get("trace", []),
    }
