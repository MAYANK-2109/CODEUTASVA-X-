import React, { useState } from 'react'

// ---------------------------------------------------------------------------
// Types: the trail the backend sends with every assistant answer
// ---------------------------------------------------------------------------
export interface TrailEvidence {
  id: string
  claim: string
  source: string
}

// One agent's part in the analysis: how it worked, what it found, what it relied on.
export interface TrailStep {
  agent: string
  title: string
  how: string
  result: string
  evidence: string[]
  uses: string[]
  notes: string[]
}

// The rule behind the hedge decision and the comparison that settled it.
export interface TrailDecision {
  action: 'hedge' | 'monitor' | 'no_hedge' | 'none'
  rule: string
  comparison: string
  sizing: string[]
  evidence: string[]
}

interface AgentRun {
  status: 'done' | 'degraded'
  ms: number
}

const AGENT_LABELS: Record<string, string> = {
  supervisor: 'Supervisor',
  sentiment: 'Sentiment',
  weather_macro: 'Weather / Macro',
  historical: 'Historical',
  risk: 'Risk',
  hedging: 'Hedging',
  synthesiser: 'Synthesiser',
}

const DECISION_LABELS: Record<TrailDecision['action'], { text: string; className: string }> = {
  hedge: { text: 'Hedge recommended', className: 'bg-red-50 text-red-600' },
  monitor: { text: 'Monitor', className: 'bg-amber-50 text-amber-700' },
  no_hedge: { text: 'No hedge needed', className: 'bg-groww-green-light text-groww-green' },
  none: { text: 'No hedge sized', className: 'bg-gray-100 text-groww-text-secondary' },
}

const duration = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`)

const EvidenceTags: React.FC<{ label: string; ids: string[]; byId: Map<string, TrailEvidence> }> = ({
  label,
  ids,
  byId,
}) =>
  ids.length === 0 ? null : (
    <p className="mt-1 flex flex-wrap items-center gap-1 text-xs text-groww-text-muted">
      <span className="font-semibold">{label}</span>
      {ids.map((id) => (
        <span
          key={id}
          title={byId.get(id)?.claim}
          className="px-1.5 rounded bg-groww-green-light text-groww-green text-[11px] font-semibold cursor-help"
        >
          {id}
        </span>
      ))}
    </p>
  )

// One step: how the agent worked, its result, and the evidence rows it produced.
const TrailStepItem: React.FC<{
  step: TrailStep
  index: number
  last: boolean
  run: AgentRun | undefined
  byId: Map<string, TrailEvidence>
}> = ({ step, index, last, run, byId }) => {
  const [showRows, setShowRows] = useState(false)
  const found = step.evidence.map((id) => byId.get(id)).filter((item): item is TrailEvidence => Boolean(item))
  return (
    <li className="relative flex gap-2.5 pb-3.5 last:pb-0">
      {!last && <span className="absolute left-[10px] top-6 bottom-0 w-px bg-groww-border" aria-hidden />}
      <span
        className={`relative shrink-0 w-[21px] h-[21px] rounded-full text-white text-[11px] font-bold flex items-center justify-center ${
          run?.status === 'degraded' ? 'bg-amber-500' : 'bg-groww-green'
        }`}
      >
        {index + 1}
      </span>
      <div className="min-w-0 flex-1 text-xs leading-relaxed">
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-semibold text-groww-text-primary">{step.title}</span>
          <span className="ml-auto shrink-0 text-[11px] text-groww-text-muted tabular-nums">
            {AGENT_LABELS[step.agent] ?? step.agent} agent{run ? ` · ${duration(run.ms)}` : ''}
          </span>
        </p>
        {run?.status === 'degraded' && (
          <p className="text-xs font-semibold text-amber-700">Ran with missing data; see the notes under the answer.</p>
        )}
        <p className="text-groww-text-secondary">
          <span className="font-semibold">How: </span>
          {step.how}
        </p>
        <p className="mt-0.5 text-groww-text-primary">
          <span className="font-semibold">Result: </span>
          {step.result}
        </p>
        {step.notes.length > 0 && (
          <ul className="mt-1 flex flex-col gap-0.5 text-xs text-groww-text-secondary">
            {step.notes.map((note, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="mt-[7px] w-1 h-1 rounded-full bg-groww-text-muted shrink-0" />
                <span>{note}</span>
              </li>
            ))}
          </ul>
        )}
        <EvidenceTags
          label={step.agent === 'synthesiser' ? 'Cited in the answer' : 'Relied on'}
          ids={step.uses}
          byId={byId}
        />
        {found.length > 0 && (
          <>
            <button
              onClick={() => setShowRows((value) => !value)}
              aria-expanded={showRows}
              className="mt-1 text-xs font-semibold text-groww-green hover:text-groww-green-dark"
            >
              {showRows ? 'Hide' : 'Show'} the {found.length} evidence row{found.length === 1 ? '' : 's'} it produced (
              {found[0].id}
              {found.length > 1 ? ` to ${found[found.length - 1].id}` : ''})
            </button>
            {showRows && (
              <ul className="mt-1.5 flex flex-col gap-1 text-xs">
                {found.map((item) => (
                  <li key={item.id} className="rounded-lg bg-groww-bg-primary px-2.5 py-1.5">
                    <span className="font-semibold text-groww-green">{item.id}</span> {item.claim}
                    <span className="block text-groww-text-muted">Source: {item.source}</span>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </li>
  )
}

// ---------------------------------------------------------------------------
// Evidence trail: why the assistant recommends what it does, step by step.
// Open by default; shown wherever an assistant answer is shown.
// ---------------------------------------------------------------------------
const EvidenceTrail: React.FC<{
  trail?: TrailStep[]
  decision?: TrailDecision | null
  evidence: TrailEvidence[]
  steps: Record<string, AgentRun>
  defaultOpen?: boolean
}> = ({ trail, decision, evidence, steps, defaultOpen = true }) => {
  const [open, setOpen] = useState(defaultOpen)
  if (!trail?.length) return null
  const byId = new Map(evidence.map((item) => [item.id, item]))
  return (
    <section className="evidence-trail rounded-xl border border-groww-border overflow-hidden" aria-label="Evidence trail">
      <button
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 bg-groww-bg-primary text-left hover:bg-groww-green-light/60 transition-colors"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-groww-green" aria-hidden>
          <path d="M9 6h11M9 12h11M9 18h11" />
          <circle cx="4" cy="6" r="1.2" />
          <circle cx="4" cy="12" r="1.2" />
          <circle cx="4" cy="18" r="1.2" />
        </svg>
        <span className="min-w-0">
          <span className="block text-xs font-bold text-groww-text-primary">Evidence trail</span>
          <span className="block text-xs text-groww-text-muted">
            Why this was recommended, in {trail.length} steps from {evidence.length} evidence rows
          </span>
        </span>
        <span className="ml-auto shrink-0 text-xs font-semibold text-groww-green">{open ? 'Hide' : 'Show'}</span>
      </button>

      {open && (
        <div className="px-3 py-3 flex flex-col gap-3 bg-white">
          {decision && (
            <div className="rounded-xl bg-groww-green-pale border border-groww-green/30 px-3 py-2.5 text-xs leading-relaxed">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[11px] font-bold uppercase tracking-wide text-groww-text-muted">Why this recommendation</p>
                <span className={`px-1.5 py-0.5 rounded-full text-[11px] font-semibold ${DECISION_LABELS[decision.action].className}`}>
                  {DECISION_LABELS[decision.action].text}
                </span>
              </div>
              <p className="mt-1 font-semibold text-groww-text-primary">{decision.comparison}</p>
              <p className="mt-1 text-groww-text-secondary">
                <span className="font-semibold">The rule: </span>
                {decision.rule}
              </p>
              {decision.sizing.map((line, i) => (
                <p key={i} className="mt-1 text-groww-text-secondary">
                  <span className="font-semibold">How it was sized: </span>
                  {line}
                </p>
              ))}
              <EvidenceTags label="Rests on" ids={decision.evidence} byId={byId} />
            </div>
          )}
          <ol className="flex flex-col">
            {trail.map((step, index) => (
              <TrailStepItem
                key={step.agent}
                step={step}
                index={index}
                last={index === trail.length - 1}
                run={steps[step.agent]}
                byId={byId}
              />
            ))}
          </ol>
        </div>
      )}
    </section>
  )
}

export default EvidenceTrail
