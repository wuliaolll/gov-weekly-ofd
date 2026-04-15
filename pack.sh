#!/bin/bash
# ============================================================
# 打包部署包 — 生成 gov-weekly-ofd.zip
# 仅包含部署所需文件，不含 .git、测试、虚拟环境等
# 用法: bash pack.sh
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}"

PACK_NAME="gov-weekly-ofd"
TIMESTAMP=$(date +%Y%m%d)
ZIP_NAME="${PACK_NAME}-${TIMESTAMP}.zip"

# 清理旧包
rm -f "${ZIP_NAME}"

# 打包必要文件
zip -r "${ZIP_NAME}" \
    app.py \
    scraper.py \
    doc_generator.py \
    pdf2ofd.py \
    scheduler.py \
    config.json \
    requirements.txt \
    gunicorn.conf.py \
    deploy.sh \
    LICENSE \
    README.md \
    templates/ \
    static/ \
    fonts/README.txt \
    docs/ \
    -x "*.pyc" "__pycache__/*"

echo ""
echo "=========================================="
echo "  打包完成: ${ZIP_NAME}"
echo "  大小: $(du -h "${ZIP_NAME}" | cut -f1)"
echo "=========================================="
echo ""
echo "  部署步骤:"
echo "    1. scp ${ZIP_NAME} user@server:/opt/"
echo "    2. ssh user@server"
echo "    3. cd /opt && unzip ${ZIP_NAME} -d ${PACK_NAME}"
echo "    4. cd ${PACK_NAME}"
echo "    5. 将字体文件放入 fonts/ 目录"
echo "    6. bash deploy.sh"
echo ""
