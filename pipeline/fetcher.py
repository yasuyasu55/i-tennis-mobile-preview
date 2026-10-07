"""
礼儀正しい取得（開発側の定期処理専用。モバイルアプリからは使わない）

方針（TTA-MOBILE-013 の通信ルールを継承）
  - robots.txt を確認し、許可されていないURLは取得しない（RFC 9309: 404等の4xx=制限なし、5xx・接続失敗=取得しない）
  - 同じホストへのアクセスは、最低 N 秒あけて1件ずつ（並列・短時間の繰り返しをしない）
  - 1回の実行あたりの総リクエスト数に上限（robots.txtも数える）
  - 取得サイズに上限。リダイレクトは標準の範囲（urllib）。認証が必要なページや、自動取得を妨げる仕組みの回避、URLの総当たりはしない
  - 取得失敗は再試行しない（次回の定期実行まで待つ）
"""
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


class FetchError(Exception):
    def __init__(self, stage: str, message: str, url: Optional[str] = None, status: Optional[int] = None):
        super().__init__(message)
        self.stage, self.message, self.url, self.status = stage, message, url, status


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes
    content_type: str


def default_http(url: str, headers: Dict[str, str], timeout: float, max_bytes: int) -> Tuple[int, Dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            body = res.read(max_bytes + 1)
            return res.status, {k.lower(): v for k, v in res.headers.items()}, body
    except urllib.error.HTTPError as e:   # 4xx/5xx はステータスとして返す
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, b""


class PoliteFetcher:
    def __init__(self, user_agent: str, delay: float = 3.0, timeout: float = 20.0, max_requests: int = 40,
                 http: Callable = default_http, sleep: Callable = time.sleep, clock: Callable = time.monotonic):
        self.ua, self.delay, self.timeout, self.max_requests = user_agent, delay, timeout, max_requests
        self.http, self.sleep, self.clock = http, sleep, clock
        self.count = 0
        self.log: List[dict] = []
        self._last: Dict[str, float] = {}
        self._robots: Dict[str, object] = {}

    def _request(self, url: str, max_bytes: int, kind: str) -> Tuple[int, Dict[str, str], bytes]:
        if self.count >= self.max_requests:
            raise FetchError("limit", "1回の実行あたりのリクエスト上限（%d）に達したため取得しません" % self.max_requests, url)
        host = urllib.parse.urlsplit(url).netloc
        wait = self.delay - (self.clock() - self._last.get(host, -1e9))
        if wait > 0:
            self.sleep(wait)
        if not self.ua.isascii():      # HTTPヘッダーは英数字・記号だけ（日本語を含むと、標準ライブラリが接続前に例外を出す）
            raise FetchError("config", "User-Agent に英数字・記号以外の文字が含まれています（接続前に停止）", url)
        self._last[host] = self.clock()
        self.count += 1
        t0 = self.clock()
        try:
            status, headers, body = self.http(url, {"User-Agent": self.ua, "Accept": "*/*"}, self.timeout, max_bytes)
        except Exception as e:  # 接続失敗・タイムアウト等
            self.log.append({"url": url, "kind": kind, "status": None, "error": "%s: %s" % (type(e).__name__, e)})
            raise FetchError("network", "接続できませんでした: %s" % type(e).__name__, url)
        self._last[host] = self.clock()
        self.log.append({"url": url, "kind": kind, "status": status, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest() if body else None,
                         "elapsed_ms": int((self.clock() - t0) * 1000)})
        return status, headers, body

    def _robots_for(self, url: str):
        parts = urllib.parse.urlsplit(url)
        origin = "%s://%s" % (parts.scheme, parts.netloc)
        if origin in self._robots:
            return self._robots[origin]
        robots_url = origin + "/robots.txt"
        try:
            status, _h, body = self._request(robots_url, 500000, "robots")
        except FetchError as e:
            if e.stage in ("limit", "config"):
                raise                          # 上限・設定の誤りは、robots.txt の拒否ではない（そのまま報告する）
            self._robots[origin] = "deny"     # 接続できない → 取得しない
            return "deny"
        if status == 200:
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(body.decode("utf-8", "replace").splitlines())
            self._robots[origin] = rp
        elif 400 <= status < 500:
            self._robots[origin] = "allow"    # robots.txt が無い（制限なし）
        else:
            self._robots[origin] = "deny"     # 5xx → 取得しない
        return self._robots[origin]

    def check_robots(self, url: str) -> None:
        """robots.txt を確認し、許可されていなければ FetchError("robots")。ブラウザ取得の前にも使う（ブラウザなら禁止を回避できる、という使い方はしない）。"""
        r = self._robots_for(url)
        if r == "deny" or (not isinstance(r, str) and not r.can_fetch(self.ua, url)):
            self.log.append({"url": url, "kind": "page", "status": None, "error": "robots.txt により取得しません"})
            raise FetchError("robots", "robots.txt により取得できない（許可されていない、または robots.txt を確認できない）", url)

    def get(self, url: str, max_bytes: int) -> FetchResult:
        self.check_robots(url)
        status, headers, body = self._request(url, max_bytes, "page")
        if status != 200:
            raise FetchError("http", "HTTP %d" % status, url, status)
        if len(body) > max_bytes:
            raise FetchError("size", "取得サイズが上限（%d バイト）を超えました" % max_bytes, url, status)
        if not body:
            raise FetchError("http", "空の応答でした", url, status)
        return FetchResult(url, status, body, headers.get("content-type", ""))
