"""WebSocket 진단 — 독립 실행. 봇 코드와 분리.

용도: 봇이 데이터를 못 받을 때 "WebSocket 자체가 문제냐, 봇 로직이 문제냐"
구분.

실행:
    python -m bot.realtime.test_ws
"""

from __future__ import annotations

import asyncio
import json
import sys
import websockets

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


async def main():
    url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"
    print(f"연결: {url}")
    print("연결 시도 중...")
    async with websockets.connect(url, ping_interval=180) as ws:
        print("✅ 연결 성공. 메시지 대기 중...")
        for i in range(3):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                print(f"❌ 메시지 {i+1}: 10초 동안 안 옴!")
                print("   → 본인 네트워크에서 바이낸스 WebSocket 차단됐을 가능성")
                print("   → 회사 방화벽 / 한국 ISP / VPN 등 확인 필요")
                return
            data = json.loads(raw)
            t = type(data).__name__
            n = len(data) if hasattr(data, "__len__") else "?"
            print(f"\n✅ 메시지 {i+1}: type={t}, 심볼 {n}개")
            if isinstance(data, list) and data:
                print(f"   첫 메시지 첫 심볼 keys: {sorted(data[0].keys())}")
                # 펀딩비 상위 5개
                sorted_data = sorted(
                    data, key=lambda x: -abs(float(x.get("r", 0)))
                )[:5]
                print(f"   현재 펀딩비 top 5:")
                for item in sorted_data:
                    sym = item.get("s", "?")
                    funding = float(item.get("r", 0)) * 100
                    mark = float(item.get("p", 0))
                    print(f"      {sym:<16} {funding:>+8.4f}%  mark={mark:g}")
            elif isinstance(data, dict):
                print(f"   keys: {sorted(data.keys())}")
                print(f"   value: {str(data)[:300]}")

    print("\n✅ 모든 메시지 정상 수신. WebSocket 100% 작동.")
    print("→ 봇이 /now2 에서 '수집안됨' 뜨는 건 다른 이유 (봇 재시작 안 됐거나, 코드 안 받아왔거나)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"❌ 에러: {type(e).__name__}: {e}")
