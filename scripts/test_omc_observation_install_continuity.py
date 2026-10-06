import json
import copy
from datetime import datetime
from pathlib import Path

import pytest

import omc_completion_observation as observation
import test_omc_completion_observation as fixtures


@pytest.mark.parametrize('field,value', [
    ('expected', {}), ('expected', {'source_revision':'after'}),
    ('expected', {'source_sha256':'a'*64, 'source_revision':'after', 'source_version':'0.3.4', 'extra':True}),
    ('expected', None), ('before', {}), ('registration_files', {}),
    ('root', 'relative'), ('extra', True),
])
def test_malformed_rehashed_plan_is_rejected_before_seal(tmp_path, monkeypatch, field, value):
    import omc_observation_install_continuity as c
    root = tmp_path / 'repo'
    root.mkdir()
    identity = {'install_receipt_sha256':'b'*64, 'source_sha256':'a'*64,
                'source_revision':'after', 'source_version':'0.3.4'}
    monkeypatch.setattr(c, '_identity', lambda root: identity)
    monkeypatch.setattr(c, '_registration_files', lambda root: {'.omc/observation-policy.json':'c'*64})
    plan = {'schema_version':c.SCHEMA, 'kind':'plan', 'root':str(root),
            'before':identity, 'expected':{k:v for k,v in identity.items() if k != 'install_receipt_sha256'},
            'registration_files':c._registration_files(root)}
    plan[field] = copy.deepcopy(value)
    plan['sha256'] = c._hash(plan)
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan))
    with pytest.raises(c.ContinuityError):
        c.seal(root, plan_path=path)
    import sys
    monkeypatch.setattr(sys, 'argv', ['continuity', 'seal', '--target', str(root), '--plan', str(path)])
    assert c.main() == 2
    assert not (root / c.PROOF_FILE).exists()


@pytest.mark.parametrize('damage', ['empty_expected', 'missing_after', 'extra', 'bad_digest', 'wrong_type'])
def test_malformed_rehashed_proof_cannot_rebind_install(tmp_path, monkeypatch, damage):
    import omc_observation_install_continuity as c
    root = tmp_path / 'repo'
    before = {'install_receipt_sha256':'b'*64, 'source_sha256':'a'*64,
              'source_revision':'before', 'source_version':'0.3.4'}
    after = {**before, 'source_revision':'after', 'install_receipt_sha256':'d'*64}
    files = {'.omc/observation-policy.json':'c'*64}
    plan = {'schema_version':c.SCHEMA, 'kind':'plan', 'root':str(root), 'before':before,
            'expected':{k:v for k,v in after.items() if k != 'install_receipt_sha256'}, 'registration_files':files}
    if damage == 'empty_expected':
        plan['expected'] = {}
    plan['sha256'] = c._hash(plan)
    proof = {'schema_version':c.SCHEMA, 'kind':'proof', 'plan':plan, 'after':copy.deepcopy(after)}
    if damage == 'missing_after':
        proof.pop('after')
    elif damage == 'extra':
        proof['extra'] = True
    elif damage == 'bad_digest':
        proof['after']['install_receipt_sha256'] = 'bad'
    elif damage == 'wrong_type':
        proof['plan'] = None
    proof['sha256'] = c._hash(proof)
    path = root / c.PROOF_FILE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(proof))
    monkeypatch.setattr(c, '_identity', lambda root: after)
    monkeypatch.setattr(c, '_registration_files', lambda root: files)
    assert not c.installation_matches(root, before, after)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(observation, '_now', lambda: datetime.fromisoformat('2026-09-10T00:00:00+00:00'))


def test_install_continuity_keeps_existing_pair_and_samples(tmp_path, monkeypatch):
    import omc_observation_install_continuity as continuity
    roots, pending, _, future = fixtures._transition_pair(tmp_path)
    observation.activate_live_pair(roots)
    monkeypatch.setattr(observation, '_now', lambda: future)
    baseline = observation._live_policy(roots['b'])
    new = fixtures._replace_live_pending(roots['b'], pending['b'], 5)
    new['work_class_locked_at'] = future.isoformat()
    (roots['b'] / '.omc/state/pending-completion.json').write_text(json.dumps(new))
    fixtures._write_live_session(roots['b'], new, request='implementation sample 5')
    observation.start_live_observation(roots['b'])
    (roots['b'] / 'app.py').write_text('print("changed")\n')
    observation.capture_live_completion(roots['b'], raw_report=b'done', raw_verification=b'pass', unrun_items=[])
    before = {str(p): p.read_bytes() for root in roots.values()
              for p in (root / '.omc/observations').rglob('*.json')}
    plans = {}
    for key, root in roots.items():
        receipt = json.loads((root / '.omc/install-receipt.json').read_text())
        plans[key] = continuity.prepare(root, expected_source_sha256=receipt['source_sha256'],
            expected_source_revision='upgraded', expected_version=receipt['omc_version'],
            output=tmp_path / (key + '-plan.json'))
        receipt['source_revision'] = 'upgraded'
        (root / '.omc/install-receipt.json').write_text(json.dumps(receipt))
        continuity.seal(root, plan_path=tmp_path / (key + '-plan.json'))
    assert observation._live_policy(roots['b']) == baseline
    assert observation.live_observation_status(roots['b'])['status'] == 'AWAITING_USER_OUTCOME'
    assert observation.live_observation_status(roots['b'])['samples_started'] == 1
    for path, data in before.items():
        assert Path(path).read_bytes() == data
    observation.disable_live_observation(roots['a'], reason='stop')
    assert observation._live_policy(roots['b']) == baseline
    receipt_path = roots['a'] / '.omc/install-receipt.json'
    receipt = json.loads(receipt_path.read_text())
    receipt['source_revision'] = 'unplanned'
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(observation.CaptureError, match='live_install_identity_invalid'):
        observation._live_policy(roots['b'])


def test_install_continuity_refuses_wrong_source_and_changed_registration(tmp_path):
    import omc_observation_install_continuity as continuity
    roots, _, _, _ = fixtures._transition_pair(tmp_path)
    observation.activate_live_pair(roots)
    root = roots['a']
    receipt = json.loads((root / '.omc/install-receipt.json').read_text())
    plan = tmp_path / 'plan.json'
    continuity.prepare(root, expected_source_sha256='f' * 64,
        expected_source_revision='upgraded', expected_version=receipt['omc_version'], output=plan)
    with pytest.raises(continuity.ContinuityError):
        continuity.seal(root, plan_path=plan)
    assert not (root / continuity.PROOF_FILE).exists()


def test_install_continuity_v3_report_preserves_roster(tmp_path, monkeypatch):
    import omc_observation_install_continuity as continuity
    import omc_skill_effectiveness_cohort_v3 as v3
    import test_omc_skill_effectiveness_cohort_v3 as workflow
    roots, _ = workflow._operational_pair(tmp_path, monkeypatch)
    for root in roots:
        path = root / '.omc/install-receipt.json'
        receipt = json.loads(path.read_text())
        receipt.update(target=str(root), source_revision='before')
        path.write_text(json.dumps(receipt))
    roster = tmp_path / 'pinned-roster.json'
    v3.create_roster(targets=roots, output=roster, activation_id='continuity-test',
                     activation_at='2026-10-01T00:00:00Z')
    for root in roots:
        v3.enroll(root, roster_path=roster)
        before = v3.config_path(root).read_bytes()
        plan = tmp_path / (root.name + '-plan.json')
        continuity.prepare(root, expected_source_sha256='a' * 64,
            expected_source_revision='after', expected_version='0.3.4', output=plan)
        path = root / '.omc/install-receipt.json'
        receipt = json.loads(path.read_text())
        receipt['source_revision'] = 'after'
        path.write_text(json.dumps(receipt))
        with pytest.raises(v3.V3Error, match='installation_binding_mismatch'):
            v3.report(root)
        continuity.seal(root, plan_path=plan)
        assert v3.report(root)['state'] == 'REGISTERED_NOT_STARTED'
        assert v3.config_path(root).read_bytes() == before


@pytest.mark.parametrize('damage', ['registration', 'proof', 'plan_root', 'symlink', 'audit'])
def test_install_continuity_tampering_never_rebinds(tmp_path, monkeypatch, damage):
    import omc_observation_install_continuity as continuity
    import omc_install_audit
    roots, _, _, _ = fixtures._transition_pair(tmp_path)
    observation.activate_live_pair(roots)
    root = roots['a']
    expected = observation._live_install_identity(root, require_fresh_source=False)
    receipt_path = root / '.omc/install-receipt.json'
    receipt = json.loads(receipt_path.read_text())
    plan_path = tmp_path / 'plan.json'
    continuity.prepare(root, expected_source_sha256=receipt['source_sha256'],
        expected_source_revision='after', expected_version=receipt['omc_version'], output=plan_path)
    receipt['source_revision'] = 'after'
    receipt_path.write_text(json.dumps(receipt))
    continuity.seal(root, plan_path=plan_path)
    actual = observation._live_install_identity(root, require_fresh_source=False)
    assert continuity.installation_matches(root, expected, actual)
    proof_path = root / continuity.PROOF_FILE
    if damage == 'registration':
        (root / '.omc/observations/transition.json').write_text('{}')
    elif damage == 'proof':
        proof_path.write_text('{}')
    elif damage == 'plan_root':
        value = json.loads(proof_path.read_text())
        value['plan']['root'] = str(tmp_path)
        value['plan']['sha256'] = continuity._hash({k:v for k,v in value['plan'].items() if k != 'sha256'})
        value['sha256'] = continuity._hash({k:v for k,v in value.items() if k != 'sha256'})
        proof_path.write_text(json.dumps(value))
    elif damage == 'symlink':
        copy = tmp_path / 'proof-copy.json'
        copy.write_bytes(proof_path.read_bytes())
        proof_path.unlink()
        proof_path.symlink_to(copy)
    else:
        monkeypatch.setattr(omc_install_audit, 'audit_target', lambda *a, **k: {'installed_integrity_status':'invalid'})
    assert not continuity.installation_matches(root, expected, actual)


def test_install_continuity_cli_prepare_seal_and_retry(tmp_path, monkeypatch, capsys):
    import omc_observation_install_continuity as continuity
    import sys
    roots, _, _, _ = fixtures._transition_pair(tmp_path)
    observation.activate_live_pair(roots)
    root = roots['a']
    path = root / '.omc/install-receipt.json'
    receipt = json.loads(path.read_text())
    plan = tmp_path / 'plan.json'
    monkeypatch.setattr(sys, 'argv', ['continuity', 'prepare', '--target', str(root),
        '--expected-source-sha256', receipt['source_sha256'], '--expected-source-revision', 'after',
        '--expected-version', receipt['omc_version'], '--out', str(plan)])
    assert continuity.main() == 0
    capsys.readouterr()
    receipt['source_revision'] = 'after'
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(sys, 'argv', ['continuity', 'seal', '--target', str(root), '--plan', str(plan)])
    assert continuity.main() == 0
    original = (root / continuity.PROOF_FILE).read_bytes()
    assert continuity.main() == 0
    assert (root / continuity.PROOF_FILE).read_bytes() == original
