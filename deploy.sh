#!/bin/bash
# ============================================================
# 政务周报 OFD 生成器 — 服务器一键部署脚本
# 适用于 CentOS / Ubuntu + Anaconda 环境
# 用法: bash deploy.sh
# ============================================================
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_NAME="gov-weekly"
PORT="${PORT:-5000}"

echo "=========================================="
echo "  政务周报 OFD 生成器 — 服务器部署"
echo "=========================================="

# ---------- 1. 检查 Anaconda ----------
if ! command -v conda &>/dev/null; then
    echo "[ERROR] 未找到 conda，请先安装 Anaconda/Miniconda"
    exit 1
fi
echo "[OK] conda: $(conda --version)"

# ---------- 2. 检查 curl ----------
if ! command -v curl &>/dev/null; then
    echo "[WARN] 未找到 curl，尝试安装..."
    if command -v yum &>/dev/null; then
        sudo yum install -y curl
    elif command -v apt-get &>/dev/null; then
        sudo apt-get install -y curl
    else
        echo "[ERROR] 请手动安装 curl"
        exit 1
    fi
fi
echo "[OK] curl: $(curl --version | head -1)"

# ---------- 3. 创建 Conda 环境 ----------
# 自动检测系统支持的最高 Python 版本（3.12 → 3.11 → 3.10 → 3.9）
if conda env list | grep -q "^${ENV_NAME} "; then
    echo "[OK] conda 环境 '${ENV_NAME}' 已存在，跳过创建"
else
    PYTHON_VER=""
    for ver in 3.12 3.11 3.10 3.9; do
        echo "[...] 尝试 Python ${ver}..."
        if conda create -n "${ENV_NAME}" python="${ver}" -y --dry-run &>/dev/null; then
            PYTHON_VER="${ver}"
            break
        fi
        echo "[WARN] Python ${ver} 不兼容当前系统，尝试更低版本..."
    done
    if [ -z "${PYTHON_VER}" ]; then
        echo "[ERROR] 无法找到兼容的 Python 版本（需要 3.9+）"
        echo "[HINT] 您的系统 glibc 版本可能过低，请考虑升级操作系统或使用 Docker"
        exit 1
    fi
    echo "[...] 创建 conda 环境: ${ENV_NAME} (Python ${PYTHON_VER})"
    conda create -n "${ENV_NAME}" python="${PYTHON_VER}" -y
fi

# 激活环境
eval "$(conda shell.bash hook)"
conda activate "${ENV_NAME}"
echo "[OK] Python: $(python --version)"

# ---------- 4. 安装依赖 ----------
echo "[...] 安装 Python 依赖..."
pip install -r "${APP_DIR}/requirements.txt" --quiet

# ---------- 5. 字体检查与提示 ----------
FONTS_DIR="${APP_DIR}/fonts"
echo ""
echo "=========================================="
echo "  字体检查"
echo "=========================================="
if [ -d "${FONTS_DIR}" ]; then
    echo "[OK] fonts/ 目录已存在"
    ls -la "${FONTS_DIR}"/*.ttf "${FONTS_DIR}"/*.TTF 2>/dev/null || echo "  (无 .ttf 文件)"
else
    mkdir -p "${FONTS_DIR}"
    echo "[WARN] 已创建 fonts/ 目录，请将以下字体文件放入:"
fi
echo ""
echo "  必需字体:"
echo "    - FZXBSJW.TTF      (方正小标宋简体 — 标题)"
echo "    - SIMFANG.TTF       (仿宋/仿宋GB2312 — 正文)"
echo "    - times.ttf         (Times New Roman — 英文/数字)"
echo ""
echo "  放置路径: ${FONTS_DIR}/"
echo "  或系统路径: /usr/share/fonts/ 或 ~/.fonts/"
echo ""

# ---------- 6. 创建输出目录 ----------
mkdir -p "${APP_DIR}/output"

# ---------- 7. 创建 systemd 服务文件（可选） ----------
SERVICE_FILE="${APP_DIR}/gov-weekly.service"
cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=政务周报 OFD 生成器
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${APP_DIR}
Environment="PATH=$(conda info --base)/envs/${ENV_NAME}/bin:/usr/bin:/bin"
Environment="PORT=${PORT}"
ExecStart=$(conda info --base)/envs/${ENV_NAME}/bin/gunicorn -c gunicorn.conf.py app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
echo "[OK] systemd 服务文件已生成: ${SERVICE_FILE}"

# ---------- 8. 完成 ----------
echo ""
echo "=========================================="
echo "  部署完成！"
echo "=========================================="
echo ""
echo "  快速启动（前台测试）:"
echo "    conda activate ${ENV_NAME}"
echo "    cd ${APP_DIR}"
echo "    gunicorn -c gunicorn.conf.py app:app"
echo ""
echo "  注册系统服务（后台运行）:"
echo "    sudo cp ${SERVICE_FILE} /etc/systemd/system/"
echo "    sudo systemctl daemon-reload"
echo "    sudo systemctl enable gov-weekly"
echo "    sudo systemctl start gov-weekly"
echo ""
echo "  访问地址: http://<服务器IP>:${PORT}"
echo ""
