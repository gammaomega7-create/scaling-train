"""ntfy.sh로 아이폰에 푸시 알림을 보낸다. (App Store의 ntfy 앱에서 같은 토픽 구독)"""

import json
import logging
import urllib.request

log = logging.getLogger(__name__)


def notify(topic: str, title: str, message: str) -> None:
    log.info("%s: %s", title, message)
    if not topic:
        return
    body = json.dumps({"topic": topic, "title": title, "message": message, "priority": 4})
    req = urllib.request.Request(
        "https://ntfy.sh/", data=body.encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except OSError as e:
        log.warning("알림 전송 실패: %s", e)
