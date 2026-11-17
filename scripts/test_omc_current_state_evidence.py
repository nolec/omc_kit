from html.parser import HTMLParser
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROADMAP = ROOT / 'docs/automatic_model_routing_roadmap.md'
DASH = ROOT / 'docs/comparison-dashboard'
BASE = '6ab95657598dfbf1332b1c35334dbe098cdd304f'

class Headings(HTMLParser):
    def __init__(self):
        super().__init__(); self.in_h2 = False; self.first_h2 = None; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == 'h2' and self.first_h2 is None: self.in_h2 = True
    def handle_data(self, data):
        if self.in_h2: self.parts.append(data)
    def handle_endtag(self, tag):
        if tag == 'h2' and self.in_h2:
            self.first_h2 = ''.join(self.parts); self.in_h2 = False

def test_snapshot_consistent_across_four_representations():
    view = json.loads((DASH / 'view.json').read_text())
    snapshot = view['current_source_snapshot']
    assert snapshot['source_commit'] == BASE
    assert snapshot['checked_at'] == '2026-11-17'
    assert snapshot['version'] == '0.7.2'
    assert snapshot['automatic_hooks'] == 'disabled'
    assert snapshot['install_status'] == 'not_reverified'
    assert snapshot['feature_evidence']['실사용'] == '미검증'
    for p in (ROADMAP, DASH / 'README.md', DASH / 'index.html'):
        text = p.read_text()
        assert BASE in text and '2026-11-17' in text
        assert '설치 현황 재검증 안 함' in text
        assert '0.7.2' in text and '12곳' in text
    html = Headings(); html.feed((DASH / 'index.html').read_text())
    assert html.first_h2.startswith('현재 소스 상태')
    assert view['roadmap']['current_priority'].startswith('이전 이력')

def test_quickstart_does_not_claim_automatic_hooks():
    text = (ROOT / 'docs/quickstart_kr.md').read_text()
    assert '세션 시작 시 훅이 자동으로 컨텍스트 수집' not in text
    assert '교훈이 쌓일수록 BM25가 현재 작업과 관련된 교훈을 자동 주입합니다' not in text
    assert '자동 세션·도구·Git 훅은 비활성' in text

def test_readme_does_not_instruct_missing_hook_install():
    text = (ROOT / 'README.md').read_text()
    assert 'cp scripts/pre-commit.sample .git/hooks/pre-commit' not in text
    assert 'Git pre-commit hook을 설치하거나 doctor의 수정 옵션' not in text
    assert '기존 프로젝트의 훅' in text

def test_legacy_current_update_is_explicitly_historical():
    view = json.loads((DASH / 'view.json').read_text())
    assert view['current_update']['status'].startswith('이전 이력')
    assert 'current_source_snapshot' in view['roadmap']['items_scope']
    assert view['current_update']['date'] == '2026-11-12'

def test_readme_only_advertises_supported_host_integration():
    text = (ROOT / 'README.md').read_text()
    assert 'Claude Code·Gemini CLI의 `/task`' not in text
    assert 'Claude Code와 Gemini CLI에서는 slash command' not in text
    assert 'Cursor는 설치된 rules와 hooks' not in text

def test_snapshot_matches_current_sections_and_actual_hook_config():
    view = json.loads((DASH / 'view.json').read_text())
    snap = view['current_source_snapshot']
    hooks = json.loads((ROOT / '.codex/hooks.json').read_text())['hooks']
    assert bool(hooks) and all(isinstance(v, list) and not v for v in hooks.values())
    assert snap['automatic_hooks'] == 'disabled'
    for p in (ROADMAP, DASH / 'README.md'):
        section = p.read_text().split('## 현재 소스 상태', 1)[1].split('\n## ', 1)[0]
        assert snap['source_commit'] in section and snap['checked_at'] in section
        assert '설치 현황 재검증 안 함' in section
    html = (DASH / 'index.html').read_text()
    section = html.split('<section><h2>현재 소스 상태', 1)[1].split('</section>', 1)[0]
    assert snap['source_commit'] in section and snap['checked_at'] in section
    assert '설치 현황 재검증 안 함' in section

def test_dashboard_title_identifies_snapshot_date():
    title = (DASH / 'README.md').read_text().splitlines()[0]
    assert '2026-11-17' in title
    assert '최신 기준 2026-11-12' not in title
