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
    # SL 은 5배 레버리지 가정 시 -1.2% → 계좌 -6% 라 그대로 유지.
    # 큰 펌프 (5%+) 잡으려고 TP3 만 폭 넓힘.
    abs_f = abs(funding)
    if abs_f >= 0.0010:           # >= 0.10%
        tp1p, tp2p, tp3p, slp = 0.015, 0.030, 0.060, 0.012
        tier = "strong"
    elif abs_f >= 0.0007:         # 0.07 ~ 0.10%
        tp1p, tp2p, tp3p, slp = 0.010, 0.020, 0.045, 0.010
        tier = "moderate"
    else:                          # 0.05 ~ 0.07%
        tp1p, tp2p, tp3p, slp = 0.007, 0.015, 0.035, 0.008
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
    # 캐스케이드 반발은 빠른 회복 (5~30분). TP1/2 는 빠른 익절, TP3 만 큰 반등 잡음.
    if total_liq_usd >= 2_000_000:        # $2M+ 강한 캐스케이드
        tp1p, tp2p, tp3p, slp = 0.015, 0.030, 0.060, 0.010
        tier = "strong"
    elif total_liq_usd >= 1_000_000:      # $1M~$2M 중간
        tp1p, tp2p, tp3p, slp = 0.010, 0.020, 0.040, 0.009
        tier = "moderate"
    else:                                  # $500k~$1M 약한
        tp1p, tp2p, tp3p, slp = 0.007, 0.015, 0.030, 0.008
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


_CASCADE_TIER_KR = {
    "strong": "강함 ($2M+)",
    "moderate": "보통 ($1~2M)",
    "weak": "약함 ($500k~$1M)",
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
    """청산 캐스케이드 시그널 — 초보자 친화."""
    emoji = "🟢" if side == "long" else "🔴"
    side_text = "*매수* (LONG)" if side == "long" else "*매도* (SHORT)"
    pct_sign = 1 if side == "long" else -1
    tp1_disp = f"{pct_sign * levels['tp1_pct'] * 100:+.2f}%"
    tp2_disp = f"{pct_sign * levels['tp2_pct'] * 100:+.2f}%"
    tp3_disp = f"{pct_sign * levels['tp3_pct'] * 100:+.2f}%"
    sl_disp = f"{-pct_sign * levels['sl_pct'] * 100:+.2f}%"

    if total_liq_usd >= 1_000_000:
        liq_disp = f"${total_liq_usd / 1_000_000:.2f}M"
    else:
        liq_disp = f"${total_liq_usd / 1000:.0f}k"

    liquidated_kr = "롱(매수)" if lopsided_side == "long" else "숏(매도)"
    tier_kr = _CASCADE_TIER_KR.get(levels["tier"], levels["tier"])
    explain = (
        "매도 압력 끝나면 가격 반등 → 매수 후보"
        if side == "long"
        else "매수 압력 끝나면 가격 하락 → 매도 후보"
    )

    return (
        f"{emoji} *#{symbol}* — 바이낸스 선물 {side_text}\n"
        f"🌊 청산 캐스케이드 반발 신호\n"
        f"━━━━━━━━━━━━\n"
        f"💵 *진입가*: `{_fmt_price(entry)}`\n"
        f"🔢 레버리지: `{leverage}배` (격리 마진 권장)\n\n"
        f"🎯 *1차 익절*: `{_fmt_price(levels['tp1'])}` (`{tp1_disp}`) — 40% 청산\n"
        f"🎯 *2차 익절*: `{_fmt_price(levels['tp2'])}` (`{tp2_disp}`) — 30% 청산\n"
        f"🎯 *3차 익절*: `{_fmt_price(levels['tp3'])}` (`{tp3_disp}`) — 나머지 30%\n\n"
        f"🛑 *손절*: `{_fmt_price(levels['sl'])}` (`{sl_disp}`)\n"
        f"━━━━━━━━━━━━\n"
        f"📊 {window_minutes}분 강제청산: `{liq_disp}` "
        f"({liquidated_kr} `{lopsided_pct:.0f}%` 차지) — {tier_kr}\n"
        f"📉 가격 변동: `{price_change_pct:+.2f}%`\n"
        f"   👉 {explain}\n"
        f"⏰ *8시간* 안에 결판\n"
    )


_FUNDING_TIER_KR = {
    "strong": "강함 (0.10%+)",
    "moderate": "보통 (0.07~0.10%)",
    "weak": "약함 (0.05~0.07%)",
}


def format_signal(
    symbol: str,
    side: Literal["long", "short"],
    entry: float,
    levels: dict,
    leverage: int,
    funding: float,
    minutes_to_funding: int,
) -> str:
    """펀딩 spike 시그널 — 초보자 친화."""
    emoji = "🟢" if side == "long" else "🔴"
    side_text = "*매수* (LONG)" if side == "long" else "*매도* (SHORT)"
    pct_sign = 1 if side == "long" else -1
    tp1_disp = f"{pct_sign * levels['tp1_pct'] * 100:+.2f}%"
    tp2_disp = f"{pct_sign * levels['tp2_pct'] * 100:+.2f}%"
    tp3_disp = f"{pct_sign * levels['tp3_pct'] * 100:+.2f}%"
    sl_disp = f"{-pct_sign * levels['sl_pct'] * 100:+.2f}%"

    hours, minutes = divmod(max(0, minutes_to_funding), 60)
    tier_kr = _FUNDING_TIER_KR.get(levels["tier"], levels["tier"])
    explain = (
        "롱(매수)이 너무 많아 과열 상태\n   👉 곧 풀릴 가능성 → *매도(SHORT)* 진입 후보"
        if funding > 0 else
        "숏(매도)이 너무 많아 과열 상태\n   👉 곧 풀릴 가능성 → *매수(LONG)* 진입 후보"
    )

    return (
        f"{emoji} *#{symbol}* — 바이낸스 선물 {side_text}\n"
        f"📈 펀딩비 spike 신호\n"
        f"━━━━━━━━━━━━\n"
        f"💵 *진입가*: `{_fmt_price(entry)}`\n"
        f"🔢 레버리지: `{leverage}배` (격리 마진 권장)\n\n"
        f"🎯 *1차 익절*: `{_fmt_price(levels['tp1'])}` (`{tp1_disp}`) — 40% 청산\n"
        f"🎯 *2차 익절*: `{_fmt_price(levels['tp2'])}` (`{tp2_disp}`) — 30% 청산\n"
        f"🎯 *3차 익절*: `{_fmt_price(levels['tp3'])}` (`{tp3_disp}`) — 나머지 30%\n\n"
        f"🛑 *손절*: `{_fmt_price(levels['sl'])}` (`{sl_disp}`)\n"
        f"━━━━━━━━━━━━\n"
        f"📊 펀딩비: `{funding * 100:+.4f}%` ({tier_kr})\n"
        f"   👉 {explain}\n"
        f"⏰ 다음 펀딩 정산: `{hours}h {minutes}m` 안에 결판\n"
    )
