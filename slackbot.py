import os
import argparse
import yaml
import time
import threading
import subprocess
import socket
import difflib
from datetime import datetime, timedelta

import psutil
from wakeonlan import send_magic_packet
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


# -------------------------
# データパス解決（CLI > ENV > デフォルト）
# -------------------------
def resolve_data_path():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", help="config.yaml と logs を置くディレクトリ")
    args, _ = parser.parse_known_args()

    if args.data:
        return os.path.abspath(args.data)

    env_path = os.environ.get("SLACKBOT_DATA")
    if env_path:
        return env_path

    # .env ファイルから読み込む（setup.sh が生成）
    dot_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(dot_env):
        with open(dot_env) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "SLACKBOT_DATA":
                        return v.strip()

    return os.path.dirname(os.path.abspath(__file__))


DATA_BASE = resolve_data_path()
CONFIG_PATH = f"{DATA_BASE}/config.yaml"
LOG_DIR = f"{DATA_BASE}/logs"

if not os.path.exists(DATA_BASE):
    raise RuntimeError(f"データディレクトリが存在しません: {DATA_BASE}")

os.makedirs(LOG_DIR, exist_ok=True)


# -------------------------
# 設定読み込み
# -------------------------
def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(f"config.yaml が見つかりません: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


cfg = load_config()
app = App(token=cfg["slack"]["bot_token"])

CMD = "/" + cfg.get("bot", {}).get("command", "local")

start_time = datetime.now()
last_activity = datetime.now()
extend_minutes = 0
watch_targets = {}


# -------------------------
# ログ
# -------------------------
def log_command(user, text):
    with open(f"{LOG_DIR}/slack_commands.log", "a") as f:
        f.write(f"{datetime.now()}  user={user}  cmd={text}\n")


def log_speedtest(result):
    with open(f"{LOG_DIR}/speedtest.log", "a") as f:
        f.write(f"{datetime.now()}\n{result}\n\n")


def load_speedtest_history():
    path = f"{LOG_DIR}/speedtest.log"
    if not os.path.exists(path):
        return []

    results = []
    with open(path, "r") as f:
        block = []
        for line in f:
            if line.strip() == "":
                if block:
                    results.append("\n".join(block))
                    block = []
            else:
                block.append(line.strip())
        if block:
            results.append("\n".join(block))

    return results


def parse_speedtest_value(block, key):
    for line in block.splitlines():
        if line.startswith(key):
            return float(line.split()[1])
    return None


def calc_speedtest_stats():
    history = load_speedtest_history()
    if not history:
        return None

    downloads = []
    uploads = []

    for block in history:
        dl = parse_speedtest_value(block, "Download:")
        ul = parse_speedtest_value(block, "Upload:")
        if dl is not None:
            downloads.append(dl)
        if ul is not None:
            uploads.append(ul)

    if not downloads:
        return None

    return {
        "avg_dl": sum(downloads) / len(downloads),
        "max_dl": max(downloads),
        "min_dl": min(downloads),
        "avg_ul": sum(uploads) / len(uploads),
        "max_ul": max(uploads),
        "min_ul": min(uploads),
    }


# -------------------------
# Raspberry Pi 状況監視
# -------------------------
def get_cpu_temp():
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"]).decode()
        return out.replace("temp=", "").strip()
    except Exception:
        return "取得不可"


def get_status():
    cpu_temp = get_cpu_temp()
    cpu_usage = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    uptime_seconds = time.time() - start_time.timestamp()
    uptime = str(timedelta(seconds=int(uptime_seconds)))

    return (
        "*Raspberry Pi 状況*\n"
        "```\n"
        f"CPU温度     : {cpu_temp}\n"
        f"CPU使用率   : {cpu_usage}%\n"
        f"メモリ使用率: {mem.percent}%\n"
        f"ディスク使用: {disk.used // (1024**3)}GB / {disk.total // (1024**3)}GB\n"
        f"稼働時間    : {uptime}\n"
        "```"
    )


# -------------------------
# LAN スキャン
# -------------------------
def scan_network(cidr):
    result = subprocess.run(
        ["sudo", "arp-scan", cidr],
        capture_output=True,
        text=True
    )
    return result.stdout


def parse_arp_scan(output):
    devices = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].count(".") == 3:
            ip = parts[0]
            mac = parts[1]
            vendor = " ".join(parts[2:])
            devices.append((ip, mac, vendor))
    return devices


def format_table(devices):
    if not devices:
        return "デバイスが見つかりませんでした。"

    header = f"{'IP':<16} {'MAC':<20} Vendor"
    rows = [header]

    for ip, mac, vendor in devices:
        rows.append(f"{ip:<16} {mac:<20} {vendor}")

    return "```\n" + "\n".join(rows) + "\n```"


# -------------------------
# WOL + 起動確認 + ポート確認
# -------------------------
def wait_for_ping(ip, retries=10, interval=2):
    for _ in range(retries):
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        if result.returncode == 0:
            return True
        time.sleep(interval)
    return False


def wol(mac):
    send_magic_packet(mac)


def check_port(ip, port, timeout=1):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def check_ports(ip, ports):
    results = {}
    for name, port in ports.items():
        results[name] = check_port(ip, port)
    return results


# -------------------------
# speedtest
# -------------------------
def run_speedtest():
    try:
        result = subprocess.check_output(
            ["speedtest-cli", "--simple"],
            stderr=subprocess.STDOUT
        ).decode()
        return result
    except Exception as e:
        return f"速度測定エラー: {e}"

# -------------------------
# 起動通知
# -------------------------
def notify_start():
    user = cfg["slack"]["notify_user_id"]
    app.client.chat_postMessage(
        channel=user,
        text=f"Raspberry Pi が起動しました。\n起動時刻: {start_time.strftime('%Y-%m-%d %H:%M:%S')}"
    )


# -------------------------
# 無操作シャットダウン監視（延長対応）
# -------------------------
def timeout_watcher():
    global last_activity, extend_minutes
    timeout_minutes = cfg["timeout"]["minutes"]

    while True:
        now = datetime.now()
        elapsed = now - last_activity
        total_timeout = timeout_minutes + extend_minutes

        if elapsed > timedelta(minutes=total_timeout):
            uptime = now - start_time
            user = cfg["slack"]["notify_user_id"]

            app.client.chat_postMessage(
                channel=user,
                text=f"⚠️ Slack 無操作が {total_timeout} 分を超えました。\n"
                     f"起動時間: {uptime}\n"
                     f"シャットダウンします。"
            )

            subprocess.run(["sudo", "shutdown", "-h", "now"])
            return

        time.sleep(60)


# -------------------------
# 疎通監視スレッド
# -------------------------
def watch_ip(ip, name, stop_event):
    user = cfg["slack"]["notify_user_id"]
    threshold = cfg.get("watch", {}).get("fail_threshold", 3)
    interval = cfg.get("watch", {}).get("interval", 10)

    last_state = "online"
    fail_count = 0

    while not stop_event.is_set():
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        is_online = (result.returncode == 0)

        if is_online:
            if last_state == "offline":
                app.client.chat_postMessage(
                    channel=user,
                    text=f"🎉 {name} ({ip}) がオンラインに復帰しました"
                )
            last_state = "online"
            fail_count = 0
        else:
            fail_count += 1
            if fail_count >= threshold and last_state == "online":
                app.client.chat_postMessage(
                    channel=user,
                    text=f"⚠️ {name} ({ip}) がオフラインになりました（{fail_count}回連続失敗）"
                )
                last_state = "offline"

        try:
            watch_targets[ip]["last_state"] = last_state
            watch_targets[ip]["fail_count"] = fail_count
        except KeyError:
            break

        stop_event.wait(interval)


# -------------------------
# スラッシュコマンド処理
# -------------------------
@app.command(CMD)
def handle_command(ack, say, command):
    global last_activity, extend_minutes

    # Slack の 3 秒タイムアウト要件を満たすため最初に ack
    ack()

    last_activity = datetime.now()
    text = command.get("text", "").strip()
    user_id = command.get("user_id")

    log_command(user_id, f"{CMD} {text}")

    # help
    if text in ("", "help"):
        say(
            f"*利用可能なコマンド一覧* (`{CMD} <サブコマンド>`)\n"
            "```\n"
            "help               : このヘルプを表示\n"
            "status             : Raspberry Pi の状態を表示\n"
            "scan               : LAN をスキャンして表形式で表示\n"
            "wol <name>         : 指定ホストに Wake-on-LAN\n"
            "speedtest          : 回線速度を測定（履歴＋統計）\n"
            "extend <分>        : 無操作シャットダウンを延長\n"
            "watch <ip>         : 指定IPを疎通監視\n"
            "unwatch <ip|name>  : 監視解除\n"
            "watchlist          : 監視中ホスト一覧\n"
            "pc shutdown <name> : PC をシャットダウン\n"
            "pc reboot <name>   : PC を再起動\n"
            "shutdown           : Raspberry Pi をシャットダウン\n"
            "reboot             : Raspberry Pi を再起動\n"
            "```"
        )
        return

    # status
    if text == "status":
        say(get_status())
        return

    # speedtest（時間がかかるが ack() 済みなので OK）
    if text == "speedtest":
        say("回線速度を測定しています…（30秒ほどかかります）")
        result = run_speedtest()
        log_speedtest(result)
        stats = calc_speedtest_stats()

        msg = f"```\n{result}\n```"
        if stats:
            msg += (
                "*【過去の統計】*\n"
                f"平均DL: {stats['avg_dl']:.2f} Mbps\n"
                f"最速DL: {stats['max_dl']:.2f} Mbps\n"
                f"最遅DL: {stats['min_dl']:.2f} Mbps\n"
                f"平均UL: {stats['avg_ul']:.2f} Mbps\n"
                f"最速UL: {stats['max_ul']:.2f} Mbps\n"
                f"最遅UL: {stats['min_ul']:.2f} Mbps\n"
            )

        say(msg)
        return

    # extend <minutes>
    if text.startswith("extend "):
        try:
            mins = int(text.split(" ", 1)[1])
            extend_minutes += mins
            say(f"⏱️ 無操作シャットダウンを {mins} 分延長しました。（合計 +{extend_minutes} 分）")
        except Exception:
            say(f"形式: `{CMD} extend <分>`")
        return

    # Raspberry Pi reboot
    if text == "reboot":
        if user_id != cfg["slack"]["notify_user_id"]:
            say("権限がありません。")
            return
        say("🔄 再起動します。5秒後に実行します。")
        def _do_reboot():
            time.sleep(5)
            subprocess.run(["sudo", "reboot"])
        threading.Thread(target=_do_reboot, daemon=True).start()
        return

    # Raspberry Pi shutdown
    if text == "shutdown":
        if user_id != cfg["slack"]["notify_user_id"]:
            say("権限がありません。")
            return
        say("⚠️ シャットダウンします。5秒後に実行します。")
        def _do_shutdown():
            time.sleep(5)
            subprocess.run(["sudo", "shutdown", "-h", "now"])
        threading.Thread(target=_do_shutdown, daemon=True).start()
        return

    # pc shutdown
    if text.startswith("pc shutdown "):
        name = text.split(" ", 2)[2]
        pcs = cfg.get("pc", {})

        if name not in pcs:
            say(f"`{name}` は config.yaml にありません")
            return

        pc = pcs[name]
        ip = pc["ip"]

        say(f"{name} をシャットダウンします…")

        try:
            if pc["os"] == "windows":
                cmd = [
                    "net", "rpc", "shutdown",
                    "-I", ip,
                    "-U", f'{pc["user"]}%{pc["password"]}'
                ]
            else:
                cmd = ["ssh", f'{pc["user"]}@{ip}', "sudo shutdown -h now"]

            subprocess.run(cmd, timeout=5)
            say(f"🛑 {name} をシャットダウンしました")
        except Exception as e:
            say(f"⚠️ シャットダウン失敗: {e}")
        return

    # pc reboot
    if text.startswith("pc reboot "):
        name = text.split(" ", 2)[2]
        pcs = cfg.get("pc", {})

        if name not in pcs:
            say(f"`{name}` は config.yaml にありません")
            return

        pc = pcs[name]
        ip = pc["ip"]

        say(f"{name} を再起動します…")

        try:
            if pc["os"] == "windows":
                cmd = [
                    "net", "rpc", "shutdown", "-r",
                    "-I", ip,
                    "-U", f'{pc["user"]}%{pc["password"]}'
                ]
            else:
                cmd = ["ssh", f'{pc["user"]}@{ip}', "sudo reboot"]

            subprocess.run(cmd, timeout=5)
            say(f"🔄 {name} を再起動しました")
        except Exception as e:
            say(f"⚠️ 再起動失敗: {e}")
        return

    # scan
    if text == "scan":
        raw = scan_network(cfg["network"]["cidr"])
        devices = parse_arp_scan(raw)
        table = format_table(devices)
        say(table)
        return

    # wol
    if text.startswith("wol "):
        name = text.split(" ", 1)[1]
        hosts = cfg.get("hosts", {})

        if name not in hosts:
            say(f"`{name}` は config.yaml にありません")
            return

        ip = hosts[name]["ip"]
        mac = hosts[name]["mac"]

        say(f"{name} に WOL を送信しました。起動確認中…")
        wol(mac)

        if wait_for_ping(ip):
            say(f"🎉 {name} が起動しました（ping 応答あり）")
        else:
            say(f"⚠️ {name} は起動しませんでした（ping 応答なし）")
            return

        ports = cfg.get("ports", {})
        results = check_ports(ip, ports)

        msg = "*ポート状態*\n```\n"
        for pname, ok in results.items():
            msg += f"{pname:<10}: {'OPEN' if ok else 'CLOSED'}\n"
        msg += "```"

        say(msg)
        return

    # watch <ip>
    if text.startswith("watch "):
        ip = text.split(" ", 1)[1].strip()

        name = ip
        for host_name, info in cfg.get("hosts", {}).items():
            if info["ip"] == ip:
                name = host_name

        if ip in watch_targets:
            say(f"{name} ({ip}) はすでに監視中です。")
            return

        say(f"{name} ({ip}) の監視を開始します。落ちたら通知し、復帰時も通知します。")

        stop_event = threading.Event()
        t = threading.Thread(target=watch_ip, args=(ip, name, stop_event), daemon=True)

        watch_targets[ip] = {
            "thread": t,
            "stop": stop_event,
            "name": name,
            "last_state": "online",
            "fail_count": 0
        }

        t.start()
        return

    # unwatch <ip or name>
    if text.startswith("unwatch "):
        target = text.split(" ", 1)[1].strip()

        ip = target
        for host_name, info in cfg.get("hosts", {}).items():
            if host_name == target:
                ip = info["ip"]

        if ip not in watch_targets:
            say(f"`{target}` は監視されていません。")
            return

        entry = watch_targets[ip]
        entry["stop"].set()
        del watch_targets[ip]

        say(f"🛑 {entry['name']} ({ip}) の監視を解除しました。")
        return

    # watchlist
    if text == "watchlist":
        if not watch_targets:
            say("監視中のホストはありません。")
            return

        msg = "*監視中のホスト:*\n"
        for ip, info in watch_targets.items():
            msg += (
                f"- {info['name']} ({ip}) "
                f"状態: {info['last_state']} "
                f"失敗回数: {info['fail_count']}\n"
            )

        say(msg)
        return

    # あいまいマッチ
    candidates = [
        "help", "status", "scan", "speedtest", "watchlist", "shutdown", "reboot",
        "wol", "extend", "watch", "unwatch", "pc shutdown", "pc reboot",
    ]

    if text.startswith("pc "):
        match_text = " ".join(text.split()[:2])
    else:
        match_text = text.split()[0] if text.split() else text

    close = difflib.get_close_matches(match_text, candidates, n=1, cutoff=0.6)
    if close:
        say(f"`{text}` は不明なコマンドです。`{CMD} {close[0]}` ですか？")
    else:
        say(f"不明なコマンドです。`{CMD} help` を実行してください。")


# -------------------------
# App Home タブ
# -------------------------
HOME_BLOCKS = [
    {
        "type": "header",
        "text": {"type": "plain_text", "text": "🏠 HomeCommander"},
    },
    {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"Raspberry Pi LAN 管理ボット\n`{CMD} <サブコマンド>` で操作します。"},
    },
    {"type": "divider"},
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*📡 監視・情報*\n"
                f"```"
                f"{CMD} status       Raspberry Pi の状態\n"
                f"{CMD} scan         LAN スキャン（表形式）\n"
                f"{CMD} speedtest    回線速度測定（履歴付き）"
                "```"
            ),
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*💻 PC 操作*\n"
                f"```"
                f"{CMD} wol <name>           Wake-on-LAN\n"
                f"{CMD} pc shutdown <name>   シャットダウン\n"
                f"{CMD} pc reboot <name>     再起動"
                "```"
            ),
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*👁 疎通監視*\n"
                f"```"
                f"{CMD} watch <ip>           監視開始\n"
                f"{CMD} unwatch <ip|name>    監視解除\n"
                f"{CMD} watchlist            監視中ホスト一覧"
                "```"
            ),
        },
    },
    {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*⚙️ システム*\n"
                f"```"
                f"{CMD} extend <分>   タイムアウト延長\n"
                f"{CMD} shutdown      Pi シャットダウン\n"
                f"{CMD} reboot        Pi 再起動"
                "```"
            ),
        },
    },
    {"type": "divider"},
    {
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": "不明なコマンドはあいまいマッチで候補を提示します。"},
        ],
    },
]


@app.event("app_home_opened")
def handle_app_home_opened(client, event):
    client.views_publish(
        user_id=event["user"],
        view={"type": "home", "blocks": HOME_BLOCKS},
    )


# -------------------------
# メイン
# -------------------------
if __name__ == "__main__":
    notify_start()

    watcher = threading.Thread(target=timeout_watcher, daemon=True)
    watcher.start()

    handler = SocketModeHandler(app, cfg["slack"]["app_token"])
    try:
        handler.start()
    except KeyboardInterrupt:
        print("\n停止しました。")
        handler.close()