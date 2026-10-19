# 광고 검수 입력 원본 보존

이 폴더는 임시 검수 폴더 삭제로 사라졌던 **두 입력의 원본 바이트**를 보존한다. `manifest.json`의 해시는 이전 실행에서 고정한 해시와 같다. 새 검증 기준으로 대체하지 않았다.

| 보존 파일 | 새 격리 후보의 복원 위치 |
|---|---|
| `build-pixels.cjs` | `.omc/visual-input/build-pixels.cjs` |
| `Home.tsx.fixture` | `libs/store-manager/components/src/lib/home/Home.tsx` |

`Home.tsx.fixture`는 앱에 직접 포함하지 않는 소스 스냅샷이다. 복원할 때만 `Home.tsx`로 복사한다. 카드 광고에 로컬 SVG 두 개를 전달하는 공통 준비 입력이며 운영 CMS 광고나 전체 CMS 연동의 증거가 아니다.

## 원본 확인과 복원

저장소 루트에서 아래를 실행한다. `CANDIDATE`는 공통 기준 `34078b6f923587b03c0fbac3d7a5a3c4bf5c0cfc`로 준비한 **새 격리 후보**의 경로다. 사용 중인 원본 storefront에 적용하지 않는다. 환경 파일·인증정보·의존성·서버 설정은 이 보존 묶음에 포함하지 않는다.

```bash
export CANDIDATE=/absolute/path/to/fresh-isolated-candidate
python3 - <<'PY'
import hashlib, json, os, shutil
from pathlib import Path

archive = Path('docs/comparison-dashboard/evidence/ad-validation-inputs-v1').resolve()
candidate = Path(os.environ['CANDIDATE']).resolve(strict=True)
manifest = json.loads((archive / 'manifest.json').read_text())
home = 'libs/store-manager/components/src/lib/home/Home.tsx'
# Check every input before writing anything. Existing non-baseline Home is refused.
baseline_home_sha256 = '18939d61349726494c3d2f2aaa450c4a1500e792033899499a2ddad7c3c5cbdb'
for name, entry in manifest['files'].items():
    source = archive / name
    assert hashlib.sha256(source.read_bytes()).hexdigest() == entry['sha256'], name
    destination = candidate / entry['destination']
    assert not destination.is_symlink(), destination
    destination.resolve().relative_to(candidate)  # 저장소 밖 폴더 링크도 거부.
    if destination.exists():
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        assert entry['destination'] == home and digest == baseline_home_sha256, 'Refuse to overwrite existing work'
for name, entry in manifest['files'].items():
    destination = candidate / entry['destination']
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive / name, destination)
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == entry['sha256']
print('Frozen inputs restored; service readiness and model execution remain unverified.')
PY
```

복원 후 후보 폴더에서 `node .omc/visual-input/build-pixels.cjs`를 실행한다. 빌더는 현재 작업 폴더 이름과 고정된 설치 위치를 사용하므로, 보존 폴더의 빌더를 직접 실행하거나 복원 위치를 바꾸지 않는다.

## 회귀 검사

```bash
OMC_AD_RESTORE_STOREFRONT=/absolute/path/to/sixshop3-storefront-fe \
  python3 -m pytest scripts/test_omc_ad_fixture_archive.py -q
```

실제 공통 기준의 Ad 코드와 foundation을 Git에서 새 임시 후보로 추출하고, 기존 설치 의존성으로 복구 builder를 실행한다. 원본 해시, 복사 후 독립 사용, 누락·손상 탐지, 실제 bundle 생성과 6종 variant·중앙 표식을 검사한다. 환경변수 미지정 시 실제 빌드 검사 1개는 건너뛰며, 그 결과를 빌드 통과로 보고하면 안 된다. 모델 호출·로그인·실제 서비스 서버 실행은 하지 않는다.

## 다음 실행의 경계와 종료 조건

- 이 입력 복구는 이전 모델 결과를 바꾸거나 새 모델 성공을 증명하지 않는다. 과거 보고서를 소급 수정하지 않는다.
- 필수 5개 검증과 픽셀 12조건은 유지한다. 실제 홈 검수는 통제 화면으로 대체하지 않는다.
- 서버 설정·브라우저 도구·현재 후보 결속·로그인·이미지 로드·자동 전환·승인 기준과 관측 증거 선택은 실제 실행 전에 별도로 확인한다.
- 다음 시도의 준비 상태가 불충족이면 모델 호출 0회로 끝내고 사유를 남긴다. 누락된 환경 때문에 입력이나 통과 조건을 임의 변경하지 않는다.
- 준비가 확인돼도 Claude 최대 1회라는 별도 실행 승인을 유지한다. 이 복구 작업에서 Claude는 호출하지 않았다.
