const SUPABASE_URL = 'https://vlhoshlnkmsojeqejzwo.supabase.co';
const SUPABASE_KEY = 'sb_publishable_OIkK1MFmOv3GYy9tsDbvUA_Nx5kW2Q6';

function fmtDate(iso) {
  if (!iso) return 'nunca';
  try {
    const d = new Date(iso);
    return d.toLocaleString('es-CL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch (e) { return iso; }
}

function $(id) { return document.getElementById(id); }

function showLogin() {
  $('loginPanel').style.display = '';
  $('statusPanel').style.display = 'none';
}
function showStatus(session) {
  $('loginPanel').style.display = 'none';
  $('statusPanel').style.display = '';
  const email = session && session.user && session.user.email ? session.user.email : '';
  $('userEmail').textContent = email;
}

async function getSession() {
  const { avr_session } = await chrome.storage.local.get(['avr_session']);
  return avr_session || null;
}
async function setSession(s) { await chrome.storage.local.set({ avr_session: s }); }
async function clearSession() { await chrome.storage.local.remove(['avr_session']); }

async function login(email, password) {
  const r = await fetch(SUPABASE_URL + '/auth/v1/token?grant_type=password', {
    method: 'POST',
    headers: { 'apikey': SUPABASE_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password })
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error_description || j.msg || j.error || ('HTTP ' + r.status));
  return j;
}

function loadStatus() {
  chrome.runtime.sendMessage({ action: 'get-status' }, (status) => {
    if (chrome.runtime.lastError || !status) { $('estado').textContent = 'sin datos'; return; }
    $('estado').textContent = status.currentlyRunning ? '🔄 corriendo' : (status.needsLogin ? '🔐 falta login' : '✅ inactivo');
    $('last').textContent = fmtDate(status.lastRun);
    $('updated').textContent = (status.lastUpdated !== undefined ? status.lastUpdated : '—');
    $('failed').textContent = (status.lastFailed !== undefined ? status.lastFailed : '—');
    $('refresh').disabled = !!status.currentlyRunning;
    $('msg').textContent = status.message || '';
  });
}

$('refresh').addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'refresh-now' }, () => {
    $('msg').textContent = 'Iniciando ciclo manual...';
    setTimeout(loadStatus, 800);
  });
});

$('loginBtn').addEventListener('click', async () => {
  const email = $('loginEmail').value.trim();
  const pass  = $('loginPass').value;
  const errEl = $('loginErr');
  errEl.textContent = '';
  if (!email || !pass) { errEl.textContent = 'Completa email y contraseña.'; return; }
  $('loginBtn').disabled = true; $('loginBtn').textContent = 'Validando…';
  try {
    const sess = await login(email, pass);
    await setSession(sess);
    chrome.runtime.sendMessage({ action: 'session-changed' });
    showStatus(sess);
    loadStatus();
  } catch (err) {
    errEl.textContent = '❌ ' + err.message;
  } finally {
    $('loginBtn').disabled = false; $('loginBtn').textContent = 'Entrar';
  }
});

$('loginPass').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') $('loginBtn').click(); });

$('logoutBtn').addEventListener('click', async () => {
  await clearSession();
  chrome.runtime.sendMessage({ action: 'session-changed' });
  showLogin();
});

(async function init() {
  const sess = await getSession();
  if (sess && sess.access_token) {
    showStatus(sess);
    loadStatus();
    setInterval(loadStatus, 2500);
  } else {
    showLogin();
    setTimeout(() => $('loginEmail').focus(), 50);
  }
})();
