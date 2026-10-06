/*
 * I-Tennis Mobile Service Worker（オフラインでアプリ画面を開くための最小構成）
 *
 * 方針:
 *  - キャッシュするのは「アプリを開くために必要なファイル」（SHELL_FILES）だけ。
 *  - 外部サイト・API応答・動的データはキャッシュしない（そもそも介入しない）。
 *    ※現時点でアプリ本体はそのような通信を行っていない。
 *  - localStorage（予定データ）には一切触れない。Service Workerからは参照もできない。
 *  - 更新するときは APP_VERSION を上げる。旧バージョンのアプリ用キャッシュだけを削除する。
 */
'use strict';

var APP_VERSION = '0.5.0';
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

  if (isShellUrl(request.url)) {
    event.respondWith(handleShellAsset(request));
    return;
  }

  // 上記以外（将来のAPI等）には介入しない = キャッシュしない
});
