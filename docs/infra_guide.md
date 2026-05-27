# 인프라 구성 가이드 — 회사·집 병행 개발

> 이 문서는 트레이딩 봇 레포의 인프라 설계도다.
> 집/회사 PC에서 이 문서대로 따라 하면 개발 환경이 구성된다.
> 패키지 관리: venv + pip (Python 표준). 레포: GitHub private 단일 레포.

---

## 0. 사전 개념 (모바일 개발자용 비유)

| 트레이딩 봇 (Python) | 안드로이드 / iOS 대응 개념 |
| --- | --- |
| venv | 프로젝트별 의존성 격리 (Gradle / CocoaPods 환경) |
| requirements.txt | build.gradle dependencies / Podfile |
| .env (API 키) | keystore / local.properties / xcconfig 시크릿 |
| .gitignore | 동일 개념 — 빌드 산출물·시크릿 제외 |
| pip install | Gradle sync / pod install |

새로 배울 것은 Python 문법 정도이며, 레포·Git·환경 분리 개념은 모바일
개발 경험 그대로 적용된다.

---

## 1. 레포 구조 (단일 레포)

GitHub에 **private** 레포 1개 생성. 폴더 구조 권장안:

```
trading-bot/
├── README.md
├── .gitignore
├── .env.example          # 키 견본 (값은 빈칸, 레포에 포함 OK)
├── requirements.txt      # Python 의존성 목록
│
├── docs/                 # 설계 문서 (이번에 만든 4개 파일)
│   ├── trading_bot_design.md
│   ├── backtest_spec.md
│   ├── deployment_guide.md
│   └── infra_guide.md     # 이 문서
│
├── scripts/
│   └── download_binance_data.py
│
├── strategy/             # ★ 전략 로직 (순수 함수) — 백테스트·봇 공유
│   └── ...                # 설계서 7.5절 원칙
│
├── backtest/             # 백테스트 엔진 — strategy/ 를 import
│   └── ...
│
├── bot/                  # 실거래 봇 — 9개 모듈, strategy/ 를 import
│   ├── market_data/
│   ├── validation/
│   ├── strategy_module/
│   ├── risk/
│   ├── execution/
│   ├── sync/
│   ├── monitoring/
│   ├── logging_module/
│   └── killswitch/        # 독립 프로세스로 기동
│
├── data/                 # 백테스트 데이터 — .gitignore 처리 (레포 제외)
├── state/                # 봇 런타임 상태 — .gitignore 처리
└── logs/                 # 로그 — .gitignore 처리
```

**핵심:** `strategy/` 는 백테스트와 봇이 공유하는 단일 전략 코드.
두 레포로 쪼개면 이 공유 코드의 소속이 애매해지므로 단일 레포가 맞다.

---

## 2. .gitignore (레포 루트에 생성)

키·데이터·런타임 산출물은 절대 레포에 올리지 않는다.

```
# 키·시크릿
.env
*.key
*.pem
config/secrets*

# 백테스트 데이터 (스크립트로 재생산 가능)
data/

# 봇 런타임 상태
state/
*.state.json

# 로그
logs/
*.log

# Python
__pycache__/
*.pyc
.venv/
venv/
.pytest_cache/

# OS / 에디터
.DS_Store
.idea/
.vscode/
```

---

## 3. .env.example (레포에 포함 — 값은 빈칸)

새 환경에서 이 파일을 복사해 `.env` 를 만든다. 견본이므로 올려도 안전.

```
# 바이낸스 API — 환경별로 다른 값을 넣는다
BINANCE_API_KEY=
BINANCE_API_SECRET=

# 킬스위치 전용 독립 키 (실행 모듈 키와 별개)
KILLSWITCH_API_KEY=
KILLSWITCH_API_SECRET=

# 봇 내부 서명용 비밀키 (리스크-실행 토큰 서명, 거래소 키와 무관)
INTERNAL_SIGNING_SECRET=

# 실행 환경: testnet | live
RUN_MODE=testnet

# 알림 (모니터링 모듈)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

`RUN_MODE` 로 테스트넷/실거래를 전환한다. 코드는 동일, .env 만 다르다.

---

## 4. 환경별 .env 운영

`.env` 는 레포에 없으므로 각 환경에 직접 만든다. 환경마다 내용이 다르다.

| 환경 | .env 내용 | 비고 |
| --- | --- | --- |
| 회사 PC | RUN_MODE=testnet, 키는 비우거나 테스트넷 키만 | 백테스트는 키 불필요 |
| 집 PC | RUN_MODE=testnet, 테스트넷 키 | 개발·검증용 |
| 클라우드 VM | RUN_MODE=live, 실거래 키 | 실거래 키는 여기에만 |

실거래 키는 클라우드 VM의 `.env` 에만 존재. 회사·집 PC에는 두지 않는다.
`.gitignore` 로 `.env` 가 빠져 있으므로, 회사에서 pull 해도 키는 따라오지
않아 환경 분리가 구조적으로 보장된다.

---

## 5. 첫 세팅 순서 (회사·집 PC 각각 1회)

```
# 1. 레포 복제
git clone <레포 URL>
cd trading-bot

# 2. Python 가상환경 생성·활성화
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Mac:      source .venv/bin/activate

# 3. 의존성 설치 (requirements.txt 는 백테스트 코드 작성 시 생성됨)
pip install -r requirements.txt

# 4. .env 생성
#    .env.example 을 복사해 .env 로 만들고, 그 환경에 맞는 값 입력

# 5. 백테스트 데이터 받기 (data/ 는 레포에 없으므로 직접)
python scripts/download_binance_data.py
```

세팅이 처음이라면 Claude Code 에 이 문서를 주고 "이대로 환경 세팅해줘"
라고 하면 단계별로 진행해 준다.

---

## 6. 회사·집 병행 워크플로우 (일상 규칙)

```
작업 시작 전:  git pull          # 항상. 빼먹으면 양쪽이 어긋남
작업 후:       git add → commit → push

데이터:        pull 로 안 옴 → 각 PC 에서 download 스크립트 1회 실행
키:            pull 로 안 옴 → 각 PC 에서 .env 직접 관리
```

**금지:** 키가 필요한 작업(실거래 연동·실거래 가동)은 회사 PC 에서 하지
않는다. 회사 PC 는 백테스트·전략·문서 작업까지만.

---

## 7. 클라우드 합류 시 (실거래 단계)

봇 완성 후 오라클 클라우드 VM 도 동일 레포를 사용한다.

```
회사 PC ─┐
         ├─ GitHub (private) ── 클라우드 VM
집 PC  ─┘

VM 배포:   git pull → 봇 재기동
실거래 키:  VM 의 .env 에만 (RUN_MODE=live)
```

상세 절차는 deployment_guide.md 참조.

---

## 요약

- GitHub private 레포 1개. 회사·집·클라우드가 모두 이 레포를 공유.
- `.gitignore` 로 키(.env)·데이터(data/)·런타임 상태(state/, logs/) 제외.
- 키는 레포가 아니라 각 환경의 `.env` 에 따로. 실거래 키는 클라우드에만.
- 패키지 관리는 venv + pip (Python 표준).
- 작업 전 pull, 작업 후 push. 데이터·키는 각 환경에서 따로 준비.
