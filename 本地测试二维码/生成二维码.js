/*
 * 本地测试二维码生成器（零依赖，只用 Node 内置模块 + 项目自带的 qrcode.min.js）
 *
 * 用法：node 生成二维码.js
 * 电脑 IP 变了？改下面 HOSTS 里的 ip，重新跑一次即可。
 * 正式部署到 Ubuntu 后，请改用管理端「二维码海报」页面生成（可填真实内网地址）。
 */
const fs = require('fs');
const vm = require('vm');
const zlib = require('zlib');
const path = require('path');

const DIR = __dirname;
const LIB = path.join(DIR, '..', 'static', 'qrcode.min.js');
const FLOORS = ['2F', '3F', '4F', '5F', '6F'];
/*
 * 2026-11-12 下午修正：本机当前只有「以太网」在线。
 * 192.168.30.67 是 WLAN 无线网卡先前连 WiFi 时拿到的 DHCP 地址，
 * 现在 WLAN 已 Disconnected，该地址只剩租约残留、并不可达，已停用。
 * 若之后重新连回 WiFi 且确实要走无线网段，再把对应条目加回来即可。
 */
const HOSTS = [
  { tag: '', ip: '192.168.12.22' },   // 以太网：当前唯一在线且默认路由优先
];
const PORT = 8080;
const SCALE = 12;   // 每个模块 12 像素
const QUIET = 4;    // 静默区 4 模块
const DARK = [0, 54, 173];   // 深蓝 #0036ad，与系统二维码前景色一致（保证扫码识别率）

/* ---- 加载浏览器端二维码库（不联网） ---- */
const ctx = {};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(LIB, 'utf8'), ctx);
if (typeof ctx.qrcode !== 'function') throw new Error('二维码库加载失败');
const qrcode = ctx.qrcode;

/* ---- 手写 PNG 编码（Node 内置 zlib，不需要 canvas / 图像库） ---- */
const CRC_TABLE = (() => {
  const t = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c;
  }
  return t;
})();
function crc32(buf) {
  let c = -1;
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  return (c ^ -1) >>> 0;
}
function chunk(type, data) {
  const t = Buffer.from(type, 'ascii');
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(Buffer.concat([t, data])), 0);
  return Buffer.concat([len, t, data, crc]);
}
function pngFromMatrix(isDark, n) {
  const W = (n + QUIET * 2) * SCALE;
  const raw = Buffer.alloc(W * (1 + W * 3));
  let off = 0;
  for (let y = 0; y < W; y++) {
    raw[off++] = 0; // filter: none
    const my = Math.floor(y / SCALE) - QUIET;
    for (let x = 0; x < W; x++) {
      const mx = Math.floor(x / SCALE) - QUIET;
      const dark = my >= 0 && my < n && mx >= 0 && mx < n && isDark(my, mx);
      raw[off++] = dark ? DARK[0] : 255;
      raw[off++] = dark ? DARK[1] : 255;
      raw[off++] = dark ? DARK[2] : 255;
    }
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(W, 0); ihdr.writeUInt32BE(W, 4);
  ihdr[8] = 8;  // 8 bit
  ihdr[9] = 2;  // truecolor RGB
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/* ---- 生成 ---- */
const made = [];
function make(name, url, tag) {
  const qr = qrcode(0, 'M');
  qr.addData(url); qr.make();
  fs.writeFileSync(path.join(DIR, name), pngFromMatrix((r, c) => qr.isDark(r, c), qr.getModuleCount()));
  made.push({ name, url, tag });
}
for (const h of HOSTS) {
  const suffix = h.tag ? '_' + h.tag : '';
  for (const f of FLOORS) make(`${f}${suffix}.png`, `http://${h.ip}:${PORT}/?f=${encodeURIComponent(f)}`, h.tag);
  // 处理人工作台：首次打开登记姓名+部门+手机号，之后一扫码直达工单池
  make(`处理人工作台${suffix}.png`, `http://${h.ip}:${PORT}/?tab=pool`, h.tag);
}
for (const h of HOSTS) {
  console.log(`\n【${h.tag || h.ip}】 http://${h.ip}:${PORT}`);
  made.filter(m => m.tag === h.tag).forEach(m => console.log(`   ${m.name.padEnd(26)} -> ${m.url}`));
}
console.log(`\n共生成 ${made.length} 张，输出目录：${DIR}`);
console.log('提示：手机与电脑必须连同一局域网；127.0.0.1 的码手机打不开。');
console.log('处理人工作台码：师傅首次打开需登记姓名+部门+手机号，之后一扫码直达工单池。');
