import os
import sys
import platform
import logging
import argparse
import yaml
import time
import threading
import subprocess
import socket
import difflib
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta

import psutil
from wakeonlan import send_magic_packet
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler


# -------------------------
# CLI 引数パース
# -------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        prog="slackbot.py",
        description=(
            "HomeCommander - Raspberry Pi LAN 管理 Slack Bot\n"
            "Slack のスラッシュコマンド経由で自宅 LAN を管理します。\n"
            "VPN 不要・ポート開放不要・Socket Mode で動作。"
        ),
        epilog=(
            "データディレクトリの解決順:\n"
            "  1. --data オプション\n"
            "  2. 環境変数 SLACKBOT_DATA\n"
            "  3. .env ファイル (setup.sh が生成)\n"
            "  4. スクリプトと同じディレクトリ（フォールバック）\n"
            "\n"
            "使用例:\n"
            "  python slackbot.py\n"
            "  python slackbot.py --debug\n"
            "  python slackbot.py --data /mnt/usbdata/slackbot\n"
            "  SLACKBOT_DATA=/mnt/usbdata/slackbot python slackbot.py"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data",
        metavar="DIR",
        help="config.yaml と logs を置くディレクトリ（省略時は .env またはスクリプトと同じ場所）",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="デバッグログを有効化（詳細な動作ログを出力）",
    )
    return parser.parse_known_args()[0]


args = parse_args()


# -------------------------
# .env 読み込み（全変数を os.environ に反映、既存の環境変数は上書きしない）
# -------------------------
def load_dot_env():
    dot_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(dot_env):
        return
    with open(dot_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_dot_env()


# -------------------------
# データパス解決（CLI > ENV > デフォルト）
# -------------------------
def resolve_data_path():
    if args.data:
        return os.path.abspath(args.data)
    env_path = os.environ.get("SLACKBOT_DATA")
    if env_path:
        return env_path
    return os.path.dirname(os.path.abspath(__file__))


DATA_BASE = resolve_data_path()
CONFIG_PATH = f"{DATA_BASE}/config.yaml"
LOG_DIR = f"{DATA_BASE}/logs"

if not os.path.exists(DATA_BASE):
    raise RuntimeError(f"データディレクトリが存在しません: {DATA_BASE}")

os.makedirs(LOG_DIR, exist_ok=True)


# -------------------------
# ロギング設定
# -------------------------
def setup_logging(log_dir: str, debug: bool = False) -> logging.Logger:
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger = logging.getLogger("homecommander")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # ファイルハンドラ: SD カード保護のため常に INFO 以上のみ書き込む
    # DEBUG ログはコンソールにのみ出力し、ディスク書き込みを抑制する
    fh = RotatingFileHandler(
        f"{log_dir}/slackbot.log",
        maxBytes=1024 * 1024,  # 1MB
        backupCount=5,          # 最大 5MB
        encoding="utf-8",
    )
    fh.setLevel(logging.INFO)   # debug フラグに関わらずファイルは INFO 以上
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # コンソールハンドラ: --debug 時は DEBUG も表示
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if debug else logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Slack Bolt / SDK のログは debug 時のみ表示
    bolt_level = logging.DEBUG if debug else logging.WARNING
    logging.getLogger("slack_bolt").setLevel(bolt_level)
    logging.getLogger("slack_sdk").setLevel(bolt_level)

    return logger


logger = setup_logging(LOG_DIR, args.debug)


# -------------------------
# 設定読み込み
# -------------------------
def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError(f"config.yaml が見つかりません: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


cfg = load_config()
logger.debug("config.yaml 読み込み完了: %s", CONFIG_PATH)

app = App(token=cfg["slack"]["bot_token"])

CMD = "/" + cfg.get("bot", {}).get("command", "local")

start_time = datetime.now()
last_activity = datetime.now()
extend_minutes = 0
watch_targets = {}


# -------------------------
# speedtest ログ（統計計算用に独立ファイルを維持）
# ローテーション: 512KB × 3世代（手動実行なので小さめ）
# -------------------------
SPEEDTEST_LOG_PATH = f"{LOG_DIR}/speedtest.log"


def log_speedtest(result):
    # plain open + 手動ローテーション（RotatingFileHandler は LogRecord 前提で使いにくいため）
    entry = f"{datetime.now()}\n{result}\n\n"
    with open(SPEEDTEST_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)
    # 512KB を超えたらローテーション
    _rotate_speedtest_log(SPEEDTEST_LOG_PATH, max_bytes=512 * 1024, backup_count=3)


def _rotate_speedtest_log(path, max_bytes, backup_count):
    if not os.path.exists(path):
        return
    if os.path.getsize(path) < max_bytes:
        return
    # .3 → 削除、.2 → .3、.1 → .2、元 → .1
    for i in range(backup_count - 1, 0, -1):
        src = f"{path}.{i}"
        dst = f"{path}.{i + 1}"
        if os.path.exists(src):
            os.replace(src, dst)
    os.replace(path, f"{path}.1")


def load_speedtest_history():
    # 最新ファイルから順にすべてのローテーション済みファイルも読む
    paths = [SPEEDTEST_LOG_PATH] + [f"{SPEEDTEST_LOG_PATH}.{i}" for i in range(1, 4)]

    results = []
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
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

    result = {
        "avg_dl": sum(downloads) / len(downloads),
        "max_dl": max(downloads),
        "min_dl": min(downloads),
    }
    if uploads:
        result.update({
            "avg_ul": sum(uploads) / len(uploads),
            "max_ul": max(uploads),
            "min_ul": min(uploads),
        })
    return result


# -------------------------
# Raspberry Pi 状況監視
# -------------------------
def get_cpu_temp():
    # Raspberry Pi: vcgencmd measure_temp → "47.2'C"
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"], stderr=subprocess.DEVNULL).decode()
        return out.replace("temp=", "").strip()
    except Exception:
        pass
    # Linux 汎用 (Ubuntu など): /sys/class/thermal/thermal_zone0/temp
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            millideg = int(f.read().strip())
        return f"{millideg / 1000:.1f}'C"
    except Exception:
        pass
    return "取得不可"


def get_status():
    cpu_temp = get_cpu_temp()
    cpu_usage = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk_root = psutil.disk_usage("/")
    uptime_seconds = time.time() - start_time.timestamp()
    hours, rem = divmod(int(uptime_seconds), 3600)
    minutes, seconds = divmod(rem, 60)
    uptime = f"{hours}時間{minutes}分{seconds}秒"

    lines = [
        "*HomeCommander 状況*",
        "```",
        f"CPU温度     : {cpu_temp}",
        f"CPU使用率   : {cpu_usage}%",
        f"メモリ使用率: {mem.percent}%",
        f"ディスク(/) : {disk_root.used // (1024**3)}GB / {disk_root.total // (1024**3)}GB ({disk_root.percent}%)",
    ]

    # DATA_BASE がルートと別パーティション（USB 等）ならディスク使用量を追加表示
    try:
        disk_data = psutil.disk_usage(DATA_BASE)
        if disk_data.device != disk_root.device if hasattr(disk_data, "device") else disk_data.total != disk_root.total:
            lines.append(
                f"ディスク(data): {disk_data.used // (1024**3)}GB / {disk_data.total // (1024**3)}GB ({disk_data.percent}%)"
            )
    except Exception:
        pass

    lines += [f"稼働時間    : {uptime}", "```"]
    return "\n".join(lines)


# -------------------------
# LAN スキャン
# -------------------------
def scan_network(cidr):
    logger.debug("arp-scan 実行: %s", cidr)
    result = subprocess.run(
        ["sudo", "arp-scan", cidr],
        capture_output=True,
        text=True,
        timeout=30,
    )
    logger.debug("arp-scan 出力:\n%s", result.stdout)
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
    for i in range(retries):
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        if result.returncode == 0:
            logger.debug("ping OK: %s (%d回目)", ip, i + 1)
            return True
        logger.debug("ping 失敗: %s (%d/%d)", ip, i + 1, retries)
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
        ok = check_port(ip, port)
        results[name] = ok
        logger.debug("ポート確認 %s %s:%d -> %s", name, ip, port, "OPEN" if ok else "CLOSED")
    return results


# -------------------------
# speedtest
# -------------------------
def run_speedtest():
    logger.info("speedtest 開始")
    try:
        result = subprocess.check_output(
            ["speedtest-cli", "--simple"],
            stderr=subprocess.STDOUT,
            timeout=120,
        ).decode()
        logger.debug("speedtest 結果:\n%s", result.strip())
        return result
    except subprocess.TimeoutExpired:
        logger.error("speedtest タイムアウト (120秒)")
        return None
    except Exception as e:
        logger.error("speedtest 失敗: %s", e)
        return None


# -------------------------
# スリープ抑制
# -------------------------
def start_sleep_inhibitor():
    system = platform.system()

    if system == "Darwin":
        try:
            # -i: システムスリープ防止  -w: 指定 PID が終了したら caffeinate も終了
            proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
            logger.info("スリープ抑制 有効 (caffeinate PID=%d)", proc.pid)
            return proc
        except FileNotFoundError:
            logger.warning("caffeinate が見つかりません。スリープ抑制をスキップします。")

    elif system == "Linux":
        if os.environ.get("SLACKBOT_PREVENT_SLEEP", "false").lower() == "true":
            try:
                proc = subprocess.Popen([
                    "systemd-inhibit",
                    "--what=sleep",
                    "--who=HomeCommander",
                    "--why=Slack Bot running",
                    "--mode=block",
                    "sleep", "infinity",
                ])
                logger.info("スリープ抑制 有効 (systemd-inhibit PID=%d)", proc.pid)
                return proc
            except FileNotFoundError:
                logger.warning("systemd-inhibit が見つかりません。スリープ抑制をスキップします。")
        else:
            logger.info("スリープ抑制 無効 (Linux / SLACKBOT_PREVENT_SLEEP=false)")

    return None


# -------------------------
# 起動通知
# -------------------------
def notify_start():
    logger.info("起動通知を送信")
    try:
        user = cfg["slack"]["notify_user_id"]
        hostname = socket.gethostname()
        app.client.chat_postMessage(
            channel=user,
            text=f"✅ HomeCommander が起動しました。\nホスト: {hostname}\n起動時刻: {start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        logger.warning("起動通知の送信に失敗しました: %s", e)


# -------------------------
# 無操作シャットダウン監視（延長対応）
# -------------------------
def timeout_watcher():
    global last_activity, extend_minutes
    timeout_minutes = cfg.get("timeout", {}).get("minutes", 30)

    if timeout_minutes == 0:
        logger.info("無操作シャットダウン: 無効 (minutes=0)")
        return

    logger.info("無操作シャットダウン監視 開始 (タイムアウト: %d 分)", timeout_minutes)

    while True:
        now = datetime.now()
        elapsed = now - last_activity
        total_timeout = timeout_minutes + extend_minutes

        if elapsed > timedelta(minutes=total_timeout):
            uptime = now - start_time
            user = cfg["slack"]["notify_user_id"]

            logger.warning("無操作タイムアウト (%d 分)。シャットダウンします。", total_timeout)

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

    logger.info("疎通監視 開始: %s (%s) threshold=%d interval=%ds", name, ip, threshold, interval)

    while not stop_event.is_set():
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        is_online = (result.returncode == 0)

        if is_online:
            if last_state == "offline":
                logger.info("疎通復帰: %s (%s)", name, ip)
                app.client.chat_postMessage(
                    channel=user,
                    text=f"🎉 {name} ({ip}) がオンラインに復帰しました"
                )
            last_state = "online"
            fail_count = 0
        else:
            fail_count += 1
            logger.debug("疎通失敗: %s (%s) %d/%d", name, ip, fail_count, threshold)
            if fail_count >= threshold and last_state == "online":
                logger.warning("疎通断: %s (%s) %d回連続失敗", name, ip, fail_count)
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

    logger.info("疎通監視 停止: %s (%s)", name, ip)


# -------------------------
# message イベントの未処理警告を抑制
# -------------------------
@app.event("message")
def handle_message_events(body):
    pass  # スラッシュコマンド移行後は DM メッセージを処理しない


# -------------------------
# スラッシュコマンド処理
# -------------------------
@app.command(CMD)
def handle_command(ack, say, command):
    global last_activity, extend_minutes

    ack()

    last_activity = datetime.now()
    text = command.get("text", "").strip()
    user_id = command.get("user_id")

    logger.info("COMMAND user=%s cmd=%s %s", user_id, CMD, text)

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
            "watch <ip|name>    : 指定ホストを疎通監視（IP またはホスト名）\n"
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
        logger.debug("status 取得")
        say(get_status())
        return

    # speedtest
    if text == "speedtest":
        say("回線速度を測定しています…（30秒ほどかかります）")
        result = run_speedtest()

        if result is None:
            say("⚠️ 速度測定に失敗しました。ログを確認してください。")
            return

        log_speedtest(result)  # 成功時のみ記録
        logger.info("speedtest 完了")
        stats = calc_speedtest_stats()

        msg = f"```\n{result}\n```"
        if stats:
            ul_lines = ""
            if "avg_ul" in stats:
                ul_lines = (
                    f"平均UL: {stats['avg_ul']:.2f} Mbps\n"
                    f"最速UL: {stats['max_ul']:.2f} Mbps\n"
                    f"最遅UL: {stats['min_ul']:.2f} Mbps\n"
                )
            msg += (
                "*【過去の統計】*\n"
                f"平均DL: {stats['avg_dl']:.2f} Mbps\n"
                f"最速DL: {stats['max_dl']:.2f} Mbps\n"
                f"最遅DL: {stats['min_dl']:.2f} Mbps\n"
                + ul_lines
            )

        say(msg)
        return

    # extend <minutes>
    if text.startswith("extend "):
        try:
            mins = int(text.split(" ", 1)[1])
            if mins <= 0:
                say(f"延長時間は 1 以上の整数で指定してください。例: `{CMD} extend 60`")
                return
            extend_minutes += mins
            logger.info("タイムアウト延長: +%d 分 (合計 +%d 分)", mins, extend_minutes)
            say(f"⏱️ 無操作シャットダウンを {mins} 分延長しました。（合計 +{extend_minutes} 分）")
        except ValueError:
            say(f"形式: `{CMD} extend <分>`  例: `{CMD} extend 60`")
        return

    # Raspberry Pi reboot
    if text == "reboot":
        if user_id != cfg["slack"]["notify_user_id"]:
            logger.warning("権限エラー: reboot user=%s", user_id)
            say("権限がありません。")
            return
        logger.warning("Pi 再起動 実行 user=%s", user_id)
        say("🔄 再起動します。5秒後に実行します。")
        def _do_reboot():
            time.sleep(5)
            subprocess.run(["sudo", "reboot"])
        threading.Thread(target=_do_reboot, daemon=True).start()
        return

    # Raspberry Pi shutdown
    if text == "shutdown":
        if user_id != cfg["slack"]["notify_user_id"]:
            logger.warning("権限エラー: shutdown user=%s", user_id)
            say("権限がありません。")
            return
        logger.warning("Pi シャットダウン 実行 user=%s", user_id)
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
            logger.warning("pc shutdown: 未定義ホスト %s", name)
            say(f"`{name}` は config.yaml にありません")
            return

        pc = pcs[name]
        ip = pc["ip"]
        logger.info("PC シャットダウン: %s (%s)", name, ip)
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
            logger.info("PC シャットダウン 完了: %s", name)
            say(f"🛑 {name} をシャットダウンしました")
        except Exception as e:
            logger.error("PC シャットダウン 失敗: %s - %s", name, e)
            say(f"⚠️ シャットダウン失敗: {e}")
        return

    # pc reboot
    if text.startswith("pc reboot "):
        name = text.split(" ", 2)[2]
        pcs = cfg.get("pc", {})

        if name not in pcs:
            logger.warning("pc reboot: 未定義ホスト %s", name)
            say(f"`{name}` は config.yaml にありません")
            return

        pc = pcs[name]
        ip = pc["ip"]
        logger.info("PC 再起動: %s (%s)", name, ip)
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
            logger.info("PC 再起動 完了: %s", name)
            say(f"🔄 {name} を再起動しました")
        except Exception as e:
            logger.error("PC 再起動 失敗: %s - %s", name, e)
            say(f"⚠️ 再起動失敗: {e}")
        return

    # scan
    if text == "scan":
        logger.info("LAN スキャン 開始: %s", cfg["network"]["cidr"])
        raw = scan_network(cfg["network"]["cidr"])
        devices = parse_arp_scan(raw)
        logger.info("LAN スキャン 完了: %d 台検出", len(devices))
        table = format_table(devices)
        say(table)
        return

    # wol
    if text.startswith("wol "):
        name = text.split(" ", 1)[1]
        hosts = cfg.get("hosts", {})

        if name not in hosts:
            logger.warning("wol: 未定義ホスト %s", name)
            say(f"`{name}` は config.yaml にありません")
            return

        ip = hosts[name]["ip"]
        mac = hosts[name]["mac"]

        logger.info("WOL 送信: %s mac=%s ip=%s", name, mac, ip)
        say(f"{name} に WOL を送信しました。起動確認中…")
        wol(mac)

        if wait_for_ping(ip):
            logger.info("WOL 成功: %s ping OK", name)
            say(f"🎉 {name} が起動しました（ping 応答あり）")
        else:
            logger.warning("WOL 失敗: %s ping 応答なし", name)
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

    # watch <ip or name>
    if text.startswith("watch "):
        target = text.split(" ", 1)[1].strip()

        # ホスト名で指定された場合は IP に解決
        hosts = cfg.get("hosts", {})
        if target in hosts:
            ip = hosts[target]["ip"]
            name = target
        else:
            ip = target
            name = ip
            # IP からホスト名を逆引き
            for host_name, info in hosts.items():
                if info["ip"] == ip:
                    name = host_name
                    break

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

        logger.info("疎通監視 解除: %s (%s)", entry["name"], ip)
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
        logger.debug("不明コマンド: '%s' -> '%s' を提案", text, close[0])
        say(f"`{text}` は不明なコマンドです。`{CMD} {close[0]}` ですか？")
    else:
        logger.debug("不明コマンド: '%s' (候補なし)", text)
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
                f"```\n"
                f"{CMD} status       HomeCommander の状態\n"
                f"{CMD} scan         LAN スキャン（表形式）\n"
                f"{CMD} speedtest    回線速度測定（履歴付き）\n"
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
                f"```\n"
                f"{CMD} wol <name>           Wake-on-LAN\n"
                f"{CMD} pc shutdown <name>   シャットダウン\n"
                f"{CMD} pc reboot <name>     再起動\n"
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
                f"```\n"
                f"{CMD} watch <ip|name>      監視開始\n"
                f"{CMD} unwatch <ip|name>    監視解除\n"
                f"{CMD} watchlist            監視中ホスト一覧\n"
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
                f"```\n"
                f"{CMD} extend <分>   タイムアウト延長\n"
                f"{CMD} shutdown      Pi シャットダウン\n"
                f"{CMD} reboot        Pi 再起動\n"
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
    logger.info("=" * 50)
    logger.info("HomeCommander 起動")
    logger.info("データディレクトリ : %s", DATA_BASE)
    logger.info("コマンド           : %s", CMD)
    logger.info("デバッグモード     : %s", args.debug)
    logger.info("ログ書き込み       : %s/slackbot.log (INFO以上のみ)", LOG_DIR)
    # SD カード保護チェック: データが /mnt 以外の場合は警告
    if platform.system() == "Linux" and not DATA_BASE.startswith("/mnt"):
        logger.warning(
            "データディレクトリが /mnt 配下にありません: %s\n"
            "  SD カードへのログ書き込みが発生します。\n"
            "  USB などに移動する場合: setup.sh を再実行するか "
            "--data /mnt/usbdata/slackbot を指定してください。",
            DATA_BASE,
        )
    logger.info("=" * 50)

    inhibitor = start_sleep_inhibitor()
    notify_start()

    watcher = threading.Thread(target=timeout_watcher, daemon=True)
    watcher.start()

    handler = SocketModeHandler(app, cfg["slack"]["app_token"])
    try:
        handler.start()
    except KeyboardInterrupt:
        logger.info("停止シグナル受信。終了します。")
        if inhibitor:
            inhibitor.terminate()
            logger.info("スリープ抑制 解除")
        handler.close()
