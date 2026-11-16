/* 公共工具：无外部依赖，可离线运行 */
window.WO_TOKEN_KEY = window.WO_TOKEN_KEY || 'wo_token';

const STATUS_META = {
  pending:        { name: '待受理', color: '#b45309', bg: '#fef3c7' },
  assigned:       { name: '已派单', color: '#1d4ed8', bg: '#dbeafe' },
  processing:     { name: '处理中', color: '#6d28d9', bg: '#ede9fe' },
  pending_accept: { name: '待验收', color: '#a16207', bg: '#fef9c3' },
  completed:      { name: '已完成', color: '#15803d', bg: '#dcfce7' },
  closed:         { name: '已关闭', color: '#4b5563', bg: '#e5e7eb' }
};

function getToken() { return localStorage.getItem(window.WO_TOKEN_KEY) || ''; }
function setToken(t) { t ? localStorage.setItem(window.WO_TOKEN_KEY, t) : localStorage.removeItem(window.WO_TOKEN_KEY); }
function clearToken() { localStorage.removeItem(window.WO_TOKEN_KEY); }

async function api(method, path, data) {
  const headers = { 'Content-Type': 'application/json', 'X-Token': getToken() };
  const opt = { method, headers, cache: 'no-store' };
  if (data !== undefined && data !== null) opt.body = JSON.stringify(data);
  let res;
  try {
    res = await fetch(path, opt);
  } catch (e) {
    throw new Error('无法连接服务器，请确认服务已启动或检查网络');
  }
  const ctype = res.headers.get('content-type') || '';
  if (ctype.indexOf('csv') >= 0) {
    if (!res.ok) throw new Error('导出失败');
    return await res.blob();
  }
  let payload = {};
  try { payload = await res.json(); } catch (e) { payload = {}; }
  if (!res.ok) {
    if (res.status === 401) clearToken();
    throw new Error(payload.error || ('请求失败 (' + res.status + ')'));
  }
  return payload;
}

function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtTime(s) {
  if (!s) return '—';
  return String(s).replace(/^\d{4}-/, '').slice(0, 14);
}

function fmtMin(m) {
  m = Number(m) || 0;
  if (m < 60) return Math.round(m) + ' 分钟';
  if (m < 1440) return (m / 60).toFixed(1) + ' 小时';
  return (m / 1440).toFixed(1) + ' 天';
}

function statusBadge(status) {
  const m = STATUS_META[status] || { name: status, color: '#4b5563', bg: '#e5e7eb' };
  return '<span class="badge" style="color:' + m.color + ';background:' + m.bg + '">' + esc(m.name) + '</span>';
}

let toastTimer = null;
function toast(msg, type) {
  let el = document.getElementById('wo-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'wo-toast';
    document.body.appendChild(el);
  }
  el.className = 'wo-toast show' + (type === 'error' ? ' error' : '');
  el.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.className = 'wo-toast'; }, 3200);
}

function compressImage(file, maxSide, quality) {
  maxSide = maxSide || 1600;
  quality = quality || 0.78;
  return new Promise(function (resolve, reject) {
    if (!/^image\//.test(file.type)) { reject(new Error('只能选择图片文件')); return; }
    if (file.size > 20 * 1024 * 1024) { reject(new Error('图片过大，请换一张')); return; }
    const reader = new FileReader();
    reader.onerror = function () { reject(new Error('读取图片失败')); };
    reader.onload = function () {
      const img = new Image();
      img.onerror = function () { reject(new Error('图片解析失败')); };
      img.onload = function () {
        let w = img.naturalWidth, h = img.naturalHeight;
        const scale = Math.min(1, maxSide / Math.max(w, h));
        w = Math.max(1, Math.round(w * scale));
        h = Math.max(1, Math.round(h * scale));
        const cv = document.createElement('canvas');
        cv.width = w; cv.height = h;
        const ctx = cv.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, w, h);
        ctx.drawImage(img, 0, 0, w, h);
        resolve(cv.toDataURL('image/jpeg', quality));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 500);
}

function openSheet(html) {
  let wrap = document.getElementById('wo-sheet');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'wo-sheet';
    wrap.className = 'sheet-wrap';
    wrap.innerHTML = '<div class="sheet-mask" onclick="closeSheet()"></div><div class="sheet" id="wo-sheet-body"></div>';
    document.body.appendChild(wrap);
  }
  document.getElementById('wo-sheet-body').innerHTML = html;
  wrap.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeSheet() {
  const wrap = document.getElementById('wo-sheet');
  if (wrap) wrap.classList.remove('open');
  document.body.style.overflow = '';
}

function showConfirm(text, onOk) {
  openSheet('<div class="sheet-head"><h3>请确认</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
    '<div class="sheet-body"><p class="muted">' + esc(text) + '</p>' +
    '<div class="row-end"><button class="btn ghost" onclick="closeSheet()">取消</button>' +
    '<button class="btn primary" id="wo-confirm-ok">确定</button></div></div>');
  document.getElementById('wo-confirm-ok').onclick = async function () {
    try { await onOk(); closeSheet(); } catch (e) { toast(e.message, 'error'); }
  };
}
