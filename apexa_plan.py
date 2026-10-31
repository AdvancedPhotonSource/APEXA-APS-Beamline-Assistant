"""APEXA analysis-plan schema — a reviewable, reproducible checkpoint.

Inspired by the ``plan/job_*.yaml`` stage in the reflectometry ``nr-analyzer``
pipeline (context.md -> plan -> model -> fit). The idea adapted here: before an
agent executes an expensive or irreversible beamline/analysis action, it first
emits a **structured, human-readable plan** that the scientist can read, edit,
and re-run — decoupled from the LLM.

Two things this buys APEXA that a direct ``TOOL_CALL`` does not:

  1. **Human-in-the-loop review** — the plan is a diff-able YAML artifact that
     can gate execution (``perform_execution``) on expensive runs.
  2. **Reproducibility + provenance** — every inferred default is recorded in
     ``assumptions`` with the *reason* it was chosen, so a run can be reproduced
     (and audited) without re-invoking the model. This mirrors the
     ``metadata.notes`` field the reflectometry planner writes
     ("field defaulted to 0.0 from context (not explicitly specified)").

The schema is deliberately technique-general (FF/NF/PF-HEDM, WAXS, SAXS, plain
integration) — a superset with optional blocks, not one struct per technique.

Round-trips through YAML:

    plan = APEXAPlan(technique="waxs", sample=Sample(name="JL_0Nb"))
    plan.assume("dark_kind", "after", "no explicit dark timing in request")
    plan.to_yaml("APEXA_plan.yaml")
    same = APEXAPlan.from_yaml("APEXA_plan.yaml")

This module has NO heavy dependencies (stdlib + PyYAML) so it can be imported by
the MCP servers, the acquisition watcher, or a subprocess without pulling in the
agent stack.
"""
from __future__ import annotations

import dataclasses as _dc
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

SCHEMA_VERSION = "0.1"

# Techniques APEXA can plan for. Kept as a flat set of strings (not an enum) so
# the YAML stays human-writable and forward-compatible.
TECHNIQUES = (
    "ff-hedm", "nf-hedm", "pf-hedm",   # 3D microstructure
    "waxs", "saxs", "integration",     # azimuthal integration / scattering
    "calibration", "powder-refinement", # detector calibration and powder models
)


@dataclass
class Assumption:
    """One inferred value and *why* — the provenance unit of a plan."""
    field: str
    value: Any
    reason: str


@dataclass
class Sample:
    name: str = ""
    material: str = ""                       # e.g. "Ni", "Ti-6Al-4V"
    space_group: str = ""                    # e.g. "Fm-3m"
    lattice: dict = field(default_factory=dict)   # {a,b,c,alpha,beta,gamma} in Å/deg
    expected_phases: list = field(default_factory=list)
    # Optional structural/analysis hypotheses to consider, à la reflectometry
    # context.md ("there may be a NiO surface layer"). For HEDM these might be
    # "secondary phase X", "strong texture", "residual strain".
    hypotheses: list = field(default_factory=list)


@dataclass
class Beam:
    energy_keV: Optional[float] = None
    wavelength_A: Optional[float] = None


@dataclass
class Detector:
    name: str = ""
    distance_mm: Optional[float] = None
    pixel_size_um: Optional[float] = None
    beam_center_px: list = field(default_factory=list)   # [x, y]
    tilt_deg: list = field(default_factory=list)         # [tx, ty] or [tilt, rot]


@dataclass
class Calibration:
    calibrant: str = ""                      # e.g. "CeO2", "LaB6"
    parameter_file: str = ""                 # MIDAS params written/consumed


@dataclass
class DataSpec:
    """WHERE the data is and HOW MANY frames make a complete measurement.

    Paths are kept deliberately loose (bare ``image_dir`` + ``pattern``) so a
    plan is portable between the beamline and the analysis cluster — the
    reflectometry planners do the same (data location injected at run time).
    """
    image_dir: str = ""
    pattern: str = "*.h5"
    stem: str = ""                           # sample/scan stem grouping frames
    data_location: str = ""                  # HDF5 dataset path, e.g. "exchange/data"
    expected_count: Optional[int] = None     # frames needed for a COMPLETE set
    exclude_substring: str = "dark"
    images: list[str] = field(default_factory=list)  # exact acquisition snapshot


@dataclass
class DarkSpec:
    source: str = "file"                     # file | embedded | none
    kind: str = "after"                      # after | before | any
    dir: str = ""
    pattern: str = "*dark*"
    location: str = ""                       # HDF5 path for a separate dark (exchange/data)


@dataclass
class Grid:
    """Output integration grid — specify in whatever convention you use."""
    r_min: Optional[float] = None
    r_max: Optional[float] = None
    r_bin_size: Optional[float] = None
    two_theta_min: Optional[float] = None
    two_theta_max: Optional[float] = None
    q_min: Optional[float] = None
    q_max: Optional[float] = None
    n_channels: Optional[int] = None
    eta_min: Optional[float] = None
    eta_max: Optional[float] = None
    eta_bin_size: Optional[float] = None


@dataclass
class Compute:
    target: str = "auto"                     # auto | local-cpu | local-gpu | remote-gpu
    n_cpus: int = 8
    machine: str = ""                        # parsl machine (e.g. polaris)
    n_nodes: Optional[int] = None
    shard_gpus: Optional[bool] = None


@dataclass
class PowderRefinement:
    recipe: dict = field(default_factory=dict)
    refine_atoms: bool = False
    bkg_terms: int = 6
    max_wall_seconds: float = 900.0
    run_lebail_companion: bool = True
    mode: str = "independent"
    observations: list[dict] = field(default_factory=list)
    max_steps: int = 100
    workers: int = 2


@dataclass
class APEXAPlan:
    """A complete, reviewable analysis plan. YAML is the canonical form."""
    technique: str = "integration"
    instrument: str = ""
    describe: str = ""                        # one-line human summary
    sample: Sample = field(default_factory=Sample)
    beam: Beam = field(default_factory=Beam)
    detector: Detector = field(default_factory=Detector)
    calibration: Calibration = field(default_factory=Calibration)
    data: DataSpec = field(default_factory=DataSpec)
    dark: DarkSpec = field(default_factory=DarkSpec)
    grid: Grid = field(default_factory=Grid)
    compute: Compute = field(default_factory=Compute)
    refinement: PowderRefinement = field(default_factory=PowderRefinement)
    result_folder: str = ""

    # Provenance: everything the planner INFERRED rather than was told.
    assumptions: list = field(default_factory=list)   # list[Assumption]
    # Execution gate — set False to require human review before anything runs.
    perform_execution: bool = False
    schema_version: str = SCHEMA_VERSION
    notes: str = ""

    # ── provenance helper ────────────────────────────────────────────────────
    def assume(self, field_name: str, value: Any, reason: str) -> "APEXAPlan":
        """Record an inferred default and return self (chainable)."""
        self.assumptions.append(Assumption(field=field_name, value=value, reason=reason))
        return self

    # ── validation ───────────────────────────────────────────────────────────
    def validate(self) -> list:
        """Return a list of human-readable problems (empty == ready to run)."""
        issues = []
        if self.technique not in TECHNIQUES:
            issues.append(f"unknown technique {self.technique!r} (expected one of {TECHNIQUES})")
        needs_params = self.technique in ("waxs", "saxs", "integration", "ff-hedm", "pf-hedm")
        if needs_params:
            if not self.calibration.parameter_file:
                issues.append("calibration.parameter_file is required for integration/HEDM")
            if not self.data.image_dir and not self.data.stem:
                issues.append("data: need image_dir or stem")
        if self.technique == "powder-refinement":
            if self.refinement.mode not in ("independent", "sequential", "joint", "search"):
                issues.append("invalid powder refinement mode")
            if not self.refinement.observations:
                issues.append("refinement.observations are required")
            for i, o in enumerate(self.refinement.observations):
                if not all(o.get(k) for k in ("data_path", "instrument_path", "cif_paths")):
                    issues.append(f"observation {i} requires data_path, instrument_path and cif_paths")
            return issues
        # Energy is only required when it can't be read from a parameter file
        # (integration/HEDM carry energy in the MIDAS params; calibration needs it up front).
        if (self.beam.energy_keV is None and self.beam.wavelength_A is None
                and not (needs_params and self.calibration.parameter_file)):
            issues.append("beam: need energy_keV or wavelength_A")
        if self.dark.source not in ("file", "embedded", "none"):
            issues.append(f"dark.source {self.dark.source!r} not in file|embedded|none")
        return issues

    # ── YAML I/O ─────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return _dc.asdict(self)

    def to_yaml(self, path: str | None = None) -> str:
        text = yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True,
                              default_flow_style=False, width=88)
        if path:
            with open(path, "w") as fh:
                fh.write(text)
        return text

    @classmethod
    def from_dict(cls, d: dict) -> "APEXAPlan":
        d = dict(d or {})
        sub = {
            "sample": Sample, "beam": Beam, "detector": Detector,
            "calibration": Calibration, "data": DataSpec, "dark": DarkSpec,
            "grid": Grid, "compute": Compute, "refinement": PowderRefinement,
        }
        kw: dict = {}
        for key, klass in sub.items():
            if key in d and isinstance(d[key], dict):
                # keep only known fields (forward-compatible with extra keys)
                names = {f.name for f in _dc.fields(klass)}
                kw[key] = klass(**{k: v for k, v in d[key].items() if k in names})
        kw["assumptions"] = [
            Assumption(**a) if isinstance(a, dict) else a
            for a in d.get("assumptions", [])
        ]
        for scalar in ("technique", "instrument", "describe", "result_folder",
                       "perform_execution", "schema_version", "notes"):
            if scalar in d:
                kw[scalar] = d[scalar]
        return cls(**kw)

    @classmethod
    def from_yaml(cls, path: str) -> "APEXAPlan":
        with open(path) as fh:
            return cls.from_dict(yaml.safe_load(fh))

    # ── bridge to the executor ───────────────────────────────────────────────
    def to_powder_refine_kwargs(self) -> dict:
        return {"observations": self.refinement.observations, "mode": self.refinement.mode,
                "max_steps": self.refinement.max_steps, "workers": self.refinement.workers,
                "recipe": self.refinement.recipe, "refine_atoms": self.refinement.refine_atoms,
                "bkg_terms": self.refinement.bkg_terms, "max_wall_seconds": self.refinement.max_wall_seconds,
                "run_lebail_companion": self.refinement.run_lebail_companion,
                "output_dir": self.result_folder or "powder_refinement"}

    def to_integrate_series_kwargs(self) -> dict:
        """Project the plan onto ``midas_integrate_series`` arguments.

        Only the fields that tool understands; unset values are dropped so the
        tool's own defaults apply. This is the seam an executor/watcher calls.
        """
        kw = {
            "parameter_file": self.calibration.parameter_file,
            "images": self.data.images or None,
            "image_dir": self.data.image_dir or None,
            "pattern": self.data.pattern,
            "exclude_substring": self.data.exclude_substring,
            "data_location": self.data.data_location or None,
            "dark_source": self.dark.source,
            "dark_kind": self.dark.kind,
            "dark_dir": self.dark.dir or None,
            "dark_pattern": self.dark.pattern,
            "dark_location": self.dark.location or None,
            "result_folder": self.result_folder or None,
            "n_cpus": self.compute.n_cpus,
            "compute_target": self.compute.target,
            "r_min": self.grid.r_min, "r_max": self.grid.r_max,
            "r_bin_size": self.grid.r_bin_size,
            "eta_min": self.grid.eta_min, "eta_max": self.grid.eta_max,
            "eta_bin_size": self.grid.eta_bin_size,
            "two_theta_min": self.grid.two_theta_min,
            "two_theta_max": self.grid.two_theta_max,
            "q_min": self.grid.q_min, "q_max": self.grid.q_max,
            "n_channels": self.grid.n_channels,
        }
        return {k: v for k, v in kw.items() if v is not None}


if __name__ == "__main__":
    # Self-demo: build a WAXS plan, log an assumption, round-trip through YAML.
    p = APEXAPlan(
        technique="waxs",
        instrument="1-ID",
        describe="Azimuthal integration of JL_0Nb WAXS series, CeO2-calibrated.",
        sample=Sample(name="JL_0Nb", material="Ni-alloy", expected_phases=["FCC"]),
        beam=Beam(energy_keV=61.332),
        calibration=Calibration(calibrant="CeO2", parameter_file="ceria_params.txt"),
        data=DataSpec(image_dir="/scratch/beam/JL_0Nb", pattern="JL_0Nb_*.vrx.h5",
                      stem="JL_0Nb", data_location="exchange/data", expected_count=180),
        dark=DarkSpec(source="file", kind="after", location="exchange/data"),
    )
    p.assume("dark_kind", "after", "no explicit dark timing given; beamline default is trailing dark")
    p.assume("compute.target", "auto", "cost = frames×MP below remote threshold")
    print(p.to_yaml())
    print("validate() ->", p.validate() or "OK")
