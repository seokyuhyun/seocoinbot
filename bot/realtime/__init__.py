"""seocoinbot 실시간 시그널 봇 (MVP).

WebSocket 으로 바이낸스 선물 데이터 받아서 시그널 발생 시 텔레그램 전송.

모듈:
- config        : .env 로드, 상수
- telegram      : Telegram Bot API 메시지 전송
- binance_ws    : WebSocket 구독 매니저 (재연결)
- symbol_picker : 상위 N USDT 무기한 조회
- funding_alerts: MVP 시그널 — 펀딩비 spike 감지
- main          : 진입점 (asyncio.run)

실행:
    python -m bot.realtime.main
"""
