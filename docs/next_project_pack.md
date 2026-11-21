# 새 프로젝트에 OMC 설치

OMC 원본 체크아웃에서 대상 프로젝트에 설치합니다. 아래의 `$OMC_SOURCE`에는 원본 체크아웃의 실제 절대 경로를 지정하세요.

```bash
OMC_SOURCE=/path/to/omc_kit
python3 "$OMC_SOURCE/scripts/install.py" --target /path/to/new-project
cd /path/to/new-project
python3 scripts/omc_doctor.py --target .
```

OMC는 Codex 스킬과 공통 CLI를 설치하고, 설치에 사용한 원본 경로를 `.omc/install-source.json`에 기록합니다. 설치기가 기존 `AGENTS.md`에 OMC 관리 블록을 추가하거나 갱신합니다. 세션·도구·Git 자동 훅은 비활성 상태입니다.

일반 작업은 Superpowers와 프로젝트 규칙을 따릅니다. 커밋 범위 확인, 작업 재개, 교훈 검색은 [Quickstart](omc_quickstart.md)에서 시작합니다. OMC 작업 흐름을 직접 선택했다면 [상세 운영 문서](omc_explicit_workflow.md)를 참고하세요.

프로젝트의 규칙과 `.omc/lessons/`의 교훈, `.omc/tasks/`의 작업 정의는 해당 프로젝트에서 관리합니다. 다른 프로젝트에 설치할 때 재사용할 공통 파일과 설치 구조는 [킷 지도](kit_map.md)에서 확인할 수 있습니다.

## 선택 설치

OMC의 출하 검증을 거쳐 PR을 준비하는 `pr-create`가 필요한 프로젝트에서는 설치 명령에 `--with-pr-create`를 추가합니다. `python3 scripts/omc.py setup`에서도 같은 옵션을 쓸 수 있습니다. 설치에 선택 옵션을 생략해도 기존 `pr-create` 파일은 일반 재설치와 `--force`에서 보존됩니다.
