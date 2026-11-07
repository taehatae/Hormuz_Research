# 2차 프로젝트 정리 실행 결과

**승인된 vendor 후보의 검증된 일부를 실제 삭제했다. 기존 연구 데이터·실험 폴더는 이동하지 않았다.**

- 실제 삭제: **507개 / 8,059,320 B (8.059 MB)**. 대상은 기존 726개 후보 중 vendor 722개에 속한 정확한 경로뿐이다.
- 삭제 파일의 로컬 압축 복구본: **1,788,579 B (1.789 MB)**. CRC와 파일별 SHA256 왕복 검증을 통과했다. 복구본을 제외한 절감은 **6,270,741 B (6.271 MB)**이며 조사·실행 기록까지 포함한 실제 프로젝트 순감소는 아래 표와 다르다.
- 유지: **219개** = vendor 215개 + 사용자 지정 CSV 1개 + 빈 로그 3개.

## 1. 정리 전후 실제 파일 수·용량

|시점|파일 수|논리 용량|
|---|---:|---:|
|2차 작업 전 스냅샷|7,342|3,242,361,241 B (3242.361 MB)|
|2차 작업 후(복구본·새 문서·실행 코드 포함)|6,853|3,240,256,580 B (3240.257 MB)|
|프로젝트 전체 순감소|489|2,104,661 B (2.105 MB)|

위 용량은 논리적 파일 크기다. OS 디스크 블록·압축·외부 Python 환경 용량을 측정한 수치는 아니다. 1차 조사 7,323개/3,228,726,875 B와 달리 2차 전후 표에는 1차 조사 산출물과 초기 2차 검사 코드도 포함된다. 시점·범위가 다르며 기존 연구 자료를 추가 복제하지 않았다.

## 2. 실제 삭제와 유지 사유

사전·직전 파일 크기/해시와 허용 경로를 대조하고, 절대 경로가 지정 vendor 내부인지 확인했다. 심볼릭 링크·경로 이탈·해시 불일치 항목은 삭제하지 않도록 가드했다. 삭제는 파일 단위로 수행했으며 디렉터리 재귀 삭제는 하지 않았다. 원장에 삭제 완료를 파일마다 기록했다.

삭제한 항목은 Twisted·zope.interface·hjson·hyperlink·incremental·news-please·queuelib·langdetect·ago·certifi·constantly의 검증된 테스트/예제 파일이다. 자체 테스트/예제 전체의 재실행 가능성은 의도적으로 줄어들지만 일반 연구용 import와 아래 제한된 기능 검사는 유지됐다. 모든 동적 실행 경로를 증명한 것은 아니며 삭제 파일은 복구 가능하게 보존했다.

|보존 이유(복수 사유는 한 행)|개수|
|---|---:|
|Windows_COM_dynamic_registration_or_help_execution_not_tested|170|
|explicit_user_preservation|4|
|license_or_required_metadata_or_uncertain_name;Windows_COM_dynamic_registration_or_help_execution_not_tested|1|
|package_family_has_runtime_import_into_candidate_tree|32|
|runtime_resource_or_help_reference;Windows_COM_dynamic_registration_or_help_execution_not_tested|4|
|runtime_resource_or_help_reference;test_security_fixture_dynamic_access_not_verified|1|
|test_security_fixture_dynamic_access_not_verified|7|

- 약 50MB 04_row_selection_audit.csv와 빈 로그 3개는 사용자 지시에 따라 그대로 유지했다.
- boto3/botocore의 docs는 실제 import되는 문서 생성 코드다. 디렉터리 이름만 보고 삭제하지 않았다. Windows COM/ISAPI/adodbapi 관련 동적 등록·도움말 실행은 검증하지 않아 보존했다. 보안 테스트 인증서 등 동적 접근이 불명인 자료와 기타 자원 참조도 보류했다.
- 라이선스·필수 배포 메타데이터는 삭제하지 않았다. 정확한 삭제/보류 경로·사전 SHA256·크기·이유·복구 경로는 [deletion_execution_ledger.csv](deletion_execution_ledger.csv)에 726행으로 남겼다. 허용 목록 밖 파일은 삭제하지 않았다.

## 3. 비파괴 기능·경로 검사

삭제 전후 **17개 모듈 검사**가 같은 경로·성공 상태·기본 함수 결과를 반환했다. requests, BeautifulSoup, trafilatura.extract 및 NewsPlease.from_html의 존재·호출 가능성을 확인했다. 실제 기사 추출은 실행하지 않았다. requests 요청 준비, 작은 로컬 HTML 파싱, Deferred 콜백, 로컬 HJSON 파싱, URL 파싱, 버전 생성, 메모리 큐 push/pop만 수행했다.

별도 프로세스의 audit hook으로 네트워크 연결/DNS/bind, 파일 쓰기·삭제·이름 변경·디렉터리 생성과 하위 프로세스를 차단했다. import 중 선택적 로컬 socket.bind 시도 1회가 차단됐으며 외부 웹/API 요청은 없었다. trafilatura는 기존 사용자 site-packages, news-please 등은 기존 vendor에서 import됐으며 경로는 JSON에 기록했다. 전체 추출 파이프라인·패키지 테스트 suite·Windows COM 실행을 통과했다고 주장하지 않는다.

첫 삭제 전 기능 검사에서 새 검사 코드의 queuelib API 경로 오류를 발견했다. 삭제 0건 상태에서 중단했고 로컬 소스에 따라 queuelib.queue.FifoMemoryQueue로 수정한 후 재검사했다. 기존 패키지는 수정하지 않았으며 probe_correction.json에 기록했다.

프로젝트 Python과 vendor Python은 AST로 읽어 import 참조를 확인했고 리소스/도움말의 문자열 참조 및 동적 자원 호출도 검토했다. 실행 시 실제 읽힌 후보 파일은 삭제 대상에서 제외했다. 기존 노트북과 pilot.py/분석/감사 코드는 실행하지 않았다. 기존 상대·절대 데이터 경로, 폴더 깊이, article_id/URL 대응은 유지됐다.

## 4. 연구 자료 보존과 과거 manifest 차이

핵심 CSV 2개와 사용자 지정 과거 감사 CSV는 SHA256 동일하다. vendor 외 CSV·HTML·전체 TXT·노트북 **278파일**을 1차 해시와 대조해 동일함을 확인했다. 기존 품질 감사·본문 시험·메타데이터 분석의 나머지 해시 자료도 보존했다. README 외 승인되지 않은 기존 파일 내용 변경/누락은 0건이다.

|과거 manifest|현재 존재·SHA 비교|이번 변경/기존 차이|
|---|---|---|
|docs/project_structure_review/content_hashes.json|불일치/의도된 차이 있음|신규 삭제 507, 이전부터 부재 0, 내용 변경 1|
|hormuz_2026_data/body_collection_pilot_20261102_01/preservation_before.json|불일치/의도된 차이 있음|신규 삭제 0, 이전부터 부재 1, 내용 변경 0|
|hormuz_2026_data/quality_audit_20261107_01/source_snapshot_before.json|일치|신규 삭제 0, 이전부터 부재 0, 내용 변경 0|
|hormuz_2026_data/metadata_selection_20261107_01/input_hashes_before.json|일치|신규 삭제 0, 이전부터 부재 0, 내용 변경 0|

기존 manifest와 1차 validation.json은 수정하지 않았다. 특히 1차 전체 보존 스냅샷은 삭제된 vendor 파일과 승인된 README 변경 때문에 현재 상태에 그대로 통과할 수 없다. 과거 검증기를 실행해 원본 검증 파일을 덮어쓰지 않고, 새 post_cleanup_validation.json에 실제 비교 결과를 남겼다. 과거 pilot의 05_article_index_v2.csv 등 이미 없던 항목은 이번 삭제와 구분했다.

배포 패키지의 dist-info/RECORD **11개**는 삭제 파일을 계속 열거한다. 메타데이터 자체는 수정하지 않았다. RECORD 기준 설치 무결성은 삭제한 파일에 대해 의도적으로 불일치하므로 정상 통과했다고 주장하지 않는다. 정확한 멤버 목록은 새 검증 JSON에 있다.

## 5. README와 새 작업 구조

README.md를 프로젝트 진입 안내로 개선했다. 연구 목적·기간, NewsCatcher 영어 필터 사용자 확인, 두 입력 CSV, 기존 작업 단계, 51건 시험/감사, 과거 A/B/C의 한계, 다음 후보 선정 작업, 경로·실행 주의사항을 상대 링크로 연결했다. 보고서를 복사하지 않았다.

```text
scripts/                         # 신규 운영 코드 안내 README
notebooks/                       # 신규 노트북 안내 README
hormuz_2026_data/runs/            # 새 작업 결과 안내 README
hormuz_2026_data/corpus/          # 향후 분석용 본문 안내 README
기존 데이터·실험·노트북            # 원래 경로 유지
```

각 폴더에 용도를 설명하는 README만 생성했다. scripts/notebooks의 안내는 비밀·데이터를 담지 않으며 commit/add하지 않았다. runs/corpus는 기존 hormuz_2026_data/ ignore 규칙을 따른다. .gitignore·Git 설정·이력은 수정하지 않았고 commit/push하지 않았다. .env 비밀 값은 읽거나 출력하지 않았다.

## 6. 오프라인 복구와 남은 검토

복구본: [vendor_cleanup_recovery.zip](vendor_cleanup_recovery.zip), SHA256 `80c59e1d6ad34ab9688ca3d7466d0670cfc06d590c87441f76defcc8d48d773d`. 삭제 파일 507개의 원래 프로젝트 상대 경로와 정확한 내용을 압축 저장했다. 복구본을 불필요하게 Git에 추가하지 않는다. 외부 다운로드 없이 복구할 수 있으며 현재는 복구를 실행하지 않았다.

복원이 필요할 때 archive SHA256과 원장/멤버별 해시를 먼저 대조하고, 멤버의 절대 대상 경로가 프로젝트 vendor 내부인지 확인한 후 현재 없는 파일만 복원한다. 이미 존재하는 파일은 덮어쓰지 않는다. 원장에 복구를 기록하고 동일 import 검사를 다시 수행한다. 타임스탬프는 1차 inventory/baseline을 참고하며 바이트 단위 복구 보장을 원래 NTFS 권한/모든 메타데이터 복구 보장으로 확대하지 않는다.

남은 vendor 후보 215개는 동적 import/실행·Windows 환경 또는 실제 런타임 용도를 별도로 검토해야 한다. 완전한 전이 의존성 lock과 전체 기능 재현은 아직 미확정이다. 전체 vendor를 삭제하지 않는다.

다음 작업의 진입점은 루트 [README.md](../../README.md) → [수집·분류 기준](../data_collection_criteria_v1.md) → [품질 감사의 후속 계획](../../hormuz_2026_data/quality_audit_20261107_01/NEXT_SELECTION_PLAN.md)이다. 전체 메타데이터 기반 분산 수집 후보 선정은 별도 작업이며 이번 정리에서 수행하지 않았다.

새 보존 검증 결과: **True**. 이는 승인된 변경을 구분한 이번 정리 검증이며, 과거 검증/RECORD 무결성이 전부 통과한다는 뜻이 아니다. [post_cleanup_validation.json](post_cleanup_validation.json)에 검사 범위와 한계를 기록했다.
