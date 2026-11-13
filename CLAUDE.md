# Hormuz_Research — 호르무즈 해협 해외 뉴스 본문 수집

NewsCatcher로 모은 기사 메타데이터(URL·제목·날짜·언론사 등)를 바탕으로 기사 URL에 접속해 본문을 추출하고,
본문이 포함된 CSV를 만든다. 목표는 2026-01-01 ~ 2026-08-31 기간 성공 **50,000건**이며,
날짜별 큰 누락·편향이 없고 언론사·국가가 다양해야 한다.

## 확정된 결정 (지도교수 협의)

- **목표 성공 50,000건.** 도달하면 멈춘다. 용량 문제로 이보다 크게 넘기지 않는다.
  전처리(근사 중복 제거 등)로 나중에 줄어드는 것은 괜찮다(보충 대상 아님).
- **층별 목표**: 개전 전 2,500 / 개전 후 47,500 (5% 비율 유지). 일별 하한(5건 / 20건)과 주 단위 할당 방식은 그대로.
- **단일 도메인 상한**: 최종 성공 건수의 1% = 500건.
- **관련성**: 본문에 `hormuz`가 1회만 나와도 포함한다. 언급 횟수와 `title_has_hormuz`는 기록만 한다.
- **국가**: "언론사 소재국" 기준. `nc_country`는 오류가 많으므로 후처리에서 별도로 검증한다.
- **수집 공백(117일)**은 한계로 명시하고 진행한다. 보충 수집은 하지 않는다(`supplement_fetch.py`는 보존).
- **사전 제외 도메인 목록**은 현재대로 유지한다.
- **예정된 분석**: 국가별 보도 차이 비교, 토픽 모델링.

## 원칙

- **요청 전 추측으로 수집을 막지 않는다.** 수집 가능 여부는 실제 요청 결과로만 판단한다.
  사전 제외는 아래 "사전 제외 도메인"뿐이다.
- **User-Agent 예외**: Wikidata(국가 검증 2단계) 요청에만 Wikimedia 정책에 따라 연락처 URL(https://github.com/taehatae/Hormuz_Research)을 넣는다(config `wikidata_user_agent`). 뉴스 사이트 요청의 User-Agent에는 넣지 않는다.
- **robots.txt**: 정상적으로 받아왔고 해당 경로를 명시적으로 `Disallow`한 경우에만 수집하지 않고
  `robots_disallow`로 기록한다. robots.txt가 없거나(4xx) 받아오지 못한 경우(5xx, timeout 등)는 사유만 기록하고 진행한다.
- **차단·페이월은 우회하지 않는다.** Cloudflare 챌린지, 403, 페이월 등은 그대로 기록만 한다.
- **[승인 대기] 지점에서는 보고하고 멈춘다.** 다음 단계는 사용자가 지시한 뒤에 진행한다.
- **설계를 바꿔야 할 이유가 생기면 임의로 바꾸지 말고 질문한다.** 애매한 점도 질문한다.
- 모든 수치는 `collection/config.json`에 둔다. 코드에 수치를 박지 않는다.
- 스크립트는 `--stage` 인자로 단계를 받는다. **인자 없이 실행하면 네트워크 요청을 보내지 않는다.**
- 기존 데이터 파일은 수정·덮어쓰기·삭제하지 않는다. 새 산출물은 새 경로에 쓴다. 정리가 필요하면 삭제하지 않고 `_archive/`로 옮긴다.
- API 키는 `.env`에서 읽고 출력·기록하지 않는다.
- **커밋하는 파일에는 로컬 절대 경로(드라이브 문자나 사용자 홈 폴더로 시작하는 경로)를 쓰지 않고 프로젝트 기준 상대 경로를 쓴다.**
  커밋 전 검사 훅(`.githooks/pre-commit`: 로컬 사용자 경로, `.env`, 실제 API 키 값 차단)과 nbstripout(커밋할 때만 노트북 출력 제거)을 쓴다.
  새로 clone한 경우 `git config core.hooksPath .githooks`와 `nbstripout --install --attributes .gitattributes`로 다시 켠다.
- **NewsCatcher API에는 요청하지 않는다**(보충 수집 보류 중, 아래 참고). 사용자가 재개를 지시할 때만 요청한다.

## 폴더와 자료

| 경로 | 용도 |
|---|---|
| `collection/` | 코드와 config (git 관리). `config.json`, `common.py`, `allocation.py`, `plan.py`, `gap_days.py`, `supplement_fetch.py`, `collection_gap_days.csv` |
| `hormuz_2026_data/05_extraction_candidates_v2.csv` | **입력**. 427,875행, URL 중복 제거 완료본 (git 관리 밖) |
| `hormuz_2026_data/01_newscatcher_raw.csv` | NewsCatcher 원본 440,260행. 읽기 전용, 검증용 |
| `hormuz_2026_data/body_collection_v3/` | **모든 본문 수집 산출물** (경로는 config `output_dir`) |
| `hormuz_2026_data/supplement_20261108/` | 보충 수집 요청 기록 (현재 403 기록 1건) |
| `methods/hormuz_2026_news_textmining_filtered.ipynb` | 원 NewsCatcher 수집 노트북 (git `bb85a6c`에서 복원, 실행 로그 포함). 읽기 전용. **로컬 전용(git 관리 밖, `.gitignore`의 `methods/*.ipynb`)** — 없으면 `git show bb85a6c:hormuz_2026_news_textmining_filtered.ipynb`로 복원 |
| `hormuz_textminig_v2.ipynb` | **1~15절만** 참고 가능 (URL 정규화·중복 제거 규칙 등) |

**참고·재사용하지 않는 것**
- `_archive/` 전체: 이전 도구가 만든 코드, 문서, 큐, 승인 목록(`confirmed_access_restrictions.json`, `runs/`, `scripts/`, `docs/` 등).
- `hormuz_textminig_v2.ipynb`의 16절 이후.
- 입력 CSV의 파생 컬럼(`hormuz_relevance_score`, `extraction_priority`, `title_dup_*`, `flag_*`, `in_research_period` 등)은 필터링에 쓰지 않는다.

**입력에서 쓰는 컬럼**: `article_id, url, domain, title, published_date, nc_word_count, nc_content_snippet,
nc_paid_content, nc_country, nc_source_name, nc_published_date_precision`

## 데이터 특성

- 원 수집 검색어는 **`"Strait of Hormuz"`와 `"Hormuz Strait"` 두 개**이며 따로 요청했다.
  조건: `lang=en`, `exclude_duplicates=True`, `sort_by=date`, `page_size=1000`, `search_in` 미지정.
- 이란-미국 전쟁이 2026-02-28에 발발했다. 1~2월 기사가 적은 것은 정상이다.
- `nc_content_snippet`: 본문 앞 300자 미리보기. **본문으로 쓰지 않고 검증용**으로만 쓴다.
- `nc_word_count`: 전체 기사 기준 단어 수로 보인다. 품질 점검 지표로 쓴다.
- `nc_country`, `nc_language`: 오류가 많다. **할당·필터에 쓰지 않고 기록만** 한다.
- `nc_paid_content`: 필터로 쓰지 않고 기록만 한다.
- `published_date`: 시간대 표기가 없다. UTC로 간주해 날짜를 자른다.
  `nc_published_date_precision`은 `full` / `date`(시각 없음, 00:00:00으로 기록) / `timezone unknown`.
- `domain` 컬럼은 `www.` 유무가 섞여 있다.

## 확정된 설계

### 사전 제외 도메인 (재게시·애그리게이터·비뉴스)
`wn.com, msn.com, yahoo.com, newsbreak.com, headtopics.com, ground.news, dailyhunt.in, custommapposter.com, news-pravda.com, biztoc.com`
— 서브도메인 포함 접미사 매칭. 제외 건수와 도메인별 건수를 리포트에 남긴다.

### 표본 할당
- 목표 성공 50,000건. 층 2개(`published_date` UTC 날짜 기준):
  - 개전 전 2026-01-01 ~ 02-27: 2,500건, 하루 하한 5건
  - 개전 후 2026-02-28 ~ 08-31: 47,500건, 하루 하한 20건
- **주 단위 층화**: 일별 수집량이 수집 과정의 영향을 받으므로, 각 층을 층 시작일부터 7일 블록으로 나누고(층 끝에서 마지막 블록을 자름)
  층 목표를 **주별 후보 수에 비례**해 배분한다. 이동평균은 쓰지 않는다.
- 주 안에서는 **하루 하한을 먼저 확보**(그날 후보가 더 적으면 전부)하고, 나머지는 그 주 전체 후보를 고정 시드(2026)로 섞은 순서로 채운다.
  "성공" 건수가 할당에 차면 멈추고, 실패하면 다음 기사로 넘어간다.
- **부족분 재배분**: 같은 주의 다른 날 → 같은 층의 가장 가까운 주(**이전 주부터**, 앞뒤 번갈아).
- **도메인 상한**: 최종 성공 기준 전체의 1%(500건). 단위는 **호스트에서 `www.`만 뗀 값**. 상한에 도달한 도메인은 건너뛰고 `domain_cap`으로 기록한다.

### 요청 (fetch)
- `requests` 사용. User-Agent는 연구용 클라이언트임을 밝히는 일반적인 이름이며 이메일은 넣지 않는다.
- **같은 등록 도메인**(tldextract 내장 목록 기준, 예: `indiatimes.com`)에는 최소 2초 간격. 동시성은 도메인 간 분산.
- 동시 작업 16개, 연결 타임아웃 10초, 읽기 타임아웃 20초.
- 재시도: 5xx, 429, timeout, 일시적 연결 오류만 지수 백오프로 최대 2회.
- 도메인 자동 제외(실제 결과 기반): 한 도메인에서 연속 5건이 모두 `http_403`·`cloudflare_challenge`·`paywall`이면
  그 도메인의 나머지를 건너뛰고 `domain_skipped_after_failures`로 기록한다. 그 밖의 실패(`code_or_markup`, `partial_body`,
  `http_429`, `http_401` 등)는 이 집계에 넣지 않는다.
- 성공적으로 받은 HTML은 gzip으로 저장한다(config로 끌 수 있음).

### 추출과 성공 판정
- 추출기는 **trafilatura 단독**: `include_comments=False, favor_precision=True, with_metadata=True`.
- **성공** = 아래를 모두 만족(파일럿 후 확정, `criteria_v2`). 위에서부터 처음 걸린 항목이 `failure_reason`:
  1. URL 경로에 `/topic/`, `/tag/`, `/tags/`, `/category/`가 없음 → 있으면 `non_article_page`(받은 뒤 판정)
  2. 본문 대부분이 코드·CSS·마크업이 아님(코드 토큰 100단어당 5개 미만) → `code_or_markup`
  3. 페이월·구독 안내 문구 위주가 아님(문장 단위 단어 비중 50% 미만, 100단어 미만이면서 페이월 문구가 있으면 페이월) → `paywall`
  4. 쿠키·로그인·동의 등 안내 문구 위주가 아님(문장 단위) → `boilerplate_only`
  5. 본문 **100단어** 이상 → `too_short`
  6. `words_ratio_to_nc` ≥ 0.5 (nc_word_count가 없거나 0이면 적용하지 않음) → `partial_body`
  7. 본문에 `hormuz` 포함(대소문자 무시) → `no_hormuz`
  8. `language_detected` = `en` → `not_english`
  9. 정규화한 본문 해시가 이미 성공한 기사와 같지 않음(완전 중복 제거) → `exact_duplicate`
- 라이브 블로그(URL에 `/live/`, `/live-updates/`, `/liveblog/`, `/live-news/` 등)는 `page_type_suspect = live`로 표시만 하고 판정은 그대로.
- **기록만 하는 품질 지표**(성공 기준 아님):
  `words_ratio_to_nc`(추출 단어 수 / nc_word_count), `snippet_match`(snippet 앞 약 120자를 공백·따옴표 정규화한 뒤 본문 포함 여부,
  미포함 시 가장 비슷한 구간과의 유사도), `language_detected`,
  `hormuz_mention_count`(본문 내 "hormuz" 등장 횟수), `strait_of_hormuz_count`("strait of hormuz" 또는 "hormuz strait" 등장 횟수),
  `title_has_hormuz`(NewsCatcher 제목에 "hormuz" 포함) — 모두 대소문자 무시,
  `sentence_count`, `title_body_overlap`(제목 단어 중 본문 첫 100단어에 나오는 비율), `body_near_title`(본문이 제목과 거의 같음),
  `non_alpha_ratio`(공백 제외 문자 중 알파벳·숫자·일반 구두점이 아닌 비율), `over_10000_words`, `code_token_density`,
  `paywall_share`, `boilerplate_share`, `page_type_suspect`.
- 성공 기준을 바꾸면 저장된 HTML로 다시 판정한다(`python collection/rejudge.py`, 실행 전 DB 백업, 변경 내역은 `rejudge_log`).
  관련성(호르무즈 언급 횟수·제목 포함)은 성공 기준이 아니라 분석 단계 변수다.

### 기록
- 시도한 모든 URL을 한 줄씩 즉시 기록(jsonl 또는 SQLite). 중단 후 재개할 수 있어야 한다.
  필드: `article_id, url, final_url, http_status, fetch_status, extract_status, failure_reason, robots_note, body_word_count,
  words_ratio_to_nc, snippet_match, has_hormuz, hormuz_mention_count, strait_of_hormuz_count, title_has_hormuz, language_detected,
  body_sha1, stratum, date, domain, attempted_at` + 위의 기록용 품질 지표
- `failure_reason` 예: `http_403, http_404, cloudflare_challenge, paywall, timeout, non_article_page, code_or_markup, boilerplate_only,
  too_short, partial_body, no_hormuz, not_english, exact_duplicate, robots_disallow, domain_skipped_after_failures, domain_cap,
  pre_excluded_domain`(수집 후 사전 제외 목록에 추가된 도메인)
- 최종 CSV: 원본 컬럼 + `body, body_word_count`, 품질 지표, `extractor_version, extracted_at`,
  `collection_gap_day, collection_gap_type, nc_published_date_precision`.

## 수집 한계와 보충 수집 보류

- **원인**: 원 수집이 `to_`에 날짜만 넘기고 9,000건 초과 구간을 `[시작, 중간] / [중간+1, 끝]`으로 재귀 분할했다.
  그래서 각 구간 마지막 날의 00:00:00 이후 기사가 누락됐다. 로그 검증 결과: 마지막 단계 구간 hits 합 = 440,260,
  하루짜리 구간 hits와 자정 기사 수의 상관 0.999.
- **공백일**: `"Strait of Hormuz"` 117일, `"Hormuz Strait"` 9일(모두 겹침). 유형별로 none 126일 / soh_only 108일 / both 9일.
  목록은 `collection/collection_gap_days.csv`(`python collection/gap_days.py`로 methods/ 로그에서 재생성).
  기사마다 `collection_gap_day`, `collection_gap_type`(none / soh_only / both)을 붙인다.
- **추정 누락**: 약 58만 건(범위 약 50만~65만, 정상일 기준 거친 추정). 3~4월이 가장 크다(70% 이상).
- **치우침**: 공백일 기사의 절반 이상이 `date` 정밀도 기사이고 일부 언론사에 몰려 있다. 주 단위 할당은 분량만 맞출 뿐 이 구성 차이는 바로잡지 못한다.
- `timezone unknown` 기사(후보 27,576건)는 UTC 간주로 날짜가 하루 어긋날 수 있다.
- **보충 수집 보류**: 2026-11-08 테스트 요청이 HTTP 403(7일 체험판 만료)으로 거부됐고, 과거 기사 조회는 Enterprise 요금제만 가능하다.
  연구 목적상 시계열 분석보다 1~8월 포괄과 언론사·국가 다양성이 중요하므로 공백은 한계로 기록하고 진행한다.
- **접근이 다시 생기면** `collection/supplement_fetch.py`로 진행한다:
  - `from_`/`to_`는 `YYYY/mm/dd HH:MM:SS` 형식으로 하루 전체(00:00:00 ~ 23:59:59)를 지정한다. 나머지 조건은 원 수집과 같다.
  - 검색어별로 자기 공백일만 재요청한다. 한 날짜가 9,000건을 넘으면 시각 단위로 나눈다.
  - 날짜·검색어·페이지 단위로 기록해 이어받을 수 있게 한다.
  - 원본과 보충분을 노트북 4~5절의 규칙으로 합쳐 새 후보 파일(`05_extraction_candidates_v3.csv`, `collection_batch` = original/supplement)을 만들고,
    보충 전후의 날짜별 기사 수와 자정 비중을 비교한 뒤 할당을 다시 계산한다.

## 진행 단계

1. **계획 리포트** (네트워크 미사용): `python collection/plan.py` → `body_collection_v3/plan/PLAN_REPORT.md`.
   모집단 규모, 층별·주별·일별 할당, 하한, 재배분 시뮬레이션, 도메인 상한 예상, 수집 한계를 담는다. — 완료
2. **시험 실행** 100건(`--stage smoke`): 실제 HTTP 요청이 나갔는지, 상태 코드와 실패 사유 분포를 보고한다. — 완료
3. **파일럿** 약 2,000건(`--stage pilot`, 본 실행과 같은 방식, 결과는 본 실행에 이어 씀): 성공률, 실패 사유 분포, 도메인별 성공률 상위/하위,
   snippet_match·words_ratio_to_nc 분포(성공/실패 비교), no_hormuz 실패 10건과 비율이 극단적인 10건의 본문 앞부분,
   성공 기준 조정 제안을 보고한다. — 완료(성공 기준 criteria_v2 확정, 저장 HTML로 재판정)
4. **본 실행**(`--stage main`): 성공 50,000건 도달까지(주별 할당 합계 = 50,000, 진행 중 건수까지 고려해 넘기지 않음).
   - 1시간마다 층별·주별 충족률과 누적 성공률을 `body_collection_v3/logs/main_progress.log`에 기록한다.
   - 이상 징후가 보이면 자동으로 멈추고 `events`에 기록한다(기준은 config `runs.anomaly_*`): 최근 구간 성공률 급락,
     대부분 요청 전 차단, 연속 네트워크 오류, 디스크 여유 공간 부족. 원인을 확인한 뒤 같은 명령으로 이어서 실행한다.
5. **후처리 리포트**(`--stage report`, 네트워크 미사용) **[승인 대기]**
   - 근사 중복(MinHash 등, 통신사 재게재) 그룹 탐지. 삭제하지 않고 그룹 ID만 부여한다. 처리 방식은 결과를 보고 정한다.
   - 최종 분포: 층별·주별·월별, 도메인 수와 상위 도메인 점유율, `nc_country`(참고), `collection_gap_type`별 분포.
     원 모집단 분포와 비교하고, 할당 부족 날짜를 보여 준다.
   - 실패 사유 집계, 자동 제외된 도메인과 그 도메인의 원 모집단 내 비중.
   - **국가 검증**: 최종 표본에서 기사 수 기준 상위 도메인(누적 약 80%)의 언론사 소재국 검증 목록을 만들고 `nc_country`와 대조한다.
     검증 방법은 그 단계에서 정한다.
   - 의심 도메인 검토 목록: 성공 상위 30개 도메인과 지정 도메인의 기사 예시(제목, 본문 앞 300자).
   - `partial_body` 중 본문 300단어 이상 예시, `over_10000_words` 성공 건 전체 목록.
   - 도메인 제외·기준 변경으로 50,000건 아래로 내려갈 때 같은 주에서 보충 수집하는 방법(근사 중복 제거로 줄어드는 것은 보충 대상 아님).
