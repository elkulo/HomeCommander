# CLAUDE.md — HomeCommander

## プロジェクト概要

Raspberry Pi / Ubuntu で自宅 LAN を管理する Slack Bot。  
Slack Socket Mode で動作（VPN・ポート開放不要）。  
スラッシュコマンドで WOL・LAN スキャン・疎通監視・PC 操作などを実行する。

---

## ファイル構成

```
slackbot.py          メインスクリプト（Bot 本体）
setup.sh             セットアップスクリプト（POSIX sh 互換）
config_sample.yaml   設定ファイルのサンプル（コメント付き）
requirements.txt     pip 依存パッケージ
slackbot.service     systemd サービステンプレート（参照用）
CHANGES.md           バージョン履歴・仕様記録
README.md            利用者向けドキュメント
.env                 データパス等の環境変数（setup.sh が生成、git 管理外）
venv/                Python 仮想環境（git 管理外）
data/                データディレクトリのデフォルト位置（git 管理外）
```

本番のデータディレクトリは通常 `/mnt/usbdata/slackbot/`（USB）。

---

## 主要な設計

### コマンド体系

`/local <subcommand>` 形式のスラッシュコマンド。  
コマンド名 `local` は `config.yaml` の `bot.command` で変更可能。  
`CMD = "/" + cfg.get("bot", {}).get("command", "local")` で解決。

### データパス解決

優先順位: `--data` 引数 → `SLACKBOT_DATA` 環境変数 → `.env` → スクリプトと同ディレクトリ。  
`.env` は `load_dot_env()` がモジュール起動時に自動読み込み。

### ロギング方針

- ファイル（`logs/slackbot.log`）: 常に INFO 以上のみ（SD カード書き込み保護）
- コンソール: `--debug` 時は DEBUG も出力
- `logs/speedtest.log`: 手動ローテーション（512KB × 3世代）

### スレッド構成

| スレッド | 役割 |
|---------|------|
| メインスレッド | Slack Socket Mode ハンドラ |
| `timeout_watcher` | 無操作シャットダウン監視（60秒ごと） |
| `watch_ip` × N | 疎通監視（IP ごとに 1スレッド） |

`watch_targets` dict でスレッドを管理。`threading.Event` で安全停止。

### Slack API の例外処理

`notify_start()`・`watch_ip()`・`timeout_watcher()` の `chat_postMessage` は
すべて try/except で囲み、API 失敗でもメイン処理が止まらないようにする。

---

## バージョン管理

バージョンは `slackbot.py` の先頭付近に定数として定義しています。

```python
VERSION = "2.0.0"
```

### バージョン番号のルール（セマンティックバージョニング）

`MAJOR.MINOR.PATCH` の形式で管理します。

| 種別 | 上げる桁 | 例 |
|------|---------|-----|
| 後方互換のない変更（設定ファイル仕様変更など） | MAJOR | 1.1.0 → 2.0.0 |
| 後方互換のある新機能追加 | MINOR | 1.1.0 → 1.2.0 |
| バグ修正・文言修正のみ | PATCH | 1.1.0 → 1.1.1 |

バグ修正と新機能が混在する場合は MINOR を上げる。

### テスト方針

現状は単一ファイル構成のため pytest は導入していない。  
モジュールレベルの副作用（config 読み込み・Slack 接続）が多く、テスト環境の整備コストが高いため。  
**MAJOR バージョンアップ（v2）のタイミングでマルチファイル構成への移行と合わせて検討する。**

### リリース時の更新箇所

| ファイル | 更新箇所 |
|---------|---------|
| `slackbot.py` | `VERSION = "x.y.z"` |
| `CHANGES.md` | 先頭に `## vx.y.z (日付)` セクションを追加 |

バージョンは起動時のコンソールログ（`HomeCommander vX.Y.Z 起動`）と
Slack への起動通知メッセージにも自動で表示されます。

---

## 開発上の注意

### setup.sh は POSIX sh 互換

- `#!/bin/sh` で動作する（`bash` 依存の構文は使わない）
- `[[ ]]` ではなく `[ ]`、`read -p` ではなく `printf + read -r`
- 変数名に日本語（全角文字）を絶対に使わない（UTF-8 バイトが変数名に混入するバグが発生する）

### Slack の 3 秒制限

スラッシュコマンドハンドラは `ack()` を最初に呼ぶ。  
`speedtest`（〜120秒）・`wol`（〜20秒）など時間のかかる処理は `ack()` 後に実行するため問題なし。

### subprocess のタイムアウト

| 処理 | タイムアウト |
|------|------------|
| `arp-scan` | 30秒 |
| `speedtest-cli` | 120秒 |
| `shutdown/reboot`（リモートホスト, SSH/RPC） | 15秒 |
| `ping`（WOL 確認） | リトライ 10回 × 2秒 |

### CPU 温度の取得

1. `vcgencmd measure_temp`（Raspberry Pi）
2. `/sys/class/thermal/thermal_zone0/temp`（Linux 汎用）
3. 「取得不可」（Mac など）

### 別パーティション判定

`os.stat(DATA_BASE).st_dev != os.stat("/").st_dev` で判定。  
`psutil.disk_usage()` は `device` 属性を持たないため使用しない。

---

## 環境変数（.env）

| 変数 | 説明 |
|------|------|
| `SLACKBOT_DATA` | データディレクトリのパス |
| `SLACKBOT_PREVENT_SLEEP` | `true` のとき Linux でスリープ抑制を有効化 |

---

## よく使うコマンド

```sh
# 開発起動
source venv/bin/activate
python slackbot.py --debug

# セットアップ（初回 / 再実行可）
sh setup.sh

# 本番サービス操作
sudo systemctl start   slackbot.service
sudo systemctl stop    slackbot.service
sudo systemctl restart slackbot.service
journalctl -u slackbot.service -f
```

---

## 対応 OS

| 環境 | OS | スリープ抑制 |
|------|----|------------|
| 開発 | macOS | caffeinate（常時） |
| 本番 | Raspberry Pi OS Trixi | systemd-inhibit（opt-in） |
| 本番 | Ubuntu | systemd-inhibit（opt-in） |
