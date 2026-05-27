# BTC 선물 자동매매 봇 — 설계서

**문서 버전: v0.2**

> 이 문서는 봇 개발 전 합의된 설계 기준이다.
> 9개 모듈 프로젝트 전부와 백테스트 작업이 이 문서를 단일 기준으로 참조한다.
> 모든 수치는 **출발점(가설)** 이며, 백테스트로 검증·조정한 뒤 v1.0으로 확정한다.
>
> 버전은 파일명이 아니라 이 문서 안에서만 관리한다. 개정 시 위 '문서 버전'을
> 올리고 아래 변경 요약에 한 줄 추가한 뒤, '00. 공통 스키마' 프로젝트의 이
> 파일을 교체한다. 9개 모듈 프로젝트의 지침은 손대지 않는다.

> **v0.2 변경 요약** (v0.1 대비)
> - 9장 추가: 기술 구현 결정 (구현 형태, 데이터 수급, 상태 저장, 코드 구조)
> - 손절·익절 분담 원칙 명시: 손절은 거래소 측 주문, 익절은 봇 감시
> - 운영 환경: 개인 PC 24시간 가동 전제, 다운 시나리오 대비책 명시

---

## 1. 거래 환경 (확정)

| 항목 | 값 |
| --- | --- |
| 거래소 | 바이낸스 USDT-M 선물 (`binance_usdm_futures`) |
| 심볼 | `BTCUSDT` 단일 |
| 마진 모드 | 격리 (ISOLATED) |
| 포지션 모드 | 단방향 (ONE-WAY) — 롱·숏 동시 보유 안 함 |
| 메인 타임프레임 | 15분봉 |
| 상위 필터 타임프레임 | 1시간봉 |
| 레버리지 | 초기 테스트 1~2배 / 실전 초반 2~3배 |
| 거래 규격 (tick/step/minQty/minNotional) | **하드코딩 금지.** 봇 부팅 시 거래소 `exchangeInfo` API로 수신, 매일 갱신 |

**중요:** 청산·미실현손익은 mark price 기준으로 계산된다. 리스크 판단은
반드시 `mark_price` 를 사용한다 (`last_price` 아님). 손절·익절 트리거 기준도
`MARK_PRICE` 로 일관되게 둔다.

---

## 2. 전략 규칙 v0.1 — CCI·RSI·ADX 추세 추종

### 2.1 전략 성격

추세 추종(Trend-following). 강한 추세가 시작·강화될 때 그 방향에 올라탄다.
신호 빈도는 평균 회귀보다 낮다 (ADX 필터가 횡보장을 걸러내므로).

### 2.2 지표 역할

| 지표 | 역할 |
| --- | --- |
| ADX | 추세 강도 문지기. 추세가 약하면(횡보) 진입 금지 |
| CCI | 방향 + 진입 트리거. ±100선 돌파를 추세 발생으로 봄 |
| RSI | 방향 동의 확인 + 과열 브레이크 (꼭대기 진입 방지) |

### 2.3 판단 시점

15분봉 캔들이 **완전히 마감된 직후에만** 판단한다.
진행 중인 캔들의 값으로 판단하지 않는다.

### 2.4 상위 시간봉 필터 (1시간봉)

- 현재가가 1시간봉 EMA50 **위** → 롱만 허용
- 현재가가 1시간봉 EMA50 **아래** → 숏만 허용

### 2.5 롱 진입 — 아래 조건 전부 만족

1. 1시간봉 필터가 '롱 허용' 상태
2. 15분봉 ADX ≥ 25
3. 15분봉 CCI 가 +100 위로 상향 돌파 (이번 마감 캔들에서 막 넘음)
4. 15분봉 RSI > 50 그리고 RSI < 70
5. 보유 포지션 없음
6. 중복 진입 조건 아님 (아래 4절 참조)

→ 충족 시 시장가 롱 진입

### 2.6 숏 진입 — 롱의 거울 대칭

1. 1시간봉 필터가 '숏 허용' 상태
2. 15분봉 ADX ≥ 25
3. 15분봉 CCI 가 -100 아래로 하향 돌파
4. 15분봉 RSI < 50 그리고 RSI > 30
5. 보유 포지션 없음
6. 중복 진입 조건 아님

→ 충족 시 시장가 숏 진입

### 2.7 청산

**손절:** 진입가 ±1.0% (전략 문서 0.5~1.2% 범위의 중간값으로 고정)
→ **진입과 동시에 거래소 측 STOP_MARKET 주문으로 건다.** 봇/PC/네트워크가
다운돼도 거래소가 손절을 집행하므로 계좌가 보호된다. (9.3절 참조)

**익절:** 봇이 감시하며 실행한다. 아래 중 먼저 닿는 것
- CCI 가 0선을 반대 방향으로 돌파 (추세 힘 빠짐 신호)
- 부분익절: 수익이 손절폭의 1배 도달 시 30% 익절,
  2배 도달 시 추가 30% 익절, 나머지는 CCI 0선 복귀 시 전량

**손절·익절 분담 원칙:** 손절은 "크게 잃는 위험"이므로 거래소에 박아두어
봇 다운과 무관하게 작동시킨다. 익절은 "못 버는 위험"에 그치고, CCI 0선
복귀 같은 동적 조건이라 봇이 감시하며 실행한다. 봇이 익절을 놓쳐도 손실
하한은 거래소 손절선이 막는다.

**반대 신호 청산:** 롱 보유 중 숏 진입 조건 충족 시 롱 종료.
재진입은 다음 캔들까지 대기(보수적).

### 2.8 미확정 항목 (코드 작성/백테스트 단계에서 확정)

- "강한 추세"의 정확한 정의 (현재는 ADX ≥ 25로 단순화)
- 급등/급락 직후 추격 진입 차단의 수치 기준
- 시장가 vs 지정가 — 현재 시장가로 가정
- 부분익절 vs 전량익절 — 현재 부분익절로 가정

---

## 3. 리스크 관리 규칙 (확정)

| 항목 | 값 |
| --- | --- |
| 1회 거래 최대 손실 | 전체 시드의 0.5~1% |
| 포지션 크기 계산 | 허용 손실금액 ÷ 손절폭 |
| 일일 손실 한도 | 전체 시드의 3% 도달 시 그날 거래 전면 중단 |
| 연속 손절 한도 | 2회 연속 손절 시 거래 중단 |
| 청산 후 대기 | 최소 1개 15분봉 캔들 |
| 손절 원칙 | 손절가 고정. 이동·해제 금지. 물타기 금지 |

**포지션 크기 예시:** 시드 1,000 USDT, 1회 허용 손실 1% = 10 USDT,
손절폭 1% → 포지션 크기 = 10 ÷ 0.01 = 1,000 USDT.

**거래 금지 조건:** 손절가가 청산가와 너무 가까울 때 / 급등·급락 직후 /
펀딩비가 과도하게 불리할 때 / 변동성이 비정상적으로 클 때.

---

## 4. 중복 진입 방지

신호는 `candle_id` (예: `BTCUSDT-15m-2026-05-27T08:30:00Z`)를 키로 가진다.

1. 같은 캔들에서 중복 진입 금지
2. 포지션 보유 중 같은 방향 추가 진입 금지
3. 익절·손절 직후 즉시 재진입 금지
4. 청산 후 최소 1캔들 대기
5. 연속 손절 2회 시 거래 중단
6. 일일 누적 손실 3% 도달 시 그날 중단

---

## 5. 시스템 아키텍처 — 9개 모듈

```
시장데이터 ──MARKET_SNAPSHOT──▶ 검증 ──VALIDATION_RESULT──▶ 전략
                                                              │ SIGNAL (trace_id 생성)
                                                              ▼
   실행 ◀──ORDER_APPROVAL(서명토큰)── 리스크 ◀──POSITION_STATE── 동기화
     │ EXECUTION_RESULT                  ▲                       │
     └──────────────(피드백 루프)────────┘                       │
                                                                 ▼
  검증·동기화·킬스위치 ──HALT──▶ (전 모듈 정지)       모든 메시지 ──▶ 로그
```

| 모듈 | 목표 | API 권한 |
| --- | --- | --- |
| 시장데이터 | 정확·신선한 시세 수집 | 없음/조회 |
| 데이터 검증 | 잘못된 데이터로 주문 방지 | 없음 |
| 전략/신호 | 매수·매도·관망 신호 생성 | 없음 |
| 리스크 관리 | 계좌 생존 보호, 서명 승인토큰 발급 | 조회 |
| 주문 실행 | 승인된 주문만 안전 실행 | 거래 권한 |
| 포지션 동기화 | 거래소·봇 상태 일치 | 조회 |
| 모니터링/알림 | 문제 즉시 통보 | 없음 |
| 로그/감사 | 모든 의사결정 기록 | 없음 |
| 킬스위치 | 위험 시 봇 정지 | 독립 거래 권한 (실행 모듈과 완전 분리) |

### 핵심 안전 원칙

1. **킬스위치 독립** — 다른 8개 모듈이 전부 죽어도 혼자 동작. 독립 프로세스,
   독립 거래 키. 실행 모듈을 거쳐 정지하지 않는다.
2. **리스크 거부권의 구조적 강제** — 실행 모듈은 유효한 서명 승인토큰
   (짧은 TTL) 없이는 주문 자체를 생성하지 못한다.
3. **거래소 측 손절** — 손절은 진입과 동시에 거래소 STOP_MARKET 주문으로
   등록. 봇·PC·네트워크 다운 시에도 손실 하한이 보장된다. (7.3절 참조)
4. **피드백 루프** — 체결 결과는 리스크 모듈로 돌아가 노출도·일일손익을 갱신.
   포지션 동기화 불일치는 즉시 HALT 트리거.
5. **멱등성** — 모든 주문에 client order ID 부여, 재시도 시 중복 주문 방지.
6. **부팅 시퀀스** — 재시작 시 포지션 동기화가 가장 먼저 실행, 동기화 완료
   전 모든 거래 금지. 거래소가 상태의 원본이다.
7. **데드맨 스위치** — 데이터 하트비트가 끊기면 킬스위치 발동.

---

## 6. 공통 데이터 스키마 v1.0

### 6.1 공통 봉투 (모든 메시지)

```json
{
  "schema_version": "1.0",
  "message_id": "uuid",
  "message_type": "MARKET_SNAPSHOT | VALIDATION_RESULT | SIGNAL | ORDER_APPROVAL | EXECUTION_RESULT | POSITION_STATE | SYSTEM_STATE | HALT | LOG_ENTRY",
  "source_module": "market_data | validation | strategy | risk | execution | sync | monitoring | log | killswitch",
  "created_at": "2026-05-27T08:30:00.123Z",
  "trace_id": "uuid | null",
  "payload": { }
}
```

- 모든 시각은 UTC, 밀리초 단위.
- 가격·수량은 float 아닌 **문자열 십진수** (`"0.1"`). 부동소수점 오차 방지.
- `trace_id` 는 전략 모듈이 SIGNAL 생성 시 발급, 이후 메시지가 물려받음.
- 시장데이터·검증은 연속 스트림이라 `trace_id` 는 null.

### 6.2 MARKET_SNAPSHOT (시장데이터 → 검증)

```json
{
  "symbol": "BTCUSDT",
  "exchange": "binance_usdm_futures",
  "candles": {
    "15m": { "open":"...", "high":"...", "low":"...", "close":"...", "volume":"...", "is_closed": true },
    "1h":  { "open":"...", "high":"...", "low":"...", "close":"...", "volume":"...", "is_closed": false }
  },
  "exchange_time": "2026-05-27T08:30:00.000Z",
  "received_at":   "2026-05-27T08:30:00.087Z",
  "mark_price": "67295.5",
  "last_price": "67295.5",
  "funding_rate": "0.00010",
  "next_funding_time": "2026-05-27T16:00:00.000Z"
}
```

`is_closed` 가 true 인 캔들로만 전략이 판단한다.
`exchange_time` 과 `received_at` 차이로 데이터 지연을 측정한다.

### 6.3 VALIDATION_RESULT (검증 → 전략)

```json
{
  "verdict": "PASS | REJECT",
  "validated_snapshot": { },
  "checks": { "no_missing_candle": true, "no_spike": true, "not_stale": true, "no_duplicate": true },
  "reject_reasons": [],
  "data_age_ms": 87
}
```

데이터가 심하게 stale 하면 이와 별개로 HALT 메시지를 발행한다.

### 6.4 SIGNAL (전략 → 리스크)

```json
{
  "signal_id": "uuid",
  "symbol": "BTCUSDT",
  "candle_id": "BTCUSDT-15m-2026-05-27T08:30:00Z",
  "primary_signal": "LONG_ENTRY | SHORT_ENTRY | TAKE_PROFIT | NONE",
  "htf_filter_passed": true,
  "indicators": { "adx": "27.3", "cci": "112.5", "rsi": "58.1" },
  "entry_blocked_reason": null,
  "stop_loss_price": "66600.0",
  "reasoning": "15분봉 CCI +100 상향돌파 + ADX 27 + RSI 58, 1시간봉 EMA50 위"
}
```

`candle_id` 는 중복 진입 방지의 키. 여기서 `trace_id` 가 처음 생성된다.

### 6.5 ORDER_APPROVAL (리스크 → 실행, 서명 승인토큰)

```json
{
  "approval_token": {
    "token_id": "uuid",
    "signal_id": "uuid",
    "verdict": "APPROVED | REDUCED | REJECTED",
    "issued_at": "2026-05-27T08:30:01.000Z",
    "expires_at": "2026-05-27T08:30:04.000Z",
    "signature": "hmac-sha256(...)"
  },
  "approved_order": {
    "symbol": "BTCUSDT",
    "side": "BUY | SELL",
    "order_type": "MARKET",
    "quantity": "0.05",
    "leverage": "3",
    "stop_loss": "66600.0",
    "take_profit_plan": [
      { "level": 1, "trigger": "1R", "close_ratio": "0.3" },
      { "level": 2, "trigger": "2R", "close_ratio": "0.3" },
      { "level": 3, "trigger": "CCI_ZERO_CROSS", "close_ratio": "remaining" }
    ],
    "reduce_only": false,
    "position_side": "BOTH",
    "working_type": "MARK_PRICE"
  },
  "reject_reasons": []
}
```

- 실행 모듈의 첫 동작: ① 서명 검증 ② `expires_at` 확인. 하나라도 실패 시 주문 거부.
- 토큰 TTL: 시장가 3초 / 지정가 5초 / 급변동 시 1.5초.
- 서명 비밀키는 바이낸스 API 키와 별개. 리스크·실행 모듈만 보유.
- "1R" = 손절폭의 1배 수익.

### 6.6 EXECUTION_RESULT (실행 → 리스크·동기화·로그)

```json
{
  "client_order_id": "uuid",
  "token_id": "uuid",
  "exchange_order_id": "binance-887766",
  "status": "FILLED | PARTIALLY_FILLED | REJECTED | TIMEOUT | CANCELED",
  "filled_quantity": "0.05",
  "remaining_quantity": "0",
  "avg_fill_price": "67298.0",
  "fee": "0.201",
  "exchange_time": "2026-05-27T08:30:01.350Z",
  "error": null
}
```

### 6.7 POSITION_STATE (동기화 → 전략·리스크)

```json
{
  "balance": "10240.55",
  "positions": [
    { "symbol": "BTCUSDT", "side": "LONG", "size": "0.05",
      "entry_price": "67298.0", "unrealized_pnl": "-12.5",
      "leverage": "3", "liquidation_price": "61200.0", "margin_mode": "ISOLATED" }
  ],
  "open_orders": [],
  "consistency": "MATCH | MISMATCH",
  "mismatch_detail": null
}
```

### 6.8 SYSTEM_STATE (봇 상태 기억)

```json
{
  "trading_enabled": true,
  "halt_reason": null,
  "last_exit": { "candle_id": "BTCUSDT-15m-2026-05-27T08:15:00Z", "type": "STOP_LOSS | TAKE_PROFIT" },
  "consecutive_stop_losses": 1,
  "daily_pnl_pct": "-1.8",
  "daily_reset_at": "2026-05-28T00:00:00Z",
  "trades_today": 7
}
```

### 6.9 HALT (검증·동기화·킬스위치 누구나 발행)

```json
{
  "severity": "WARNING | CRITICAL",
  "trigger": "STALE_DATA | POSITION_MISMATCH | DAILY_LOSS_LIMIT | API_ERROR | HEARTBEAT_LOST | PRICE_SPIKE | CONSECUTIVE_STOP_LOSS",
  "detail": "데이터 하트비트 4초간 끊김",
  "requires_manual_recovery": true,
  "action_taken": "TRADING_SUSPENDED | POSITIONS_CLOSED | NONE"
}
```

### 6.10 LOG_ENTRY (로그/감사)

```json
{
  "event_type": "SIGNAL | APPROVAL | ORDER | FILL | ERROR | PNL | HALT",
  "trace_id": "uuid",
  "level": "INFO | WARN | ERROR",
  "data": { },
  "logged_at": "2026-05-27T08:30:01.400Z"
}
```

### 6.11 공통 enum

```
direction    : LONG | SHORT | FLAT
side         : BUY | SELL
order_type   : MARKET | LIMIT
order_status : FILLED | PARTIALLY_FILLED | REJECTED | TIMEOUT | CANCELED
verdict(검증)  : PASS | REJECT
verdict(리스크) : APPROVED | REDUCED | REJECTED
severity     : WARNING | CRITICAL
consistency  : MATCH | MISMATCH
```

---

## 7. 기술 구현 결정

### 7.1 구현 형태

- **메인 봇** — 단일 프로세스. 9개 중 8개 모듈을 그 안에서 클래스/패키지로
  분리한다. BTC 단일 심볼이라 분산 아키텍처는 과하다.
- **킬스위치** — 별도 독립 프로세스. 메인 봇이 통째로 죽어도 살아남아
  포지션을 청산할 수 있어야 한다. 자체 거래 키 보유.
- 언어: Python.

### 7.2 데이터 수급

- 바이낸스 **WebSocket** 스트림 사용 (무료, 비용 0원).
  캔들 마감 시점 정확도와 mark price 실시간 추적을 위해 REST 폴링보다 우수.
- WebSocket 연결 끊김 감지·자동 재연결 필수. 재연결 동안은 데이터 stale로
  간주하고, 일정 시간 이상 끊기면 HALT.
- 부팅 시 및 주기적으로 REST `exchangeInfo` 호출로 거래 규격 갱신.

### 7.3 운영 환경과 다운 대비 (개인 PC 24시간 가동)

봇은 개인 PC에서 24시간 가동을 전제로 한다. PC는 항상 켜둔다.
그러나 네트워크 단절·정전·OS 재부팅·절전 진입 등으로 봇이 다운될 수
있다는 전제로 설계한다.

**핵심 안전장치 — 손절은 거래소에 건다:**
포지션 진입과 동시에 손절(STOP_MARKET) 주문을 거래소 측에 등록한다.
봇·PC·네트워크가 모두 다운돼도 거래소가 손절을 집행하므로, 봇이 죽은 채
가격이 급변해도 손실 하한이 보장된다. 이것은 개인 PC 운영의 필수 조건이다.

**익절은 봇이 감시한다:**
익절 조건(CCI 0선 복귀, 부분익절 1R·2R)은 동적이라 거래소 주문으로 미리
걸기 어렵다. 봇이 감시하며 실행한다. 봇 다운으로 익절을 놓쳐도 최악은
"수익 감소 또는 본전"이며, 손실은 거래소 손절선이 막는다.

**부팅 복구:** 재시작 시 포지션 동기화 모듈이 거래소에서 실제 상태를
가져와 봇 상태를 복원한다. 거래소가 상태의 원본(source of truth)이다.

**향후 검토(보류):** 24시간 무중단이 더 필요해지면 무료 클라우드 VM
(예: 오라클 클라우드 평생 무료 티어)으로 이전을 검토한다. 실거래 단계에서
재논의. 현 단계에서는 개인 PC + 거래소 측 손절로 진행한다.

### 7.4 상태 저장

- 봇 상태(`SYSTEM_STATE`, 포지션 장부 등)는 **JSON 파일**로 저장한다.
  BTC 단일 심볼·단일 프로세스라 파일로 충분하다.
- **원자적 쓰기(atomic write)** 패턴 필수: 임시 파일에 먼저 쓰고 완료 후
  `rename`으로 교체. 쓰기 도중 크래시해도 파일이 깨지지 않는다.
- 로그는 별도 append-only 파일에 기록.
- 파일은 보조 기억일 뿐, 상태의 원본은 거래소다 (7.3절 부팅 복구 참조).

### 7.5 백테스트·실거래 코드 공유 구조

전략 로직은 **순수 함수**로 구현한다.

```
입력: 캔들 데이터 + 지표값 + 현재 포지션 상태
출력: 신호 (LONG_ENTRY | SHORT_ENTRY | TAKE_PROFIT | NONE)
```

이 함수는 자신이 백테스트에서 호출되는지 실거래에서 호출되는지 모른다.

- **백테스트 엔진** — 과거 CSV를 한 캔들씩 이 함수에 입력
- **라이브 엔진** — WebSocket 실시간 데이터를 이 함수에 입력

둘이 동일한 전략 함수를 공유하므로, 백테스트에서 검증한 로직과 실거래
로직이 100% 일치한다. "백테스트는 됐는데 실거래는 다르다"를 원천 차단.

---

## 8. 다음 단계

1. **백테스트** — 전략 규칙 v0.1을 과거 BTC 데이터로 검증 (별도 명세서 참조)
2. 백테스트 통과 시 전략 v1.0 확정
3. 9개 모듈 봇 개발 (Claude Code)
4. 바이낸스 테스트넷 가동 테스트
5. 아주 작은 금액 실거래 → 점진적 확대

**경고:** 본 설계서의 수치는 검증 전 가설이다. 백테스트와 테스트넷 검증 없이
실거래에 투입해서는 안 된다. 자동매매는 설계가 완전해도 시장 리스크로 손실이
발생할 수 있다.
