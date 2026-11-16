"""Declared calibration contracts and live native execution receipts.

This is a workflow consistency boundary, not an OS security boundary or an
independent physical reference. Historical handoffs retain their supplied-evidence
scope; edited files cannot acquire the provenance of a new native computation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import uuid


P_NAMES = ('a2', 'a4', 'iso_R2', 'phi4', 'iso_R6', 'iso_R4', 'phi2',
           'a1', 'phi1', 'a3', 'phi3', 'a5', 'phi5', 'a6', 'phi6')
LIMITS = [
    'The contract contains supplied acquisition facts; their physical truth is not independently established.',
    'Image agreement and nominal-distance consistency do not establish absolute geometry accuracy; the distance tolerance is a decision policy.',
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError(f'Nonfinite JSON constant: {value}')
    return json.loads(Path(path).read_text(), object_pairs_hook=unique, parse_constant=invalid)


def number(value, name, *, positive=True):
    if type(value) not in (int, float) or not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError(f'{name} must be a finite {"positive" if positive else "nonnegative"} number')
    return value


def parameters(path):
    wanted = {'px', 'pxY', 'pxZ', 'Lsd', 'Wavelength', 'SpaceGroup', 'LatticeConstant',
              *(f'p{i}' for i in range(15))}
    result = {}
    for line in Path(path).read_text().splitlines():
        fields = line.split('#', 1)[0].split()
        if not fields or fields[0] not in wanted:
            continue
        key = fields[0]
        values = [float(x) for x in fields[1:]]
        if len(values) != (6 if key == 'LatticeConstant' else 1) or not all(map(math.isfinite, values)):
            raise ValueError(f'Incomplete or nonfinite parameter {key}')
        value = values if key == 'LatticeConstant' else values[0]
        if key in result and result[key] != value:
            raise ValueError(f'Conflicting parameter {key}')
        result[key] = value
    return result


def normalize(document):
    if not isinstance(document, dict):
        raise ValueError('Calibration contract must be a JSON object')
    doc = deepcopy(document)
    if doc.get('schema_version') != 1:
        raise ValueError('Calibration contract requires schema_version 1')
    facts = doc['facts']
    if not isinstance(facts, dict):
        raise ValueError('Calibration facts must be a JSON object')
    for key in ('pixel_pitch_um', 'nominal_distance_mm', 'lattice_angstrom', 'energy_kev'):
        number(facts[key], key)
    if type(facts['space_group']) is not int or not 195 <= facts['space_group'] <= 230:
        raise ValueError('This scalar-lattice contract supports single cubic phases (space groups 195–230)')
    frames = facts['frames']
    if (not isinstance(frames, list) or not frames or any(type(x) is not int or x < 0 for x in frames)
            or frames != list(range(frames[0], frames[0] + len(frames)))):
        raise ValueError('Frames must be an explicit contiguous ascending list of nonnegative indices')
    if facts['frame_reduction'] not in ('mean', 'median'):
        raise ValueError('Unsupported frame reduction')
    transform = facts['image_transform']
    if type(transform) is int:
        transform = [transform]
    if not isinstance(transform, list) or not transform or any(type(x) is not int or x not in range(4) for x in transform):
        raise ValueError('image_transform must contain MIDAS codes 0–3')
    facts['image_transform'] = transform
    if not isinstance(facts['data_loc'], str) or not facts['data_loc'].strip('/'):
        raise ValueError('An explicit detector dataset path is required')
    if doc['requested_model'] not in ('full', 'radial', 'none'):
        raise ValueError('requested_model must be full, radial or none')
    if doc['pipeline'] not in ('tilted', 'four-stage'):
        raise ValueError('Unsupported calibration pipeline')
    if doc['pipeline'] == 'tilted' and doc['requested_model'] != 'full':
        raise ValueError('Explicit restrictions require the four-stage pipeline')
    if number(doc['distance_policy_fraction'], 'distance policy') >= 1:
        raise ValueError('Distance policy must be a fraction below one')
    number(doc['zero_tolerance'], 'zero tolerance', positive=False)
    return doc


def phase_matches(par, facts):
    expected = [facts['lattice_angstrom']] * 3 + [90.] * 3
    return (par.get('SpaceGroup') == facts['space_group'] and
            len(par.get('LatticeConstant', [])) == 6 and
            all(abs(a-b) <= 1e-6 for a, b in zip(par['LatticeConstant'], expected)))


def frozen_keys(doc):
    keep = set(range(15)) if doc['requested_model'] == 'full' else ({2, 4, 5} if doc['requested_model'] == 'radial' else set())
    return {f'p{i}': name for i, name in enumerate(P_NAMES) if i not in keep}


def evaluate(doc, path, run, execution, *, native=False):
    """Check complete supplied facts; provenance is recorded separately by caller."""
    checks, errors = {}, []
    try:
        facts, par = doc['facts'], parameters(path)
        frame = run['frame_selection']
        pitches = [par[k] for k in ('px', 'pxY', 'pxZ') if k in par]
        number(par['Lsd'], 'Lsd')
        number(par['Wavelength'], 'Wavelength')
        effective_pitch = execution['effective_arguments']['px_um'] if native else execution['effective_pixel_pitch_um']
        number(effective_pitch, 'effective pixel pitch')
        checks['input'] = (bool(pitches) and all(x == facts['pixel_pitch_um'] for x in pitches)
            and effective_pitch == facts['pixel_pitch_um'] and phase_matches(par, facts)
            and abs(par['Wavelength'] - 12.398419843320026 / facts['energy_kev']) <= 5.1e-7
            and frame['frame_indices'] == facts['frames']
            and frame['frame_reduce'] == facts['frame_reduction']
            and frame['data_loc'].strip('/') == facts['data_loc'].strip('/')
            and run['im_trans_applied'] == facts['image_transform'])
        if native:
            effective = execution['effective_arguments']
            checks['input'] &= (str(Path(run['image_file']).resolve()) == effective['image_file']
                and str(Path(frame['source']).resolve()) == effective['image_file'])
            if doc['pipeline'] == 'four-stage' and 'four_stage' not in run.get('engine', ''):
                raise ValueError('Native execution does not identify the contracted four-stage engine')
            if doc['pipeline'] == 'tilted' and 'tilted' not in run.get('engine', ''):
                raise ValueError('Native execution does not identify the contracted tilted engine')
        checks['nominal_distance'] = abs(par['Lsd']/1000 - facts['nominal_distance_mm']) / facts['nominal_distance_mm'] <= doc['distance_policy_fraction']
        gate = run['gate']
        if type(gate['passed']) is not bool:
            raise ValueError('Image gate must contain a boolean passed field')
        if doc['pipeline'] == 'four-stage':
            metric = number(gate['stage4_strain_uE_test'], 'held-out strain', positive=False)
            threshold = number(gate['threshold_uE'], 'strain threshold')
            checks['image_gate'] = gate['passed'] and metric < threshold
        else:
            number(gate['median_px'], 'ring residual', positive=False)
            if type(gate['n_fits']) is not int or type(gate['n_cells']) is not int or not 0 < gate['n_fits'] <= gate['n_cells']:
                raise ValueError('Invalid ring support counts')
            checks['image_gate'] = gate['passed'] and gate['verdict'] == 'PASS'
        frozen = frozen_keys(doc)
        checks['restriction'] = True
        if frozen:
            exported_ok = all(abs(par[key]) <= doc['zero_tolerance'] for key in frozen)
            if native:
                verification = run['distortion_verification']
                values = verification['checked_values']
                for name in frozen.values():
                    if type(values[name]) not in (float, int) or not math.isfinite(values[name]):
                        raise ValueError('Incomplete fitted restriction evidence')
                stages = run['constraint_execution']
                flags_ok = bool(stages) and all(all(s['after_thaw_refine_flags'][name] is False for name in frozen.values()) for s in stages)
                execution_ok = (verification['status'] == 'verified' and
                    all(abs(values[name]) <= doc['zero_tolerance'] for name in frozen.values()) and flags_ok
                    and run['refine_distortion'] == doc['requested_model'])
            else:
                stages = execution['stages']
                if not stages:
                    raise ValueError('No executed stage evidence')
                execution_ok = True
                for stage in stages:
                    flags = stage['before_refine_flags']
                    if set(flags) != set(P_NAMES) or any(type(v) is not bool for v in flags.values()):
                        raise ValueError('Incomplete stage flags')
                    execution_ok &= all(flags[name] is False for name in frozen.values())
            checks['restriction'] = exported_ok and execution_ok
    except (KeyError, ValueError, TypeError, OSError) as exc:
        errors.append(str(exc))
    return {'checks': checks, 'errors': errors, 'complete': not errors,
            'all_satisfied': not errors and len(checks) == 4 and all(checks.values())}


def load_contract(contract_path, image_file, template_param_file,
                  candidate_parameters_file='', candidate_run_file='', candidate_execution_file=''):
    paths = {k: str(Path(v).expanduser().resolve()) for k, v in {
        'contract': contract_path, 'image': image_file, 'template': template_param_file,
        'candidate': candidate_parameters_file, 'run': candidate_run_file,
        'execution': candidate_execution_file}.items() if v}
    for path in paths.values():
        if not Path(path).is_file():
            raise ValueError(f'Contract input is not a file: {path}')
    doc = normalize(read_json(paths['contract']))
    if not phase_matches(parameters(paths['template']), doc['facts']):
        raise ValueError('Template phase conflicts with the declared fitted phase')
    if any(k in paths for k in ('candidate', 'run', 'execution')) and not all(k in paths for k in ('candidate', 'run', 'execution')):
        raise ValueError('An existing handoff needs parameters, run record and execution evidence together')
    hashes = {k: sha(v) for k, v in paths.items() if k != 'image'}
    bundle = {'contract': doc, 'paths': paths, 'sha256': hashes, 'scope_limits': LIMITS}
    if 'candidate' in paths:
        bundle['handoff_checks'] = evaluate(doc, paths['candidate'], read_json(paths['run']), read_json(paths['execution']))
    return bundle


class CalibrationContract:
    """One query's pinned contract and native receipts, owned by the client."""
    def __init__(self, bundle):
        self.bundle = deepcopy(bundle)
        self.doc = normalize(bundle['contract'])
        self.receipts = {}
        self.assert_sources()

    def assert_sources(self):
        for key, digest in self.bundle['sha256'].items():
            if sha(self.bundle['paths'][key]) != digest:
                raise ValueError(f'Pinned {key} file changed since contract loading')

    def bind(self, arguments):
        self.assert_sources()
        proposed, facts = deepcopy(arguments), self.doc['facts']
        bound = {**arguments, 'image_file': self.bundle['paths']['image'],
            'template_param_file': self.bundle['paths']['template'], 'parameters_file': '',
            'calibration_engine': 'v2', 'calibration_pipeline': self.doc['pipeline'],
            'skip_frame': facts['frames'][0], 'frame_count': len(facts['frames']),
            'frame_reduce': facts['frame_reduction'], 'image_transform': ' '.join(map(str, facts['image_transform'])),
            'data_loc': facts['data_loc'], 'px_um': facts['pixel_pitch_um'],
            'energy_kev': facts['energy_kev'], 'wavelength_angstrom': 0.,
            'refine_distortion': self.doc['requested_model'], 'ignore_calibration_gate': False}
        # A dark is a separate acquisition fact, not an inferred correction.
        if arguments.get('dark_file'):
            raise ValueError('A dark-file change needs a separate contract; this contract declares no dark')
        if arguments.get('host'):
            raise ValueError('Contract receipt validation currently requires local native execution')
        if self.doc['pipeline'] == 'tilted':
            bound.update(device='cpu', lsd_tol_um=0., tol_tilts_deg=0.)
        if not bound.get('output_dir'):
            raise ValueError('Contract-bound calibration requires an explicit new output directory')
        output = Path(bound['output_dir']).resolve()
        # Never overwrite a handoff or a prior execution before inspecting its result.
        if output.exists() and any(output.iterdir()):
            raise ValueError('Use an empty new output directory for each native computation')
        changes = {k: {'proposed': proposed[k], 'bound': v} for k, v in bound.items() if k in proposed and proposed[k] != v}
        return bound, {'proposed_arguments': proposed, 'effective_arguments': deepcopy(bound),
                       'corrected_arguments': changes, 'binding_source': 'declared_contract_not_model_reasoning'}

    def capture(self, result, binding):
        self.assert_sources()
        if result.get('status') != 'success' or not result.get('artifact_path'):
            return {'registered': False, 'reason': 'Native tool did not return a successful artifact', **binding}
        path = str(Path(result['artifact_path']).resolve())
        digest = sha(path)
        if digest != result.get('artifact_sha256'):
            raise ValueError('Native artifact hash disagrees with returned bytes')
        if Path(path).parent != Path(binding['effective_arguments']['output_dir']).resolve():
            raise ValueError('Native artifact lies outside the contracted output directory')
        checks = evaluate(self.doc, path, result, binding, native=True)
        record = {'schema_version': 1, 'contract_sha256': self.bundle['sha256']['contract'],
            'origin': 'live_native_dispatch', 'artifact_path': path, 'artifact_sha256': digest,
            **binding, 'checks': checks,
            'native_result': {k: v for k, v in result.items() if k not in ('handbook_rules', 'suggestion')},
            'scope_limits': LIMITS}
        receipt_path = Path(path).parent / f'APEXA_contract_execution_{uuid.uuid4().hex}.json'
        with receipt_path.open('x') as stream:
            json.dump(record, stream, indent=2, allow_nan=False)
            stream.write('\n')
        record['receipt_path'] = str(receipt_path)
        record['receipt_sha256'] = sha(receipt_path)
        self.receipts[path] = record
        return {'registered': True, 'receipt_path': str(receipt_path), 'checks': checks, **binding}

    def finalize(self, arguments):
        self.assert_sources()
        args = deepcopy(arguments)
        details = args.get('result_details') or {}
        proposed = deepcopy(details)
        if not isinstance(details, dict):
            raise ValueError('Contract finalization requires result_details')
        assessments = details.get('assessments') or {}
        if not isinstance(assessments, dict) or any(not isinstance(v, dict) for v in assessments.values()):
            raise ValueError('assessments must map check names to assessment objects')
        restriction = assessments.get('restriction') or {}
        if self.doc['requested_model'] == 'full' and restriction.get('status') == 'violated':
            raise ValueError('Full model has no zero-distortion restriction. Revise the unsupported restriction assessment.')
        decision = details.get('final_decision')
        if decision not in ('release_conditionally', 'repaired_and_release_conditionally', 'hold', 'unassessed'):
            raise ValueError('Declare final_decision: release_conditionally, repaired_and_release_conditionally, hold or unassessed')
        handoff = self.bundle.get('handoff_checks')
        if handoff is not None:
            initial_decision = details.get('handoff_decision')
            if initial_decision not in ('release_conditionally', 'hold', 'unassessed'):
                raise ValueError('Declare handoff_decision for the ORIGINAL artifact separately: release_conditionally, hold or unassessed')
            if initial_decision == 'release_conditionally' and not handoff['all_satisfied']:
                raise ValueError(f'The original handoff cannot be released; its evidence is {handoff}. A successful repair does not change its original status.')
        root = Path(args.get('workspace_root') or Path.cwd()).resolve()
        def relative(path):
            return Path(path).resolve().relative_to(root).as_posix()
        verification = {'contract_sha256': self.bundle['sha256']['contract'], 'scope_limits': LIMITS,
                        'activation_source': getattr(self, 'activation_source', 'model_tool_call'),
                        'original_handoff_checks': handoff,
                        'agent_proposed_summary': args.get('summary', '')}
        if decision in ('hold', 'unassessed'):
            if args.get('scientific_status') == 'accepted' or args.get('artifact_path') or details.get('adopted_artifact'):
                raise ValueError('A hold/unassessed decision cannot adopt an artifact or be accepted')
            if not details.get('reason'):
                raise ValueError('A hold/unassessed decision needs a specific reason')
            details.update(adopted_artifact=None, repair_evidence=None)
        else:
            if args.get('scientific_status') != 'provisional':
                raise ValueError('A conditional release must use scientific_status="provisional"')
            adopted = details.get('adopted_artifact') or {}
            value = args.get('artifact_path') or adopted.get('path')
            if not value:
                raise ValueError('A conditional release needs an adopted parameter artifact')
            path = str((root / value).resolve())
            relative(path)
            record = self.receipts.get(path)
            if record:
                if sha(path) != record['artifact_sha256'] or sha(record['receipt_path']) != record['receipt_sha256']:
                    raise ValueError('Native artifact or execution receipt changed after dispatch')
                checks = record['checks']
                details['final_decision'] = 'repaired_and_release_conditionally'
                details['repair_evidence'] = {'command_or_tool': 'midas_auto_calibrate',
                    'parameters_path': relative(path), 'run_record_path': relative(record['receipt_path']),
                    'execution_record_path': relative(record['receipt_path'])}
                verification.update(origin='live_native_dispatch', execution_receipt_sha256=record['receipt_sha256'])
                args['artifact_paths'] = list(dict.fromkeys([*(args.get('artifact_paths') or []), relative(record['receipt_path'])]))
            elif path == self.bundle['paths'].get('candidate'):
                if decision == 'repaired_and_release_conditionally':
                    raise ValueError('The supplied handoff is not a new native repair')
                checks = self.bundle['handoff_checks']
                details['repair_evidence'] = None
                verification['origin'] = 'supplied_handoff_not_live_execution'
            else:
                raise ValueError('Unregistered artifact: edited/copied files cannot stand in for a live native repair')
            if not checks['all_satisfied']:
                raise ValueError(f'Conditional release blocked by contract evidence: {checks}')
            for name, assessment in assessments.items():
                if name in checks['checks'] and assessment.get('status') == 'violated':
                    raise ValueError(f'Assessment {name} contradicts the adopted artifact evidence')
            args['artifact_path'] = path
            details['adopted_artifact'] = {'path': relative(path), 'sha256': sha(path)}
            verification['checks'] = checks
            if record:
                run = record['native_result']
                evidence_paths = [relative(path), relative(record['receipt_path'])]
            else:
                run = read_json(self.bundle['paths']['run'])
                evidence_paths = [relative(self.bundle['paths'][k]) for k in ('candidate', 'run', 'execution')]
            report = self.report(path, run, evidence_paths)
            # These fields describe the adopted evidence. The complete original
            # agent proposal remains below for audit, including unsupported prose.
            details['assessments'] = report['assessments']
            details['reason'] = report['summary']
            args['summary'] = report['summary']
            verification['reported_values'] = report['values']
            verification['reporting_scope'] = 'Generated from the adopted artifact and recorded gate; agent prose and proposed extra fields are not certified.'
        limits = details.get('remaining_limits') or []
        if not isinstance(limits, list) or any(not isinstance(x, str) for x in limits):
            raise ValueError('remaining_limits must be an array of strings')
        # Agent-added limits can themselves assert false facts (e.g. "coefficients
        # stayed zero" on a full-model fit). Preserve them in agent_proposed_result,
        # while the authoritative scope field contains only the enforced limits.
        details['remaining_limits'] = list(LIMITS)
        details['contract_verification'] = verification
        details['agent_proposed_result'] = proposed
        args.update(result_details=details, workspace_root=str(root))
        return args

    def report(self, artifact_path, run, evidence_paths):
        """A factual report for an already-verified conditional release."""
        par, facts = parameters(artifact_path), self.doc['facts']
        frame, gate = run['frame_selection'], run['gate']
        distance = par['Lsd']/1000
        discrepancy = abs(distance-facts['nominal_distance_mm'])/facts['nominal_distance_mm']
        nonzero = sum(abs(par[f'p{i}']) > self.doc['zero_tolerance'] for i in range(15))
        if self.doc['pipeline'] == 'four-stage':
            gate_text = (f"Held-out strain {gate['stage4_strain_uE_test']:.6g} microstrain "
                         f"is below {gate['threshold_uE']:.6g} microstrain.")
            gate_kind = 'held_out_strain'
        else:
            gate_text = (f"Ring-position gate {gate['verdict']}: median residual {gate['median_px']:.6g} pixels, "
                         f"{gate['n_fits']}/{gate['n_cells']} fitted cells.")
            gate_kind = 'ring_position'
        model_text = (f"Requested model: {self.doc['requested_model']}; {nonzero}/15 exported distortion coefficients are nonzero.")
        if self.doc['requested_model'] == 'full':
            model_text += ' No zero-distortion restriction was requested.'
        else:
            model_text += ' The recorded constraint checks passed.'
        input_text = (f"Frames {frame['frame_indices'][0]}–{frame['frame_indices'][-1]}, "
                      f"{frame['frame_reduce']} reduction, transform {run['im_trans_applied']}; "
                      f"pixel pitch {facts['pixel_pitch_um']:.6g} um and the fitted phase match the supplied facts.")
        distance_text = (f"Fitted distance {distance:.9g} mm versus nominal {facts['nominal_distance_mm']:.9g} mm; "
                         f"relative discrepancy {100*discrepancy:.6g}% is within the {100*self.doc['distance_policy_fraction']:.6g}% policy.")
        texts = {'input': input_text, 'restriction': model_text,
                 'image_gate': gate_text, 'nominal_distance': distance_text}
        return {'summary': 'Conditional use supported by the declared checks. ' + ' '.join(texts.values()) +
                          ' Absolute geometry accuracy is not established.',
                'assessments': {k: {'status': 'satisfied', 'reason': text,
                                    'evidence_paths': list(evidence_paths)} for k, text in texts.items()},
                'values': {'fitted_distance_mm': distance, 'nominal_distance_mm': facts['nominal_distance_mm'],
                           'relative_distance_discrepancy': discrepancy, 'requested_model': self.doc['requested_model'],
                           'nonzero_distortion_exports': nonzero, 'image_gate_kind': gate_kind,
                           'frame_indices': list(frame['frame_indices']), 'frame_reduction': frame['frame_reduce']}}
