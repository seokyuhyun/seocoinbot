"""Upbit 현물 실시간 시그널 봇.

Binance 봇 (bot/realtime/) 과 완전 분리:
- 별도 Telegram 봇 토큰 (UPBIT_TELEGRAM_BOT_TOKEN)
- 별도 paper trade CSV
- 현물 거래만 (LONG only, 레버리지 1, 펀딩·청산 X)

모듈:
- config        : .env 로드 (Upbit 전용)
- telegram      : Telegram 전송 (Upbit 토큰 사용)
- upbit_ws      : WebSocket ticker stream
- upbit_rest    : REST API (캔들·마켓 조회)
- symbol_picker : 상위 N KRW 마켓 (24h 거래대금)
- volume_detector: 1분 거래량 spike 감지 (캔들 기반)
- levels        : 현물 TP/SL 계산
- paper_trader  : 현물 paper (LONG only)
- handler       : WS price + REST 폴링 통합
- commands      : Telegram 명령어 (/help, /status, /now 등)
- main          : 진입점

실행:
    python -m bot.upbit.main
"""
