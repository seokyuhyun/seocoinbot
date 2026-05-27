# 작업 핸드오프 — 백테스트 진행 상황

> 회사·집 PC 전환 시 컨텍스트 복구용. Claude Code 새 세션이 이 파일을 먼저
> 읽고 시작하면 진행 상태가 그대로 복원된다. 진행이 한 단계 끝날 때마다
> 이 파일을 갱신한다.

**마지막 갱신:** 2026-05-28
**현재 단계:** v0.1 OOS 실패 → 신규 전략군(유튜브 4종) IS 탐색 진입

---

## TL;DR

**v0.1 후보 OOS 검증 결과: 실패 (과최적화 확인).**

| 지표 | IS | OOS | 변화 |
|---|---|---|---|
| 수익률 | +6.74% | **-9.99%** | 반전 |
| PF | 1.29 | **0.48** | 1/3 |
| 승률 | 34.3% | 27.6% | -6.7%p |
| MDD | -8.92% | -10.97% | 비슷 |
| 거래수 | 67 | 29 | 절반 |

명세 §6.2 분기상 **이 후보(CCI/ADX 추세추종)는 폐기**. OOS 데이터(2025-09-24 ~
2026-04-30)는 다시 만지지 않는다. 새 후보는 같은 IS 에서 탐색.

**다음 방향:** 추세추종 메커니즘 자체가 BTC 1h 에 안 맞는다는 잠정 결론.
유튜브 자료에서 정리된 4전략을 IS 에서 비교 평가:

1. **WM** — MACD 히스토그램 약화 + 종가 10MA 돌파 (추세+모멘텀)
2. **더블 바텀 (TRIX)** — 두 저점 + TRIX < 0 (패턴/평균회귀)
3. **더블 탑 (RSI 다이버전스)** — 고점 갱신 + RSI 다이버전스 (패턴/평균회귀)
4. **SRT** — Stoch K↗D + RSI 본선↗시그널 + TRIX 위치 (모멘텀 반전)

(원본 5번째 "하이타이트 플래그" 는 알트코인 전용이라 BTC 데이터로 불가, 제외)

---

## 바로 다음 명령

```powershell
# venv 활성화된 상태에서 — 4전략 IS 비교
python backtest/compare_yt.py
```

(아직 미작성. 다음 세션에서 strategy/ 와 backtest/compare_yt.py 새로 만들고
바로 위 명령으로 비교 결과 보면 됨.)

---

## v0.1 시도 정리 (참고용, 폐기됨)

| # | 시도 | IS 결과 | 결론 |
|---|------|--------|------|
| 1 | v0.1 baseline (15m, crossover, partial_tp) | -33.7% / PF 0.85 | 메커니즘 자체 적자 |
| 2 | 15m 그리드 서치 48조합 | 모두 음수, best -16.6% | 15m 에선 어떤 파라미터도 양수 불가 |
| 3 | extension entry (CCI 임계 위 지속+가속) | -43.2% | 기각. "꼭대기에서 진입" 가설 틀림 |
| 4 | trailing only (15m, ATR×2) | -28.9% / PF 0.90 | 15m 비용 못 이김 |
| 5 | 1h main + 4h HTF baseline | -6.2% / PF 0.99 | 돌파. TF 한 칸이 결정타 |
| 6 | 1h + CCI ±130 + trailing ATR×2 (**최종 후보**) | **+6.74% / PF 1.29** (IS) | **OOS 에서 -9.99% / PF 0.48 → 폐기** |

상세 수치·코드는 다음 커밋:
- `3a924e7` — strategy v0.1 + engine
- `ac249f0` — 15m 48조합 그리드 서치
- `2b363e9` — extension + trailing 시도 (기각)
- `b688dcd` — 타임프레임 일반화 + 1h 결과
- `ce961f5` — OOS 검증 스크립트 (locked-in 후보)
- `ba20064` — STATUS.md 핸드오프 문서
- (이번 커밋) — OOS 실패 기록 + 신규 4전략 방향

---

## 중요 규칙·제약

### OOS는 단 한 번 (명세 §6.2)
[backtest/verify_oos.py](backtest/verify_oos.py) 는 **이미 1회 사용됨**
(2026-05-28, 결과 -9.99%). 더 이상 같은 OOS 구간을 만지면 통계적 의미 없음.

새 후보(유튜브 4전략 중 IS 통과한 것)를 OOS 검증할 때, 같은 split (70:30)
구간으로 평가하되 **단 한 번만**. 절대 파라미터 재조정 금지.

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
- **Python 자체 설치 — 회사 PC LTSC 정책 주의:** Windows 10 Enterprise LTSC
  에서는 python.org MSI installer 가 그룹정책에 막혀 0x80070003 으로 실패함
  (Package Cache 에 core.msi 가 안 캐싱됨). 우회: **`uv`** standalone exe 로
  설치.
  ```powershell
  # 1) uv 받기 (단일 exe, 설치 불필요)
  curl -sSL -o $env:TEMP\uv.zip https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip
  Expand-Archive $env:TEMP\uv.zip C:\Users\$env:USERNAME\tools\uv
  # 2) standalone Python 3.12 추출 (MSI 안 씀)
  C:\Users\$env:USERNAME\tools\uv\uv.exe python install 3.12
  # 3) venv 생성 (위에서 받은 python 사용)
  & "$env:APPDATA\uv\python\cpython-3.12.13-windows-x86_64-none\python.exe" -m venv .venv
  ```
- **git config** (user.name / user.email) 은 글로벌로 이미 설정돼 있음
  (seokh / kyuhyun.seo@kweather.co.kr). 새 PC 면 다시 설정 필요.
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

## 아직 안 한 일

- 유튜브 4전략 IS 비교 (다음 단계 — 코드 작성부터)
- OOS 재검증 (4전략 중 IS 통과한 후보가 있을 때, 단 한 번)
- 전략 v1.0 확정 (OOS 통과 시)
- 9개 모듈 봇 개발 (설계서 §5) — 백테스트 통과 후
- 바이낸스 테스트넷 가동 — 봇 완성 후
- 실거래 (아주 작은 금액) — 테스트넷 검증 후

설계서 §8 의 흐름 그대로.
