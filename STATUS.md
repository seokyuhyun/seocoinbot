# 작업 핸드오프 — 백테스트 진행 상황

> 회사·집 PC 전환 시 컨텍스트 복구용. Claude Code 새 세션이 이 파일을 먼저
> 읽고 시작하면 진행 상태가 그대로 복원된다. 진행이 한 단계 끝날 때마다
> 이 파일을 갱신한다.

**마지막 갱신:** 2026-05-27
**현재 단계:** 백테스트 — OOS 단일 검증 대기

---

## TL;DR

전략 v0.1 (CCI/ADX 추세추종)을 15분봉으로 검증했더니 IS·OOS 모두 -33% / -19% 손실.
파라미터 그리드 서치(48조합)·전략 구조 변형(7시나리오) 모두 IS 양수 실패.

**타임프레임을 1시간봉으로 옮긴 게 결정적**이었음. 비용 비중이 1/4로 줄고
추세 길이가 늘면서, **1h main + 4h HTF + CCI ±130 + 트레일링 ATR×2.0** 조합이
IS에서 **+6.74% / MDD -8.92% / PF 1.29** 로 처음으로 양수 양호 결과를 냄.

**다음 단계:** OOS 검증 1회 (명세서 §6.2 — 단 한 번 규칙). 후보 조합은
[backtest/verify_oos.py](backtest/verify_oos.py) 에 박혀 있음. OOS 가 IS 와
비슷하면 → 전략 v1.0 후보 확정. 무너지면 → 과최적화·재검토.

---

## 바로 다음 명령

```powershell
# venv 활성화된 상태에서
python backtest/verify_oos.py
```

이거 한 번만 실행. 결과 보고 다음 분기:
- IS 와 비슷하게 양수 (대략 +2% 이상, PF ≥ 1.1, MDD ≤ 15%) → 전략 v1.0 후보
- IS 와 비슷하게 양수지만 약함 → "edge 는 있는데 약함. 9개 모듈 봇 개발 진입할지 결정"
- 음수 → 과최적화. 전략 메커니즘 재고 (평균회귀로 갈아엎기 검토)

---

## 시도해본 것 정리 (커밋 메시지에 상세, 여기는 요약)

| # | 시도 | IS 결과 | 결론 |
|---|------|--------|------|
| 1 | v0.1 baseline (15m, crossover, partial_tp) | -33.7% / PF 0.85 | 메커니즘 자체 적자 |
| 2 | 15m 그리드 서치 48조합 | 모두 음수, best -16.6% | 15m 에선 어떤 파라미터도 양수 불가 |
| 3 | extension entry (CCI 임계 위 지속+가속) | -43.2% | **기각.** "꼭대기에서 진입" 가설 틀림. 추세가 그렇게 길게 안 감 |
| 4 | trailing only (15m, ATR×2) | -28.9% / PF 0.90 | 의미 있는 개선이지만 15m 비용 못 이김 |
| 5 | 1h main + 4h HTF baseline | -6.2% / PF 0.99 | **돌파.** TF 한 칸이 결정타 |
| 6 | 1h + CCI ±130 + trailing ATR×2 | **+6.74% / PF 1.29** | **현재 후보.** OOS 대기 |

상세 수치·코드는 다음 커밋:
- `3a924e7` — strategy v0.1 + engine
- `ac249f0` — 15m 48조합 그리드 서치
- `2b363e9` — extension + trailing 시도 (기각)
- `b688dcd` — 타임프레임 일반화 + 1h 결과
- `ce961f5` — OOS 검증 스크립트 (locked-in 후보)

---

## 중요 규칙·제약

### OOS는 단 한 번 (명세 §6.2)
[backtest/verify_oos.py](backtest/verify_oos.py) 를 한 번 돌린 뒤 결과를 보고
파라미터를 또 만지면 OOS 가 사실상 IS 가 됨. 결정해야 할 것:

- IS 결과 보고 만족 → 그대로 v1.0 후보
- IS 결과 불만족 → **이 후보는 폐기**하고 다른 메커니즘으로 다시 시작 (OOS 데이터는 더 이상 안 만짐 — 새 후보는 같은 IS 에서 다시 탐색)

### 운영·코드 규칙
- 파이썬 표준 출력 UTF-8 강제 — 모든 백테스트·다운로드 스크립트 상단에서
  `sys.stdout.reconfigure(encoding="utf-8")` 처리됨. 그래도 PowerShell 에서
  깨지면 `$env:PYTHONUTF8=1`.
- 손절은 거래소 측 STOP_MARKET 시뮬레이션. 인트라바 H/L 가 손절가를
  관통하면 손절. SL 과 TP 가 같은 캔들이면 SL 우선 (명세 §4 보수).
- 신호는 캔들 close 에서 생성, 진입은 다음 캔들 open. look-ahead 차단.
- 트레일링 모드에서 손절은 **한 방향으로만** 이동 (롱이면 위로만). 설계서 §3
  "손절 이동·해제 금지" 원칙의 안전한 해석.

---

## 회사·집 환경 차이로 인한 주의

- **데이터 파일** (`data/BTCUSDT-*.csv`) 은 `.gitignore` 처리됨. 각 PC 에서
  `python scripts/download_binance_data.py` 로 직접 받아야 함. 같은 코드·기간
  이라 결과는 비트 단위 동일.
- **백테스트 결과** (`data/backtest_results/*.csv`) 도 gitignored. 재실행으로
  재생성. IS 결과는 결정론적이라 동일.
- **`.env`** 는 `.env.example` 복사. 백테스트만 할 거면 키 빈칸 OK.
  `RUN_MODE=testnet` 확인.
- **`.venv`** 는 PC 마다 새로. `python -m venv .venv` + `pip install -r requirements.txt`.
- **git config** (user.name / user.email) 은 `--local` 로 이 레포에만 설정.
  PC 마다 한 번씩 설정 필요.
- **PowerShell ExecutionPolicy** — `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`
  를 PC 마다 한 번. (안 그러면 `Activate.ps1` 실행 안 됨)

---

## 새 Claude Code 세션 시작 시 권장 첫 문장

```
seocoinbot 백테스트 작업 이어가는 거야. STATUS.md 와 git log -8 읽어보고
현재 상태 파악한 다음, 다음 단계 진행해줘.
```

이걸로 컨텍스트 복원됨. STATUS.md 의 "바로 다음 명령" 섹션에 정확한 명령
박혀 있음.

---

## 아직 안 한 일 (참고용)

- OOS 검증 (다음 단계)
- 전략 v1.0 확정 (OOS 통과 시)
- 9개 모듈 봇 개발 (설계서 §5) — 백테스트 통과 후
- 바이낸스 테스트넷 가동 — 봇 완성 후
- 실거래 (아주 작은 금액) — 테스트넷 검증 후

설계서 §8 의 흐름 그대로.
