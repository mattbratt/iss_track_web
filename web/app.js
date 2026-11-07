/*
 * ISS Track (web): the visible passes drawer, the place picker and the pass
 * settings sheet. Labels, pills, formatters and the sky dome come from the
 * integration's shared module, served from the submodule under /vendor.
 *
 * Nothing here computes the orbit or decides what matches. The service runs
 * the integration's engine; this page draws what it returns.
 */
import * as S from '/vendor/iss-track-shared.js';
import * as map from '/map.js';

const sharedStyle = document.createElement('style');
sharedStyle.textContent = S.SHARED_CSS;
document.head.append(sharedStyle);

const $ = (id) => document.getElementById(id);

const KEYS = {
  observer: 'iss_track_web.observer',
  recent: 'iss_track_web.recent',
  settings: 'iss_track_web.settings',
  metric: 'iss_track_web.metric',
  open: 'iss_track_web.passes_open',
  remind: 'iss_track_web.remind',
};
const REMIND_CHOICES = [5, 10, 15, 30];
const remember = {
  get(key, fallback) {
    try {
      const raw = window.localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (err) {
      return fallback;
    }
  },
  set(key, value) {
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (err) { /* private mode */ }
  },
};

const PASSES_REFRESH_MS = 5 * 60 * 1000;
const PASSES_RETRY_MS = 15 * 1000;
const RECENT_LIMIT = 5;
const SEARCH_DELAY_MS = 300;
const REASON = {
  quality: (p) => `${S.QUALITY[p.quality].label} is off`,
  brightness: () => 'Too faint',
  period: (p) => (p.period === 'morning' ? 'Morning is off' : 'Evening is off'),
};
const DEFAULT_SETTINGS = {
  qualities: ['good', 'excellent'],
  faintest_magnitude: -2,
  evening: true,
  morning: false,
  sky_limit: 'partly_cloudy',
};
const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const IMPERIAL_LOCALES = ['en-US', 'en-LR', 'my-MM'];

// ----- remembered state ---------------------------------------------------

function cleanSettings(raw) {
  const s = { ...DEFAULT_SETTINGS, ...(raw && typeof raw === 'object' ? raw : {}) };
  const qualities = Array.isArray(s.qualities) ? s.qualities.filter((q) => S.QUALITY[q]) : [];
  s.qualities = qualities.length ? qualities : [...DEFAULT_SETTINGS.qualities];
  const magnitude = Number(s.faintest_magnitude);
  s.faintest_magnitude = Number.isFinite(magnitude)
    ? Math.min(S.MAGNITUDE_FAINTEST, Math.max(S.MAGNITUDE_BRIGHTEST, magnitude))
    : DEFAULT_SETTINGS.faintest_magnitude;
  s.evening = Boolean(s.evening);
  s.morning = Boolean(s.morning);
  if (!s.evening && !s.morning) s.evening = true;
  if (!S.SKY_LIMITS.some(([key]) => key === s.sky_limit)) s.sky_limit = DEFAULT_SETTINGS.sky_limit;
  return s;
}

function cleanObserver(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const latitude = Number(raw.latitude);
  const longitude = Number(raw.longitude);
  if (!(latitude >= -90 && latitude <= 90 && longitude >= -180 && longitude <= 180)) return null;
  return {
    kind: raw.kind === 'here' ? 'here' : 'place',
    name: String(raw.name || (raw.kind === 'here' ? 'Your location' : 'A place')),
    region: String(raw.region || ''),
    country: String(raw.country || ''),
    latitude: Number(latitude.toFixed(2)),
    longitude: Number(longitude.toFixed(2)),
    timezone: String(raw.timezone || browserZone),
  };
}

let observer = cleanObserver(remember.get(KEYS.observer, null));
let recent = (remember.get(KEYS.recent, []) || []).map(cleanObserver).filter(Boolean).slice(0, RECENT_LIMIT);
let settings = cleanSettings(remember.get(KEYS.settings, null));
let metric = remember.get(KEYS.metric, !IMPERIAL_LOCALES.includes(navigator.language));
let open = remember.get(KEYS.open, true) !== false;
let remind = REMIND_CHOICES.includes(remember.get(KEYS.remind, 10)) ? remember.get(KEYS.remind, 10) : 10;

let data = null;
let failure = null;
let loadSeq = 0;
let retryTimer = null;
let signature = '';
let elementsSeen = null;

// The shared module formats times for a Home Assistant locale and zone;
// here the zone is the place's own, and the language the browser's.
const hassLike = () => ({
  config: { time_zone: (observer && observer.timezone) || browserZone },
  locale: { language: navigator.language },
});

function placeLabel(who) {
  if (!who) return 'Choose a place';
  if (who.kind === 'here') return 'Your location';
  return who.region ? `${who.name}, ${who.region}` : who.name;
}

// ----- passes -------------------------------------------------------------

function stateOf(now) {
  if (!observer) return { kind: 'nowhere' };
  if (!data) return { kind: failure ? 'error' : 'loading' };
  const next = S.nextMatching(data.passes, now);
  if (!next) return { kind: 'none' };
  return { kind: S.isLive(next, now) ? 'live' : 'next', pass: next };
}

// Only the newest request is applied, so a slow answer for one place can
// never land under another place's name.
async function load() {
  if (!observer) return;
  const seq = ++loadSeq;
  window.clearTimeout(retryTimer);
  const params = new URLSearchParams({
    latitude: String(observer.latitude),
    longitude: String(observer.longitude),
    qualities: settings.qualities.join(','),
    faintest_magnitude: String(settings.faintest_magnitude),
    evening: String(settings.evening),
    morning: String(settings.morning),
    sky_limit: settings.sky_limit,
  });
  let result = null;
  let problem = null;
  try {
    const response = await fetch(`/api/passes?${params}`, { cache: 'no-store' });
    if (!response.ok) {
      let message = `the service answered ${response.status}`;
      try { message = (await response.json()).message || message; } catch (err) { /* not JSON */ }
      throw new Error(message);
    }
    result = await response.json();
  } catch (err) {
    problem = (err && err.message) || 'ISS Track is not ready';
  }
  if (seq !== loadSeq) return;
  if (problem) {
    failure = problem;
    retryTimer = window.setTimeout(load, PASSES_RETRY_MS);
  } else {
    data = result;
    failure = null;
  }
  render();
}

function statusPill(p) {
  if (!p.matches) {
    return S.pill('dim', REASON[p.reason](p), 'LEFT OUT: this pass is outside your pass settings.');
  }
  if (!p.alert) {
    return S.pill('dim', 'Clouds likely', 'CLOUDS LIKELY: the forecast for this pass is cloudier than your sky setting allows.');
  }
  return S.pill('on', 'Matches', 'MATCHES: this pass fits your pass settings.');
}

// The shared sky pill names a weather entity; here the forecast is Open-Meteo's.
function skyPill(p) {
  if (p.sky === 'unknown') return '';
  return S.pill(
    p.sky === 'cloudy' ? 'dim' : 'neutral',
    S.SKY[p.sky],
    'SKY FORECAST: hourly cloud cover from Open-Meteo for the time of this pass.',
  );
}

function renderHead(state, now) {
  $('passes').classList.toggle('closed', !open);
  $('ps-toggle').setAttribute('aria-expanded', String(open));
  let summary = '';
  if (state.kind === 'next') {
    const when = S.formatWhen(state.pass.start, hassLike(), now);
    summary = `Next: ${when.day} at ${when.time}, ${S.QUALITY[state.pass.quality].label}`;
  } else if (state.kind === 'live') {
    summary = 'A pass is visible now';
  } else if (state.kind === 'none') {
    summary = 'No matching passes for 10 days';
  }
  $('ps-summary').textContent = open ? '' : summary;
  let note = '';
  if (observer) {
    const where = observer.kind === 'here' ? 'Your location, rounded to about a kilometre' : placeLabel(observer);
    note = `${where} · times in ${observer.timezone}`;
  }
  $('ps-note').textContent = note;
  $('where-name').textContent = placeLabel(observer);
}

// Why the station crossing your sky right now, or shortly, is not a pass.
function unseenNow(now) {
  if (!data) return null;
  return S.nextUnseen(data.unseen, now);
}

function renderUnseen(now) {
  const box = $('ps-unseen');
  const item = unseenNow(now);
  box.hidden = !item;
  if (!item) {
    box.innerHTML = '';
    return;
  }
  const words = S.describeUnseen(item, hassLike(), now);
  box.innerHTML = `${S.EYE_OFF_SVG}<div class="ps-unseen-text"><b id="ps-unseen-lead">${S.escapeHtml(words.lead)}</b> `
    + `<span>${S.escapeHtml(words.reason)}</span></div>`
    + S.pill('neutral', 'Not visible', 'NOT VISIBLE: the station is above your horizon, but there is nothing to see, so this is not a pass.');
}

function signatureOf(state, now) {
  const item = unseenNow(now);
  const unseen = item ? `${item.start}|${Date.parse(item.start) <= now}` : '';
  return `${state.kind}|${state.pass ? state.pass.start : ''}|${unseen}`;
}

function renderHero(state, now) {
  const hero = $('ps-hero');
  hero.className = state.kind === 'live' ? 'live' : (state.kind === 'next' ? '' : 'plain');
  if (state.kind === 'nowhere') {
    hero.innerHTML = '<div><p class="ps-big">Where are you watching from?</p>'
      + '<p class="ps-sub">Passes depend on where you stand. Use your location, or search a town or city.</p>'
      + '<button id="ps-choose" class="ps-action" title="WATCHING FROM: choose a place.">Choose a place</button></div>';
    $('ps-choose').addEventListener('click', openWhere);
  } else if (state.kind === 'loading') {
    hero.innerHTML = '<p class="ps-sub" style="margin:0">Loading passes…</p>';
  } else if (state.kind === 'error') {
    hero.innerHTML = `<div><p class="ps-big">Passes aren't available</p><p class="ps-sub" style="margin:0">${S.escapeHtml(failure)}. Trying again shortly.</p></div>`;
  } else if (state.kind === 'none') {
    const other = S.bestOther(data.passes, now);
    let hint = 'There are no visible passes at all in that time.';
    if (other) {
      const when = S.formatWhen(other.start, hassLike(), now);
      hint = `The best one coming is ${S.QUALITY[other.quality].label}, ${Math.round(other.peak_elevation)}°, ${when.day} at ${when.time}.`;
    }
    hero.innerHTML = `<div><p class="ps-big">No matching passes for 10 days</p><p class="ps-sub">${S.escapeHtml(hint)}</p>`
      + '<button id="ps-adjust" class="ps-action" title="PASS SETTINGS: change which passes count.">Adjust pass settings</button></div>';
    $('ps-adjust').addEventListener('click', openSettings);
  } else if (state.kind === 'live') {
    const p = state.pass;
    hero.innerHTML = '<div><p class="ps-label">Visible now</p><p class="ps-big" id="ps-big"></p>'
      + '<p class="ps-sub" id="ps-sub"></p><div class="ps-bar"><div id="ps-bar" style="width:0%"></div></div>'
      + `<p class="ps-sub" style="margin:0"><b id="ps-left" style="color:var(--iss-text)"></b> · peaks at ${Math.round(p.peak_elevation)}°, ${S.escapeHtml(S.describePass(p).split(', ')[1])}</p></div>`
      + '<div id="ps-dome"></div>';
    updateLive(p, now);
  } else {
    const p = state.pass;
    const when = S.formatWhen(p.start, hassLike(), now);
    hero.innerHTML = '<div><p class="ps-label">Next pass that matches</p>'
      + `<p class="ps-big" id="ps-big">${S.escapeHtml(S.formatCountdown(Date.parse(p.start) - now))}</p>`
      + `<p class="ps-sub">${S.escapeHtml(`${when.day} at ${when.time}, visible for ${S.formatDuration(p.duration_seconds)}. ${S.describePass(p)}.`)}</p>`
      + `<div class="ps-pills">${S.qualityPill(p)}${S.magnitudePill(p)}${skyPill(p)}</div></div>`
      + `<div>${S.domeSvg(p)}</div>`;
  }
}

function updateLive(p, now) {
  const look = S.liveLook(p, now);
  if (!look || !$('ps-big')) return;
  $('ps-big').textContent = `Look ${look.direction}`;
  $('ps-sub').textContent = `${Math.round(look.elevation)}° above the horizon`;
  $('ps-bar').style.width = `${Math.round(look.progress * 100)}%`;
  $('ps-left').textContent = `${S.formatDuration(look.secondsLeft)} left`;
  $('ps-dome').innerHTML = S.domeSvg(p, look.progress);
}

function renderList(now) {
  const list = $('ps-list');
  const count = $('ps-count');
  if (!data) {
    list.innerHTML = '';
    count.textContent = '';
    return;
  }
  const rows = data.passes.filter((p) => Date.parse(p.end) >= now);
  const matching = rows.filter((p) => p.matches).length;
  count.textContent = rows.length
    ? `${matching} of ${rows.length} visible passes in the next 10 days match your settings`
    : 'No visible passes in the next 10 days';
  list.innerHTML = rows.map((p) => {
    const when = S.formatWhen(p.start, hassLike(), now);
    return `<div class="ps-row${p.matches ? '' : ' off'}">`
      + `<span class="ps-when" title="${S.escapeHtml(S.describePass(p))}">${S.escapeHtml(`${when.day} · ${when.time}`)}</span>`
      + `<span>${S.qualityPill(p, false)}</span>`
      + `<span class="ps-num" title="HIGHEST POINT: degrees above the horizon while visible.">${Math.round(p.peak_elevation)}°</span>`
      + `<span class="ps-num ps-mag" title="BRIGHTNESS: estimated magnitude. Lower is brighter.">${S.formatMagnitude(p.magnitude)}</span>`
      + `<span class="ps-sky">${skyPill(p)}</span>`
      + `<span class="ps-dir">${S.escapeHtml(`${p.start_direction} to ${p.end_direction}`)}</span>`
      + `<span class="ps-status">${statusPill(p)}</span></div>`;
  }).join('');
}

function pushOverlay(state, now) {
  const p = state.pass || null;
  const unseen = unseenNow(now);
  map.setOverlay({
    observer: observer ? { latitude: observer.latitude, longitude: observer.longitude } : null,
    pass: p ? { start: Date.parse(p.start), end: Date.parse(p.end) } : null,
    unseen: unseen ? { start: Date.parse(unseen.start), end: Date.parse(unseen.end) } : null,
  });
}

function render() {
  const now = Date.now();
  const state = stateOf(now);
  signature = signatureOf(state, now);
  $('passes').hidden = false;
  renderHead(state, now);
  renderUnseen(now);
  renderHero(state, now);
  renderList(now);
  pushOverlay(state, now);
}

function tick() {
  if (!data) return;
  const now = Date.now();
  const state = stateOf(now);
  if (signatureOf(state, now) !== signature) {
    render();
    return;
  }
  // While an unseen overflight is overhead, its height and direction move.
  const item = unseenNow(now);
  if (item && Date.parse(item.start) <= now && $('ps-unseen-lead')) {
    $('ps-unseen-lead').textContent = S.describeUnseen(item, hassLike(), now).lead;
  }
  if (state.kind === 'next' && $('ps-big')) {
    $('ps-big').textContent = S.formatCountdown(Date.parse(state.pass.start) - now);
  } else if (state.kind === 'live') {
    updateLive(state.pass, now);
  }
}

// ----- the place picker ---------------------------------------------------

const PIN_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-6-5.3-6-11a6 6 0 0 1 12 0c0 5.7-6 11-6 11z"/><circle cx="12" cy="10" r="2.5"/></svg>';
const HISTORY_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 7v5l3 2"/></svg>';
let searchTimer = null;
let searchSeq = 0;

function coords(who) {
  const lat = `${Math.abs(who.latitude).toFixed(2)}° ${who.latitude >= 0 ? 'N' : 'S'}`;
  const lon = `${Math.abs(who.longitude).toFixed(2)}° ${who.longitude >= 0 ? 'E' : 'W'}`;
  return `${lat}, ${lon}`;
}

function placeRow(who, icon, index, forget) {
  const detail = [who.region, who.country].filter(Boolean).join(', ');
  const current = observer && observer.kind === 'place'
    && observer.latitude === who.latitude && observer.longitude === who.longitude;
  return `<button class="where-row" data-index="${index}" title="${S.escapeHtml(`${who.name}: predict passes for this place.`)}">${icon}`
    + `<span class="where-text">${S.escapeHtml(who.name)} <span>${S.escapeHtml(detail)}</span></span>`
    + (current ? S.pill('on', 'Watching from here', 'WATCHING FROM HERE: the place passes are predicted for now.') : `<span class="where-coords">${coords(who)}</span>`)
    + (forget ? `<span class="where-forget" data-forget="${index}" role="button" aria-label="Forget this place" title="FORGET: remove this place from the recent list.">×</span>` : '')
    + '</button>';
}

function renderRecent() {
  $('where-recent-title').hidden = !recent.length;
  $('where-recent').innerHTML = recent.map((who, i) => placeRow(who, HISTORY_SVG, i, true)).join('');
}

let results = [];

function renderResults(status) {
  $('where-results').innerHTML = results.map((who, i) => placeRow(who, PIN_SVG, i, false)).join('')
    + (status ? `<p class="ps-note" style="margin:8px 4px">${S.escapeHtml(status)}</p>` : '');
}

async function search(text) {
  const seq = ++searchSeq;
  if (text.length < 2) {
    results = [];
    renderResults('');
    return;
  }
  try {
    const response = await fetch(`/api/places?q=${encodeURIComponent(text)}`);
    if (!response.ok) {
      let message = 'Place search is not available right now.';
      try { message = (await response.json()).message || message; } catch (err) { /* not JSON */ }
      throw new Error(message);
    }
    const found = (await response.json()).places || [];
    if (seq !== searchSeq) return;
    results = found.map((row) => cleanObserver({ ...row, kind: 'place' })).filter(Boolean);
    renderResults(results.length ? '' : `No place called "${text}" found.`);
  } catch (err) {
    if (seq !== searchSeq) return;
    results = [];
    renderResults((err && err.message) || 'Place search is not available right now.');
  }
}

function setWhereStatus(text, bad = false) {
  const status = $('where-status');
  status.textContent = text;
  status.classList.toggle('bad', bad);
}

function browserPosition() {
  return new Promise((resolve, reject) => {
    if (!window.isSecureContext || !navigator.geolocation) {
      reject(new Error('insecure'));
      return;
    }
    // Some browsers never call back when the prompt is dismissed.
    const giveUp = window.setTimeout(() => reject(new Error('denied')), 15000);
    navigator.geolocation.getCurrentPosition(
      (position) => { window.clearTimeout(giveUp); resolve(position.coords); },
      () => { window.clearTimeout(giveUp); reject(new Error('denied')); },
      { timeout: 10000, maximumAge: 600000 },
    );
  });
}

async function useHere() {
  setWhereStatus('Asking the browser for your position…');
  try {
    const where = await browserPosition();
    chooseObserver(cleanObserver({
      kind: 'here', name: 'Your location', latitude: where.latitude, longitude: where.longitude, timezone: browserZone,
    }));
  } catch (err) {
    if (err.message === 'insecure') {
      setWhereStatus("This page isn't served over HTTPS, so the browser won't share a location. Search a place instead.", true);
    } else {
      setWhereStatus('The location request was declined. Search a place instead.', true);
    }
  }
}

function chooseObserver(who) {
  if (!who) return;
  observer = who;
  remember.set(KEYS.observer, observer);
  if (who.kind === 'place') {
    recent = [who, ...recent.filter((r) => r.latitude !== who.latitude || r.longitude !== who.longitude)].slice(0, RECENT_LIMIT);
    remember.set(KEYS.recent, recent);
  }
  data = null;
  failure = null;
  closeWhere();
  render();
  load();
}

function openWhere() {
  setWhereStatus('Your position is rounded to about a kilometre before it leaves this browser, and nothing is stored on the server.');
  $('where-input').value = '';
  results = [];
  renderResults('');
  renderRecent();
  $('whereModal').style.display = 'flex';
  $('where-input').focus();
}

function closeWhere() {
  $('whereModal').style.display = 'none';
}

// ----- pass settings sheet ------------------------------------------------

function sheetHtml() {
  const s = settings;
  const qualities = S.QUALITY_ORDER.map((key) => {
    const q = S.QUALITY[key];
    return `<label class="ps-q"><input type="checkbox" data-quality="${key}"${s.qualities.includes(key) ? ' checked' : ''}/>`
      + `${S.pill(key, q.label)}<span>${q.range}</span><span>${q.hint}</span></label>`;
  }).join('');
  const sky = S.SKY_LIMITS.map(([key, label]) => (
    `<button data-sky="${key}" class="${s.sky_limit === key ? 'on' : ''}">${label}</button>`
  )).join('');
  const slider = Math.round((S.MAGNITUDE_FAINTEST - s.faintest_magnitude) / S.MAGNITUDE_STEP);
  const steps = Math.round((S.MAGNITUDE_FAINTEST - S.MAGNITUDE_BRIGHTEST) / S.MAGNITUDE_STEP);
  return '<div class="ps-sheet-head"><h2 id="ps-sheet-title">Pass settings</h2>'
    + `${S.pill('neutral', 'Saved in this browser', 'SAVED HERE: settings live in this browser and are sent with each request.')}`
    + '<button id="ps-close" class="ps-icon" aria-label="Close" title="CLOSE: changes are saved as you make them.">×</button></div>'
    + '<p class="ps-h" style="margin-top:0">Pass quality to include</p>' + qualities
    + '<p class="ps-h">Minimum brightness</p>'
    + `<div class="ps-slider"><span>Dimmer</span><input type="range" id="ps-mag" min="0" max="${steps}" step="1" value="${slider}" aria-label="Minimum brightness" title="MINIMUM BRIGHTNESS: leave out passes fainter than this."/><span>Brighter</span></div>`
    + '<p style="font-size:0.85rem;margin:8px 0 0"><b id="ps-mag-out"></b> <span id="ps-mag-hint" style="color:var(--iss-text2)"></span></p>'
    + '<p class="ps-note" style="margin:8px 0 0">Magnitude runs backwards: a lower number is brighter. Brightness is an estimate.</p>'
    + '<div class="ps-two"><div><p class="ps-h">Sky forecast</p>'
    + '<p class="ps-source" title="SKY FORECAST: hourly cloud cover from Open-Meteo for the place you are watching from.">'
    + '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 18a4 4 0 0 1-.6-8 6 6 0 0 1 11.4 2H18a3 3 0 0 1 0 6H7z"/></svg>'
    + 'Hourly cloud cover from Open-Meteo for the place you are watching from</p>'
    + '<p class="ps-note" style="margin:10px 0 6px">Count the sky as good enough when it is</p>'
    + `<div class="ps-seg" id="ps-sky" title="SKY LIMIT: a pass under a worse forecast is marked Clouds likely. A pass with no forecast is never marked.">${sky}</div></div>`
    + '<div><p class="ps-h">Time of day</p>'
    + `<label class="ps-check"><input type="checkbox" id="ps-evening"${s.evening ? ' checked' : ''}/>Evening passes, after sunset</label>`
    + `<label class="ps-check"><input type="checkbox" id="ps-morning"${s.morning ? ' checked' : ''}/>Morning passes, before sunrise</label>`
    + '<p class="ps-note" style="margin:6px 0 0">About half of all visible passes happen before dawn.</p></div></div>'
    + '<p id="ps-status">Changes are saved as you make them.</p>';
}

function showMagnitude() {
  const limit = S.MAGNITUDE_FAINTEST - Number($('ps-mag').value) * S.MAGNITUDE_STEP;
  $('ps-mag-out').textContent = `Magnitude ${S.formatMagnitude(limit)} or brighter`;
  $('ps-mag-hint').textContent = `· ${S.magnitudeHint(limit)}`;
  return limit;
}

function setStatus(text, bad = false) {
  const status = $('ps-status');
  if (!status) return;
  status.textContent = text;
  status.classList.toggle('bad', bad);
}

function save(patch) {
  settings = cleanSettings({ ...settings, ...patch });
  remember.set(KEYS.settings, settings);
  setStatus('Saved');
  load();
}

function buildSheet() {
  $('ps-sheet').innerHTML = sheetHtml();
  showMagnitude();
  $('ps-close').addEventListener('click', closeSettings);
  for (const box of $('ps-sheet').querySelectorAll('input[data-quality]')) {
    box.addEventListener('change', () => {
      const chosen = [...$('ps-sheet').querySelectorAll('input[data-quality]:checked')].map((b) => b.dataset.quality);
      if (!chosen.length) {
        box.checked = true;
        setStatus('Keep at least one pass quality.', true);
        return;
      }
      save({ qualities: chosen });
    });
  }
  $('ps-mag').addEventListener('input', showMagnitude);
  $('ps-mag').addEventListener('change', () => save({ faintest_magnitude: showMagnitude() }));
  $('ps-sky').addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    for (const other of $('ps-sky').querySelectorAll('button')) other.classList.toggle('on', other === button);
    save({ sky_limit: button.dataset.sky });
  });
  for (const id of ['ps-evening', 'ps-morning']) {
    $(id).addEventListener('change', (event) => {
      if (!$('ps-evening').checked && !$('ps-morning').checked) {
        event.target.checked = true;
        setStatus('Keep evening passes, morning passes, or both.', true);
        return;
      }
      save({ evening: $('ps-evening').checked, morning: $('ps-morning').checked });
    });
  }
}

function openSettings() {
  buildSheet();
  $('settingsModal').style.display = 'flex';
  $('ps-close').focus();
}

function closeSettings() {
  $('settingsModal').style.display = 'none';
  $('gear').focus();
}

// ----- the reminder sheet: the matching passes into a person's calendar ------
//
// The sheet decides by device and puts the one-step route on top. A desktop
// browser downloads the calendar file, which the person's calendar imports in
// one go. An iPhone subscribes in Apple Calendar with one tap. Android goes
// to Google Calendar's add-by-address page. Google Calendar and Outlook.com
// have that page on every device, so they are one step everywhere. The feed
// address and the webcal link stay under a fold for people who want them.

const CALENDAR_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>';
const DOWNLOAD_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10l5 5 5-5M4 19h16"/></svg>';
const COPY_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>';
const LINK_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>';

/* Which kind of device this is, for the order of the buttons. An iPad with a
 * desktop user agent still reports touch points, which is how it is told from
 * a Mac. */
function device() {
  const ua = navigator.userAgent || '';
  const touchMac = navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1;
  if (/iPhone|iPad|iPod/.test(ua) || touchMac) return 'ios';
  if (/Android/.test(ua)) return 'android';
  return 'desktop';
}

function joinWords(words) {
  if (words.length <= 1) return words.join('');
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

/* The settings in a few words, for the sheet's "For" line. */
function settingsSummary() {
  const qualities = joinWords(S.QUALITY_ORDER.filter((q) => settings.qualities.includes(q)).map((q) => S.QUALITY[q].label));
  const when = settings.evening && settings.morning ? 'evenings and mornings' : settings.morning ? 'mornings' : 'evenings';
  return `${qualities} · magnitude ${S.formatMagnitude(settings.faintest_magnitude)} or brighter · ${when}`;
}

function feedUrl(extra = {}) {
  const params = new URLSearchParams({
    latitude: String(observer.latitude),
    longitude: String(observer.longitude),
    name: placeLabel(observer),
    qualities: settings.qualities.join(','),
    faintest_magnitude: String(settings.faintest_magnitude),
    evening: String(settings.evening),
    morning: String(settings.morning),
    sky_limit: settings.sky_limit,
    remind: String(remind),
    ...extra,
  });
  return `${window.location.origin}/calendar.ics?${params}`;
}

/* Every way into a calendar, each a link. Google and Outlook.com have a page
 * that takes a feed address and asks to add it; the webcal link is the same
 * feed for Apple Calendar and desktop Outlook; the download is the feed as a
 * file, which the person opens and their calendar imports. */
function calendarRoutes() {
  const feed = feedUrl();
  const encoded = encodeURIComponent(feed);
  const title = encodeURIComponent(`ISS passes, ${placeLabel(observer)}`);
  return {
    feed,
    webcal: feed.replace(/^https?:/, 'webcal:'),
    download: feedUrl({ download: '1' }),
    google: `https://calendar.google.com/calendar/render?cid=${encoded}`,
    outlook: `https://outlook.live.com/calendar/0/addfromweb?url=${encoded}&name=${title}`,
    office: `https://outlook.office.com/calendar/0/addfromweb?url=${encoded}&name=${title}`,
  };
}

function matchingCount() {
  if (!data) return 0;
  const now = Date.now();
  return data.passes.filter((p) => p.matches && Date.parse(p.end) >= now).length;
}

function passesWord(count) {
  return count === 1 ? '1 pass' : `${count} passes`;
}

/* One link styled as a button. `primary` makes it the big one; `blank` opens
 * it in a new tab, for the Google and Outlook pages; `save` asks the browser
 * to download rather than show. */
function routeButton(id, href, icon, label, title, { primary = false, blank = false, save = false } = {}) {
  const extra = (blank ? ' target="_blank" rel="noopener"' : '') + (save ? ' download' : '');
  return `<a id="${id}" class="remind-btn${primary ? ' primary' : ''}" href="${S.escapeHtml(href)}"${extra} title="${S.escapeHtml(title)}">${icon}<span>${S.escapeHtml(label)}</span></a>`;
}

/* The buttons for this device: the one-step route first, then the other two
 * that work here, and a hint under the first. */
function routesHtml(routes, kind, count) {
  const google = routeButton('remind-google', routes.google, LINK_SVG, 'Google Calendar',
    'GOOGLE CALENDAR: opens Google Calendar, which asks to add this feed. It keeps itself up to date.', { blank: true });
  const outlook = routeButton('remind-outlook', routes.outlook, LINK_SVG, 'Outlook.com',
    'OUTLOOK.COM: opens Outlook on the web, which asks to add this feed. It keeps itself up to date.', { blank: true });
  const fileHint = `Saves a file with the next 10 days. Open it and your calendar adds ${count ? `all ${passesWord(count)}` : 'the passes'}, each with its reminder. The file does not update itself, so come back for more.`;
  if (kind === 'ios') {
    return routeButton('remind-apple', routes.webcal, CALENDAR_SVG, 'Add to Apple Calendar',
      'ADD TO APPLE CALENDAR: one tap, then Subscribe. The calendar keeps itself up to date.', { primary: true })
      + '<p class="remind-hint">One tap, then Subscribe. Updates itself, reminders included.</p>'
      + `<div class="remind-grid">${google}${routeButton('remind-file', routes.download, DOWNLOAD_SVG, 'Calendar file',
        `CALENDAR FILE: ${fileHint}`)}</div>`;
  }
  if (kind === 'android') {
    return routeButton('remind-google', routes.google, LINK_SVG, 'Add to Google Calendar',
      'ADD TO GOOGLE CALENDAR: opens Google Calendar, which asks to add this feed. It keeps itself up to date.', { primary: true, blank: true })
      + '<p class="remind-hint">Google asks once, then keeps the passes up to date.</p>'
      + `<div class="remind-grid">${routeButton('remind-file', routes.download, DOWNLOAD_SVG, 'Calendar file',
        `CALENDAR FILE: ${fileHint}`, { save: true })}${outlook}</div>`;
  }
  return routeButton('remind-file', routes.download, DOWNLOAD_SVG, 'Download calendar file',
    `DOWNLOAD CALENDAR FILE: ${fileHint}`, { primary: true, save: true })
    + `<p class="remind-hint">${S.escapeHtml(fileHint)}</p>`
    + `<div class="remind-grid">${google}${outlook}</div>`
    + '<p class="remind-hint">Those two subscribe in one step and keep themselves up to date.</p>';
}

/* The fold: the feed itself, for calendars that take an address. */
function moreHtml(routes, kind) {
  const webcal = kind === 'android' ? '' : routeButton('remind-webcal', routes.webcal, CALENDAR_SVG,
    kind === 'ios' ? 'Subscribe again' : 'Subscribe in my calendar app',
    'SUBSCRIBE: hands the feed to Apple Calendar or Outlook on this computer, which asks to subscribe.');
  const office = `<a href="${S.escapeHtml(routes.office)}" target="_blank" rel="noopener" title="MICROSOFT 365: for a work or school Outlook account.">Microsoft 365 work account</a>`;
  return '<details class="remind-more">'
    + '<summary title="MORE WAYS: the feed address, for any calendar that subscribes by address.">More ways to add it</summary>'
    + '<p class="remind-note">This is a calendar feed. Any calendar that subscribes by address can use it, and it refreshes itself every few hours.</p>'
    + `<div class="remind-actions">${webcal}`
    + `<button id="remind-copy" class="remind-btn" title="COPY ADDRESS: for any calendar that asks for a URL.">${COPY_SVG}<span>Copy address</span></button>`
    + '<span id="remind-copied"></span></div>'
    + `<div class="remind-addr" id="remind-addr" title="FEED ADDRESS: paste this where your calendar asks for a URL.">${S.escapeHtml(routes.feed)}</div>`
    + `<p class="remind-note">${office}. The address holds this place and these settings; after changing them, add it again. The forecast in each event is the one from when your calendar last fetched the feed, and a pass is never dropped for weather.</p>`
    + '</details>';
}

function remindHtml() {
  const head = '<div class="ps-sheet-head"><h2 id="remind-title">Remind me</h2>'
    + '<button id="remind-close" class="ps-icon" aria-label="Close" title="CLOSE">×</button></div>';
  if (!observer) {
    return head
      + '<p class="remind-lead">Your calendar can remind you before every pass that matches your settings. Choose where you are watching from first.</p>'
      + '<p style="margin:14px 0 0"><button id="remind-choose" class="ps-action" title="WATCHING FROM: choose a place.">Choose a place</button></p>';
  }
  const routes = calendarRoutes();
  const kind = device();
  const count = matchingCount();
  const choices = REMIND_CHOICES.map((minutes) => (
    `<button data-remind="${minutes}" class="${minutes === remind ? 'on' : ''}">${minutes}</button>`
  )).join('');
  return head
    + `<p class="remind-lead">Add the next 10 days of passes that match your settings to your calendar. Each one carries its own reminder.</p>`
    + `<p class="remind-for">${S.escapeHtml(placeLabel(observer))} <span>· ${S.escapeHtml(settingsSummary())}</span></p>`
    + '<div class="remind-when">'
    + '<span class="ps-h">Remind me</span>'
    + `<div class="ps-seg" id="remind-lead" title="LEAD TIME: how long before the pass starts the reminder goes off.">${choices}</div>`
    + '<span class="ps-h">min before</span></div>'
    + `<div class="remind-routes">${routesHtml(routes, kind, count)}</div>`
    + moreHtml(routes, kind);
}

async function copyFeed() {
  const text = feedUrl();
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch (err) {
    const range = document.createRange();
    range.selectNodeContents($('remind-addr'));
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    try { copied = document.execCommand('copy'); } catch (err2) { copied = false; }
  }
  $('remind-copied').innerHTML = copied
    ? S.pill('on', 'Copied', 'COPIED: the address is on your clipboard.')
    : S.pill('dim', 'Select and copy the address below', 'COPY FAILED: this browser would not copy on its own.');
}

function buildRemind() {
  const wasOpen = $('remind-sheet').querySelector('details')?.open || false;
  $('remind-sheet').innerHTML = remindHtml();
  $('remind-close').addEventListener('click', closeRemind);
  if (!observer) {
    $('remind-choose').addEventListener('click', () => { closeRemind(); openWhere(); });
    return;
  }
  $('remind-lead').addEventListener('click', (event) => {
    const button = event.target.closest('button[data-remind]');
    if (!button) return;
    remind = Number(button.dataset.remind);
    remember.set(KEYS.remind, remind);
    buildRemind();
  });
  $('remind-copy').addEventListener('click', copyFeed);
  if (wasOpen) $('remind-sheet').querySelector('details').open = true;
}

function openRemind() {
  buildRemind();
  $('remindModal').style.display = 'flex';
  $('remind-close').focus();
}

function closeRemind() {
  $('remindModal').style.display = 'none';
  $('bell').focus();
}

// ----- units and the age of the elements ----------------------------------

function applyUnits() {
  $('units').textContent = metric ? 'km' : 'mi';
  $('units').title = metric
    ? 'UNITS: showing kilometres. Click for miles.'
    : 'UNITS: showing miles. Click for kilometres.';
  map.setUnits(metric);
}

function elementsAge(track) {
  const pill = $('tle-pill');
  const tle = track && track.tle;
  if (!tle || !tle.fetched_at) {
    pill.hidden = true;
    return;
  }
  const hours = Math.floor((Date.now() - tle.fetched_at) / 3600000);
  const age = hours < 1 ? 'fresh' : hours < 24 ? `${hours} h old` : `${Math.floor(hours / 24)} d old`;
  pill.hidden = false;
  pill.className = `iss-pill ${tle.stale ? 'warn' : 'on'}`;
  pill.textContent = `Elements ${age}`;
  pill.title = tle.stale
    ? 'OUT OF DATE: the orbital elements have not refreshed for more than three days, so positions and passes drift.'
    : 'ORBITAL ELEMENTS: fetched from Celestrak every two hours. Positions and passes come from this set.';
  // New elements mean new passes.
  if (elementsSeen !== null && elementsSeen !== tle.fetched_at && data) load();
  elementsSeen = tle.fetched_at;
}

// ----- wiring -------------------------------------------------------------

$('ps-toggle').addEventListener('click', () => {
  open = !open;
  remember.set(KEYS.open, open);
  render();
});
$('gear').addEventListener('click', openSettings);
$('bell').addEventListener('click', openRemind);
$('units').addEventListener('click', () => {
  metric = !metric;
  remember.set(KEYS.metric, metric);
  applyUnits();
});
$('where-chip').addEventListener('click', openWhere);
$('where-close').addEventListener('click', closeWhere);
$('where-here').addEventListener('click', useHere);
$('where-input').addEventListener('input', (event) => {
  window.clearTimeout(searchTimer);
  const text = event.target.value.trim();
  searchTimer = window.setTimeout(() => search(text), SEARCH_DELAY_MS);
});
$('where-results').addEventListener('click', (event) => {
  const row = event.target.closest('.where-row');
  if (row) chooseObserver(results[Number(row.dataset.index)]);
});
$('where-recent').addEventListener('click', (event) => {
  const forget = event.target.closest('[data-forget]');
  if (forget) {
    event.stopPropagation();
    recent.splice(Number(forget.dataset.forget), 1);
    remember.set(KEYS.recent, recent);
    renderRecent();
    return;
  }
  const row = event.target.closest('.where-row');
  if (row) chooseObserver(recent[Number(row.dataset.index)]);
});
for (const [modal, close] of [['settingsModal', closeSettings], ['whereModal', closeWhere], ['remindModal', closeRemind]]) {
  $(modal).addEventListener('click', (event) => {
    if (event.target === $(modal)) close();
  });
}
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if ($('settingsModal').style.display === 'flex') closeSettings();
  if ($('whereModal').style.display === 'flex') closeWhere();
  if ($('remindModal').style.display === 'flex') closeRemind();
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && data) load();
});

applyUnits();
render();
map.onTrack(elementsAge);
map.start();
if (observer) load();
else openWhere();
window.setInterval(tick, 1000);
window.setInterval(load, PASSES_REFRESH_MS);
