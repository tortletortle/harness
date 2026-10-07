/**
 * 星空背景（Canvas 2D，无依赖）
 * - 开场“跃迁”：星星从中心向外拉出光轨，随后减速为缓慢漂移
 * - 三层视差星星 + 闪烁，部分带十字星芒
 * - 随机流星
 * - warpOut()：结尾再次跃迁冲散
 * 返回 { stop, warpOut }
 */
type Star = { x: number; y: number; z: number; r: number; tw: number; sp: number; hue: string };
type Meteor = { x: number; y: number; vx: number; vy: number; life: number; max: number };

export type Starfield = { stop: () => void; warpOut: () => void };

export function startStarfield(canvas: HTMLCanvasElement): Starfield {
  var ctx = canvas.getContext('2d') as CanvasRenderingContext2D;
  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var W = 0, H = 0, raf = 0, start = performance.now(), last = start;
  var hues = ['255,255,255', '255,255,255', '180,230,215', '160,220,200', '160,210,210', '255,236,210'];
  var stars: Star[] = [];
  var meteors: Meteor[] = [];
  var nextMeteor = 700;
  var warpAt = 0;

  function resize() {
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function spawn(): Star {
    // 以屏幕中心为原点的三维坐标，z 越小越近
    return {
      x: (Math.random() - 0.5) * 2, y: (Math.random() - 0.5) * 2, z: Math.random() * 0.9 + 0.1,
      r: Math.random() < 0.06 ? 1.6 + Math.random() * 1.2 : 0.3 + Math.random() * 1.0,
      tw: Math.random() * Math.PI * 2, sp: 0.8 + Math.random() * 2.4,
      hue: hues[(Math.random() * hues.length) | 0],
    };
  }
  resize();
  var count = Math.min(900, Math.round((W * H) / 1400));
  for (var i = 0; i < count; i++) stars.push(spawn());
  window.addEventListener('resize', resize);

  function frame(now: number) {
    // rAF 首帧时间戳可能早于 start，负的 dt 会把星星推到镜头后面，导致半径为负
    var t = Math.max(0, now - start), dt = Math.max(0, Math.min(50, now - last)); last = now;
    // 跃迁速度：开场很快，1.1 秒内缓动到巡航速度
    var k = Math.min(1, t / 1100);
    var speed = 0.00004 + (1 - k) * (1 - k) * (1 - k) * 0.0022;
    var trail = (1 - k) * (1 - k);
    if (warpAt) {
      // 结尾反向跃迁：再次加速冲散
      var w = Math.min(1, (now - warpAt) / 650);
      speed += w * w * 0.0035;
      trail = Math.max(trail, w * 0.9);
    }
    var cx = W / 2, cy = H * 0.42, fov = Math.max(W, H) * 0.5;

    ctx.clearRect(0, 0, W, H);
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];
      var pz = s.z;
      s.z -= speed * dt;
      if (s.z <= 0.02) { stars[i] = spawn(); stars[i].z = 1; continue; }
      var x = cx + (s.x / s.z) * fov, y = cy + (s.y / s.z) * fov;
      if (x < -50 || x > W + 50 || y < -50 || y > H + 50) { stars[i] = spawn(); stars[i].z = 1; continue; }
      var depth = 1 - s.z;
      var tw = 0.55 + 0.45 * Math.sin(s.tw + t * 0.001 * s.sp);
      var a = Math.min(1, (0.25 + depth * 0.9) * (k < 1 || warpAt ? 1 : tw)) * Math.min(1, t / 250);
      var r = Math.max(0.1, s.r * (0.5 + depth * 1.1));

      if (trail > 0.02) {
        // 光轨：从上一帧位置（按跃迁强度放大）拉到当前位置
        var zz = pz + (pz - s.z) * 10 * trail;
        var px = cx + (s.x / zz) * fov, py = cy + (s.y / zz) * fov;
        ctx.strokeStyle = 'rgba(' + s.hue + ',' + (a * 0.9) + ')';
        ctx.lineWidth = r;
        ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(x, y); ctx.stroke();
      }
      ctx.fillStyle = 'rgba(' + s.hue + ',' + a + ')';
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      if (s.r > 1.5 && k >= 1) {
        // 亮星十字星芒
        var L = r * 5 * tw;
        ctx.strokeStyle = 'rgba(' + s.hue + ',' + (a * 0.35) + ')';
        ctx.lineWidth = 0.6;
        ctx.beginPath(); ctx.moveTo(x - L, y); ctx.lineTo(x + L, y); ctx.moveTo(x, y - L); ctx.lineTo(x, y + L); ctx.stroke();
      }
    }

    // 流星
    nextMeteor -= dt;
    if (nextMeteor <= 0 && t > 900) {
      nextMeteor = 900 + Math.random() * 1400;
      var ang = Math.PI * (0.78 + Math.random() * 0.12);
      var v = 0.9 + Math.random() * 0.5;
      meteors.push({ x: W * (0.45 + Math.random() * 0.55), y: H * Math.random() * 0.35, vx: Math.cos(ang) * v, vy: Math.sin(ang) * v, life: 0, max: 700 + Math.random() * 400 });
    }
    for (var m = meteors.length - 1; m >= 0; m--) {
      var me = meteors[m];
      me.life += dt; me.x += me.vx * dt; me.y += me.vy * dt;
      var p = me.life / me.max;
      if (p >= 1) { meteors.splice(m, 1); continue; }
      var fade = Math.sin(p * Math.PI);
      var tx = me.x - me.vx * 140, ty = me.y - me.vy * 140;
      var g = ctx.createLinearGradient(me.x, me.y, tx, ty);
      g.addColorStop(0, 'rgba(255,255,255,' + fade + ')');
      g.addColorStop(0.3, 'rgba(170,160,255,' + fade * 0.5 + ')');
      g.addColorStop(1, 'rgba(71,191,255,0)');
      ctx.strokeStyle = g; ctx.lineWidth = 1.6; ctx.lineCap = 'round';
      ctx.beginPath(); ctx.moveTo(me.x, me.y); ctx.lineTo(tx, ty); ctx.stroke();
    }
    raf = requestAnimationFrame(frame);
  }
  raf = requestAnimationFrame(frame);
  return {
    stop: function () { cancelAnimationFrame(raf); window.removeEventListener('resize', resize); },
    warpOut: function () { if (!warpAt) warpAt = performance.now(); },
  };
}
