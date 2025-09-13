// Frontend polling for /telemetry and updating model-viewer + charts + background
const mv = document.getElementById('mv');
const statusEl = document.getElementById('status');
const tVal = document.getElementById('tVal');
const xVal = document.getElementById('xVal');
const yVal = document.getElementById('yVal');
const zVal = document.getElementById('zVal');
const tsVal = document.getElementById('tsVal');

const API_URL = `${window.location.origin}/telemetry`; // same origin as server.py

function setStatus(msg){ statusEl.textContent = msg; }

// Temperature [-20..60] -> hue [220..0] (blue -> red)
function tempToHue(c){
  const t = Math.min(1, Math.max(0, (Number(c) + 20) / 80));
  return 220 * (1 - t); // 220 blue to 0 red
}
function applyBackground(c){
  const hue = tempToHue(c);
  document.documentElement.style.setProperty('--bgHue', String(hue));
}

// Charts setup
const tempCtx = document.getElementById('tempChart').getContext('2d');
const axesCtx = document.getElementById('axesChart').getContext('2d');

const tempChart = new Chart(tempCtx, {
  type: 'line',
  data: { labels: [], datasets: [{ label: '°C', data: [], tension: 0.25 }] },
  options: {
    animation: false,
    responsive: true,
    scales: { x: { display: false }, y: { suggestedMin: -20, suggestedMax: 60 } },
    plugins: { legend: { display: true } }
  }
});

const axesChart = new Chart(axesCtx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      { label: 'X (°)', data: [], tension: 0.25 },
      { label: 'Y (°)', data: [], tension: 0.25 },
      { label: 'Z (m)', data: [], tension: 0.25 }
    ]
  },
  options: {
    animation: false,
    responsive: true,
    scales: { x: { display: false }, y: { suggestedMin: -180, suggestedMax: 180 } },
    plugins: { legend: { display: true } }
  }
});

function pushData(ts, temp, axes){
  const label = new Date(ts).toLocaleTimeString();
  // Temperature
  tempChart.data.labels.push(label);
  tempChart.data.datasets[0].data.push(temp);
  if (tempChart.data.labels.length > 120) { // keep last ~2 minutes @1Hz
    tempChart.data.labels.shift();
    tempChart.data.datasets[0].data.shift();
  }
  tempChart.update();

  // Axes
  axesChart.data.labels.push(label);
  axesChart.data.datasets[0].data.push(axes.x);
  axesChart.data.datasets[1].data.push(axes.y);
  axesChart.data.datasets[2].data.push(axes.z);
  if (axesChart.data.labels.length > 120) {
    axesChart.data.labels.shift();
    axesChart.data.datasets[0].data.shift();
    axesChart.data.datasets[1].data.shift();
    axesChart.data.datasets[2].data.shift();
  }
  axesChart.update();
}

function applyToUI(state){
  tVal.textContent = state.temperatureC.toFixed(1);
  xVal.textContent = state.axes.x.toFixed(0);
  yVal.textContent = state.axes.y.toFixed(0);
  zVal.textContent = state.axes.z.toFixed(1);
  tsVal.textContent = new Date(state.timestamp).toLocaleString();

  // Update background color
  applyBackground(state.temperatureC);

  // Rotate view (camera orbit) according to axes
  mv.cameraOrbit = `${state.axes.x}deg ${state.axes.y}deg ${state.axes.z}m`;

  // Append to charts
  pushData(state.timestamp, state.temperatureC, state.axes);
}

let lastTs = null;
async function poll(){
  try{
    const res = await fetch(API_URL, { method: 'GET' });
    if(!res.ok) throw new Error(`HTTP ${res.status}`);
    const state = await res.json();
    if (state.timestamp !== lastTs) {
      lastTs = state.timestamp;
      applyToUI(state);
      setStatus('OK');
    }
  }catch(e){
    setStatus('API nicht erreichbar…');
  }
}

setInterval(poll, 1000);
poll();
