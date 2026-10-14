import type { ToolResult } from '@/api/types'
import { CalibrationCard } from './CalibrationCard'
import { FileListCard } from './FileListCard'
import { XrayCalcCard } from './XrayCalcCard'
import { WorkflowCard } from './WorkflowCard'
import { IntegrationCard } from './IntegrationCard'
import { ErrorCard } from './ErrorCard'
import { GenericCard } from './GenericCard'
import { ToolCardChrome } from './ToolCardChrome'

/** Bare card with no chrome — used inside the Canvas (already an artifact). */
export function cardFor(result: ToolResult) {
  switch (result.tool.split(':').pop()) {
    case 'midas_auto_calibrate':
    case 'run_ff_calibration':
      return <CalibrationCard result={result} />

    case 'xray_calculate':
      return <XrayCalcCard result={result} />

    case 'list_directory':
      return <FileListCard result={result} />

    case 'midas_integrate_2d_to_1d':
    case 'midas_batch_integrate':
      return <IntegrationCard result={result} />

    case 'run_ff_hedm_full_workflow':
    case 'run_nf_hedm_reconstruction':
    case 'run_pf_hedm_workflow':
    case 'hedm_workflow':
      return <WorkflowCard result={result} />

    default:
      return <GenericCard result={result} />
  }
}

export function ToolResultCard({ result }: { result: ToolResult }) {
  // Errors render bare (no "open in Canvas" — nothing to inspect/reproduce).
  if (result.status === 'error' || result.status === 'failed') {
    return <ErrorCard result={result} />
  }

  // Every successful tool result gets shared chrome: provenance + open-in-Canvas.
  return <ToolCardChrome result={result}>{cardFor(result)}</ToolCardChrome>
}
