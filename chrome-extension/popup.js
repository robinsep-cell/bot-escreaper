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
function setMsg(text, ok) {
  const el = $('loginMsg');
  el.className = ok ? 'ok' : 'err';
  el.textContent = text;
}

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

async function loginPassword(email, password) {
  const r = await fetch(SUPABASE_URL + '/auth/v1/token?grant_type=password', {
    method: 'POST',
    headers: { 'apikey': SUPABASE_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password })
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error_description || j.msg || j.error || ('HTTP ' + r.status));
  return j;
}
async function pedirOTP(email) {
  const r = await fetch(SUPABASE_URL + '/auth/v1/otp', {
    method: 'POST',
    headers: { 'apikey': SUPABASE_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, create_user: false })
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error_description || j.msg || j.error || ('HTTP ' + r.status));
  return j;
}
async function verificarOTP(email, token) {
  const r = await fetch(SUPABASE_URL + '/auth/v1/verify', {
    method: 'POST',
    headers: { 'apikey': SUPABASE_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, token, type: 'email' })
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

// === Tabs login ===
function setTab(modo) {
  setMsg('', true); $('loginMsg').textContent = '';
  if (modo === 'pass') {
    $('tabPass').classList.add('active'); $('tabOtp').classList.remove('active');
    $('passBlock').style.display = ''; $('otpBlock').style.display = 'none';
  } else {
    $('tabOtp').classList.add('active'); $('tabPass').classList.remove('active');
    $('passBlock').style.display = 'none'; $('otpBlock').style.display = '';
  }
}
$('tabPass').addEventListener('click', () => setTab('pass'));
$('tabOtp').addEventListener('click', () => setTab('otp'));

// === Login con contraseña ===
$('loginBtn').addEventListener('click', async () => {
  const email = $('loginEmail').value.trim();
  const pass  = $('loginPass').value;
  if (!email || !pass) { setMsg('Completa email y contraseña.', false); return; }
  $('loginBtn').disabled = true; $('loginBtn').textContent = 'Validando…';
  try {
    const sess = await loginPassword(email, pass);
    await setSession(sess);
    chrome.runtime.sendMessage({ action: 'session-changed' });
    showStatus(sess); loadStatus();
  } catch (err) {
    setMsg('❌ ' + err.message, false);
  } finally {
    $('loginBtn').disabled = false; $('loginBtn').textContent = 'Entrar';
  }
});
$('loginPass').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') $('loginBtn').click(); });

// === Login con OTP ===
$('otpSendBtn').addEventListener('click', async () => {
  const email = $('loginEmail').value.trim();
  if (!email) { setMsg('Pon tu email arriba.', false); return; }
  $('otpSendBtn').disabled = true; $('otpSendBtn').textContent = 'Enviando…';
  try {
    await pedirOTP(email);
    setMsg('✅ Código enviado a ' + email + '. Revisa tu bandeja.', true);
    $('otpLabel').style.display = ''; $('otpVerifyBtn').style.display = '';
    $('otpSendBtn').textContent = '📩 Reenviar código';
    setTimeout(() => $('loginOtp').focus(), 50);
  } catch (err) {
    setMsg('❌ ' + err.message, false);
    $('otpSendBtn').textContent = '📩 Enviarme código al email';
  } finally {
    $('otpSendBtn').disabled = false;
  }
});
$('otpVerifyBtn').addEventListener('click', async () => {
  const email = $('loginEmail').value.trim();
  const code  = $('loginOtp').value.trim();
  if (!email || !code) { setMsg('Falta email o código.', false); return; }
  $('otpVerifyBtn').disabled = true; $('otpVerifyBtn').textContent = 'Verificando…';
  try {
    const sess = await verificarOTP(email, code);
    await setSession(sess);
    chrome.runtime.sendMessage({ action: 'session-changed' });
    showStatus(sess); loadStatus();
  } catch (err) {
    setMsg('❌ ' + err.message, false);
  } finally {
    $('otpVerifyBtn').disabled = false; $('otpVerifyBtn').textContent = 'Verificar código';
  }
});
$('loginOtp').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') $('otpVerifyBtn').click(); });

// === Refresh manual ===
$('refresh').addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'refresh-now' }, () => {
    $('msg').textContent = 'Iniciando ciclo manual...';
    setTimeout(loadStatus, 800);
  });
});

// === Logout ===
$('logoutBtn').addEventListener('click', async () => {
  await clearSession();
  chrome.runtime.sendMessage({ action: 'session-changed' });
  showLogin();
});

(async function init() {
  const sess = await getSession();
  if (sess && sess.access_token) {
    showStatus(sess); loadStatus(); setInterval(loadStatus, 2500);
  } else {
    showLogin();
    setTimeout(() => $('loginEmail').focus(), 50);
  }
})();
