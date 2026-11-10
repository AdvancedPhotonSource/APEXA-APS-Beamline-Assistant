# Local adapter qualification, November 10, 2026

These are synthetic fixture checks on macOS arm64, not detector calibrations or
scientific benchmark outcomes. The model requested was `claudeopus5` through the
same configured ARGO service. Provider-reported model identity and usage matched
on the successful checks; this does not prove the service's internal routing.

| System | Installed version | Check | Result |
|---|---|---|---|
| Claude Code | 2.1.318 | Exact file copy | Passed, 5 requests |
| Claude Code | 2.1.318 | Native image read and content-dependent answer | Passed, 3 requests |
| Claude Code | 2.1.318 | Larger synthetic tool result and exact copy | Passed, 4 requests |
| Claude Code | 2.1.318 | Delayed native API observation | Passed, 8 requests |
| Pi | 1.2.0 | Native image read with image capability declared | Passed; final source check 3 requests |
| OpenCode | 1.18.23 | Native image read with original model config | Failed; no image delivered and no guessed answer |
| OpenCode | 1.18.23 | Native image read with attachment/input modalities declared | Passed; final source check 4 requests |

All nine attempts are retained (including development checks and the OpenCode
failure): eight passed, one failed, 39 model requests total. The table groups the
development and final-source repetitions. The final Pi and OpenCode checks used
the completed adapter code; their source files are archived with matching hashes.

The Pi and OpenCode repairs are explicit opt-ins in a new specification. They do
not change R5. Both systems sent the exact synthetic image bytes through their
native tool and model API, then wrote the correct randomly generated six-character
answer. The original OpenCode configuration let its tool open the image but
withheld it from the model as unsupported input. This is an adapter limitation,
not evidence that the underlying model cannot see images.

Claude's previously observed unsupported-endpoint rejection did not recur in any
of these local checks, including a deliberately longer-lived process. The old
relay did not preserve the rejected route, so its cause remains unresolved.
The updated relay logs sanitized method/path without query values or request
bodies. Unknown endpoints still invalidate protocol qualification. No R5
exclusion has been relabeled as a pass.

The focused regression suite passed **148 tests**, covering relay identity and
budgets, image evidence, frozen guidance, shared delivery, admission, containment,
result accounting and resumption. These tests and synthetic checks establish
preparation for a follow-up, not improved calibration completion.

Full receipts, prompts, native traces, model-call metadata, synthetic fixtures,
answers and checksums are kept in the canonical Box audit's
`guided_followup/qualification/` directory. The protocol and prepared ten-trial
schedule are adjacent. No new scientific trial has been launched from this plan.
