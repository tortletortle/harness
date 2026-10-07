/**
 * 业务过程总览条：像迷你 Dify 流水线，点节点跳转对应视图。
 */
import { useAppStore } from '../stores/appStore';
import { derivePipelineStages, type PipelineStageState } from './pipelineStages';

const TASK_VIEWS = new Set(['input', 'design', 'plan', 'execution']);

function tone(state: PipelineStageState): string {
  switch (state) {
    case 'done':
      return 'border-emerald-400/35 bg-emerald-500/15 text-emerald-200';
    case 'current':
      return 'border-[var(--app-brand)]/50 bg-[var(--app-brand)]/15 text-white';
    case 'live':
      return 'border-blue-400/40 bg-blue-500/15 text-blue-100';
    case 'blocked':
      return 'border-amber-400/40 bg-amber-500/10 text-amber-100';
    case 'failed':
      return 'border-red-400/40 bg-red-500/15 text-red-100';
    default:
      return 'border-white/[0.1] bg-white/[0.03] text-zinc-500';
  }
}

export function PipelineOverview() {
  const currentView = useAppStore((s) => s.currentView);
  const inputText = useAppStore((s) => s.inputText);
  const design = useAppStore((s) => s.currentDesign);
  const plan = useAppStore((s) => s.currentPlan);
  const isExecuting = useAppStore((s) => s.isExecuting);
  const setCurrentView = useAppStore((s) => s.setCurrentView);

  if (!TASK_VIEWS.has(currentView)) return null;

  const stages = derivePipelineStages({
    currentView,
    inputText,
    design,
    plan,
    isExecuting,
  });

  return (
    <nav
      aria-label="任务过程"
      className="border-b border-white/[0.06] bg-black/20 px-4 py-2.5 sm:px-8"
    >
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-600">
        过程
      </div>
      <ol className="flex flex-wrap items-center gap-1.5 sm:gap-0">
        {stages.map((stage, index) => (
          <li key={stage.id} className="flex items-center gap-1.5 sm:gap-0">
            {index > 0 && (
              <span
                className="mx-1 hidden h-px w-4 bg-white/15 sm:inline-block sm:w-6"
                aria-hidden
              />
            )}
            <button
              type="button"
              title={stage.hint || stage.label}
              onClick={() => setCurrentView(stage.view)}
              className={`inline-flex max-w-[9rem] flex-col items-start rounded-lg border px-2.5 py-1.5 text-left transition-colors hover:brightness-110 ${tone(stage.state)}`}
            >
              <span className="text-[11px] font-semibold leading-none">{stage.label}</span>
              {stage.hint && (
                <span className="mt-1 truncate text-[9px] opacity-80">{stage.hint}</span>
              )}
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}
