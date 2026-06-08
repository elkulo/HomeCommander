# HomeCommander (Slack LAN Manager Bot)

Raspberry Pi / Ubuntu だけで自宅 LAN を完全管理できる Slack Bot。  
VPN 不要・ポート開放不要・PC 側設定不要。  
overlayFS（読み取り専用 root）でも動作し、設定・ログは USB に永続化される。

---

## 主な機能

- Raspberry Pi の状態監視（CPU温度 / メモリ / ディスク / 稼働時間）
- LAN スキャン（arp-scan → 表形式）
- Wake-on-LAN（起動確認＋ポート確認）
- PC shutdown / reboot（Windows RPC / Linux SSH）
- 回線速度測定（履歴保存＋平均/最速/最遅）
- コマンドログ保存
- 無操作シャットダウン（延長コマンド付き）
- 疎通監視（落下通知・復帰通知・連続失敗閾値・一覧表示）
- USB 永続化（config/logs）
- overlayFS 対応（読み取り専用 root でも動作）

---

## ディレクトリ構成

### Raspberry Pi（読み取り専用 root）

```
/home/pi/HomeCommander/
  slackbot.py
  slackbot.service
  requirements.txt
  setup.sh
```

### USB（永続化領域）

```
/mnt/usbdata/slackbot/
  config.yaml
  logs/
    speedtest.log
    slack_commands.log
```

---

## config.yaml（USB 側）

```yaml
slack:
  bot_token: "xoxb-***"
  app_token: "xapp-***"
  notify_user: "@your_user"
  notify_user_id: "U12345678"

network:
  cidr: "192.168.1.0/24"

hosts:
  desktop:
    ip: "192.168.1.20"
    mac: "11:22:33:44:55:66"

ports:
  ssh: 22
  rdp: 3389
  smb: 445

pc:
  desktop:
    ip: "192.168.1.20"
    os: "windows"
    user: "your_user"
    password: "your_password"

timeout:
  minutes: 30

watch:
  fail_threshold: 3
  interval: 10
```

---

## データパス解決（優先順位）

起動時のデータディレクトリは以下の順で決定されます：

1. `--data` オプション: `python3 slackbot.py --data /mnt/usbdata/slackbot`
2. 環境変数: `export SLACKBOT_DATA=/mnt/usbdata/slackbot`
3. `.env` ファイル: `setup.sh` が自動生成
4. スクリプトと同じディレクトリ（フォールバック）

---

## セットアップ

### セットアップスクリプト（推奨）

Mac・Raspberry Pi OS を自動判別して環境を構築します。

```sh
sh setup.sh
```

実行内容：
- システムパッケージのインストール（Homebrew / apt）
- Python 仮想環境（venv）の作成
- pip パッケージのインストール
- データディレクトリの作成と config.yaml のコピー
- `.env` へのデータパス保存
- systemd サービスの登録・有効化（Raspberry Pi のみ）

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
cp config_sample.yaml config.yaml   # または任意のディレクトリへ
```

`config.yaml` を編集して Slack トークンとネットワーク情報を設定してください。

#### 4. 起動

```sh
source venv/bin/activate
python slackbot.py
# データディレクトリを指定する場合:
python slackbot.py --data /mnt/usbdata/slackbot
```

#### 5. systemd サービス登録（Raspberry Pi のみ）

`slackbot.service` の `<...>` を実際のパスに書き換えてからコピー:

```sh
sudo cp slackbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable slackbot.service
sudo systemctl start slackbot.service
```

---

## 使い方

Bot との **DM にテキストを送信**して操作します。スラッシュは不要です。

## コマンド一覧

```
help               : このヘルプを表示
status             : Raspberry Pi の状態を表示
scan               : LAN をスキャンして表形式で表示
wol <name>         : 指定ホストに Wake-on-LAN
speedtest          : 回線速度を測定（履歴＋統計）
extend <分>        : 無操作シャットダウンを延長
watch <ip>         : 指定IPを疎通監視
unwatch <ip|name>  : 監視解除
watchlist          : 監視中ホスト一覧
pc shutdown <name> : PC をシャットダウン
pc reboot <name>   : PC を再起動
shutdown           : Raspberry Pi をシャットダウン
reboot             : Raspberry Pi を再起動
```

---

## 疎通監視（watch/unwatch/watchlist）

### 監視開始

```
watch 192.168.1.20
```

### 監視解除

```
unwatch desktop
```

### 一覧表示

```
watchlist
```

### 仕様

連続失敗回数（fail_threshold）で落下判定  
復帰通知あり  
監視間隔（interval）設定可能  
複数監視可能  
スレッド安全停止  

---

## speedtest（履歴＋統計）

```
speedtest
```

返答例：

```
Ping: 14.82 ms
Download: 93.12 Mbit/s
Upload: 8.01 Mbit/s

【過去の統計】
平均DL: 92.10 Mbps
最速DL: 95.30 Mbps
最遅DL: 88.55 Mbps
平均UL: 7.95 Mbps
最速UL: 8.12 Mbps
最遅UL: 7.80 Mbps
```

---

## PC 管理（PC 側設定不要）

### シャットダウン

```
pc shutdown desktop
```

### 再起動

```
pc reboot desktop
```

Windows → RPC  
Linux/macOS → SSH  
追加ソフト不要

---

## 無操作シャットダウン（延長可能）

### 延長

```
extend 60
```

→ 無操作シャットダウンを +60 分延長

---

## LAN スキャン

```
scan
```

arp-scan を実行し、表形式で返す。

---

## Raspberry Pi 状況監視

```
status
```

CPU温度 / CPU使用率 / メモリ / ディスク / 稼働時間 を返す。

---

## systemd（自動起動）

`setup.sh` 実行時に自動で登録されます。手動で操作する場合：

```bash
sudo systemctl start   slackbot.service
sudo systemctl stop    slackbot.service
sudo systemctl restart slackbot.service
sudo systemctl status  slackbot.service
journalctl -u slackbot.service -f
```

---

## overlayFS 対応

- slackbot.py は読み取り専用 root に置く  
- config.yaml / logs は USB に置く  
- USB が抜けていたら Slack にエラー返す  
- 再起動しても設定とログは消えない
