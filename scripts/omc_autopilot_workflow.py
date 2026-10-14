#!/usr/bin/env python3
"""Fail-closed contracts for durable Autopilot task workflows."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
import stat
import difflib
from itertools import chain
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "omc-autopilot-task/v2"


def execution_observation(*, task: dict, step: dict, attempt: int) -> dict:
    """Local executor observation, never proof of provider side effects."""
    return {
        "schema_version": "omc-execution-observation/v1",
        "attempt_id": uuid.uuid4().hex,
        "task_id": task["id"], "task_spec_sha256": task_spec_sha256(task),
        "step_id": step["id"], "attempt": attempt,
        "phase": "requested", "source": "omc_executor",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }


def observation_digest(observation: dict) -> str:
    return hashlib.sha256(_canonical_json(observation)).hexdigest()


def _bounded_regular_file(root: Path, relative: str) -> bytes:
    path = Path(relative)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise ValueError('경로 이탈 또는 빈 경로')
    directory = os.open(str(root.resolve()), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[:-1]:
            next_directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        fd = os.open(path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('일반 파일이 아님')
            if before.st_size > 65536:
                raise ValueError('파일 크기 64 KiB 초과')
            data = os.read(fd, 65537)
            after = os.fstat(fd)
            current = os.stat(path.parts[-1], dir_fd=directory, follow_symlinks=False)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if identity(before) != identity(after) or identity(after) != identity(current) or len(data) != after.st_size:
                raise ValueError('읽는 동안 파일 변경됨')
            return data
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def verified_target_diff(root: Path, step: dict) -> dict:
    completion = step['completion']
    unavailable = lambda reason: {'status': 'unavailable', 'reason': reason, 'lines': [], 'summary_lines': []}
    if 'target_path' not in completion:
        return unavailable('목표 파일 미지정—상세 비교 불가')
    if completion.get('validator_id') != 'artifact_sha256':
        return unavailable('목표 해시 비교를 지원하지 않는 validator')
    try:
        current = _bounded_regular_file(root, completion['output_path'])
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        error = 'completion_artifact_missing' if isinstance(exc, FileNotFoundError) else 'completion_artifact_unreadable'
        return {**unavailable(str(exc)), 'artifact_sha256': None, 'artifact_error': error}
    digest = hashlib.sha256(current).hexdigest()
    snapshot = {'artifact_sha256': digest, 'artifact_error': None if digest == completion['expected_sha256'] else 'completion_artifact_hash_mismatch'}
    result = lambda reason: {**unavailable(reason), **snapshot}
    try:
        target = current if Path(completion['target_path']) == Path(completion['output_path']) else _bounded_regular_file(root, completion['target_path'])
        if hashlib.sha256(target).hexdigest() != completion['expected_sha256']:
            return result('목표 파일 해시 불일치')
        old, new = current.decode('utf-8'), target.decode('utf-8')
        if '\x00' in old or '\x00' in new:
            return result('비텍스트 파일')
        old_lines, new_lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
        if len(old_lines) > 1000 or len(new_lines) > 1000:
            return result('파일당 1,000줄 초과')
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        return result(str(exc))
    if current == target:
        return {'status': 'equal', 'reason': '선언된 목표와 일치', 'lines': [],
                'summary_lines': ['추가·삭제·교체할 내용 없음—선언된 목표와 일치'], **snapshot}
    lines = []
    summary_lines = []
    length = 0
    truncated = False
    def changes():
        for operation, i, j, k, l in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
            if operation == 'equal':
                continue
            if operation == 'insert':
                for index in range(k, l):
                    yield f'추가할 내용 (목표 {index + 1}행): ' + json.dumps(new_lines[index], ensure_ascii=False)
            elif operation == 'delete':
                for index in range(i, j):
                    yield f'삭제할 내용 (현재 {index + 1}행): ' + json.dumps(old_lines[index], ensure_ascii=False)
            else:
                yield f'바꿀 내용 (현재 {i + 1}–{j}행 → 목표 {k + 1}–{l}행):'
                for index in range(i, j):
                    yield '현재 내용: ' + json.dumps(old_lines[index], ensure_ascii=False)
                for index in range(k, l):
                    yield '목표 내용: ' + json.dumps(new_lines[index], ensure_ascii=False)
    def indicated_lines():
        for index, line in enumerate(difflib.unified_diff(old_lines, new_lines, fromfile='현재 내용', tofile='목표 내용', lineterm='')):
            yield line
            if index >= 2 and line.startswith(('-', '+')) and not line.endswith(('\n', '\r')):
                side = '현재 내용' if line.startswith('-') else '목표 내용'
                yield f'[{side}: 마지막 줄바꿈 없음]'
    destinations = chain(((summary_lines, line) for line in changes()), ((lines, line) for line in indicated_lines()))
    for destination, line in destinations:
        line = line.rstrip('\n\r')
        line = ''.join(char if char.isprintable() else f'\\u{ord(char):04x}' for char in line)
        remaining = 3800 - length
        if len(summary_lines) + len(lines) >= 39 or remaining <= 0:
            truncated = True
            break
        if len(line) > remaining:
            destination.append(line[:remaining])
            truncated = True
            break
        destination.append(line)
        length += len(line) + 1
    if truncated:
        lines.append('[차이 일부 생략—40줄·4,000자 한계]')
    return {'status': 'different', 'reason': '선언된 목표와의 비교', 'lines': lines, 'summary_lines': summary_lines, **snapshot}


def recovery_assessment_facts(root: Path, task: dict, state: dict) -> list[dict]:
    """Read-only current checks; malformed or unbound records stay unknown."""
    recorded_steps = state.get("steps")
    recorded_steps = recorded_steps if isinstance(recorded_steps, dict) else {}
    verified = {}
    results = []
    for step in _assessment_order(task["steps"]):
        sid = step["id"]
        recorded = recorded_steps.get(sid)
        recorded = recorded if isinstance(recorded, dict) else {}
        observation = recorded.get("execution_observation")
        label = "확인 불가—현재 시도의 실행 관측 기록 없음"
        execution = "unknown"
        if isinstance(observation, dict):
            valid = (
                observation.get("schema_version") == "omc-execution-observation/v1"
                and observation.get("task_id") == task["id"]
                and observation.get("task_spec_sha256") == task_spec_sha256(task)
                and observation.get("step_id") == sid
                and observation.get("attempt") == recorded.get("attempt")
                and type(observation.get("attempt")) is int
                and observation.get("source") == "omc_executor"
                and isinstance(observation.get("attempt_id"), str)
                and bool(re.fullmatch(r"[0-9a-f]{32}", observation["attempt_id"]))
                and isinstance(state.get("execution_attempt_ids"), dict)
                and state["execution_attempt_ids"].get(sid) == observation["attempt_id"]
                and recorded.get("execution_observation_sha256") == observation_digest(observation)
            )
            try:
                requested_at = datetime.fromisoformat(observation["requested_at"])
                valid = valid and requested_at.tzinfo is not None
                if observation.get("phase") == "returned":
                    returned_at = datetime.fromisoformat(observation["returned_at"])
                    valid = valid and returned_at.tzinfo is not None and returned_at >= requested_at
            except (KeyError, TypeError, ValueError):
                valid = False
            if valid and observation.get("phase") == "requested":
                execution = "requested"
                label = "실행 요청 중 중단됨—요청 기록 있음; 실제 호출·외부 반영 여부는 확인 불가"
            elif valid and observation.get("phase") == "returned" and type(observation.get("returncode")) is int:
                execution = "returned"
                label = "실행기 반환 기록 있음—외부 반영·완료 증명은 아님"
            else:
                label = "확인 불가—실행 관측 기록 손상 또는 현재 요청·단계·시도와 불일치"
        comparison = verified_target_diff(root, step)
        try:
            if 'target_path' in step['completion']:
                digest, error = comparison.get('artifact_sha256'), comparison.get('artifact_error', 'completion_artifact_unreadable')
            else:
                digest, error = validate_completion_artifact(root, step)
        except (OSError, RuntimeError):
            digest, error = None, "completion_artifact_unreadable"
            label += " / 산출물 확인 불가—경로 해석 또는 읽기 실패"
        deps = step.get("depends_on", [])
        predecessors = [verified[d]["receipt_sha256"] for d in deps if d in verified]
        receipt = recorded.get("completion_receipt")
        if (recorded.get("status") == "completed" and not error
                and len(predecessors) == len(deps)
                and completion_receipt_valid(receipt, task=task, step=step, predecessor_receipts=predecessors)
                and receipt.get("artifact_sha256") == digest):
            verified[sid] = receipt
            label += " / 현재 완료 검증 통과—산출물·완료 기록·선행 결속 일치"
        else:
            label += " / 현재 완료 확인 불가—산출물 또는 완료·선행 기록 미검증"
        title = step.get("title")
        title = title.strip() if isinstance(title, str) else ""
        if error == "completion_artifact_missing":
            file_status = "현재 파일이 없습니다"
        elif error in {"completion_artifact_unreadable", "completion_artifact_path_escape"}:
            file_status = "현재 파일을 읽거나 경로를 확인할 수 없습니다"
        elif step["completion"]["validator_id"] == "artifact_sha256":
            file_status = "현재 파일이 선언된 목표 내용과 일치합니다" if not error else "현재 파일이 선언된 목표 내용과 다릅니다"
        else:
            file_status = "필수 항목만 확인됨—목표 내용 비교 기준 없음" if not error else "필수 항목 확인 실패—목표 내용 비교 기준 없음"
        results.append({"step_id": sid, "name": f"{title} ({sid})" if title else sid,
                        "execution": execution, "verified": sid in verified, "detail": f"{sid}: {label}",
                        "file_path": step["completion"]["output_path"], "target_path": step["completion"].get('target_path'), "file_status": file_status,
                        "target_diff": comparison})
    return results


def recovery_assessment(root: Path, task: dict, state: dict) -> list[str]:
    return [fact["detail"] for fact in recovery_assessment_facts(root, task, state)]


def completed_reuse_issue(*, task: dict, step: dict, recorded: dict,
                         predecessor_receipts: list[str], artifact_hash: str | None,
                         artifact_error: str | None) -> dict | None:
    """Explain a reuse block; this does not grant approval or prove an external action."""
    labels = {
        'file_changed': '파일 내용 불일치',
        'file_missing': '파일 없음',
        'file_unreadable': '파일 읽기 또는 경로 확인 불가',
        'artifact_invalid': '산출물 검증 실패',
        'receipt_missing': '완료 기록 없음',
        'receipt_invalid': '완료 기록 손상 또는 작업 불일치',
        'predecessor_invalid': '선행 완료 기록 불일치',
    }
    if artifact_error:
        code = {'completion_artifact_hash_mismatch': 'file_changed',
                'completion_artifact_missing': 'file_missing',
                'completion_artifact_unreadable': 'file_unreadable',
                'completion_artifact_path_escape': 'file_unreadable'}.get(artifact_error, 'artifact_invalid')
    else:
        receipt = recorded.get('completion_receipt')
        stored_predecessors = receipt.get('predecessor_receipts') if isinstance(receipt, dict) else None
        if receipt is None:
            code = 'receipt_missing'
        elif (not isinstance(stored_predecessors, list)
              or not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
                         for value in stored_predecessors)
              or not completion_receipt_valid(receipt, task=task, step=step,
                                              predecessor_receipts=stored_predecessors)):
            code = 'receipt_invalid'
        elif len(predecessor_receipts) != len(step.get('depends_on', [])) or stored_predecessors != predecessor_receipts:
            code = 'predecessor_invalid'
        elif receipt.get('artifact_sha256') != artifact_hash:
            code = 'file_changed'
        else:
            return None
    return {'code': code, 'label': labels[code]}


def completed_reuse_summary(task: dict, step: dict, issue: dict, comparison: dict,
                           *, record_path: str, details: bool = False) -> list[str]:
    def short(text, limit=110):
        clean = ''.join(char if char.isprintable() else ' ' for char in str(text))
        return clean if len(clean) <= limit else clean[:limit - 7] + '…일부 생략'
    sid = step['id']
    name = step.get('title') or sid
    target = step['completion'].get('target_path')
    if comparison['status'] == 'different':
        difference = ' / '.join(comparison.get('summary_lines', [])[:3])
        compare = '현재/목표: ' + (difference or '현재 파일 내용이 선언된 목표와 다릅니다')
    elif comparison['status'] == 'equal':
        compare = '현재/목표: 현재 파일은 목표와 일치합니다. 파일 일치만으로 완료 기록을 대신하지 않습니다.'
    else:
        compare = '현재/목표: 비교 불가 — ' + comparison['reason']
    if issue['code'] == 'predecessor_invalid':
        action = '선행 단계 ' + ', '.join(step.get('depends_on', [])) + '의 완료 기록과 이 단계의 선행 결속을 대조하세요.'
    elif issue['code'] in {'receipt_missing', 'receipt_invalid'}:
        action = '현재 작업·단계와 완료 기록을 대조하세요. 파일만 보고 완료로 바꾸지 마세요.'
    else:
        action = '현재 파일·선언된 목표·완료 기록을 대조하세요. 파일 변경 원인·외부 반영은 확인되지 않았습니다.'
    judgments = {
        'file_changed': '내용 변경이 의도한 것인지 확인하고 현재/목표를 대조하세요.',
        'file_missing': '파일 위치와 삭제 여부를 확인하고 목표와 대조하세요.',
        'file_unreadable': '파일 경로와 읽기 권한을 확인한 뒤 대조하세요.',
        'artifact_invalid': '현재 파일이 작업의 완료 조건을 충족하는지 대조하세요.',
        'receipt_missing': '현재 작업의 완료 기록을 찾아 대조하세요.',
        'receipt_invalid': '현재 작업의 완료 기록이 올바른지 대조하세요.',
        'predecessor_invalid': '선행 단계의 완료 기록을 대조하세요.',
    }
    title = task.get('title')
    title = title.strip() if isinstance(title, str) else ''
    lines = ['하려던 일: ' + short(title or '작업 목적 정보 없음', 80) + ' 작업을 이어가려 했습니다.',
             f'작업 위치: 작업 폴더의 {step["completion"]["output_path"]} / {name} ({sid})',
             '중단 이유: ' + issue['label'] + '로 이 단계가 완료됐는지 확인하지 못했습니다.',
             compare,
             '이번 실행: 이 단계의 파일 수정과 이후 작업을 진행하지 않고 보류했습니다. 자동 재실행·복원·승인 없음. 상세: --recovery-details',
             '다음 판단: ' + judgments.get(issue['code'], '현재 파일과 완료 기록을 대조하세요.') + ' 기록: ' + short(record_path, 48)]
    if not details:
        return [short(line) for line in lines]
    lines = [short(line, 400) for line in lines]
    def path_label(value):
        value = str(value)
        return value if value.isprintable() else json.dumps(value, ensure_ascii=False)
    # Critical lookup paths must remain complete even when the prose is abbreviated.
    lines.append('OMC 기록(작업 루트 기준): ' + path_label(record_path))
    lines.append('현재 파일(작업 루트 기준): ' + path_label(step['completion']['output_path']))
    lines.append('목표 파일(작업 루트 기준): ' + (path_label(target) if target else '미지정'))
    lines.append('구체적 대조: ' + short(action, 400))
    lines.append('상세 비교는 현재 파일과 선언된 목표의 대조입니다. 과거 파일 내용 또는 실행 여부의 증명이 아닙니다.')
    lines.extend(comparison.get('summary_lines', []))
    lines.extend(comparison.get('lines', []))
    return lines


def compact_recovery_summary(task: dict, facts: list[dict], *, record_path: str | None = None) -> list[str]:
    def short(value, limit=105):
        text = ''.join(c if c.isprintable() else ' ' for c in str(value))
        return text if len(text) <= limit else text[:limit - 9] + '…일부 생략'
    title = task.get('title')
    title = title.strip() if isinstance(title, str) else ''
    lines = ['작업: ' + short(title or '작업 목적 정보 없음')]
    for fact in facts[:2]:
        progress = '완료 검증 통과' if fact.get('verified', False) else '완료 확인 불가'
        name = fact.get('name', fact.get('step_id', fact['file_path']))
        lines.append(short(f"진행: {name}: {progress} / {fact['file_path']} — {fact['file_status']}"))
    summaries = [(fact['file_path'], line) for fact in facts if fact['target_diff']['status'] == 'different' for line in fact['target_diff']['summary_lines']]
    if summaries:
        change = f"{summaries[0][0]} — {summaries[0][1]}"
        if summaries[0][1].startswith('바꿀 내용'):
            group = next(fact['target_diff']['summary_lines'] for fact in facts
                         if fact['file_path'] == summaries[0][0] and fact['target_diff']['status'] == 'different')
            current = None
            target = None
            for line in group[1:]:
                if line.startswith(('추가할 내용', '삭제할 내용', '바꿀 내용')):
                    break
                if line.startswith('현재 내용: ') and current is None:
                    current = line[len('현재 내용: '):]
                if line.startswith('목표 내용: ') and target is None:
                    target = line[len('목표 내용: '):]
            if current is not None and target is not None:
                change = f"{short(summaries[0][0], 25)} — 바꿀 내용: 현재 {short(current, 25)} → 목표 {short(target, 25)}"
            else:
                change = f"{short(summaries[0][0], 25)} — 교체 내용 일부 생략; --recovery-details에서 확인"
    elif facts and all(fact['target_diff']['status'] == 'equal' for fact in facts):
        change = '추가·삭제·교체할 내용 없음—선언된 목표와 일치'
    else:
        change = '목표 파일 미지정 또는 비교 불가—상세 근거 확인 필요'
    lines.append('필요한 변경: ' + short(change))
    uncertain = [fact for fact in facts if not fact.get('verified', False)]
    requested = [fact for fact in uncertain if fact.get('execution') == 'requested']
    selected = (requested or uncertain)[:1]
    target = (short(selected[0].get('name', selected[0].get('step_id', '단계 미상')), 25)
              + ' [' + short(selected[0]['file_path'], 35) + ']' if selected else '불확실 단계 특정 불가')
    location = short(record_path, 80) if record_path else 'OMC 기록 위치 미제공'
    lines.append(f'확인 대상: {target} / OMC 기록(작업 루트 기준): {location}')
    if selected:
        execution = selected[0].get('execution')
        reason = {'requested': '실행 요청 기록만 확인됨; 실제 호출·반영과 완료는 확인 불가',
                  'returned': '실행기 반환 기록 확인됨; 실제 반영과 완료는 확인 불가'}.get(
                      execution, '유효 실행 기록 없음; 실행 여부와 완료는 확인 불가')
    else:
        reason = '로컬 완료 검증 통과; 외부 반영은 확인 불가' if facts else '판독할 단계 기록 없음'
    lines.append('판독: ' + reason + '. 외부 실행 기록·파일 수정 이력: 미수집; 판단 불가이면 보류 유지. 파일 일치만으로 실행 증명 불가.')
    lines.append('현재 행동: 다시 실행하지 않고 보류합니다. 미실행이 확인돼도 별도 승인 후 재실행합니다. 자동 승인·수정·후속 실행 없음.')
    lines.append('상세 확인: --recovery-details' + (' (단계·변경 일부 생략)' if len(facts) > 2 or len(summaries) > 1 else ''))
    return lines


def recovery_narrative(task: dict, facts: list[dict]) -> list[str]:
    title = task.get("title")
    title = title.strip() if isinstance(title, str) else ""
    completed = [fact["name"] for fact in facts if fact["verified"]]
    interrupted = [fact["name"] for fact in facts if fact["execution"] == "requested"]
    unknown = [fact["name"] for fact in facts if not fact["verified"]]
    missing = [fact["name"] for fact in facts if fact["execution"] == "unknown"]
    return [
        f"하려던 작업: {title or '작업 목적 정보 없음'}. 단계: " + " → ".join(fact["name"] for fact in facts),
        "파일 확인: " + " / ".join(f"{fact['name']} [{fact['file_path']}]: {fact['file_status']}" for fact in facts)
        + ". 파일 상태는 이번 실행 여부나 외부 반영 증명이 아닙니다.",
        "확인된 진행: " + (", ".join(completed) + "의 현재 산출물과 완료 기록 검증을 통과했습니다." if completed else "현재 완료 검증을 통과한 단계가 없습니다."),
        "중단 경위: " + (", ".join(interrupted) + "의 실행 요청 기록이 남은 채 이전 실행이 중단됐습니다. 실제 호출 여부와 중단 원인은 확인할 수 없습니다." if interrupted else "이전 실행의 중단 상태가 남아 있지만, 어느 호출에서 어떻게 중단됐는지는 확인할 수 없습니다."),
        "현재 결과: " + (", ".join(unknown) + "의 완료와 반영 여부를 확인하지 못했습니다." if unknown else "선언된 로컬 완료 검증은 통과했지만 외부 반영 여부까지 증명하지 않습니다.")
        + (" " + ", ".join(missing) + ": 현재 시도의 유효한 실행 관측 기록이 없어 이전 실행 여부를 확인할 수 없습니다." if missing else ""),
        "다음 행동: 이번에는 다시 실행하지 않고 보류합니다. 이전 작업의 반영 여부를 확정하지 못해 중복 반영 가능성이 있으므로 차단을 유지합니다. 자동 승인이나 후속 실행은 하지 않습니다.",
    ]


def _assessment_order(steps: list[dict]) -> list[dict]:
    """Validated DAG, with predecessors checked before consumers."""
    by_id = {step["id"]: step for step in steps}
    ordered = []
    visited = set()
    def visit(sid):
        if sid in visited:
            return
        visited.add(sid)
        for dep in by_id[sid].get("depends_on", []):
            visit(dep)
        ordered.append(by_id[sid])
    for sid in by_id:
        visit(sid)
    return ordered
APPROVAL_REQUIRED_EXIT_CODE = 75
_VALIDATORS = {"artifact_sha256", "json_object_fields"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STEP_RUNTIME_FIELDS = {
    "executor",
    "task_kind",
    "domain",
    "model_profile",
    "routing_policy",
    "policy_profile",
    "routing_reason_codes",
    "routing_reason_summary",
    "recommended_next_skill",
    "recommended_policy_profile",
    "policy_reason_summary",
    "policy_confidence",
    "user_selection_needed",
    "auto_execution_allowed",
    "comparison_id",
    "variant",
    "environment_fingerprint",
    "elapsed_ms",
    "duration_ms",
    "started_at",
    "finished_at",
    "failed_at",
    "provider_call_count",
    "tool_call_count",
    "tool_call_measurement_status",
    "timeout_sec",
    "skill_path",
    "skill_count",
    "failure_category",
    "provider_exit_status",
    "cost_metadata_status",
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def task_spec_sha256(task: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(task)).hexdigest()


def validate_task_spec(task: object) -> list[str]:
    if not isinstance(task, dict):
        return ["task_not_object"]
    if task.get("schema_version") != SCHEMA_VERSION:
        return ["schema_version_invalid"]
    errors: list[str] = []
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        errors.append("id_missing")
    elif not _ID_RE.fullmatch(task_id):
        errors.append("id_invalid")
    steps = task.get("steps")
    if not isinstance(steps, list) or not steps:
        return errors + ["steps_missing"]

    ids: list[str] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"steps.{index}.not_object")
            continue
        step_id = str(step.get("id") or "").strip()
        if not step_id:
            errors.append(f"steps.{index}.id_missing")
            continue
        if not _ID_RE.fullmatch(step_id):
            errors.append(f"steps.{index}.id_invalid")
            continue
        ids.append(step_id)
        if not str(step.get("prompt") or "").strip():
            errors.append(f"steps.{step_id}.prompt_missing")
        completion = step.get("completion")
        if not isinstance(completion, dict):
            errors.append(f"steps.{step_id}.completion_missing")
            continue
        validator_id = str(completion.get("validator_id") or "")
        if validator_id not in _VALIDATORS:
            errors.append(f"steps.{step_id}.completion.validator_id_unknown")
        output_path = str(completion.get("output_path") or "").strip()
        if not output_path:
            errors.append(f"steps.{step_id}.completion.output_path_missing")
        elif Path(output_path).is_absolute() or ".." in Path(output_path).parts:
            errors.append(f"steps.{step_id}.completion.output_path_unsafe")
        if 'target_path' in completion:
            target_path = completion['target_path']
            if validator_id != 'artifact_sha256':
                errors.append(f"steps.{step_id}.completion.target_validator_invalid")
            if not isinstance(target_path, str) or not target_path.strip() or Path(target_path).is_absolute() or '..' in Path(target_path).parts:
                errors.append(f"steps.{step_id}.completion.target_path_unsafe")
        if validator_id == "artifact_sha256" and not _SHA256_RE.fullmatch(
            str(completion.get("expected_sha256") or "")
        ):
            errors.append(f"steps.{step_id}.completion.expected_sha256_invalid")
        if validator_id == "json_object_fields":
            fields = completion.get("required_fields")
            if not isinstance(fields, list) or not fields or not all(isinstance(field, str) and field for field in fields):
                errors.append(f"steps.{step_id}.completion.required_fields_invalid")
        gate = step.get("approval_gate")
        if gate is not None:
            if not isinstance(gate, dict):
                errors.append(f"steps.{step_id}.approval_gate_invalid")
            else:
                if not str(gate.get("approval_id") or "").strip():
                    errors.append(f"steps.{step_id}.approval_id_missing")
                if not _SHA256_RE.fullmatch(str(gate.get("payload_sha256") or "")):
                    errors.append(f"steps.{step_id}.approval_payload_sha256_invalid")

    if len(ids) != len(set(ids)):
        errors.append("step_ids_not_unique")
    known_ids = set(ids)
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id") or "")
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list) or any(dep not in known_ids for dep in dependencies):
            errors.append(f"steps.{step_id}.depends_on_invalid")
    return errors


def _approval_path(root: Path, task_id: str, step_id: str) -> Path:
    return root / ".omc" / "state" / "autopilot" / "approvals" / task_id / f"{step_id}.json"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f"{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_approval_receipt(
    root: Path,
    *,
    task_id: str,
    task_spec_sha256: str,
    step_id: str,
    approval_id: str,
    payload_sha256: str,
    approved_at: str | None = None,
) -> Path:
    if not _SHA256_RE.fullmatch(task_spec_sha256) or not _SHA256_RE.fullmatch(payload_sha256):
        raise ValueError("approval_hash_invalid")
    receipt = {
        "schema_version": "omc-autopilot-approval/v1",
        "task_id": task_id,
        "task_spec_sha256": task_spec_sha256,
        "step_id": step_id,
        "approval_id": approval_id,
        "payload_sha256": payload_sha256,
        "approved_at": approved_at or datetime.now(timezone.utc).isoformat(),
    }
    path = _approval_path(root, task_id, step_id)
    _atomic_write_json(path, receipt)
    return path


def approval_receipt_valid(root: Path, *, task: dict[str, Any], step: dict[str, Any]) -> bool:
    gate = step.get("approval_gate")
    if gate is None:
        return True
    path = _approval_path(root, str(task["id"]), str(step["id"]))
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = {
        "schema_version": "omc-autopilot-approval/v1",
        "task_id": str(task["id"]),
        "task_spec_sha256": task_spec_sha256(task),
        "step_id": str(step["id"]),
        "approval_id": str(gate["approval_id"]),
        "payload_sha256": str(gate["payload_sha256"]),
    }
    return all(receipt.get(key) == value for key, value in expected.items()) and bool(receipt.get("approved_at"))


def validate_completion_artifact(root: Path, step: dict[str, Any]) -> tuple[str | None, str | None]:
    completion = step["completion"]
    target = (root / str(completion["output_path"])).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None, "completion_artifact_path_escape"
    if not target.is_file():
        return None, "completion_artifact_missing"
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    validator_id = completion["validator_id"]
    if validator_id == "artifact_sha256" and digest != completion["expected_sha256"]:
        return None, "completion_artifact_hash_mismatch"
    if validator_id == "json_object_fields":
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None, "completion_artifact_json_invalid"
        if not isinstance(payload, dict) or any(field not in payload for field in completion["required_fields"]):
            return None, "completion_artifact_fields_missing"
    return digest, None


def completion_artifact_snapshot(root: Path, step: dict[str, Any]) -> dict[str, Any]:
    """Capture enough state to reject an unchanged artifact after execution."""
    target = (root / str(step["completion"]["output_path"])).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return {"exists": False, "path_error": "completion_artifact_path_escape"}
    if not target.is_file():
        return {"exists": False}
    stat = target.stat()
    return {
        "exists": True,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "inode": stat.st_ino,
    }


def build_completion_receipt(
    *, task: dict[str, Any], step: dict[str, Any], artifact_sha256: str, predecessor_receipts: list[str]
) -> dict[str, Any]:
    base = {
        "schema_version": "omc-autopilot-completion/v1",
        "task_id": str(task["id"]),
        "task_spec_sha256": task_spec_sha256(task),
        "step_id": str(step["id"]),
        "validator_id": str(step["completion"]["validator_id"]),
        "artifact_sha256": artifact_sha256,
        "predecessor_receipts": predecessor_receipts,
    }
    return {**base, "receipt_sha256": hashlib.sha256(_canonical_json(base)).hexdigest()}


def completion_receipt_valid(
    receipt: object, *, task: dict[str, Any], step: dict[str, Any], predecessor_receipts: list[str]
) -> bool:
    if not isinstance(receipt, dict):
        return False
    artifact_sha256 = str(receipt.get("artifact_sha256") or "")
    if not _SHA256_RE.fullmatch(artifact_sha256):
        return False
    expected = build_completion_receipt(
        task=task,
        step=step,
        artifact_sha256=artifact_sha256,
        predecessor_receipts=predecessor_receipts,
    )
    return receipt == expected


def output_diagnostics(output: str) -> dict[str, Any]:
    """Return content-free diagnostics suitable for durable state."""
    return {
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "output_length": len(output),
    }


def sanitize_step_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Keep schema-v2 telemetry while replacing provider output with diagnostics."""
    sanitized = {
        key: value
        for key, value in runtime.items()
        if key in _STEP_RUNTIME_FIELDS
    }
    partial_output = runtime.get("partial_output")
    if isinstance(partial_output, str) and partial_output:
        sanitized["partial_output_diagnostics"] = output_diagnostics(partial_output)
    return sanitized
