// Frontend polling for /telemetry and updating background + charts + values

const statusEl = document.getElementById('status');
const tVal = document.getElementById('tVal');
const lightRawVal = document.getElementById('lightRawVal');
const lightNormVal = document.getElementById('lightNormVal');
const tsVal = document.getElementById('tsVal');

const API_URL = `https://ibw-ta-v-api.onrender.com/telemetry`;
const LED_URL = `https://ibw-ta-v-api.onrender.com/led`;


function setStatus(msg) { if (statusEl) statusEl.textContent = msg; }

// Temperature [-20..60] -> hue [220..0] (blue -> red)
function tempToHue(c) {
    const t = Math.min(1, Math.max(0, (Number(c) + 20) / 80));
    return 220 * (1 - t); // 220 blue -> 0 red
}
function applyBackground(c) {
    const hue = tempToHue(c);
    document.documentElement.style.setProperty('--bgHue', String(hue));
}

// Charts
const tempCtx = document.getElementById('tempChart')?.getContext('2d');
const lightCtx = document.getElementById('lightChart')?.getContext('2d');

const tempChart = tempCtx ? new Chart(tempCtx, {
    type: 'line',
    data: { labels: [], datasets: [{ label: '°C', data: [], tension: 0.25 }] },
    options: {
        animation: false,
        responsive: true,
        scales: { x: { display: false }, y: { suggestedMin: -20, suggestedMax: 60 } },
        plugins: { legend: { display: true } }
    }
}) : null;

// Light chart: zwei Reihen (raw 0..65535, norm 0..1)
const lightChart = lightCtx ? new Chart(lightCtx, {
    type: 'line',
    data: {
        labels: [],
        datasets: [
            { label: 'light_raw (0..65535)', data: [], tension: 0.25, yAxisID: 'y-raw' },
            { label: 'light_norm (0..1.0)', data: [], tension: 0.25, yAxisID: 'y-norm' }
        ]
    },
    options: {
        animation: false,
        responsive: true,
        scales: {
            x: { display: false },
            'y-raw': { type: 'linear', position: 'left', suggestedMin: 0, suggestedMax: 65535 },
            'y-norm': { type: 'linear', position: 'right', suggestedMin: 0, suggestedMax: 1 }
        },
        plugins: { legend: { display: true } }
    }
}) : null;

function pushData(ts, tempC, light) {
    const label = new Date(ts).toLocaleTimeString();

    if (tempChart) {
        tempChart.data.labels.push(label);
        tempChart.data.datasets[0].data.push(tempC);
        if (tempChart.data.labels.length > 120) {
            tempChart.data.labels.shift();
            tempChart.data.datasets[0].data.shift();
        }
        tempChart.update();
    }

    if (lightChart) {
        lightChart.data.labels.push(label);
        lightChart.data.datasets[0].data.push(Number(light.raw));
        lightChart.data.datasets[1].data.push(Number(light.norm));
        if (lightChart.data.labels.length > 120) {
            lightChart.data.labels.shift();
            lightChart.data.datasets[0].data.shift();
            lightChart.data.datasets[1].data.shift();
        }
        lightChart.update();
    }
}

function applyToUI(state) {
    // erwartetes Schema: { temperatureC, light:{raw, norm}, timestamp }
    const t = Number(state.temperatureC);
    const lr = Number(state.light?.raw ?? 0);
    const ln = Number(state.light?.norm ?? 0);

    if (tVal) tVal.textContent = isFinite(t) ? t.toFixed(1) : '–';
    if (lightRawVal) lightRawVal.textContent = isFinite(lr) ? String(lr) : '–';
    if (lightNormVal) lightNormVal.textContent = isFinite(ln) ? ln.toFixed(4) : '–';
    if (tsVal) tsVal.textContent = new Date(state.timestamp).toLocaleString();

    applyBackground(t);
    pushData(state.timestamp, t, { raw: lr, norm: ln });
}

let lastTs = null;
async function poll() {
    try {
        const res = await fetch(API_URL, { method: 'GET' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const state = await res.json();
        if (state.timestamp !== lastTs) {
            lastTs = state.timestamp;
            applyToUI(state);
            setStatus('OK');
        }
    } catch (e) {
        setStatus('API nicht erreichbar…');
    }
}

function rgbFromHex(hex) {
    const h = hex.replace('#', '');
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
function hexFromRgb([r, g, b]) {
    const to2 = (n) => n.toString(16).padStart(2, '0');
    return `#${to2(r)}${to2(g)}${to2(b)}`;
}

async function ledGet() {
    const res = await fetch(LED_URL, { method: 'GET' });
    if (!res.ok) throw new Error(`LED GET HTTP ${res.status}`);
    return await res.json(); // { on, rgb:[r,g,b], brightness, updatedAt }
}

async function ledPut({ on, rgb, brightness }) {
    const body = {};
    if (typeof on === 'boolean') body.on = on;
    if (Array.isArray(rgb)) body.rgb = rgb;
    if (typeof brightness === 'number') body.brightness = brightness;

    const res = await fetch(LED_URL, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    if (!res.ok) throw new Error(`LED PUT HTTP ${res.status}`);
    return await res.json();
}

// === LED UI Wiring ===
const ledStateEl = document.getElementById('ledState');
const btnOn = document.getElementById('btnOn');
const btnOff = document.getElementById('btnOff');
const colorInp = document.getElementById('ledColor');
const briInp = document.getElementById('ledBri');
const briVal = document.getElementById('briVal');

function setLedStatusText(led) {
    if (!ledStateEl) return;
    const hex = hexFromRgb(led.rgb || [0, 0, 0]);
    ledStateEl.textContent = led.on ? `AN (${hex}, ${led.brightness}%)` : 'AUS';
}

async function refreshLedUI() {
    try {
        const led = await ledGet();
        if (colorInp) colorInp.value = hexFromRgb(led.rgb || [255, 160, 0]);
        if (briInp) { briInp.value = led.brightness ?? 20; briVal.textContent = String(briInp.value); }
        setLedStatusText(led);
    } catch (e) {
        if (ledStateEl) ledStateEl.textContent = 'API nicht erreichbar…';
    }
}

async function setLedOn() {
    try {
        const rgb = rgbFromHex(colorInp.value || '#ffa000');
        const bri = Number(briInp.value || 20);
        const led = await ledPut({ on: true, rgb, brightness: bri });
        setLedStatusText(led);
    } catch (e) {
        if (ledStateEl) ledStateEl.textContent = 'LED setzen fehlgeschlagen';
    }
}
async function setLedOff() {
    try {
        const led = await ledPut({ on: false });
        setLedStatusText(led);
    } catch (e) {
        if (ledStateEl) ledStateEl.textContent = 'LED setzen fehlgeschlagen';
    }
}

if (btnOn) btnOn.addEventListener('click', setLedOn);
if (btnOff) btnOff.addEventListener('click', setLedOff);
if (briInp) briInp.addEventListener('input', () => { briVal.textContent = String(briInp.value); });

// Beim Laden einmal initial holen:
refreshLedUI();

// Optional: alle 3 Sekunden Status aktualisieren (falls jemand anderes /led ändert)
setInterval(refreshLedUI, 3000);

setInterval(poll, 1000);
poll();
