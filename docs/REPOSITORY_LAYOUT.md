# Repository layout and local outputs

| Location | Purpose |
| --- | --- |
| Root `apexa_*.py`, `*_server.py` and runners | Importable application modules and configured entry points. Their current paths remain part of launch/MCP compatibility. |
| `frontend/src`, `frontend/tests` | React application and UI regression checks. |
| `frontend/public` | Shared static assets, including the APEXA SVG mark. |
| `frontend/dist` | Committed production assets for hosts without Node. Rebuild when frontend sources change. |
| `tests` | Automated Python tests and explicitly versioned fixtures. |
| `scripts` | Operational and development commands. |
| `docs` | User guides, deployment instructions and development reference. |
| `docs/audits` | Dated findings and validation evidence; historical reports are not current deployment claims. |
| `benchmark` | Versioned benchmark definitions and qualified references; large generated datasets/results are ignored. |
| `knowledge_base` | Retrieval implementation and shipped reference material/index. A locally changed Chroma database needs separate review before publication. |
| `scratch` | Ignored local working files and archives. |

Write new analysis outputs to an explicitly chosen dataset/run directory. Use
`scratch/` for disposable experiments and `~/.apexa` for application state.
Source data, credentials, application packages, downloaded papers, generated
viewers, runtime job directories and research manuscripts do not belong in a
source commit. Explicit reference fixtures under `tests/` and `benchmark/` are
the reviewed exception.

## Cleanup on 30 October 2026

The loose generated CIF viewer and unrelated application-package output were
preserved under `scratch/local-artifacts/2026-10-30/`. The ignored
`manifest.json` there records their original and current paths. This was an
archive move; no scientific data was deleted. Ignore rules now also cover those
root output paths, local reference papers and `runinfo/`.

Existing beamtime datasets, parameter files, downloaded structures, manuscripts,
operator configuration and runtime directories remain at their established
paths. Moving the root Python modules requires an import/entry-point migration;
that is separate from archiving generated files and is not performed by cleanup.

Before a commit, inspect `git status --short` and `git diff --cached --stat`.
Stage source, tests, documentation and the matching production build explicitly.
Avoid blanket deletion commands: ignored directories may contain irreplaceable
beamtime work. See [quality gates](development/quality-gates.md).
