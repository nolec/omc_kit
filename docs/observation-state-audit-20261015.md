# 관측 상태 점검 — 2026-10-15

## 결론

두 사용처의 OMC 0.5.8 설치 파일 무결성은 정상이다. 그러나 기존 관측 등록은 현재 설치와 연결되지 않아 효과 비교 자료로 사용할 수 없다. 설치 성공을 관측 재개 또는 효과 입증으로 해석하지 않는다.

| 대상 | 설치 감사 | v3 작업 관측 | 완료 관측 | 권고 |
| --- | --- | --- | --- | --- |
| ai-cs | 0.5.8 / ok | installation_binding_mismatch | DISABLED_BY_USER | 사용자 중단 유지 |
| sixshop3-storefront-fe | 0.5.8 / ok | installation_binding_mismatch | live_install_identity_invalid | 기존 기록 보존, 재개 전 별도 계획 |

storefront는 조회용 잠금 파일 접근 권한을 확보한 뒤 다시 조회했다. 따라서 이전 권한 오류와 이번 설치 식별 불일치는 별개의 결과다. 완료 관측 구현은 이 불일치를 OBSERVATION_INVALID / FROZEN_INVALID로 표시하며 new_registration_required를 안내한다. 이번 점검은 해당 상태 함수의 실제 예외를 기록했다.

## 확인한 근거와 한계

- 관측 시각(UTC): 2026-10-15T03:22:19.428982+00:00
- 감사 실행 원본: 로컬 OMC scripts의 audit_target, v3.report, live_observation_status. 사용처 설치 파일의 감사와 관측 상태를 별도로 확인했다.
- 두 설치 receipt의 source revision: c04794724c4fc2d033e95ede0c6c6fa177349135.
- 두 설치 source SHA-256: e9edc19fb4861bbf6db0d2067d693f2e1e7b156205c66c9691e2d349ec000027.
- v3 roster는 두 대상 모두 0.3.4 / source SHA 6c4096519a6d167afb7f098f1832202bff37609c73ead2c87f0f8f957269eaa7에 결속돼 있다.
- 기존 install-continuity.json은 0.3.4의 source SHA를 d31ba78eda90c4a792f56202061bdbc914b7dce9c53ec75ab14c752d5a28be81로 연결한다. 현재 0.5.8까지 연결하는 유효한 증거로 인정되지 않는다.
- ai-cs 완료 관측은 2026-10-06T18:35:46.656359+09:00에 “사용자 요청: ai-cs 관측 중단”으로 비활성화됐다. 내부 정책을 우회해 조회하거나 재개하지 않았다.
- 원래 observation-policy.json 외에 transition 및 studies 정책이 존재한다. 단순히 원래 정책 버전만 보고 현재 정책을 단정하지 않고 공개 상태 함수의 전환 검증을 사용했다.
- 불일치가 처음 발생한 릴리스, 이후 전체 업데이트 경위, 과거 표본별 유효성은 이번 점검만으로 확정할 수 없다. 장부 전체의 통계 유효성 검증은 설치 결속 검사에서 차단됐다.

## 기존 절차와 선택지

기존 절차는 [v3 운영 안내](work_lifecycle_cohort_v3_runbook.md), 구현은 scripts/omc_observation_install_continuity.py와 scripts/omc_completion_observation.py에 있다. 새 측정 시스템을 추가하기보다 이 절차의 사용 가능 여부를 먼저 검토한다.

1. **유지:** ai-cs 중단과 기존 자료를 보존한다. 비용이 가장 작지만 새 실무 효과 자료는 쌓이지 않는다. 현재 적용한 선택이다.
2. **기존 관측 복구:** prepare/seal은 업데이트 직전·직후 설치를 연결하는 단일 업데이트 증명이다. 기존 proof의 목적 설치가 이미 과거이므로 지금 과거 연결을 소급 작성하거나 덮어써서는 안 된다. 누락된 정당한 증거가 있는지 확인하기 전 복구 가능하다고 약속할 수 없다.
3. **신규 등록:** 새 동의·대상·시작 시각을 정해 전환 절차를 검토한다. 과거 자료와 새 자료를 섞거나 소급 수집하지 않는다. ai-cs의 명시적 재개 동의가 없으므로 기존 두 대상 조합을 자동 재등록할 수 없다.

권고: 지금은 보존을 유지하고, 별도 계획에서 사용자 중단을 존중하는 관측 대상 구성과 신규 등록 가능 조건을 결정한다. C3의 설치된 0.5.8 다섯 항목 안내에 대한 새 격리 사례 사용자 이해 확인은 별도 미완료 과제이며 이 점검으로 대체하지 않는다.

## 보존 검증

조회 전후 install receipt, v3 설정·장부, observations 하위 일반 파일의 경로 집합과 SHA-256을 대조했다. ai-cs 26개, storefront 86개 모두 동일했다. 심볼릭 링크는 파일 읽기 대상에서 제외했다. 조회용 state 잠금 파일은 이 검증 범위가 아니다. 업무 파일 전체 보존 또는 외부 저장소 백업을 증명하는 결과는 아니다.

제품 코드·설치·등록·관측 정책·장부를 수정하지 않았다. 원문 요청·코드·실행 로그를 복제하지 않고 아래 경로 및 해시만 기록한다. 임시 실행 결과는 /private/tmp/omc-observation-audit.json이며 영구 보관 근거는 이 문서다.

## 증거 목록

### /Users/noseunglae/Downloads/dev/ai-cs

| 파일(대상 루트 기준) | 전후 동일 SHA-256 |
| --- | --- |
| `.omc/install-receipt.json` | `d72ffc68201a9164f2c0d8b80c13184e7b9d5640fcc516319c9dec821ae78d79` |
| `.omc/observation-policy.json` | `dd3c0313abdf606c5a74bf940844cd58ebe8556231a9a2e03389336ac9ce5a92` |
| `.omc/observations/install-continuity.json` | `1a7ea29ec01213ee3295431bd15b8806d99432ba9440a5609c6cca63a362cd8e` |
| `.omc/observations/live/.allocation.lock` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `.omc/observations/live/3b486bdce23644bebd1217f28ce0a34e/first-completion.json` | `e1693bc991a479848674efa874242f7d327bf1d254ba44c6776c6e4b5bfad232` |
| `.omc/observations/live/3b486bdce23644bebd1217f28ce0a34e/start.json` | `d468d55a899d3acd50680da870f963fc205b2165f28eb0aabef79dfdc8ed4dc7` |
| `.omc/observations/live/8055142800ce4a8e82fbd2db25b9ef0f/first-completion.json` | `edbfef6f4b53b67b2f5b29bd3a0b853a74650c80bf0441e9c659e0c3e0062807` |
| `.omc/observations/live/8055142800ce4a8e82fbd2db25b9ef0f/start.json` | `df93324310e7e7e6749f806a77a3c8699b946b6372e9ed851e0dac5aa3a93f9b` |
| `.omc/observations/live/8055142800ce4a8e82fbd2db25b9ef0f/terminal.json` | `288c582653b18d7e1c90da561c2045ca14e85117b905a58b0260811e8a0e4c48` |
| `.omc/observations/live/905400e7983745ce9ca71a687e4f2253/first-completion.json` | `c3d77523cb037f792486d6f52da0bc7259ee1cb92e8dee3d76506fdf3d54325b` |
| `.omc/observations/live/905400e7983745ce9ca71a687e4f2253/start.json` | `fe286e572d6d425926bad3fd2759af51a4126f52d9f4e2b3d37baa3d3f7ca966` |
| `.omc/observations/live/a1b053c7405e413b8cbce68d8772c9cc/first-completion.json` | `00e076ec0fdccf9c75acab2836c34a0e2fd2ff78422496899c00deeb3467a748` |
| `.omc/observations/live/a1b053c7405e413b8cbce68d8772c9cc/start.json` | `d9d8f6c036ee6d5e0a8c8ad3e86080197bce88ae3d7801cfc34ba29c963504b4` |
| `.omc/observations/live/a2186b46c6c5404cbfb169a6ec0c2ad3/first-completion.json` | `77c186a9650b8782475957eb62e773b6523f29a4e17a943c48668aee868c04e1` |
| `.omc/observations/live/a2186b46c6c5404cbfb169a6ec0c2ad3/start.json` | `fc4067edeaa266ebc0b1089e77b9e8675fea5bb40843b0a9c9799b8859ef01c3` |
| `.omc/observations/live/e8d20466e48f4e6aa975d76163db387f/start.json` | `570db414535b36f28da4f8bdb48b487e19c8bd2484f67af937afb98b274b283f` |
| `.omc/observations/opt-out.json` | `74d0eb90f3601f27e6a50ed3a025eafd35c0a7dd235e4e8a7aadeb0656caed86` |
| `.omc/observations/policies/completion-quality-live-20260910-v1.json` | `76c45a674e656820a84e3ba5eccfba1be8c10928fb31e294afc2ad14877dabda` |
| `.omc/observations/registrations/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71.json` | `eaa57f938a2ade4697c2003a63c278ac961cf767b0cc95883fac180d97509cf4` |
| `.omc/observations/registrations/f8c4814023088a7d0def311b0820a5783648896e7ef8605cca31e8aedf10a977.json` | `469203239e18f343826b69f0395efbab23724021c5e5b35d68d62389dc149441` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/policy.json` | `83b1620df32d5e2da49477d726eea72de42736e8da126ba86b50b6f3c531aa43` |
| `.omc/observations/studies/f8c4814023088a7d0def311b0820a5783648896e7ef8605cca31e8aedf10a977/policy.json` | `d1709d28e0fb881991e0554094caf59f9cf99dc0138cfee759351c4d0136350b` |
| `.omc/observations/transition.json` | `32ae8105e88e7caf0aae99537e08813aa94a69e8cdc478d919d22342110ba389` |
| `.omc/observations/transitions/4b5c91fb5f3d2ee197cf37af75de65c2dd77bb29f24aa29c107be9b342151115.json` | `3e085a2e05edad138e207120b40e375f168c3cd104e9e2d06bdeb833f1958a85` |
| `.omc/observations/transitions/f933c067a1b386efea9fb55e418df990ca66fcbbba9fc563253bdd8a88cb2c5a.json` | `db9bc81bf28517acdffdf86bed3b01a875c4d648f7a83ea12efc9a868b619662` |
| `.omc/skill-effectiveness-cohort-v3.json` | `f03f7a204a02c4aeff46367e7ff1ce34df87ea55db44900897e3a307c06eca6b` |

### /Users/noseunglae/Downloads/dev/sixshop/sixshop3-storefront-fe

| 파일(대상 루트 기준) | 전후 동일 SHA-256 |
| --- | --- |
| `.omc/install-receipt.json` | `afbcb5c0ee7bad6886ff50f6ed6072399751c053e0ded5e87808740523df6fc6` |
| `.omc/observation-policy.json` | `b2317cd46b474ce5a9e46bb9955d88718f525aecc267cde2a9bde0762b470dfd` |
| `.omc/observations/install-continuity.json` | `332260cb0fb1b4984f70a91be206a30fa50bc91e5a798ca110613571a37daa7b` |
| `.omc/observations/live/.allocation.lock` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `.omc/observations/live/2abad46720464145b55f7d1439b9d653/first-completion.json` | `0e8c6b81f83ce5067470de52cdc5b24ccf100cfcc9bf8831f558a9cce30c62d1` |
| `.omc/observations/live/2abad46720464145b55f7d1439b9d653/start.json` | `428f936f0d616eb7bb062d02cc7a1c6449af657835832a7e95383ecc4c13e73a` |
| `.omc/observations/live/2abad46720464145b55f7d1439b9d653/terminal.json` | `94321ab59e8d2e0548dbd3ac0f0c3a0168fc6aead529a49fbfa51b6bbda669ac` |
| `.omc/observations/live/c7803c7beec140009062a2e3e3fd52fa/first-completion.json` | `bb7ce5ae808c7a2b2c785d7100cd4d6c6b463f5ae14e6ff29f791a63beb85144` |
| `.omc/observations/live/c7803c7beec140009062a2e3e3fd52fa/start.json` | `906deef3a3e9f52d4b2e874c18d645cafc2ecfff6c9de324ad3cb08263a636eb` |
| `.omc/observations/live-failures/12bcd5abba532661b633f70a4dba38239af1213dddcd33d825ed2a97c87fe38c.json` | `207127ffaf5c3737d0477661b1699e6a0064ac260868dc9aaa93b1e36bc2de5e` |
| `.omc/observations/live-failures/aabd2be7f8173cae41406485ba9db16daef1388e2fa4ce05ef067fd531549bf7.json` | `89361ea393fd91da9cd686c873e9d3bbec7991ce62377aa1d0fc2e96654f56c7` |
| `.omc/observations/live-failures/f2d148b3b85580264ebea938dcae034e429373df57c6daa48f002c03da686277.json` | `4893df67e0f8e7b688810e61882563e50d351cd60fb283d9fb965c9196d72f0f` |
| `.omc/observations/policies/completion-quality-live-20260910-v1.json` | `da96cf053bd9c2e1aefd0ba4cc60e40ce56c8c52ab25e832ed1f57a2cc845834` |
| `.omc/observations/registrations/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71.json` | `eaa57f938a2ade4697c2003a63c278ac961cf767b0cc95883fac180d97509cf4` |
| `.omc/observations/registrations/f8c4814023088a7d0def311b0820a5783648896e7ef8605cca31e8aedf10a977.json` | `469203239e18f343826b69f0395efbab23724021c5e5b35d68d62389dc149441` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/.allocation.lock` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/01db367ed70041a49746f23f5377e935/first-completion.json` | `d565b98d778ff4239849a201aa64dc46295fa56d7063d74a7e21712cf5a07966` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/01db367ed70041a49746f23f5377e935/start.json` | `c769b65c8cbdee91b158e336863e32d495d95b5ac32be45de910755b488a78c1` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/05bcd1e0282b46a38da7ef8f0d966a74/first-completion.json` | `f056cf680aba759c0d5d5522c9ed620ab0d4029874515d93487b92ac508d8256` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/05bcd1e0282b46a38da7ef8f0d966a74/start.json` | `8cc0562a31dca3053af0f6b9c4e1b76e4abda09af59d7752df5fbec4f8840de2` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/14a28f3a5f6d471e891e151ebb42fe05/first-completion.json` | `6e8c293eefa5e7fe5b6d991c7e9f4b6027cc3371bfe7ef918bbb8589e978cee8` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/14a28f3a5f6d471e891e151ebb42fe05/start.json` | `646c6412920023e4356f71e737981de42547da23047c3d5e6a2bc9ab384cc42c` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/1a944adcf7df4674ada725a76ddafbc9/start.json` | `7c45180c1bb77079511afed843870d2bda3a1e15cf66fbbc3f1ee5ca22181a2f` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/2060a09f19614128af20e42ce7b0b4df/first-completion.json` | `e64e49a9121777743573f967998a877d9abd4173e287511acb16cbe7da689521` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/2060a09f19614128af20e42ce7b0b4df/start.json` | `8e93e375b6c48ff1000d9384114be0ac7b4ecc563559255c377df78496c5336d` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/227ad4932d87430eafe2b648f62ac908/first-completion.json` | `fe02829dbcc58be04682e384e5366f8225ee00ac7495e0c04164ea1a9d884320` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/227ad4932d87430eafe2b648f62ac908/start.json` | `e2a689809802e8c6d6b9073867845dd313cf82c0426f08510106cdb2f2ce974c` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/3037e667a35043e290ad17ae833c60f6/start.json` | `6ff96ac58abda8f577b8c6a93c35a84d2be649682687c488f827499a25afaf18` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/329a2362afac467ab98cb45051cf373f/first-completion.json` | `5ffc4ca73933f20609e8947f3f68aad53e789e986a947304c7ad90ea01d199b2` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/329a2362afac467ab98cb45051cf373f/start.json` | `e18b1f4bc4a3d3cb9d7d3f5d326643e0201fe7416d99cf8ae8bc0a7a5b2f94c8` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/332d611754804dd799e05d1e1dd56892/first-completion.json` | `7608e0a87bf509a7051e7fc70968b79de623ee5f248245865dca3e7a8482367c` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/332d611754804dd799e05d1e1dd56892/start.json` | `6b36b343ca1ca29245bde5a9c12af3711716920e976e02a23a3dd59f44675bfa` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/468cca87e1f7492f9f1b93cad31115ee/start.json` | `ea8abdca0fad7f4d92c7727fbea0bd3cf4357131daa9338623839020cb56bd05` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/5d2369ded63e4aa88c474b2aeb7a076f/first-completion.json` | `342ef7fcc4ea065c98d4959fb0ea2a66ccf3c412c6f0cbbdebdc36d09aa1fc35` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/5d2369ded63e4aa88c474b2aeb7a076f/start.json` | `40e32da17c9d4afcba96d5e25af3379fb136bd4f8b365f06bf8e530b5f36a635` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/5ef2e70a48864bffa6b8974d3a366a42/first-completion.json` | `019b65de15aa3bdacb3e56a8b823baadff4181d8f53505ea4aca1e6d6a74dd32` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/5ef2e70a48864bffa6b8974d3a366a42/start.json` | `4336ee264b26a5edfab553bace525943786da17946d55d87a7ec8fdb6eaafb9f` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/6e0ed38828ca4e6c84eb2ac4e1540c06/first-completion.json` | `d37fbf40fe76f9ae702777fd44b20247d66d054a0fd56737cbbc04ebfd935c7a` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/6e0ed38828ca4e6c84eb2ac4e1540c06/start.json` | `2989978845f8e72ab966135b62ef5263170a457ca21a26cceabc8bd0ea469c6e` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/732006dcf93548fa9527e9b5c422407e/first-completion.json` | `a31bad76e20016ec548fc5defdb3263cd29e9f3a0f4e770b8a640b15210fc1f4` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/732006dcf93548fa9527e9b5c422407e/start.json` | `f63d6571ae1582bb3ce9a5a4b2fb7487a4d891b943e5bbc11083afe8db9615d5` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/8f8bae28ab1c44b881d8413f5a365964/first-completion.json` | `6b37ff535f1cc4d35dbe1cedd1453b75d710a8953e90dc71f36442ddaf751a73` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/8f8bae28ab1c44b881d8413f5a365964/start.json` | `77b50e96cb9dd8552c553bd3927474ae0591bfc7736fe5472c360c64416c6f2a` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/95616808ddbf4adf86a44604b0c9494a/first-completion.json` | `6cd9269c1303ca1401ba268a6cac3538203bb06b62680db8cc3f0ee6c24a0dab` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/95616808ddbf4adf86a44604b0c9494a/start.json` | `aea1fcd52c6622a197fa278e69b2f8161920cfd414ffe8f8b690af9a4e950079` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/9a479fd77633474496241e49e43c1531/start.json` | `0ee9e286ea0d7836295468b14f255a73fdc15a22834b8efc058421618ed5fdf6` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/9ac2ddb568134761b70e6e95a787795a/first-completion.json` | `84f7882603f2a013c3718eed45e28d7ae022922aec8a5b86a8da5306e7668a6a` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/9ac2ddb568134761b70e6e95a787795a/start.json` | `42435a1bea0d2151a184a2574648d51e4dfe1b38c0fb14a25a30126faf31f334` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/9cb63b88730848808c5414206ded6dbe/first-completion.json` | `5e0295cbeb57cb3f9e5eefd559e3164fef00587a07b15bfeccd27e41a2e2a227` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/9cb63b88730848808c5414206ded6dbe/start.json` | `645b842f1998996c597855076dc9733fc6026203321ff30ff7f892b63d564b8f` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/a36fa7a3201c4e83bcee3afa5e9943fe/first-completion.json` | `6153c729d1819689870e5aca94ed65e00953386c74f4954b554a52bb7451a641` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/a36fa7a3201c4e83bcee3afa5e9943fe/start.json` | `a77de3a232044c4649b942245d640dbcbf2b9707a087806bec11341b35ea9404` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/b510cc3a272a4c2cb9add74d3b9638f1/first-completion.json` | `f19c82560fd91efd3528e7f0246402af5c081c1396467d2367bcc4b0a399ae95` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/b510cc3a272a4c2cb9add74d3b9638f1/start.json` | `5dbb5dab05a1320168c87b7ec498a10e8f97ef61f6d8197a1dce29ec21ddb504` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/b6781d33027e4201934f8623e5cb4d7f/first-completion.json` | `46766e0a9a4822e309a055dba5bddc13decafc3ee68184b5f5155c47f17b2543` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/b6781d33027e4201934f8623e5cb4d7f/start.json` | `60ac21073921031f057f89b5f6c6f5f680c7204092d11d90b49bf92086a14480` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/b6bc35124fec45db9ff89aa1a729bd4d/first-completion.json` | `288d764f7b782d47c7e28d634c7f9a9db3eecbad6f14aea73bebf40bfe3792af` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/b6bc35124fec45db9ff89aa1a729bd4d/start.json` | `398629e9b54e049ec904e94af01413c8066ccd6f31bf4dbbf6e2cbfc54057241` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/ba0bfb2500be4ad2a46e4e85890c1704/first-completion.json` | `161dc5b28cef822121ce5c606733478fb2d5fb73cdf99075f5a8fcd4f75c85c6` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/ba0bfb2500be4ad2a46e4e85890c1704/start.json` | `0640c9e7215d5e4778c091d7523af88f3ae5b05133330d98e424dcc1fcf48358` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/bcb4ca8273294d1daedc526366c37cd2/start.json` | `a93d583e3cf599d31cd632590875672470e6b63d78c7ab0253d8d3e5c52fe40f` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/c85ede99c01b49c0b83d063fba7f3e29/start.json` | `e86c1f5d707cc64161eea02faa6fcf6ca9aa2683a9a3bd4ead3efb9c3e562a83` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/ca44f96034fb4e60826710dc650a9e02/start.json` | `ea81012b8874c8f9be6c4c409e19185d88fa5221a71f3342b054088d5cb6dd34` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/ca8cbeaa828647a886ef9e5a23649fe6/start.json` | `0930eae114d2d86513e532236402a8042dd81cd60a0884d6204cbb2c0772952d` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/d27f00e5b0324c4ba5c91c52da2969f0/first-completion.json` | `4bc782156e5db9fcc10a141f1992a3fc6caeb9bd96d93dcf8a95dc40597d05ec` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/d27f00e5b0324c4ba5c91c52da2969f0/start.json` | `3f6b9fbd69e036e3df9854e6584ca7f1191e0d513a5ba1d45ee860d2658bf163` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/e48a4277693e4bcb930c4abc1b84201e/first-completion.json` | `6e8990336036d868a2d57a83d678cfb78fd1cc39a16f98debdc8c607e897623b` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/e48a4277693e4bcb930c4abc1b84201e/start.json` | `aafdc7663e969ea5cefabb6c08b61b4bb36bcb29769ac8bafde8a704a97da5e9` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/e5baa117178145299610ad8a34ad52e0/first-completion.json` | `4572dc650078eb08b785e874cf6d38d963806db0379bf92311dc1b1d5b8ae48f` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/e5baa117178145299610ad8a34ad52e0/start.json` | `5689a418244f83d3b1f89ad58eb68a741b4423d194d20aaf04334e61c9503849` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/ee07952794dd4b0dae20186b1d1656c5/first-completion.json` | `617997f3e2da8f3017c2d6780568dcf57d8a7f85ea9ea3a6830e4eeca5392a99` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/ee07952794dd4b0dae20186b1d1656c5/start.json` | `30dd40bd47014b789514470e79c839617c0fd743b6aa983dd741c7f4a8256d39` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/ee07952794dd4b0dae20186b1d1656c5/terminal.json` | `f6717cfce7ae0851d5e73ae6d5f33e444e0b69a2c456559f5d04561d99d99391` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/efc7dbe7101648d681ab1d6b0afc12d2/first-completion.json` | `1a9fd6372cecda8b49fb88ff47052456b610e34eb280414030953207eec6a468` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/efc7dbe7101648d681ab1d6b0afc12d2/start.json` | `37f3e1c1864a82bfbe1feb6f415a41f78562b4ab24ae374e816a77fea685b780` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/f1beeb5fe596446ba84d0768283bb527/first-completion.json` | `00d8c39029a0134dcfc2517541e52997c2d781f84db78de575998971f24fc257` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live/f1beeb5fe596446ba84d0768283bb527/start.json` | `f80ca7dfac2e9ffde0b1ad3bee952efc3d00edb7aeae17bb6b68bba633f6b29d` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live-failures/64d2aa927c27618986af1860e8375b9d61e35b091739e57248b850faa0b949ad.json` | `f53c02c2b6d64e76a7b4438b13bc199c61c13872554be7f018c8464e4c157003` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/live-failures/84089c2e335de60ff6704ad29be197203e2a8bded8e5857f541c843f2645f11b.json` | `22753a4db2ccd3b6dcd80d8991d9ad92e6b3589e200aaef3b6709016412eb995` |
| `.omc/observations/studies/8ef7e4efa9c76d8581057c94871d1aa8b182f7b2d58d18842365551261157b71/policy.json` | `16266e23785da1942945d1659e4d105ac160ec16824050b138da20c8d0a9f276` |
| `.omc/observations/studies/f8c4814023088a7d0def311b0820a5783648896e7ef8605cca31e8aedf10a977/policy.json` | `af9f76ef8eb79951c6c232c5ebe606ea214980d88fb81d891e0c5f72fbd7345c` |
| `.omc/observations/transition.json` | `5737f4462e7c9a7a2a211cd5d7c59daa886509fd5d94acc6a5b1311a199079a3` |
| `.omc/observations/transitions/181664695fefe08c7f32c7a75d444ba8ff82769b3c060fe2beb135857f8a062e.json` | `05fd8b845655698af1f8366429c78a01adad5a3d180afad81c0fa6b7a4a8c0df` |
| `.omc/observations/transitions/80ecb25895e9d6146e8e54559c7ec842f0ad8d25f3921c01f780e6e445e4ac54.json` | `2cb09d98eae69fa30080d1986a5fd01cf447d0e6e129d1a97ab9914642c76ef7` |
| `.omc/skill-effectiveness-cohort-v3.json` | `498c909f9e9a77e93408e9d39b6ff87a254340c5f45a2e1034c3d70f0b654e2f` |
| `.omc/skill-effectiveness-cohort-v3.jsonl` | `b8d1dfdd033226cf16aed725a86e5df657ceb0f44f1a448087f84bf0501ae5ee` |

