"""Regression tests for errors observed in calibration handoff review/repair."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from apexa_calibration_contract import (CalibrationContract, P_NAMES, evaluate,
    load_contract, normalize, sha)


@pytest.fixture
def case(tmp_path):
    facts = dict(pixel_pitch_um=150, nominal_distance_mm=900, energy_kev=63.314,
        lattice_angstrom=4.15689, space_group=225, frames=list(range(1, 21)),
        frame_reduction='mean', image_transform=2, data_loc='exchange/data')
    doc = dict(schema_version=1, facts=facts, requested_model='none', pipeline='four-stage',
               distance_policy_fraction=.05, zero_tolerance=1e-12)
    param = ('SpaceGroup 225\nLatticeConstant 4.15689 4.15689 4.15689 90 90 90\n'
             'Wavelength 0.195824\npx 150\nLsd 899500\n' + ''.join(f'p{i} 0\n' for i in range(15)))
    run = dict(frame_selection=dict(frame_indices=facts['frames'], frame_reduce='mean',
                                   data_loc='exchange/data', source=str(tmp_path/'image.h5')),
               im_trans_applied=[2], gate=dict(passed=True, stage4_strain_uE_test=14., threshold_uE=100.))
    execution = dict(effective_pixel_pitch_um=150,
                     stages=[dict(before_refine_flags={n: False for n in P_NAMES})])
    for name, obj in [('contract.json', doc), ('run.json', run), ('execution.json', execution)]:
        (tmp_path/name).write_text(json.dumps(obj))
    (tmp_path/'candidate.params').write_text(param)
    (tmp_path/'image.h5').write_bytes(b'fixture: no solver executes in these tests')
    args = dict(contract_path=str(tmp_path/'contract.json'), image_file=str(tmp_path/'image.h5'),
        template_param_file=str(tmp_path/'candidate.params'), candidate_parameters_file=str(tmp_path/'candidate.params'),
        candidate_run_file=str(tmp_path/'run.json'), candidate_execution_file=str(tmp_path/'execution.json'))
    return SimpleNamespace(root=tmp_path, args=args, doc=doc, run=run, execution=execution,
                           state=CalibrationContract(load_contract(**args)))


def final_args(case, path='candidate.params', decision='release_conditionally'):
    return dict(scientific_status='provisional', workspace_root=str(case.root),
        manifest_path=str(case.root/'result_manifest.json'),
        result_details=dict(final_decision=decision, reason='Recorded evidence checked',
            adopted_artifact=dict(path=path), remaining_limits=[]))


def native_result(case, effective):
    directory = Path(effective['output_dir'])
    directory.mkdir()
    path = directory/'refined.params'
    path.write_bytes((case.root/'candidate.params').read_bytes())
    result = dict(status='success', artifact_path=str(path), artifact_sha256=sha(path),
        image_file=case.args['image_file'], engine='canonical-v2:four_stage (0.26.0)',
        refine_distortion='none', distortion_verification=dict(status='verified', checked_values={n:0. for n in P_NAMES}),
        constraint_execution=[dict(after_thaw_refine_flags={n:False for n in P_NAMES})], **deepcopy(case.run))
    return result


def test_full_model_never_implies_zero_coefficients(case):
    doc = normalize(case.doc)
    doc['requested_model'] = 'full'
    path = case.root/'candidate.params'
    path.write_text(path.read_text().replace('p0 0', 'p0 0.7'))
    execution = deepcopy(case.execution)
    execution['stages'][0]['before_refine_flags'] = {n: True for n in P_NAMES}
    assert evaluate(doc, path, case.run, execution)['all_satisfied']


@pytest.mark.parametrize('change', ['frames', 'pixel', 'phase', 'wave', 'gate', 'flags', 'missing', 'nan'])
def test_invalid_handoffs_never_pass(case, change):
    run, execution = deepcopy(case.run), deepcopy(case.execution)
    path = case.root/'candidate.params'
    if change == 'frames': run['frame_selection']['frame_indices'] = list(range(20))
    if change == 'pixel': execution['effective_pixel_pitch_um'] = 120
    if change == 'phase': path.write_text(path.read_text().replace('4.15689', '5.431'))
    if change == 'wave': path.write_text(path.read_text().replace('0.195824', '0.25'))
    if change == 'gate': run['gate']['passed'] = False
    if change == 'flags': execution['stages'][0]['before_refine_flags']['a1'] = True
    if change == 'missing': del run['frame_selection']
    if change == 'nan': run['gate']['stage4_strain_uE_test'] = float('nan')
    assert not evaluate(normalize(case.doc), path, run, execution)['all_satisfied']


def test_binding_restores_exact_inputs_and_preserves_proposal(case):
    proposal = dict(output_dir=str(case.root/'repair'), skip_frame=0, frame_count=20,
                    refine_distortion='full', px_um=120, calibration_pipeline='tilted')
    copy = deepcopy(proposal)
    effective, receipt = case.state.bind(proposal)
    assert proposal == copy
    assert effective['skip_frame'] == 1 and effective['frame_count'] == 20
    assert effective['refine_distortion'] == 'none' and effective['px_um'] == 150
    assert effective['calibration_pipeline'] == 'four-stage'
    assert receipt['corrected_arguments']['skip_frame'] == {'proposed':0, 'bound':1}


def test_changed_contract_and_conflicting_template_fail(case):
    (case.root/'contract.json').write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        case.state.bind(dict(output_dir=str(case.root/'repair')))
    (case.root/'contract.json').write_text(json.dumps(case.doc))
    p = case.root/'candidate.params'
    p.write_text(p.read_text().replace('4.15689', '5.431'))
    with pytest.raises(ValueError, match='phase'):
        load_contract(**case.args)


def test_verified_repair_keeps_actual_provenance_and_scope(case):
    args, binding = case.state.bind(dict(output_dir=str(case.root/'repair')))
    result = native_result(case, args)
    capture = case.state.capture(result, binding)
    assert capture['checks']['all_satisfied']
    finalized = case.state.finalize(final_args(case, 'repair/refined.params'))
    details = finalized['result_details']
    assert details['final_decision'] == 'repaired_and_release_conditionally'
    assert details['agent_proposed_result']['final_decision'] == 'release_conditionally'
    assert details['repair_evidence']['execution_record_path'].startswith('repair/APEXA_contract_execution_')
    assert details['remaining_limits']
    assert details['contract_verification']['origin'] == 'live_native_dispatch'


@pytest.mark.parametrize('change', ['artifact', 'receipt', 'copied_gate', 'frames', 'flags', 'missing_values'])
def test_altered_or_incomplete_repairs_cannot_be_released(case, change):
    args, binding = case.state.bind(dict(output_dir=str(case.root/'repair')))
    result = native_result(case, args)
    if change == 'frames': result['frame_selection']['frame_indices'] = list(range(20))
    if change == 'flags': result['constraint_execution'][0]['after_thaw_refine_flags']['a2'] = True
    if change == 'missing_values': del result['distortion_verification']['checked_values']['a1']
    receipt = case.state.capture(result, binding)
    if change == 'artifact': Path(result['artifact_path']).write_text('changed')
    if change == 'receipt': Path(receipt['receipt_path']).write_text('{}')
    if change == 'copied_gate':
        path = case.root/'copied.params'
        path.write_bytes(Path(result['artifact_path']).read_bytes())
    else:
        path = Path(result['artifact_path'])
    with pytest.raises(ValueError):
        case.state.finalize(final_args(case, str(path)))


def test_handoff_is_not_a_repair_and_extra_hold_is_allowed(case):
    details = case.state.finalize(final_args(case))['result_details']
    assert details['repair_evidence'] is None
    assert details['contract_verification']['origin'] == 'supplied_handoff_not_live_execution'
    with pytest.raises(ValueError, match='not a new native repair'):
        case.state.finalize(final_args(case, decision='repaired_and_release_conditionally'))
    args = final_args(case, decision='hold')
    args['result_details']['adopted_artifact'] = None
    assert case.state.finalize(args)['result_details']['final_decision'] == 'hold'


def test_full_model_unsupported_restriction_claim_refused(case):
    case.state.doc['requested_model'] = 'full'
    args = final_args(case, decision='hold')
    args['result_details'].update(adopted_artifact=None, assessments={'restriction': {'status':'violated'}})
    with pytest.raises(ValueError, match='no zero-distortion'):
        case.state.finalize(args)


def test_tilted_full_dispatch_does_not_add_a_restriction(case):
    case.state.doc.update(requested_model='full', pipeline='tilted')
    args, binding = case.state.bind(dict(output_dir=str(case.root/'repair'), refine_distortion='none'))
    assert args['refine_distortion'] == 'full'
    assert args['calibration_pipeline'] == 'tilted' and args['device'] == 'cpu'
    assert binding['corrected_arguments']['refine_distortion']['bound'] == 'full'


def test_pinned_contract_cannot_be_changed_by_its_callers(case):
    bundle = load_contract(**case.args)
    state = CalibrationContract(bundle)
    bundle['contract']['facts']['frames'][0] = 0
    assert state.doc['facts']['frames'][0] == 1


@pytest.mark.parametrize('document', [None, [], {'schema_version':1,'facts':[]}])
def test_malformed_contract_refused(document):
    with pytest.raises(ValueError):normalize(document)


def test_dispatch_integration_load_bind_capture_finalize(case, monkeypatch):
    from argo_mcp_client import APEXAClient
    from beamline_core_server import load_calibration_contract, finalize_result
    from apexa_toolsurface import CORE_TOOLS
    assert 'load_calibration_contract' in CORE_TOOLS
    monkeypatch.delenv('APEXA_BEAMLINE', raising=False)
    calls = []
    async def call_tool(name, arguments):
        calls.append((name, deepcopy(arguments)))
        if name == 'load_calibration_contract': text = await load_calibration_contract(**arguments)
        elif name == 'finalize_result': text = await finalize_result(**arguments)
        else: text = json.dumps(native_result(case, arguments))
        return SimpleNamespace(content=[{'type':'text', 'text':text}])
    client = APEXAClient.__new__(APEXAClient)
    client._tool_registry = {x:'test' for x in ('load_calibration_contract','midas_auto_calibrate','finalize_result')}
    client._available_tools = []
    client.sessions = {'test':SimpleNamespace(call_tool=call_tool)}
    client.context = SimpleNamespace(add_analysis=lambda *a:None)
    async def exercise():
        loaded = json.loads(await client.execute_tool_call('load_calibration_contract', case.args))
        assert loaded['client_contract_active'], loaded
        assert json.loads(await client.execute_tool_call('load_calibration_contract', case.args))['client_contract_active']
        result = json.loads(await client.execute_tool_call('midas_auto_calibrate',
            dict(output_dir=str(case.root/'repair'), skip_frame=0, refine_distortion='full')))
        assert result['contract_execution']['checks']['all_satisfied'], result
        assert calls[-1][1]['skip_frame'] == 1
        final = json.loads(await client.execute_tool_call('finalize_result', final_args(case, 'repair/refined.params')))
        assert final['status'] == 'success', final
        saved = json.loads((case.root/'result_manifest.json').read_text())
        assert saved['repair_evidence'] and saved['remaining_limits']
        assert saved['validation']['scientific_interpretation'] == 'not_evaluated_by_hash_checks'
    asyncio.run(exercise())


def test_query_isolation_even_on_exception(monkeypatch):
    import argo_mcp_client as module
    client = module.APEXAClient.__new__(module.APEXAClient)
    client.permission_callback = None
    client.anl_username, client.selected_model = '', 'test'
    sentinel = object()
    client._calibration_contract = sentinel
    monkeypatch.setattr(module, 'select_provider', lambda *a:None)
    async def process(*a, **kw):
        assert client._calibration_contract is None
        client._calibration_contract = object()
        raise ValueError('fixture interruption')
    client.orchestrator = SimpleNamespace(process=process)
    with pytest.raises(ValueError, match='fixture interruption'):
        asyncio.run(client._execute_query('test'))
    assert client._calibration_contract is sentinel
