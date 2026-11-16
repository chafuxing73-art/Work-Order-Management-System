/* 工单管理后台 */
var CFG = null, FULLCFG = null, ME = null, USERS = [], TICKETS = [], CATS = [], PAGE = 'tickets';
var FIN_A = { photos: [] };
const $ = function (id) { return document.getElementById(id); };
const NAV = [
  { id: 'tickets', name: '工单管理', ico: '▤', roles: ['admin', 'dispatcher', 'worker'] },
  { id: 'dashboard', name: '统计看板', ico: '▥', roles: ['admin', 'dispatcher'] },
  { id: 'users', name: '员工与权限', ico: '◎', roles: ['admin', 'dispatcher'] },
  { id: 'qr', name: '二维码海报', ico: '▦', roles: ['admin', 'dispatcher', 'worker'] },
  { id: 'settings', name: '系统设置', ico: '⚙', roles: ['admin', 'dispatcher'] }
];
const ROLE_OPTS = [['staff', '普通员工'], ['worker', '处理人'], ['dispatcher', '行政派单'], ['admin', '超级管理员']];
const canManage = function () { return ME && (ME.role === 'admin' || ME.role === 'dispatcher'); };
const canConfig = function () { return ME && ME.role === 'admin'; };

async function init() {
  $('lg-link').href = '/';
  if (getToken()) {
    try { ME = await api('GET', '/api/me'); } catch (e) { ME = null; }
  }
  if (!ME) return;
  await enterMain();
}

async function doLogin() {
  const u = $('lg-user').value.trim(), p = $('lg-pwd').value;
  if (!u || !p) { toast('请输入账号和密码', 'error'); return; }
  try {
    const r = await api('POST', '/api/auth/login', { username: u, password: p });
    setToken(r.token); ME = r.user;
    await enterMain();
  } catch (e) { toast(e.message, 'error'); }
}

async function enterMain() {
  $('login-view').classList.add('hidden');
  $('main-view').classList.remove('hidden');
  $('top-user').textContent = ME.name + ' · ' + (ROLE_OPTS.find(function (r) { return r[0] === ME.role; }) || ['', ''])[1];
  $('top-avatar').textContent = ME.name.slice(0, 1);
  try { CFG = await api('GET', '/api/config/public'); } catch (e) { toast(e.message, 'error'); }
  buildNav(); fillFilters();
  if (canManage()) loadUsers();
  switchPage('tickets');
}

function buildNav() {
  $('nav').innerHTML = NAV.filter(function (n) { return n.roles.indexOf(ME.role) >= 0; })
    .map(function (n) {
      return '<button data-page="' + n.id + '" onclick="switchPage(\'' + n.id + '\')">' +
        '<span class="ico">' + n.ico + '</span><span class="tx">' + n.name + '</span></button>';
    }).join('');
}

function switchPage(id) {
  PAGE = id;
  document.querySelectorAll('.page').forEach(function (p) { p.classList.remove('on'); });
  const el = $('page-' + id);
  if (el) el.classList.add('on');
  document.querySelectorAll('#nav button').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-page') === id); });
  const n = NAV.find(function (x) { return x.id === id; });
  $('page-title').textContent = n ? n.name : '';
  if (id === 'tickets') loadTickets();
  if (id === 'dashboard') loadStats();
  if (id === 'users') loadUsers();
  if (id === 'qr') { if (!$('qr-base').value) $('qr-base').value = location.origin; renderQr(); }
  if (id === 'settings') loadSettings();
}

async function doLogout() {
  try { await api('POST', '/api/auth/logout', {}); } catch (e) { /* ignore */ }
  clearToken(); location.reload();
}

/* ---------------- 工单管理 ---------------- */

function fillFilters() {
  if (!CFG) return;
  const opts = function (arr, val, name) { return arr.map(function (x) { return '<option value="' + esc(val(x)) + '">' + esc(name(x)) + '</option>'; }).join(''); };
  $('fl-status').innerHTML = '<option value="">全部状态</option>' +
    Object.keys(STATUS_META).map(function (k) { return '<option value="' + k + '">' + STATUS_META[k].name + '</option>'; }).join('');
  $('fl-floor').innerHTML = '<option value="">全部楼层</option>' + opts(CFG.floors, function (x) { return x; }, function (x) { return x; });
  $('fl-category').innerHTML = '<option value="">全部类型</option>' + opts(CFG.categories, function (x) { return x.id; }, function (x) { return x.name; });
  $('fl-urgency').innerHTML = '<option value="">全部紧急度</option>' + opts(CFG.urgency, function (x) { return x.id; }, function (x) { return x.name; });
}

function resetFilters() {
  ['fl-status', 'fl-floor', 'fl-category', 'fl-urgency', 'fl-from', 'fl-to', 'fl-q'].forEach(function (id) { $(id).value = ''; });
  $('fl-overdue').checked = false;
  loadTickets();
}

async function loadTickets() {
  const params = new URLSearchParams();
  const map = { 'fl-status': 'status', 'fl-floor': 'floor', 'fl-category': 'category', 'fl-urgency': 'urgency', 'fl-from': 'from', 'fl-to': 'to', 'fl-q': 'q' };
  Object.keys(map).forEach(function (id) { if ($(id).value.trim()) params.set(map[id], $(id).value.trim()); });
  if ($('fl-overdue').checked) params.set('overdue', '1');
  $('ticket-tbody').innerHTML = '<tr><td colspan="9" class="muted">加载中…</td></tr>';
  try {
    TICKETS = await api('GET', '/api/admin/tickets?' + params.toString());
    $('ticket-count').textContent = '共 ' + TICKETS.length + ' 条工单' + (TICKETS.length >= 500 ? '（仅显示最近 500 条）' : '');
    $('ticket-tbody').innerHTML = TICKETS.length ? TICKETS.map(function (t) {
      return '<tr onclick="openDetail(' + t.id + ')">' +
        '<td class="nowrap small" style="font-family:ui-monospace,Consolas,monospace">' + esc(t.ticket_no) + '</td>' +
        '<td>' + statusBadge(t.status) + (t.overdue ? ' <span class="warn-dot">超时</span>' : '') + '</td>' +
        '<td><b>' + esc(t.floor) + '</b> ' + esc(t.location) + '</td>' +
        '<td>' + esc(t.category_name) + '</td>' +
        '<td>' + esc(t.urgency_name) + '</td>' +
        '<td>' + esc(t.creator_name) + '</td>' +
        '<td>' + esc(t.assignee_name || '—') + '</td>' +
        '<td class="nowrap small">' + fmtTime(t.created_at) + '</td>' +
        '<td class="nowrap small muted">' + fmtTime(t.resolve_due_at) + '</td></tr>';
    }).join('') : '<tr><td colspan="9" class="empty">没有符合条件的工单</td></tr>';
  } catch (e) { $('ticket-tbody').innerHTML = '<tr><td colspan="9" class="empty">' + esc(e.message) + '</td></tr>'; }
}

async function exportCsv() {
  try {
    const blob = await api('GET', '/api/admin/tickets/export');
    downloadBlob(blob, '工单明细_' + new Date().toISOString().slice(0, 10) + '.csv');
    toast('已导出，可直接用 Excel 打开');
  } catch (e) { toast(e.message, 'error'); }
}

async function openDetail(id) {
  openSheet('<div class="sheet-body"><div class="empty">加载中…</div></div>');
  try {
    const t = await api('GET', '/api/tickets/' + id);
    const ev = await api('GET', '/api/tickets/' + id + '/events');
    const imgs = (t.images || []).map(function (u) { return '<img src="' + esc(u) + '" onclick="window.open(\'' + esc(u) + '\')" alt="现场照片">'; }).join('');
    const finishImgs = (t.finish_images || []).map(function (u) { return '<img src="' + esc(u) + '" onclick="window.open(\'' + esc(u) + '\')" alt="完工照片">'; }).join('');
    const reportHtml = (t.finish_process || t.finish_result || finishImgs) ?
      '<div class="divider"></div><div class="bold small">完工报告</div>' +
      '<div class="kv" style="margin-top:6px">' +
      (t.finish_process ? '<div class="k">处理过程</div><div style="white-space:pre-wrap">' + esc(t.finish_process) + '</div>' : '') +
      (t.finish_result ? '<div class="k">处理结果</div><div style="white-space:pre-wrap">' + esc(t.finish_result) + '</div>' : '') +
      '</div>' + (finishImgs ? '<div class="detail-imgs">' + finishImgs + '</div>' : '') : '';
    const workers = USERS.filter(function (u) { return ['worker', 'dispatcher', 'admin'].indexOf(u.role) >= 0 && u.enabled; });
    let actions = '';
    if (canManage()) {
      actions += '<select id="d-assignee" style="width:auto;min-width:140px;padding:8px">' +
        '<option value="">选择处理人</option>' + workers.map(function (u) {
          return '<option value="' + u.id + '"' + (u.id === t.assignee_id ? ' selected' : '') + '>' + esc(u.name) + '（' + esc(u.dept || u.role) + '）</option>';
        }).join('') + '</select>';
      actions += '<button class="btn primary sm" onclick="assignTicket(' + id + ')">' + (t.assignee_id ? '改派' : '派单') + '</button>';
      if (t.status !== 'closed' && t.status !== 'completed') actions += '<button class="btn danger sm" onclick="closeTicket(' + id + ')">关闭工单</button>';
      else actions += '<button class="btn sm" onclick="reopenTicket(' + id + ')">重新打开</button>';
    }
    if (['assigned', 'processing'].indexOf(t.status) >= 0 && (t.assignee_id === ME.id || canManage())) {
      actions += '<button class="btn sm" onclick="workerAct(' + id + ',\'start\')">开始处理</button>';
      actions += '<button class="btn sm" onclick="workerAct(' + id + ',\'finish\')">提交完工报告</button>';
    }
    openSheet('<div class="sheet-head"><h3>工单详情</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
      '<div class="sheet-body">' +
      '<div class="row" style="margin-bottom:12px;flex-wrap:wrap">' + statusBadge(t.status) +
      (t.overdue ? '<span class="warn-dot">已超时</span>' : '') +
      '<span class="small muted" style="font-family:ui-monospace,Consolas,monospace">' + esc(t.ticket_no) + '</span></div>' +
      '<div class="kv">' +
      '<div class="k">位置</div><div>' + esc(t.floor) + ' · ' + esc(t.location) + '</div>' +
      '<div class="k">类型</div><div>' + esc(t.category_name) + '（' + esc(t.urgency_name) + '）</div>' +
      '<div class="k">描述</div><div style="white-space:pre-wrap">' + esc(t.description) + '</div>' +
      '<div class="k">提单人</div><div>' + esc(t.creator_name) + (t.creator_dept ? ' · ' + esc(t.creator_dept) : '') + ' / ' + esc(t.contact_phone) + '</div>' +
      '<div class="k">接单人</div><div>' + esc(t.assignee_name || '尚未指派') + '</div>' +
      '<div class="k">响应时限</div><div>' + fmtTime(t.response_due_at) + '</div>' +
      '<div class="k">完成时限</div><div>' + fmtTime(t.resolve_due_at) + '</div>' +
      '<div class="k">提交时间</div><div>' + fmtTime(t.created_at) + '</div>' +
      (t.rating ? '<div class="k">满意度</div><div>' + t.rating + ' / 5</div>' : '') +
      (t.accept_remark ? '<div class="k">验收备注</div><div>' + esc(t.accept_remark) + '</div>' : '') +
      (t.close_reason ? '<div class="k">关闭原因</div><div>' + esc(t.close_reason) + '</div>' : '') +
      '</div>' + (imgs ? '<div class="detail-imgs">' + imgs + '</div>' : '') +
      reportHtml +
      '<div class="divider"></div><div class="bold small">处理进度</div><div class="timeline">' +
      ev.map(function (e) {
        return '<div class="tl-item"><div class="act">' + esc(e.action) + '</div><div class="tm">' +
          fmtTime(e.at) + ' · ' + esc(e.actor_name) + '</div>' + (e.note ? '<div class="nt">' + esc(e.note) + '</div>' : '') + '</div>';
      }).join('') + '</div>' +
      (actions ? '<div class="divider"></div><div class="row" style="flex-wrap:wrap;gap:8px">' + actions + '</div>' : '') +
      '</div>');
  } catch (e) { toast(e.message, 'error'); closeSheet(); }
}

async function adminAct(id, action, payload) {
  try {
    await api('POST', '/api/admin/tickets/' + id + '/' + action, payload || {});
    closeSheet(); toast('操作成功'); loadTickets();
  } catch (e) { toast(e.message, 'error'); }
}

async function workerAct(id, action) {
  if (action === 'finish') { openFinishSheetAdmin(id); return; }
  try {
    await api('POST', '/api/tickets/' + id + '/' + action, {});
    closeSheet(); toast('操作成功'); loadTickets();
  } catch (e) { toast(e.message, 'error'); }
}

function openFinishSheetAdmin(id) {
  FIN_A = { photos: [] };
  openSheet('<div class="sheet-head"><h3>提交完工报告（后台代操作）</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
    '<div class="sheet-body">' +
    '<label class="field"><span class="lb">处理过程<em>*</em></span>' +
    '<textarea id="a-fp" placeholder="检查发现什么、做了哪些处理、是否更换配件（至少 5 个字）"></textarea></label>' +
    '<label class="field"><span class="lb">处理结果<em>*</em></span>' +
    '<textarea id="a-fr" placeholder="例如：已修复，现场测试正常，可正常使用"></textarea></label>' +
    '<label class="field"><span class="lb">完工照片<span class="muted">（选填，最多 3 张）</span></span>' +
    '<div class="photo-grid" id="a-fin-photo-grid"></div>' +
    '<input type="file" id="a-fin-file-input" accept="image/*" multiple class="hidden" onchange="onAdminFinishFiles(event)">' +
    '</label>' +
    '<p class="hint">代操作会记录你的账号，请按师傅实际处理情况填写。提交后进入「待验收」。</p>' +
    '<div class="row-end"><button class="btn ghost" onclick="closeSheet()">取消</button>' +
    '<button class="btn primary" id="a-btn-finish" onclick="submitFinishAdmin(' + id + ')">确认完工</button></div></div>');
  renderAdminFinishPhotos();
}

function renderAdminFinishPhotos() {
  var html = FIN_A.photos.map(function (p, i) {
    return '<div class="photo"><img src="' + p + '" alt="完工照片"><button type="button" class="del" onclick="removeAdminFinishPhoto(' + i + ')">×</button></div>';
  }).join('');
  if (FIN_A.photos.length < 3) {
    html += '<div class="photo add" onclick="document.getElementById(\'a-fin-file-input\').click()"><div class="plus">+</div><div>添加照片</div></div>';
  }
  var el = $('a-fin-photo-grid');
  if (el) el.innerHTML = html;
}

function removeAdminFinishPhoto(i) { FIN_A.photos.splice(i, 1); renderAdminFinishPhotos(); }

async function onAdminFinishFiles(ev) {
  var files = Array.prototype.slice.call(ev.target.files || []);
  if (!files.length) return;
  toast('正在处理照片…');
  for (var k = 0; k < files.length; k++) {
    if (FIN_A.photos.length >= 3) break;
    try { FIN_A.photos.push(await compressImage(files[k])); } catch (e) { toast(e.message, 'error'); }
  }
  ev.target.value = '';
  renderAdminFinishPhotos();
}

async function submitFinishAdmin(id) {
  var process = $('a-fp').value.trim();
  var result = $('a-fr').value.trim();
  if (process.length < 5) { toast('请至少用 5 个字描述处理过程', 'error'); return; }
  if (!result) { toast('请填写处理结果', 'error'); return; }
  var btn = $('a-btn-finish');
  if (btn) { btn.disabled = true; btn.textContent = '提交中…'; }
  try {
    await api('POST', '/api/tickets/' + id + '/finish',
      { process: process, result: result, images: FIN_A.photos });
    closeSheet(); toast('完工报告已提交'); loadTickets();
  } catch (e) {
    toast(e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '确认完工'; }
  }
}

function assignTicket(id) {
  const v = $('d-assignee').value;
  if (!v) { toast('请选择处理人', 'error'); return; }
  adminAct(id, 'assign', { assignee_id: Number(v) });
}

function closeTicket(id) {
  openSheet('<div class="sheet-head"><h3>关闭工单</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
    '<div class="sheet-body"><label class="field"><span class="lb">关闭原因<em>*</em></span>' +
    '<textarea id="d-reason" placeholder="例如：重复报修 / 已由其他渠道处理 / 无法处理"></textarea></label>' +
    '<div class="row-end"><button class="btn ghost" onclick="closeSheet()">取消</button>' +
    '<button class="btn danger" onclick="adminAct(' + id + ',\'close\',{reason:document.getElementById(\'d-reason\').value})">确认关闭</button></div></div>');
}

function reopenTicket(id) {
  showConfirm('重新打开后工单会回到「待受理」，需要重新派单。确定继续吗？', function () {
    return adminAct(id, 'reopen', { reason: '后台重新打开' });
  });
}

/* ---------------- 统计看板 ---------------- */

function quickRange(days) {
  if (!days) { $('st-from').value = ''; $('st-to').value = ''; }
  else {
    const end = new Date(), start = new Date(Date.now() - (days - 1) * 86400000);
    const f = function (d) { return d.toISOString().slice(0, 10); };
    $('st-from').value = f(start); $('st-to').value = f(end);
  }
  loadStats();
}

async function loadStats() {
  const p = new URLSearchParams();
  if ($('st-from').value) p.set('from', $('st-from').value);
  if ($('st-to').value) p.set('to', $('st-to').value);
  try {
    const s = await api('GET', '/api/admin/stats?' + p.toString());
    const cards = [
      ['工单总量', s.total, '当前筛选区间'],
      ['已完成', s.done, '含自动完成'],
      ['完成率', s.completion_rate + '%', '已完成 / 总量'],
      ['超时工单', s.overdue, '超时率 ' + s.overdue_rate + '%', s.overdue > 0],
      ['平均响应', fmtMin(s.avg_response_min), '提交 → 接单'],
      ['平均完成', fmtMin(s.avg_resolve_min), '提交 → 完工'],
      ['当前积压', s.backlog, '未完结工单', s.backlog > 0]
    ];
    $('stat-grid').innerHTML = cards.map(function (c) {
      return '<div class="stat"><div class="lb">' + c[0] + '</div><div class="vl' + (c[3] ? ' warn' : '') + '">' + esc(c[1]) + '</div><div class="ex">' + esc(c[2]) + '</div></div>';
    }).join('');
    renderBars('bars-status', s.by_status);
    renderBars('bars-floor', s.by_floor, true);
    renderBars('bars-category', s.by_category);
    renderBars('bars-assignee', s.by_assignee);
    renderBars('bars-urgency', s.by_urgency, true);
    const max = Math.max.apply(null, s.daily.map(function (d) { return d.count; }).concat([1]));
    $('trend').innerHTML = s.daily.length ? s.daily.map(function (d) {
      return '<div class="col"><div class="bar" style="height:' + Math.max(2, d.count / max * 88) + '%" title="' + d.date + '：' + d.count + ' 单"></div>' +
        '<div class="lb">' + d.date.slice(5) + '</div></div>';
    }).join('') : '<div class="empty small" style="width:100%">暂无数据</div>';
  } catch (e) { toast(e.message, 'error'); }
}

function renderBars(id, list, orange) {
  const el = $(id);
  if (!list || !list.length) { el.innerHTML = '<div class="empty small">暂无数据</div>'; return; }
  const max = Math.max.apply(null, list.map(function (x) { return x.count; }).concat([1]));
  el.innerHTML = list.map(function (x) {
    return '<div class="bar-row"><div class="nm" title="' + esc(x.name) + '">' + esc(x.name) + '</div>' +
      '<div class="track"><div class="fill' + (orange ? ' orange' : '') + '" style="width:' + (x.count / max * 100) + '%"></div></div>' +
      '<div class="ct">' + x.count + '</div></div>';
  }).join('');
}

/* ---------------- 员工与权限 ---------------- */

async function loadUsers() {
  if (!canManage()) return;
  const q = $('us-q') ? $('us-q').value.trim() : '';
  try {
    USERS = await api('GET', '/api/admin/users' + (q ? '?q=' + encodeURIComponent(q) : ''));
    $('user-tbody').innerHTML = USERS.length ? USERS.map(function (u) {
      return '<tr><td class="nowrap small">' + esc(u.employee_no) + '</td><td>' + esc(u.name) + '</td>' +
        '<td>' + esc(u.dept || '—') + '</td><td class="small">' + esc(u.phone || '—') + '</td>' +
        '<td><span class="badge" style="background:#eef2f8;color:#334155">' + esc(u.role_name) + '</span></td>' +
        '<td class="small">' + (u.wecom_userid ? esc(u.wecom_userid) : '<span class="muted">未绑定</span>') + '</td>' +
        '<td>' + (u.enabled ? '<span style="color:#16a34a">正常</span>' : '<span style="color:#dc2626">已停用</span>') + '</td>' +
        '<td class="nowrap"><button class="btn sm" onclick="editUser(' + u.id + ')">编辑</button> ' +
        '<button class="btn sm" title="重置为统一默认密码 123456" onclick="resetPwd(' + u.id + ')">重置密码</button></td></tr>';
    }).join('') : '<tr><td colspan="8" class="empty">没有匹配的员工</td></tr>';
  } catch (e) { toast(e.message, 'error'); }
}

function editUser(id) {
  const u = id ? USERS.find(function (x) { return x.id === id; }) : null;
  openSheet('<div class="sheet-head"><h3>' + (u ? '编辑员工' : '新增员工') + '</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
    '<div class="sheet-body">' +
    '<label class="field"><span class="lb">工号<em>*</em></span><input type="text" id="u-no" value="' + esc(u ? u.employee_no : '') + '"' + (u ? ' disabled' : '') + '></label>' +
    '<label class="field"><span class="lb">姓名<em>*</em></span><input type="text" id="u-name" value="' + esc(u ? u.name : '') + '"></label>' +
    '<label class="field"><span class="lb">部门</span><input type="text" id="u-dept" value="' + esc(u ? u.dept : '') + '"></label>' +
    '<label class="field"><span class="lb">手机号</span><input type="text" id="u-phone" value="' + esc(u ? u.phone : '') + '"></label>' +
    '<label class="field"><span class="lb">角色</span><select id="u-role">' + ROLE_OPTS.map(function (r) {
      return '<option value="' + r[0] + '"' + (u && u.role === r[0] ? ' selected' : '') + '>' + r[1] + '</option>';
    }).join('') + '</select></label>' +
    '<label class="field"><span class="lb">企业微信 UserID</span><input type="text" id="u-wecom" value="' + esc(u ? u.wecom_userid : '') + '" placeholder="用于应用消息推送"></label>' +
    '<label class="field"><span class="lb">状态</span><select id="u-enabled"><option value="1"' + (!u || u.enabled ? ' selected' : '') + '>正常</option>' +
    '<option value="0"' + (u && !u.enabled ? ' selected' : '') + '>停用</option></select></label>' +
    '<div class="row-end"><button class="btn ghost" onclick="closeSheet()">取消</button>' +
    '<button class="btn primary" onclick="saveUser(' + (u ? u.id : 'null') + ')">保存</button></div></div>');
}

async function saveUser(id) {
  const d = {
    employee_no: $('u-no').value.trim(), name: $('u-name').value.trim(), dept: $('u-dept').value.trim(),
    phone: $('u-phone').value.trim(), role: $('u-role').value,
    wecom_userid: $('u-wecom').value.trim(), enabled: Number($('u-enabled').value)
  };
  if (!d.name) { toast('请填写姓名', 'error'); return; }
  try {
    if (id) await api('PUT', '/api/admin/users/' + id, d);
    else { if (!d.employee_no) { toast('请填写工号', 'error'); return; } await api('POST', '/api/admin/users', d); }
    closeSheet(); toast('已保存'); loadUsers();
  } catch (e) { toast(e.message, 'error'); }
}

async function resetPwd(id) {
  try {
    const r = await api('POST', '/api/admin/users/' + id + '/reset_password', {});
    openSheet('<div class="sheet-head"><h3>密码已重置</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
      '<div class="sheet-body"><p class="muted">该账号已恢复为统一默认密码，旧密码立即失效。</p>' +
      '<div class="box" style="margin:14px 0"><div class="small muted">账号</div><div class="bold" style="font-size:16px">' + esc(r.username) + '</div>' +
      '<div class="small muted" style="margin-top:10px">默认密码</div><div class="bold" style="font-size:20px;color:#0036ad;font-family:ui-monospace,Consolas,monospace">' + esc(r.password) + '</div></div>' +
      '<div class="notice warn">所有账号统一使用同一个默认密码，请提醒本人首次登录后到「修改密码」里改掉。</div>' +
      '<div class="row-end"><button class="btn primary" onclick="closeSheet()">知道了</button></div></div>');
  } catch (e) { toast(e.message, 'error'); }
}

/* ---------------- 二维码 ---------------- */

function qrUrl(floor) {
  let base = ($('qr-base').value || location.origin).trim().replace(/\/+$/, '');
  if (!/^https?:\/\//.test(base)) base = 'http://' + base;
  return base + '/?f=' + encodeURIComponent(floor);
}

function makeQrCanvas(url, size) {
  const qr = qrcode(0, 'M');
  qr.addData(url); qr.make();
  const n = qr.getModuleCount(), quiet = 4, total = n + quiet * 2;
  const cell = Math.max(2, Math.floor(size / total)), px = cell * total;
  const cv = document.createElement('canvas'); cv.width = px; cv.height = px;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, px, px);
  ctx.fillStyle = '#0036ad';
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
    if (qr.isDark(r, c)) ctx.fillRect((c + quiet) * cell, (r + quiet) * cell, cell, cell);
  }
  return cv;
}

function renderQr() {
  const floors = (CFG && CFG.floors) || ['2F', '3F', '4F', '5F', '6F'];
  $('qr-hint').textContent = '示例链接：' + qrUrl(floors[0]) + '　（换服务器 IP 或域名后，改上面的地址重新生成即可）';
  $('qr-grid').innerHTML = floors.map(function (f) {
    return '<div class="qr-card"><div class="floor">' + esc(f) + '</div>' +
      '<div class="tip">' + esc((CFG && CFG.company.system_name) || '楼层报修') + '</div>' +
      '<div class="qv" id="qv-' + esc(f) + '"></div>' +
      '<div class="acts"><button class="btn sm" onclick="dlQr(\'' + esc(f) + '\')">下载二维码</button>' +
      '<button class="btn sm primary" onclick="dlPoster(\'' + esc(f) + '\')">下载海报</button></div></div>';
  }).join('');
  floors.forEach(function (f) {
    const cv = makeQrCanvas(qrUrl(f), 260);
    cv.style.width = '170px'; cv.style.height = '170px';
    cv.id = 'qr-cv-' + f;
    $('qv-' + f).appendChild(cv);
  });
}

function dlQr(floor) {
  const cv = makeQrCanvas(qrUrl(floor), 600);
  cv.toBlob(function (b) { downloadBlob(b, '二维码_' + floor + '.png'); });
}

function loadImg(src) {
  return new Promise(function (resolve, reject) {
    const im = new Image();
    im.onload = function () { resolve(im); };
    im.onerror = reject;
    im.src = src;
  });
}

async function makePoster(floor) {
  const W = 480, H = 680;
  const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d');
  const company = (CFG && CFG.company.name) || '锦联国际物流';
  const slogan = (CFG && CFG.company.slogan) || '扫码报修，进度可查';
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, W, H);
  const grad = ctx.createLinearGradient(0, 0, W, 148);
  grad.addColorStop(0, '#0e42d2'); grad.addColorStop(1, '#0036ad');
  ctx.fillStyle = grad; ctx.fillRect(0, 0, W, 148);
  try {
    const im = await loadImg('/static/logo.png');
    ctx.drawImage(im, 30, 32, 84, 84);
  } catch (e) { /* 无 logo 时跳过 */ }
  ctx.textAlign = 'left';
  ctx.fillStyle = '#ffffff';
  let fs = 22;
  ctx.font = '600 ' + fs + 'px "Microsoft YaHei",sans-serif';
  while (ctx.measureText(company).width > W - 154 && fs > 14) {
    fs -= 1; ctx.font = '600 ' + fs + 'px "Microsoft YaHei",sans-serif';
  }
  ctx.fillText(company, 130, 70);
  ctx.font = '14px "Microsoft YaHei",sans-serif'; ctx.fillStyle = 'rgba(255,255,255,.8)';
  ctx.fillText(slogan, 130, 98);
  ctx.textAlign = 'center';
  ctx.fillStyle = '#0036ad'; ctx.font = '600 78px "Microsoft YaHei",sans-serif';
  ctx.fillText(floor, W / 2, 262);
  ctx.fillStyle = '#6b7280'; ctx.font = '16px "Microsoft YaHei",sans-serif';
  ctx.fillText('本层报修 · 扫码提交工单', W / 2, 298);
  const q = makeQrCanvas(qrUrl(floor), 520);
  ctx.drawImage(q, (W - 260) / 2, 320, 260, 260);
  ctx.fillStyle = '#64748b'; ctx.font = '13px "Microsoft YaHei",sans-serif';
  ctx.fillText('水电 · 空调 · 灯具 · 门窗 · 家具 · 保洁', W / 2, 608);
  ctx.fillStyle = '#ff7d00'; ctx.fillRect(0, H - 62, W, 62);
  ctx.fillStyle = '#ffffff'; ctx.font = '600 17px "Microsoft YaHei",sans-serif';
  ctx.fillText('仅限公司内部员工使用', W / 2, H - 22);
  return cv;
}

async function dlPoster(floor) {
  try {
    const cv = await makePoster(floor);
    cv.toBlob(function (b) { downloadBlob(b, '报修二维码海报_' + floor + '.png'); });
  } catch (e) { toast('海报生成失败：' + e.message, 'error'); }
}

async function downloadAllPosters() {
  const floors = (CFG && CFG.floors) || ['2F', '3F', '4F', '5F', '6F'];
  toast('正在生成 ' + floors.length + ' 张海报…');
  for (let i = 0; i < floors.length; i++) {
    await dlPoster(floors[i]);
    await new Promise(function (r) { setTimeout(r, 450); });
  }
}

/* ---------------- 系统设置 ---------------- */

async function loadSettings() {
  $('cfg-notice').textContent = canConfig() ?
    '修改后点击页面底部「保存全部设置」，楼层和报修类型会立即对员工端生效。' :
    '当前账号可查看配置，但只有超级管理员可以修改并保存。';
  $('btn-save-cfg').disabled = !canConfig();
  try {
    FULLCFG = await api('GET', '/api/admin/config');
    $('cf-company').value = FULLCFG.company.name || '';
    $('cf-company-full').value = FULLCFG.company.full_name || '';
    $('cf-system').value = FULLCFG.company.system_name || '';
    $('cf-slogan').value = FULLCFG.company.slogan || '';
    $('cf-floors').value = (FULLCFG.floors || []).join(',');
    $('cf-autoclose').value = FULLCFG.sla.auto_close_hours;
    $('cf-wecom-enabled').value = FULLCFG.wecom.enabled ? '1' : '0';
    $('cf-wecom-notify-workers').value = FULLCFG.wecom.notify_new_ticket_to_workers === false ? '0' : '1';
    $('cf-corp').value = FULLCFG.wecom.corp_id || '';
    $('cf-agent').value = FULLCFG.wecom.agent_id || '';
    $('cf-secret').value = FULLCFG.wecom.corp_secret || '';
    $('cf-contact-secret').value = FULLCFG.wecom.contact_secret || '';
    $('cf-public-base').value = FULLCFG.wecom.public_base_url || '';
    $('cf-oauth-enabled').value = FULLCFG.wecom.oauth_enabled ? '1' : '0';
    $('cf-urgency').innerHTML = FULLCFG.urgency.map(function (u, i) {
      return '<div class="row" style="margin-bottom:8px;gap:8px">' +
        '<span style="width:60px;flex:none" class="small">' + esc(u.name) + '</span>' +
        '<span class="tiny muted">响应</span><input type="number" style="width:76px" id="urg-r-' + i + '" value="' + u.response_min + '">' +
        '<span class="tiny muted">完成</span><input type="number" style="width:76px" id="urg-d-' + i + '" value="' + u.resolve_min + '">' +
        '<span class="tiny muted">分钟</span></div>';
    }).join('');
    CATS = JSON.parse(JSON.stringify(FULLCFG.categories));
    renderCatRows();
  } catch (e) { toast(e.message, 'error'); }
}

function renderCatRows() {
  $('cfg-cat-tbody').innerHTML = CATS.map(function (c, i) {
    return '<tr><td><input type="text" value="' + esc(c.name) + '" oninput="CATS[' + i + '].name=this.value"></td>' +
      '<td><input type="text" style="width:58px" value="' + esc(c.mark || '') + '" oninput="CATS[' + i + '].mark=this.value"></td>' +
      '<td><input type="color" class="color-input" value="' + esc(c.color || '#0e42d2') + '" oninput="CATS[' + i + '].color=this.value"></td>' +
      '<td><select onchange="CATS[' + i + '].urgency=this.value">' + FULLCFG.urgency.map(function (u) {
        return '<option value="' + u.id + '"' + (u.id === c.urgency ? ' selected' : '') + '>' + esc(u.name) + '</option>';
      }).join('') + '</select></td>' +
      '<td><input type="checkbox" style="width:auto" ' + (c.enabled ? 'checked' : '') + ' onchange="CATS[' + i + '].enabled=this.checked"></td>' +
      '<td><button class="btn sm danger" onclick="CATS.splice(' + i + ',1);renderCatRows()">删除</button></td></tr>';
  }).join('');
}

function addCatRow() {
  CATS.push({ id: 'cat_' + Date.now(), name: '新类型', mark: '新', color: '#0e42d2', urgency: 'normal', enabled: true });
  renderCatRows();
}

async function saveSettings() {
  if (!canConfig()) { toast('只有超级管理员可以保存设置', 'error'); return; }
  const floors = $('cf-floors').value.split(/[,，]/).map(function (x) { return x.trim(); }).filter(Boolean);
  if (!floors.length) { toast('至少配置一个楼层', 'error'); return; }
  if (!CATS.length || CATS.some(function (c) { return !c.name.trim(); })) { toast('报修类型名称不能为空', 'error'); return; }
  if ($('cf-oauth-enabled').value === '1' && !$('cf-public-base').value.trim()) {
    toast('开启企微免登需先填写公网访问地址', 'error'); return;
  }
  const urgency = FULLCFG.urgency.map(function (u, i) {
    return Object.assign({}, u, {
      response_min: Math.max(1, Number($('urg-r-' + i).value) || 1),
      resolve_min: Math.max(1, Number($('urg-d-' + i).value) || 1)
    });
  });
  const payload = {
    company: {
      name: $('cf-company').value.trim(), full_name: $('cf-company-full').value.trim(),
      system_name: $('cf-system').value.trim(), slogan: $('cf-slogan').value.trim()
    },
    floors: floors, categories: CATS, urgency: urgency,
    sla: { auto_close_hours: Math.max(1, Number($('cf-autoclose').value) || 48), check_interval_sec: FULLCFG.sla.check_interval_sec || 60 },
    wecom: {
      enabled: $('cf-wecom-enabled').value === '1', corp_id: $('cf-corp').value.trim(),
      agent_id: $('cf-agent').value.trim(), corp_secret: $('cf-secret').value.trim(),
      to_dispatchers: true,
      notify_new_ticket_to_workers: $('cf-wecom-notify-workers').value !== '0',
      contact_secret: $('cf-contact-secret').value.trim(),
      public_base_url: $('cf-public-base').value.trim().replace(/\/+$/, ''),
      oauth_enabled: $('cf-oauth-enabled').value === '1'
    }
  };
  try {
    await api('PUT', '/api/admin/config', payload);
    CFG = await api('GET', '/api/config/public');
    fillFilters();
    toast('设置已保存，员工端刷新后生效');
    loadSettings();
  } catch (e) { toast(e.message, 'error'); }
}

async function testWecom() {
  const userid = $('cf-testuser').value.trim();
  if (!userid) { toast('请填写测试接收人的企微 UserID', 'error'); return; }
  try {
    await api('POST', '/api/admin/wecom/test', { userid: userid });
    toast('测试消息已发送，请在企业微信里查收');
  } catch (e) { toast(e.message, 'error'); }
}

async function syncWecomUsers() {
  const btn = $('btn-sync-wecom');
  btn.disabled = true; btn.textContent = '同步中…';
  $('sync-result').textContent = '';
  try {
    const r = await api('POST', '/api/admin/wecom/sync', {});
    $('sync-result').textContent = '同步完成：新增 ' + r.added + ' 人，更新 ' + r.updated +
      ' 人，读取部门 ' + r.departments + ' 个。';
    toast('通讯录同步完成');
    loadUsers();
  } catch (e) {
    $('sync-result').textContent = e.message;
    toast('同步失败，详见下方提示', 'error');
  } finally {
    btn.disabled = false; btn.textContent = '一键同步通讯录';
  }
}

function changePwd() {
  openSheet('<div class="sheet-head"><h3>修改密码</h3><button class="icon-btn" onclick="closeSheet()">关闭</button></div>' +
    '<div class="sheet-body"><label class="field"><span class="lb">原密码</span><input type="password" id="p-old"></label>' +
    '<label class="field"><span class="lb">新密码（至少 6 位）</span><input type="password" id="p-new"></label>' +
    '<div class="row-end"><button class="btn ghost" onclick="closeSheet()">取消</button>' +
    '<button class="btn primary" onclick="savePwd()">保存</button></div></div>');
}

async function savePwd() {
  try {
    await api('POST', '/api/auth/password', { old_password: $('p-old').value, new_password: $('p-new').value });
    closeSheet(); toast('密码已修改');
  } catch (e) { toast(e.message, 'error'); }
}

init();
