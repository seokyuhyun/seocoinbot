"""적응형 TP/SL 레벨 계산.

펀딩비 강도가 클수록 TP 폭 넓힘 (포지션 과열 클수록 되돌림도 큼).
부분 청산: TP1=40%, TP2=30%, TP3=30% (합 100%).
"""

from __future__ import annotations

from typing import Literal

TP_FRACTIONS: tuple[float, float, float] = (0.40, 0.30, 0.30)


def calculate_levels(
    entry: float,
    side: Literal["long", "short"],
    funding: float,
) -> dict:
    """funding 강도에 따라 TP/SL 자동 산출.

    반환 dict:
      tp1, tp2, tp3, sl: 절대 가격
      tp1_pct, tp2_pct, tp3_pct, sl_pct: 진입 대비 비율 (양수)
      tier: "strong" | "moderate" | "weak"
    """
    abs_f = abs(funding)
    if abs_f >= 0.0010:           # >= 0.10%
        tp1p, tp2p, tp3p, slp = 0.010, 0.020, 0.040, 0.012
        tier = "strong"
    elif abs_f >= 0.0007:         # 0.07 ~ 0.10%
        tp1p, tp2p, tp3p, slp = 0.008, 0.015, 0.030, 0.010
        tier = "moderate"
    else:                          # 0.05 ~ 0.07%
        tp1p, tp2p, tp3p, slp = 0.006, 0.012, 0.025, 0.008
        tier = "weak"

    if side == "long":
        tp1 = entry * (1 + tp1p)
        tp2 = entry * (1 + tp2p)
        tp3 = entry * (1 + tp3p)
        sl = entry * (1 - slp)
    else:  # short
        tp1 = entry * (1 - tp1p)
        tp2 = entry * (1 - tp2p)
        tp3 = entry * (1 - tp3p)
        sl = entry * (1 + slp)

    return {
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl,
        "tp1_pct": tp1p, "tp2_pct": tp2p, "tp3_pct": tp3p, "sl_pct": slp,
        "tier": tier,
    }


def _fmt_price(p: float) -> str:
    """소수점 자릿수 적응형."""
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    if p >= 0.01:
        return f"${p:.5f}"
    return f"${p:.7f}"


def calculate_cascade_levels(
    entry: float,
    side: Literal["long", "short"],
    total_liq_usd: float,
) -> dict:
    """청산 캐스케이드 반발 매매용 레벨. 청산 규모 클수록 TP 넓힘.

    캐스케이드 반발은 펀딩 spike 보다 빠르고 작은 폭 (5~30분) — TP/SL 모두 타이트.
    """
    if total_liq_usd >= 2_000_000:        # $2M+ 강한 캐스케이드
        tp1p, tp2p, tp3p, slp = 0.010, 0.020, 0.040, 0.010
        tier = "strong"
    elif total_liq_usd >= 1_000_000:      # $1M~$2M 중간
        tp1p, tp2p, tp3p, slp = 0.007, 0.015, 0.030, 0.009
        tier = "moderate"
    else:                                  # $500k~$1M 약한
        tp1p, tp2p, tp3p, slp = 0.005, 0.010, 0.020, 0.008
        tier = "weak"

    if side == "long":
        tp1 = entry * (1 + tp1p)
        tp2 = entry * (1 + tp2p)
        tp3 = entry * (1 + tp3p)
        sl = entry * (1 - slp)
    else:
        tp1 = entry * (1 - tp1p)
        tp2 = entry * (1 - tp2p)
        tp3 = entry * (1 - tp3p)
        sl = entry * (1 + slp)

    return {
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl,
        "tp1_pct": tp1p, "tp2_pct": tp2p, "tp3_pct": tp3p, "sl_pct": slp,
        "tier": tier,
    }


def format_cascade_signal(
    symbol: str,
    side: Literal["long", "short"],
    entry: float,
    levels: dict,
    leverage: int,
    total_liq_usd: float,
    lopsided_pct: float,
    lopsided_side: str,          # "long" | "short" (어느 쪽이 청산됐는지)
    price_change_pct: float,
    window_minutes: int,
) -> str:
    """청산 캐스케이드 시그널 메시지."""
    emoji = "🟢" if side == "long" else "🔴"
    side_text = "LONG" if side == "long" else "SHORT"
    pct_sign = 1 if side == "long" else -1
    tp1_disp = f"{pct_sign * levels['tp1_pct'] * 100:+.2f}%"
    tp2_disp = f"{pct_sign * levels['tp2_pct'] * 100:+.2f}%"
    tp3_disp = f"{pct_sign * levels['tp3_pct'] * 100:+.2f}%"
    sl_disp = f"{-pct_sign * levels['sl_pct'] * 100:+.2f}%"

    if total_liq_usd >= 1_000_000:
        liq_disp = f"${total_liq_usd / 1_000_000:.2f}M"
    else:
        liq_disp = f"${total_liq_usd / 1000:.0f}k"

    liquidated_label = "롱 청산" if lopsided_side == "long" else "숏 청산"

    return (
        f"{emoji} *Coin: #{symbol}*\n"
        f"*{side_text}*  · 청산 캐스케이드 반발\n"
        f"━━━━━━━━━━━━\n"
        f"Entry: `{_fmt_price(entry)}`\n"
        f"Leverage: `{leverage}x` (isolated 권장)\n\n"
        f"Target 1: `{_fmt_price(levels['tp1'])}` ({tp1_disp})  · 40% 청산\n"
        f"Target 2: `{_fmt_price(levels['tp2'])}` ({tp2_disp})  · 30% 청산\n"
        f"Target 3: `{_fmt_price(levels['tp3'])}` ({tp3_disp})  · 30% 청산\n\n"
        f"StopLoss: `{_fmt_price(levels['sl'])}` ({sl_disp})\n"
        f"━━━━━━━━━━━━\n"
        f"{window_minutes}분 청산: `{liq_disp}` ({liquidated_label} `{lopsided_pct:.0f}%`)\n"
        f"가격 변화: `{price_change_pct:+.2f}%`\n"
        f"강도: `{levels['tier'].upper()}`\n"
    )


def format_signal(
    symbol: str,
    side: Literal["long", "short"],
    entry: float,
    levels: dict,
    leverage: int,
    funding: float,
    minutes_to_funding: int,
) -> str:
    """텔레그램 시그널 메시지 (사용자 요청 포맷)."""
    emoji = "🟢" if side == "long" else "🔴"
    side_text = "LONG" if side == "long" else "SHORT"
    pct_sign = 1 if side == "long" else -1
    # TP pct 표기: side 에 따라 부호 결정 (long=+, short=-)
    tp1_disp = f"{pct_sign * levels['tp1_pct'] * 100:+.2f}%"
    tp2_disp = f"{pct_sign * levels['tp2_pct'] * 100:+.2f}%"
    tp3_disp = f"{pct_sign * levels['tp3_pct'] * 100:+.2f}%"
    sl_disp = f"{-pct_sign * levels['sl_pct'] * 100:+.2f}%"

    hours, minutes = divmod(max(0, minutes_to_funding), 60)
    funding_reason = "롱 과열 → 숏" if funding > 0 else "숏 과열 → 롱"

    return (
        f"{emoji} *Coin: #{symbol}*\n"
        f"*{side_text}*\n"
        f"━━━━━━━━━━━━\n"
        f"Entry: `{_fmt_price(entry)}`\n"
        f"Leverage: `{leverage}x` (isolated 권장)\n\n"
        f"Target 1: `{_fmt_price(levels['tp1'])}` ({tp1_disp})  · 40% 청산\n"
        f"Target 2: `{_fmt_price(levels['tp2'])}` ({tp2_disp})  · 30% 청산\n"
        f"Target 3: `{_fmt_price(levels['tp3'])}` ({tp3_disp})  · 30% 청산\n\n"
        f"StopLoss: `{_fmt_price(levels['sl'])}` ({sl_disp})\n"
        f"━━━━━━━━━━━━\n"
        f"펀딩비: `{funding * 100:+.4f}%` ({funding_reason})\n"
        f"다음 정산: `{hours}h {minutes}m`\n"
        f"강도: `{levels['tier'].upper()}`\n"
    )
