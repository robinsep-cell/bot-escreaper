function fmtDate(iso) {
  if (!iso) return 'nunca';
  try {
    const d = new Date(iso);
    return d.toLocaleString('es-CL', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch (e) { return iso; }
}

function loadStatus() {
  chrome.runtime.sendMessage({ action: 'get-status' }, (status) => {
    if (chrome.runtime.lastError || !status) {
      document.getElementById('estado').textContent = 'sin datos';
      return;
    }
    document.getElementById('estado').textContent = status.currentlyRunning ? '🔄 corriendo' : '✅ inactivo';
    document.getElementById('last').textContent = fmtDate(status.lastRun);
    document.getElementById('updated').textContent = (status.lastUpdated !== undefined ? status.lastUpdated : '—');
    document.getElementById('failed').textContent = (status.lastFailed !== undefined ? status.lastFailed : '—');
    document.getElementById('refresh').disabled = !!status.currentlyRunning;
    document.getElementById('msg').textContent = status.message || '';
  });
}

document.getElementById('refresh').addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'refresh-now' }, () => {
    document.getElementById('msg').textContent = 'Iniciando ciclo manual...';
    setTimeout(loadStatus, 800);
  });
});

loadStatus();
setInterval(loadStatus, 2500);
