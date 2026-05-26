# Standalone Repo Setup (템플릿 repo로 분리)

이 킷은 `omc_kit/` 디렉토리만 떼어서 **독립 git repo**로 관리하는 걸 권장합니다.

## 옵션 A) 로컬에서 새 repo로 “내보내기”(추천, 네트워크 없음)

이 프로젝트 루트에서:

```bash
python omc_kit/scripts/export_repo.py --dest /tmp/omc_kit
```

이후 `/tmp/omc_kit`를 원하는 원격(GitHub 등)에 push 하면 됩니다.

새 프로젝트에 붙인 뒤에는 `python scripts/omc.py setup --target .`를 먼저 실행하면 부트스트랩 블록과 기본 파일을 한 번에 깔 수 있습니다.
이 명령은 `.omc/` 상태 디렉터리와 기본 `hooks.json`도 함께 초기화합니다.

## 옵션 B) 다른 프로젝트에 붙이기: submodule

원격 repo URL이 준비된 뒤, 다른 프로젝트에서:

```bash
git submodule add <REMOTE_URL> omc_kit
python omc_kit/scripts/install.py --target .
```

- 장점: 킷 업데이트를 한 번에 여러 프로젝트에 배포하기 쉬움
- 단점: submodule 운영(업데이트/고정 버전) 규칙이 필요

## 옵션 C) 다른 프로젝트에 붙이기: subtree

submodule을 싫어하면 subtree도 가능:

```bash
# 예시(원격이 있는 경우)
git subtree add --prefix omc_kit <REMOTE_URL> main --squash
```

- 장점: 단일 repo로 단순하게 운용 가능
- 단점: 업/다운스트림 동기화가 다소 번거로움
