import type { ToolResult } from '@/api/types'
import { CalibrationCard } from './CalibrationCard'
import { FileListCard } from './FileListCard'
import { XrayCalcCard } from './XrayCalcCard'
import { WorkflowCard } from './WorkflowCard'
import { IntegrationCard } from './IntegrationCard'
import { ErrorCard } from './ErrorCard'
import { GenericCard } from './GenericCard'
import { ToolCardChrome } from './ToolCardChrome'
import { RefinementCard } from './RefinementCard'
import { DynamicsCard } from './DynamicsCard'
import { BackgroundCard } from './BackgroundCard'

/** Bare card with no chrome — used inside the Canvas (already an artifact). */
export function ToolResultBody({ result }: { result: ToolResult }) {
  switch (result.tool.split(':').pop()) {
    case 'midas_auto_calibrate':
    case 'run_ff_calibration':
      return ['success', 'completed'].includes(result.status)
        ? <CalibrationCard result={result} /> : <GenericCard result={result} />

    case 'xray_calculate':
      return <XrayCalcCard result={result} />

    case 'list_directory':
      return <FileListCard result={result} />

    case 'midas_integrate_2d_to_1d':
    case 'midas_batch_integrate':
      return <IntegrationCard result={result} />

    case 'refine_pattern':
    case 'plan_refinement':
    case 'assess_refinement':
    case 'refine_series_submit':
    case 'powder_refine_submit':
    case 'refinement_status':
    case 'powder_evidence':
    case 'powder_advise':
    case 'powder_trial':
    case 'powder_live_ingest':
    case 'powder_session_create':
    case 'powder_session_status':
    case 'powder_session_evidence':
    case 'powder_session_control':
    case 'powder_adopt':
    case 'powder_improve':
    case 'powder_strategy_sweep_submit':
    case 'powder_compare':
    case 'gsas2_tutorials':
    case 'gsas2_capabilities':
    case 'gsas2_workflow_submit':
    case 'gsas2_workflow_status':
    case 'gsas2_workflow_cancel':
      return <RefinementCard result={result} />

    case 'track_diffraction_peak_series':
      return <DynamicsCard result={result} />

    case 'combine_detector_backgrounds':
      return <BackgroundCard result={result} />

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
  return <ToolCardChrome result={result}><ToolResultBody result={result} /></ToolCardChrome>
}
