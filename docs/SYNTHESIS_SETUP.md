# In-situ synthesis XRD server — setup

`synthesis_server.py` exposes six tools (`synth_*`) for batch analysis of in-situ thermal
synthesis series: quick look, PCA, a global multiphase fit, Avrami/JMAK kinetics, an
advisory next-temperature step, and a refinement-visualization report. Methods are
I. A. Shkrob's (ANL CSE). Skill: `.agents/skills/insitu-synthesis/SKILL.md`.

## 1. The synthesis environment (`APEXA_SYNTH_PYTHON`)

The heavy stack stays out of APEXA's `.venv` (same route-to-native pattern as tomo and
GSAS-II):

```bash
uv venv --python 3.12 ~/.apexa/envs/synth
uv pip install --python ~/.apexa/envs/synth/bin/python \
    gemmi lmfit "numpy>=2" scipy pandas tables h5py matplotlib deap
export APEXA_SYNTH_PYTHON=~/.apexa/envs/synth/bin/python   # this path is the default
```

`deap` is needed only because the collaborator's `XRD_gemmi` imports it at module level.
`numpy>=2` is required (`np.trapezoid`, `np.asin`).

## 2. The fit engine (`APEXA_SYNTH_CODE`) — fit only

`synth_fit_series` calls the collaborator's `XRD_gemmi.Gemmi.fit_map_LM` **unmodified**.
His code is unreleased, so APEXA does not ship it; point at your copy:

```bash
export APEXA_SYNTH_CODE="/path/to/pre-package 5-1-2026"   # directory with XRD_gemmi.py
```

`XRD_gemmi.py` is the only one of his modules that imports headless (no tkinter, no
GSAS-II). The others are not imported; what APEXA needs from them is re-implemented in
`_synthesis_runner.py` with line citations. Inspect, PCA, recommend and kinetics work
without `APEXA_SYNTH_CODE`.

## 3. Register

`servers.config` already contains `synthesis:synthesis_server.py`. Restart APEXA.

## 4. Input format

A `processed_<sample>.h5` as written by his `save_processed_h5`:
root attrs = experiment parameters (incl. `lambda`); root datasets `d` (Å) and `t2`;
groups `scan0…scanN-1` each with dataset `y`; a pandas table at key `table` with `TC`
(°C) and `t` (s). CIFs are found case-insensitively and recursively under `cif_dir`
(default: the data directory).

## 5. Validation on Sample16 heating (APS, 46 scans, 40–701 °C)

| check | result |
|---|---|
| Recommender vs his `SVD_control_table` "TC step" | **exact**: 41/41 steps, max \|Δ\| = 0 |
| Quick look vs his `stats_Sample16.jpg` | same shape: peaks 11 → 3–5 (300–420 °C) → 23–27 (480–650 °C) → 13 |
| Fit input vs his saved `PC1.csv` | identical (1276 points, max \|Δ\| 2e-16) at d = 3.6–6.0 Å |
| Constrained fit vs his `constrained gemmi RR map` | χ² 1071.6 vs 1083.0; fractions within 0.04 / 0.03 / 0.07; NFPP cell differs (weakly determined) |
| Unconstrained fit vs his `gemmi RR map` | does **not** reproduce: χ² 1280 vs 871, `a1_0` railed — flagged LOOK |
| JMAK Na2FeP2O7 (n = 2) vs his Igor α2 | 32.8 ± 6.1 vs 30.5 kJ/mol |
| JMAK NFPP (n = 3) vs his Igor α1 | 77.5 ± 20 vs 25.5 kJ/mol — does not reproduce |

The fit runs the same code on the same input; residual differences are the optimizer
path (library versions — scipy rewrote `nnls` in 1.12). The report flags the
non-thermal NFPP lattice motion that appears in **both** his fit and ours.

## 6. Tests

```bash
uv run pytest tests/test_synthesis_runner.py tests/test_synthesis_server.py -q
```

The CLI round-trip test runs under `APEXA_SYNTH_PYTHON` and is skipped if that env is
absent. No collaborator data or code is used by the tests.
