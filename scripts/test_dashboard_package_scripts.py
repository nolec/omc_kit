import json
from pathlib import Path


def test_dashboard_package_has_explicit_api_test_script():
    package_json = Path("dashboard/package.json")
    data = json.loads(package_json.read_text(encoding="utf-8"))
    scripts = data.get("scripts") or {}
    assert scripts.get("test:api") == "../scripts/run_dashboard_api_tests.sh"
