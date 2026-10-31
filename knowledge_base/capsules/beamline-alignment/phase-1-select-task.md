> Runtime boundary: only `diode_max_1d_scan` simulation/replay/read-only shadow is implemented. Hardware and Bayesian-optimization guidance below is future commissioning context, not an executable procedure. Do not dispatch motor commands or bypass a broker refusal. See [README.md](README.md) and `docs/ALIGNMENT_RUNTIME.md`.

# Phase 1 — Select the alignment task

## Goal

Match the operator's natural-language request to exactly one task spec in the
library (`knowledge_base/alignment_tasks/*.yaml`). If no task fits, **refuse**
rather than compose one on the fly.

## Procedure

1. **Call `list_alignment_tasks` first.** Even if you think you know what's
   available — the library is the source of truth, Python memory isn't.

2. **Match the operator phrase to a task name.** The current library (4 tasks):

   | Operator phrase pattern | Task |
   |---|---|
   | "find rotation axis", "cor from tomo", "align sample stage" | `rotation_axis_from_tomo` |
   | "center the beam on the beamstop", "SAXS alignment", "pixirad centering" | `saxs_beam_center_on_beamstop` |
   | "tune slits for IC2", "slit alignment", "get IC2 to typical" | `slit_tune_for_ic_target` |
   | "find the hotspot", "max diode", "1D scan on motor X" | `diode_max_1d_scan` |

3. **If no task matches, refuse.** Return:
   ```json
   {"status": "no_matching_task",
    "suggestion": "Add a new YAML to knowledge_base/alignment_tasks/ or "
                  "describe which part of the existing templates to clone."}
   ```
   DO NOT invent a VOCS spec from thin air. Alignment tasks live in version
   control; one-shot inventions aren't reproducible.

4. **Confirm the resolved task to the operator** before advancing to phase 2:
   - Task name
   - Decision variables (motors)
   - Objective + direction (MINIMIZE / MAXIMIZE)
   - Safety constraints
   - Algorithm choice

## Refusal cases

- No matching task → return `no_matching_task` (above).
- Multiple ambiguous matches → ask the operator to pick.
- Task matched but required argument (e.g. `tomo_h5` for rotation_axis) is
  missing → return `missing_argument` and name the field.

## Successful phase-1 exit

```json
{"phase": 1, "task": "<name>", "task_spec": {...}, "advance": "phase-2-warm-start"}
```
