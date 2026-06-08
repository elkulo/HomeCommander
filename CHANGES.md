# CHANGES

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
