# Hormuz_Research

호르무즈 관련 뉴스의 영어 기사 메타데이터와 실제 기사 본문을 활용하기 위한 연구 프로젝트다. 기존 메타데이터를 보존하면서 수집 후보 선정, 무료 본문 접근 확인, 추출 품질 검증, 최종 분석용 본문 데이터셋 구축을 단계적으로 진행한다. 최종 연구 방법·매체 목록·수집량은 아직 확정하지 않았다.

기본 연구 기간은 **2026년 1월 1일~8월 31일**이다. **NewsCatcher API 수집 시 영어 필터를 사용했다는 점은 사용자 확인 사항**이다. API의 영어·비유료 메타데이터가 실제 원문 전체의 영어 여부나 무료 접근을 보증하지는 않는다.

## 처음 확인할 파일

1. [수집·분류 기준](docs/data_collection_criteria_v1.md): 기존 정의와 보존 원칙을 확인한다.
2. [51건 품질 감사 보고서](hormuz_2026_data/quality_audit_20261107_01/QUALITY_AUDIT_REPORT.md)와 [후속 선정 계획](hormuz_2026_data/quality_audit_20261107_01/NEXT_SELECTION_PLAN.md): 실제 본문·접근·추출 오류의 근거와 미확정 사항을 확인한다. AI 판정은 사람의 최종 정답이 아니다.
3. [기존 메타데이터 분포](hormuz_2026_data/metadata_selection_20261107_01/SOURCE_DISTRIBUTION_REPORT.md)와 [과거 선정 시나리오 비교](hormuz_2026_data/metadata_selection_20261107_01/SELECTION_RECOMMENDATION_REPORT.md): 기존 자료의 범위·편중·날짜 공백을 파악한다.
4. [프로젝트 정리 계획](PROJECT_CLEANUP_PLAN.md)과 [2차 정리 실행 보고서](docs/project_structure_review/CLEANUP_EXECUTION_REPORT.md): 현재 경로, 보존 자료, 의도된 삭제와 과거 해시 검증에 미치는 영향을 확인한다. [1차 파일 목록](PROJECT_FILE_INVENTORY.csv)은 당시 스냅샷이며 현재 상태와 다를 수 있다.

## 핵심 입력

|경로|역할|
|---|---|
|[01_newscatcher_raw.csv](hormuz_2026_data/01_newscatcher_raw.csv)|NewsCatcher API에서 확보한 원본 메타데이터. 수정·덮어쓰기·삭제하지 않는다.|
|[05_extraction_candidates_v2.csv](hormuz_2026_data/05_extraction_candidates_v2.csv)|기존 URL 정규화·중복 제거 및 기간 처리가 반영된 후보 메타데이터. 기존 URL·article_id 대응의 기준이며 그대로 보존한다.|

후속 작업은 **전체 메타데이터를 기반으로 분산 본문 수집 후보를 선정하는 것**이다. 날짜·언론사·국가·지역의 극단적인 편중을 점검하되 균등 할당이나 매일 기사 확보를 강제하지 않는다. 특정 국가의 주요 매체를 필수로 지정하거나 목표 수량을 먼저 정하지 않는다. 무료 영어 일반 기사 본문을 우선 검토하고, 미확정 언어·접근·출처·기사 유형은 근거에 따라 구분한다.

기존 **A/B/C 매체 선정 시나리오는 과거 분석의 비교 기록**이다. 향후 본문 수집 대상의 확정 기준이나 실행 승인 목록으로 사용하지 않는다. 현재 저장된 본문·추출문을 확인하지 않고 메타데이터 조건만으로 전체 본문 확보를 확정하지 않는다.

## 기존 작업과 근거 위치

|위치|내용|
|---|---|
|[hormuz_textminig.ipynb](hormuz_textminig.ipynb), [hormuz_textminig_v2.ipynb](hormuz_textminig_v2.ipynb)|기존 메타데이터 처리 노트북. v2에는 추출 벤치마크 단계도 포함한다. 원래 위치를 유지한다.|
|`hormuz_2026_data/05_extractor_benchmark_sample.csv`, `06_benchmark_*`|과거 추출 벤치마크의 표본·상태·비교·검토표.|
|`hormuz_2026_data/benchmark_html_cache/`|과거 벤치마크 HTML. 재추출·검증 근거이며 삭제 가능한 임시 캐시로 취급하지 않는다.|
|`hormuz_2026_data/candidate_status_analysis_20261031/`|기존 후보의 상태·국가·매체 분포 감사. 과거 행별 감사 CSV도 보존한다.|
|`hormuz_2026_data/ai_label_review_20261031/`|과거 AI 초안과 검토 근거.|
|[매체 검증·시험 제안](hormuz_2026_data/media_verification_pilot_proposal_20261031/11_next_pilot_protocol.md)|매체 역할·원 출처·접근 근거와 다음 시험 프로토콜.|
|[51건 본문 수집 시험](hormuz_2026_data/body_collection_pilot_20261102_01/REPORT.md)|선정 목록, HTML·출처 기록, 추출기별 전체 본문, 접근·재개 로그. 17개 매체 각 3건의 소규모 시험이다.|
|[51건 품질 감사](hormuz_2026_data/quality_audit_20261107_01/QUALITY_AUDIT_REPORT.md)|HTML 대조에 따른 AI 1차 품질 판정, 오류 분석, 사람 검토 질문·우선순위.|
|`hormuz_2026_data/metadata_selection_20261107_01/`|전체 메타데이터 분포·날짜 포괄성·과거 A/B/C 시나리오와 기사별 결정 원장.|

과거 실험 폴더는 경로·manifest·본문 대응의 재현성을 위해 이동하지 않는다. 51건의 시험을 전체 매체의 성능이나 최종 코퍼스의 대표 성능으로 일반화하지 않는다.

## 앞으로의 저장 위치

```text
scripts/                         # 신규 운영 코드
notebooks/                       # 신규 탐색 노트북 (기존 2개는 루트 유지)
docs/                            # 기준·진행 문서·정리 기록
hormuz_2026_data/
  기존 CSV·실험 폴더              # 기존 경로 유지
  runs/<task>_<YYYYMMDD>_<run-id>/ # 새 작업의 설정·로그·HTML·본문·검토 결과
  corpus/<version>/              # 검토 상태가 명시된 향후 분석용 본문
```

하나의 작업에 하나의 run-id를 사용한다. 새 작업에는 입력 파일 해시, 기존 article_id·URL 대응, 기준·설정 버전, 접근·추출·사람 검토 상태를 기록한다. 보고서는 기존 근거를 상대 링크로 연결하며 원본을 중복 복사하지 않는다. 새 폴더가 존재한다는 사실이 수집 실행이나 최종 데이터셋 확정을 뜻하지 않는다.

## 실행과 보존 주의사항

- **기존 노트북을 전체 실행하지 않는다.** 메타데이터 출력 셀은 기존 후보·중간 산출물을 덮어쓸 수 있고, 벤치마크·수집 셀에는 네트워크 요청과 파일 쓰기가 포함돼 있다. 원본 CSV를 덮어쓰지 않는 의도가 명시돼 있어도 모든 셀이 무해하다는 뜻은 아니다. 셀별 입력·출력을 먼저 확인하고 새 출력 경로를 지정해야 한다.
- 기존 `pilot.py` 등 수집 코드를 경로 검사나 import 검증 목적으로 실행하지 않는다. 재개 파일·로그·HTML·추출문을 갱신하거나 네트워크에 접근할 수 있다. 새 수집은 별도 작업 지시와 접근 정책 확인 후 진행한다.
- 실험 폴더 깊이, CSV/JSON에 저장된 절대 경로, 기존 해시 manifest를 임의로 고치지 않는다. 승인된 README 변경·패키지 정리로 과거 스냅샷과 차이가 생기면 실행 보고서에 남기며, 과거 검증을 통과한 것처럼 기록하지 않는다.
- 프로젝트 수준의 완전한 의존성 lock은 아직 없다. 과거 실행 환경 버전은 [environment.json](hormuz_2026_data/body_collection_pilot_20261102_01/environment.json)과 설치 로그를 참고한다. `vendor/` 전체를 불필요한 캐시로 간주하지 않는다.
- `.env`에는 비밀 설정이 있을 수 있다. 내용을 출력하거나 Git에 추가하지 않는다. `.gitignore`는 `.env`와 `hormuz_2026_data/` 전체를 제외한다. 데이터 폴더 내부의 코드·보고서도 자동으로 Git 백업되지 않는다는 점에 유의한다. commit/push와 원문 공개는 별도 작업이다.


## Fixed-200 runner: limited parallel HTTP

The existing `scripts/body_pilot_200.py` uses 4 HTTP workers by default; `--workers N` changes the limit. Per-host requests remain serial, with shared robots and rate controls. A single manager writes state/results and runs extraction. Default invocation remains offline dry-run; live collection still needs separate approval and domain-policy evidence. See [parallelization report](hormuz_2026_data/runs/body_pilot_200_20261107_01/parallelization_20261108_01/PARALLELIZATION_REPORT.md).
