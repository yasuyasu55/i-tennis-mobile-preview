# 新城エリア 自動取得の調査（2026-10-10）

## 方針
地域を統合しない。新城は個別の検索項目とし、実取得・検証できた大会がある場合に追加する。今回は公開画面・大会データ・定期更新設定を変更していない。

## 掲載先
- 新城テニス協会: https://shinshiro-tennis.com/
- 2026年度年間予定: https://shinshiro-tennis.com/e1382200.html
- 2026年B級ダブルス: https://shinshiro-tennis.com/e1376566.html
- 2026年春季クラブ対抗: https://shinshiro-tennis.com/e1383946.html
- 市の趣味活案内が示す入口: http://www.shinshiro-tennis.com/
  確認元: https://wakamono-gikai.jp/shumikatsu/detail?id=5

掲載先は検索・公式案内で確認した候補。実HTML・PDFの取得成功とは扱わない。年間予定、募集要項、結果、練習会の区別を検証するまでは大会レコードを作らない。

## 実測
- 初回: https://shinshiro-tennis.com/robots.txt への接続が25秒でタイムアウト。
  実行: https://github.com/yasuyasu55/i-tennis-mobile-preview/actions/runs/38014685310
- 別表記の入口: http://www.shinshiro-tennis.com/robots.txt と https://www.shinshiro-tennis.com/robots.txt とも接続タイムアウト。
  実行: https://github.com/yasuyasu55/i-tennis-mobile-preview/actions/runs/38014773827
- 既存fetcherの方針に従い、robots確認不能時は本文取得へ進まない。
- 明示的なアクセス禁止、公式サイト全体の障害、恒久的な取得不可のいずれも断定しない。
- Actionsの緑表示は「診断処理完了」であり「大会取得成功」ではない。取得大会数は0件。

## 再開条件
入口への通常HTTP接続とrobotsの確認が可能になった後、表示HTMLからPDFリンクを発見して取得し、募集・年間予定・結果を分類する。期限・参加資格等を推測しない。新規source追加後は既存74件を保持するdry-runと検索回帰確認を経て定期更新へ組み込む。手動取得で代用しない。

## 変更範囲
検証専用ブランチshinshiro-source-probeのprobeとこの文書のみ。main、hamamatsu-r5-candidate、公開JSON、公開UI、既存の週次更新は変更していない。不要な再アクセスを避けるためprobeのpushトリガーを終了した。
