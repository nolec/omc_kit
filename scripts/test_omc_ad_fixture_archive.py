"""Recovery of the frozen Ad inputs; no model calls or service credentials."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ARCHIVE = Path(__file__).resolve().parents[1] / 'docs/comparison-dashboard/evidence/ad-validation-inputs-v1'
BASELINE = '34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc'
AD = 'libs/sellerhub-store/src/lib/components/Ad'
HOME = 'libs/store-manager/components/src/lib/home/Home.tsx'
PINS = {
    'build-pixels.cjs': 'dfb994a6020f70eae41bf35dea2a0c638024e6cea8652e5a7f93aa10315cacc9',
    'Home.tsx.fixture': 'cea906960f5e49420c64ce759d9dfc3f7db3f784f379de4f6cfffade7cf58faf',
}
VARIANTS = ['card-large', 'card-medium', 'card-small', 'banner-small', 'banner-large', 'banner-landing']


def assert_pins(root):
    for name, expected in PINS.items():
        assert (root / name).is_file(), f'missing frozen input: {name}'
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name


@pytest.mark.parametrize('name', PINS)
def test_frozen_input_matches_historical_sha256(name):
    path = ARCHIVE / name
    assert path.is_file(), f'missing frozen input: {name}'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PINS[name]


def test_manifest_matches_historical_policy_and_destinations():
    manifest = json.loads((ARCHIVE / 'manifest.json').read_text())
    assert manifest['baseline_commit'] == BASELINE
    assert manifest['files'] == {
        name: {'sha256': pin, 'destination': '.omc/visual-input/build-pixels.cjs' if name.endswith('.cjs') else HOME}
        for name, pin in PINS.items()
    }
    assert manifest['visual_policy']['variants'] == VARIANTS
    assert manifest['visual_policy']['dprs'] == [1, 2]
    assert manifest['visual_policy']['images_per_variant'] == 2
    assert manifest['visual_policy']['builder_inputs'] == {'.omc/visual-input/build-pixels.cjs': PINS['build-pixels.cjs']}
    from omc_visual_check import CRITERIA
    assert manifest['visual_policy']['criteria'] == CRITERIA


def test_detached_copy_survives_original_temporary_directory_loss(tmp_path):
    # No source path in the archive is needed after this copy.
    detached = tmp_path / 'retained'
    shutil.copytree(ARCHIVE, detached)
    assert_pins(detached)
    manifest = json.loads((detached / 'manifest.json').read_text())
    candidate = tmp_path / 'new-candidate'
    for name, entry in manifest['files'].items():
        destination = candidate / entry['destination']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(detached / name, destination)
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == entry['sha256']


def run_documented_restore(candidate):
    readme = (ARCHIVE / 'README.md').read_text()
    code = readme.split("python3 - <<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    return subprocess.run([sys.executable, '-c', code], cwd=ARCHIVE.parents[3],
        env={**os.environ, 'CANDIDATE': str(candidate)}, capture_output=True, text=True)


def test_documented_restore_recipe_works_in_fresh_directory(tmp_path):
    result = run_documented_restore(tmp_path)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((ARCHIVE / 'manifest.json').read_text())
    for entry in manifest['files'].values():
        assert hashlib.sha256((tmp_path / entry['destination']).read_bytes()).hexdigest() == entry['sha256']


def test_documented_restore_refuses_existing_work_before_any_write(tmp_path):
    home = tmp_path / HOME
    home.parent.mkdir(parents=True)
    home.write_bytes(b'existing user work')
    result = run_documented_restore(tmp_path)
    assert result.returncode != 0
    assert 'Refuse to overwrite existing work' in result.stderr
    assert home.read_bytes() == b'existing user work'
    assert not (tmp_path / '.omc/visual-input/build-pixels.cjs').exists()


def test_documented_restore_refuses_outside_directory_link(tmp_path):
    candidate = tmp_path / 'candidate'
    outside = tmp_path / 'outside'
    candidate.mkdir()
    outside.mkdir()
    (candidate / '.omc').symlink_to(outside, target_is_directory=True)
    result = run_documented_restore(candidate)
    assert result.returncode != 0
    assert list(outside.iterdir()) == []
    assert not (candidate / HOME).exists()


@pytest.mark.parametrize('name', PINS)
def test_corruption_is_detected_before_reuse(tmp_path, name):
    shutil.copytree(ARCHIVE, tmp_path / 'archive')
    path = tmp_path / 'archive' / name
    path.write_bytes(path.read_bytes() + b'\nchanged')
    with pytest.raises(AssertionError, match=name):
        assert_pins(tmp_path / 'archive')


@pytest.mark.parametrize('name', PINS)
def test_missing_input_is_detected_before_reuse(tmp_path, name):
    shutil.copytree(ARCHIVE, tmp_path / 'archive')
    (tmp_path / 'archive' / name).unlink()
    with pytest.raises(AssertionError, match='missing frozen input'):
        assert_pins(tmp_path / 'archive')


@pytest.mark.skipif(not os.environ.get('OMC_AD_RESTORE_STOREFRONT'),
                    reason='real baseline build requires an explicit local storefront with dependencies')
def test_recovered_builder_bundles_actual_baseline_ad(tmp_path):
    assert_pins(ARCHIVE)
    storefront = Path(os.environ['OMC_AD_RESTORE_STOREFRONT'])
    assert (storefront / 'node_modules/esbuild').is_dir(), 'Provide OMC_AD_RESTORE_STOREFRONT with installed dependencies'
    candidate = tmp_path / 'new-candidate'
    candidate.mkdir()
    listing = subprocess.check_output(['git', '-C', str(storefront), 'ls-tree', '-r', '--name-only', BASELINE,
        AD, 'libs/sellerhub-store/src/lib/foundation'], text=True)
    assert listing.splitlines(), 'baseline Ad source missing'
    for name in listing.splitlines():
        destination = candidate / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(subprocess.check_output(['git', '-C', str(storefront), 'show', f'{BASELINE}:{name}']))
    (candidate / 'node_modules').symlink_to((storefront / 'node_modules').resolve(), target_is_directory=True)
    builder = candidate / '.omc/visual-input/build-pixels.cjs'
    builder.parent.mkdir(parents=True)
    shutil.copyfile(ARCHIVE / 'build-pixels.cjs', builder)
    result = subprocess.run(['node', str(builder)], cwd=candidate, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    output = candidate / 'apps/store-manager/public/omc-ad-pixels'
    assert (output / 'index.html').is_file()
    bundle = (output / 'bundle.js').read_text()
    assert len(bundle) > 10000
    for variant in VARIANTS:
        assert variant in bundle
    assert 'rgb(200,20,80)' in bundle
    assert 'OMC_TEMPORARY_AD_FIXTURE' not in bundle  # Home is a separate surface.
