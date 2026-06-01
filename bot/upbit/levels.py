"""현물용 TP/SL 계산 + 시그널 포맷."""

from __future__ import annotations

TP_FRACTIONS: tuple[float, float, float] = (0.40, 0.30, 0.30)


def calculate_volume_spike_levels(entry: float, ratio: float) -> dict:
    """거래량 spike 강도에 따라 TP/SL 산출. 현물 = LONG only."""
    if ratio >= 10.0:
        tp1p, tp2p, tp3p, slp = 0.015, 0.030, 0.060, 0.020
        tier = "extreme"
    elif ratio >= 5.0:
        tp1p, tp2p, tp3p, slp = 0.010, 0.020, 0.040, 0.015
        tier = "strong"
    else:  # 3 ~ 5x
        tp1p, tp2p, tp3p, slp = 0.008, 0.015, 0.025, 0.012
        tier = "moderate"

    return {
        "tp1": entry * (1 + tp1p),
        "tp2": entry * (1 + tp2p),
        "tp3": entry * (1 + tp3p),
        "sl": entry * (1 - slp),
        "tp1_pct": tp1p, "tp2_pct": tp2p, "tp3_pct": tp3p, "sl_pct": slp,
        "tier": tier,
    }


def _fmt_krw(p: float) -> str:
    if p >= 1000:
        return f"₩{p:,.0f}"
    if p >= 1:
        return f"₩{p:,.2f}"
    if p >= 0.01:
        return f"₩{p:.4f}"
    return f"₩{p:.6f}"


def _fmt_vol_krw(v: float) -> str:
    if v >= 1_000_000_000:
        return f"{v/1e9:.2f}B원"
    if v >= 100_000_000:
        return f"{v/1e8:.1f}억원"
    if v >= 1_000_000:
        return f"{v/1e6:.0f}M원"
    return f"{v/1e3:.0f}k원"


_UPBIT_TIER_KR = {
    "extreme": "🔥 매우 강함 (×10배 이상)",
    "strong": "강함 (×5~10배)",
    "moderate": "보통 (×3~5배)",
}


def _pct_5min_label(pct_5min: float) -> tuple[str, str]:
    """5분 변동 → (이모지, 라벨)"""
    if pct_5min >= 0.5:
        return "📈", "상승 추세"
    if pct_5min >= 0.0:
        return "➡", "평탄 (약상승)"
    if pct_5min >= -0.5:
        return "➡", "평탄 (약하락, 폭락 아님)"
    # < -0.5% 는 필터에서 차단됐어야 함. 방어용
    return "📉", "하락 중"


def format_signal(
    market: str,
    entry: float,
    levels: dict,
    ratio: float,
    cur_vol_krw: float,
    avg_vol_krw: float,
    pct_5min: float = 0.0,
) -> str:
    """텔레그램 시그널 메시지 — 초보자 친화."""
    sym_short = market.replace("KRW-", "")
    tier_kr = _UPBIT_TIER_KR.get(levels["tier"], levels["tier"])
    pct5_emoji, pct5_label = _pct_5min_label(pct_5min)
    return (
        f"🟢 *#{sym_short}/KRW* — 업비트 현물 *매수* 신호\n"
        f"━━━━━━━━━━━━\n"
        f"💵 *진입가*: `{_fmt_krw(entry)}`\n"
        f"🔢 레버리지: `1배` (현물 거래)\n\n"
        f"🎯 *1차 익절*: `{_fmt_krw(levels['tp1'])}` "
        f"(`+{levels['tp1_pct']*100:.2f}%`) — 40% 매도\n"
        f"🎯 *2차 익절*: `{_fmt_krw(levels['tp2'])}` "
        f"(`+{levels['tp2_pct']*100:.2f}%`) — 30% 매도\n"
        f"🎯 *3차 익절*: `{_fmt_krw(levels['tp3'])}` "
        f"(`+{levels['tp3_pct']*100:.2f}%`) — 나머지 30% 매도\n\n"
        f"🛑 *손절*: `{_fmt_krw(levels['sl'])}` "
        f"(`-{levels['sl_pct']*100:.2f}%`)\n"
        f"━━━━━━━━━━━━\n"
        f"📊 거래량 폭발: `×{ratio:.1f}` ({tier_kr})\n"
        f"   👉 1분 거래량이 직전 20분 평균보다 `×{ratio:.1f}`\n"
        f"{pct5_emoji} 5분 변동: `{pct_5min:+.2f}%` ({pct5_label})\n"
        f"   👉 강한 양봉 + 5분 폭락 아님 = 매수세 들어옴\n"
        f"   _(5분 -0.5% 이상 하락은 자동 차단)_\n"
        f"⏰ *2시간* 안에 결판 (TP/SL 안 닿으면 자동 정리)\n"
    )
