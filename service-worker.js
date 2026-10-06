/*
 * I-Tennis Mobile Service Worker（オフラインでアプリ画面を開くための最小構成）
 *
 * 方針:
 *  - キャッシュするのは「アプリを開くために必要なファイル」（SHELL_FILES）だけ。
 *  - 外部サイト・API応答はキャッシュしない（そもそも介入しない）。アプリは外部サイトへ通信しない。
 *  - 例外として、同じ場所の大会データ(data/tournaments.json)だけは、ネットワーク優先で取得する（下の DATA-NETWORK-FIRST）。
 *  - localStorage（予定データ）には一切触れない。Service Workerからは参照もできない。
 *  - アプリ本体を更新するときは APP_VERSION を上げる。旧バージョンのアプリ用キャッシュだけを削除する。
 *    大会データの更新だけでは、APP_VERSION は変えない（データはネットワーク優先で取得するため）。
 */
'use strict';

var APP_VERSION = '0.7.2';
var CACHE_PREFIX = 'i-tennis-mobile-shell-';
var CACHE_NAME = CACHE_PREFIX + 'v' + APP_VERSION;

// Service Workerの置かれた場所を基準にした相対パス（サブディレクトリ公開でも動く）
var SHELL_FILES = [
  './',
  './index.html',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/apple-touch-icon-180.png',
  './data/tournaments.json'
];

function scopeUrl(relative) {
  return new URL(relative, self.registration.scope).href;
}

function shellUrls() {
  return SHELL_FILES.map(scopeUrl);
}

function isShellUrl(urlString) {
  var u = new URL(urlString);
  u.search = '';
  u.hash = '';
  return shellUrls().indexOf(u.href) !== -1;
}

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      // cache:'reload' でブラウザのHTTPキャッシュを経由せず、最新のファイルを保存する
      var requests = shellUrls().map(function (u) { return new Request(u, { cache: 'reload' }); });
      return cache.addAll(requests);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      // このアプリのプレフィックスを持ち、かつ現行バージョンでないキャッシュだけを削除する。
      // 他のアプリ・他の用途のキャッシュには触れない。
      return Promise.all(
        keys
          .filter(function (key) { return key.indexOf(CACHE_PREFIX) === 0 && key !== CACHE_NAME; })
          .map(function (key) { return caches.delete(key); })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

/* DATA-NETWORK-FIRST:START
 * 大会データ(data/tournaments.json)は、ネットワーク優先で取得する。
 *   ・取得に成功し、JSONとして妥当（tournaments が配列）なら、キャッシュを更新して、その応答を返す。
 *   ・失敗（接続できない・タイムアウト・HTTPエラー・JSONとして不正）したときは、前回キャッシュしたデータを返す。
 *     返す応答には X-Served-From-Cache: fallback を付け、画面で「保存済みのデータを表示している」と伝えられるようにする。
 *   ・キャッシュも無ければ 503 を返す（画面は「読み込めませんでした」を表示する。予定データには影響しない）。
 * 対象は、同じ場所の data/tournaments.json だけ。外部サイトには介入しない。localStorage（予定データ）には触れない。
 */
var DATA_PATH = './data/tournaments.json';
var DATA_TIMEOUT_MS = 8000;

function isDataUrl(urlString) {
  var u = new URL(urlString);
  u.search = '';
  u.hash = '';
  return u.href === scopeUrl(DATA_PATH);
}

function fetchDataWithTimeout(url) {
  return new Promise(function (resolve, reject) {
    var finished = false;
    var timer = setTimeout(function () {
      if (!finished) { finished = true; reject(new Error('timeout')); }
    }, DATA_TIMEOUT_MS);
    fetch(url, { cache: 'no-store' }).then(function (res) {
      if (finished) { return; }
      finished = true;
      clearTimeout(timer);
      resolve(res);
    }, function (err) {
      if (finished) { return; }
      finished = true;
      clearTimeout(timer);
      reject(err);
    });
  });
}

function isValidDataResponse(res) {
  return res.clone().json().then(function (j) {
    return !!(j && Array.isArray(j.tournaments));
  }).catch(function () { return false; });
}

function findCachedData() {
  var url = scopeUrl(DATA_PATH);
  return caches.open(CACHE_NAME).then(function (cache) {
    return cache.match(url);
  }).then(function (hit) {
    if (hit) { return hit; }
    // 現行のキャッシュに無いときは、このアプリの旧バージョンのキャッシュから探す
    return caches.keys().then(function (keys) {
      var names = keys.filter(function (k) { return k.indexOf(CACHE_PREFIX) === 0; }).sort().reverse();
      return names.reduce(function (p, name) {
        return p.then(function (found) {
          if (found) { return found; }
          return caches.open(name).then(function (c) { return c.match(url); });
        });
      }, Promise.resolve(null));
    });
  });
}

function markServedFromCache(res) {
  var headers = new Headers(res.headers);
  headers.set('X-Served-From-Cache', 'fallback');
  return res.blob().then(function (blob) {
    return new Response(blob, { status: 200, statusText: 'OK', headers: headers });
  });
}

function handleData(request) {
  return fetchDataWithTimeout(request.url).then(function (res) {
    if (!res.ok) { throw new Error('http ' + res.status); }
    return isValidDataResponse(res).then(function (valid) {
      if (!valid) { throw new Error('invalid data'); }
      var copy = res.clone();
      return caches.open(CACHE_NAME).then(function (cache) {
        return cache.put(scopeUrl(DATA_PATH), copy);
      }).catch(function () { /* 保存に失敗しても、取得できた応答は返す */ }).then(function () { return res; });
    });
  }).catch(function () {
    return findCachedData().then(function (hit) {
      if (hit) { return markServedFromCache(hit); }
      return new Response('', { status: 503, statusText: 'Service Unavailable' });
    });
  });
}
/* DATA-NETWORK-FIRST:END */

function offlineFallbackResponse() {
  return new Response(
    '<!doctype html><html lang="ja"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>I-Tennis Mobile</title></head><body style="font-family:sans-serif;padding:24px">' +
    '<h1>オフラインです</h1><p>アプリの画面がまだ保存されていません。' +
    'ネットワークに接続してから、もう一度開いてください。</p></body></html>',
    { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
  );
}

function handleNavigation(request) {
  // ページ移動はネットワーク優先（更新を受け取りやすくする）。
  // ネットワークに失敗したときだけ、保存済みの index.html を返す。
  return fetch(request).catch(function () {
    return caches.open(CACHE_NAME).then(function (cache) {
      return cache.match(scopeUrl('./index.html'));
    }).then(function (cached) {
      return cached || offlineFallbackResponse();
    });
  });
}

function handleShellAsset(request) {
  // アプリ用ファイルは保存済みを優先。無い場合だけネットワークへ（取得結果は保存しない）。
  return caches.open(CACHE_NAME).then(function (cache) {
    return cache.match(request, { ignoreSearch: true }).then(function (hit) {
      return hit || fetch(request);
    });
  });
}

self.addEventListener('fetch', function (event) {
  var request = event.request;

  if (request.method !== 'GET') { return; }

  var url = new URL(request.url);
  if (url.origin !== self.location.origin) { return; } // 外部サイトには介入しない

  if (request.mode === 'navigate') {
    event.respondWith(handleNavigation(request));
    return;
  }

  /* DATA-NETWORK-FIRST:START */
  if (isDataUrl(request.url)) {
    event.respondWith(handleData(request));
    return;
  }
  /* DATA-NETWORK-FIRST:END */

  if (isShellUrl(request.url)) {
    event.respondWith(handleShellAsset(request));
    return;
  }

  // 上記以外（将来のAPI等）には介入しない = キャッシュしない
});
