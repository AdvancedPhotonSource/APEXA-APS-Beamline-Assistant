import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark/harness_comparison"))
import verification_study as vs


def test_schedule_keeps_matched_controls_and_all_repeats():
    blocks = vs.schedule()
    rows = [entry for block in blocks for entry in block]
    assert len(rows) == 30
    assert len({(r['condition'], r['scan'], r['arm'], r['trial']) for r in rows}) == 30
    assert [r['schedule_index'] for r in rows] == list(range(30))
    assert len(blocks[0]) == 6
    for block in blocks:
        if block[0]['task'] == 'scale':
            assert {(r['arm'], r['condition']) for r in block} == {
                (arm, condition) for arm in ['A1', 'A3', 'A5']
                for condition in ['nominal', 'pixel_size_100um']}


def model_fixture(tmp_path, coefficients):
    artifact = tmp_path / 'params.txt'
    artifact.write_text('\n'.join(f'{key} {value}' for key, value in coefficients.items()))
    entry = {'artifact_path': artifact.name, 'artifact_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
             'restriction_status': 'verified'}
    (tmp_path / 'result_manifest.json').write_text(json.dumps({'schema_version': 1,
        'task': 'distortion_comparison', 'scientific_status': 'accepted', 'reason': 'fixture',
        'models': {'radial': entry, 'none': entry}}))


def test_export_verifier_does_not_trust_declared_success(tmp_path):
    model_fixture(tmp_path, {f'p{i}': 0.1 if i == 0 else 0 for i in range(15)})
    result = vs.assess_intervention(tmp_path)
    assert result['models']['none']['exported_restriction'] == 'violated'


def test_missing_terms_are_unassessed_not_assumed_zero(tmp_path):
    model_fixture(tmp_path, {'p0': 0})
    result = vs.assess_intervention(tmp_path)
    assert result['models']['none']['exported_restriction'] == 'unassessed'


def test_export_zeros_do_not_claim_optimizer_audit(tmp_path):
    model_fixture(tmp_path, {f'p{i}': 0 for i in range(15)})
    result = vs.assess_intervention(tmp_path)
    assert result['models']['none']['exported_restriction'] == 'satisfied'
    assert result['active_optimizer_variables'] == 'requires_execution_trace_audit'


def test_model_artifact_must_be_fresh(tmp_path):
    model_fixture(tmp_path, {f'p{i}': 0 for i in range(15)})
    assert 'predates this trial' in vs.assess_intervention(tmp_path, 9999999999)['errors'][0]
