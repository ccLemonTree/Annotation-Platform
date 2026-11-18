import { useState, type ReactNode } from 'react';
import type { ModelService } from '../../api/modelServices';
import type { AttributeSchema } from '../../api/objectAttributes';
import type {
  WorkflowAiGenerateResult,
  WorkflowDryRunNodeReport,
  WorkflowEdge,
  WorkflowOperation,
  WorkflowStep,
} from '../../api/projectWork';
import WorkflowCopilotBar from './WorkflowCopilotBar';
import WorkflowCanvas, { type InsertTarget } from './WorkflowCanvas';
import WorkflowNodeDrawer from './WorkflowNodeDrawer';
import { createDefaultStep, normalizeGraph } from './workflowGraph';

export default function WorkflowGraphBuilder({
  projectId,
  workflowName,
  steps,
  edges,
  models,
  inputLabel,
  attributeDefault,
  qualityDefault,
  attributes,
  attributeLabel,
  attributeRevision = 0,
  attributeAction,
  dryRunByNode,
  disabled,
  onChangeGraph,
}: {
  projectId?: string;
  workflowName: string;
  steps: WorkflowStep[];
  edges: WorkflowEdge[];
  models: ModelService[];
  inputLabel: string;
  attributeDefault?: string;
  qualityDefault?: string;
  attributes?: AttributeSchema;
  attributeLabel: string;
  attributeRevision?: number;
  attributeAction?: (stepIndex: number, category: boolean) => ReactNode;
  dryRunByNode?: Map<string, WorkflowDryRunNodeReport>;
  disabled?: boolean;
  onChangeGraph: (next: { name?: string; steps: WorkflowStep[]; edges: WorkflowEdge[] }) => void;
}) {
  const [selectedKey, setSelectedKey] = useState<string>();
  const [undoSnapshot, setUndoSnapshot] = useState<{ name: string; steps: WorkflowStep[]; edges: WorkflowEdge[] }>();

  const selectedIndex = steps.findIndex(s => s.node_key === selectedKey);
  const selectedStep = selectedIndex >= 0 ? steps[selectedIndex] : undefined;

  const handleInsert = (target: InsertTarget, operation: WorkflowOperation) => {
    const newStep = createDefaultStep(operation, steps, models);
    const nextSteps = [...steps, newStep];
    let nextEdges = [...edges];

    if (target.mode === 'entry' && target.targetKey) {
      // Materialize implicit entry edges first if none use __input__
      const hasExplicitInput = nextEdges.some(e => e.source === '__input__');
      if (!hasExplicitInput) {
        const indeg = new Map(steps.map(s => [s.node_key!, 0]));
        for (const e of nextEdges) {
          if (e.source !== '__input__') indeg.set(e.target, (indeg.get(e.target) || 0) + 1);
        }
        for (const s of steps) {
          if ((indeg.get(s.node_key!) || 0) === 0) {
            nextEdges.push({ source: '__input__', target: s.node_key!, source_handle: 'default' });
          }
        }
      }
      nextEdges = nextEdges.filter(e => !(e.source === '__input__' && e.target === target.targetKey));
      nextEdges.push({ source: '__input__', target: newStep.node_key!, source_handle: 'default' });
      nextEdges.push({ source: newStep.node_key!, target: target.targetKey, source_handle: 'default' });
    } else if (target.mode === 'between' && target.targetKey) {
      nextEdges = nextEdges.filter(
        e => !(e.source === target.sourceKey && e.target === target.targetKey && e.source_handle === target.sourceHandle),
      );
      nextEdges.push({ source: target.sourceKey, target: newStep.node_key!, source_handle: target.sourceHandle });
      nextEdges.push({ source: newStep.node_key!, target: target.targetKey, source_handle: 'default' });
    } else {
      nextEdges.push({ source: target.sourceKey, target: newStep.node_key!, source_handle: target.sourceHandle });
    }

    onChangeGraph({ steps: nextSteps, edges: nextEdges });
    setSelectedKey(newStep.node_key);
  };

  const handleUpdateStep = (updated: WorkflowStep) => {
    const nextSteps = steps.map(s => (s.node_key === updated.node_key ? updated : s));
    let nextEdges = edges;
    if (updated.operation === 'branch') {
      const validHandles = new Set([...(updated.branch_config?.branches || []).map(b => b.key), 'else']);
      nextEdges = edges.map(e =>
        e.source === updated.node_key && !validHandles.has(e.source_handle)
          ? { ...e, source_handle: 'else' }
          : e,
      );
    } else if (updated.operation !== 'script') {
      nextEdges = edges.map(e => (e.source === updated.node_key ? { ...e, source_handle: 'default' } : e));
    }
    onChangeGraph({ steps: nextSteps, edges: nextEdges });
  };

  const handleUpdateIncoming = (nodeKey: string, sources: { source: string; source_handle: string }[]) => {
    const hasExplicitInput = edges.some(e => e.source === '__input__') || sources.some(s => s.source === '__input__');
    let baseEdges = [...edges];
    if (hasExplicitInput && !edges.some(e => e.source === '__input__')) {
      const indeg = new Map(steps.map(s => [s.node_key!, 0]));
      for (const e of baseEdges) indeg.set(e.target, (indeg.get(e.target) || 0) + 1);
      for (const s of steps) {
        if (s.node_key !== nodeKey && (indeg.get(s.node_key!) || 0) === 0) {
          baseEdges.push({ source: '__input__', target: s.node_key!, source_handle: 'default' });
        }
      }
    }
    const remaining = baseEdges.filter(e => e.target !== nodeKey);
    const added = sources.map(src => ({ source: src.source, target: nodeKey, source_handle: src.source_handle }));
    onChangeGraph({ steps, edges: [...remaining, ...added] });
  };

  const handleDeleteNode = (delKey: string) => {
    if (steps.length <= 1) return;
    const incoming = edges.filter(e => e.target === delKey);
    const outgoing = edges.filter(e => e.source === delKey);
    const keptSteps = steps.filter(s => s.node_key !== delKey);
    const keptEdges = edges.filter(e => e.source !== delKey && e.target !== delKey);
    const bridged = [...keptEdges];

    const effectiveIn = incoming.length ? incoming : [{ source: '__input__', target: delKey, source_handle: 'default' }];
    for (const inE of effectiveIn) {
      for (const outE of outgoing) {
        if (inE.source === outE.target) continue;
        if (!bridged.some(e => e.source === inE.source && e.target === outE.target && e.source_handle === inE.source_handle)) {
          bridged.push({ source: inE.source, target: outE.target, source_handle: inE.source_handle });
        }
      }
    }
    setSelectedKey(undefined);
    onChangeGraph({ steps: keptSteps, edges: bridged });
  };

  const applyAiResult = (result: WorkflowAiGenerateResult) => {
    setUndoSnapshot({ name: workflowName, steps: structuredClone(steps), edges: structuredClone(edges) });
    const norm = normalizeGraph(result.steps, result.edges);
    setSelectedKey(undefined);
    onChangeGraph({ name: result.name, steps: norm.steps, edges: norm.edges });
  };

  const undoAi = () => {
    if (!undoSnapshot) return;
    setSelectedKey(undefined);
    onChangeGraph(undoSnapshot);
    setUndoSnapshot(undefined);
  };

  return (
    <div>
      <WorkflowCopilotBar
        projectId={projectId}
        models={models}
        currentWorkflow={{ name: workflowName, steps, edges, attribute_schema: attributes }}
        disabled={disabled}
        canUndo={!!undoSnapshot}
        onApply={applyAiResult}
        onUndo={undoAi}
      />

      <WorkflowCanvas
        steps={steps}
        edges={edges}
        models={models}
        inputLabel={inputLabel}
        selectedKey={selectedKey}
        dryRunByNode={dryRunByNode}
        readonly={disabled}
        onSelectNode={setSelectedKey}
        onInsertNode={handleInsert}
      />

      <WorkflowNodeDrawer
        key={selectedStep ? `${selectedStep.node_key}:${selectedStep.operation}` : 'empty'}
        open={!!selectedStep}
        step={selectedStep}
        stepIndex={selectedIndex}
        allSteps={steps}
        edges={edges}
        models={models}
        projectId={projectId}
        attributeDefault={attributeDefault}
        qualityDefault={qualityDefault}
        attributes={attributes}
        attributeLabel={attributeLabel}
        attributeRevision={attributeRevision}
        attributeAction={selectedIndex >= 0 && attributeAction ? cat => attributeAction(selectedIndex, cat) : undefined}
        disabled={disabled}
        onClose={() => setSelectedKey(undefined)}
        onUpdateStep={handleUpdateStep}
        onUpdateIncomingEdges={handleUpdateIncoming}
        onDeleteNode={handleDeleteNode}
      />
    </div>
  );
}
