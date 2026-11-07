# 프로젝트 정리 조사 및 2차 실행 승인 계획

**1차 조사만 완료 — 기존 연구 파일 이동·이름 변경·수정·삭제 없음.** 크롤링·API·외부 접속·기존 분석 재계산·Git commit/push는 수행하지 않았다. 사용자 요청의 보존·승인 조건이 이 계획의 기준이다.

## 1. 조사 범위와 실제 구조

조사 기준 UTC: 2026-11-07T09:56:40.678152+00:00. 새 조사 산출물을 제외한 기존 프로젝트는 **폴더 1,617개(루트 제외), 파일 7,323개, 3,228,726,875 B (3,079.15 MiB) = 3.229 GB / 3.007 GiB**다. .git·숨김 파일을 포함한다. 디스크 할당 공간·압축률·외부 가상환경 용량은 포함하지 않은 논리적 파일 크기다.

처음 기본 권한에서는 vendor 내부가 보이지 않아 910개/3.106GB만 확인됐으나, 읽기 전용 권한으로 재조사해 누락을 해소했다. 최종 조사 오류는 0건이다. 새 계획서·원장·docs/project_structure_review 자체는 기준 집계에서 제외하고, 완료 검증에 별도 크기와 목록을 남긴다.

```text
Hormuz_Research/
├─ .git/                         # Git 이력, 건드리지 않음
├─ .env, .env.example, .gitignore, LICENSE
├─ .vscode/settings.json
├─ README.md                     # 현재 제목 한 줄
├─ hormuz_textminig.ipynb          # 31셀: 메타데이터 정리
├─ hormuz_textminig_v2.ipynb       # 57셀: 위 단계 + 추출 벤치마크
├─ docs/                         # 수집 기준·과거 정리 이력
└─ hormuz_2026_data/
   ├─ 01_newscatcher_raw.csv
   ├─ 05_extraction_candidates_v2.csv
   ├─ 05_extractor_benchmark_sample.csv, 06_benchmark_*
   ├─ benchmark_html_cache/
   ├─ ai_label_review_20261031/
   ├─ candidate_status_analysis_20261031/
   ├─ media_verification_pilot_proposal_20261031/
   ├─ body_collection_pilot_20261102_01/
   │  ├─ html/, texts/, fetch/, records/, first_pass_records/
   │  ├─ robots/, page_evidence/, cache_audit/
   │  └─ vendor/
   ├─ quality_audit_20261107_01/
   └─ metadata_selection_20261107_01/
```

|주요 영역|파일 수|용량|역할|
|---|---:|---:|---|
|hormuz_2026_data|7,225|3,227,887,603 B (3,078.35 MiB)|전체 연구 자료|
|.git|88|415,465 B (0.40 MiB)|이력/환경/문서; 보존|
|(root)|7|354,864 B (0.34 MiB)|이력/환경/문서; 보존|
|docs|2|68,899 B (0.07 MiB)|이력/환경/문서; 보존|
|.vscode|1|44 B (0.00 MiB)|이력/환경/문서; 보존|
|hormuz_2026_data/metadata_selection_20261107_01|41|272,486,968 B (259.86 MiB)|과거 메타데이터 분포·A/B/C 비교·기사별 결정 근거; 최종 수집 기준 아님|
|hormuz_2026_data/body_collection_pilot_20261102_01|7,076|142,598,796 B (135.99 MiB)|51건 수집·추출·접근·재개 근거와 실행 환경|
|hormuz_2026_data/candidate_status_analysis_20261031|26|59,921,125 B (57.15 MiB)|기존 후보 상태·국가·매체 분포의 과거 감사|
|hormuz_2026_data/benchmark_html_cache|17|3,600,518 B (3.43 MiB)|과거 벤치마크의 원본 HTML, 삭제 캐시 아님|
|hormuz_2026_data/quality_audit_20261107_01|23|1,135,731 B (1.08 MiB)|51건 AI 품질 감사·사람 검토 질문; 정답 라벨 아님|
|hormuz_2026_data/media_verification_pilot_proposal_20261031|19|433,905 B (0.41 MiB)|매체 역할·출처·접근 근거와 프로토콜|
|hormuz_2026_data/ai_label_review_20261031|2|149,617 B (0.14 MiB)|과거 AI 초안·검토 근거|

vendor만 **6,430파일 / 126,899,604 B (121.02 MiB)**다. 패키지 파일 때문에 전체 파일 수가 크게 보이며, Python 코드 수를 연구 스크립트 수로 해석하면 안 된다. 프로젝트 자체 Python 스크립트는 19개다. 전체 하위 폴더별 수치는 [folder_sizes.csv](docs/project_structure_review/folder_sizes.csv)에 있다.

### 파일 유형

|확장자|파일 수|용량|
|---|---:|---:|
|.csv|71|3,058,260,224 B (2,916.58 MiB)|
|.py|3,244|44,967,815 B (42.88 MiB)|
|.pyd|83|38,505,472 B (36.72 MiB)|
|.gz|942|38,059,802 B (36.30 MiB)|
|.html|80|15,606,543 B (14.88 MiB)|
|.dll|9|9,568,848 B (9.13 MiB)|
|.json|1,481|7,824,103 B (7.46 MiB)|
|(none)|601|5,003,549 B (4.77 MiB)|
|.exe|34|2,783,690 B (2.65 MiB)|
|.chm|1|2,642,781 B (2.52 MiB)|
|.txt|337|1,172,673 B (1.12 MiB)|
|.h|94|887,115 B (0.85 MiB)|
|.pxi|27|596,342 B (0.57 MiB)|
|.pem|7|506,401 B (0.48 MiB)|
|.ipynb|2|352,804 B (0.34 MiB)|
|.lib|4|343,048 B (0.33 MiB)|
|.pyx|8|295,596 B (0.28 MiB)|
|.md|23|203,411 B (0.19 MiB)|
|.xsl|8|159,651 B (0.15 MiB)|
|.pxd|21|150,988 B (0.14 MiB)|

나머지 유형도 survey_summary.json에 전부 기록했다. .gz 대부분은 패키지 리소스이며 분석 결과 압축 파일은 별도 구분해야 한다. 현재 .pyc·__pycache__·.pytest_cache·.ipynb_checkpoints 파일과 임시 .sqlite/.db/.tmp는 0개다. 디렉터리명 cache만으로 삭제하지 않는다.

## 2. 역할·활성 상태와 유지할 핵심 자료

- **A 원본/핵심 메타데이터:** 01_newscatcher_raw.csv(884,016,978 B), 05_extraction_candidates_v2.csv(1,861,820,890 B)는 절대 보존한다. URL·article_id·컬럼·인코딩·연구 기간을 변경하지 않는다. 두 파일 합계 2,745,837,868 B로 기존 전체 용량의 약 85.0%다. 크다는 이유로 정리 대상에 넣지 않았다.
- **B 현재 입력·재현 코드:** docs/data_collection_criteria_v1.md, 핵심 두 CSV, v2 노트북을 현재 참고 진입점으로 삼는다. 노트북은 즉시 실행 가능한 운영 수집기로 승인된 것이 아니며 기존 CSV 출력·벤치마크·네트워크 셀이 있으므로 전체 실행하지 않는다. 각 실험 폴더의 .py는 해당 단계 재현 코드로 제자리에 둔다.
- **C 과거 실험:** 위 7개 단계/캐시 폴더와 05/06 벤치마크 산출물을 보존한다. 과거 AI 초안, 첫 패스/최종 records, extractor별 TXT/JSON, 실패 HTML·robots 응답도 역할이 다르다. 특히 51건 HTML·본문·품질 감사와 선정 lock·provenance는 must_keep다.
- **D 문서:** README는 현재 설명이 부족하다. 승인 후 현재 진입점·단계 순서·과거 실험 링크·실행 주의사항을 보강할 수 있지만 이번에는 수정하지 않았다. 문서를 실험 폴더 밖으로 모두 이동하면 근거 문맥과 링크가 깨지므로 원위치 링크 색인을 권장한다.
- **E 임시/환경:** 빈 진단 로그 3개도 과거 해시 계약 때문에 보류한다. vendor는 환경 의존성이지 전체가 임시 캐시는 아니다. 역할이 불확실한 패키지 파일은 needs_review로 남긴다.

수정 시각은 PROJECT_FILE_INVENTORY.csv에 UTC로 기록했다. 최근 작업은 11/7 메타데이터 분석·품질 감사, 이전 11/2 본문 시험, 10/31 매체·후보 검증이다. 수정 시각만으로 활성/삭제 여부를 판정하지 않았다. A/B/C 시나리오는 과거 분석 기록이며 향후 확정 선정 기준으로 사용하지 않는다.

## 3. 실제 의존성과 이동 위험

정적 경로/파일명 발견 7,352건, 기존 보존 manifest에 연결된 파일 759개를 기록했다. [path_references.csv](docs/project_structure_review/path_references.csv)는 파일명 출현에 따른 보수적인 영향 후보이고, 동명 파일은 ambiguous로 표시한다. 실제 실행·읽기 여부를 완벽히 증명하는 그래프는 아니다.

|부분|실제 확인한 의존성|이동/삭제 시 영향|
|---|---|---|
|본문 시험 pilot.py|HERE=__file__.parent, ROOT=HERE.parents[1], HERE/vendor, 고정 proposal 폴더, DATA/benchmark_html_cache|실험을 한 단계 더 중첩하면 ROOT가 달라져 입력·캐시·출력·재개 경로가 깨짐|
|품질 감사 build_audit.py/verify_inputs.py|OUT.parent/body_collection_pilot_20261102_01; CSV text_path/result_path를 Path로 그대로 읽음|상대 형제 관계와 CSV의 절대 경로 모두 필요|
|메타데이터 ingest/analyze/validate/verify_preservation.py|OUT.parent=DATA; OUT.parents[1]=ROOT; input_hashes_before의 상대 키|폴더 깊이나 입력 경로 변경 시 재현·해시 검증 실패|
|노트북 2개|cwd와 부모에서 raw를 찾아 PROJECT_ROOT 지정, DATA_DIR 아래 고정 출력|notebooks/로 단순 이동해도 외부 스크립트 root.glob 및 과거 hash 키가 달라짐. 출력 셀 재실행은 덮어쓰기 가능|
|과거 analyze_candidates.py|__file__.parents[2] 기본 루트; --project-root/--output-dir 지원|새 빈 출력만 허용. 04_row_selection_audit는 summarize_and_verify.py가 읽음|
|Markdown·JSON·CSV|상대 링크, 파일명 참조, 절대 HTML/본문 경로, 보존 스냅샷 키|내용을 고치면 기존 해시/감사 기록 자체가 변경됨. 기존 기록 자동 수정 금지|
|삭제된 05_article_index_v2.csv|노트북의 과거 출력과 2026-11-02 정리 이력에 참조가 남음|이미 승인 삭제된 과거 파일이다. 재생성·현재 누락 사고로 취급하지 않음|
|working.sqlite|메타데이터 분석 후 이미 제거한 임시 DB; cleanup.json에 기록|validate.py를 재실행하면 DB를 요구한다. 이번에는 실행하지 않았고 결과 재검증은 원본 코드 읽기·파일 보존만 검사|

절대 경로는 [absolute_path_references.csv](docs/project_structure_review/absolute_path_references.csv)에 원문 위치와 함께 남겼다. 로컬 사용자 홈·프로젝트 경로·C:/Python314 계열 실행 환경의 참조가 있으며 자동 수정하지 않았다. 프로젝트 밖 IDE 최근 파일/사용자 스크립트/가상환경/다른 프로젝트의 참조는 조사 범위 밖이므로 없다고 단정하지 않는다. .env 본문·API 키 값은 읽거나 출력하지 않았다. 노트북 출력 셀은 경로 검색 대상에서 제외했고, 동적 문자열 생성·제3자 내부 import·외부 사용자 환경은 미확정이다. CSV는 경로 열만 스트리밍하고 원본 대형 CSV는 헤더만 읽어 기사 내용 분석을 하지 않았다.

## 4. Git·설정·실행 환경

기존 Git 추적 파일은 **8개**(.env.example, .gitignore, LICENSE, README, docs 2개, notebook 2개)다. 기존 비추적/ignore 파일 7,227개(.env·.vscode·데이터 포함)는 .gitignore 적용을 확인했다. .git 내부는 추적 판정 대상이 아니다. git status/ls-files/check-ignore만 읽기 전용으로 사용했고 GIT_OPTIONAL_LOCKS=0을 설정했다. Git 이력·index·설정 변경 및 commit/push는 수행하지 않았다.

.gitignore는 hormuz_2026_data/ 전체와 .env, 캐시, 로그, IDE 설정을 제외한다. 따라서 코드가 데이터 폴더에 있다는 이유로 Git 백업이 되는 것은 아니다. 승인 후 **새 운영 코드만** scripts/와 명시적 의존성 파일에서 관리하고, 원래 실험 코드는 그 자리에 둔다. 기존 원문·비밀 값은 Git에 추가하지 않는다. 현재 프로젝트 수준 requirements.txt/pyproject.toml/lockfile은 없다. vendor 내부 packaging/requirements.py는 의존성 선언 파일이 아니다. environment.json·설치 로그에는 버전 근거가 있으나 전체 전이 의존성 lock·설치 wheel 오프라인 복구까지 보장하지 않는다. .vscode/settings.json은 python.terminal.useEnvFile=true를 설정한다.

## 5. 중복과 삭제 후보 — 이번에는 삭제하지 않음

스트리밍 SHA256으로 7,233개 파일을 확인했다. .git 및 비밀 환경 파일은 내용 해시/중복 검사에서 제외했다. 동일 내용 그룹 **201개**, 그룹당 한 사본만 남긴다고 가정한 순수 중복 바이트는 **1,392,387 B (1.33 MiB)**다. 이는 삭제 가능량이 아니다. 라이선스/패키지 버전별 리소스/추출기별 독립 결과/수집 checkpoint와 provenance처럼 같은 내용이 필요한 경우가 대부분이다. 두 notebook과 대형 CSV는 동일 내용 사본이 아니다.

모든 중복 경로와 해시는 duplicate_groups.json, 파일명 중복은 inventory의 duplicate_status/evidence에 있다. 비어 있는 추출문도 추출 실패 증거이므로 빈 파일이라는 이유로 삭제하지 않는다. 서로 다른 article_id의 동일 보조 추출문도 품질 오류 근거여서 유지한다.

|분류|파일 수|합계|의미|
|---|---:|---:|---|
|safe_to_delete|0|0 B (0.00 MiB)|승인 후 자동 삭제 가능한 근거가 충분한 현존 항목|
|delete_after_review|726|61,954,508 B (59.08 MiB)|검토·명시 승인 전 삭제 불가; 확보량은 조건부 상한|
|must_keep|6,597|3,166,772,367 B (3,020.07 MiB)|현재 보존. 영구 불변 판정이 아니라 이번 계획의 보존 범위|

**현재 즉시 안전 삭제로 확인된 용량은 0 B다.** 캐시·임시 DB는 과거 작업에서 이미 정리된 상태다. 아래 후보를 모두 별도 검토·승인해도 절약 가능한 최대 논리 용량은 61,954,508 B (59.08 MiB)이며 실제 확보량은 승인 부분집합과 디스크 할당 크기에 따라 다르다. 중복 바이트를 다시 합산하지 않았다.

|검토 묶음|파일 수|용량|삭제 근거·남은 확인|복구|
|---|---:|---:|---|---|
|빈 진단 로그|3|0 B (0.00 MiB)|extractor.log, recheck_console.log, recheck_final_console.log는 0 B. quality source_snapshot와 metadata input_hashes가 존재를 요구함; 삭제 실익 없음|빈 파일 내용 복구 가능, 원시 mtime은 baseline 참조|
|PyWin32.chm|1|2,642,781 B (2.52 MiB)|런타임 코드가 아닌 Windows 도움말. 직접 프로젝트 참조 없음. package 리소스·배포 RECORD 영향 확인|pywin32 312 배포물 재설치 가능성; wheel 미보유|
|패키지 테스트·예제·도움말|721|9,150,709 B (8.73 MiB)|vendor 내 test/tests/examples/demo/demos/doc/docs 경로 721파일. 직접 프로젝트 참조 없음. 내부 import·동적 리소스·라이선스 포함 여부와 향후 환경 재현 필요성은 미검증이므로 일괄 삭제 금지|배포 패키지 재설치 가능성; 전체 lock/오프라인 복구 미보장|
|과거 행별 감사 CSV|1|50,161,018 B (47.84 MiB)|보존 후보 CSV에서 생성된 구버전 파생 원장. 최신 원장과 필드/판정 의미가 달라 자동 대체 불가. summarize_and_verify 및 preservation_before/input_hashes가 참조하므로 과거 감사 근거 포기 여부와 보존 계약 예외를 승인해야 함|analyze_candidates.py 입력·출력 코드 확인, 동일 bytes 재생성 미실행|

정확한 **파일별 경로·바이트·참조·보존 manifest·복구 가능성**은 [deletion_candidates.csv](docs/project_structure_review/deletion_candidates.csv)에 전부 기록했다. 경로 패턴으로 승인하는 방식보다 이 목록의 SHA256과 경로를 기준으로 개별/묶음 승인하는 것을 권장한다. 승인되더라도 기존 보존 manifest를 고쳐 과거 사실을 덮어쓰지 말고, 별도 승인 삭제·의도된 부재 기록을 만든다. 기존 검증기가 부재를 실패로 처리할 수 있다는 점을 그대로 보고해야 한다.

### 삭제하지 않을 큰 파일/과거 결과

- 최신 selection_decision_ledger.csv 226,674,035 B: 과거 결정 근거와 원본 대응을 보존한다. gzip 전환은 향후 별도 승인·읽기 호환성 검증 후 가능한 저장 형식 변경이며 이번 삭제량에 포함하지 않았다.
- 전체 body pilot 약 142.6MB 중 vendor 약 126.9MB: vendor 전체 제거는 news-please 등 전이 의존성에 영향을 준다. 환경 lock과 재설치 대체 검증 없이 제거하지 않는다. 나머지 HTML·본문·상태·로그는 연구 근거다.
- benchmark_html_cache 17개 HTML 약 3.60MB: 새로 수집하지 않고 추출 개선에 재사용할 수 있는 원자료다. 반드시 유지한다.
- candidate_status_analysis의 나머지 국가·매체별 표, media 검증 자료, AI 초안, quality audit 모두 과거 단계의 정의/판정 근거를 담는다. 최신 분석이 존재한다는 사실만으로 불필요하다고 단정하지 않는다.

## 6. 권장 구조 — 기존 경로 보존 + 앞으로 생성할 자료만 정돈

가장 적합한 기본안은 **기존 연구 파일 실제 이동 0개**다. 별도 archive 폴더로 전부 이동해도 저장공간은 줄지 않고 현재 실행·검증만 손상된다. 대신 역할 색인과 신규 작업 경로를 단순하게 정하고, 검증된 삭제 후보만 2차에서 제거한다.

```text
Hormuz_Research/
├─ README.md                     # 승인 후 연구 진입점/현재·과거 구분 보강
├─ PROJECT_CLEANUP_PLAN.md
├─ PROJECT_FILE_INVENTORY.csv
├─ scripts/                      # 향후 새 운영 코드만; 이번 미생성
├─ notebooks/                    # 향후 새 탐색 노트북만; 이번 미생성
├─ docs/
│  ├─ data_collection_criteria_v1.md
│  ├─ data_cleanup_20261102.json
│  └─ project_structure_review/  # 이번 조사·경로·삭제 후보·검증 기록
├─ 기존 노트북 2개               # 이동하지 않음
└─ hormuz_2026_data/
   ├─ 기존 핵심 CSV/benchmark/7개 실험 폴더 모두 제자리
   ├─ runs/                       # 향후 신규 실험만, 이번 미생성
   │  └─ <task>_<YYYYMMDD>_<run-id>/
   │     ├─ manifest.json, config.json, logs/
   │     └─ html/, texts/, review/ # 필요한 하위 폴더만 생성
   └─ corpus/                     # 향후 검증 완료 분석용 본문만, 이번 미생성
```

추가 최상위 디렉터리는 신규 실행 코드·신규 노트북 두 개만 조건부 권장한다. reports는 docs 색인에서 원본 보고서를 링크하고 복제하지 않는다. tmp는 새 run 내부에서 만료 대상과 재생성 방법을 manifest에 명시한 경우에만 만들며, HTML 저장소를 cache라고 부르지 않는다. run-id는 실험 하나당 하나만 사용하고 중간 업데이트마다 날짜 폴더를 만들지 않는다.

|기존 경로 → 권장 경로|실행 계획|영향|
|---|---|---|
|01_newscatcher_raw.csv / 05_extraction_candidates_v2.csv → 동일|이동 없음|모든 기존 코드·URL/ID·해시 유지|
|기존 7개 실험/HTML 폴더 → 동일|이동 없음, README에서 단계·역할 링크|형제 참조·절대 경로·해시 계약 보존|
|기존 노트북 2개 → 동일|이동 없음; 새 노트북만 notebooks/|root.glob과 historical path 키 유지|
|실험 내부 .py/MD/manifest → 동일|이동 없음; 운영 코드 승격은 향후 별도 이식|__file__ 깊이·문맥 보존|
|향후 신규 코드 → scripts/|승인 후 새 파일만|프로젝트 루트/입력/출력을 명시 인자로 받도록 설계|
|향후 신규 수집·검증 결과 → hormuz_2026_data/runs/<task>_<date>_<id>/|실제 후속 작업을 할 때만 생성|상위 깊이를 가정하지 않는 신규 코드 필요|
|향후 분석용 완성 본문 → hormuz_2026_data/corpus/<version>/|완료·검토 상태 확정 후 생성|raw HTML·AI 초안과 분석용 본문을 구분|

machine-readable 계획은 move_plan.csv에 있다. 기존 파일의 proposed_path는 원래 경로와 같다. 디렉터리 이동이 필수인 파일은 발견되지 않았다. notebooks/로 기존 노트북을 옮기는 대안은 해시/루트 검색 영향에 비해 실익이 작아 권장하지 않는다.

## 7. 승인 후 2차 작업 순서와 중단·복구 조건

1. **사용자 승인 범위를 파일 목록으로 확정:** 삭제 후보 CSV의 승인 행과 보존 예외를 기록한다. must_keep는 승인 삭제 목록에 자동 추가하지 않는다. 현재 계획 자체는 삭제 승인이 아니다.
2. **사전 재확인:** 승인 경로가 workspace 안인지 해석하고 SHA256·크기·mtime·새 참조·Git 상태를 대조한다. 삭제 후보가 바뀌었거나 새 실행이 사용 중이면 중단한다. 삭제 직전 검증 실패 시 추가 삭제/이동을 하지 않는다.
3. **복구 확보:** 패키지 tests/docs는 동일 배포물/정확 버전/설치 의존성 확인을 먼저 수행한다. 구버전 CSV는 의미 보존 필요성을 사람이 판단하고 재생성 또는 기존 외부 백업이 실제로 가능한지 확인한다. 대용량을 무조건 복사하지 않는다. 복구 보장 없는 영구 삭제는 별도 명시 승인 필요다.
4. **승인된 삭제만 실행:** 상대 경로·원래 hash·용량·이유·의존성·복구 자료·승인 내역·실제 해제량을 별도 실행 원장에 기록한다. 파일별 처리 실패 시 추가 작업을 멈추고 완료/미완료 목록을 남긴다. 빈 로그 삭제는 용량 이득이 없어 생략 권고다.
5. **탐색성 개선:** 별도 승인 후 README에 활성 입력/현재 연구 방향/과거 단계 보고서 링크를 추가한다. 기존 실험 폴더나 기록을 재작성하지 않는다. 미래 운영 코드·새 run이 필요할 때만 제안 폴더를 만든다.
6. **사후 검증:** 남은 원본·HTML·본문·감사 파일의 hash와 경로를 대조하고, 삭제 승인 항목만 의도된 부재로 기록한다. 이전 검증의 실패를 숨기지 않는다. 기사 선정·본문 추출·수집 분석을 실행해 검증하지 않는다. commit/push는 별도 요청 없으면 하지 않는다.

## 8. 후속 연구를 지원하는 운영 원칙

기존 NewsCatcher 메타데이터와 2026-01-01~08-31 범위를 보존한다. 후속 run에는 입력 CSV hash·원래 article_id/URL 대응·선정 기준 버전·실행 환경·요청/추출 설정·검토 상태를 명시한다. 무료 영어 일반 기사와 품질 확인을 우선하되 날짜·국가·월 균등 할당이나 특정 주요 매체 필수 선정을 구조에 내장하지 않는다. A/B/C 결과는 참고 기록만으로 연결한다. 새로운 본문 수집 결과와 최종 분석용 corpus는 검토 상태를 구분한다. 이번에는 이 후속 작업을 실행하지 않았다.

## 9. 산출물·검증 및 한계

- PROJECT_FILE_INVENTORY.csv: 기존 파일 전체의 크기·확장자·수정 시각·Git·역할·활성 상태·권장 경로·위험·참조·중복·삭제 분류. 새 조사 산출물의 자기참조를 피하려고 기준 목록에는 포함하지 않으며 완료 validation에서 별도로 센다.
- docs/project_structure_review/: baseline.json, content_hashes.json, survey_summary.json, folder_sizes.csv, path_references.csv, absolute_path_references.csv, reference_scan_scope.json, duplicate_groups.json, deletion_candidates.csv, move_plan.csv, 조사/계획 생성/보존 검증 코드.
- .env 및 환경 비밀 파일은 내용/해시를 저장하지 않았고 Git 내부 내용 중복 검사도 생략했다. 따라서 해당 파일들의 내용 동일성은 판정하지 않는다. 나머지 읽기 가능한 연구 파일은 스트리밍 해시로 전후 보존을 검증한다. 실행 중 자동 생성되는 IDE/도구 Git 참조는 별도 상태로 관찰하며 Git 명령으로 변경하지 않는다.
- 정적 탐색으로 파일이 참조되지 않는다고 증명할 수는 없다. 외부 환경과 동적 의존성이 불명인 항목은 needs_review다. 전체 조사 결과에 없는 임시 파일을 존재한다고 가정하거나 절약 용량으로 더하지 않았다.

**여기서 중단한다. 다음 작업은 사용자가 구체적인 삭제/문서 개선 범위를 승인한 뒤에만 실행한다.**
