/**
 * 星舵 Helm 舵轮 SVG 生成器（纯字符串，无依赖）
 * 结构：星轨 → 转柄 ×8 → 辐条 ×8 → 双层轮缘 + 刻度 → 内环 → 轮毂（铆钉 + 八芒星）→ 阶段节点 → 闪烁星点
 */
export const HELM_STAGES = ['需求', '设计', '审批', '计划', '执行'];

export function buildHelmSvg(): string {
  var C = 120;
  var f = function (n: number) { return n.toFixed(2); };
  var pt = function (r: number, deg: number): [number, number] {
    var a = (deg - 90) * Math.PI / 180;
    return [C + Math.cos(a) * r, C + Math.sin(a) * r];
  };
  var s: string = '';
  s += '<svg class="helm-w" viewBox="0 0 240 240" aria-hidden="true">';
  s += '<defs>'
    + '<linearGradient id="hw-metal" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="var(--app-brand)"/><stop offset="55%" stop-color="#6f86ff"/><stop offset="100%" stop-color="#47bfff"/></linearGradient>'
    + '<linearGradient id="hw-spoke" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#47bfff"/><stop offset="100%" stop-color="var(--app-brand)"/></linearGradient>'
    + '<radialGradient id="hw-hub" cx="50%" cy="40%" r="60%"><stop offset="0%" stop-color="var(--app-brand-soft)"/><stop offset="100%" stop-color="var(--app-bg)"/></radialGradient>'
    + '<filter id="hw-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
    + '</defs>';

  // 外圈星轨（反向慢转）
  s += '<g class="hw-orbit"><circle cx="120" cy="120" r="112" class="hw-orbit-ring"/>';
  [20, 140, 255].forEach(function (d: number, i: number) {
    var p = pt(112, d);
    s += '<circle cx="' + f(p[0]) + '" cy="' + f(p[1]) + '" r="' + (i === 0 ? 2.2 : 1.4) + '" class="hw-orbit-dot"/>';
  });
  s += '</g>';

  s += '<g class="hw-rotor">';
  // 转柄 + 辐条
  for (var i = 0; i < 8; i++) {
    var d = i * 45, dl = (0.35 + i * 0.05).toFixed(2), dp = (0.75 + i * 0.05).toFixed(2);
    s += '<g transform="rotate(' + d + ' 120 120)">';
    // 辐条：从轮毂到轮缘渐细
    s += '<path class="hw-spoke" style="animation-delay:' + dl + 's" d="M116.2 98 L117.6 42 Q120 38.5 122.4 42 L123.8 98 Z"/>';
    // 车削转柄：颈 → 珠 → 握把 → 端头
    s += '<g class="hw-peg" style="animation-delay:' + dp + 's">'
      + '<rect x="118" y="24" width="4" height="12" rx="1.5"/>'
      + '<ellipse cx="120" cy="24" rx="4.2" ry="2.4"/>'
      + '<path d="M117.2 22 Q115.4 15 117.4 9 L122.6 9 Q124.6 15 122.8 22 Z"/>'
      + '<circle cx="120" cy="6.6" r="3.6"/>'
      + '</g>';
    s += '</g>';
  }
  // 双层轮缘
  s += '<circle class="hw-rim hw-rim--band" cx="120" cy="120" r="80"/>';
  s += '<circle class="hw-rim hw-rim--outer" cx="120" cy="120" r="85"/>';
  s += '<circle class="hw-rim hw-rim--inner" cx="120" cy="120" r="75"/>';
  // 轮缘刻度
  s += '<g class="hw-ticks">';
  for (var t = 0; t < 72; t++) {
    var a = t * 5, long = t % 9 === 0;
    var p1 = pt(long ? 76.5 : 78, a), p2 = pt(long ? 83.5 : 82, a);
    s += '<line x1="' + f(p1[0]) + '" y1="' + f(p1[1]) + '" x2="' + f(p2[0]) + '" y2="' + f(p2[1]) + '"' + (long ? ' class="hw-tick-l"' : '') + '/>';
  }
  s += '</g>';
  // 内环（虚线）
  s += '<circle class="hw-inner" cx="120" cy="120" r="46"/>';
  s += '</g>';

  // 轮毂
  s += '<g class="hw-hub">';
  s += '<circle cx="120" cy="120" r="24" fill="url(#hw-hub)" class="hw-hub-ring"/>';
  s += '<circle cx="120" cy="120" r="19" class="hw-hub-ring2"/>';
  for (var r = 0; r < 8; r++) {
    var q = pt(21.5, r * 45 + 22.5);
    s += '<circle cx="' + f(q[0]) + '" cy="' + f(q[1]) + '" r="1.3" class="hw-rivet"/>';
  }
  // 八芒星（星舵之“星”）
  var star: string = '';
  for (var k = 0; k < 16; k++) {
    var sp = pt(k % 2 === 0 ? (k % 4 === 0 ? 15 : 10) : 4.2, k * 22.5);
    star += (k ? 'L' : 'M') + f(sp[0]) + ' ' + f(sp[1]);
  }
  s += '<path class="hw-star" filter="url(#hw-glow)" d="' + star + 'Z"/>';
  s += '<circle cx="120" cy="120" r="2.2" class="hw-core"/>';
  s += '</g>';

  // 星座：每个阶段节点向外连到两颗星
  s += '<g class="hw-const">';
  HELM_STAGES.forEach(function (_: string, i: number) {
    var base = i * 72, n0 = pt(80, base);
    var a1 = pt(104, base - 13), a2 = pt(116, base + 9), a3 = pt(100, base + 22);
    var dl = (1.25 + i * 0.18).toFixed(2);
    var d = 'M' + f(n0[0]) + ' ' + f(n0[1]) + ' L' + f(a1[0]) + ' ' + f(a1[1]) + ' L' + f(a2[0]) + ' ' + f(a2[1]) + ' L' + f(a3[0]) + ' ' + f(a3[1]);
    s += '<path class="hw-const-line" style="animation-delay:' + dl + 's" d="' + d + '"/>';
    [a1, a2, a3].forEach(function (q: [number, number], j: number) {
      s += '<circle class="hw-const-star" style="animation-delay:' + (1.35 + i * 0.18 + j * 0.08).toFixed(2) + 's" cx="' + f(q[0]) + '" cy="' + f(q[1]) + '" r="' + (j === 1 ? 1.8 : 1.2) + '"/>';
    });
  });
  s += '</g>';

  // 五个阶段节点 + 连线
  var nodes = HELM_STAGES.map(function (_: string, i: number) { return pt(80, i * 72); });
  s += '<polygon class="hw-path" points="' + nodes.map(function (n: [number, number]) { return f(n[0]) + ',' + f(n[1]); }).join(' ') + '"/>';
  nodes.forEach(function (n: [number, number], i: number) {
    s += '<g class="hw-node" style="animation-delay:' + (1.1 + i * 0.18).toFixed(2) + 's">'
      + '<circle cx="' + f(n[0]) + '" cy="' + f(n[1]) + '" r="8" class="hw-node-halo"/>'
      + '<circle cx="' + f(n[0]) + '" cy="' + f(n[1]) + '" r="4" class="hw-node-dot"/></g>';
  });

  // 闪烁星点
  [[18, 40, 1.0], [214, 30, 0.8], [226, 176, 1.1], [30, 200, 0.9], [196, 222, 0.7], [8, 118, 0.6]].forEach(function (p: number[], i: number) {
    var x = p[0], y = p[1], z = p[2] * 5;
    s += '<path class="hw-spark" style="animation-delay:' + (0.6 + i * 0.35).toFixed(2) + 's" d="M' + x + ' ' + (y - z) + ' Q' + x + ' ' + y + ' ' + (x + z) + ' ' + y + ' Q' + x + ' ' + y + ' ' + x + ' ' + (y + z) + ' Q' + x + ' ' + y + ' ' + (x - z) + ' ' + y + ' Q' + x + ' ' + y + ' ' + x + ' ' + (y - z) + 'Z"/>';
  });

  s += '</svg>';
  return s;
}
