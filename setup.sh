#!/bin/sh
# HomeCommander セットアップスクリプト
# 対応環境: macOS (開発) / Raspberry Pi OS Trixi (本番)
# 実行: sh setup.sh
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
SERVICE_NAME="slackbot.service"
OS="$(uname -s)"

# -------------------------
# ユーティリティ
# -------------------------
info()  { echo "[OK]   $*"; }
warn()  { echo "[WARN] $*"; }
error() { echo "[ERROR] $*" >&2; exit 1; }
step()  { echo ""; echo "--- $* ---"; }

echo "=== HomeCommander セットアップ ==="
echo "プロジェクト : $SCRIPT_DIR"
echo "OS          : $OS"

# -------------------------
# Python バージョン確認
# -------------------------
step "Python 確認"

if ! command -v python3 &>/dev/null; then
    error "python3 が見つかりません。インストールしてください。"
fi

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]; }; then
    error "Python 3.9 以上が必要です (現在: $PYTHON_VER)"
fi

info "Python $PYTHON_VER"

# -------------------------
# システムパッケージ
# -------------------------
if [ "$OS" = "Darwin" ]; then
    step "Mac: システムパッケージ (Homebrew)"
    if ! command -v brew &>/dev/null; then
        warn "Homebrew が見つかりません。arp-scan / speedtest-cli は手動でインストールしてください。"
        warn "  https://brew.sh"
    else
        brew install arp-scan speedtest-cli 2>/dev/null && info "brew パッケージ" \
            || warn "一部パッケージのインストールに失敗しました (既インストールの場合は無視してください)"
    fi

elif [ "$OS" = "Linux" ]; then
    step "Linux: システムパッケージ (apt)"
    sudo apt-get update -qq
    sudo apt-get install -y \
        arp-scan \
        speedtest-cli \
        samba-common-bin \
        python3-venv \
        python3-dev
    info "apt パッケージ"

else
    error "未対応のOS: $OS"
fi

# -------------------------
# 仮想環境の作成
# -------------------------
step "Python 仮想環境"

if [ -d "$VENV_DIR" ]; then
    warn "venv は既に存在します: ${VENV_DIR} (スキップ)"
else
    python3 -m venv "$VENV_DIR"
    info "venv を作成: $VENV_DIR"
fi

# -------------------------
# pip パッケージのインストール
# -------------------------
step "pip パッケージ"

"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
info "pip パッケージ"

# -------------------------
# データディレクトリの入力
# -------------------------
step "データディレクトリ"

if [ "$OS" = "Darwin" ]; then
    DEFAULT_DATA="$SCRIPT_DIR/data"
elif [ "$OS" = "Linux" ]; then
    DEFAULT_DATA="/mnt/usbdata/slackbot"
else
    DEFAULT_DATA="$SCRIPT_DIR/data"
fi

# 既存の .env があればデフォルト値として使う
if [ -f "$SCRIPT_DIR/.env" ]; then
    SAVED=$(grep "^SLACKBOT_DATA=" "$SCRIPT_DIR/.env" | cut -d= -f2)
    if [ -n "$SAVED" ]; then
        DEFAULT_DATA="$SAVED"
    fi
fi

echo "config.yaml と logs の保存先を入力してください。"
echo "(Enter でデフォルト: $DEFAULT_DATA)"
printf "> "
read -r DATA_INPUT
DATA_DIR="${DATA_INPUT:-$DEFAULT_DATA}"

# .env に保存
echo "SLACKBOT_DATA=${DATA_DIR}" > "$SCRIPT_DIR/.env"
info ".env に保存: SLACKBOT_DATA=${DATA_DIR}"

# -------------------------
# データディレクトリ・設定ファイルの作成
# -------------------------
step "設定ファイル"

case "$DATA_DIR" in
    /mnt/*) SUDO_PREFIX="sudo" ;;
    *)      SUDO_PREFIX="" ;;
esac

if [ ! -d "$DATA_DIR" ]; then
    ${SUDO_PREFIX} mkdir -p "$DATA_DIR/logs"
    info "ディレクトリを作成: $DATA_DIR"
else
    ${SUDO_PREFIX} mkdir -p "$DATA_DIR/logs" 2>/dev/null || true
    info "データディレクトリ: $DATA_DIR"
fi

if [ ! -f "$DATA_DIR/config.yaml" ]; then
    if [ -f "$SCRIPT_DIR/config_sample.yaml" ]; then
        ${SUDO_PREFIX} cp "$SCRIPT_DIR/config_sample.yaml" "$DATA_DIR/config.yaml"
        info "config_sample.yaml -> $DATA_DIR/config.yaml にコピーしました"
        warn "config.yaml を編集して Slack トークンとネットワーク情報を設定してください"
    else
        warn "config_sample.yaml が見つかりません。手動で $DATA_DIR/config.yaml を作成してください"
    fi
else
    warn "${DATA_DIR}/config.yaml は既に存在します (スキップ)"
fi

# -------------------------
# systemd サービス (Linux のみ)
# -------------------------
if [ "$OS" = "Linux" ]; then
    step "systemd サービス"

    PYTHON_BIN="$VENV_DIR/bin/python3"
    SERVICE_DEST="/etc/systemd/system/$SERVICE_NAME"

    sudo tee "$SERVICE_DEST" > /dev/null <<EOF
[Unit]
Description=Slack LAN Manager Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_BIN $SCRIPT_DIR/slackbot.py
Restart=always
RestartSec=5
Environment=SLACKBOT_DATA=${DATA_DIR}

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    info "systemd サービスを登録・有効化しました"
fi

# -------------------------
# 完了メッセージ
# -------------------------
echo ""
echo "=============================="
echo " セットアップ完了"
echo "=============================="

if [ "$OS" = "Darwin" ]; then
    echo ""
    echo "[起動方法 (Mac 開発環境)]"
    echo ""
    echo "  source venv/bin/activate"
    echo "  python slackbot.py"
    echo ""
    echo "[次のステップ]"
    echo "  1. ${DATA_DIR}/config.yaml を編集 (Slack トークン・ネットワーク情報)"
    echo "  2. 上記コマンドで起動"

elif [ "$OS" = "Linux" ]; then
    echo ""
    echo "[サービス操作]"
    echo ""
    echo "  sudo systemctl start   $SERVICE_NAME"
    echo "  sudo systemctl stop    $SERVICE_NAME"
    echo "  sudo systemctl restart $SERVICE_NAME"
    echo "  sudo systemctl status  $SERVICE_NAME"
    echo "  journalctl -u $SERVICE_NAME -f"
    echo ""
    echo "[次のステップ]"
    echo "  1. ${DATA_DIR}/config.yaml を編集"
    echo "  2. sudo systemctl start $SERVICE_NAME"
fi
