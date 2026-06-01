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


def format_signal(
    market: str,
    entry: float,
    levels: dict,
    ratio: float,
    cur_vol_krw: float,
    avg_vol_krw: float,
) -> str:
    """텔레그램 시그널 메시지."""
    sym_short = market.replace("KRW-", "")
    return (
        f"🟢 *Coin: #{sym_short}/KRW* (Upbit)\n"
        f"*LONG* · 거래량 spike (현물)\n"
        f"━━━━━━━━━━━━\n"
        f"Entry: `{_fmt_krw(entry)}`\n"
        f"Leverage: `1x` (현물)\n\n"
        f"Target 1: `{_fmt_krw(levels['tp1'])}` "
        f"(+{levels['tp1_pct']*100:.2f}%)  · 40% 익절\n"
        f"Target 2: `{_fmt_krw(levels['tp2'])}` "
        f"(+{levels['tp2_pct']*100:.2f}%)  · 30% 익절\n"
        f"Target 3: `{_fmt_krw(levels['tp3'])}` "
        f"(+{levels['tp3_pct']*100:.2f}%)  · 30% 익절\n\n"
        f"StopLoss: `{_fmt_krw(levels['sl'])}` "
        f"(-{levels['sl_pct']*100:.2f}%)\n"
        f"━━━━━━━━━━━━\n"
        f"1분 거래량: `{_fmt_vol_krw(cur_vol_krw)}`\n"
        f"평균 (20분): `{_fmt_vol_krw(avg_vol_krw)}`\n"
        f"배수: `×{ratio:.1f}`\n"
        f"강도: `{levels['tier'].upper()}`\n"
    )
