/**
 * 产物视图（1.18 Artifact Store 的展示层）。
 *
 * 列出产物元数据（文件 / 测试报告 / 交付报告），点击预览已入库内容；
 * 交付报告单独高亮置顶。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { FileText, Loader2, Package, RefreshCw, ShieldCheck } from 'lucide-react';
import { apiClient } from '../api/client';
import { useAppStore } from '../stores/appStore';
import type { Artifact } from '../types';

const KIND_LABELS: Record<string, { label: string; tone: string }> = {
  delivery_report: { label: '交付报告', tone: 'border-violet-400/25 bg-violet-400/10 text-violet-200' },
  desktop_screenshot: { label: '窗口截图', tone: 'border-sky-400/25 bg-sky-400/10 text-sky-200' },
  file: { label: '文件', tone: 'border-sky-400/25 bg-sky-400/10 text-sky-200' },
  test_report: { label: '测试报告', tone: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200' },
};

function kindMeta(kind: string) {
  return KIND_LABELS[kind] ?? { label: kind, tone: 'border-white/[0.1] bg-white/[0.05] text-zinc-300' };
}

function formatSize(size?: number | null): string {
  if (size == null) return '—';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function ArtifactsView() {
  const { addLog } = useAppStore();
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [planFilter, setPlanFilter] = useState('');
  const [preview, setPreview] = useState<{ artifact: Artifact; content: string; imageUrl?: string; external?: boolean } | null>(null);
  const [copied, setCopied] = useState(false);
  const [previewLoading, setPreviewLoading] = useState<string | null>(null);

  const previewRequest = useRef(0);
  const imageUrl = useRef<string | null>(null);
  const releaseImage = () => {
    if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
    imageUrl.current = null;
  };
  useEffect(() => () => { previewRequest.current += 1; releaseImage(); }, []);
  const closePreview = () => {
    previewRequest.current += 1;
    releaseImage();
    setPreview(null);
    setPreviewLoading(null);
  };

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setArtifacts(await apiClient.getArtifacts(planFilter || undefined));
    } catch (err) {
      const message = `获取产物列表失败: ${err instanceof Error ? err.message : String(err)}`;
      setError(message);
      addLog({ type: 'log', level: 'error', message });
    } finally {
      setLoading(false);
    }
  }, [planFilter, addLog]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openPreview = async (artifact: Artifact) => {
    const requestId = ++previewRequest.current;
    setPreviewLoading(artifact.artifact_id);
    try {
      if (artifact.kind === 'desktop_screenshot') {
        const blob = await apiClient.getArtifactImage(artifact.artifact_id);
        if (requestId !== previewRequest.current) return;
        releaseImage();
        const url = URL.createObjectURL(blob);
        imageUrl.current = url;
        setPreview({ artifact, content: '', imageUrl: url });
      } else {
        const content = await apiClient.getArtifactContent(artifact.artifact_id);
        if (requestId !== previewRequest.current) return;
        releaseImage();
        setPreview({ artifact, content });
      }
    } catch (err) {
      if (requestId !== previewRequest.current) return;
      if (!artifact.content_hash) {
        // 仅登记了外部引用（内容未入库）：展示文件位置，而不是只报错
        releaseImage();
        setCopied(false);
        setPreview({ artifact, content: '', external: true });
        return;
      }
      const message = `读取产物内容失败: ${err instanceof Error ? err.message : String(err)}`;
      addLog({ type: 'log', level: 'error', message });
    } finally {
      if (requestId === previewRequest.current) setPreviewLoading(null);
    }
  };

  const reports = artifacts.filter((artifact) => artifact.kind === 'delivery_report');
  const others = artifacts.filter((artifact) => artifact.kind !== 'delivery_report');

  const renderRow = (artifact: Artifact) => {
    const meta = kindMeta(artifact.kind);
    const label = artifact.kind === 'desktop_screenshot' && typeof artifact.metadata?.window_title === 'string'
      ? `窗口截图 · ${artifact.metadata.window_title}`
      : artifact.uri.replace(/^artifact:\/\//, '').replace(/^file:\/\//, '');
    return (
      <button
        key={artifact.artifact_id}
        onClick={() => void openPreview(artifact)}
        className="flex w-full items-center gap-3 rounded-xl border border-white/[0.08] bg-[#111419]/90 px-4 py-3 text-left transition-colors hover:bg-white/[0.03]"
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/[0.05] text-zinc-400">
          {artifact.kind === 'delivery_report' ? <ShieldCheck className="h-4 w-4 text-violet-300" /> : <FileText className="h-4 w-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-sm font-medium text-zinc-100">{label}</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] ${meta.tone}`}>{meta.label}</span>
            {!artifact.content_hash && <span className="rounded-full border border-zinc-600/40 px-2 py-0.5 text-[10px] text-zinc-500" title="内容未入库，只记录了文件位置">外部引用</span>}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-[11px] text-zinc-600">
            <span className="font-mono">{artifact.artifact_id.slice(0, 8)}</span>
            <span>{formatSize(artifact.size_bytes)}</span>
            {artifact.plan_id && <span className="font-mono">plan {artifact.plan_id.slice(0, 8)}</span>}
            {artifact.created_at && <span>{artifact.created_at.slice(0, 19).replace('T', ' ')}</span>}
          </div>
        </div>
        {previewLoading === artifact.artifact_id && <Loader2 className="h-4 w-4 shrink-0 animate-spin text-violet-300" />}
      </button>
    );
  };

  return (
    <div className="min-h-full px-4 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-5xl space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-white">产物库</h2>
            <p className="mt-1 text-xs text-zinc-500">步骤与 Agent 产生的文件、报告与交付总结；点击预览已入库内容（SHA-256 校验过的存储）。</p>
          </div>
          <div className="flex items-center gap-2">
            <input
              value={planFilter}
              onChange={(event) => setPlanFilter(event.target.value)}
              placeholder="按 plan_id 过滤（留空显示全部）"
              aria-label="按 plan_id 过滤产物"
              className="w-64 rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-2 font-mono text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-violet-400/40 focus:outline-none"
            />
            <button
              onClick={() => void refresh()}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-xs font-semibold text-zinc-200 transition-colors hover:bg-white/[0.08] disabled:opacity-40"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              刷新
            </button>
          </div>
        </div>

        {error && (
          <div role="alert" className="rounded-lg border border-red-400/20 bg-red-400/[0.08] px-3 py-2 text-xs text-red-200">{error}</div>
        )}

        {!loading && artifacts.length === 0 && !error && (
          <div className="rounded-xl border border-dashed border-white/[0.12] bg-white/[0.02] p-6 text-center text-sm text-zinc-400">
            还没有产物。执行计划后，步骤生成的文件与交付报告会自动登记在这里。
          </div>
        )}

        {reports.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-violet-300">
              <ShieldCheck className="h-3.5 w-3.5" />交付报告
            </div>
            {reports.map(renderRow)}
          </div>
        )}

        {others.length > 0 && (
          <div className="space-y-2">
            {reports.length > 0 && (
              <div className="flex items-center gap-2 pt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-600">
                <Package className="h-3.5 w-3.5" />步骤产物
              </div>
            )}
            {others.map(renderRow)}
          </div>
        )}

        {preview && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 px-4 backdrop-blur-sm" role="dialog" aria-label="产物内容预览">
            <div className="flex max-h-[80vh] w-full max-w-3xl flex-col rounded-2xl border border-white/[0.1] bg-[#171a20] shadow-2xl">
              <div className="flex shrink-0 items-center justify-between gap-3 border-b border-white/[0.08] px-5 py-4">
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-semibold text-white">{preview.artifact.uri.replace(/^artifact:\/\//, 'artifact://')}</h3>
                  <p className="mt-0.5 text-[11px] text-zinc-500">
                    {kindMeta(preview.artifact.kind).label}
                    {preview.artifact.content_hash && (
                      <span className="ml-2 inline-flex items-center gap-1"><ShieldCheck className="h-3 w-3 text-emerald-400" />SHA-256 已登记</span>
                    )}
                  </p>
                </div>
                <button onClick={closePreview} className="rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-1.5 text-xs text-zinc-300 transition-colors hover:bg-white/[0.08]">关闭</button>
              </div>
              {preview.imageUrl ? (
                <div className="min-h-0 flex-1 overflow-auto p-5">
                  <p className="mb-3 text-xs text-amber-200">只读窗口截图 · 未自动遮挡隐私 · 图片来自已校验的产物副本</p>
                  <img src={preview.imageUrl} alt="窗口截图" className="mx-auto max-w-full rounded object-contain" />
                </div>
              ) : preview.external ? (
                <div className="min-h-0 flex-1 overflow-auto p-5 text-sm text-zinc-300">
                  <p className="text-xs text-amber-200">这个产物只登记了文件位置，内容没有存进数据库，所以无法在这里预览。</p>
                  <p className="mt-4 text-[11px] text-zinc-500">文件位置</p>
                  <div className="mt-1 flex items-center gap-2">
                    <code className="min-w-0 flex-1 break-all rounded-lg border border-white/[0.08] bg-black/30 px-3 py-2 font-mono text-[12px] text-zinc-200">{preview.artifact.uri.replace(/^file:\/\//, '')}</code>
                    <button
                      type="button"
                      onClick={() => {
                        void navigator.clipboard?.writeText(preview.artifact.uri.replace(/^file:\/\//, '')).then(() => setCopied(true), () => setCopied(false));
                      }}
                      className="shrink-0 rounded-lg border border-white/[0.1] bg-white/[0.04] px-3 py-2 text-xs text-zinc-300 transition-colors hover:bg-white/[0.08]"
                    >{copied ? '已复制' : '复制路径'}</button>
                  </div>
                  <p className="mt-3 text-[11px] text-zinc-600">如果文件所在的临时目录已被清理，这个文件可能已经不存在。</p>
                </div>
              ) : <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-5 font-mono text-[12px] leading-6 text-zinc-300">{preview.content}</pre>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
