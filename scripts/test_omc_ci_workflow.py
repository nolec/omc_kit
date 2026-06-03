#!/usr/bin/env python3
"""omc-ci.yml 구조 검증 테스트 — stdlib only

GitHub Actions 워크플로우 파일이 올바르게 구성됐는지 확인한다.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
WORKFLOW_FILE = ROOT / ".github" / "workflows" / "omc-ci.yml"


def _content() -> str:
    if not WORKFLOW_FILE.exists():
        return ""
    return WORKFLOW_FILE.read_text(encoding="utf-8")


class TestOmcCiWorkflow(unittest.TestCase):
    def test_workflow_file_exists(self):
        """omc-ci.yml 파일이 존재해야 한다."""
        self.assertTrue(WORKFLOW_FILE.exists(), f"{WORKFLOW_FILE} 없음")

    def test_triggers_push_and_pull_request(self):
        """push + pull_request 트리거 포함."""
        c = _content()
        self.assertIn("push:", c, "push 트리거 없음")
        self.assertIn("pull_request:", c, "pull_request 트리거 없음")

    def test_runs_on_ubuntu(self):
        """ubuntu-latest runner 사용 (mktemp --suffix 이슈 회피)."""
        c = _content()
        self.assertIn("ubuntu-latest", c, "ubuntu-latest 없음")

    def test_has_pytest_step(self):
        """pytest 실행 step 포함."""
        c = _content()
        self.assertTrue(
            "pytest" in c or "python3 -m pytest" in c,
            "pytest step 없음",
        )

    def test_shell_dependent_tests_excluded(self):
        """셸 의존 테스트 2개 --ignore 처리 확인."""
        c = _content()
        self.assertIn("test_omc_pipeline_check_exit", c, "exit 테스트 --ignore 없음")
        self.assertIn("test_omc_post_file_check", c, "post-file-check 테스트 --ignore 없음")

    def test_has_omc_tdd_check_step(self):
        """omc_tdd_check.py --run-tests step 포함 (--report-only는 exit 0 → CI 무효)."""
        c = _content()
        self.assertIn("omc_tdd_check", c, "omc_tdd_check.py step 없음")
        self.assertIn("--run-tests", c, "--report-only는 항상 exit 0 — --run-tests여야 CI에서 차단됨")
        self.assertNotIn("--report-only", c, "--report-only 사용 금지 (CI 게이트 무효화)")

    def test_checkout_action_present(self):
        """actions/checkout step 포함."""
        c = _content()
        self.assertIn("actions/checkout", c, "checkout action 없음")

    def test_python_setup_present(self):
        """Python setup step 포함."""
        c = _content()
        self.assertTrue(
            "python-version" in c or "setup-python" in c,
            "Python setup step 없음",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
