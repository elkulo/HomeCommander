# HomeCommander (Slack LAN Manager Bot)

Raspberry Pi / Ubuntu で自宅 LAN を管理できる Slack Bot。  
VPN 不要・ポート開放不要・PC 側設定不要。  
overlayFS（読み取り専用 root）でも動作し、設定・ログは USB に永続化できる。

---

## 主な機能

- ホストの状態監視（CPU温度 / メモリ / ディスク / 稼働時間）
- LAN スキャン（arp-scan → 表形式）
- Wake-on-LAN（起動確認＋ポート確認）
- PC shutdown / reboot（Windows: RPC / Linux・macOS: SSH）
- 回線速度測定（履歴保存＋平均/最速/最遅）
- 無操作シャットダウン（延長コマンド付き・0 で無効化可）
- 疎通監視（落下通知・復帰通知・連続失敗閾値・一覧表示）
- USB 永続化（config / logs）
- overlayFS 対応（読み取り専用 root でも動作）

---

## ディレクトリ構成

### ホスト（SD カードなど）

```
/home/<user>/HomeCommander/
  slackbot.py
  requirements.txt
  setup.sh
  config_sample.yaml
  venv/
```

### データディレクトリ（USB などの永続化領域）

```
/mnt/usbdata/slackbot/          ← setup.sh のデフォルト
  config.yaml
  logs/
    slackbot.log                ← 動作ログ（INFO 以上、1MB × 5世代）
    speedtest.log               ← 速度測定履歴（512KB × 3世代）
```

---

## セットアップ

### セットアップスクリプト（推奨）

macOS / Raspberry Pi OS / Ubuntu を自動判別して環境を構築します。

```sh
sh setup.sh
```

実行内容：
- システムパッケージのインストール（Homebrew / apt）
- Python 仮想環境（venv）の作成
- pip パッケージのインストール
- データディレクトリの入力と config.yaml のコピー
- `.env` へのデータパス保存（起動時に自動読み込み）
- systemd サービスの生成・登録・有効化（Linux のみ）
  - データが `/mnt/` 配下の場合は USB マウント待ち設定を自動付与

### 手動セットアップ

#### 1. システムパッケージ

**Mac:**
```sh
brew install arp-scan speedtest-cli
```

**Raspberry Pi OS / Ubuntu:**
```sh
sudo apt install arp-scan speedtest-cli samba-common-bin python3-venv python3-dev
```

#### 2. Python 仮想環境

```sh
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 3. 設定ファイル

```sh
cp config_sample.yaml /mnt/usbdata/slackbot/config.yaml
```

`config.yaml` を編集して Slack トークンとネットワーク情報を設定してください。

#### 4. 起動

```sh
source venv/bin/activate
python slackbot.py
# データディレクトリを指定する場合:
python slackbot.py --data /mnt/usbdata/slackbot
```

#### 5. systemd サービス登録（Linux のみ）

`setup.sh` を実行すると自動生成されます。手動で登録する場合は
`slackbot.service` の `<...>` を実際のパスに書き換えてからコピーしてください。

```sh
sudo cp slackbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable slackbot.service
sudo systemctl start slackbot.service
```

---

## データパス解決（優先順位）

起動時のデータディレクトリは以下の順で決定されます：

1. `--data` オプション: `python3 slackbot.py --data /mnt/usbdata/slackbot`
2. 環境変数: `export SLACKBOT_DATA=/mnt/usbdata/slackbot`
3. `.env` ファイル: `setup.sh` が自動生成
4. スクリプトと同じディレクトリ（フォールバック）

---

## config.yaml

```yaml
bot:
  command: "local"        # スラッシュコマンド名 → /local

slack:
  bot_token: "xoxb-***"
  app_token: "xapp-***"
  notify_user: "@your_user"
  notify_user_id: "U12345678"

network:
  cidr: "192.168.1.0/24"

hosts:
  raspberrypi:
    ip: "192.168.1.10"
    self: true               # この Bot が動作しているホスト

  desktop:
    ip: "192.168.1.20"
    mac: "11:22:33:44:55:66"  # wol/watch 用（任意）
    os: "windows"             # shutdown/reboot 用（任意）
    user: "your_user"
    password: "your_password"

ports:
  ssh: 22
  rdp: 3389
  smb: 445

timeout:
  minutes: 30             # 0 = 無操作シャットダウン無効（手動のみ）

watch:
  fail_threshold: 3
  interval: 10
```

詳細なコメント付きのサンプルは `config_sample.yaml` を参照してください。

---

## 使い方

Slack の **スラッシュコマンド**で操作します。コマンド名は `config.yaml` の `bot.command` で変更できます（デフォルト: `local`）。

```
/local help
/local status
/local scan
...
```

App Home タブにコマンド一覧が表示されます。

---

## コマンド一覧

| コマンド | 説明 |
|---------|------|
| `/local help` | コマンド一覧を表示 |
| `/local status` | ホストの状態を表示（CPU温度・メモリ・ディスク・稼働時間） |
| `/local scan` | LAN をスキャンして表形式で表示 |
| `/local wol <name>` | 指定ホストに Wake-on-LAN（起動確認＋ポート確認） |
| `/local speedtest` | 回線速度を測定（履歴・統計付き） |
| `/local extend <分>` | 自動シャットダウンまでの時間を設定（上書き）。`0` で無効化、再度正の値で再有効化 |
| `/local watch <ip\|name>` | 指定ホストの疎通監視を開始（IP またはホスト名） |
| `/local unwatch <ip\|name>` | 監視解除 |
| `/local watchlist` | 監視中ホスト一覧 |
| `/local shutdown <name>` | 指定ホストをシャットダウン（自ホスト含む、権限チェックあり） |
| `/local reboot <name>` | 指定ホストを再起動（自ホスト含む、権限チェックあり） |

不明なコマンドはあいまいマッチで候補を提示します。

---

## 疎通監視（watch）

```
/local watch desktop
/local watch 192.168.1.20
```

ホスト名・IP どちらでも指定できます。`hosts` に登録されていれば名前で解決されます。

- `fail_threshold` 回連続失敗でオフライン通知
- 復帰時にオンライン通知
- `interval` 秒ごとに ping チェック
- 複数ホストの同時監視可能

```
/local watchlist           # 監視中ホスト一覧
/local unwatch desktop     # 監視解除
```

---

## 無操作シャットダウン

`timeout.minutes` で設定した時間 Slack 操作がなければ自動シャットダウンします。

```
/local extend 60    # 60分後にシャットダウン（上書き設定）
/local extend 0     # このセッション中のみ無効化
/local extend 30    # 無効化中に実行 → 再有効化して30分後にシャットダウン
```

- `extend` は加算ではなく**上書き**。何度でも変更できる
- `extend 0` は起動中のみ有効。再起動すると `timeout.minutes` の設定値に戻る
- `timeout.minutes: 0` で恒久的に無効化（手動 `shutdown` のみ）

---

## speedtest（履歴・統計）

```
/local speedtest
```

返答例：

```
Ping: 14.82 ms
Download: 93.12 Mbit/s
Upload: 8.01 Mbit/s

【過去の統計】
平均DL: 92.10 Mbps  最速DL: 95.30 Mbps  最遅DL: 88.55 Mbps
平均UL: 7.95 Mbps   最速UL: 8.12 Mbps   最遅UL: 7.80 Mbps
```

---

## shutdown / reboot

```
/local shutdown desktop
/local reboot desktop
/local shutdown raspberrypi   # self: true のホスト → ローカル実行
```

`hosts` の設定内容によって動作が変わります。

| `hosts` の設定 | 動作 |
|---|---|
| `self: true` | この Bot が動作しているホストでローカル実行（5秒後に `sudo shutdown` / `sudo reboot`） |
| `os: windows` | `net rpc shutdown`（追加ソフト不要） |
| `os: linux` / `os: macos` | SSH |
| いずれも未設定 | 「shutdown/reboot に対応していません」と返答 |

`notify_user_id` のユーザー以外が実行した場合は権限エラーになります。

`watch` 中のホストに対して実行すると、オフライン誤通知を防ぐため拒否されます。
事前に `/local unwatch <name>` で監視を解除してください。

---

## systemd（自動起動）

`setup.sh` 実行時に自動で登録されます。手動で操作する場合：

```sh
sudo systemctl start   slackbot.service
sudo systemctl stop    slackbot.service
sudo systemctl restart slackbot.service
sudo systemctl status  slackbot.service
journalctl -u slackbot.service -f
```

---

## 起動オプション

```sh
python slackbot.py [--data DIR] [--debug]
```

| オプション | 説明 |
|-----------|------|
| `--data DIR` | データディレクトリを指定（省略時は `.env` またはスクリプトと同じ場所） |
| `--debug` | デバッグログをコンソールに出力（ファイルは INFO 以上のみ） |

---

## バージョン管理

`MAJOR.MINOR.PATCH` のセマンティックバージョニングで管理します。

| 種別 | 上げる桁 |
|------|---------|
| 後方互換のない変更（設定ファイル仕様変更など） | MAJOR |
| 後方互換のある新機能追加 | MINOR |
| バグ修正・文言修正のみ | PATCH |

バグ修正と新機能が混在する場合は MINOR を上げる。

バージョン履歴は `CHANGES.md` を参照してください。

---

## overlayFS 対応

- `slackbot.py` は読み取り専用 root に置く
- `config.yaml` / `logs/` は USB（`/mnt/usbdata/slackbot`）に置く
- USB が抜けていたら起動時にエラーで停止
- 再起動しても設定とログは消えない
