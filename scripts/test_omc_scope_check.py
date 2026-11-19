import json
import os
from pathlib import Path
import subprocess
import sys

CLI = Path(__file__).resolve().parent / 'omc.py'
ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'GIT_OPTIONAL_LOCKS': '0'}

def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, env=ENV)

def invoke(repo, *paths):
    args = [sys.executable, '-B', str(CLI), 'scope-check', '--target', str(repo)]
    for p in paths: args.extend(['--expected-path', p])
    return subprocess.run(args, capture_output=True, text=True, env=ENV)

def init_repo(repo):
    git(repo, 'init', '-q')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    for name in ['a.txt', '한 글.txt']: (repo/name).write_text('base')
    git(repo, 'add', 'a.txt', '한 글.txt')
    git(repo, 'commit', '-qm', 'base')

def test_exact_and_unrelated_changes_preserved(tmp_path):
    init_repo(tmp_path)
    (tmp_path/'a.txt').write_text('requested')
    git(tmp_path, 'add', 'a.txt')
    (tmp_path/'한 글.txt').write_text('unrelated')
    (tmp_path/'new.txt').write_text('untracked')
    (tmp_path/'.omc').mkdir(); (tmp_path/'.omc/note').write_text('preserve')
    before = {p: (tmp_path/p).read_bytes() for p in ['.git/index', 'a.txt', '한 글.txt', 'new.txt', '.omc/note']}
    result = invoke(tmp_path, 'a.txt')
    assert result.returncode == 0, result.stderr
    obj=json.loads(result.stdout)
    assert obj['result']=='MATCH' and obj['staged']==['a.txt'] and obj['unstaged']==['한 글.txt']
    assert 'new.txt' in obj['untracked'] and obj['content_verified'] is False
    assert before == {p: (tmp_path/p).read_bytes() for p in before}

def test_mismatch_reports_missing_and_unexpected(tmp_path):
    init_repo(tmp_path); (tmp_path/'a.txt').write_text('a'); git(tmp_path, 'add', 'a.txt')
    result=invoke(tmp_path, '한 글.txt'); obj=json.loads(result.stdout)
    assert result.returncode == 1 and obj['result']=='MISMATCH'
    assert obj['missing']==['한 글.txt'] and obj['unexpected']==['a.txt']

def test_no_scope_is_unknown(tmp_path):
    init_repo(tmp_path); result=invoke(tmp_path); obj=json.loads(result.stdout)
    assert result.returncode == 2 and obj['result']=='UNKNOWN'

def test_git_failure_is_unknown(tmp_path):
    result=invoke(tmp_path, 'a.txt'); obj=json.loads(result.stdout)
    assert result.returncode == 2 and obj['result']=='UNKNOWN'

def test_rename_compares_both_paths(tmp_path):
    init_repo(tmp_path); git(tmp_path, 'mv', 'a.txt', 'renamed.txt')
    result=invoke(tmp_path, 'a.txt', 'renamed.txt'); obj=json.loads(result.stdout)
    assert result.returncode == 0 and obj['staged']==['a.txt','renamed.txt']

def test_invalid_or_duplicate_scope_is_unknown(tmp_path):
    init_repo(tmp_path)
    for paths in [('a.txt','a.txt'), ('../a.txt',), ('./a.txt',)]:
        result=invoke(tmp_path,*paths); assert result.returncode==2 and json.loads(result.stdout)['result']=='UNKNOWN'

def test_unmerged_is_unknown(tmp_path):
    init_repo(tmp_path); git(tmp_path,'checkout','-qb','feature'); (tmp_path/'a.txt').write_text('feature'); git(tmp_path,'commit','-qam','feature')
    git(tmp_path,'checkout','-q','-'); (tmp_path/'a.txt').write_text('main'); git(tmp_path,'commit','-qam','main')
    subprocess.run(['git','-C',str(tmp_path),'merge','feature'],capture_output=True,env=ENV)
    result=invoke(tmp_path,'a.txt'); assert result.returncode==2 and json.loads(result.stdout)['result']=='UNKNOWN'

def test_same_file_staged_and_unstaged_is_reported_in_both(tmp_path):
    init_repo(tmp_path); (tmp_path/'a.txt').write_text('staged'); git(tmp_path,'add','a.txt'); (tmp_path/'a.txt').write_text('unstaged')
    result=invoke(tmp_path,'a.txt'); obj=json.loads(result.stdout)
    assert result.returncode==0 and obj['staged']==['a.txt'] and obj['unstaged']==['a.txt']
    assert obj['content_verified'] is False

def test_fsmonitor_hook_is_not_executed(tmp_path):
    init_repo(tmp_path); hook=tmp_path/'monitor.sh'; marker=tmp_path/'monitor-invoked'
    hook.write_text('#!/bin/sh\nprintf called > "'+str(marker)+'"\n'); hook.chmod(0o755)
    git(tmp_path,'config','core.fsmonitor',str(hook))
    result=invoke(tmp_path,'a.txt')
    assert result.returncode==1 and not marker.exists()

def test_unborn_repository_can_report_staged_path(tmp_path):
    git(tmp_path,'init','-q'); (tmp_path/'a.txt').write_text('new'); git(tmp_path,'add','a.txt')
    result=invoke(tmp_path,'a.txt')
    assert result.returncode==0 and json.loads(result.stdout)['staged']==['a.txt']

def test_alternate_index_environment_is_not_a_match(tmp_path):
    init_repo(tmp_path); (tmp_path/'a.txt').write_text('staged'); git(tmp_path,'add','a.txt')
    alternate = tmp_path/'alternate-index'; alternate.write_bytes((tmp_path/'.git/index').read_bytes())
    result=subprocess.run([sys.executable,'-B',str(CLI),'scope-check','--target',str(tmp_path),'--expected-path','a.txt'],capture_output=True,text=True,env={**ENV,'GIT_INDEX_FILE':str(alternate)})
    assert result.returncode==2 and json.loads(result.stdout)['reason']=='git_environment_override'

def test_target_must_be_repository_root(tmp_path):
    init_repo(tmp_path); (tmp_path/'a.txt').write_text('staged'); git(tmp_path,'add','a.txt'); (tmp_path/'sub').mkdir()
    result=invoke(tmp_path/'sub','a.txt')
    assert result.returncode==2 and json.loads(result.stdout)['reason']=='target_not_repository_root'

def test_other_repository_environment_is_rejected(tmp_path):
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir(); init_repo(a); init_repo(b)
    (b/'a.txt').write_text('other repo staged'); git(b,'add','a.txt')
    result=subprocess.run([sys.executable,'-B',str(CLI),'scope-check','--target',str(a),'--expected-path','a.txt'],capture_output=True,text=True,env={**ENV,'GIT_DIR':str(b/'.git'),'GIT_WORK_TREE':str(b)})
    assert result.returncode==2 and json.loads(result.stdout)['reason']=='git_environment_override'

def test_dirty_submodule_not_hidden_by_git_config(tmp_path):
    child=tmp_path/'child'; root=tmp_path/'root'; child.mkdir(); root.mkdir(); init_repo(child); init_repo(root)
    git(root,'-c','protocol.file.allow=always','submodule','add','-q',str(child),'sub')
    git(root,'commit','-qam','add submodule')
    git(root,'config','submodule.sub.ignore','all')
    (root/'sub'/'a.txt').write_text('submodule dirty')
    (root/'a.txt').write_text('staged'); git(root,'add','a.txt')
    result=invoke(root,'a.txt'); obj=json.loads(result.stdout)
    assert result.returncode==0 and 'sub' in obj['unstaged']

def test_fresh_isolated_install_exposes_scope_check(tmp_path):
    target=tmp_path/'target'; target.mkdir(); init_repo(target)
    result=subprocess.run([sys.executable,'-B',str(CLI.parent/'install.py'),'--target',str(target)],capture_output=True,text=True,input='n\n',env=ENV,timeout=60)
    assert result.returncode==0, result.stderr
    (target/'a.txt').write_text('staged'); git(target,'add','a.txt')
    check=subprocess.run([sys.executable,'-B',str(target/'scripts/omc.py'),'scope-check','--target',str(target),'--expected-path','a.txt'],capture_output=True,text=True,env=ENV,timeout=20)
    assert check.returncode==0, check.stderr
    assert json.loads(check.stdout)['result']=='MATCH'

def test_status_coverage_explicit_when_assume_unchanged(tmp_path):
    init_repo(tmp_path); git(tmp_path,'update-index','--assume-unchanged','한 글.txt')
    (tmp_path/'한 글.txt').write_text('hidden change'); (tmp_path/'a.txt').write_text('staged'); git(tmp_path,'add','a.txt')
    result=invoke(tmp_path,'a.txt'); obj=json.loads(result.stdout)
    assert result.returncode==0 and obj['worktree_coverage']=='git_status_only_may_hide_changes'

def test_matching_paths_with_unstaged_overlap_has_explicit_warning(tmp_path):
    init_repo(tmp_path)
    (tmp_path/'a.txt').write_text('old staged content')
    git(tmp_path, 'add', 'a.txt')
    (tmp_path/'a.txt').write_text('requested new content')
    index_before = (tmp_path/'.git/index').read_bytes()
    working_before = (tmp_path/'a.txt').read_bytes()
    staged_before = git(tmp_path, 'show', ':a.txt').stdout
    result = invoke(tmp_path, 'a.txt')
    obj = json.loads(result.stdout)
    assert result.returncode == 0 and obj['result'] == 'MATCH'
    assert obj['path_comparison'] == 'MATCH'
    assert obj['staged_and_unstaged'] == ['a.txt']
    assert obj['content_status'] == 'NOT_VERIFIED'
    assert 'staged_and_unstaged_content_not_verified' in obj['warnings']
    assert (tmp_path/'.git/index').read_bytes() == index_before
    assert (tmp_path/'a.txt').read_bytes() == working_before
    assert git(tmp_path, 'show', ':a.txt').stdout == staged_before

def test_nonoverlap_does_not_claim_staged_and_unstaged(tmp_path):
    init_repo(tmp_path)
    (tmp_path/'a.txt').write_text('staged'); git(tmp_path, 'add', 'a.txt')
    (tmp_path/'한 글.txt').write_text('unstaged')
    result = invoke(tmp_path, 'a.txt'); obj = json.loads(result.stdout)
    assert result.returncode == 0 and obj['path_comparison'] == 'MATCH'
    assert obj['staged_and_unstaged'] == []
    assert obj['content_status'] == 'NOT_VERIFIED'
    assert 'staged_and_unstaged_content_not_verified' not in obj['warnings']

def test_mismatch_can_also_report_overlap(tmp_path):
    init_repo(tmp_path)
    (tmp_path/'a.txt').write_text('staged'); git(tmp_path, 'add', 'a.txt')
    (tmp_path/'a.txt').write_text('unstaged')
    result = invoke(tmp_path, '한 글.txt'); obj = json.loads(result.stdout)
    assert result.returncode == 1 and obj['path_comparison'] == 'MISMATCH'
    assert obj['staged_and_unstaged'] == ['a.txt']
    assert 'staged_and_unstaged_content_not_verified' in obj['warnings']
