#!/usr/bin/env python3
"""Collect a native task final verbatim and assess a controlled workflow run.

Observations are not attestations. Known verification arguments are checked;
this collector is not a sandbox or a general shell/filesystem auditor.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path

from omc_output_contract import OutputContractError, parse_envelope
from omc_workflow_preflight import assess_scope
from omc_visual_check import CHECK_ID as VISUAL_ID, assess_report as assess_visual
from omc_pipeline_guard import _load_approved_basis, evaluate_task_completion, completion_command

STATUSES = {
    '필수 검증 통과·작업 완료', '구현됨·검증 미완료',
    '구현 여부 확인 불가·검증 미완료', '검증 실패·수정 필요', '증거 불일치·재검증 필요',
}


def _single_bash_argv(command: str) -> list[str]:
    """Recognize a single literal invocation; never interpret composite shell code.

    Conservative V0: substitutions and multiline arguments remain unsupported,
    even when quoted. Quoted literal punctuation is allowed without evaluation.
    """
    from omc_native_command import single_argv
    return single_argv(command)


def _output_protocol_return(root, args, text, expected_context):
    """Only explicit detached runs recognize these narrow local protocol effects."""
    if not expected_context or expected_context.get('output_mode')!='detached_v1':return False
    from omc_output_contract import capabilities,load_output_context,record_path
    if args==['python3','scripts/omc_output_contract.py','capabilities']:
        return json.loads(text)==capabilities()
    if args==['python3','scripts/omc_workflow_validation.py','capabilities']:
        return json.loads(text)==output_capabilities()
    if args[:3]!=['python3','scripts/omc_output_contract.py','prepare']:return False
    tail=args[3:]
    if len(tail)%2:return False
    opts={};refs=[]
    for flag,value in zip(tail[::2],tail[1::2]):
        if flag=='--reference':refs.append(value)
        elif flag in {'--target','--stage','--report-id','--budget-sec'} and flag not in opts:opts[flag]=value
        else:return False
    if set(opts)!={'--target','--stage','--report-id','--budget-sec'}:return False
    if (Path(opts['--target']).resolve()!=root.resolve() and (root/opts['--target']).resolve()!=root.resolve()):return False
    if (opts['--stage']!=expected_context['stage'] or opts['--report-id']!=expected_context['report_id']
            or not 0<float(opts['--budget-sec'])<=480):return False
    meta=json.loads(text)
    actual=load_output_context(root,meta['output_context'],meta['output_context_sha256'],live_deadline=False)
    if actual.get('budget_sec')!=float(opts['--budget-sec']):return False
    if [(root/p).resolve() for p in refs]!=[(root/r['path']).resolve() for r in actual['references']]:return False
    return actual==expected_context and meta['record_path']==str(record_path(root,expected_context))


def assess_native_procedure(root: Path, rows: list, basis: dict, events_file: Path | None = None, output_context: dict | None = None) -> dict:
    """Opt-in result-order audit. Rejected writes are not successful edits.

    Only fixed public commands and documented read-only inspection commands are
    understood. Arbitrary Bash is unknown; this is not shell containment.
    """
    from omc_pipeline_guard import _is_test_file
    pins = [c['native_red'] for c in basis.get('checks', []) if 'native_red' in c]
    if not pins:
        return {'status': 'unknown', 'reason': 'native_red_not_selected'}
    calls, returned = {}, set()
    contract_at = red_at = observed_at = None
    failures, unknown, unsupported_commands = [], [], []
    for index, row in enumerate(rows):
        for block in row.get('message', {}).get('content', []):
            if block.get('type') == 'tool_use':
                ident = block.get('id')
                if not ident or ident in calls or row.get('type') != 'assistant':
                    unknown.append('ambiguous_tool_call')
                    continue
                calls[ident] = (index, block, contract_at, red_at, observed_at, bool(unknown))
            elif block.get('type') == 'tool_result':
                ident = block.get('tool_use_id')
                if ident not in calls or ident in returned or row.get('type') != 'user':
                    unknown.append('unlinked_tool_result')
                    continue
                returned.add(ident)
                start, call, prior_contract, prior_red, prior_observation, prior_unresolved = calls[ident]
                if block.get('is_error'):
                    continue
                name, inp = call.get('name'), call.get('input', {})
                if name in {'Write', 'Edit', 'MultiEdit'}:
                    path = inp.get('file_path')
                    if not isinstance(path, str):
                        unknown.append('edit_path_unknown')
                        continue
                    try:
                        relative = str((root/path).resolve().relative_to(root.resolve()))
                    except ValueError:
                        failures.append('edit_outside_candidate')
                        continue
                    if relative.startswith('.omc/'):
                        continue
                    if prior_contract is None:
                        (unknown if prior_unresolved else failures).append('edit_before_contract_success')
                    if not _is_test_file(relative):
                        if prior_red is None:
                            (unknown if prior_unresolved else failures).append('product_edit_before_red_success')
                        if not any(relative == p or relative.startswith(p.rstrip('/')+'/') for p in pins[0]['product_paths']):
                            failures.append('product_edit_outside_approved_scope')
                elif name == 'Bash':
                    args = _single_bash_argv(inp.get('command', ''))
                    text = block.get('content', '')
                    if isinstance(text, list):
                        text = ''.join(c.get('text', '') for c in text if c.get('type') == 'text')
                    from omc_native_command import validation_profile
                    profile = validation_profile(inp.get('command', ''))
                    if output_context and args[:2] in [['python3','scripts/omc_output_contract.py'],['python3','scripts/omc_workflow_validation.py']]:
                        try:
                            if not _output_protocol_return(root,args,text,output_context):raise ValueError('output_protocol_return_mismatch')
                        except (OSError,ValueError,TypeError,KeyError) as exc:
                            unknown.append('unsupported_output_protocol:'+str(exc))
                    elif profile is not None:
                        try:
                            from omc_validation_command import validate_receipt
                            pin = basis['validation_commands_policy_sha256']
                            value = json.loads(text)
                            doc = validate_receipt(root,value['receipt'],pin,profile,historical=True)
                            if doc['status'] != 'passed' or value['status'] != doc['status']:
                                raise ValueError('validation_command_not_passed')
                        except (OSError,ValueError,KeyError,TypeError) as exc:
                            unknown.append('validation_command_unknown:'+str(exc))
                    elif args[:3] in [
                            ['python3','scripts/omc_validation_command.py',a]
                            for a in ('task-context','selection-path')]:
                        try:
                            from omc_validation_command import load_policy, runtime_command_result
                            frozen, pin = load_policy(root, basis['validation_commands_policy_sha256'])
                            expected = runtime_command_result(root, inp.get('command', ''), frozen, pin)
                            if expected is None or json.loads(text) != expected:
                                raise ValueError('runtime_query_return_mismatch')
                        except (OSError,ValueError,KeyError,TypeError) as exc:
                            unknown.append('runtime_query_unknown:'+str(exc))
                    elif (len(args) >= 3 and args[:2] == ['python3', 'scripts/omc_pipeline_guard.py']
                            and not any(a in {';', '&&', '||', '|', '>'} for a in args)):
                        action = args[2]
                        if action == 'contract-done' and 'CONTRACT 확인 완료' in text:
                            contract_at = index
                        elif action == 'red-done' and 'RED 완료 등록' in text:
                            if prior_contract is None or prior_observation is None:
                                (unknown if prior_unresolved else failures).append('red_registration_before_execution_success')
                            try:
                                from omc_native_red_evidence import local, validate_evidence
                                pos = args.index('--evidence-file')
                                doc = json.loads(local(root, args[pos+1]).read_bytes())
                                validate_evidence(root, doc, historical=True, events_file=events_file)
                                if events_file is not None:
                                    from omc_native_red_evidence import selected
                                    custody = selected(root, doc['native_receipt'])
                                    before_request = sum(len(line) for line in events_file.read_bytes().splitlines(keepends=True)[:start])
                                    if custody['prefix_bytes'] > before_request:
                                        raise ValueError('native_red_receipt_after_registration_request')
                                if args[3] != doc['test_file']:
                                    raise ValueError('native_red_test_mismatch')
                                red_at = index
                            except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                                failures.append('invalid_native_red_registration:'+str(exc))
                        elif action not in {'status','check','check-edit','verification-path','verification-template','complete-task','approve-checks','contract-done','red-done'}:
                            unknown.append('unsupported_guard_command')
                    elif args == ['python3','scripts/omc_native_red_evidence.py','observe','--target','.']:
                        if prior_contract is None:
                            (unknown if prior_unresolved else failures).append('red_execution_before_contract_success')
                        try:
                            value = json.loads(text)
                            if value.get('schema_version') != 'omc-native-red-result/v1':
                                raise ValueError('observation_unknown')
                            from omc_native_red_evidence import validate_execution
                            record = validate_execution(root, value['execution'])
                            if record['test_exit_code'] != 1 or value.get('failed_cases') != record['failed_cases']:
                                raise ValueError('observation_mismatch')
                            observed_at = index
                        except (OSError, ValueError, TypeError, KeyError, AttributeError):
                            unknown.append('native_red_execution_unknown')
                    elif args[:2] == ['python3','scripts/omc_native_red_evidence.py'] and len(args) > 2 and args[2] in {'receipts','evidence','diagnose'}:
                        pass
                    elif args in [
                            ['python3','scripts/omc_home_observation.py','observe','--target','.'],
                            ['python3','scripts/omc_home_observation.py','receipts','--target','.'],
                            ['pwd'], ['git','status','--short'], ['git','diff'], ['git','diff','--stat'],
                            ['git','diff','--cached'], ['git','diff','--name-only']]:
                        pass  # Fixed observation/inspection commands; no shell expansion.
                    elif len(args) > 3 and args[:3] == ['git','add','--']:
                        try:
                            names = [str((root/name).resolve().relative_to(root.resolve())) for name in args[3:]]
                            if any('*' in name or not any(name == p or name.startswith(p.rstrip('/')+'/') for p in pins[0]['product_paths']) for name in names):
                                raise ValueError('unapproved_staging_path')
                        except ValueError:
                            unknown.append('unsupported_staging_command')
                    else:
                        from omc_native_command import read_command_reason
                        reason = read_command_reason(root, inp.get('command', ''),
                                                     historical=events_file is not None)
                        if reason is not None:
                            unknown.append('unsupported_bash_command')
                            unsupported_commands.append({'tool_use_id': ident,
                                'request_line': start+1, 'return_line': index+1, 'reason': reason})
                elif name not in {'Read','Glob','Grep'}:
                    unknown.append('unsupported_native_tool')
    if set(calls)-returned:
        unknown.append('missing_tool_return')
    if not any(r.get('type') == 'result' for r in rows):
        unknown.append('native_stream_incomplete')
    if contract_at is None or red_at is None or observed_at is None:
        unknown.append('required_success_return_missing')
    return {'status': 'failed' if failures else 'unknown' if unknown else 'passed',
            'reason': ';'.join(dict.fromkeys(failures+unknown)),
            'contract_return': contract_at, 'observation_return': observed_at, 'red_return': red_at,
            'unsupported_commands': unsupported_commands}


def _detached_evidence(root, rows, final, sid, evaluation, command, context_file, context_pin, record_file, record_pin):
    from omc_output_contract import load_output_context, read_record, _local_file
    context=load_output_context(root,context_file,context_pin,live_deadline=False)
    if context['session_id']!=sid or context['stage']!='task':raise ValueError('output_context_session_or_stage_mismatch')
    doc=read_record(root,record_file,record_pin,context,display=final)
    if doc['source_kind']!='completion_gate':raise ValueError('model_declaration_is_not_completion_evidence')
    if doc['machine_payload']['outcome']!=evaluation['outcome'] or not final.startswith(evaluation['first']+'\n'):
        raise ValueError('output_evaluation_mismatch')
    from omc_native_command import single_argv
    calls={}; found=[]
    for row in rows:
        for block in row.get('message',{}).get('content',[]):
            if block.get('type')=='tool_use' and row.get('type')=='assistant':
                ident=block.get('id')
                if ident in calls:raise ValueError('duplicate_output_call')
                calls[ident]=block
            elif block.get('type')=='tool_result' and row.get('type')=='user':
                call=calls.get(block.get('tool_use_id'),{})
                if call.get('name')!='Bash':continue
                args=single_argv(call.get('input',{}).get('command',''))
                if len(args)<3 or args[:2]!=['python3','scripts/omc_pipeline_guard.py'] or args[2]!=command:continue
                options=args[3:]; allowed={'--verification-file','--output-mode','--output-context','--output-context-sha256'}
                if len(options)%2 or len(options)!=8 or set(options[::2])!=allowed:raise ValueError('unsupported_detached_command_options')
                opts=dict(zip(options[::2],options[1::2]))
                if (opts['--output-mode']!='detached_v1' or opts['--output-context-sha256']!=context_pin
                        or _local_file(root,opts['--output-context'])!=_local_file(root,context_file)
                        or not context['native_call_reference']['invocation_id']):
                    raise ValueError('output_call_context_mismatch')
                # exit 1 is the expected business block, not a delivery failure.
                exit_code=block.get('exit_code')
                if (exit_code not in (0,1) or isinstance(exit_code,bool)
                        or exit_code!=(1 if evaluation['outcome']=='blocked' else 0)
                        or (exit_code==0 and block.get('is_error') is True)):
                    raise ValueError('output_delivery_return_unconfirmed')
                if doc['gate_binding']['verification_file']!=str(_local_file(root,opts['--verification-file'])):
                    raise ValueError('gate_verification_reference_mismatch')
                if block.get('content')!=doc['display']:raise ValueError('output_return_body_mismatch')
                found.append(({**doc,'observed_native_call_id':block.get('tool_use_id')},opts['--verification-file']))
    if len(found)!=1:raise ValueError('output_call_return_missing_or_ambiguous')
    return found[0]


def collect(root: Path, events_file: Path, sid: str, verification: Path,
            expected: str, output: Path, scope_report: Path | None = None,
            scope_report_sha256: str | None = None, visual_report: Path | None = None,
            visual_report_sha256: str | None = None, *, output_mode='legacy_inline',
            output_context=None, output_context_sha256=None, output_record=None, output_record_sha256=None) -> dict:
    source = events_file.read_bytes()
    rows = [json.loads(line) for line in source.decode('utf-8').splitlines() if line.strip()]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('native event must be an object')
        message = row.get('message', {})
        if not isinstance(message, dict):
            raise ValueError('native event message must be an object')
        content = message.get('content', [])
        if not isinstance(content, list):
            raise ValueError('native message content must be a list')
        for block in content:
            if not isinstance(block, dict):
                raise ValueError('native content block must be an object')
            if block.get('type') == 'tool_use' and not isinstance(block.get('input', {}), dict):
                raise ValueError('native tool input must be an object')
    finals = [r for r in rows if r.get('type') == 'result']
    if len(finals) != 1 or not isinstance(finals[0].get('result'), str):
        raise ValueError('native result must contain exactly one final response')
    final = finals[0]['result']
    # Preserve the complete response; never normalize or repair explicit output.
    evaluation = evaluate_task_completion(root, verification, expected_session_id=sid)
    try:
        gate_command = completion_command(root,sid)
    except (OSError,ValueError,KeyError,TypeError):
        gate_command = 'complete-task'
    transfer_mode = gate_command == 'complete-transfer'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'final.txt').write_text(final, encoding='utf-8')
    issues = []
    detached=None
    try:
        if output_mode=='detached_v1':
            if not all((output_context,output_context_sha256,output_record,output_record_sha256)):
                raise OutputContractError('detached_reference_required')
            detached,detached_verification=_detached_evidence(root,rows,final,sid,evaluation,gate_command,
                output_context,output_context_sha256,output_record,output_record_sha256)
            if (root/detached_verification).resolve()!=verification.resolve():raise OutputContractError('output_verification_mismatch')
            envelope={**detached['machine_payload'],'verdict':detached['verdict']}
        elif output_mode=='legacy_inline':
            if any((output_context,output_context_sha256,output_record,output_record_sha256)):
                raise OutputContractError('mixed_output_mode')
            envelope = parse_envelope(final)
        else:raise OutputContractError('unsupported_output_mode')
        semantic = 'passed' if envelope['stage'] == 'task' and envelope['outcome'] == expected else 'failed'
    except (OutputContractError,OSError,ValueError,TypeError,KeyError) as exc:
        envelope = None
        semantic = 'unknown'
        issues.append(str(exc))
    lines = [line.strip().strip('#*` ').strip() for line in final.splitlines() if line.strip()]
    # Declared body states are semantic evidence in both outcome directions.
    body_states = [line for line in lines if line in STATUSES]
    expected_states = {evaluation['first']}
    if any(status not in expected_states for status in body_states):
        semantic = 'failed'
        issues.append('본문 상태와 필수 검증 판정이 충돌합니다')
    substantive = [line for line in lines if line not in {'---', '***', '___'}]
    if semantic == 'passed':
        if not substantive or substantive[0] not in expected_states:
            semantic = 'unknown'
            issues.append('본문 상태를 확인할 수 없어 기계 판정만으로 통과시키지 않습니다')
        elif expected == 'blocked':
            # A named test passing is compatible with another required check
            # remaining unrun. Only whole-task claims require reconciliation.
            overall_claim = re.compile(
                r'(?:작업|전체|구현)\s*(?:을|이|은|는|이\s*)?\s*완료'
                r'|(?:모든|전체)\s*(?:필수\s*)?검증.*?통과'
            )
            body = [line for line in lines if line not in STATUSES
                    and not line.startswith(('<!-- OMC_OUTPUT:', 'OMC_OUTPUT:', 'VERDICT:'))]
            if any(overall_claim.search(line) for line in body):
                semantic = 'unknown'
                issues.append('전체 완료 주장은 별도 의미 확인이 필요합니다')
    format_quality = {
        'first_line': bool(lines) and final.splitlines()[0] in STATUSES,
        'unique_status': sum(line in STATUSES for line in lines) == 1,
        'single_next_action': sum(bool(re.match(r'^(?:[-+*]\s+|[0-9]+[.)]\s+)?(?:\*\*|__)?(?:다음 행동|다음 액션)(?:\*\*|__)?\s*:', line)) for line in lines) == 1,
    }
    registered = evaluation['registered']
    calls = {}
    write_results = {}
    evidence = []
    verification_paths = []
    known_writes = []
    for index, row in enumerate(rows):
        content = row.get('message', {}).get('content', [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                calls[block.get('id')] = (index, block)
                if block.get('name') in {'Write', 'Edit'}:
                    value = block.get('input', {}).get('file_path')
                    if isinstance(value, str):
                        known_writes.append(str((root / value).resolve()))
                if block.get('name') == 'Bash':
                    command = block.get('input', {}).get('command', '')
                    if gate_command in command:
                        try:
                            args = shlex.split(command)
                            positions = [i for i, arg in enumerate(args) if arg == '--verification-file']
                            verification_paths.extend(args[i + 1] for i in positions if i + 1 < len(args))
                        except ValueError:
                            verification_paths.append('UNPARSEABLE')
            elif block.get('type') == 'tool_result' and block.get('tool_use_id') in calls:
                call_index, call = calls[block['tool_use_id']]
                if call.get('name') in {'Write', 'Edit'}:
                    write_results[block['tool_use_id']] = not block.get('is_error', False)
                command = call.get('input', {}).get('command', '')
                text = block.get('content', '')
                if not isinstance(text, str):
                    text = json.dumps(text, ensure_ascii=False)
                for token, marker in [('contract-done', 'CONTRACT 확인 완료'), ('red-done', 'RED 완료 등록')]:
                    if call.get('name') == 'Bash' and token in command and marker in text and not block.get('is_error'):
                        evidence.append((token, call_index))
                if transfer_mode and call.get('name') == 'Bash':
                    literal_guard=False;args=[]
                    try:
                        args = _single_bash_argv(command)
                        literal_guard = len(args)>2 and Path(args[1]).name=='omc_pipeline_guard.py'
                        if literal_guard and 'register-transfer-baseline' in args and not block.get('is_error') and json.loads(text).get('status')=='registered':
                            evidence.append(('register-transfer-baseline',call_index))
                    except (ValueError,TypeError,AttributeError):
                        pass
                if call.get('name') == 'Bash' and gate_command in command:
                    try:
                        gate = ({**detached['machine_payload'],'verdict':detached['verdict']} if detached and text==detached['display'] else parse_envelope(text))
                        gate_states = [line.strip().strip('#*` ').strip() for line in text.splitlines()
                                       if line.strip().strip('#*` ').strip() in STATUSES]
                        if (gate['stage'] == 'task' and gate['outcome'] == expected == evaluation['outcome']
                                and gate_states and all(state == evaluation['first'] for state in gate_states)):
                            if detached or not transfer_mode or (literal_guard and gate_command in args and not block.get('is_error')):
                                evidence.append((gate_command, call_index))
                    except OutputContractError:
                        pass
    order = [next((i for name, i in evidence if name == token), None)
             for token in (['contract-done','register-transfer-baseline','complete-transfer'] if transfer_mode else ['contract-done','red-done','complete-task'])]
    procedure = all(i is not None for i in order) and order == sorted(order, key=lambda x: -1 if x is None else x)
    path_state = 'unknown'
    observed = []
    if verification_paths:
        path_state = 'passed'
        for value in verification_paths:
            candidate = (root / value).resolve()
            observed.append(str(candidate))
            try:
                candidate.relative_to(root.resolve())
                internal = True
            except ValueError:
                internal = False
            if value == 'UNPARSEABLE' or not internal or candidate != verification.resolve():
                path_state = 'failed'
        if not verification.is_file():
            path_state = 'unknown' if path_state == 'passed' else path_state
    for value in known_writes:
        try:
            Path(value).relative_to(root.resolve())
        except ValueError:
            path_state = 'failed'
    approved_scope = 'unknown'
    try:
        _load_approved_basis(root, sid)
        approved_scope = 'passed'
        if expected != evaluation['outcome'] or (envelope and envelope.get('outcome') != evaluation['outcome']):
            semantic = 'failed'
            issues.append('승인된 검증 기준과 최종 완료 주장이 충돌합니다')
        issues.extend(evaluation['problems'])
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        issues.append(str(exc))
    required = {
        'approved_scope': approved_scope,
        'registration': 'passed' if registered else 'unknown' if registered is None else 'failed',
        'procedure_calls': 'passed' if procedure else 'unknown',
        'truthful_final': semantic,
        'verification_scope': path_state,
        'native_exit': 'passed' if finals[0].get('subtype') == 'success' and not finals[0].get('is_error') else 'failed',
    }
    scope_assessment = None
    if scope_report is not None or scope_report_sha256 is not None:
        try:
            if scope_report is None or not scope_report_sha256:
                raise ValueError('scope_report_and_digest_required')
            basis = _load_approved_basis(root, sid)
            confirmed, unresolved = [], []
            for call_id, (_, call) in calls.items():
                if call.get('name') in {'Write', 'Edit'}:
                    value = call.get('input', {}).get('file_path')
                    if isinstance(value, str):
                        if call_id not in write_results:
                            unresolved.append(str((root / value).resolve()))
                        elif write_results[call_id]:
                            confirmed.append(str((root / value).resolve()))
            scope_assessment = assess_scope(root, scope_report, scope_report_sha256, confirmed,
                                            {item['id'] for item in basis['checks']}, unresolved)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            scope_assessment = {'status': 'unknown', 'violations': [], 'reason': str(exc)}
        required['work_scope'] = scope_assessment['status']
        required['report_format'] = 'passed' if all(format_quality.values()) else 'failed'
        if scope_assessment['reason']:
            issues.append(scope_assessment['reason'])
    visual_assessment = evaluation.get('visual_assessment')
    try:
        basis = _load_approved_basis(root, sid)
        visual_check = next((c for c in basis['checks'] if c['id'] == VISUAL_ID), None)
        if basis.get('task_profile') == 'advertising-visual/v1' or (root/'.omc/visual-requirement.json').exists():
            visual_assessment = visual_assessment or {'status': 'unknown', 'reason': 'required_visual_result_missing'}
            required['visual_controlled'] = visual_assessment['status']
            if visual_assessment['reason']:
                issues.append(visual_assessment['reason'])
        elif visual_check is not None or visual_report is not None or visual_report_sha256 is not None:
            visual_assessment = assess_visual(root, visual_report, visual_report_sha256,
                visual_check.get('visual_manifest_sha256', '') if visual_check else '')
            required['visual_controlled'] = visual_assessment['status']
            if visual_assessment['reason']:
                issues.append(visual_assessment['reason'])
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        if visual_report is not None or visual_report_sha256 is not None:
            required['visual_controlled'] = 'unknown'
        issues.append(str(exc))
    home_assessment = None
    try:
        from omc_home_observation import assess_completion_home
        home_record = json.loads(verification.read_bytes())
        home_assessment = assess_completion_home(root, _load_approved_basis(root, sid), events_file=events_file,
                                                 submission=home_record.get('home_motion_result'))
        if home_assessment is not None:
            required['home_motion'] = home_assessment['status']
            if home_assessment['reason']:
                issues.append(home_assessment['reason'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        required['home_motion'] = 'unknown'
        issues.append(str(exc))
    native_procedure = None
    try:
        native_basis = _load_approved_basis(root, sid)
        if any('native_red' in c for c in native_basis['checks']):
            native_procedure = assess_native_procedure(root, rows, native_basis, events_file,
                output_context=detached['context'] if detached else None)
            required['native_procedure'] = native_procedure['status']
            if native_procedure['reason']:
                issues.append(native_procedure['reason'])
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        required['native_procedure'] = 'unknown'
        issues.append(str(exc))
    assessment = {
        'schema_version': 'omc-workflow-validation/v1',
        'output_mode': output_mode, 'output_record': str(output_record) if detached else None,
        'completion_command': gate_command,
        'final_sha256': hashlib.sha256(final.encode('utf-8')).hexdigest(),
        'events_sha256': hashlib.sha256(source).hexdigest(),
        'required': required, 'format_quality': format_quality,
        'recompare_entry': 'allow' if all(value == 'passed' for value in required.values()) else 'hold',
        'scope_assessment': scope_assessment, 'native_procedure': native_procedure,
        'visual_assessment': visual_assessment, 'home_assessment': home_assessment,
        'issues': issues, 'observed_verification_paths': observed, 'observed_direct_write_paths': known_writes,
        'boundary': 'Known CLI arguments and returned outputs only; not filesystem containment or evidence authentication.',
    }
    # Separate semantic state from existing format-only quality checks.
    model_state = substantive[0] if substantive and substantive[0] in STATUSES else None
    comparison = {'official_state': evaluation['first'], 'official_outcome': evaluation['outcome'],
                  'model_state': model_state, 'state_match': model_state == evaluation['first'],
                  'model_first_line': final.splitlines()[0] if final.splitlines() else '',
                  'first_line_exact': bool(final.splitlines()) and final.splitlines()[0] == evaluation['first'],
                  'overall_decision': assessment['recompare_entry'], 'model_original': 'final.txt',
                  'boundary': '공식 판정은 현재 검증 평가이며 모델 원문을 교정하거나 대체하지 않습니다.'}
    (output / 'report-comparison.json').write_text(json.dumps(comparison, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    procedure_note = ''
    diagnostics = (native_procedure or {}).get('unsupported_commands', [])
    if diagnostics:
        labels = {'syntax_unsupported': '구문 미지원', 'command_unsupported': '명령 미지원',
                  'path_unverified': '경로 확인 불가',
                  'historical_path_unverified': '당시 경로 증거 없음'}
        procedure_note = '\n절차 확인: 일부 Bash 명령의 효과를 확인하지 못했습니다.'
        for diagnostic in diagnostics:
            procedure_note += ('\n호출 ' + str(diagnostic['request_line']) + '행 / 반환 '
                + str(diagnostic['return_line']) + '행: ' + labels[diagnostic['reason']])
        procedure_note += '\n미확인 명령을 통과나 위반으로 간주하지 않으며 전체 비교는 보류합니다.\n'
    (output / 'report-comparison.txt').write_text(
        '공식 판정: ' + evaluation['first'] + '\n모델 보고 상태: ' + (model_state or '확인 불가')
        + '\n상태 일치: ' + ('일치' if comparison['state_match'] else '불일치')
        + '\n전체 판정: ' + assessment['recompare_entry'] + '\n' + procedure_note, encoding='utf-8')
    (output / 'assessment.json').write_text(json.dumps(assessment, ensure_ascii=False, indent=2) + '\n')
    return assessment


def assess_common_validation(root: Path, rows: list, selection: dict, policy_pin: str) -> dict:
    """New opt-in comparison contract; never rewrites the legacy overall gate.

    Common observations are distinct from OMC internal protocol compliance.
    Local custody and source scope are not adversarial effect containment.
    """
    from omc_validation_command import load_policy, validate_receipt, REQUIRED, CONTRACT, _scope, _raw
    from omc_native_command import validation_profile
    result = {'schema_version':'omc-common-validation/v2','contract_id':CONTRACT,
        'common_work':'unknown','internal_procedure':'unknown','comparison_entry':'hold',
        'claim_boundary':'SINGLE_ARM_LOCAL_EVIDENCE_ONLY','checks':{},'issues':[]}
    try:
        policy,_ = load_policy(root,policy_pin)
        if 'browser_evidence_contract' in policy:
            result['browser_evidence_contract'] = policy['browser_evidence_contract']
        if (selection.get('schema_version') != 'omc-common-selection/v2'
                or selection.get('contract_id') != CONTRACT or selection.get('run_id') != policy['run_id']
                or set(selection.get('receipts',{})) != set(REQUIRED)):
            raise ValueError('explicit_new_comparison_selection_required')
        result['internal_procedure'] = 'not_applicable' if policy['arm']=='oh-my-claudecode' else 'unknown'
        if policy['arm']=='omc-kit' and isinstance(policy.get('session_id'),str):
            try:
                internal_basis=_load_approved_basis(root,policy['session_id'])
                if internal_basis.get('validation_commands_policy_sha256')==policy_pin:
                    result['internal_procedure']=assess_native_procedure(root,rows,internal_basis)['status']
            except (OSError,ValueError,KeyError,TypeError):
                pass  # Missing internal evidence is not a common-work pass.
        _scope(root,policy)
        calls,returned,observed,ids,writes,product_writes = {},set(),{},set(),set(),set()
        red_at = None
        red_cases = set()
        selected_red = selection.get('red_receipt')
        for index,row in enumerate(rows):
            for block in row.get('message',{}).get('content',[]):
                if block.get('type')=='tool_use':
                    ident=block.get('id')
                    if row.get('type')!='assistant' or not ident or ident in calls:
                        raise ValueError('ambiguous_common_call')
                    calls[ident]=(index,block,red_at)
                elif block.get('type')=='tool_result':
                    ident=block.get('tool_use_id')
                    if row.get('type')!='user' or ident not in calls or ident in returned:
                        raise ValueError('unlinked_common_return')
                    returned.add(ident)
                    start,call,prior_red=calls[ident];name=call.get('name');inp=call.get('input',{})
                    if name=='Bash':
                        command=inp.get('command','');profile=validation_profile(command)
                        if profile is not None:
                            text=block.get('content','')
                            if isinstance(text,list):text=''.join(c.get('text','') for c in text if c.get('type')=='text')
                            value=json.loads(text)
                            ref=value['receipt']
                            is_red=ref==selected_red and profile=='tests'
                            is_selected=ref==selection['receipts'].get(profile)
                            doc=validate_receipt(root,ref,policy_pin,profile,historical=not is_selected)
                            if doc['call_id'] in ids or value['status']!=doc['status']:
                                raise ValueError('reused_or_mismatched_common_receipt')
                            ids.add(doc['call_id'])
                            if is_red:
                                if doc['exit_code']!=1 or not doc['judgment'].get('new_failed_cases'):
                                    raise ValueError('genuine_new_red_required')
                                from omc_pipeline_guard import _is_test_file
                                for path,item in doc['scope_before'].items():
                                    if path.startswith('libs/sellerhub-store/src/lib/components/Ad/') and not _is_test_file(path):
                                        if item.get('working') != policy['baseline_scope'].get(path,{}).get('working'):
                                            raise ValueError('product_changed_before_red_execution')
                                red_at=index
                                red_cases=set(doc['judgment']['new_failed_cases'])
                            elif ref==selection['receipts'].get(profile):
                                if block.get('is_error') and doc['status']=='passed':
                                    raise ValueError('failed_call_claimed_passed')
                                observed[profile]=doc
                        else:
                            args=_single_bash_argv(command)
                            from omc_validation_command import runtime_command_result
                            expected = runtime_command_result(root, command, policy, policy_pin)
                            if expected is not None:
                                text = block.get('content', '')
                                if isinstance(text, list):
                                    text = ''.join(c.get('text','') for c in text if c.get('type')=='text')
                                if block.get('is_error') or json.loads(text) != expected:
                                    raise ValueError('runtime_query_return_mismatch')
                                continue
                            # Internal protocol commands are audited separately. No
                            # general shell exemption, free Python or tool calls.
                            internal=(policy['arm']=='omc-kit' and len(args)>2
                                and args[:2]==['python3','scripts/omc_pipeline_guard.py']
                                and args[2] in {'contract-done','red-done','verification-path',
                                    'verification-template','complete-task','status','check-edit'})
                            internal_observation=(policy['arm']=='omc-kit' and len(args)>2 and (
                                (args[:2]==['python3','scripts/omc_native_red_evidence.py']
                                    and args[2] in {'observe','receipts','evidence','diagnose'})
                                or args in [['python3','scripts/omc_home_observation.py',action,'--target','.']
                                            for action in ['observe','receipts']]))
                            if internal or internal_observation:
                                if any(i+1>=len(args) or args[i+1] not in {'.',str(root)}
                                       for i,arg in enumerate(args) if arg=='--target'):
                                    raise ValueError('internal_target_mismatch')
                                if any(arg.startswith('--target=') for arg in args):
                                    raise ValueError('internal_target_form_unsupported')
                                script=(root/args[1]).resolve()
                                from omc_validation_command import sha
                                if policy['oracle_pins'].get(str(script))!=sha(script.read_bytes()):
                                    raise ValueError('internal_observation_tool_unpinned')
                            inspection=args in [['pwd'],['git','status','--short'],['git','diff'],
                                ['git','diff','--cached'],['git','diff','--stat'],['git','diff','--name-only']]
                            staging=(len(args)>3 and args[:3]==['git','add','--']
                                and all(p.startswith('libs/sellerhub-store/src/lib/components/Ad/')
                                        and '..' not in Path(p).parts for p in args[3:]))
                            if not (internal or internal_observation or inspection or staging):
                                raise ValueError('unsupported_common_command')
                    elif name in {'Edit','Write','MultiEdit'} and not block.get('is_error'):
                        path=(root/inp['file_path']).resolve().relative_to(root.resolve())
                        if path.parts[0]=='.omc':continue
                        from omc_pipeline_guard import _is_test_file
                        if not str(path).startswith('libs/sellerhub-store/src/lib/components/Ad/'):
                            raise ValueError('common_edit_outside_scope')
                        writes.add(str(path))
                        if not _is_test_file(str(path)) and prior_red is None:
                            raise ValueError('common_product_edit_before_red_return')
                        if not _is_test_file(str(path)):
                            product_writes.add(str(path))
                    elif name not in {'Read','Glob','Grep','Edit','Write','MultiEdit'}:
                        raise ValueError('unsupported_common_tool')
        if set(calls)!=returned or red_at is None:
            raise ValueError('common_returns_or_red_missing')
        from omc_workflow_preflight import baseline
        current=baseline(root)
        changed={n for n in set(current)|set(policy['baseline_scope'])
            if current.get(n,{}).get('working')!=policy['baseline_scope'].get(n,{}).get('working')}
        if not product_writes or not changed <= writes:
            raise ValueError('common_product_change_trace_missing')
        if set(observed)!=set(REQUIRED):
            raise ValueError('common_validation_receipts_missing')
        result['profiles']={key:doc['status'] for key,doc in observed.items()}
        final_tests=json.loads(_raw(root,observed['tests']['artifacts']['jest']))
        passed_cases={case['fullName'] for suite in final_tests['testResults']
                      for case in suite['assertionResults'] if case['status']=='passed'}
        def combined(*profiles):
            statuses=[observed[key]['status'] for key in profiles]
            return 'failed' if 'failed' in statuses else 'passed' if set(statuses)=={'passed'} else 'unknown'
        result['checks']={'tests':combined('tests'),'lint':combined('lint'),
            'browser-controlled':combined('build-controlled','controlled'),
            'browser-home':combined('home'),
            'browser-visual-controlled':combined('build-pixels','pixels-dpr1','pixels-dpr2')}
        result['common_work']=('failed' if 'failed' in result['checks'].values() else
                               'passed' if set(result['checks'].values())=={'passed'} else 'unknown')
        if not red_cases <= passed_cases:
            raise ValueError('red_cases_not_passed')
        finals=[r for r in rows if r.get('type')=='result']
        if len(finals)!=1 or not isinstance(finals[0].get('result'),str):
            raise ValueError('common_final_missing')
        first=finals[0]['result'].splitlines()[0] if finals[0]['result'].splitlines() else ''
        failures=len(observed['tests']['judgment']['existing_failed_cases'])
        result['existing_failures']=failures
        if failures and policy['report_contract']['existing_failures'].format(count=failures) not in finals[0]['result']:
            raise ValueError('existing_failures_not_disclosed')
        body_states={line.strip().strip('#*` ').strip() for line in finals[0]['result'].splitlines()} & STATUSES
        if result['common_work']=='passed' and body_states-{'필수 검증 통과·작업 완료'}:
            raise ValueError('common_report_conflicting_state')
        if result['common_work']=='passed' and first=='필수 검증 통과·작업 완료':
            result['comparison_entry']='allow'
        else:
            result['issues'].append('common_checks_or_report_not_passed')
    except (OSError,ValueError,KeyError,TypeError,AttributeError,IndexError) as exc:
        result['issues'].append(str(exc))
    return result


def common_main(argv):
    parser=argparse.ArgumentParser(description='New common comparison contract; no legacy reclassification')
    parser.add_argument('--target',type=Path,required=True)
    parser.add_argument('--events-file',type=Path,required=True)
    parser.add_argument('--selection-file',type=Path)
    parser.add_argument('--policy-sha256',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        from omc_home_observation import local
        root = args.target.resolve()
        local(root, str(args.output_dir.absolute().relative_to(root)))
        if args.selection_file is not None:
            selection_name = str(args.selection_file.absolute().relative_to(root))
            local(root, selection_name)
        raw = args.events_file.read_bytes()
        rows = []
        try:
            rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
            if any(not isinstance(row, dict) for row in rows):
                raise ValueError('native_event_object_required')
            from omc_validation_command import load_policy, selection_path
            policy, _ = load_policy(root, args.policy_sha256)
            canonical = selection_path(root, policy)
            selected = args.selection_file.absolute() if args.selection_file is not None else canonical
            if 'selection_path' in policy and selected != canonical:
                raise ValueError('selection_path_mismatch')
            selection=json.loads(selected.read_bytes())
            result=assess_common_validation(root,rows,selection,args.policy_sha256)
        except (OSError,ValueError,KeyError,TypeError,AttributeError,IndexError) as exc:
            result={'schema_version':'omc-common-validation/v2','common_work':'unknown',
                    'internal_procedure':'unknown','comparison_entry':'hold','issues':[str(exc)]}
        finals=[r['result'] for r in rows if isinstance(r,dict) and r.get('type')=='result' and isinstance(r.get('result'),str)]
        args.output_dir.mkdir(parents=True,exist_ok=False)
        (args.output_dir/'native-events.jsonl').write_bytes(raw)
        (args.output_dir/'assessment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        (args.output_dir/'final.txt').write_text(finals[0] if len(finals)==1 else '')
        (args.output_dir/'report.txt').write_text('공통 업무 검증: '+result['common_work']
            +'\n도구 내부 절차: '+result['internal_procedure']+'\n비교 진입: '+result['comparison_entry']
            +'\n근거: '+'; '.join(result['issues'])+'\n기존 비교 판정은 변경하지 않습니다.\n')
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['comparison_entry']=='allow' else 1
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'comparison_entry':'hold','reason':str(exc)}));return 1


def output_capabilities():
    return {'schema_version':'omc-output-record/v1','output_modes':['legacy_inline','detached_v1'],
            'consumer':'workflow_validation','requires_explicit_native_exit':True}


def main() -> int:
    if sys.argv[1:]==['capabilities']:
        print(json.dumps(output_capabilities()));return 0
    if sys.argv[1:2]==['compare-entry']:
        return common_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--events-file', type=Path, required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--verification-file', type=Path, required=True)
    parser.add_argument('--expected-outcome', choices=['done', 'blocked'], required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--scope-report', type=Path)
    parser.add_argument('--scope-report-sha256')
    parser.add_argument('--visual-report', type=Path)
    parser.add_argument('--visual-report-sha256')
    parser.add_argument('--output-mode',choices=['legacy_inline','detached_v1'],default='legacy_inline')
    parser.add_argument('--output-context',type=Path)
    parser.add_argument('--output-context-sha256')
    parser.add_argument('--output-record',type=Path)
    parser.add_argument('--output-record-sha256')
    args = parser.parse_args()
    try:
        result = collect(args.target.resolve(), args.events_file, args.session_id,
                         args.verification_file, args.expected_outcome, args.output_dir,
                         args.scope_report, args.scope_report_sha256,
                         args.visual_report, args.visual_report_sha256, output_mode=args.output_mode,
                         output_context=args.output_context,output_context_sha256=args.output_context_sha256,
                         output_record=args.output_record,output_record_sha256=args.output_record_sha256)
    except (OSError, ValueError, TypeError) as exc:
        failure = {'schema_version': 'omc-workflow-validation/v1', 'recompare_entry': 'hold', 'error': str(exc)}
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / 'assessment.json').write_text(json.dumps(failure, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(failure, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['recompare_entry'] == 'allow' else 1


if __name__ == '__main__':
    raise SystemExit(main())
