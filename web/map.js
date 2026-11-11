/*
 * ISS Track (web) map: the world, the day/night line, the station and its
 * tracks, the observer's ring and the next pass. This is the renderer from
 * the integration's own map page (my_iss.html), lifted into a module with
 * the transport swapped: the track comes from the service over HTTP.
 *
 * Nothing here computes an orbit. The page draws the track it is given.
 */
const PI = Math.PI;
const HALF_PI = PI * 0.5;
const TWO_PI = PI * 2;

let currentISS = { x: 0, y: 0, size: 50, valid: false };

const DAYIMAGESRC = '/vendor/images/mapday.jpg';
const NIGHTIMAGESRC = '/vendor/images/mapnight.jpg';
const ISSIMAGESRC = '/vendor/images/iss_tiny.png';
const SUNIMAGESRC = '/vendor/images/sun_128.png';
const MOONIMAGESRC = '/vendor/images/moon_128.png';

// Everything about the orbit comes from the service, which runs the
// integration's engine. This page draws the track it is given and
// computes no position of its own.
let track = null;
let trackTimer = null;
let overlay = { observer: null, pass: null };
const trackListeners = [];
const TRACK_REFRESH_MS = 5 * 60 * 1000;
const TRACK_RETRY_MS = 15 * 1000;

// The passes drawer tells the map whose sky to ring and which pass to mark.
export function setOverlay(value) {
  overlay = value || { observer: null, pass: null };
  if (isInitialized) drawEverything();
}

// Miles or kilometers, decided by the page.
export function setUnits(metric) {
  useMetric = Boolean(metric);
  if (isInitialized) drawEverything();
}

// Called with the track each time it is fetched, so the page can show
// the age of the orbital elements.
export function onTrack(listener) {
  trackListeners.push(listener);
  if (track) listener(track);
}

const canvas = document.getElementById('earthCanvas');
const ctx = canvas.getContext('2d', { willReadFrequently: false });
const waitmsg = document.getElementById('waitmsg');
const fullscreenToggle = document.getElementById('fullscreen-toggle');
const tooltip = document.getElementById('iss-tooltip');
const videoModal = document.getElementById('videoModal');
const modalIframe = videoModal.querySelector('iframe');
const videoNotice = document.getElementById('videoNotice');
const closeButton = document.querySelector('.close');
const latEl = document.getElementById('lat');
const lonEl = document.getElementById('lon');
const altEl = document.getElementById('alt');
const speedEl = document.getElementById('speed');
const timeEl = document.getElementById('time');
const tleInfo = document.getElementById('tle-info');

let canvasWidth, canvasHeight;
let useMetric = false;
let dayImage = null, nightImage = null, issImage = null, sunImage = null, moonImage = null;
let imagesLoaded = 0;

let isInitialized = false;

function drawSunIcon(sunLatDeg, sunLonDeg) {
  if (!sunImage) {
    console.warn("Sun image not loaded yet");
    return;
  }
  const sunPosXY = latLonToXY(sunLatDeg, sunLonDeg);
  const sunIconSize = 40;
  ctx.save();
  ctx.drawImage(sunImage, sunPosXY.x - sunIconSize / 2, sunPosXY.y - sunIconSize / 2, sunIconSize, sunIconSize);
  ctx.restore();
}

function drawMoonIcon(moonLatDeg, moonLonDeg) {
  if (!moonImage) {
    console.warn("Moon image not loaded yet");
    return;
  }
  const moonPosXY = latLonToXY(moonLatDeg, moonLonDeg);
  const moonIconSize = 40;
  ctx.save();
  ctx.drawImage(moonImage, moonPosXY.x - moonIconSize / 2, moonPosXY.y - moonIconSize / 2, moonIconSize, moonIconSize);
  ctx.restore();
}

function drawViewCircle(lat, lon, angularRadiusRad, color = 'rgba(0, 255, 0, 0.5)') {
  const numPoints = 100;
  ctx.beginPath();
  for (let i = 0; i <= numPoints; i++) {
    const theta = (i / numPoints) * TWO_PI;
    const centerLatRad = lat * PI / 180;
    const centerLonRad = lon * PI / 180;
    const pointLat = Math.asin(
      Math.sin(centerLatRad) * Math.cos(angularRadiusRad) +
      Math.cos(centerLatRad) * Math.sin(angularRadiusRad) * Math.cos(theta)
    );
    const pointLon = centerLonRad + Math.atan2(
      Math.sin(theta) * Math.sin(angularRadiusRad) * Math.cos(centerLatRad),
      Math.cos(angularRadiusRad) - Math.sin(centerLatRad) * Math.sin(pointLat)
    );
    const pointLatDeg = pointLat * 180 / PI;
    const pointLonDeg = pointLon * 180 / PI;
    const { x, y } = latLonToXY(pointLatDeg, pointLonDeg);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.stroke();
}

function updateCanvasSize() {
  // A canvas that has not been laid out yet reports no width; a zero width
  // would make the terminator loop step by zero and never end.
  canvasWidth = canvas.clientWidth || Math.min(1000, window.innerWidth) || 1000;
  canvasHeight = canvasWidth / 2;
  canvas.width = canvasWidth;
  canvas.height = canvasHeight;
}

function loadImages(callback) {
  dayImage = new Image();
  nightImage = new Image();
  issImage = new Image();
  sunImage = new Image();
  moonImage = new Image();

  dayImage.onload = () => {
    imagesLoaded++;
    updateCanvasSize();
    if (!isInitialized) {
      initialRender();
    }
    if (imagesLoaded === 5) {
      callback();
    }
  };
  nightImage.onload = () => {
    imagesLoaded++;
    if (imagesLoaded === 5) callback();
  };
  issImage.onload = () => {
    imagesLoaded++;
    if (imagesLoaded === 5) callback();
    else if (!isInitialized) initialRender();
  };
  sunImage.onload = () => {
    imagesLoaded++;
    if (imagesLoaded === 5) callback();
  };
  moonImage.onload = () => {
    imagesLoaded++;
    if (imagesLoaded === 5) callback();
  };

  dayImage.onerror = () => console.error("Failed to load day image");
  nightImage.onerror = () => console.error("Failed to load night image");
  issImage.onerror = () => console.error("Failed to load ISS image");
  sunImage.onerror = () => console.error("Failed to load sun image");
  moonImage.onerror = () => console.error("Failed to load Moon image");

  dayImage.src = DAYIMAGESRC;
  nightImage.src = NIGHTIMAGESRC;
  issImage.src = ISSIMAGESRC;
  sunImage.src = SUNIMAGESRC;
  moonImage.src = MOONIMAGESRC;
}

// Fetch the ground track around now. It is refreshed every few minutes;
// while a fetch fails, the last track keeps the map moving.
async function loadTrack() {
  clearTimeout(trackTimer);
  let delay = TRACK_REFRESH_MS;
  try {
    const response = await fetch('/api/track', { cache: 'no-store' });
    if (!response.ok) throw new Error(`track request failed (${response.status})`);
    track = await response.json();
    waitmsg.style.display = 'none';
    for (const listener of trackListeners) listener(track);
  } catch (err) {
    console.error("ISS Track data not available:", err);
    delay = TRACK_RETRY_MS;
    if (!track) {
      waitmsg.textContent = 'Waiting for orbital data…';
      waitmsg.style.display = 'block';
    }
  }
  trackTimer = setTimeout(loadTrack, delay);
}

function wrapLon(lon) {
  return ((lon + 540) % 360) - 180;
}

function lonBetween(a, b, f) {
  return wrapLon(a + (((b - a + 540) % 360) - 180) * f);
}

// The station's state at a moment, read off the track by interpolation.
function stateAt(ms) {
  if (!track) return null;
  const pos = (ms - track.start) / (track.step * 1000);
  const i = Math.floor(pos);
  if (i < 0 || i >= track.points.length - 1) return null;
  const f = pos - i;
  const a = track.points[i], b = track.points[i + 1];
  return {
    lat: a[0] + (b[0] - a[0]) * f,
    lon: lonBetween(a[1], b[1], f),
    altKm: a[2] + (b[2] - a[2]) * f,
    speedKps: a[3] + (b[3] - a[3]) * f
  };
}

function getCurrentISSPosition() {
  const state = stateAt(Date.now());
  if (!state) console.warn("ISS position not available yet");
  return state;
}

// Where the Sun is overhead, from the samples sent with the track.
function sunAt(ms) {
  const sun = track && track.sun;
  if (!sun || sun.length < 2) return null;
  let i = 0;
  while (i < sun.length - 2 && sun[i + 1][0] <= ms) i++;
  const a = sun[i], b = sun[i + 1];
  const f = (ms - a[0]) / (b[0] - a[0]);
  return { lat: a[1] + (b[1] - a[1]) * f, lon: lonBetween(a[2], b[2], f) };
}

function orbitPoints(fromMs, toMs, stepMs = 60000) {
  const points = [];
  for (let t = fromMs; t <= toMs; t += stepMs) {
    const state = stateAt(t);
    if (state) points.push({ lat: state.lat, lon: state.lon });
  }
  return points;
}

// The observer's ring (everywhere the station is at least 10 degrees up
// for them) and the ground track of their next matching pass.
function drawOverlay(altKm) {
  const who = overlay.observer;
  if (who && who.latitude !== null && who.longitude !== null) {
    const ratio = 6371 / (6371 + altKm);
    const floor = 10 * PI / 180;
    const reach = Math.acos(ratio * Math.cos(floor)) - floor;
    drawViewCircle(who.latitude, who.longitude, reach, 'rgba(133, 183, 235, 0.95)');
    const { x, y } = latLonToXY(who.latitude, who.longitude);
    ctx.save();
    ctx.fillStyle = 'rgb(133, 183, 235)';
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, TWO_PI);
    ctx.fill();
    ctx.restore();
  }
  const pass = overlay.pass;
  if (pass) {
    const points = orbitPoints(pass.start, pass.end, 10000);
    if (points.length > 1) drawOrbitSegments(splitDateLine(points), 'rgb(93, 202, 165)', 5);
  }
  // Up for this observer but not visible: dashed and gray, never green.
  const unseen = overlay.unseen;
  if (unseen) {
    const points = orbitPoints(unseen.start, unseen.end, 10000);
    if (points.length > 1) {
      // A dark bed first, so the dashes read as gray and not as gaps in
      // the yellow track underneath.
      const segments = splitDateLine(points);
      drawOrbitSegments(segments, 'rgb(40, 44, 60)', 5);
      drawOrbitSegments(segments, 'rgb(190, 194, 204)', 3, [7, 6]);
    }
  }
}

function getMoonPosition(date = new Date()) {
  if (typeof Astronomy === 'undefined') {
    console.error('Astronomy library not loaded');
    return { lat: 0, lon: 0 };
  }
  const dummyObserver = new Astronomy.Observer(0, 0, 0);
  const equ = Astronomy.Equator('Moon', date, dummyObserver, true, true);
  const ra = equ.ra;
  const dec = equ.dec;
  const gmst = Astronomy.SiderealTime(date);
  let moonLonDeg = (ra - gmst) * 15;
  while (moonLonDeg > 180) moonLonDeg -= 360;
  while (moonLonDeg < -180) moonLonDeg += 360;
  const moonLatDeg = dec;
  return { lat: moonLatDeg, lon: moonLonDeg };
}

function drawDayNight() {
  if (!dayImage || !nightImage) {
    console.warn("Day or night image not loaded, cannot draw terminator");
    if (dayImage) ctx.drawImage(dayImage, 0, 0, canvasWidth, canvasHeight);
    return;
  }
  ctx.drawImage(dayImage, 0, 0, canvasWidth, canvasHeight);
  const sunPos = sunAt(Date.now());
  if (!sunPos) return;
  const sunLonDeg = sunPos.lon;
  const sunLatDeg = sunPos.lat;
  const sunLonRad = sunLonDeg * PI / 180;
  const sunLatRad = sunLatDeg * PI / 180;
  const cosSunLatRad = Math.cos(Math.abs(sunLatRad));
  const sinSunLatRad = Math.sin(Math.abs(sunLatRad));
  const terminatorPoints = [];
  const NUM_SEGS = 90;
  const step = canvasWidth / NUM_SEGS;
  if (!(step > 0)) return;
  for (let x = -step; x <= canvasWidth + step; x += step) {
    let lonRad = XToLonRad(x);
    while (lonRad > PI) lonRad -= TWO_PI;
    while (lonRad < -PI) lonRad += TWO_PI;
    let latRad;
    if (Math.abs(lonRad - sunLonRad) === HALF_PI) {
      latRad = sunLatRad;
    } else {
      latRad = -Math.atan2(cosSunLatRad * Math.cos(lonRad - sunLonRad), sinSunLatRad);
      latRad = sunLatRad < 0 ? -latRad : latRad;
    }
    terminatorPoints.push({ x, y: LatRadToY(latRad) });
  }
  ctx.save();
  ctx.beginPath();
  if (terminatorPoints.length) {
    ctx.moveTo(terminatorPoints[0].x, terminatorPoints[0].y);
    for (let i = 1; i < terminatorPoints.length; i++) {
      ctx.lineTo(terminatorPoints[i].x, terminatorPoints[i].y);
    }
    const bottomY = sunLatDeg < 0 ? 0 : canvasHeight;
    ctx.lineTo(canvasWidth + step, bottomY);
    ctx.lineTo(-step, bottomY);
    ctx.closePath();
    ctx.clip();
  }
  ctx.drawImage(nightImage, 0, 0, canvasWidth, canvasHeight);
  ctx.restore();
  ctx.beginPath();
  ctx.rect(0, 0, canvasWidth, canvasHeight);
  ctx.clip();
  ctx.save();
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.5)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (terminatorPoints.length) {
    let started = false;
    for (let i = 0; i < terminatorPoints.length; i++) {
      const { x, y } = terminatorPoints[i];
      if (x >= 0 && x <= canvasWidth) {
        if (!started) {
          ctx.moveTo(x, y);
          started = true;
        } else {
          ctx.lineTo(x, y);
        }
      }
    }
    ctx.stroke();
  }
  ctx.restore();
  ctx.save();
  ctx.globalAlpha = 0.3;
  const gradientWidth = 30;
  for (let i = 0; i < terminatorPoints.length - 1; i += 2) {
    const p1 = terminatorPoints[i];
    const p2 = terminatorPoints[i + 1];
    if (p1.x < 0 || p1.x > canvasWidth || p2.x < 0 || p2.x > canvasWidth) continue;
    const dx = p2.x - p1.x;
    const dy = p2.y - p1.y;
    const steps = Math.max(1, Math.ceil(Math.hypot(dx, dy) / 4));
    for (let j = 0; j < steps; j++) {
      const t = j / steps;
      const x = p1.x + dx * t;
      const y = p1.y + dy * t;
      const gradient = ctx.createLinearGradient(x - gradientWidth / 2, y, x + gradientWidth / 2, y);
      gradient.addColorStop(0, 'rgba(0, 0, 0, 0)');
      gradient.addColorStop(1, 'rgba(0, 0, 0, 0.5)');
      ctx.fillStyle = gradient;
      ctx.fillRect(x - gradientWidth / 2, y - 2, gradientWidth, 4);
    }
  }
  ctx.restore();
  drawSunIcon(sunLatDeg, sunLonDeg);
  const moonPos = getMoonPosition();
  drawMoonIcon(moonPos.lat, moonPos.lon);
}

function XToLonRad(x) {
  return ((x - canvasWidth / 2) * TWO_PI / canvasWidth);
}

function LatRadToY(latRad) {
  return (canvasHeight * (0.5 - latRad / PI));
}

function latLonToXY(lat, lon) {
  const x = (lon + 180) * (canvasWidth / 360);
  const y = (90 - lat) * (canvasHeight / 180);
  return { x, y };
}

function splitDateLine(points) {
  if (!points.length) return [];
  const segs = [];
  let seg = [points[0]];
  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1], cur = points[i];
    if (Math.abs(cur.lon - prev.lon) > 180) {
      segs.push(seg);
      seg = [cur];
    } else {
      if (prev.lon > 150 && cur.lon < -150) {
        const t = (180 - prev.lon) / (prev.lon - (cur.lon + 360));
        const lat = prev.lat + t * (cur.lat - prev.lat);
        seg.push({ lat: lat, lon: 180 });
        segs.push(seg);
        seg = [{ lat: lat, lon: -180 }, cur];
      } else if (prev.lon < -150 && cur.lon > 150) {
        const t = (-180 - prev.lon) / (prev.lon - (cur.lon - 360));
        const lat = prev.lat + t * (cur.lat - prev.lat);
        seg.push({ lat: lat, lon: -180 });
        segs.push(seg);
        seg = [{ lat: lat, lon: 180 }, cur];
      } else {
        seg.push(cur);
      }
    }
  }
  if (seg.length) segs.push(seg);
  return segs;
}

function drawOrbitSegments(segments, color, width = 2, dash = []) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.setLineDash(dash);
  segments.forEach(sg => {
    if (sg.length) {
      ctx.beginPath();
      let { x, y } = latLonToXY(sg[0].lat, sg[0].lon);
      ctx.moveTo(x, y);
      for (let i = 1; i < sg.length; i++) {
        ({ x, y } = latLonToXY(sg[i].lat, sg[i].lon));
        ctx.lineTo(x, y);
      }
      ctx.stroke();
    }
  });
  ctx.restore();
}

function drawISS(lat, lon) {
  if (isNaN(lat) || isNaN(lon)) {
    console.warn("Invalid lat/lon for ISS:", lat, lon);
    return;
  }
  const { x, y } = latLonToXY(lat, lon);
  const issSize = 50;
  ctx.save();
  if (issImage) {
    currentISS = { x: x, y: y, size: issSize, valid: true };
    ctx.shadowBlur = 10;
    ctx.shadowColor = 'rgba(255, 255, 255, 0.7)';
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;
    ctx.drawImage(issImage, x - issSize / 2, y - issSize / 2, issSize, issSize);
    ctx.shadowBlur = 0;
    ctx.shadowColor = 'transparent';
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = 0;
  } else {
    console.warn("ISS image not loaded, using fallback");
    currentISS = { x: x, y: y, size: 7, valid: false };
    ctx.fillStyle = 'red';
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, TWO_PI);
    ctx.fill();
  }
  ctx.restore();
}

function drawLatLonGrid() {
  ctx.save();
  ctx.lineWidth = 1;
  ctx.setLineDash([5, 5]);

  for (let lat = -90; lat <= 90; lat += 30) {
    if (lat === 0) {
      ctx.strokeStyle = 'red';
    } else {
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
    }
    const { x: x1, y } = latLonToXY(lat, -180);
    const { x: x2 } = latLonToXY(lat, 180);
    ctx.beginPath();
    ctx.moveTo(x1, y);
    ctx.lineTo(x2, y);
    ctx.stroke();
  }

  for (let lon = -180; lon <= 180; lon += 30) {
    if (lon === 0) {
      ctx.strokeStyle = 'blue';
    } else {
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.3)';
    }
    const { x, y: y1 } = latLonToXY(90, lon);
    const { y: y2 } = latLonToXY(-90, lon);
    ctx.beginPath();
    ctx.moveTo(x, y1);
    ctx.lineTo(x, y2);
    ctx.stroke();
  }

  ctx.setLineDash([]);
  ctx.restore();
}

function drawInitialMap() {
  if (!dayImage) {
    console.warn("Day image not loaded, using fallback background");
    ctx.fillStyle = '#222';
    ctx.fillRect(0, 0, canvasWidth, canvasHeight);
  } else {
    ctx.drawImage(dayImage, 0, 0, canvasWidth, canvasHeight);
  }
  drawLatLonGrid();
  waitmsg.style.display = 'none';
}

function drawInitialISS(lat, lon) {
  if (isNaN(lat) || isNaN(lon)) {
    console.warn("Invalid lat/lon for initial ISS:", lat, lon);
    return;
  }
  drawISS(lat, lon);
}

// The player is fetched from YouTube only when someone opens the video, and
// dropped again on close: until then the page asks YouTube for nothing, which
// is what the privacy page promises.
//
// NASA gives each new stream a new address and the old one stops for good,
// so the page carries none: it asks the site which stream is live now. An
// answer with no id, or no answer, shows the notice instead of the player.
let modalOpen = false;

async function liveVideoId() {
  try {
    const response = await fetch('/api/video');
    if (!response.ok) return null;
    const id = (await response.json()).id;
    return typeof id === 'string' && /^[A-Za-z0-9_-]{11}$/.test(id) ? id : null;
  } catch (err) {
    return null;
  }
}

async function openModal() {
  modalOpen = true;
  videoNotice.hidden = true;
  modalIframe.hidden = false;
  videoModal.style.display = 'flex';
  const id = await liveVideoId();
  if (!modalOpen) return; // closed while the answer was on its way
  if (id) {
    modalIframe.src = `https://www.youtube.com/embed/${id}?autoplay=1`;
  } else {
    modalIframe.hidden = true;
    videoNotice.hidden = false;
  }
}

function closeModal() {
  modalOpen = false;
  videoModal.style.display = 'none';
  modalIframe.src = 'about:blank';
}

window.onclick = function(event) {
  if (event.target === videoModal) {
    closeModal();
  }
};

function setupCanvasEvents() {
  canvas.addEventListener('mousemove', function(event) {
    const rect = canvas.getBoundingClientRect();
    const mouseX = event.clientX - rect.left;
    const mouseY = event.clientY - rect.top;
    if (currentISS.valid &&
        mouseX >= currentISS.x - currentISS.size / 2 &&
        mouseX <= currentISS.x + currentISS.size / 2 &&
        mouseY >= currentISS.y - currentISS.size / 2 &&
        mouseY <= currentISS.y + currentISS.size / 2) {
      tooltip.classList.add('tooltip-visible');
      canvas.classList.add('cursor-pointer');
      canvas.classList.remove('cursor-default');
      const iconX = rect.left + currentISS.x;
      const iconY = rect.top + currentISS.y;
      const margin = 8;
      let left = iconX - tooltip.offsetWidth / 2;
      left = Math.max(margin, Math.min(left, window.innerWidth - tooltip.offsetWidth - margin));
      let top = iconY - 10;
      top = Math.max(margin, Math.min(top, window.innerHeight - tooltip.offsetHeight - margin));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    } else {
      tooltip.classList.remove('tooltip-visible');
      canvas.classList.remove('cursor-pointer');
      canvas.classList.add('cursor-default');
    }
  });

  canvas.addEventListener('click', function(event) {
    const rect = canvas.getBoundingClientRect();
    const clickX = event.clientX - rect.left;
    const clickY = event.clientY - rect.top;
    if (currentISS.valid &&
        clickX >= currentISS.x - currentISS.size / 2 &&
        clickX <= currentISS.x + currentISS.size / 2 &&
        clickY >= currentISS.y - currentISS.size / 2 &&
        clickY <= currentISS.y + currentISS.size / 2) {
      openModal();
    }
  });

  canvas.addEventListener('mouseleave', function() {
    tooltip.classList.remove('tooltip-visible');
    canvas.classList.remove('cursor-pointer');
    canvas.classList.add('cursor-default');
  });
}

function setupOtherEvents() {
  fullscreenToggle.addEventListener('click', toggleFullScreen);
  closeButton.addEventListener('click', closeModal);
}

function initialRender() {
  drawInitialMap();
  const data = getCurrentISSPosition();
  if (data) {
    drawInitialISS(data.lat, data.lon);
    updateInfoPanel(data.lat, data.lon, data.altKm, data.speedKps);
    updateTLEInfo();
  } else {
    console.warn("No ISS position data available for initial render");
  }
}

function drawEverything() {
  ctx.clearRect(0, 0, canvasWidth, canvasHeight);
  drawDayNight();
  drawLatLonGrid();

  const data = getCurrentISSPosition();
  if (!data) {
    console.error("No ISS position data available for full render");
    return;
  }
  const { lat, lon, altKm, speedKps } = data;

  const periodMs = track.period_minutes * 60 * 1000;
  const nowMs = Date.now();
  const pastPts = orbitPoints(nowMs - periodMs, nowMs);
  const futurePts = orbitPoints(nowMs, nowMs + periodMs);

  drawOrbitSegments(splitDateLine(pastPts), 'white');
  drawOrbitSegments(splitDateLine(futurePts), 'yellow');
  drawOverlay(altKm);
  drawISS(lat, lon);

  const viewRadiusKm = 2000;
  const earthRadiusKm = 6371;
  const angularRadiusRad = Math.asin(viewRadiusKm / (earthRadiusKm + altKm));
  drawViewCircle(lat, lon, angularRadiusRad);

  const pastEdge = pastPts.length ? pastPts[0] : { lat, lon };
  const futureEdge = futurePts.length ? futurePts[futurePts.length - 1] : { lat, lon };
  const futureXY = latLonToXY(futureEdge.lat, futureEdge.lon);
  const pastXY = latLonToXY(pastEdge.lat, pastEdge.lon);
  const pastSegments = splitDateLine(pastPts);
  const futureSegments = splitDateLine(futurePts);
  let pastStartXY = pastXY, futureEndXY = futureXY;
  if (pastSegments.length > 0 && pastSegments[0].length > 0) {
    const firstPastPoint = pastSegments[0][0];
    pastStartXY = latLonToXY(firstPastPoint.lat, firstPastPoint.lon);
  }
  if (futureSegments.length > 0 && futureSegments[futureSegments.length - 1].length > 0) {
    const lastFuturePoint = futureSegments[futureSegments.length - 1][futureSegments[futureSegments.length - 1].length - 1];
    futureEndXY = latLonToXY(lastFuturePoint.lat, lastFuturePoint.lon);
  }
  let pastDirection = 0, futureDirection = 0;
  if (pastSegments.length > 0 && pastSegments[0].length > 1) {
    pastDirection = getOrbitDirection(pastSegments[0], 0);
  } else {
    pastDirection = Math.atan2(futureXY.y - pastXY.y, futureXY.x - pastXY.x) || 0;
  }
  if (futureSegments.length > 0) {
    const lastFutureSegment = futureSegments[futureSegments.length - 1];
    if (lastFutureSegment.length > 1) {
      futureDirection = getOrbitDirection(lastFutureSegment, lastFutureSegment.length - 1);
    }
  }

  function getOrbitDirection(segment, index) {
    if (!segment || segment.length < 2) return 0;
    if (index >= segment.length || index < 0) return 0;
    const prevIndex = Math.max(0, index - 1);
    const nextIndex = Math.min(segment.length - 1, index + 1);
    const prevPoint = latLonToXY(segment[prevIndex].lat, segment[prevIndex].lon);
    const nextPoint = latLonToXY(segment[nextIndex].lat, segment[nextIndex].lon);
    const dx = nextPoint.x - prevPoint.x;
    const dy = nextPoint.y - prevPoint.y;
    return dx === 0 && dy === 0 ? 0 : Math.atan2(dy, dx);
  }

  function drawArrow(x, y, label, color, isOutward) {
    if (isNaN(x) || isNaN(y)) return;
    ctx.save();
    ctx.fillStyle = color;
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.font = '12px Arial';
    const arrowLength = 20;
    const arrowHeadLength = 10;
    const arrowHeadWidth = 8;
    let baseAngle = isOutward ? futureDirection : pastDirection;
    const shaftEndX = x + Math.cos(baseAngle) * arrowLength;
    const shaftEndY = y + Math.sin(baseAngle) * arrowLength;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(shaftEndX, shaftEndY);
    ctx.stroke();
    const headX1 = shaftEndX - Math.cos(baseAngle) * arrowHeadLength;
    const headY1 = shaftEndY - Math.sin(baseAngle) * arrowHeadLength;
    const headX2 = headX1 + Math.sin(baseAngle) * arrowHeadWidth;
    const headY2 = headY1 - Math.cos(baseAngle) * arrowHeadWidth;
    const headX3 = headX1 - Math.sin(baseAngle) * arrowHeadWidth;
    const headY3 = headY1 + Math.cos(baseAngle) * arrowHeadWidth;
    ctx.beginPath();
    ctx.moveTo(shaftEndX, shaftEndY);
    ctx.lineTo(headX2, headY2);
    ctx.lineTo(headX3, headY3);
    ctx.closePath();
    ctx.fill();
    const labelOffset = 15;
    const verticalOffset = 5;
    if (isOutward) {
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, shaftEndX - labelOffset, shaftEndY - verticalOffset);
    } else {
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, shaftEndX + labelOffset, shaftEndY + verticalOffset);
    }
    ctx.restore();
  }

  if (!isNaN(pastStartXY.x) && !isNaN(pastStartXY.y)) {
    drawArrow(pastStartXY.x, pastStartXY.y, '-1.5h', 'white', false);
  }
  if (!isNaN(futureEndXY.x) && !isNaN(futureEndXY.y)) {
    drawArrow(futureEndXY.x, futureEndXY.y, '+1.5h', 'yellow', true);
  }

  if (track.tle && track.tle.stale) {
    ctx.save();
    ctx.translate(canvasWidth / 2, canvasHeight / 2);
    ctx.rotate(-PI / 4);
    ctx.font = '60px Arial';
    ctx.fillStyle = 'rgba(255,0,0,0.3)';
    ctx.textAlign = 'center';
    ctx.fillText('STALE TLE DATA', 0, 0);
    ctx.restore();
  }

  updateInfoPanel(lat, lon, altKm, speedKps);
  updateTLEInfo();
}

function updateInfoPanel(lat, lon, altKm, speedKps) {
  if (isNaN(lat) || isNaN(lon) || isNaN(altKm) || isNaN(speedKps)) {
    console.warn("Invalid data for info panel:", { lat, lon, altKm, speedKps });
    return;
  }
  const latDir = lat >= 0 ? 'N' : 'S';
  const lonDir = lon >= 0 ? 'E' : 'W';
  const latAbs = Math.abs(lat).toFixed(1);
  const lonAbs = Math.abs(lon).toFixed(1);
  const speedKmh = speedKps * 3600;
  const alt = useMetric ? `${altKm.toFixed(0)} km` : `${(altKm * 0.621371).toFixed(0)} mi`;
  const speed = useMetric ? `${speedKmh.toFixed(0)} km/h` : `${(speedKmh * 0.621371).toFixed(0)} mph`;

  const now = new Date();
  const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const month = monthNames[now.getUTCMonth()];
  const day = String(now.getUTCDate()).padStart(2, '0');
  const hours = String(now.getUTCHours()).padStart(2, '0');
  const minutes = String(now.getUTCMinutes()).padStart(2, '0');
  const seconds = String(now.getUTCSeconds()).padStart(2, '0');
  const timeText = window.innerWidth <= 768
    ? `${hours}:${minutes}:${seconds} GMT`
    : `${month} ${day}, ${hours}:${minutes}:${seconds} GMT`;

  latEl.textContent = `${latAbs}° ${latDir}`;
  lonEl.textContent = `${lonAbs}° ${lonDir}`;
  altEl.textContent = alt;
  speedEl.textContent = speed;
  timeEl.textContent = timeText;
}

function updateTLEInfo() {
  const tle = track && track.tle;
  if (!tle || !tle.fetched_at) {
    tleInfo.textContent = "";
    return;
  }
  const format = {
    month: '2-digit',
    day: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true
  };
  const lastUpdate = new Date(tle.fetched_at).toLocaleString('en-US', format);
  const nextUpdate = new Date(tle.next_refresh).toLocaleString('en-US', format);
  tleInfo.textContent = `Orbital elements updated ${lastUpdate} · next refresh ${nextUpdate} · times local`;
}

let lastFrameTime = 0;
const FRAME_INTERVAL = 5000;

function startLoop() {
  function renderLoop(currentTime) {
    if (currentTime - lastFrameTime >= FRAME_INTERVAL) {
      drawEverything();
      lastFrameTime = currentTime;
    }
    requestAnimationFrame(renderLoop);
  }
  requestAnimationFrame(renderLoop);
}

function toggleFullScreen() {
  if (!document.fullscreenElement) {
    const el = document.documentElement;
    if (el.requestFullscreen) el.requestFullscreen();
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
    else if (el.msRequestFullscreen) el.msRequestFullscreen();
  } else {
    if (document.exitFullscreen) document.exitFullscreen();
    else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
    else if (document.msExitFullscreen) document.msExitFullscreen();
  }
}

export async function start() {
  try {
    updateCanvasSize();
    setupCanvasEvents();
    setupOtherEvents();
    waitmsg.style.display = 'block';
    await new Promise(res => loadImages(res));
    await loadTrack();
    while (!track) await new Promise(res => setTimeout(res, 500));
    initialRender();
    isInitialized = true;
    drawEverything();
    startLoop();
  } catch (err) {
    console.error("Init error:", err);
  }
}

let resizeTimeout;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimeout);
  resizeTimeout = setTimeout(() => {
    updateCanvasSize();
    drawEverything();
  }, 100);
});

document.addEventListener('fullscreenchange', () => {
  updateCanvasSize();
  drawEverything();
});
