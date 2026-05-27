#!/usr/bin/env python3
"""
바이낸스 공개 데이터 다운로더 — BTC 선물 백테스트용

data.binance.vision 에서 BTCUSDT USDT-M 선물의 월별 klines(캔들) 데이터를
받아서, CHECKSUM 검증 후 압축을 풀고, 하나의 CSV로 병합한다.

- 표준 라이브러리만 사용 (의존성 0). `python download_binance_data.py` 로 실행.
- 백테스트 명세서(02_backtest_spec.md) 2장의 데이터 소스 단계에 해당.

산출물: ./data/BTCUSDT-15m.csv , ./data/BTCUSDT-1h.csv
"""

import csv
import hashlib
import io
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 설정 — 필요하면 여기만 바꾸면 된다
# ─────────────────────────────────────────────────────────────
SYMBOL = "BTCUSDT"
INTERVALS = ["15m", "1h"]          # 메인 + 상위 필터 타임프레임
MARKET_PATH = "futures/um"         # USDT-M 선물. 현물(spot) 아님 — 절대 바꾸지 말 것
OUTPUT_DIR = Path("./data")        # 산출물 저장 위치

# 다운로드 기간 (포함). 기본 = 약 2년치.
# 월별 파일은 그 달이 끝나야 생성되므로, 종료월은 '지난달' 이전으로 둔다.
START = (2024, 5)                  # (년, 월)
END = (2026, 4)                    # (년, 월)

BASE_URL = "https://data.binance.vision/data"
# ─────────────────────────────────────────────────────────────

# 바이낸스 futures klines CSV 컬럼 (12개)
COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


def month_range(start, end):
    """(년,월) 시작~끝을 포함하는 (년,월) 리스트를 만든다."""
    months = []
    y, m = start
    while (y, m) <= end:
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def build_url(interval, year, month, checksum=False):
    """월별 zip(또는 .CHECKSUM) 파일의 다운로드 URL을 만든다."""
    fname = f"{SYMBOL}-{interval}-{year:04d}-{month:02d}.zip"
    if checksum:
        fname += ".CHECKSUM"
    return f"{BASE_URL}/{MARKET_PATH}/monthly/klines/{SYMBOL}/{interval}/{fname}"


def fetch(url):
    """URL을 받아 bytes로 반환. 404 등은 None을 반환(해당 월 파일 없음)."""
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except urllib.error.URLError as e:
        print(f"  [네트워크 오류] {url}\n    {e}", file=sys.stderr)
        return None


def verify_checksum(zip_bytes, checksum_bytes):
    """다운로드한 zip의 sha256이 .CHECKSUM 파일과 일치하는지 확인."""
    if checksum_bytes is None:
        return None  # CHECKSUM 파일이 없으면 검증 건너뜀
    expected = checksum_bytes.decode().split()[0].strip().lower()
    actual = hashlib.sha256(zip_bytes).hexdigest().lower()
    return expected == actual


def parse_zip(zip_bytes):
    """zip 안의 CSV를 읽어 행 리스트로 반환. 헤더 행이 있으면 자동으로 건너뛴다."""
    rows = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_name = zf.namelist()[0]
        with zf.open(csv_name) as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8"))
            for i, row in enumerate(reader):
                if not row:
                    continue
                # 바이낸스는 2025년부터 일부 파일에 헤더 행을 넣는다.
                # 첫 칸이 숫자가 아니면 헤더로 보고 건너뛴다.
                if i == 0 and not row[0].strip().replace(".", "").isdigit():
                    continue
                rows.append(row[:len(COLUMNS)])
    return rows


def download_interval(interval, months):
    """한 인터벌의 전체 월을 받아 병합·정렬·검증한 행 리스트를 반환."""
    print(f"\n=== {SYMBOL} {interval} — {len(months)}개월 다운로드 ===")
    all_rows = []
    missing, bad_checksum = [], []

    for (y, m) in months:
        tag = f"{y:04d}-{m:02d}"
        zip_bytes = fetch(build_url(interval, y, m))
        if zip_bytes is None:
            print(f"  {tag}  없음 (건너뜀)")
            missing.append(tag)
            continue

        checksum_bytes = fetch(build_url(interval, y, m, checksum=True))
        ok = verify_checksum(zip_bytes, checksum_bytes)
        if ok is False:
            print(f"  {tag}  CHECKSUM 불일치! (제외)")
            bad_checksum.append(tag)
            continue

        rows = parse_zip(zip_bytes)
        all_rows.extend(rows)
        mark = "검증OK" if ok else "검증생략"
        print(f"  {tag}  {len(rows):>6}개 캔들  [{mark}]")

    # open_time 기준 정렬 + 중복 제거
    seen = set()
    deduped = []
    for row in sorted(all_rows, key=lambda r: int(r[0])):
        key = row[0]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    if missing:
        print(f"  ! 누락된 달: {', '.join(missing)}")
    if bad_checksum:
        print(f"  ! CHECKSUM 실패: {', '.join(bad_checksum)}")
    return deduped


def check_gaps(rows, interval):
    """캔들 사이 시간 간격을 검사해 누락 구간을 보고한다."""
    step_ms = {"15m": 15 * 60_000, "1h": 60 * 60_000}.get(interval)
    if step_ms is None or len(rows) < 2:
        return
    gaps = 0
    for prev, cur in zip(rows, rows[1:]):
        diff = int(cur[0]) - int(prev[0])
        if diff != step_ms:
            gaps += 1
            if gaps <= 5:  # 처음 5개만 자세히 출력
                t = date.fromtimestamp(int(prev[0]) / 1000)
                print(f"  [간격 이상] {t} 부근, 간격 {diff/step_ms:.1f}캔들")
    if gaps:
        print(f"  ! 시간 간격 이상 {gaps}건 — 백테스트 검증 모듈에서 처리 필요")
    else:
        print(f"  시간 간격 정상 (누락 없음)")


def write_csv(rows, path):
    """병합 결과를 헤더 포함 CSV로 저장."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    print(f"  저장: {path}  ({len(rows)}개 캔들)")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    months = month_range(START, END)
    print(f"기간: {START[0]}-{START[1]:02d} ~ {END[0]}-{END[1]:02d}  "
          f"({len(months)}개월)")

    for interval in INTERVALS:
        rows = download_interval(interval, months)
        if not rows:
            print(f"  ! {interval}: 받은 데이터 없음. 기간/경로 확인 필요.")
            continue
        check_gaps(rows, interval)
        write_csv(rows, OUTPUT_DIR / f"{SYMBOL}-{interval}.csv")

    print("\n완료. 산출물은 ./data/ 에 있다.")
    print("open_time/close_time 은 밀리초 유닉스 타임스탬프다.")


if __name__ == "__main__":
    main()
