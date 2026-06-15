# CHANGES

## v2.0.1 (2026-06-11)

### バグ修正

- `shutdown`/`reboot`: `os` 設定済みホストで `user`/`password`（windowsの場合）が未設定の場合に `KeyError` で分かりにくいエラーになっていた問題を修正し、設定不備として明示的にメッセージを返すように変更

### ドキュメント

- `config_sample.yaml`: SSH(`os: linux/macos`)では `password` が使われない（鍵認証のみ対応）ことを明記

---

## v2.0.0 (2026-06-11)

### 破壊的変更

- **`config.yaml` の `hosts` / `pc` セクションを `hosts` に統合**
  - `pc` セクションを廃止。`os` / `user` / `password` は `hosts` の各ホストに統合
  - 自ホスト（Bot 動作ホスト）は `hosts` に `self: true` を指定して登録する
  - 既存の `config.yaml` は手動でのマイグレーションが必要

### コマンド変更

- **`shutdown` / `reboot` を `<name>` 必須に変更し、`pc shutdown` / `pc reboot` と統合**
  - `/local shutdown <name>` / `/local reboot <name>` に一本化
  - `hosts` の `self: true` ホスト → ローカルで `sudo shutdown` / `sudo reboot` を実行
  - `os` 設定済みのリモートホスト → SSH / `net rpc` で実行
  - いずれの設定も無いホストは「対応していません」と返答
  - 引数なしの `shutdown` / `reboot` は廃止（誤操作で自ホストを止めてしまうことを防止するため `<name>` を必須化）
  - `pc shutdown` / `pc reboot` の権限チェックを `notify_user_id` のみに統一（従来は権限チェック無し）
  - `watch` 中のホストに対する `shutdown`/`reboot` を拒否し、警告を返すように変更（オフライン誤通知を防止）

### バグ修正

- `wol` コマンド: `hosts` に `mac` が未設定のホストを指定すると KeyError でクラッシュする問題を修正

### 改善

- ログ出力: `[INFO    ]` のような levelname のパディングを廃止し `[INFO]` のように詰めて表示

---

## v1.2.0 (2026-06-09)

### 新機能

- **`extend 0` による自動シャットダウンの一時無効化**
  - `/local extend 0` でセッション中の自動シャットダウンを無効化
  - 無効化中に `/local extend <N>`（N>0）を実行すると再有効化し、N 分後にシャットダウン
  - 再起動すると `timeout.minutes` の設定値に戻る
  - `timeout.minutes: 0`（設定で恒久無効）とは独立した動作

---

## v1.1.0 (2026-06-09)

### 新機能

- **二重起動防止**: `fcntl` によるファイルロックを追加。同一ホストで複数インスタンスが起動しようとすると即座にエラー終了する。クラッシュ後も OS がロックを自動解放するため手動削除不要

### バグ修正

- `calc_speedtest_stats()`: アップロード結果が空のとき ZeroDivisionError が発生する問題を修正
- `notify_start()`: Slack API 失敗時（起動直後のネットワーク未接続など）に例外が伝播して起動クラッシュする問題を修正
- `timeout_watcher()`: `chat_postMessage` 失敗時に `shutdown` が実行されない問題を修正
- `watch_ip()`: `chat_postMessage` 失敗時に監視スレッドが無言で終了する問題を修正
- `get_status()`: `psutil.disk_usage()` に `device` 属性が存在しないため別パーティション判定が常に誤動作していた問題を修正（`os.stat().st_dev` に変更）
- `speedtest`: エラー時の文字列が `speedtest.log` に書き込まれ、次回の統計計算でパース失敗する問題を修正
- `run_speedtest()`: ハング時にスレッドが詰まる問題を修正（タイムアウト 120 秒を追加）
- `scan_network()`: ハング時にブロックする問題を修正（タイムアウト 30 秒を追加）
- `scan` コマンド: `arp-scan` 失敗・タイムアウト時の例外が握りつぶされていた問題を修正
- `wol` コマンド: ホスト名の末尾スペースでホスト名不一致になる問題を修正（`.strip()` 追加）
- `timeout_watcher()`: `cfg["timeout"]["minutes"]` のキー直アクセスを `.get()` に変更（`timeout` セクション未定義時の KeyError を修正）
- `extend` コマンド: 0 以下の値を受け付けてタイムアウトが意図せず縮む問題を修正
- `setup.sh`: `&>/dev/null` を `>/dev/null 2>&1` に変更（POSIX sh 非互換構文の修正）

### 改善

- `watch` コマンド: IP アドレスに加えてホスト名での監視開始に対応
- `pc shutdown/reboot`: サブプロセスのタイムアウトを 5 秒 → 15 秒に延長（SSH/RPC の応答時間を考慮）
- `scan_network()`: `arp-scan` が異常終了した場合に stderr を WARNING ログに出力
- `get_status()`: DATA_BASE が別パーティション（USB など）の場合にそのディスク使用量も追加表示
- `get_status()`: 稼働時間の表示形式を `H:MM:SS` から `X時間Y分Z秒` に変更
- `timeout_watcher()`: 起動通知・シャットダウン通知の稼働時間表示を `X時間Y分Z秒` 形式に統一
- `notify_start()`: 起動通知にホスト名を追加
- ログ: ファイルハンドラを常に INFO 以上に固定（`--debug` 時もディスク書き込みを抑制）
- `speedtest.log`: ローテーションを追加（512KB × 3世代）
- Linux 起動時: DATA_BASE が `/mnt` 配下でない場合に SD カード書き込みの警告を表示
- 各所の "Raspberry Pi" 固定文言をホスト汎用表現に変更

---

## v1.0.0 (2026-06-09)

初回リリース。

---

### 対応環境

| 用途 | OS |
|------|----|
| 開発 | macOS |
| 本番 | Raspberry Pi OS Trixi / Ubuntu |

---

### 機能仕様

#### スラッシュコマンド

Slack の Socket Mode（VPN・ポート開放不要）で動作。  
コマンド名は `config.yaml` の `bot.command` で変更可能（デフォルト: `local`）。

| コマンド | 動作 |
|---------|------|
| `help` | コマンド一覧を表示 |
| `status` | CPU温度・CPU使用率・メモリ・ディスク・稼働時間を表示 |
| `scan` | arp-scan で LAN をスキャンして表形式で表示 |
| `wol <name>` | Wake-on-LAN 送信 → ping で起動確認 → ポート確認 |
| `speedtest` | 回線速度測定（結果を記録・統計表示） |
| `extend <分>` | 無操作シャットダウンタイマーを延長 |
| `watch <ip\|name>` | 指定ホストの疎通監視を開始（IP またはホスト名） |
| `unwatch <ip\|name>` | 疎通監視を解除 |
| `watchlist` | 監視中ホスト一覧を表示 |
| `pc shutdown <name>` | PC をシャットダウン（Windows: net rpc / Linux・macOS: SSH） |
| `pc reboot <name>` | PC を再起動（同上） |
| `shutdown` | ホストをシャットダウン（権限チェックあり） |
| `reboot` | ホストを再起動（権限チェックあり） |

不明なコマンドはあいまいマッチ（difflib）で候補を提示。

#### App Home タブ

Slack の App Home タブにコマンド一覧を常時表示。

#### 疎通監視

- `watch.fail_threshold` 回連続失敗でオフライン通知
- 復帰時にオンライン通知
- `watch.interval` 秒ごとに ping チェック（デフォルト 10 秒）
- 複数ホストを同時監視可能
- スレッド安全な停止処理

#### 無操作シャットダウン

- `timeout.minutes` 分間 Slack 操作がなければ自動シャットダウン
- `extend <分>` で延長可能（累積）
- `timeout.minutes: 0` で無効化（手動 `shutdown` のみ）

#### スリープ抑制

| OS | 手段 |
|----|------|
| macOS | `caffeinate -i -w <PID>`（常時有効） |
| Linux | `systemd-inhibit`（`SLACKBOT_PREVENT_SLEEP=true` 時のみ） |

#### ログ

| ファイル | 内容 | ローテーション |
|---------|------|--------------|
| `logs/slackbot.log` | 動作ログ（INFO 以上） | 1MB × 5世代 |
| `logs/speedtest.log` | 速度測定履歴 | 512KB × 3世代 |

`--debug` フラグ時は DEBUG レベルをコンソールのみ出力（ファイル書き込みなし）。

#### 起動通知

起動時に `notify_user_id` へ Slack DM を送信。ホスト名・起動時刻を含む。

---

### 設定ファイル（config.yaml）

| キー | 説明 |
|------|------|
| `bot.command` | スラッシュコマンド名（デフォルト: `local`） |
| `slack.bot_token` | Bot Token（`xoxb-`） |
| `slack.app_token` | App-Level Token（`xapp-`、Socket Mode 用） |
| `slack.notify_user_id` | 起動通知・監視通知の送信先ユーザー ID（`U`始まり）。shutdown/reboot の権限チェックにも使用 |
| `network.cidr` | arp-scan の対象 CIDR |
| `hosts` | WOL・watch のホスト定義（ip / mac） |
| `ports` | WOL 後のポート確認対象 |
| `pc` | PC 操作対象（ip / os / user / password） |
| `timeout.minutes` | 無操作シャットダウンまでの時間（0 で無効） |
| `watch.fail_threshold` | オフライン判定の連続失敗回数 |
| `watch.interval` | ping チェック間隔（秒） |

---

### データパス解決（優先順位）

1. `--data` オプション
2. 環境変数 `SLACKBOT_DATA`
3. `.env` ファイル（`setup.sh` が自動生成）
4. スクリプトと同じディレクトリ（フォールバック）

---

### セットアップ（setup.sh）

`sh setup.sh` で以下を自動実行：

- Python バージョン確認（3.9 以上）
- システムパッケージのインストール（Homebrew / apt）
- Python 仮想環境（venv）の作成
- pip パッケージのインストール
- データディレクトリ・config.yaml のセットアップ
- `.env` への設定保存
- systemd サービスの生成・登録・有効化（Linux のみ）
  - データが `/mnt/` 配下の場合は `RequiresMountsFor` を自動付与

---

### 依存パッケージ

```
slack-bolt>=1.18
psutil>=5.9
wakeonlan>=2.1
PyYAML>=6.0
```

システムコマンド: `arp-scan`（要 sudo）、`speedtest-cli`、`ping`、`ssh`、`net rpc`
