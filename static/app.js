/* 员工端 / 处理人移动工作台 */
var CFG = null, ME = null, TAB = 'new';
var SEL = { cat: '', urg: '', photos: [] };
var FIN = { photos: [] };
var LAST_STATE = { init: false, unread: 0, pool: 0, active: 0 };
var BASE_TITLE = document.title, TITLE_TIMER = null, POLL_MS = 20000;
const LOC_TAGS = ['茶水间', '工位', '会议室', '卫生间', '走廊', '前台', '打印区', '电梯厅'];
const TAB_DEF = [
  { id: 'new', name: '报修', need: 'staff' },
  { id: 'mine', name: '我的工单', need: 'staff' },
  { id: 'pool', name: '工单池', need: 'worker' },
  { id: 'tasks', name: '我的任务', need: 'worker' },
  { id: 'notif', name: '消息', need: 'staff' }
];

const $ = function (id) { return document.getElementById(id); };
const isWorker = function () { return ME && ['worker', 'dispatcher', 'admin'].indexOf(ME.role) >= 0; };

/* 企业微信 OAuth 静默授权：code 换 UserID，后端自动建档并签发会话。
   返回 true 表示已发起跳转，调用方应停止后续渲染。 */
async function tryWecomLogin() {
  const params = new URLSearchParams(location.search);
  const code = params.get('code');
  if (code) {
    try {
      const r = await api('POST', '/api/auth/wecom', { code: code });
      setToken(r.token); ME = r.user;
    } catch (e) { toast(e.message, 'error'); }
    params.delete('code'); params.delete('state');
    const q = params.toString();
    try { history.replaceState(null, '', location.pathname + (q ? '?' + q : '')); } catch (e) { }
    return false;
  }
  if (!CFG.wecom_oauth || getToken()) return false;
  if (!/wxwork/i.test(navigator.userAgent || '')) return false;
  try {
    const r = await api('GET', '/api/wecom/auth_url?redirect=' +
      encodeURIComponent(location.href.split('#')[0]));
    if (r.url) { location.replace(r.url); return true; }
  } catch (e) { /* 配置异常时静默降级为手动填表 */ }
  return false;
}

async function init() {
  try {
    CFG = await api('GET', '/api/config/public');
  } catch (e) {
    document.querySelector('.m-body').innerHTML = '<div class="empty"><div class="big">×</div>' + esc(e.message) + '</div>';
    return;
  }
  // 企微扫码免登：仅在企微内置浏览器 + 已配置公网可信域名时生效，
  // 未配置或普通浏览器扫码完全不走这里，保持手动填表。
  if (await tryWecomLogin()) return;

  $('hd-title').textContent = CFG.company.system_name || '锦联国际 · 楼层报修';
  $('hd-sub').textContent = CFG.company.slogan || '扫码报修，进度可查';
  document.title = CFG.company.system_name || '楼层报修';
  BASE_TITLE = document.title;

  const urlFloor = new URLSearchParams(location.search).get('f');
  let floor = CFG.floors.indexOf(urlFloor) >= 0 ? urlFloor : CFG.floors[0];
  $('f-floor').innerHTML = CFG.floors.map(function (f) {
    return '<option value="' + esc(f) + '"' + (f === floor ? ' selected' : '') + '>' + esc(f) + '</option>';
  }).join('');
  $('hd-floor').textContent = floor;
  $('f-floor').onchange = function () { $('hd-floor').textContent = this.value; };

  $('loc-tags').innerHTML = LOC_TAGS.map(function (t) {
    return '<span class="tag" onclick="addLoc(\'' + t + '\')">' + t + '</span>';
  }).join('');
  renderCats();
  renderUrg();
  renderPhotos();
  $('f-phone').value = localStorage.getItem('wo_last_phone') || '';
  $('f-dept').value = localStorage.getItem('wo_last_dept') || '';
  $('dept-list').innerHTML = (CFG.departments || []).map(function (d) {
    return '<option value="' + esc(d) + '"></option>';
  }).join('');

  if (getToken()) {
    try { ME = await api('GET', '/api/me'); } catch (e) { ME = null; }
  }
  applyIdentityUI();
  buildTabs();
  // 轮询与音频解锁在「未绑定」时也注册：师傅首次登记后无需刷新页面即可生效
  setInterval(function () { pollState(false); }, POLL_MS);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) pollState(false); });
  document.addEventListener('touchstart', unlockAudio, { once: true });
  document.addEventListener('click', unlockAudio, { once: true });

  const wantedTab = new URLSearchParams(location.search).get('tab');
  // 只有明确要进「我的工单 / 工单池 / 我的任务 / 消息」这类必须认人的入口，
  // 才在首屏要求登记；普通楼层扫码一律直接进报修表单。
  if (!ME && wantedTab && ['mine', 'pool', 'tasks', 'notif'].indexOf(wantedTab) >= 0) {
    showPanel('bind');
    return;
  }
  if (ME) fillUser();
  let wanted = wantedTab;
  if (wanted && !isWorker() && (wanted === 'pool' || wanted === 'tasks')) wanted = null;
  if (!wanted || !TAB_DEF.some(function (t) { return t.id === wanted; })) wanted = 'new';
  switchTab(wanted);
  if (ME) pollState(true);
}

/* 未登记时给一句「首次填、以后自动记住」的提示；已登记则隐藏。
   姓名 / 部门 / 手机号三个字段始终显示，登录后自动带出，本人也可临时改。 */
function applyIdentityUI() {
  $('identity-hint').classList.toggle('hidden', !!ME);
}

function fillUser() {
  $('f-contact').value = ME.name || '';
  $('f-dept').value = ME.dept || '';
  if (ME.phone) $('f-phone').value = ME.phone;
  $('hd-floor').title = ME.name + (ME.dept ? ' · ' + ME.dept : '');
  applyIdentityUI();
  refreshBell();
}

function buildTabs() {
  const box = $('tabs');
  const list = TAB_DEF.filter(function (t) {
    return ME && (t.need === 'staff' || isWorker());
  });
  box.innerHTML = list.map(function (t) {
    return '<button data-tab="' + t.id + '" onclick="switchTab(\'' + t.id + '\')">' + t.name + '</button>';
  }).join('');
  box.style.display = list.length ? 'flex' : 'none';
}

function showPanel(id) {
  document.querySelectorAll('.panel').forEach(function (p) { p.classList.remove('on'); });
  const el = $('panel-' + id);
  if (el) el.classList.add('on');
}

function switchTab(id) {
  if (!ME && id !== 'new') { showPanel('bind'); return; }
  TAB = id;
  showPanel(id);
  document.querySelectorAll('#tabs button').forEach(function (b) {
    b.classList.toggle('on', b.getAttribute('data-tab') === id);
  });
  if (id === 'mine') loadMine();
  if (id === 'pool') loadPool();
  if (id === 'tasks') loadTasks();
  if (id === 'notif') loadNotif(true);
  window.scrollTo(0, 0);
}

async function doBind() {
  const name = $('bind-name').value.trim(), dept = $('bind-dept').value.trim(), phone = $('bind-phone').value.trim();
  if (!name) { toast('请填写姓名', 'error'); return; }
  if (!dept) { toast('请填写部门', 'error'); return; }
  if (!/^[0-9+\- ]{6,20}$/.test(phone)) { toast('请填写有效的手机号', 'error'); return; }
  try {
    const r = await api('POST', '/api/auth/bind', { name: name, dept: dept, phone: phone });
    setToken(r.token); ME = r.user;
    localStorage.setItem('wo_last_dept', dept);
    localStorage.setItem('wo_last_phone', phone);
    buildTabs(); fillUser();
    const wanted = new URLSearchParams(location.search).get('tab');
    switchTab(wanted && isWorker() && TAB_DEF.some(function (t) { return t.id === wanted; }) ? wanted : 'new');
    pollState(true);
    toast('登记成功，欢迎使用');
  } catch (e) { toast(e.message, 'error'); }
}

/* ---------------- 报修表单 ---------------- */

function renderCats() {
  $('cat-grid').innerHTML = CFG.categories.map(function (c) {
    return '<div class="cat-item' + (SEL.cat === c.id ? ' on' : '') + '" onclick="selectCat(\'' + c.id + '\')">' +
      '<div class="mark" style="background:' + esc(c.color || '#0e42d2') + '">' + esc(c.mark || c.name.slice(0, 2)) + '</div>' +
      '<div class="nm">' + esc(c.name) + '</div></div>';
  }).join('');
}

function selectCat(id) {
  SEL.cat = id;
  const c = CFG.categories.find(function (x) { return x.id === id; });
  if (c && c.urgency) SEL.urg = c.urgency;
  renderCats(); renderUrg();
}

function renderUrg() {
  $('urg-grid').innerHTML = CFG.urgency.map(function (u) {
    return '<div class="urg-item' + (SEL.urg === u.id ? ' on' : '') + '" onclick="SEL.urg=\'' + u.id + '\';renderUrg()">' +
      '<div class="nm">' + esc(u.name) + '</div><div class="ds">' + esc(u.desc) + '<br>' +
      fmtMin(u.response_min) + '内响应</div></div>';
  }).join('');
}

function addLoc(t) {
  const el = $('f-location');
  const cur = el.value.trim();
  if (cur.indexOf(t) < 0) el.value = cur ? cur + ' · ' + t : t;
  el.focus();
}

function renderPhotos() {
  let html = SEL.photos.map(function (p, i) {
    return '<div class="photo"><img src="' + p + '" alt="现场照片"><button type="button" class="del" onclick="removePhoto(' + i + ')">×</button></div>';
  }).join('');
  if (SEL.photos.length < 6) {
    html += '<div class="photo add" onclick="document.getElementById(\'file-input\').click()"><div class="plus">+</div><div>添加照片</div></div>';
  }
  $('photo-grid').innerHTML = html;
}

function removePhoto(i) { SEL.photos.splice(i, 1); renderPhotos(); }

async function onFiles(ev) {
  const files = Array.prototype.slice.call(ev.target.files || []);
  if (!files.length) return;
  toast('正在处理照片…');
  for (const f of files) {
    if (SEL.photos.length >= 6) break;
    try { SEL.photos.push(await compressImage(f)); } catch (e) { toast(e.message, 'error'); }
  }
  ev.target.value = '';
  renderPhotos();
}

async function submitTicket() {
  const data = {
    floor: $('f-floor').value,
    location: $('f-location').value.trim(),
    category_id: SEL.cat,
    urgency: SEL.urg,
    description: $('f-desc').value.trim(),
    images: SEL.photos,
    contact_name: $('f-contact').value.trim(),
    contact_phone: $('f-phone').value.trim()
  };
  if (!data.location) { toast('请填写具体位置', 'error'); return; }
  if (!data.category_id) { toast('请选择报修类型', 'error'); return; }
  if (!data.urgency) { toast('请选择紧急程度', 'error'); return; }
  if (data.description.length < 5) { toast('请至少用 5 个字描述问题', 'error'); return; }
  if (!data.images.length) { toast('请至少上传 1 张现场照片', 'error'); return; }
  if (!data.contact_name) { toast('请填写姓名', 'error'); return; }
  if (!/^[0-9+\- ]{6,20}$/.test(data.contact_phone)) { toast('请填写有效的手机号', 'error'); return; }
  const dept = $('f-dept').value.trim();
  if (!dept) { toast('请填写部门', 'error'); return; }
  const btn = $('btn-submit');
  btn.disabled = true; btn.textContent = '提交中…';
  try {
    if (!ME) {
      // 首次提交：姓名+部门+手机号在后台静默完成登记，紧接着直接提交工单。
      // 表单内容与照片都保留在页面上，用户不会感到被拦截。
      const r = await api('POST', '/api/auth/bind', {
        name: data.contact_name, dept: dept, phone: data.contact_phone
      });
      setToken(r.token); ME = r.user;
      buildTabs(); applyIdentityUI(); refreshBell(); pollState(true);
      btn.textContent = '提交中…';
    }
    const t = await api('POST', '/api/tickets', data);
    localStorage.setItem('wo_last_phone', data.contact_phone);
    localStorage.setItem('wo_last_dept', dept);
    $('succ-no').textContent = t.ticket_no;
    $('succ-tip').textContent = '期望响应：' + fmtTime(t.response_due_at) + ' · 完成时限：' + fmtTime(t.resolve_due_at);
    showPanel('success');
    document.querySelectorAll('#tabs button').forEach(function (b) { b.classList.remove('on'); });
    loadMine(); refreshBell();
    toast('提交成功');
  } catch (e) { toast(e.message, 'error'); }
  finally { btn.disabled = false; btn.textContent = '提交工单'; }
}

function resetForm() {
  $('f-location').value = ''; $('f-desc').value = '';
  SEL = { cat: '', urg: '', photos: [] };
  renderCats(); renderUrg(); renderPhotos();
  switchTab('new');
}

/* ---------------- 列表 ---------------- */

function ticketCard(t) {
  const warn = t.overdue ? '<span class="warn-dot">超时</span>' : '';
  return '<div class="ticket-item" onclick="openTicket(' + t.id + ')">' +
    '<div class="top">' + statusBadge(t.status) + warn +
    '<span class="no">' + esc(t.ticket_no) + '</span><span class="spacer"></span>' +
    '<span class="tiny muted">' + fmtTime(t.created_at) + '</span></div>' +
    '<div class="tt">' + esc(t.category_name) + ' · ' + esc(t.location) + '</div>' +
    '<div class="meta"><span>' + esc(t.floor) + '</span><span>' + esc(t.urgency_name) + '</span>' +
    (t.assignee_name ? '<span>接单：' + esc(t.assignee_name) + '</span>' : '<span>待派单</span>') + '</div></div>';
}

async function loadMine() {
  const box = $('mine-list');
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const list = await api('GET', '/api/tickets/mine');
    box.innerHTML = list.length ? list.map(ticketCard).join('') :
      '<div class="empty"><div class="big">≡</div>还没有工单<br><span class="small">点「报修」提交第一单</span></div>';
  } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; }
}

async function loadPool() {
  const box = $('pool-list');
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const list = await api('GET', '/api/pool');
    const bar = '<div class="notice" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">' +
      '工单池里是还没人接的单，点开可抢单。接单后才能看到提单人联系方式。' +
      '<span class="sound-toggle" onclick="toggleSound(event)">声音提醒：' + (soundOn() ? '开' : '关') + '</span></div>';
    box.innerHTML = bar + (list.length ? list.map(ticketCard).join('') :
      '<div class="empty"><div class="big">✓</div>工单池已清空</div>');
  } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; }
}

async function loadTasks() {
  const box = $('tasks-list');
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const list = await api('GET', '/api/tickets/assigned');
    box.innerHTML = list.length ?
      '<div class="notice">接单后请及时点「开始处理」，完工后点「提交完工」。</div>' + list.map(ticketCard).join('') :
      '<div class="empty"><div class="big">≡</div>当前没有待办任务</div>';
  } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; }
}

/* ---------------- 详情 ---------------- */

async function openTicket(id) {
  openSheet('<div class="sheet-body"><div class="empty">加载中…</div></div>');
  try {
    const t = await api('GET', '/api/tickets/' + id);
    const ev = await api('GET', '/api/tickets/' + id + '/events');
    const imgs = (t.images || []).map(function (u) {
      return '<img src="' + esc(u) + '" onclick="window.open(\'' + esc(u) + '\')" alt="现场照片">';
    }).join('');
    const finishImgs = (t.finish_images || []).map(function (u) {
      return '<img src="' + esc(u) + '" onclick="window.open(\'' + esc(u) + '\')" alt="完工照片">';
    }).join('');
    const reportHtml = (t.finish_process || t.finish_result || finishImgs) ?
      '<div class="divider"></div><div class="bold small">完工报告</div>' +
      '<div class="kv" style="margin-top:6px">' +
      (t.finish_process ? '<div class="k">处理过程</div><div style="white-space:pre-wrap">' + esc(t.finish_process) + '</div>' : '') +
      (t.finish_result ? '<div class="k">处理结果</div><div style="white-space:pre-wrap">' + esc(t.finish_result) + '</div>' : '') +
      '</div>' + (finishImgs ? '<div class="detail-imgs">' + finishImgs + '</div>' : '') : '';
    let actions = '';
    if (t.status === 'pending' && isWorker()) {
      actions += '<button class="btn accent" onclick="actTicket(' + id + ',\'claim\')">抢单接下</button>';
    }
    if (t.assignee_id === ME.id || ['admin', 'dispatcher'].indexOf(ME.role) >= 0) {
      if (t.status === 'assigned') actions += '<button class="btn primary" onclick="actTicket(' + id + ',\'start\')">开始处理</button>';
      if (t.status === 'processing' || t.status === 'assigned') actions += '<button class="btn primary" onclick="finishTicket(' + id + ')">提交完工</button>';
    }
    if (t.status === 'pending_accept' && (t.creator_id === ME.id || ['admin', 'dispatcher'].indexOf(ME.role) >= 0)) {
      actions += '<button class="btn primary" onclick="confirmTicket(' + id + ')">确认完成</button>';
    }
    actions += '<button class="btn" onclick="noteTicket(' + id + ')">补充说明</button>';

    const html =
      '<div class="sheet-head"><h3>工单详情</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
      '<div class="sheet-body">' +
      '<div class="row" style="margin-bottom:12px">' + statusBadge(t.status) +
      (t.overdue ? '<span class="warn-dot">已超时</span>' : '') +
      '<span class="no small muted" style="font-family:ui-monospace,Consolas,monospace">' + esc(t.ticket_no) + '</span></div>' +
      '<div class="kv">' +
      '<div class="k">位置</div><div>' + esc(t.floor) + ' · ' + esc(t.location) + '</div>' +
      '<div class="k">类型</div><div>' + esc(t.category_name) + '（' + esc(t.urgency_name) + '）</div>' +
      '<div class="k">提单人</div><div>' + esc(t.creator_name) + (t.creator_dept ? ' · ' + esc(t.creator_dept) : '') + '</div>' +
      '<div class="k">联系电话</div><div>' + (t.contact_phone ? esc(t.contact_phone) : '<span class="muted">接单后可见</span>') + '</div>' +
      '<div class="k">接单人</div><div>' + esc(t.assignee_name || '尚未指派') + '</div>' +
      '<div class="k">响应时限</div><div>' + fmtTime(t.response_due_at) + '</div>' +
      '<div class="k">完成时限</div><div>' + fmtTime(t.resolve_due_at) + '</div>' +
      '<div class="k">提交时间</div><div>' + fmtTime(t.created_at) + '</div>' +
      '</div>' +
      '<div class="divider"></div><div class="bold small">问题描述</div>' +
      '<p style="font-size:13px;white-space:pre-wrap;margin:6px 0 0">' + esc(t.description) + '</p>' +
      (imgs ? '<div class="detail-imgs">' + imgs + '</div>' : '') +
      reportHtml +
      '<div class="divider"></div><div class="bold small">处理进度</div>' +
      '<div class="timeline">' + ev.map(function (e) {
        return '<div class="tl-item"><div class="act">' + esc(e.action) + '</div>' +
          '<div class="tm">' + fmtTime(e.at) + ' · ' + esc(e.actor_name) + '</div>' +
          (e.note ? '<div class="nt">' + esc(e.note) + '</div>' : '') + '</div>';
      }).join('') + '</div>' +
      '<div class="divider"></div><div class="row" style="flex-wrap:wrap;gap:8px">' + actions + '</div>' +
      '</div>';
    openSheet(html);
  } catch (e) { toast(e.message, 'error'); closeSheet(); }
}

async function actTicket(id, action, payload) {
  try {
    await api('POST', '/api/tickets/' + id + '/' + action, payload || {});
    closeSheet(); toast('操作成功');
    refreshCurrent(); refreshBell();
  } catch (e) { toast(e.message, 'error'); }
}

function finishTicket(id) {
  FIN = { photos: [] };
  openSheet('<div class="sheet-head"><h3>提交完工报告</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
    '<div class="sheet-body">' +
    '<label class="field"><span class="lb">处理过程<em>*</em></span>' +
    '<textarea id="f-fp" placeholder="检查发现什么、做了哪些处理、是否更换配件（至少 5 个字）"></textarea></label>' +
    '<label class="field"><span class="lb">处理结果<em>*</em></span>' +
    '<textarea id="f-fr" placeholder="例如：已修复，现场测试正常，可正常使用"></textarea></label>' +
    '<label class="field"><span class="lb">完工照片<span class="muted">（选填，最多 3 张）</span></span>' +
    '<div class="photo-grid" id="fin-photo-grid"></div>' +
    '<input type="file" id="fin-file-input" accept="image/*" multiple class="hidden" onchange="onFinishFiles(event)">' +
    '</label>' +
    '<p class="hint">完工报告会随工单永久留存并可导出 CSV。提交后进入「待验收」，提单人可确认；48 小时内无异议系统自动完成。</p>' +
    '<div class="row-end"><button class="btn ghost" onclick="closeSheet()">取消</button>' +
    '<button class="btn primary" id="btn-finish" onclick="submitFinish(' + id + ')">确认完工</button></div></div>');
  renderFinishPhotos();
}

function renderFinishPhotos() {
  var html = FIN.photos.map(function (p, i) {
    return '<div class="photo"><img src="' + p + '" alt="完工照片"><button type="button" class="del" onclick="removeFinishPhoto(' + i + ')">×</button></div>';
  }).join('');
  if (FIN.photos.length < 3) {
    html += '<div class="photo add" onclick="document.getElementById(\'fin-file-input\').click()"><div class="plus">+</div><div>添加照片</div></div>';
  }
  var el = $('fin-photo-grid');
  if (el) el.innerHTML = html;
}

function removeFinishPhoto(i) { FIN.photos.splice(i, 1); renderFinishPhotos(); }

async function onFinishFiles(ev) {
  var files = Array.prototype.slice.call(ev.target.files || []);
  if (!files.length) return;
  toast('正在处理照片…');
  for (var k = 0; k < files.length; k++) {
    if (FIN.photos.length >= 3) break;
    try { FIN.photos.push(await compressImage(files[k])); } catch (e) { toast(e.message, 'error'); }
  }
  ev.target.value = '';
  renderFinishPhotos();
}

async function submitFinish(id) {
  var process = $('f-fp').value.trim();
  var result = $('f-fr').value.trim();
  if (process.length < 5) { toast('请至少用 5 个字描述处理过程', 'error'); return; }
  if (!result) { toast('请填写处理结果', 'error'); return; }
  var btn = $('btn-finish');
  if (btn) { btn.disabled = true; btn.textContent = '提交中…'; }
  try {
    await api('POST', '/api/tickets/' + id + '/finish',
      { process: process, result: result, images: FIN.photos });
    closeSheet(); toast('完工报告已提交');
    refreshCurrent(); refreshBell();
  } catch (e) {
    toast(e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '确认完工'; }
  }
}

function confirmTicket(id) {
  openSheet('<div class="sheet-head"><h3>确认完成</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
    '<div class="sheet-body"><label class="field"><span class="lb">满意度</span><select id="f-rating">' +
    '<option value="5">5 · 非常满意</option><option value="4">4 · 满意</option>' +
    '<option value="3">3 · 一般</option><option value="2">2 · 不太满意</option><option value="1">1 · 不满意</option>' +
    '</select></label><label class="field"><span class="lb">备注<span class="muted">（选填）</span></span>' +
    '<textarea id="f-remark" placeholder="确认无误，或有其他说明"></textarea></label>' +
    '<div class="row-end"><button class="btn ghost" onclick="closeSheet()">取消</button>' +
    '<button class="btn primary" onclick="actTicket(' + id + ',\'confirm\',{rating:Number(document.getElementById(\'f-rating\').value),remark:document.getElementById(\'f-remark\').value})">确认完成</button></div></div>');
}

function noteTicket(id) {
  openSheet('<div class="sheet-head"><h3>补充说明</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
    '<div class="sheet-body"><label class="field"><span class="lb">内容<em>*</em></span>' +
    '<textarea id="f-note2" placeholder="补充现场情况或反馈问题"></textarea></label>' +
    '<div class="row-end"><button class="btn ghost" onclick="closeSheet()">取消</button>' +
    '<button class="btn primary" onclick="actTicket(' + id + ',\'remark\',{note:document.getElementById(\'f-note2\').value})">发送</button></div></div>');
}

function refreshCurrent() {
  if (TAB === 'mine') loadMine();
  else if (TAB === 'pool') loadPool();
  else if (TAB === 'tasks') loadTasks();
  else if (TAB === 'notif') loadNotif(false);
  else { loadMine(); }
}

/* ---------------- 消息 ---------------- */

async function refreshBell() {
  try {
    const me = await api('GET', '/api/me');
    ME.unread = me.unread;
    $('bell-dot').classList.toggle('hidden', !me.unread);
  } catch (e) { /* ignore */ }
}

async function loadNotif(markRead) {
  const box = $('notif-list');
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const list = await api('GET', '/api/notifications');
    box.innerHTML = list.length ? list.map(function (n) {
      return '<div class="notif' + (n.is_read ? '' : ' unread') + '">' +
        '<div class="nt">' + esc(n.title) + '</div>' +
        '<div class="nc">' + esc(n.content) + '</div>' +
        '<div class="tiny muted" style="margin-top:5px">' + fmtTime(n.created_at) + '</div></div>';
    }).join('') : '<div class="empty"><div class="big">≡</div>暂无消息</div>';
    if (markRead && list.some(function (n) { return !n.is_read; })) {
      await api('POST', '/api/notifications/read', {});
      $('bell-dot').classList.add('hidden');
    }
  } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + '</div>'; }
}

async function readAll() {
  try { await api('POST', '/api/notifications/read', {}); loadNotif(false); refreshBell(); toast('已全部标为已读'); }
  catch (e) { toast(e.message, 'error'); }
}

/* ---------------- 新工单主动提醒 ----------------
   页面开着时每 20 秒轮询一次 /api/me，发现工单池新增就弹横幅 + 响铃 + 振动；
   手机锁屏 / 切到后台时浏览器会暂停轮询，切回前台立即补检查一次。
   锁屏也要收到的场景，靠「企业微信应用消息」，在管理端「系统设置」里配置。 */

function soundOn() { return localStorage.getItem('wo_sound') !== '0'; }

function toggleSound(ev) {
  if (ev) ev.stopPropagation();
  localStorage.setItem('wo_sound', soundOn() ? '0' : '1');
  toast(soundOn() ? '声音提醒已开启' : '声音提醒已关闭');
  loadPool();
}

function unlockAudio() {
  try {
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    const c = new C();
    if (c.resume) c.resume();
    setTimeout(function () { c.close(); }, 300);
  } catch (e) { /* ignore */ }
}

function beep() {
  try {
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    const ac = new C();
    [0, 0.24].forEach(function (t) {
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = 'sine'; o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, ac.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.16, ac.currentTime + t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + t + 0.17);
      o.connect(g); g.connect(ac.destination);
      o.start(ac.currentTime + t); o.stop(ac.currentTime + t + 0.19);
    });
    setTimeout(function () { ac.close(); }, 900);
  } catch (e) { /* ignore */ }
}

function flashTitle(msg) {
  if (TITLE_TIMER) clearInterval(TITLE_TIMER);
  let n = 0;
  TITLE_TIMER = setInterval(function () {
    document.title = (n++ % 2) ? BASE_TITLE : msg;
    if (n > 12) { clearInterval(TITLE_TIMER); TITLE_TIMER = null; document.title = BASE_TITLE; }
  }, 700);
}

function hideAlert() { $('alert-bar').classList.add('hidden'); }

function alertGo() {
  hideAlert();
  if (TITLE_TIMER) { clearInterval(TITLE_TIMER); TITLE_TIMER = null; }
  document.title = BASE_TITLE;
  switchTab('pool');
}

async function pollState(first) {
  if (!ME || document.hidden) return;
  try {
    const r = await api('GET', '/api/me');
    const st = {
      unread: Number(r.unread) || 0,
      pool: Number(r.pool_count) || 0,
      active: Number(r.my_active) || 0
    };
    $('bell-dot').classList.toggle('hidden', !st.unread);
    const fresh = !first && LAST_STATE.init && isWorker() && st.pool > LAST_STATE.pool;
    if (fresh) {
      const delta = st.pool - LAST_STATE.pool;
      $('alert-text').textContent = (delta > 0 ? delta + ' 张新工单待接' : '有新工单待接') + '，共 ' + st.pool + ' 张';
      $('alert-bar').classList.remove('hidden');
      if (soundOn()) beep();
      if (navigator.vibrate) navigator.vibrate([120, 80, 120]);
      flashTitle('【新工单】' + st.pool + ' 张待接');
      if (TAB === 'pool') loadPool();
      else if (TAB === 'tasks') loadTasks();
    }
    LAST_STATE = { init: true, unread: st.unread, pool: st.pool, active: st.active };
  } catch (e) { /* 轮询失败静默，下一轮重试 */ }
}

init();
