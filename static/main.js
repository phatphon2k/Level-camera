const $ = (id) => document.getElementById(id);
const qs = (sel) => document.querySelector(sel);
const qsa = (sel) => Array.from(document.querySelectorAll(sel));

let cameras = [];
let selectedCam = getInitialCamera();
let gridMode = 4;
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
let currentUser = {username:"guest", role:"viewer", authenticated:false};
let roiDrawing = false;
let roiDrag = null;
let lastSelectedInfo = null;

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
    qsa(".nav-item").forEach(b=>b.classList.toggle("active", b.dataset.tab === tab));
    qsa(".tab-page").forEach(p=>p.classList.toggle("active", p.id === "tab-"+tab));
    if(tab === "grid") renderGrid();
    if(tab === "history") loadHistory();
    if(tab === "settings") loadUsers();
    if(tab === "dashboard"){ renderTrend(); renderTankCompare(); resizeDonuts(); }
}

function setSelectedCamera(camId, pushUrl=false){
    selectedCam = String(camId).toUpperCase();
    localStorage.setItem("selectedCam", selectedCam);
    mainVideo.src = `/video/${selectedCam}?t=${Date.now()}`;
    $("footerSelected").textContent = selectedCam;
    resetTrend(false);
    updateSelectedInfo();
    if(pushUrl){
        const url = new URL(window.location.href);
        url.searchParams.set("cam", selectedCam);
        history.replaceState(null, "", url.toString());
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

function getVisibleTrendRows(){
    const windowPoints = Math.max(10, Math.round(Number($("trendWindow").value || 120) / Math.max(0.2, trendZoom)));
    if($("trendAuto").checked) return trendRows.slice(-windowPoints);
    return trendRows.slice(0, windowPoints);
}
function renderTrend(){
    if(!trendChart) return;
    trendWindowSec = Math.max(10, Math.min(7200, Number($("trendWindow").value)||120));
    const yMin = Number($("trendYMin").value), yMax = Number($("trendYMax").value);
    trendChart.options.scales.y.min = isFinite(yMin) ? yMin : 0;
    trendChart.options.scales.y.max = isFinite(yMax) ? yMax : 100;
    trendChart.data.datasets[0].pointRadius = $("trendShowPoints").checked ? 2 : 0;
    const rows = getVisibleTrendRows();
    trendChart.data.labels = rows.map(r=>r.ts.toLocaleTimeString("vi-VN"));
    trendChart.data.datasets[0].data = rows.map(r=>r.level);
    trendChart.update("none");
    $("trendCursor").textContent = rows.length ? `Hiển thị ${rows.length}/${trendRows.length} điểm | Zoom ${trendZoom.toFixed(1)}x | Camera ${selectedCam}` : "Chưa có dữ liệu trend.";
}
function resetTrend(clear=true){
    if(clear) trendRows=[];
    trendZoom = 1;
    if(trendChart){ trendChart.data.labels=[]; trendChart.data.datasets[0].data=[]; trendChart.update("none"); }
    $("trendCursor").textContent="Đã reset trend. Click trên biểu đồ để xem timestamp và giá trị.";
}
function pushTrend(level){
    if(!trendChart || trendPaused) return;
    const now = new Date();
    trendRows.push({ts:now, level:Number(level||0), camera:selectedCam});
    while(trendRows.length > trendHardLimit) trendRows.shift();
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
async function loadCameras(){
    try{
        const res = await fetch("/api/cameras", {cache:"no-store"});
        const data = await res.json();
        cameras = data.cameras || [];
        renderCameraStrip(); renderTankList(); renderAlarmList(data.alarms || []); updateSummary(data.alarms || []); renderTankCompare();
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
        setDot("cameraDot", info.online); setDot("footerDot", info.online);
        $("cameraStateText").textContent = info.online ? "Online" : (info.error || "Offline");
        $("cameraOfflineOverlay").classList.toggle("hidden", !!info.online);
        drawLiveRoi(info);
        pushTrend(info.level || 0);
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
    const box=$("cameraGrid"); box.className=`camera-grid grid-${gridMode}`; box.innerHTML="";
    cameras.slice(0, gridMode === 20 ? 20 : gridMode).forEach(cam=>{
        const tile=document.createElement("div"); tile.className="grid-tile";
        tile.innerHTML=`<img src="/video/${cam.id}?grid=${Date.now()}" alt="${cam.id}"><div class="grid-title">${cam.id} · ${escapeHtml(cam.name||"")}</div><div class="grid-level">${number(cam.level)}%</div>`;
        tile.onclick=()=>{setSelectedCamera(cam.id,true);switchTab("camera");}; box.appendChild(tile);
    });
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
    try{const res=await fetch(`/api/history?camera_id=${selectedCam}&limit=200`,{cache:"no-store"}); const rows=await res.json(); const body=$("historyBody"); body.innerHTML=""; rows.slice().reverse().forEach(r=>{const tr=document.createElement("tr"); tr.innerHTML=`<td>${r.ts}</td><td>${r.camera_id}</td><td>${number(r.level)}%</td><td>${r.status}</td><td>${number(r.confidence)}</td>`; body.appendChild(tr);});}catch(e){}
}

$("btnGridMode").onclick=()=>switchTab("grid");
$("btnPin").onclick=()=>setMessage(`${selectedCam} đã được ghim cho tab này`);
$("btnFullscreen").onclick=()=>$("cameraStage").requestFullscreen && $("cameraStage").requestFullscreen();
$("btnOpenSettings").onclick=()=>openSettings(); $("btnOpenSettings2").onclick=()=>openSettings(); $("btnCloseSettings").onclick=()=>closeSettings();

async function openSettings(){
    if(!canEdit()){openLogin("Cần quyền Cài đặt hoặc Admin để chỉnh camera.");return;}
    const cam = cameras.find(c=>c.id===selectedCam) || await (await fetch(`/api/camera/${selectedCam}`)).json();
    $("modalCamTitle").textContent=`${cam.id} · ${cam.name || ""}`; $("setName").value=cam.name||""; $("setArea").value=cam.area||""; $("setSource").value=cam.source??""; $("setEnabled").value=String(!!cam.enabled); $("setLow").value=cam.low_threshold??20; $("setHigh").value=cam.high_threshold??90; $("setOverlay").value=String(!!cam.show_overlay); $("setProcessFps").value=cam.process_fps||8;
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
    const payload={name:$("setName").value,area:$("setArea").value,source:$("setSource").value,enabled:$("setEnabled").value==="true",low_threshold:Number($("setLow").value),high_threshold:Number($("setHigh").value),show_overlay:$("setOverlay").value==="true",process_fps:Number($("setProcessFps").value),roi:{x1:Number($("roiX1").value),y1:Number($("roiY1").value),x2:Number($("roiX2").value),y2:Number($("roiY2").value)}};
    try{const res=await fetch(`/api/camera/${selectedCam}/config`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}); const data=await res.json(); if(data.result!=="ok")throw new Error(data.message||"error"); closeSettings(); setMessage("Đã lưu cài đặt camera"); await loadCameras(); setSelectedCamera(selectedCam,true);}catch(e){setMessage("Lỗi lưu cấu hình: "+e.message,"error");}
};

function resizeRoiCanvas(){roiCanvas.width=mainVideo.clientWidth;roiCanvas.height=mainVideo.clientHeight; if(lastSelectedInfo) drawLiveRoi(lastSelectedInfo);}
mainVideo.onload=()=>resizeRoiCanvas(); window.addEventListener("resize",()=>{resizeRoiCanvas();resizePreviewCanvas();});
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

async function init(){
    initCharts(); renderTrend(); await loadMe(); await loadSystem(); await loadCameras(); resizeDonuts(); setSelectedCamera(selectedCam,true); renderGrid();
    setInterval(loadCameras,2000); setInterval(updateSelectedInfo,1000); setInterval(loadSystem,6000); setMessage("WaterAI Multi Camera Ready");
}
init();
