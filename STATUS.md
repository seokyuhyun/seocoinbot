# 작업 핸드오프 — 회사 PC → 집 PC 전환용

> Claude Code 새 세션이 이 파일 먼저 읽고 시작하면 진행 상태 그대로 복원됨.
> 진행 한 단계 끝날 때마다 이 파일 갱신.

**마지막 갱신:** 2026-06-01 (회사 방화벽이 바이낸스 WS 차단 확인 → 집 PC 로 핸드오프)
**현재 단계:** 실시간 시그널 봇 (펀딩 spike + 청산 캐스케이드 + paper trade + 텔레그램 명령어) 완성. 집 PC 에서 가동 시작 필요.

---

## TL;DR — 큰 그림

1. **백테스트 전략 4개 다 walk-forward 실패** → 실시간 시그널 봇 방향으로 pivot 완료
2. **실시간 봇 (bot/realtime/) 완성** — 코드 100%. 텔레그램 토큰·chat_id 도 .env 에 설정됨
3. **회사 PC 가동 시도 → 회사 방화벽이 바이낸스 WebSocket 차단** (test_ws.py 로 확인). 3일 0건은 이게 원인. 봇 문제 아님
4. **집 PC 에서 가동 = 100% 정상 작동 예상** — 단순히 `git pull` + `python -m bot.realtime.main`

---

## 바로 다음 명령 (집 PC 에서 첫 메시지)

집 PC 에 처음 도착해서 환경이 안 깔려있으면:

```
seocoinbot 이어서 작업할 거야. STATUS.md 읽고 환경부터 셋업해줘.
```

환경 이미 있고 봇만 켜고 싶으면 (시간 절약 — Claude Code 안 거치고 바로):

```cmd
cd C:\path\to\seocoinbot
git pull origin main
.venv\Scripts\activate
python -m bot.realtime.main
```

봇 시작 메시지 텔레그램 도착 → /help2 보내면 명령 목록 → 시간 지나면 시그널 알림 옴.

---

## 4개 전략 시도 결과 (요약표)

| 전략 | 파일 | 전구간 수익률 | walk-forward | 결론 |
|---|---|---|---|---|
| 1. SRT C1 (legacy) | `backtest/verify_oos_srt_c1.py` | OOS +53.67% | K=4 통과 (75%) | 이전 v1.0 — 보류 |
| 2. trend breakout (15m+1h) | `backtest/run_trend_breakout.py`, `strategy/trend_breakout.py`, `backtest/trend_breakout_engine.py` | -12.71% (BTC only, IS) | — | strict 12pt 도 음수. 폐기 |
| 3. breakout_v2 multi-symbol | `backtest/breakout_v2.py` | -28% (7sym) → -64% (20sym) → -59% (BTC 제외) → -95% (regime) | — | 늘릴수록 더 망함. 폐기 |
| 4. pullback v3 (4h pullback) | `backtest/pullback_v3.py`, `..._walkforward.py` | -2.27% | **부분 통과**: SHORT-only 3/4 fold 양수, LONG 0/4 | **부분 합격** |
| 5. ORB-C v4 (세션 돌파+풀백) | `backtest/orbc_v4.py`, `..._walkforward.py` | -11.36% (172거래) | 1/4 fold 양수 | 폐기 |

**유일하게 ship 가능 후보 = pullback v3 SHORT-only.** 단 SHORT 만 가능 → 폭등장 0%.

---

## 실시간 봇 (bot/realtime/) — 현재 상태

**기능 완성**. 코드는 깃에 있고 즉시 가동 가능. 단 회사 방화벽이 바이낸스 WS 차단해서 회사 PC 에서 못 돌림.

### 구조
| 모듈 | 역할 |
|---|---|
| `bot/realtime/config.py` | .env 로드 (TG 토큰, chat_ids, 임계값, refresh 주기 등) |
| `bot/realtime/telegram.py` | sendMessage / getUpdates (long polling) |
| `bot/realtime/binance_ws.py` | WebSocket 매니저 (재연결 + backoff) |
| `bot/realtime/symbol_picker.py` | 상위 N USDT 무기한 조회 (거래대금 기준) |
| `bot/realtime/levels.py` | 적응형 TP/SL 계산 (펀딩·캐스케이드 별) + 시그널 포맷터 |
| `bot/realtime/paper_trader.py` | 가상 진입·청산·CSV 저장·승률 통계 |
| `bot/realtime/liquidation_alerts.py` | `!forceOrder@arr` 스트림 → 캐스케이드 감지 |
| `bot/realtime/handler.py` | 두 스트림 통합 처리 + paper trade 트리거 |
| `bot/realtime/commands.py` | 텔레그램 명령어 (/help2 /status2 /summary2 /now2 /pause2 /resume2 /stop2) |
| `bot/realtime/main.py` | asyncio.gather (mark WS + force WS + cmd polling + 심볼 갱신) |
| `bot/realtime/test_ws.py` | **방화벽 진단용** — 봇 안 거치고 WebSocket 직접 받는지 테스트 |

### 시그널 종류
1. **펀딩비 spike** — 8h 정산 임박 시 자주. 펀딩 ±0.02% 이상 → 반대 방향 진입
2. **청산 캐스케이드** — 5분 강제청산 $500k 이상 + 한쪽 80%+ + 가격 ±1.5% → 반발 매매

### 텔레그램 명령어 (DM 에서)
- `/help2` 도움말
- `/status2` 가동시간·오픈 포지션
- `/summary2` 누적 승률·PF
- `/now2` 모니터 중인 심볼들의 현재 펀딩비 top 15 (진단)
- `/pause2` 새 시그널 일시정지
- `/resume2` 재개
- `/stop2` 종료

### `.env` 필수 항목
```
TELEGRAM_BOT_TOKEN=<BotFather 토큰>
TELEGRAM_CHAT_IDS=ID1,ID2          # 또는 TELEGRAM_CHAT_ID 단수형 폴백
REALTIME_TOP_N_SYMBOLS=100         # 기본 100 (옛날 30 이면 spike 잘 안 잡힘)
REALTIME_FUNDING_THRESHOLD=0.0002  # 0.02%. 이전 0.05 → 0.03 → 0.02 로 낮춤
REALTIME_SYMBOL_REFRESH_MINUTES=60 # 1시간마다 풀 재선정
REALTIME_LEVERAGE=5                # 5배 권장
REALTIME_MAX_CONCURRENT=5
REALTIME_TIME_STOP_HOURS=8
```

### 회사 vs 집 — 방화벽 진단법
새 CMD 창에서 `python -m bot.realtime.test_ws` 실행 → WebSocket 메시지 3개 받는지 확인. 받으면 OK, 타임아웃이면 방화벽 차단.

집에서 잘 돌면 — **paper trade 결과 누적이 진짜 목표**. 1주일 이상 켜둬서 100건+ 모이면 펀딩 spike vs 캐스케이드 어느 시그널이 실제 양수 edge 있는지 데이터로 확인 가능.

---

## 4번 시도가 알려준 객관 사실 (집중)

1. **15m breakout 매수 = 2025-2026 BTC 시장에 음수 edge** (PF 0.82~0.90)
2. **LONG 메커니즘 모두 walk-forward 실패** (4번 시도 통틀어 0개 통과). 시장 탓 + breakout/pullback long 자체의 한계
3. **SHORT 가 LONG 보다 항상 좋음** — 2025-2026 시장 추세
4. **신호 완화 = 손실 가속** (1차원적 데이터 마이닝 함정)
5. **WEAK_DOWN regime 만 일관되게 양수** — 초기 약세 진입
6. **거래수 늘려도 PF 안 좋아짐** — 메커니즘 문제, 빈도 문제 아님
7. **수수료+슬리피지+펀딩 = 라운드트립 ~0.16%** — retail 알고의 최대 적

---

## 본인이 원한 것 vs 데이터가 말한 것

| 본인 원함 | 데이터 현실 |
|---|---|
| 일 1건 이상 거래 | v3 = 일 0.08건 / v4 = 0.36건 |
| 월 30% 수익 | 실측 -2~-95% (음수). 이론 ceiling 도 월 1~3% |
| 폭등·폭락 다 잡기 | LONG 메커니즘 다 실패 → SHORT-only 만 가능 |
| 꾸준한 양의 edge | walk-forward 통과 = pullback v3 SHORT 뿐 |

→ 본인 목표 = retail backtest-only 알고로는 달성 불가능. **WebSocket 실시간 데이터로 차원 바꿔야** 가능성 있음.

---

## 다음 진짜 가능성 — WebSocket 실시간 전략 3개

backtest 가 아닌 **실시간 호가창·체결·청산 스트림 활용**. 바이낸스 무료 제공.

### A. 청산 캐스케이드 사냥 (가장 추천)
- `@forceOrder` 스트림으로 강제청산 실시간 감지
- cascade 시작 → 끝 판정 → 반발 진입
- 일평균 3~20 신호. 기대 연 10~40%
- 작업: 1주 (인프라+로직+paper trade 시작)

### B. Order Book Imbalance (OBI) 스칼핑
- `@depth` 스트림으로 매수벽/매도벽 비율
- 짧은 진입·짧은 익절
- 일 20~100+ 신호. 기대 -10~+30% (수수료 비중 큼)
- 작업: 1~2주

### C. 펀딩 차익
- `@markPrice` 로 모든 USDT 무기한 펀딩비 추적
- 펀딩 spike 시 반대 방향 진입
- 일 1~5 신호. 기대 연 5~15% (가장 안정)
- 작업: 3일

### D. A+C 분산
- 다른 시장 상황 cover. 가장 견고
- 작업: 1~2주

**저(=Claude)의 추천: D 또는 A 부터.**

### WebSocket 으로 가면 바뀌는 것
- 백테스트 → 실시간 데이터 수집 + paper trade
- 24/7 봇 운영 필요 (PC 끄면 신호 놓침)
- 클라우드 (AWS / DigitalOcean ~$5/월) 권장
- 회사/집 PC 전환 워크플로 영향 큼 → 클라우드 배포가 더 적합

---

## SRT C1 시대 결과 (legacy, 보존)

| 지표 | IS (12,264봉) | OOS (5,256봉) |
|---|---|---|
| 수익률 | +109.23% | +53.67% |
| MDD | -26.82% | -16.80% |
| PF | 1.44 | 1.60 |
| 승률 | 48.7% | 47.8% |

후보 파라미터: `SRTParams(trix_required=False, rr=1.5, swing_lookback=20)` — `strategy/yt_strategies.py` 에 박혀 있음. 1h main, HTF 없음.

**같은 OOS 구간 2회 사용 (명세 §6.2 한도 소진).** 이 데이터셋 OOS 더 못 함.

---

## 새로 만든 파일 (회사 PC, 이번 세션)

### 코드 (gitignored 아님, push 됨)
```
strategy/
  trend_breakout.py            ← 1H 추세 + 15m 거래량 돌파 (폐기, 코드만 보존)

backtest/
  trend_breakout_engine.py     ← 위 전략 엔진
  run_trend_breakout.py        ← IS sweep (lev 1/2/3)
  breakout_v2.py               ← 사용자 제공 단일파일. 캐시 + EXCLUDE_BTC + regime 추가
  pullback_v3.py               ← 4h 추세 풀백 (★ SHORT-only walk-forward 부분 통과)
  pullback_v3_walkforward.py   ← v3 K=4 walk-forward
  orbc_v4.py                   ← 세션 ORB-C (폐기)
  orbc_v4_walkforward.py       ← v4 K=4 walk-forward
```

### 데이터 (gitignored, 집 PC 에서 재생성)
```
data/breakout_v2_cache/        ← 20심볼 × 15m + 1h Binance fapi 캐시
data/pullback_v3_cache/        ← 3심볼 × 4h + 1d 캐시
```

### 로그 (gitignored)
```
backtest/*_run*.log            ← 백테스트 출력. 집에서 재실행하면 됨
```

---

## 집 PC 에서 환경 복구 (LTSC 우회 포함)

### Python 설치 (회사 PC LTSC 와 다를 수 있음)
- 집이 일반 Windows 라면 python.org 인스톨러 그대로
- 집도 LTSC 면 아래 우회 (이전 메모리 항목):
  ```powershell
  curl -sSL -o $env:TEMP\uv.zip https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip
  Expand-Archive $env:TEMP\uv.zip C:\Users\$env:USERNAME\tools\uv
  C:\Users\$env:USERNAME\tools\uv\uv.exe python install 3.12
  & "$env:APPDATA\uv\python\cpython-3.12.13-windows-x86_64-none\python.exe" -m venv .venv
  ```

### 의존성
```powershell
git clone https://github.com/seokyuhyun/seocoinbot.git
cd seocoinbot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install requests   # breakout_v2.py + orbc_v4.py 가 사용 (requirements.txt 에 없을 수도)
```

### `.env`
```powershell
cp .env.example .env
# 백테스트만 할 거면 키 빈 채로 OK
```

### 캐시 데이터 (집에서 새로 다운로드)
- 처음 실행 시 Binance fapi 에서 자동 다운로드 (캐시 폴더에 저장)
- 첫 실행 5~10분 소요 (rate limit 회피 sleep 포함)
- 두 번째 실행부터 캐시 사용으로 1~10초

---

## 운영·코드 규칙 (변동 없음)

- **stdout UTF-8 강제 + errors="replace"**: 모든 백테스트 스크립트 상단. orphan surrogate 가 Claude Code API 거부 막음 (2026-05-28 수정)
- **신호는 캔들 close, 진입은 다음 캔들 open** (look-ahead 차단)
- **인트라바 H/L 가 SL 관통 시 SL 우선 청산** (보수, 명세 §4)
- **TP vs SL 같은 봉 → SL 우선** (보수)
- **OOS 단일 사용 (§6.2)**: 이 BTC 데이터셋 (2024-05 ~ 2026-04) OOS 는 SRT 검증으로 소진. 새 데이터 (2026-05 이후) 누적되면 그게 새 OOS

---

## 회사·집 PC 차이 관련 메모

- **git config** (user.name / user.email) 이미 글로벌. 새 PC 면 다시 설정 필요
- **PowerShell ExecutionPolicy** — `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
- **데이터 파일** 다 gitignored. 집에서 자동 재다운로드됨
- **WebSocket 전략 가면** 클라우드 24/7 배포가 답 (PC 전환 무관해짐)

---

## 새 Claude Code 세션 시작 시 권장 첫 문장 (= 위 "바로 다음 명령" 과 동일)

```
seocoinbot 이어서 작업할 거야. STATUS.md 와 git log -15 읽고 현재 상태
파악한 다음, 다음 단계 진행해줘.

내 마지막 결정은: [A/B/C/D/E/F 중 하나]

(결정 못 했으면 추천해줘)
```

---

## 아직 안 한 일

- WebSocket 전략 (A/B/C/D 중 어떤 거든) 구현 시작 ← **다음 단계**
- Paper trade 환경 구성 (E)
- 클라우드 배포 (필요시)
- 9 모듈 봇 (설계서 §5) — SHORT-only 변형이라도 ship 결정 시
- 바이낸스 testnet — 봇 완성 후
- 실거래 (소액) — testnet 검증 후

설계서 §8 흐름 그대로.

---

## 최근 git log (참고)

- `a207ce0` Sanitize stdout to drop orphan surrogates (Claude Code 400 fix)
- `5642ccd` SRT C1 passes OOS — strategy v1.0 candidate locked  ← 이전 v1.0 후보 시점
- `671c701` Add SRT walk-forward (K=4)
- `5071f76` Tune SRT: TRIX/RR/swing grid
- `adac71c` Add 4 YT strategies + engine + IS comparison
- (이번 커밋) 4개 새 전략 시험 + WebSocket pivot 검토 핸드오프
