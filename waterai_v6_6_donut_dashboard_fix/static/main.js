const $ = (id) => document.getElementById(id);
const qs = (sel) => document.querySelector(sel);
const qsa = (sel) => Array.from(document.querySelectorAll(sel));

let cameras = [];
let selectedCam = getInitialCamera();
let activeTab = "camera";
let gridMode = 4;
let gridRefreshTimer = null;
let streamRetryTimer = null;
let trendChart = null;
let tankCompareChart = null;
let levelDonutChart = null;
let statusDonutChart = null;
let deviceDonutChart = null;
let trendRows = [];
let trendPaused = false;
let trendWindowSec = 120;
let trendZoom = 1;
let trendHardLimit = 3600;
let trendScrollOffset = 0;
let trendSampleTimer = null;
let currentUser = {username:"guest", role:"viewer", authenticated:false};
let roiDrawing = false;
let roiDrag = null;
let lastSelectedInfo = null;
let chatContext = {};
let currentChatTaskId = localStorage.getItem("wateraiChatTaskId") || "";
let currentChatTaskTitle = localStorage.getItem("wateraiChatTaskTitle") || "Task mới";
let historyLastRows = [];
let historyLastEvents = [];
let voiceRecognition = null;
let voiceListening = false;
let voiceSpeakEnabled = localStorage.getItem("wateraiVoiceSpeak") !== "false";
let voiceList = [];
let selectedVoiceURI = localStorage.getItem("wateraiVoiceURI") || "";
let voiceLanguage = localStorage.getItem("wateraiVoiceLanguage") || "vi-VN";
let voiceStyle = localStorage.getItem("wateraiVoiceStyle") || "male-standard";
let voiceRate = Number(localStorage.getItem("wateraiVoiceRate") || 1.02);
let voicePitch = Number(localStorage.getItem("wateraiVoicePitch") || 1.05);
let lastSpokenAnswer = "";
let currentTtsAudio = null;

const voiceProfiles = {
    "male-deep": {label:"Nam trầm", gender:"male", rate:.96, pitch:.88, keys:["nam tram","nam trầm","deep","low","bass","david","mark","paul","minh","ichiro","keita"]},
    "male-standard": {label:"Nam chuẩn", gender:"male", rate:1.0, pitch:.98, keys:["nam chuan","nam chuẩn","male","man","guy","an","minh","david","mark","ichiro","keita"]},
    "female-natural": {label:"Nữ tự nhiên", gender:"female", rate:1.02, pitch:1.08, keys:["nu tu nhien","nữ tự nhiên","female","woman","natural","aria","jenny","sara","mai","linh","nanami","sunhi"]},
    "female-warm": {label:"Nữ ấm", gender:"female", rate:.98, pitch:1.02, keys:["nu am","nữ ấm","warm","soft","susan","helen","hoai","hoài","hanh","hạnh","huihui"]},
    "female-bright": {label:"Nữ sáng", gender:"female", rate:1.06, pitch:1.16, keys:["nu sang","nữ sáng","bright","clear","zira","aria","my","mỹ","lan","haruka","minh anh"]}
};
const voiceLanguageLabels = {"vi-VN":"Tiếng Việt","en-US":"English","zh-CN":"Tiếng Trung","ja-JP":"Tiếng Nhật","ko-KR":"Tiếng Hàn"};

const roleRank = {viewer:1, operator:2, engineer:3, admin:4};
const roleLabel = {viewer:"Giám sát", operator:"Vận hành", engineer:"Cài đặt", admin:"Administrator"};
const mainVideo = $("mainVideo");
const roiCanvas = $("roiCanvas");
const roiCtx = roiCanvas.getContext("2d");
const roiPreviewImg = $("roiPreviewImg");
const roiPreviewCanvas = $("roiPreviewCanvas");
const roiPreviewCtx = roiPreviewCanvas.getContext("2d");

function getInitialCamera(){
    const params = new URLSearchParams(window.location.search);
    return (params.get("cam") || localStorage.getItem("selectedCam") || "CAM01").toUpperCase();
}
function setMessage(text, type="info"){
    const el = $("systemMessage");
    el.innerHTML = text;
    el.className = "message-pill flash " + type;
    setTimeout(()=>el.classList.remove("flash"), 800);
}
function clockTick(){
    const now = new Date();
    $("clock").textContent = now.toLocaleDateString("vi-VN") + " " + now.toLocaleTimeString("vi-VN");
}
setInterval(clockTick, 1000); clockTick();

$("btnMenu").onclick = () => {
    qs(".app-shell").classList.toggle("sidebar-collapsed");
    localStorage.setItem("sidebarCollapsed", qs(".app-shell").classList.contains("sidebar-collapsed"));
};
if(localStorage.getItem("sidebarCollapsed") === "true") qs(".app-shell").classList.add("sidebar-collapsed");
$("btnTheme").onclick = () => {document.body.classList.toggle("light"); localStorage.setItem("themeLight", document.body.classList.contains("light"));};
if(localStorage.getItem("themeLight") === "true") document.body.classList.add("light");
qsa(".nav-item").forEach(btn=>btn.addEventListener("click", ()=>switchTab(btn.dataset.tab)));

function switchTab(tab){
    activeTab = tab;
    qsa(".nav-item").forEach(b=>b.classList.toggle("active", b.dataset.tab === tab));
    qsa(".tab-page").forEach(p=>p.classList.toggle("active", p.id === "tab-"+tab));
    if(tab === "camera") startMainVideo();
    else stopMainVideo();
    if(tab !== "grid") clearGrid();
    if(tab === "grid") renderGrid();
    if(tab === "history") loadHistory();
    if(tab === "settings"){ loadUsers(); renderSettingsOverview(); }
    if(tab === "dashboard"){ renderTrend(); renderTankCompare(); resizeDonuts(); }
    if(tab === "unified") loadUnified();
}

function setSelectedCamera(camId, pushUrl=false){
    selectedCam = String(camId).toUpperCase();
    localStorage.setItem("selectedCam", selectedCam);
    if(activeTab === "camera") startMainVideo(true);
    $("footerSelected").textContent = selectedCam;
    resetTrend(false);
    updateSelectedInfo();
    if(pushUrl){
        const url = new URL(window.location.href);
        url.searchParams.set("cam", selectedCam);
        history.replaceState(null, "", url.toString());
    }
}

function startMainVideo(force=false){
    if(!mainVideo) return;
    const src = `/video/${selectedCam}?t=${Date.now()}`;
    if(force || mainVideo.dataset.cam !== selectedCam || !mainVideo.src){
        mainVideo.dataset.cam = selectedCam;
        mainVideo.src = src;
    }
    startStreamWatchdog();
}

function stopMainVideo(){
    if(!mainVideo) return;
    stopStreamWatchdog();
    mainVideo.dataset.cam = "";
    mainVideo.removeAttribute("src");
    mainVideo.load();
}

function reloadMainVideo(){
    if(activeTab !== "camera" || document.hidden) return;
    if(!mainVideo || mainVideo.dataset.cam !== selectedCam) return;
    mainVideo.src = `/video/${selectedCam}?t=${Date.now()}`;
}

function startStreamWatchdog(){
    if(streamRetryTimer) return;
    streamRetryTimer = setInterval(()=>{
        if(activeTab !== "camera" || document.hidden || !mainVideo?.src) return;
        if(mainVideo.naturalWidth === 0) reloadMainVideo();
    }, 5000);
}

function stopStreamWatchdog(){
    if(streamRetryTimer){
        clearInterval(streamRetryTimer);
        streamRetryTimer = null;
    }
}

function initCharts(){
    trendChart = new Chart($("trendChart"), {
        type:"line",
        data:{labels:[], datasets:[{label:"Mức nước (%)", data:[], tension:.22, borderWidth:2, pointRadius:0, fill:false}]},
        options:{
            responsive:true, maintainAspectRatio:false, animation:false, normalized:true, resizeDelay:200,
            interaction:{mode:"nearest", intersect:false},
            plugins:{
                legend:{display:true, labels:{color:"#eaf3ff"}},
                tooltip:{enabled:true, callbacks:{
                    title:(items)=>items[0]?.label || "",
                    label:(item)=>`Mức: ${Number(item.parsed.y||0).toFixed(1)} %`
                }}
            },
            scales:{
                x:{ticks:{maxTicksLimit:10, color:"#d8e7ff"}, grid:{color:"rgba(255,255,255,.04)"}},
                y:{min:0, max:100, ticks:{color:"#d8e7ff"}, grid:{color:"rgba(255,255,255,.05)"}}
            },
            onClick:(evt, elements)=>showTrendCursor(elements)
        }
    });
    const donutOptions = {
        responsive:true,
        maintainAspectRatio:false,
        animation:false,
        cutout:"64%",
        plugins:{
            legend:{display:false},
            tooltip:{enabled:true}
        }
    };

    levelDonutChart = new Chart($("levelDonutChart"), {
        type:"doughnut",
        data:{labels:["LOW","NORMAL","HIGH","OFFLINE"], datasets:[{data:[0,0,0,0], backgroundColor:["#ffcc00","#00ffa6","#ff4d4d","#667085"], borderWidth:0}]},
        options:donutOptions
    });

    statusDonutChart = new Chart($("statusDonutChart"), {
        type:"doughnut",
        data:{labels:["NORMAL","ALARM","OFFLINE"], datasets:[{data:[0,0,0], backgroundColor:["#00ffa6","#ff4d4d","#667085"], borderWidth:0}]},
        options:donutOptions
    });

    deviceDonutChart = new Chart($("deviceDonutChart"), {
        type:"doughnut",
        data:{labels:["ONLINE","OFFLINE","DISABLED"], datasets:[{data:[0,0,0], backgroundColor:["#00ffa6","#ff4d4d","#4f5f78"], borderWidth:0}]},
        options:donutOptions
    });
}

function trendWindowPoints(){
    return Math.max(10, Math.round(Number($("trendWindow").value || 120) / Math.max(0.2, trendZoom)));
}
function trendWindowPoints(){
    return Math.max(10, Math.round(Number($("trendWindow").value || 120) / Math.max(0.2, trendZoom)));
}
function trendMaxOffset(){
    return Math.max(0, trendRows.length - trendWindowPoints());
}
function getVisibleTrendRows(){
    const maxOffset = trendMaxOffset();
    if($("trendAuto").checked) trendScrollOffset = maxOffset;
    trendScrollOffset = Math.max(0, Math.min(maxOffset, Number(trendScrollOffset || 0)));
    return trendRows.slice(trendScrollOffset, trendScrollOffset + trendWindowPoints());
}
function syncTrendScroll(){
    const slider = $("trendScroll");
    const label = $("trendScrollLabel");
    if(!slider || !label) return;
    const maxOffset = trendMaxOffset();
    slider.min = "0";
    slider.max = String(maxOffset);
    slider.step = "1";
    if($("trendAuto").checked) trendScrollOffset = maxOffset;
    trendScrollOffset = Math.max(0, Math.min(maxOffset, Number(trendScrollOffset || 0)));
    slider.value = String(trendScrollOffset);
    const visible = getVisibleTrendRows();
    if($("trendAuto").checked || trendScrollOffset === maxOffset){
        label.textContent = "Live";
    }else if(visible.length){
        label.textContent = `${visible[0].ts.toLocaleTimeString("vi-VN")} - ${visible[visible.length-1].ts.toLocaleTimeString("vi-VN")}`;
    }else{
        label.textContent = "--";
    }
}
function renderTrend(){
    if(!trendChart) return;
    trendWindowSec = Math.max(10, Math.min(7200, Number($("trendWindow").value)||120));
    $("trendYMin").value = 0;
    $("trendYMax").value = 100;
    trendChart.options.scales.y.min = 0;
    trendChart.options.scales.y.max = 100;
    trendChart.options.scales.x.ticks.maxTicksLimit = 10;
    trendChart.data.datasets[0].pointRadius = $("trendShowPoints").checked ? 2 : 0;
    const rows = getVisibleTrendRows();
    trendChart.data.labels = rows.map(r=>r.ts.toLocaleTimeString("vi-VN"));
    trendChart.data.datasets[0].data = rows.map(r=>r.level);
    trendChart.update("none");
    syncTrendScroll();
    $("trendCursor").textContent = rows.length ? `Hiển thị ${rows.length}/${trendRows.length} điểm | ${$("trendScrollLabel")?.textContent || "Live"} | Zoom ${trendZoom.toFixed(1)}x | Camera ${selectedCam}` : "Chưa có dữ liệu trend.";
}
function resetTrend(clear=true){
    if(clear) trendRows=[];
    trendScrollOffset = 0;
    trendZoom = 1;
    if(trendChart){ trendChart.data.labels=[]; trendChart.data.datasets[0].data=[]; trendChart.update("none"); }
    syncTrendScroll();
    $("trendCursor").textContent="Đã reset trend. Click trên biểu đồ để xem timestamp và giá trị.";
}
function pushTrend(level){
    if(!trendChart || trendPaused) return;
    const now = new Date();
    trendRows.push({ts:now, level:clamp(level||0), camera:selectedCam});
    while(trendRows.length > trendHardLimit) trendRows.shift();
    renderTrend();
}
function sampleTrend(){
    if(lastSelectedInfo) pushTrend(lastSelectedInfo.level || 0);
}
function trendPan(delta){
    $("trendAuto").checked = false;
    trendScrollOffset = Math.max(0, Math.min(trendMaxOffset(), trendScrollOffset + delta));
    renderTrend();
}
function trendGoLive(){
    $("trendAuto").checked = true;
    trendScrollOffset = trendMaxOffset();
    renderTrend();
}
function showTrendCursor(elements){
    if(!$("trendRule").checked || !elements.length) return;
    const rows = getVisibleTrendRows();
    const r = rows[elements[0].index];
    if(!r) return;
    $("trendCursor").textContent = `Rule: ${r.ts.toLocaleString("vi-VN")} | ${r.level.toFixed(1)} % | ${r.camera}`;
}
function exportTrendCsv(){
    const rows = [["timestamp","camera","level"]].concat(trendRows.map(r=>[r.ts.toISOString(), r.camera, r.level]));
    const csv = rows.map(r=>r.join(",")).join("\n");
    downloadBlob(csv, `trend_${selectedCam}_${Date.now()}.csv`, "text/csv;charset=utf-8");
}
function exportTrendPng(){
    const link=document.createElement("a");
    link.download=`trend_${selectedCam}_${Date.now()}.png`;
    link.href=trendChart.toBase64Image("image/png",1);
    link.click();
}
function downloadBlob(content, filename, type){
    const blob = new Blob([content], {type});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
}
function trendConfig(){ renderTrend(); }
$("btnTrendPause").onclick=()=>{trendPaused=!trendPaused;$("btnTrendPause").textContent=trendPaused?"Tiếp tục":"Tạm dừng";};
$("btnTrendReset").onclick=()=>resetTrend(true);
$("btnTrendApply").onclick=()=>trendConfig();
if($("trendScroll")) $("trendScroll").oninput=()=>{trendScrollOffset=Number($("trendScroll").value||0);$("trendAuto").checked=false;renderTrend();};
if($("btnTrendPrev")) $("btnTrendPrev").onclick=()=>trendPan(-trendWindowPoints());
if($("btnTrendNext")) $("btnTrendNext").onclick=()=>trendPan(trendWindowPoints());
if($("btnTrendLive")) $("btnTrendLive").onclick=()=>trendGoLive();
$("trendAuto").onchange=()=>renderTrend();
$("btnTrendZoomIn").onclick=()=>{trendZoom=Math.min(16,trendZoom*1.5);renderTrend();};
$("btnTrendZoomOut").onclick=()=>{trendZoom=Math.max(.25,trendZoom/1.5);renderTrend();};
$("btnTrendResetZoom").onclick=()=>{trendZoom=1;renderTrend();};
$("btnTrendExportPng").onclick=()=>exportTrendPng();
$("btnTrendExportCsv").onclick=()=>exportTrendCsv();
$("trendMode").onchange=()=>setMessage(`Trend mode: ${$("trendMode").value}`);

async function loadSystem(){
    try{
        const res = await fetch("/api/system", {cache:"no-store"});
        const data = await res.json();
        $("footerVersion").textContent = data.version || "--";
        $("footerOpc").textContent = data.opcua?.running ? "Running" : "Offline";
        $("opcMini").textContent = data.opcua?.running ? "Running" : "Offline";
        $("opcEndpoint").textContent = data.opcua?.endpoint || "--";
        if(data.performance?.gpu) window.wateraiGpu = data.performance.gpu;
    }catch(e){$("footerOpc").textContent="Offline";}
}
async function loadUnified(){
    try{
        const res = await fetch(`/api/unified?camera_id=${selectedCam}`, {cache:"no-store"});
        const data = await res.json();
        const system = data.system || {};
        const scada = data.scada || {};
        $("unifiedOnline").textContent = `${system.online_cameras || 0}/${system.enabled_cameras || 0}`;
        $("unifiedAlarm").textContent = system.active_alarms || 0;
        $("unifiedAvg").textContent = isFinite(Number(system.average_level)) ? Number(system.average_level).toFixed(1) + " %" : "-- %";
        $("unifiedOpc").textContent = scada.opcua_running ? "Running" : "Offline";
        $("unifiedEndpoint").textContent = scada.opcua_endpoint || "--";
        renderUnifiedTags(scada.tags || []);
    }catch(e){
        setMessage("Khong doc duoc /api/unified", "error");
    }
}
function renderUnifiedTags(tags){
    const box = $("unifiedTags");
    if(!box) return;
    box.innerHTML = "";
    tags.slice(0, 20).forEach(tag=>{
        const row = document.createElement("div");
        row.className = "unified-tag-row";
        row.innerHTML = `
            <div><strong>${escapeHtml(tag.camera_id)}</strong><span>${escapeHtml(tag.base_path || "")}</span></div>
            <div>${number(tag.WaterLevel)}%</div>
            <div class="status-${String(tag.StatusText || "").toLowerCase()}">${escapeHtml(tag.StatusText || "NO_DATA")}</div>
            <div>${tag.CameraOnline ? "ONLINE" : "OFFLINE"}</div>`;
        row.onclick = ()=>{setSelectedCamera(tag.camera_id, true); switchTab("camera");};
        box.appendChild(row);
    });
}
function cleanChatText(text){
    return String(text || "").replace(/\[rule fallback\]/ig, "").replace(/reason=/g, "lý do=").replace(/quality=/g, "chất lượng=").replace(/occlusion=/g, "che khuất=").trim();
}
function setVoiceStatus(text, active=false){
    const el = $("voiceStatus");
    if(el) el.textContent = text;
    if($("btnVoiceChat")) $("btnVoiceChat").classList.toggle("active", active);
}
function normalizeVoiceText(text){
    return String(text || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "d");
}
function getVoiceProfile(style=voiceStyle){
    return voiceProfiles[style] || voiceProfiles["female-natural"];
}
function voiceLangRoot(lang){
    return String(lang || "").toLowerCase().split("-")[0];
}
function isLikelyMaleVoice(voice){
    const name = ` ${normalizeVoiceText(voice?.name)} `;
    return [" male "," man "," guy "," david "," mark "," paul "," an "," minh "," ichiro "," keita "," yunjian "," yunxi "," yunyang "," guyneural "," davis "].some(k=>name.includes(k));
}
function voiceScore(voice, langCode=voiceLanguage, style=voiceStyle){
    const profile = getVoiceProfile(style);
    const name = normalizeVoiceText(voice.name || "");
    const lang = String(voice.lang || "").toLowerCase();
    const target = String(langCode || "vi-VN").toLowerCase();
    let score = 0;
    if(lang === target) score += 120;
    if(voiceLangRoot(lang) === voiceLangRoot(target)) score += 70;
    if(target.startsWith("vi") && (name.includes("vietnam") || name.includes("viet"))) score += 35;
    (profile.keys || []).forEach(key=>{
        const k = normalizeVoiceText(key);
        if(name.includes(k) || lang.includes(k)) score += 24;
    });
    if(profile.gender === "female"){
        if(isLikelyFemaleVoice(voice)) score += 55;
        if(isLikelyMaleVoice(voice)) score -= 95;
    }else if(profile.gender === "male"){
        if(isLikelyMaleVoice(voice)) score += 55;
        if(isLikelyFemaleVoice(voice)) score -= 70;
    }
    if(name.includes("natural") || name.includes("online") || name.includes("premium") || name.includes("neural")) score += 8;
    if(voice.localService) score += 6;
    if(voice.default) score += 4;
    return score;
    const femaleKeys = ["female","woman","girl","zira","jenny","aria","sara","susan","helen","mai","my","mỹ","hoai","hoài","linh","lan","hanh","hạnh","nguyet","nguyệt","duong","dương","ban mai","minh anh","huihui","haruka","nanami","sunhi"];
    const maleKeys = ["male"," man ","guy","david","mark","paul","microsoft an"," an ","nam","minh","ichiro","keita"];
    const presetKeys = {
        "vbee-nguyet-duong": ["vbee","nguyet","nguyệt","duong","dương"],
        "fpt-nu-bac": ["fpt","ban mai","banmai","nu bac","nữ bắc","northern"],
        "fpt-nu-nam": ["fpt","nu nam","nữ nam","southern"],
        "zalo-nu": ["zalo","kiki","zalo ai"],
        "google-vi-nu": ["google","wavenet","neural","studio","vi-vn"],
    };
    (presetKeys[style] || []).forEach(key=>{
        if(name.includes(key) || lang.includes(key)) score += 28;
    });
    if(style.includes("nu") || style.includes("nguyet") || style.includes("google") || style.includes("zalo")){
        if(femaleKeys.some(k=>name.includes(k))) score += 35;
        if(maleKeys.some(k=>name.includes(k))) score -= 25;
    }
    if(style.includes("nu") || style.includes("nguyet") || style.includes("google") || style.includes("zalo")){
        if(name.includes("microsoft an")) score -= 120;
        if(/\ban\b/.test(name) && !name.includes("minh anh")) score -= 55;
    }
    if(name.includes("natural") || name.includes("online") || name.includes("premium")) score += 8;
    if(voice.localService) score += 6;
    if(voice.default) score += 4;
    return score;
}
function bestVoiceForChoice(){
    if(!voiceList.length) return null;
    const profile = getVoiceProfile();
    const targetRoot = voiceLangRoot(voiceLanguage || "vi-VN");
    const profileCandidates = voiceList
        .filter(v=>voiceLangRoot(v.lang) === targetRoot)
        .sort((a,b)=>voiceScore(b) - voiceScore(a));
    if(!profileCandidates.length) return null;
    const genderBest = profileCandidates.find(v=>profile.gender === "female" ? isLikelyFemaleVoice(v) : isLikelyMaleVoice(v));
    const profileBest = genderBest || profileCandidates[0];
    if(profile.gender === "female" && isLikelyMaleVoice(profileBest) && !isLikelyFemaleVoice(profileBest)) return null;
    if(profile.gender === "male" && isLikelyFemaleVoice(profileBest) && !isLikelyMaleVoice(profileBest)) return null;
    return voiceScore(profileBest) > 0 ? profileBest : null;
    const candidates = voiceList.slice().sort((a,b)=>voiceScore(b) - voiceScore(a));
    let best = candidates[0];
    if(voiceStyle.includes("nu") || voiceStyle.includes("nguyet") || voiceStyle.includes("google") || voiceStyle.includes("zalo")){
        const femaleBest = candidates.find(v=>isLikelyFemaleVoice(v) && String(v.lang || "").toLowerCase().split("-")[0] === String(voiceLanguage || "vi-VN").toLowerCase().split("-")[0]);
        if(femaleBest) best = femaleBest;
        else return null;
    }
    return voiceScore(best) > 0 ? best : null;
}
function isLikelyFemaleVoice(voice){
    const name = ` ${String(voice?.name || "").toLowerCase()} `;
    if(name.includes("microsoft an") || (/\ban\b/.test(name) && !name.includes("minh anh"))) return false;
    return ["female","woman","girl","zira","jenny","aria","sara","susan","helen","mai","my","mỹ","hoai","hoài","linh","lan","hanh","hạnh","nguyet","nguyệt","duong","dương","ban mai","minh anh","huihui","haruka","nanami","sunhi"].some(k=>name.includes(k));
}
function isLikelyFemaleVoice(voice){
    const name = ` ${normalizeVoiceText(voice?.name)} `;
    if(name.includes(" microsoft an ") || (/\ban\b/.test(name) && !name.includes("minh anh"))) return false;
    return [" female "," woman "," girl "," zira "," jenny "," aria "," sara "," susan "," helen "," mai "," my "," hoai "," linh "," lan "," hanh "," nguyet "," duong "," ban mai "," minh anh "," huihui "," haruka "," nanami "," sunhi "," xiaoxiao "," xiaoyi "," jiwon "].some(k=>name.includes(k));
}
async function refreshVoiceChoice(){
    if(voiceLanguage !== "vi-VN"){
        voiceLanguage = "vi-VN";
        localStorage.setItem("wateraiVoiceLanguage", voiceLanguage);
        if($("voiceLanguage")) $("voiceLanguage").value = voiceLanguage;
    }
    const embeddedStatusProfile = getVoiceProfile();
    const profile = embeddedStatusProfile;
    const langLabel = voiceLanguageLabels[voiceLanguage] || voiceLanguage;
    try{
        const res = await fetch("/api/tts/status", {cache:"no-store"});
        const data = await res.json();
        const voice = (data.voices || []).find(v=>v.language === voiceLanguage && v.style === voiceStyle);
        if(!data.engine_available){
            setVoiceStatus("Voice: chưa nhúng tts/bin/piper.exe vào project");
        }else if(!voice){
            setVoiceStatus(`Voice: chưa khai báo ${langLabel}, ${profile.label}`);
        }else if(!voice.available){
            setVoiceStatus(`Voice: thiếu model ${langLabel}, ${profile.label} trong tts/voices`);
        }else{
            setVoiceStatus(`Voice: ${voice.label || `${langLabel}, ${profile.label}`} · nhúng trong project`);
        }
    }catch(e){
        setVoiceStatus("Voice: chưa đọc được /api/tts/status");
    }
    return;
    if(!("speechSynthesis" in window)) return;
    voiceList = window.speechSynthesis.getVoices() || [];
    if(!voiceProfiles[voiceStyle]){
        voiceStyle = "female-natural";
        localStorage.setItem("wateraiVoiceStyle", voiceStyle);
        if($("voiceStyle")) $("voiceStyle").value = voiceStyle;
    }
    const best = bestVoiceForChoice();
    selectedVoiceURI = best?.voiceURI || "";
    if(selectedVoiceURI) localStorage.setItem("wateraiVoiceURI", selectedVoiceURI);
    const chosen = voiceList.find(v=>v.voiceURI === selectedVoiceURI);
    const browserProfile = getVoiceProfile();
    const browserLangLabel = voiceLanguageLabels[voiceLanguage] || voiceLanguage;
    setVoiceStatus(chosen ? `Voice: ${chosen.name} (${chosen.lang}) · ${langLabel}, ${profile.label}` : `Voice: chưa thấy giọng ${langLabel} phù hợp với ${profile.label}`);
    return;
    if(chosen && !isLikelyFemaleVoice(chosen)){
        setVoiceStatus(`Voice: ${chosen.name} (${chosen.lang}) · fallback browser, chưa thấy preset nữ đã chọn`);
    }else{
        setVoiceStatus(chosen ? `Voice: ${chosen.name} (${chosen.lang}) · preset nữ` : `Voice: chưa thấy giọng ${voiceLanguage}`);
    }
}
function initVoiceChat(){
    if($("voiceLanguage")){
        $("voiceLanguage").value = voiceLanguage;
        $("voiceLanguage").onchange = ()=>{
            voiceLanguage = $("voiceLanguage").value;
            localStorage.setItem("wateraiVoiceLanguage", voiceLanguage);
            if(voiceRecognition) voiceRecognition.lang = voiceLanguage;
            refreshVoiceChoice();
        };
    }
    if($("voiceStyle")){
        $("voiceStyle").value = voiceStyle;
        $("voiceStyle").onchange = ()=>{
            voiceStyle = $("voiceStyle").value;
            localStorage.setItem("wateraiVoiceStyle", voiceStyle);
            refreshVoiceChoice();
        };
    }
    if($("voiceRate")){
        $("voiceRate").value = String(voiceRate);
        $("voiceRate").oninput = ()=>{voiceRate = Number($("voiceRate").value || 1.02); localStorage.setItem("wateraiVoiceRate", String(voiceRate));};
    }
    if($("voicePitch")){
        $("voicePitch").value = String(voicePitch);
        $("voicePitch").oninput = ()=>{voicePitch = Number($("voicePitch").value || 1.05); localStorage.setItem("wateraiVoicePitch", String(voicePitch));};
    }
    refreshVoiceChoice();
    if("speechSynthesis" in window && window.speechSynthesis.onvoiceschanged !== undefined){
        window.speechSynthesis.onvoiceschanged = refreshVoiceChoice;
    }
    if($("btnVoiceSpeak")){
        $("btnVoiceSpeak").classList.toggle("active", voiceSpeakEnabled);
        $("btnVoiceSpeak").title = voiceSpeakEnabled ? "Đang bật đọc phản hồi" : "Đang tắt đọc phản hồi";
    }
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if(!SpeechRecognition){
        setVoiceStatus("Voice: trình duyệt chưa hỗ trợ nhận giọng nói");
        if($("btnVoiceChat")) $("btnVoiceChat").disabled = true;
        return;
    }
    voiceRecognition = new SpeechRecognition();
    voiceRecognition.lang = voiceLanguage || "vi-VN";
    voiceRecognition.interimResults = true;
    voiceRecognition.continuous = false;
    let finalText = "";
    voiceRecognition.onstart = ()=>{
        voiceListening = true;
        finalText = "";
        setVoiceStatus("Voice: đang nghe...", true);
    };
    voiceRecognition.onresult = (event)=>{
        let interim = "";
        for(let i=event.resultIndex;i<event.results.length;i++){
            const part = event.results[i][0].transcript;
            if(event.results[i].isFinal) finalText += part;
            else interim += part;
        }
        const text = (finalText || interim || "").trim();
        if(text) $("chatInput").value = text;
    };
    voiceRecognition.onerror = (event)=>{
        voiceListening = false;
        setVoiceStatus(`Voice: lỗi ${event.error || "micro"}`);
    };
    voiceRecognition.onend = ()=>{
        voiceListening = false;
        setVoiceStatus("Voice: sẵn sàng");
        const text = String($("chatInput").value || finalText || "").trim();
        if(text) handleVoiceCommand(text);
    };
}
function toggleVoiceListen(){
    if(!voiceRecognition){
        setVoiceStatus("Voice: chưa hỗ trợ trên trình duyệt này");
        return;
    }
    if(voiceListening){
        voiceRecognition.stop();
        return;
    }
    try{
        window.speechSynthesis?.cancel();
        if(currentTtsAudio){ currentTtsAudio.pause(); currentTtsAudio = null; }
        voiceRecognition.start();
    }catch(e){
        setVoiceStatus("Voice: chưa mở được micro");
    }
}
function toggleVoiceSpeak(){
    voiceSpeakEnabled = !voiceSpeakEnabled;
    localStorage.setItem("wateraiVoiceSpeak", String(voiceSpeakEnabled));
    if($("btnVoiceSpeak")){
        $("btnVoiceSpeak").classList.toggle("active", voiceSpeakEnabled);
        $("btnVoiceSpeak").title = voiceSpeakEnabled ? "Đang bật đọc phản hồi" : "Đang tắt đọc phản hồi";
    }
    if(!voiceSpeakEnabled){
        window.speechSynthesis?.cancel();
        if(currentTtsAudio){ currentTtsAudio.pause(); currentTtsAudio = null; }
    }
    setVoiceStatus(voiceSpeakEnabled ? "Voice: sẽ đọc phản hồi" : "Voice: đã tắt đọc phản hồi");
}
function speakTextBrowserFallback(clean, reason=""){
    if(!("speechSynthesis" in window)) {
        setVoiceStatus(reason ? `Voice: ${reason}` : "Voice: trình duyệt không hỗ trợ đọc");
        return;
    }
    const utter = new SpeechSynthesisUtterance(clean);
    voiceList = window.speechSynthesis.getVoices() || [];
    const viVoices = voiceList.filter(v=>String(v.lang || "").toLowerCase().startsWith("vi"));
    const male = viVoices.find(v=>normalizeVoiceText(v.name).includes(" an ")) || viVoices.find(v=>isLikelyMaleVoice(v));
    const chosen = male || viVoices[0] || voiceList[0];
    if(chosen){
        utter.voice = chosen;
        utter.lang = chosen.lang || "vi-VN";
    }else{
        utter.lang = "vi-VN";
    }
    utter.rate = Math.max(0.7, Math.min(1.25, voiceRate || 1.0));
    utter.pitch = Math.max(0.7, Math.min(1.25, voicePitch || 1.0));
    utter.onstart = ()=>setVoiceStatus(`Voice: fallback ${chosen ? chosen.name : "browser"}`);
    utter.onend = ()=>setVoiceStatus("Voice: sẵn sàng");
    utter.onerror = ()=>setVoiceStatus("Voice: fallback browser lỗi");
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
}
async function speakText(text){
    if(!voiceSpeakEnabled) return;
    const clean = cleanChatText(text).replace(/\n/g, ". ").slice(0, 900);
    if(!clean) return;
    lastSpokenAnswer = clean;
    window.speechSynthesis?.cancel();
    if(currentTtsAudio){ currentTtsAudio.pause(); currentTtsAudio = null; }
    const embeddedProfile = getVoiceProfile();
    try{
        setVoiceStatus("Voice: đang tạo audio nhúng...");
        const res = await fetch("/api/tts", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({
                text: clean,
                language: voiceLanguage,
                style: voiceStyle,
                rate: voiceRate || embeddedProfile.rate || 1.0,
                pitch: voicePitch || embeddedProfile.pitch || 1.0
            })
        });
        if(!res.ok){
            let msg = "không tạo được audio nhúng";
            try{ const data = await res.json(); msg = data.message || msg; }catch(e){}
            throw new Error(msg);
        }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        currentTtsAudio = audio;
        audio.onplay = ()=>setVoiceStatus("Voice: đang nói bằng voice nhúng");
        audio.onended = ()=>{URL.revokeObjectURL(url); if(currentTtsAudio === audio) currentTtsAudio = null; setVoiceStatus("Voice: sẵn sàng");};
        audio.onerror = ()=>{URL.revokeObjectURL(url); if(currentTtsAudio === audio) currentTtsAudio = null; setVoiceStatus("Voice: lỗi phát audio nhúng");};
        await audio.play();
    }catch(e){
        setVoiceStatus(`Voice: ${e.message || "chưa có voice nhúng"}`);
    }
    return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(clean);
    const profile = getVoiceProfile();
    let chosen = voiceList.find(v=>v.voiceURI === selectedVoiceURI) || bestVoiceForChoice();
    if(chosen && profile.gender === "female" && isLikelyMaleVoice(chosen) && !isLikelyFemaleVoice(chosen)) chosen = null;
    if(chosen && profile.gender === "male" && isLikelyFemaleVoice(chosen) && !isLikelyMaleVoice(chosen)) chosen = null;
    if(!chosen){
        const langLabel = voiceLanguageLabels[voiceLanguage] || voiceLanguage;
        setVoiceStatus(`Voice: chưa thấy giọng ${langLabel} phù hợp với ${profile.label}`);
        return;
    }
    if(chosen){
        utter.voice = chosen;
        utter.lang = chosen.lang || "vi-VN";
    }else{
        utter.lang = voiceLanguage || "vi-VN";
    }
    utter.rate = Math.max(0.7, Math.min(1.35, voiceRate || profile.rate || 1.02));
    utter.pitch = Math.max(0.7, Math.min(1.4, voicePitch || profile.pitch || 1.05));
    utter.onstart = ()=>setVoiceStatus("Voice: đang nói");
    utter.onend = ()=>setVoiceStatus("Voice: sẵn sàng");
    utter.onerror = ()=>setVoiceStatus("Voice: không đọc được phản hồi");
    window.speechSynthesis.speak(utter);
}
function handleVoiceCommand(text){
    const normalized = text.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").trim();
    if(["im lang", "dung noi", "tat tieng", "stop"].some(cmd=>normalized.includes(cmd))){
        window.speechSynthesis?.cancel();
        if(currentTtsAudio){ currentTtsAudio.pause(); currentTtsAudio = null; }
        setVoiceStatus("Voice: đã dừng đọc");
        $("chatInput").value = "";
        return;
    }
    if(["noi lai", "doc lai", "lap lai"].some(cmd=>normalized.includes(cmd))){
        $("chatInput").value = "";
        speakText(lastSpokenAnswer || "Chưa có câu trả lời trước đó để đọc lại.");
        return;
    }
    sendChat(text, {fromVoice:true});
}
function appendChatMessage(who, text, engine="", suggestions=[]){
    const box = $("chatMessages");
    if(!box) return;
    const msg = document.createElement("div");
    msg.className = "chat-msg " + who;
    if(engine){
        const badge = document.createElement("span");
        badge.className = "chat-engine";
        badge.textContent = engine;
        msg.appendChild(badge);
    }
    const body = document.createElement("span");
    body.textContent = cleanChatText(text);
    msg.appendChild(body);
    if(who === "bot" && Array.isArray(suggestions) && suggestions.length){
        const chips = document.createElement("div");
        chips.className = "chat-suggestions";
        suggestions.slice(0, 5).forEach(item=>{
            const chip = document.createElement("button");
            chip.type = "button";
            chip.className = "chat-suggestion";
            chip.textContent = item.label || item.message || "Gợi ý";
            chip.onclick = ()=>sendChat(item.message || item.label || "");
            chips.appendChild(chip);
        });
        msg.appendChild(chips);
    }
    box.appendChild(msg);
    box.scrollTop = box.scrollHeight;
}
function clearChatMessages(){
    const box = $("chatMessages");
    if(box) box.innerHTML = "";
}
function setChatTask(task){
    if(!task) return;
    currentChatTaskId = task.id || currentChatTaskId;
    currentChatTaskTitle = task.title || currentChatTaskTitle || "Task vận hành";
    if(currentChatTaskId) localStorage.setItem("wateraiChatTaskId", currentChatTaskId);
    if(currentChatTaskTitle) localStorage.setItem("wateraiChatTaskTitle", currentChatTaskTitle);
    if($("chatTaskTitle")) $("chatTaskTitle").textContent = currentChatTaskTitle;
    if(task.context) chatContext = task.context;
}
function renderChatTaskMessages(task){
    clearChatMessages();
    (task.messages || []).forEach(row=>{
        appendChatMessage(row.role === "user" ? "user" : "bot", row.message || "", row.engine || "", row.meta?.suggestions || []);
    });
}
async function newChatTask(){
    try{
        const res = await fetch("/api/chatbot/tasks", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({camera_id:selectedCam, title:`Task ${selectedCam} ${new Date().toLocaleTimeString("vi-VN")}`})
        });
        const data = await res.json();
        setChatTask(data.task || {id:data.welcome?.task_id, title:`Task ${selectedCam}`});
        clearChatMessages();
        if(data.welcome){
            chatContext = data.welcome.chat_context || {};
            appendChatMessage("bot", data.welcome.answer, data.welcome.engine || "WaterAI agent", data.welcome.suggestions || []);
        }
        await loadChatTasks(false);
    }catch(e){
        appendChatMessage("bot", "Không tạo được task trò chuyện mới.", "WaterAI");
    }
}
async function loadChatTask(taskId){
    if(!taskId) return false;
    try{
        const res = await fetch(`/api/chatbot/tasks/${encodeURIComponent(taskId)}`, {cache:"no-store"});
        if(!res.ok) return false;
        const data = await res.json();
        if(!data.ok || !data.task) return false;
        setChatTask(data.task);
        renderChatTaskMessages(data.task);
        return true;
    }catch(e){
        return false;
    }
}
async function loadChatTasks(toggle=true){
    const box = $("chatTaskList");
    if(!box) return;
    try{
        const res = await fetch("/api/chatbot/tasks?limit=12", {cache:"no-store"});
        const data = await res.json();
        box.innerHTML = "";
        (data.tasks || []).forEach(task=>{
            const row = document.createElement("button");
            row.type = "button";
            row.className = "chat-task-item" + (task.id === currentChatTaskId ? " active" : "");
            row.innerHTML = `<strong>${escapeHtml(task.title || task.id)}</strong><span>${escapeHtml(task.updated_at || "")} · ${escapeHtml(task.camera_id || "")}</span>`;
            row.onclick = ()=>loadChatTask(task.id);
            box.appendChild(row);
        });
        if(!(data.tasks || []).length) box.innerHTML = `<div class="chat-task-empty">Chưa có task đã lưu.</div>`;
    }catch(e){
        box.innerHTML = `<div class="chat-task-empty">Không tải được task gần đây.</div>`;
    }
}
function openChatbotManage(){
    $("chatbotManageModal")?.classList.remove("hidden");
    loadChatTasks(false);
    if(isAdmin()) loadOpenAiKeyStatus();
}
function closeChatbotManage(){
    $("chatbotManageModal")?.classList.add("hidden");
}
async function loadOpenAiKeyStatus(){
    const status = $("openaiApiKeyStatus");
    if(!status) return;
    try{
        const res = await fetch("/api/chatbot/api-key", {cache:"no-store"});
        const data = await res.json();
        if(!res.ok) throw new Error(data.message || "Không đọc được trạng thái API key");
        if(data.provider && $("externalAiProvider")) $("externalAiProvider").value = data.provider;
        const providerLabel = data.provider === "anthropic" ? "Claude (Anthropic)" : "OpenAI";
        status.textContent = data.configured ? `Đã có API key trên máy chủ${data.provider ? ` cho ${providerLabel}` : ""}.` : "Chưa có API key. Dán key rồi bấm Lưu và bật.";
    }catch(e){ status.textContent = e.message || "Không đọc được trạng thái API key."; }
}
async function saveOpenAiKey(){
    const input = $("openaiApiKeyInput");
    const key = String(input?.value || "").trim();
    if(!key){ setMessage("Hãy dán API key trước", "error"); return; }
    try{
        const provider = $("externalAiProvider")?.value || "anthropic";
        const res = await fetch("/api/chatbot/api-key", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({api_key:key, provider})});
        const data = await res.json();
        if(!res.ok || !data.ok) throw new Error(data.message || "Không lưu được API key");
        input.value = "";
        $("openaiApiKeyStatus").textContent = data.message || "Đã lưu API key và bật provider cho chatbot.";
        setMessage(data.message || "Đã bật AI API cho chatbot");
    }catch(e){ setMessage(e.message || "Không lưu được API key", "error"); }
}
async function clearOpenAiKey(){
    try{
        const res = await fetch("/api/chatbot/api-key", {method:"DELETE"});
        const data = await res.json();
        if(!res.ok || !data.ok) throw new Error(data.message || "Không xóa được API key");
        $("openaiApiKeyStatus").textContent = data.configured ? "Đã xóa key lưu cục bộ; vẫn còn key trong biến môi trường." : "Đã xóa API key. Chatbot quay lại model nội bộ.";
        setMessage("Đã cập nhật API key");
    }catch(e){ setMessage(e.message || "Không xóa được API key", "error"); }
}
async function ensureChatTask(){
    if(currentChatTaskId && await loadChatTask(currentChatTaskId)) return;
    await newChatTask();
}
async function sendChat(message, options={}){
    const text = String(message || $("chatInput").value || "").trim();
    if(!text) return;
    $("chatInput").value = "";
    appendChatMessage("user", text);
    try{
        const res = await fetch("/api/chatbot", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({message:text, camera_id:selectedCam, context:chatContext, task_id:currentChatTaskId})
        });
        const data = await res.json();
        if(data.chat_context) chatContext = data.chat_context;
        if(data.task_id){
            currentChatTaskId = data.task_id;
            localStorage.setItem("wateraiChatTaskId", currentChatTaskId);
        }
        const engine = data.llm_used ? `${data.llm_provider || "LLM"} / ${data.llm_model || "model"}` : (["mini-agent","context-agent"].includes(data.agent_mode) ? "WaterAI agent" : "WaterAI rule");
        appendChatMessage("bot", data.answer || "Chưa có phản hồi phù hợp.", engine, data.suggestions || []);
        if(options.fromVoice || voiceSpeakEnabled) speakText(data.answer || "");
        if(text && currentChatTaskTitle === "Task mới"){
            currentChatTaskTitle = text.slice(0, 54);
            localStorage.setItem("wateraiChatTaskTitle", currentChatTaskTitle);
            if($("chatTaskTitle")) $("chatTaskTitle").textContent = currentChatTaskTitle;
        }
        if(data.snapshot) loadUnified();
    }catch(e){
        appendChatMessage("bot", "Không kết nối được chatbot local.", "WaterAI");
    }
}
function openModelCheck(){
    $("modelCheckModal").classList.remove("hidden");
    loadModelCheck();
}
function closeModelCheck(){
    $("modelCheckModal").classList.add("hidden");
}
async function loadModelCheck(){
    const list = $("modelCheckList");
    $("modelCheckSummary").textContent = "Dang kiem tra model...";
    list.innerHTML = `<div class="model-check-row">Dang goi /api/chatbot/check_models...</div>`;
    try{
        const res = await fetch("/api/chatbot/check_models", {cache:"no-store"});
        const data = await res.json();
        const providers = data.providers || [];
        const ok = providers.filter(p=>p.status==="ok").length;
        const error = providers.filter(p=>p.status==="error").length;
        const skipped = providers.filter(p=>p.status==="skipped").length;
        $("modelCheckSummary").textContent = `LLM ${data.llm_enable ? "bat" : "tat"} | OK ${ok} | Loi ${error} | Skip ${skipped} | Rule fallback ${data.fallback_to_rules ? "bat" : "tat"}`;
        list.innerHTML = "";
        providers.forEach(p=>{
            const row = document.createElement("div");
            row.className = `model-check-row model-${p.status || "unknown"}`;
            row.innerHTML = `
                <div>
                    <strong>${escapeHtml(p.provider || "--")}</strong>
                    <span>${escapeHtml(p.model || "--")} · ${escapeHtml(p.type || "ollama")}</span>
                </div>
                <div class="model-status">${escapeHtml(String(p.status || "--").toUpperCase())}</div>
                <div class="model-latency">${Number(p.latency_ms || 0)} ms</div>
                <div class="model-error">${escapeHtml(p.error || p.sample || "")}</div>`;
            list.appendChild(row);
        });
        if(!providers.length) list.innerHTML = `<div class="model-check-row">Chua cau hinh provider nao.</div>`;
    }catch(e){
        $("modelCheckSummary").textContent = "Khong kiem tra duoc model";
        list.innerHTML = `<div class="model-check-row model-error-row">${escapeHtml(e.message || String(e))}</div>`;
    }
}
async function loadCameras(){
    try{
        const res = await fetch("/api/cameras", {cache:"no-store"});
        const data = await res.json();
        cameras = data.cameras || [];
        renderCameraStrip(); renderTankList(); renderAlarmList(data.alarms || []); updateSummary(data.alarms || []); renderTankCompare();
        if($("tab-settings")?.classList.contains("active")) renderSettingsOverview();
    }catch(e){setMessage("Không đọc được /api/cameras", "error");}
}
async function updateSelectedInfo(){
    try{
        const res = await fetch(`/api/camera/${selectedCam}`, {cache:"no-store"});
        const info = await res.json();
        lastSelectedInfo = info;
        $("selectedCameraTitle").textContent = `${info.id} · ${info.name || "Camera"}`;
        $("selectedCameraArea").textContent = info.area || "--";
        $("camLevel").textContent = number(info.level) + " %";
        $("camStatus").textContent = info.status || "--";
        $("camFps").textContent = info.fps || "--";
        $("camResolution").textContent = info.width && info.height ? `${info.width}×${info.height}` : "--";
        $("footerSelected").textContent = selectedCam;
        $("liveLevel").textContent = number(info.level) + "%";
        $("liveStatus").textContent = info.online ? (info.status || "NO_DATA") : "OFFLINE";
        $("liveStatus").className = "status-" + statusClass(info);
        $("liveHud").classList.toggle("hidden", !!info.show_overlay);
        setDot("cameraDot", info.online); setDot("footerDot", info.online);
        $("cameraStateText").textContent = info.online ? "Online" : (info.error || "Offline");
        $("cameraOfflineOverlay").classList.toggle("hidden", !!info.online);
        drawLiveRoi(info);
    }catch(e){setDot("cameraDot", false);setDot("footerDot", false);$("cameraStateText").textContent="Offline";}
}
function renderCameraStrip(){
    const box=$("cameraStrip"); box.innerHTML="";
    cameras.forEach(cam=>{
        const div=document.createElement("div"); div.className="cam-chip"+(cam.id===selectedCam?" active":"");
        div.innerHTML=`<div class="cam-chip-title"><span>${cam.id}</span><span class="status-${statusClass(cam)}">${cam.online?cam.status:"OFF"}</span></div><div class="cam-chip-meta">${escapeHtml(cam.name||cam.id)}</div><div class="level-mini"><span style="width:${clamp(cam.level||0)}%"></span></div>`;
        div.onclick=()=>setSelectedCamera(cam.id,true); box.appendChild(div);
    });
}
function renderTankList(){
    const box=$("tankList"); if(!box)return; box.innerHTML="";
    cameras.forEach(cam=>{
        const row=document.createElement("div"); row.className="tank-item";
        row.innerHTML=`<div><div class="tank-name">${cam.id} · ${escapeHtml(cam.name||"")}</div><div class="muted">${escapeHtml(cam.area||"")}</div></div><div><div class="status-text status-${statusClass(cam)}">${cam.online?cam.status:"OFFLINE"}</div><div>${number(cam.level)} %</div></div>`;
        row.onclick=()=>{setSelectedCamera(cam.id,true);switchTab("camera");}; box.appendChild(row);
    });
}
function resizeDonuts(){
    [levelDonutChart, statusDonutChart, deviceDonutChart].forEach(ch=>{
        if(ch){
            ch.resize();
            ch.update("none");
        }
    });
}

function renderTankCompare(){
    if(!levelDonutChart || !statusDonutChart || !deviceDonutChart) return;

    const enabled = cameras.filter(c=>c.enabled);
    const disabled = cameras.filter(c=>!c.enabled);
    const online = enabled.filter(c=>c.online);
    const offline = enabled.filter(c=>!c.online);

    const levelLow = online.filter(c=>Number(c.level||0) < Number(c.low_threshold ?? 20)).length;
    const levelHigh = online.filter(c=>Number(c.level||0) > Number(c.high_threshold ?? 90)).length;
    const levelNormal = Math.max(0, online.length - levelLow - levelHigh);

    const normal = online.filter(c=>String(c.status||"").toUpperCase()==="NORMAL").length;
    const alarmCnt = online.filter(c=>["LOW","HIGH"].includes(String(c.status||"").toUpperCase())).length;

    levelDonutChart.data.datasets[0].data = [levelLow, levelNormal, levelHigh, offline.length];
    statusDonutChart.data.datasets[0].data = [normal, alarmCnt, offline.length];
    deviceDonutChart.data.datasets[0].data = [online.length, offline.length, disabled.length];

    levelDonutChart.update("none");
    statusDonutChart.update("none");
    deviceDonutChart.update("none");

    if($("levelDonutText")) $("levelDonutText").innerHTML = `
        <div class="donut-row"><span class="swatch warn"></span><span>LOW</span><b>${levelLow}</b></div>
        <div class="donut-row"><span class="swatch ok"></span><span>NORMAL</span><b>${levelNormal}</b></div>
        <div class="donut-row"><span class="swatch danger"></span><span>HIGH</span><b>${levelHigh}</b></div>
        <div class="donut-row"><span class="swatch muted-swatch"></span><span>OFFLINE</span><b>${offline.length}</b></div>`;

    if($("statusDonutText")) $("statusDonutText").innerHTML = `
        <div class="donut-row"><span class="swatch ok"></span><span>NORMAL</span><b>${normal}</b></div>
        <div class="donut-row"><span class="swatch danger"></span><span>ALARM</span><b>${alarmCnt}</b></div>
        <div class="donut-row"><span class="swatch muted-swatch"></span><span>OFFLINE</span><b>${offline.length}</b></div>`;

    if($("deviceDonutText")) $("deviceDonutText").innerHTML = `
        <div class="donut-row"><span class="swatch ok"></span><span>ONLINE</span><b>${online.length}</b></div>
        <div class="donut-row"><span class="swatch danger"></span><span>OFFLINE</span><b>${offline.length}</b></div>
        <div class="donut-row"><span class="swatch muted-swatch"></span><span>DISABLED</span><b>${disabled.length}</b></div>`;
}
function renderAlarmList(alarms){
    const box=$("alarmList"); if(!box)return; box.innerHTML="";
    if(!alarms.length){box.innerHTML=`<div class="alarm-row">Không có cảnh báo active</div>`;return;}
    alarms.forEach(a=>{const row=document.createElement("div");row.className="alarm-row";row.innerHTML=`<strong>${a.source}</strong> · ${escapeHtml(a.message)}<br><span class="muted">${a.time} · ${a.severity}</span>`;box.appendChild(row);});
}
function renderGrid(){
    clearGrid(false);
    const box=$("cameraGrid"); box.className=`camera-grid grid-${gridMode}`; box.innerHTML="";
    cameras.slice(0, gridMode === 20 ? 20 : gridMode).forEach(cam=>{
        const tile=document.createElement("div"); tile.className="grid-tile";
        tile.innerHTML=`<img class="grid-snapshot" data-cam="${cam.id}" src="/api/camera/${cam.id}/snapshot?grid=${Date.now()}" alt="${cam.id}"><div class="grid-title">${cam.id} · ${escapeHtml(cam.name||"")}</div><div class="grid-level">${number(cam.level)}%</div>`;
        tile.onclick=()=>{setSelectedCamera(cam.id,true);switchTab("camera");}; box.appendChild(tile);
    });
    startGridRefresh();
}
function refreshGridSnapshots(){
    if(activeTab !== "grid") return;
    qsa(".grid-snapshot").forEach(img=>{
        img.src = `/api/camera/${img.dataset.cam}/snapshot?grid=${Date.now()}`;
    });
}
function startGridRefresh(){
    if(gridRefreshTimer) clearInterval(gridRefreshTimer);
    gridRefreshTimer = setInterval(refreshGridSnapshots, 1200);
}
function clearGrid(clearHtml=true){
    if(gridRefreshTimer){
        clearInterval(gridRefreshTimer);
        gridRefreshTimer = null;
    }
    qsa(".grid-snapshot").forEach(img=>{
        img.removeAttribute("src");
    });
    if(clearHtml && $("cameraGrid")) $("cameraGrid").innerHTML = "";
}
qsa(".grid-btn").forEach(btn=>btn.onclick=()=>{qsa(".grid-btn").forEach(b=>b.classList.remove("active"));btn.classList.add("active");gridMode=Number(btn.dataset.grid);renderGrid();});
function updateSummary(alarms){
    const enabled=cameras.filter(c=>c.enabled), online=enabled.filter(c=>c.online), fault=enabled.filter(c=>!c.online || ["HIGH","LOW"].includes(c.status));
    const levels=online.map(c=>Number(c.level||0)); const avg=levels.length?levels.reduce((a,b)=>a+b,0)/levels.length:0;
    $("sumOnline").textContent=`${online.length}/${enabled.length}`; $("footerOnline").textContent=`${online.length}/${enabled.length}`;
    $("sumFault").textContent=fault.length; $("sumEnabled").textContent=enabled.length;
    $("sumAlarm").textContent=alarms.length; $("footerAlarm").textContent=alarms.length;
    $("sumAvgLevel").textContent=levels.length?number(avg)+" %":"-- %"; $("sumSelected").textContent=selectedCam;
}
async function loadHistory(){
    const limit = Math.max(50, Math.min(2000, Number($("historyLimit")?.value || 200)));
    const camMode = $("historyCamera")?.value || "selected";
    const camQuery = camMode === "all" ? "" : `camera_id=${encodeURIComponent(selectedCam)}&`;
    try{
        const res=await fetch(`/api/history?${camQuery}limit=${limit}`,{cache:"no-store"});
        const rows=await res.json();
        historyLastRows = rows || [];
        const body=$("historyBody");
        body.innerHTML="";
        if(!historyLastRows.length){
            body.innerHTML = `<tr><td colspan="5"><div class="history-empty">Chưa có dữ liệu mức nước trong vùng lịch sử này.</div></td></tr>`;
        }else{
            historyLastRows.slice().reverse().forEach(r=>{const tr=document.createElement("tr"); tr.innerHTML=`<td>${escapeHtml(r.ts||"")}</td><td>${escapeHtml(r.camera_id||"")}</td><td>${number(r.level)}%</td><td>${escapeHtml(r.status||"")}</td><td>${number(r.confidence)}</td>`; body.appendChild(tr);});
        }
    }catch(e){}
    await loadEventHistory(limit);
}
async function loadEventHistory(limit){
    const mode = $("historyMode")?.value || "active";
    const body=$("eventHistoryBody");
    if(!body) return;
    body.innerHTML="";
    try{
        let rows = [];
        if(mode === "active"){
            const res = await fetch("/api/alarms", {cache:"no-store"});
            const data = await res.json();
            rows = (data.active || []).map(a=>({ts:a.time,event_type:"alarm_active",source:a.source,message:a.message,severity:a.severity,username:""}));
        }else{
            const type = mode === "resolved" ? "alarm_clear" : "";
            const res=await fetch(`/api/events?limit=${limit}${type ? `&type=${type}` : ""}`,{cache:"no-store"});
            rows=await res.json();
            if(mode === "operations") rows = rows.filter(r=>!["alarm","alarm_clear"].includes(r.event_type));
        }
        historyLastEvents = rows || [];
        if(!historyLastEvents.length){
            body.innerHTML = `<tr><td colspan="6"><div class="history-empty">Không có sự kiện phù hợp với chế độ đang chọn.</div></td></tr>`;
            return;
        }
        historyLastEvents.slice().reverse().forEach(r=>{const tr=document.createElement("tr"); tr.innerHTML=`<td>${escapeHtml(r.ts||"")}</td><td>${escapeHtml(r.event_type||"")}</td><td>${escapeHtml(r.source||"")}</td><td>${escapeHtml(r.message||"")}</td><td>${escapeHtml(r.severity||"")}</td><td>${escapeHtml(r.username||"")}</td>`; body.appendChild(tr);});
    }catch(e){
        body.innerHTML = `<tr><td colspan="6"><div class="history-empty">Không tải được lịch sử sự kiện.</div></td></tr>`;
    }
}
function exportHistoryReport(){
    const rows = [["section","timestamp","camera/source","level/type","status/severity","message","user"]];
    historyLastRows.forEach(r=>rows.push(["water", r.ts, r.camera_id, r.level, r.status, "", ""]));
    historyLastEvents.forEach(r=>rows.push(["event", r.ts, r.source, r.event_type, r.severity, r.message, r.username || ""]));
    downloadBlob(rows.map(r=>r.map(v=>`"${String(v ?? "").replace(/"/g,'""')}"`).join(",")).join("\n"), `waterai_history_${Date.now()}.csv`, "text/csv;charset=utf-8");
}
function historyRollBack(){
    const input = $("historyLimit");
    if(input) input.value = Math.min(2000, Number(input.value || 200) + 200);
    loadHistory();
}


function cameraConfigPayloadFromRow(row){
    return {
        id: row.dataset.cam,
        name: row.querySelector(".cfg-name").value,
        area: row.querySelector(".cfg-area").value,
        source: row.querySelector(".cfg-source").value,
        enabled: row.querySelector(".cfg-enabled").checked,
        low_threshold: Number(row.querySelector(".cfg-low").value),
        high_threshold: Number(row.querySelector(".cfg-high").value),
        process_fps: Math.max(15, Number(row.querySelector(".cfg-fps").value || 24)),
        show_overlay: row.querySelector(".cfg-overlay").checked
    };
}
function renderSettingsOverview(){
    const body = $("cameraConfigBody");
    if(!body) return;
    body.innerHTML = "";
    cameras.forEach(cam=>{
        const row = document.createElement("tr");
        row.dataset.cam = cam.id;
        row.innerHTML = `
            <td><input class="cfg-check" type="checkbox"></td>
            <td><button class="small-btn cfg-open">${cam.id}</button></td>
            <td><input class="cfg-name" value="${escapeHtml(cam.name || "")}"></td>
            <td><input class="cfg-area" value="${escapeHtml(cam.area || "")}"></td>
            <td><input class="cfg-source" value="${escapeHtml(cam.source ?? "")}" placeholder="0 hoặc rtsp://..."></td>
            <td><input class="cfg-enabled" type="checkbox" ${cam.enabled ? "checked" : ""}></td>
            <td><input class="cfg-low" type="number" value="${cam.low_threshold ?? 20}"></td>
            <td><input class="cfg-high" type="number" value="${cam.high_threshold ?? 90}"></td>
            <td><input class="cfg-fps" type="number" min="15" max="60" value="${cam.process_fps ?? 24}"></td>
            <td><input class="cfg-overlay" type="checkbox" ${cam.show_overlay ? "checked" : ""}></td>
            <td><span class="status-text status-${statusClass(cam)}">${cam.online ? cam.status : "OFFLINE"}</span></td>
            <td><button class="small-btn cfg-save">Lưu</button></td>`;
        row.querySelector(".cfg-open").onclick=()=>{setSelectedCamera(cam.id,true); openSettings();};
        row.querySelector(".cfg-save").onclick=()=>saveCameraConfigRow(row);
        body.appendChild(row);
    });
}
async function saveCameraConfigRow(row){
    if(!canEdit()){openLogin("Cần quyền Cài đặt hoặc Admin.");return;}
    const payload = cameraConfigPayloadFromRow(row);
    try{
        const res = await fetch(`/api/camera/${payload.id}/config`, {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify(payload)
        });
        const data = await res.json();
        if(data.result !== "ok") throw new Error(data.message || "error");
        setMessage(`Đã lưu ${payload.id}`);
        await loadCameras();
        if(payload.id === selectedCam) setSelectedCamera(selectedCam,true);
    }catch(e){
        setMessage(`Lỗi lưu ${payload.id}: ${e.message}`, "error");
    }
}
function selectedConfigRows(){
    return qsa("#cameraConfigBody tr").filter(row=>row.querySelector(".cfg-check")?.checked);
}
function setConfigSelection(mode){
    qsa("#cameraConfigBody tr").forEach(row=>{
        const cam = cameras.find(c=>c.id===row.dataset.cam);
        const checked = mode === "all" || (mode === "enabled" && cam?.enabled);
        row.querySelector(".cfg-check").checked = checked;
    });
}
async function applyBulkCameraConfig(){
    if(!canEdit()){openLogin("Cần quyền Cài đặt hoặc Admin.");return;}
    const rows = selectedConfigRows();
    if(!rows.length){setMessage("Chọn ít nhất một camera để áp dụng hàng loạt", "error");return;}
    const patch = {};
    if($("bulkLow").value !== "") patch.low_threshold = Number($("bulkLow").value);
    if($("bulkHigh").value !== "") patch.high_threshold = Number($("bulkHigh").value);
    if($("bulkFps").value !== "") patch.process_fps = Math.max(15, Number($("bulkFps").value));
    if($("bulkOverlay").value !== "") patch.show_overlay = $("bulkOverlay").value === "true";
    if($("bulkEnabled").value !== "") patch.enabled = $("bulkEnabled").value === "true";
    if(!Object.keys(patch).length){setMessage("Nhập ít nhất một giá trị bulk cần đổi", "error");return;}

    const updates = rows.map(row=>({id:row.dataset.cam, ...patch}));
    try{
        const res = await fetch("/api/cameras/config", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({updates})
        });
        const data = await res.json();
        if(!["ok","partial"].includes(data.result)) throw new Error(data.message || "error");
        setMessage(`Đã áp dụng ${data.changed?.length || 0} camera${data.errors?.length ? ", có lỗi một phần" : ""}`, data.errors?.length ? "error" : "info");
        await loadCameras();
        setSelectedCamera(selectedCam,true);
    }catch(e){
        setMessage("Lỗi áp dụng hàng loạt: " + e.message, "error");
    }
}

$("btnGridMode").onclick=()=>switchTab("grid");
$("btnPin").onclick=()=>setMessage(`${selectedCam} đã được ghim cho tab này`);
$("btnFullscreen").onclick=()=>$("cameraStage").requestFullscreen && $("cameraStage").requestFullscreen();
$("btnOpenSettings").onclick=()=>openSettings(); $("btnOpenSettings2").onclick=()=>openSettings(); $("btnCloseSettings").onclick=()=>closeSettings();

async function openSettings(){
    if(!canEdit()){openLogin("Cần quyền Cài đặt hoặc Admin để chỉnh camera.");return;}
    const cam = cameras.find(c=>c.id===selectedCam) || await (await fetch(`/api/camera/${selectedCam}`)).json();
    $("modalCamTitle").textContent=`${cam.id} · ${cam.name || ""}`; $("setName").value=cam.name||""; $("setArea").value=cam.area||""; $("setSource").value=cam.source??""; $("setEnabled").value=String(!!cam.enabled); $("setLow").value=cam.low_threshold??20; $("setHigh").value=cam.high_threshold??90; $("setOverlay").value=String(!!cam.show_overlay); $("setProcessFps").value=cam.process_fps||24;
    const roi=cam.roi||{}; $("roiX1").value=roi.x1??""; $("roiY1").value=roi.y1??""; $("roiX2").value=roi.x2??""; $("roiY2").value=roi.y2??"";
    $("roiPreviewWrap").classList.add("hidden"); $("settingsModal").classList.remove("hidden");
}
function closeSettings(){roiDrawing=false;roiDrag=null;clearPreviewRoi();$("settingsModal").classList.add("hidden");}
$("btnStartRoi").onclick=()=>{
    if(!canEdit())return;
    $("roiPreviewWrap").classList.remove("hidden");
    clearPreviewRoi();
    roiDrawing=false;
    roiDrag=null;
    // Dùng snapshot tĩnh thay vì mở thêm luồng MJPEG để tránh đen màn hình/ăn client stream.
    roiPreviewImg.src=`/api/camera/${selectedCam}/snapshot?t=${Date.now()}`;
    setTimeout(()=>{resizePreviewCanvas(); drawCurrentPreviewRoi();},350);
    setMessage("Kéo ROI trực tiếp trong popup cài đặt");
};
$("btnSaveSettings").onclick=async()=>{
    if(!canEdit()){openLogin("Cần quyền Cài đặt hoặc Admin.");return;}
    const payload={name:$("setName").value,area:$("setArea").value,source:$("setSource").value,enabled:$("setEnabled").value==="true",low_threshold:Number($("setLow").value),high_threshold:Number($("setHigh").value),show_overlay:$("setOverlay").value==="true",process_fps:Math.max(15,Number($("setProcessFps").value||24)),roi:{x1:Number($("roiX1").value),y1:Number($("roiY1").value),x2:Number($("roiX2").value),y2:Number($("roiY2").value)}};
    try{const res=await fetch(`/api/camera/${selectedCam}/config`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}); const data=await res.json(); if(data.result!=="ok")throw new Error(data.message||"error"); closeSettings(); setMessage("Đã lưu cài đặt camera"); await loadCameras(); setSelectedCamera(selectedCam,true);}catch(e){setMessage("Lỗi lưu cấu hình: "+e.message,"error");}
};

function resizeRoiCanvas(){roiCanvas.width=mainVideo.clientWidth;roiCanvas.height=mainVideo.clientHeight; if(lastSelectedInfo) drawLiveRoi(lastSelectedInfo);}
mainVideo.onload=()=>resizeRoiCanvas();
mainVideo.onerror=()=>{
    if(activeTab === "camera" && !document.hidden){
        setTimeout(reloadMainVideo, 1200);
    }
};
window.addEventListener("resize",()=>{resizeRoiCanvas();resizePreviewCanvas();});
document.addEventListener("visibilitychange", ()=>{
    if(document.hidden){
        stopMainVideo();
        clearGrid();
    }else if(activeTab === "camera"){
        startMainVideo();
    }else if(activeTab === "grid"){
        renderGrid();
    }
});
window.addEventListener("beforeunload", ()=>{
    stopMainVideo();
    clearGrid();
});
function imageDrawRect(img, canvas){
    const cw=canvas.width, ch=canvas.height, iw=img.naturalWidth||cw, ih=img.naturalHeight||ch; const car=cw/ch, iar=iw/ih; let w,h,x,y;
    if(car>iar){h=ch;w=h*iar;x=(cw-w)/2;y=0;} else {w=cw;h=w/iar;x=0;y=(ch-h)/2;} return {x,y,w,h,iw,ih};
}
function drawLiveRoi(info){
    roiCtx.clearRect(0,0,roiCanvas.width,roiCanvas.height); if(!info || !info.show_overlay || !info.roi || !mainVideo.naturalWidth)return;
    const r=imageDrawRect(mainVideo,roiCanvas), roi=info.roi; const x=r.x+roi.x1/r.iw*r.w, y=r.y+roi.y1/r.ih*r.h, w=(roi.x2-roi.x1)/r.iw*r.w, h=(roi.y2-roi.y1)/r.ih*r.h;
    roiCtx.strokeStyle="#00ffa6"; roiCtx.lineWidth=2; roiCtx.fillStyle="rgba(0,255,166,.10)"; roiCtx.fillRect(x,y,w,h); roiCtx.strokeRect(x,y,w,h);
}
function resizePreviewCanvas(){
    const rect = roiPreviewImg.getBoundingClientRect();
    roiPreviewCanvas.width = Math.max(1, Math.round(rect.width));
    roiPreviewCanvas.height = Math.max(1, Math.round(rect.height));
}
roiPreviewImg.onload=()=>{resizePreviewCanvas(); drawCurrentPreviewRoi();};
roiPreviewCanvas.addEventListener("mousedown",e=>{
    if($("roiPreviewWrap").classList.contains("hidden")) return;
    resizePreviewCanvas();
    roiDrawing=true;
    const r=roiPreviewCanvas.getBoundingClientRect();
    roiDrag={sx:e.clientX-r.left,sy:e.clientY-r.top,ex:e.clientX-r.left,ey:e.clientY-r.top};
});
roiPreviewCanvas.addEventListener("mousemove",e=>{
    if(!roiDrawing||!roiDrag)return;
    const r=roiPreviewCanvas.getBoundingClientRect();
    roiDrag.ex=e.clientX-r.left;
    roiDrag.ey=e.clientY-r.top;
    drawPreviewRoi();
});
window.addEventListener("mouseup",()=>{
    if(!roiDrawing||!roiDrag)return;
    roiDrawing=false;
    const r=imageDrawRect(roiPreviewImg,roiPreviewCanvas);
    if(r.w<=1 || r.h<=1) return;
    const sx=Math.max(0,Math.min(r.w,Math.min(roiDrag.sx,roiDrag.ex)-r.x));
    const ex=Math.max(0,Math.min(r.w,Math.max(roiDrag.sx,roiDrag.ex)-r.x));
    const sy=Math.max(0,Math.min(r.h,Math.min(roiDrag.sy,roiDrag.ey)-r.y));
    const ey=Math.max(0,Math.min(r.h,Math.max(roiDrag.sy,roiDrag.ey)-r.y));
    if(Math.abs(ex-sx)<4 || Math.abs(ey-sy)<4){drawCurrentPreviewRoi(); return;}
    $("roiX1").value=Math.round(sx/r.w*r.iw);
    $("roiY1").value=Math.round(sy/r.h*r.ih);
    $("roiX2").value=Math.round(ex/r.w*r.iw);
    $("roiY2").value=Math.round(ey/r.h*r.ih);
    roiDrag=null;
    drawCurrentPreviewRoi();
    setMessage("ROI đã cập nhật, nhấn Lưu cài đặt");
});
function drawCurrentPreviewRoi(){
    clearPreviewRoi();
    if(!roiPreviewImg.naturalWidth || !roiPreviewCanvas.width) return;
    const r=imageDrawRect(roiPreviewImg,roiPreviewCanvas);
    const rx1=Number($("roiX1").value||0), ry1=Number($("roiY1").value||0), rx2=Number($("roiX2").value||0), ry2=Number($("roiY2").value||0);
    if(rx2<=rx1 || ry2<=ry1) return;
    const x=r.x+rx1/r.iw*r.w, y=r.y+ry1/r.ih*r.h, w=(rx2-rx1)/r.iw*r.w, h=(ry2-ry1)/r.ih*r.h;
    roiPreviewCtx.fillStyle="rgba(0,255,166,.10)";
    roiPreviewCtx.strokeStyle="#00ffa6";
    roiPreviewCtx.lineWidth=2;
    roiPreviewCtx.fillRect(x,y,w,h);
    roiPreviewCtx.strokeRect(x,y,w,h);
}
function drawPreviewRoi(){
    clearPreviewRoi();
    if(!roiDrag)return;
    const x=Math.min(roiDrag.sx,roiDrag.ex), y=Math.min(roiDrag.sy,roiDrag.ey), w=Math.abs(roiDrag.ex-roiDrag.sx), h=Math.abs(roiDrag.ey-roiDrag.sy);
    roiPreviewCtx.fillStyle="rgba(0,255,166,.12)";
    roiPreviewCtx.strokeStyle="#00ffa6";
    roiPreviewCtx.lineWidth=2;
    roiPreviewCtx.fillRect(x,y,w,h);
    roiPreviewCtx.strokeRect(x,y,w,h);
}
function clearPreviewRoi(){roiPreviewCtx.clearRect(0,0,roiPreviewCanvas.width,roiPreviewCanvas.height)}

function setDot(id,on){const el=$(id);if(!el)return;el.classList.toggle("online",!!on)}
function statusClass(cam){if(!cam.online)return "offline";return String(cam.status||"").toLowerCase()}
function number(v){const n=Number(v);return isFinite(n)?n.toFixed(1):"--"}
function clamp(v){return Math.max(0,Math.min(100,Number(v||0)))}
function escapeHtml(s){return String(s).replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[m]))}

async function loadMe(){try{const res=await fetch("/api/auth/me",{cache:"no-store"}); currentUser=await res.json(); renderAuth();}catch(e){currentUser={username:"guest",role:"viewer",authenticated:false};renderAuth();}}
function renderAuth(){
    $("currentUser").textContent=currentUser.username||"guest"; $("currentRole").textContent=roleLabel[currentUser.role]||"Giám sát";
    $("btnLoginTop").classList.toggle("hidden",!!currentUser.authenticated); $("btnLogoutTop").classList.toggle("hidden",!currentUser.authenticated);
    const edit=canEdit(), admin=isAdmin(); qsa(".require-engineer").forEach(el=>el.classList.toggle("disabled",!edit)); qsa(".require-admin").forEach(el=>el.classList.toggle("hidden",!admin));
}
function canEdit(){return (roleRank[currentUser.role]||1)>=3}
function isAdmin(){return currentUser.role==="admin"}
function openLogin(msg=""){ $("loginError").textContent=msg; $("loginModal").classList.remove("hidden"); setTimeout(()=>$("loginUser").focus(),50);}
function closeLogin(){ $("loginModal").classList.add("hidden"); }
$("btnLoginTop").onclick=()=>openLogin(); $("btnLoginCancel").onclick=()=>closeLogin();
$("btnLoginSubmit").onclick=async()=>{try{const res=await fetch("/api/auth/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:$("loginUser").value,password:$("loginPass").value})}); const data=await res.json(); if(!data.ok)throw new Error(data.message||"Sai tài khoản/mật khẩu"); closeLogin(); await loadMe(); setMessage("Đăng nhập thành công");}catch(e){$("loginError").textContent=e.message;}};
$("btnLogoutTop").onclick=async()=>{await fetch("/api/auth/logout",{method:"POST"}); await loadMe(); setMessage("Đã đăng xuất");};
async function loadUsers(){
    if(!isAdmin())return; try{const res=await fetch("/api/users",{cache:"no-store"}); const data=await res.json(); const box=$("userList"); box.innerHTML=""; (data.users||[]).forEach(u=>{const row=document.createElement("div"); row.className="user-row"; row.innerHTML=`<span><b>${escapeHtml(u.username)}</b><em>${roleLabel[u.role]||u.role}</em></span><button class="small-btn" data-user="${u.username}">Xóa</button>`; row.querySelector("button").onclick=()=>deleteUser(u.username); box.appendChild(row);});}catch(e){}
}
$("btnAddUser").onclick=async()=>{if(!isAdmin())return; const username=$("newUser").value.trim(), password=$("newPass").value, role=$("newRole").value; if(!username||!password){setMessage("Nhập đủ user/pass", "error");return;} try{const res=await fetch("/api/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username,password,role})}); const data=await res.json(); if(!data.ok)throw new Error(data.message||"error"); $("newPass").value=""; await loadUsers(); setMessage("Đã cập nhật tài khoản");}catch(e){setMessage("Lỗi tài khoản: "+e.message,"error");}};
async function deleteUser(username){if(!confirm("Xóa tài khoản "+username+"?"))return; const res=await fetch(`/api/users/${encodeURIComponent(username)}`,{method:"DELETE"}); const data=await res.json(); if(!data.ok)setMessage(data.message||"Không xóa được", "error"); await loadUsers();}

$("btnSendChat").onclick=()=>sendChat();
$("chatInput").addEventListener("keydown", e=>{if(e.key==="Enter")sendChat();});
qsa(".chat-quick").forEach(btn=>btn.onclick=()=>sendChat(btn.dataset.msg));
if($("btnChatbotSettings")) $("btnChatbotSettings").onclick=()=>openChatbotManage();
if($("btnSaveOpenAiKey")) $("btnSaveOpenAiKey").onclick=()=>saveOpenAiKey();
if($("btnClearOpenAiKey")) $("btnClearOpenAiKey").onclick=()=>clearOpenAiKey();
if($("btnCloseChatbotManage")) $("btnCloseChatbotManage").onclick=()=>closeChatbotManage();
if($("btnVoiceChat")) $("btnVoiceChat").onclick=()=>toggleVoiceListen();
if($("btnVoiceSpeak")) $("btnVoiceSpeak").onclick=()=>toggleVoiceSpeak();
if($("btnNewChatTask")) $("btnNewChatTask").onclick=()=>newChatTask();
if($("btnRefreshChatTasks")) $("btnRefreshChatTasks").onclick=()=>loadChatTasks(true);
$("btnCheckModels").onclick=()=>openModelCheck();
$("btnCloseModelCheck").onclick=()=>closeModelCheck();
$("btnRefreshModelCheck").onclick=()=>loadModelCheck();
$("btnSelectAllCams").onclick=()=>setConfigSelection("all");
$("btnSelectEnabledCams").onclick=()=>setConfigSelection("enabled");
$("btnClearCamSelection").onclick=()=>setConfigSelection("none");
$("btnApplyBulkCams").onclick=()=>applyBulkCameraConfig();
if($("btnHistoryRefresh")) $("btnHistoryRefresh").onclick=()=>loadHistory();
if($("btnHistoryMore")) $("btnHistoryMore").onclick=()=>historyRollBack();
if($("btnHistoryExport")) $("btnHistoryExport").onclick=()=>exportHistoryReport();
if($("historyMode")) $("historyMode").onchange=()=>loadHistory();
if($("historyCamera")) $("historyCamera").onchange=()=>loadHistory();

async function init(){
    initCharts(); renderTrend(); await loadMe(); await loadSystem(); await loadCameras(); await loadUnified(); resizeDonuts(); setSelectedCamera(selectedCam,true);
    initVoiceChat();
    if($("chatTaskTitle")) $("chatTaskTitle").textContent = currentChatTaskTitle;
    await ensureChatTask();
    setInterval(loadCameras,2000); setInterval(updateSelectedInfo,1000); setInterval(sampleTrend,1000); setInterval(loadSystem,6000); setInterval(loadUnified,5000); setMessage("WaterAI Multi Camera Ready");
}
init();
