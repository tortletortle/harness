/**
 * 星舵 Helm 开屏动画
 * 星空跃迁 → 舵轮绘制 → 五个阶段节点依次点亮（需求/设计/审批/计划/执行）→ 品牌名 → 淡出
 * - 每个浏览器会话只播放一次（sessionStorage）
 * - 点击或按任意键跳过
 * - 系统开启“减少动态效果”时直接跳过
 * - 跟随 data-theme 亮/暗主题（使用 index.css 中的 --app-* 变量）
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import './SplashScreen.css';
import { buildHelmSvg, HELM_STAGES as stages } from './helmWheel';
import { startStarfield, type Starfield } from './starfield';

const SESSION_KEY = 'eas-splash-shown';
const TOTAL_MS = 2800;
const EXIT_MS = 850;


export function shouldShowSplash(): boolean {
  if (typeof window === 'undefined') return false;
  if (sessionStorage.getItem(SESSION_KEY)) return false;
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return false;
  return true;
}

export function SplashScreen({ onDone }: { onDone: () => void }) {
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    sessionStorage.setItem(SESSION_KEY, '1');
    const t1 = window.setTimeout(() => setLeaving(true), TOTAL_MS);
    const t2 = window.setTimeout(onDone, TOTAL_MS + EXIT_MS);
    const skip = () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      setLeaving(true);
      window.setTimeout(onDone, EXIT_MS);
    };
    window.addEventListener('keydown', skip, { once: true });
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.removeEventListener('keydown', skip);
    };
  }, [onDone]);

  const wheel = useMemo(() => buildHelmSvg(), []);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fieldRef = useRef<Starfield | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !canvas.getContext('2d')) return; // jsdom 等无 canvas 环境直接跳过
    const field = startStarfield(canvas);
    fieldRef.current = field;
    return () => { field.stop(); fieldRef.current = null; };
  }, []);

  useEffect(() => {
    if (leaving) fieldRef.current?.warpOut();
  }, [leaving]);

  return (
    <div
      className={`helm-splash ${leaving ? 'is-leaving' : ''}`}
      role="status"
      aria-label="星舵 Helm 正在启动"
      onClick={() => { setLeaving(true); window.setTimeout(onDone, EXIT_MS); }}
    >
      <div className="helm-splash__nebula" />
      <div className="helm-splash__milky" />
      <canvas ref={canvasRef} className="helm-splash__stars" />
      <div className="helm-splash__glow" />

      <div className="helm-splash__center">
        <div className="helm-splash__wheel-wrap" dangerouslySetInnerHTML={{ __html: wheel }} />

        <div className="helm-splash__stages" aria-hidden="true">
          {stages.map((s, i) => (
            <span key={s} style={{ animationDelay: `${1.1 + i * 0.18}s` }}>
              {s}{i < stages.length - 1 && <i>→</i>}
            </span>
          ))}
        </div>

        <h1 className="helm-splash__title">
          <span className="helm-splash__title-cn">星舵</span>
          <span className="helm-splash__title-en">Helm</span>
        </h1>
        <p className="helm-splash__subtitle">AI 任务编排与执行系统</p>

        <div className="helm-splash__bar"><span /></div>
      </div>

      <div className="helm-splash__hint">点击任意处跳过</div>
    </div>
  );
}
