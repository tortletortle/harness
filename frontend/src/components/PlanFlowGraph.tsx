/**
 * Dify 风格：执行计划步骤画布（只读，状态随计划刷新）。
 */
import { useMemo, useCallback, memo, useEffect } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  useReactFlow,
  type Node,
  type Edge,
  type NodeProps,
  ReactFlowProvider,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { CheckCircle, Clock, Loader2, MinusCircle, XCircle } from 'lucide-react';
import type { Step, StepStatus } from '../types';
import { FLOW_NODE_SIZE, stepsToFlowLayout, type FlowNodeData } from './planFlowLayout';

function statusClasses(status: StepStatus): string {
  switch (status) {
    case 'Running':
    case 'Retry':
      return 'border-blue-400/50 bg-blue-500/10 shadow-[0_0_0_1px_rgba(96,165,250,0.25)]';
    case 'Success':
      return 'border-emerald-400/40 bg-emerald-500/10';
    case 'Failed':
      return 'border-red-400/50 bg-red-500/10';
    case 'Skipped':
      return 'border-zinc-500/30 bg-zinc-500/10 opacity-70';
    default:
      return 'border-white/[0.12] bg-[#14181f]';
  }
}

function StatusIcon({ status }: { status: StepStatus }) {
  const cls = 'h-3.5 w-3.5 shrink-0';
  switch (status) {
    case 'Running':
    case 'Retry':
      return <Loader2 className={`${cls} animate-spin text-blue-300`} />;
    case 'Success':
      return <CheckCircle className={`${cls} text-emerald-300`} />;
    case 'Failed':
      return <XCircle className={`${cls} text-red-300`} />;
    case 'Skipped':
      return <MinusCircle className={`${cls} text-zinc-500`} />;
    default:
      return <Clock className={`${cls} text-zinc-500`} />;
  }
}

function StepFlowNode({ data, selected }: NodeProps<Node<FlowNodeData>>) {
  return (
    <div
      className={`w-[220px] rounded-xl border px-3 py-2.5 shadow-lg shadow-black/30 transition-colors ${statusClasses(data.status)} ${
        selected ? 'ring-2 ring-[var(--app-brand)]/60' : ''
      }`}
    >
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-zinc-500" />
      <div className="flex items-start gap-2">
        <StatusIcon status={data.status} />
        <div className="min-w-0 flex-1">
          <div className="line-clamp-2 text-xs font-medium leading-snug text-zinc-100">{data.label}</div>
          <div className="mt-1.5 flex items-center justify-between gap-2">
            <span className="helm-chip max-w-[140px] truncate rounded-md px-1.5 py-0.5 text-[10px]">
              {data.tool}
            </span>
            {data.order != null && (
              <span className="font-mono text-[10px] text-zinc-600">
                {String(data.order).padStart(2, '0')}
              </span>
            )}
          </div>
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="!h-2 !w-2 !border-0 !bg-zinc-500" />
    </div>
  );
}

const nodeTypes = { step: memo(StepFlowNode) };

type PlanFlowGraphProps = {
  steps: Step[];
  onSelectStep?: (stepId: string | null) => void;
  className?: string;
};

function PlanFlowGraphInner({ steps, onSelectStep, className }: PlanFlowGraphProps) {
  const layout = useMemo(() => stepsToFlowLayout(steps), [steps]);
  const { fitView } = useReactFlow();

  useEffect(() => {
    const id = window.setTimeout(() => {
      void fitView({ padding: 0.2, duration: 200 });
    }, 50);
    return () => window.clearTimeout(id);
  }, [layout.nodes.length, layout.edges.length, fitView]);

  const nodes: Node<FlowNodeData>[] = useMemo(
    () =>
      layout.nodes.map((n) => ({
        id: n.id,
        type: 'step',
        position: n.position,
        data: n.data,
        style: { width: FLOW_NODE_SIZE.width },
        draggable: false,
        selectable: true,
      })),
    [layout.nodes],
  );

  const runningIds = useMemo(
    () => new Set(steps.filter((s) => s.status === 'Running' || s.status === 'Retry').map((s) => s.step_id)),
    [steps],
  );

  const edges: Edge[] = useMemo(
    () =>
      layout.edges.map((e) => {
        const live = runningIds.has(e.target);
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          type: 'smoothstep',
          animated: live,
          style: {
            stroke: live ? 'rgba(96,165,250,0.75)' : 'rgba(148,163,184,0.45)',
            strokeWidth: live ? 2 : 1.5,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 16,
            height: 16,
            color: live ? 'rgba(96,165,250,0.85)' : 'rgba(148,163,184,0.55)',
          },
        };
      }),
    [layout.edges, runningIds],
  );

  const onSelectionChange = useCallback(
    ({ nodes: selected }: { nodes: Node[] }) => {
      onSelectStep?.(selected[0]?.id ?? null);
    },
    [onSelectStep],
  );

  if (steps.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-white/[0.1] p-8 text-center text-sm text-zinc-500">
        暂无步骤可展示
      </div>
    );
  }

  return (
    <div className={className ?? 'h-[min(70vh,560px)] w-full overflow-hidden rounded-xl border border-white/[0.08] bg-[#0a0c10]'}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.35}
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        panOnScroll
        zoomOnScroll
        proOptions={{ hideAttribution: true }}
        onSelectionChange={onSelectionChange}
        defaultEdgeOptions={{ type: 'smoothstep' }}
      >
        <Background gap={20} size={1} color="rgba(255,255,255,0.04)" />
        <Controls
          showInteractive={false}
          className="!overflow-hidden !rounded-lg !border !border-white/10 !bg-[#14181f] !shadow-none [&>button]:!border-white/10 [&>button]:!bg-transparent [&>button]:!fill-zinc-300"
        />
        <MiniMap
          pannable
          zoomable
          className="!overflow-hidden !rounded-lg !border !border-white/10 !bg-[#14181f]"
          maskColor="rgba(0,0,0,0.55)"
          nodeColor={() => 'rgba(45,212,191,0.55)'}
        />
      </ReactFlow>
    </div>
  );
}

export function PlanFlowGraph(props: PlanFlowGraphProps) {
  return (
    <ReactFlowProvider>
      <PlanFlowGraphInner {...props} />
    </ReactFlowProvider>
  );
}
