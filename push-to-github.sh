#!/bin/bash
# 推送到 GitHub 的脚本
# 使用方法: ./push-to-github.sh YOUR_GITHUB_USERNAME YOUR_GITHUB_TOKEN

if [ $# -lt 2 ]; then
    echo "使用方法: ./push-to-github.sh <GitHub用户名> <GitHub Token或密码>"
    echo ""
    echo "步骤:"
    echo "1. 访问 https://github.com/new 创建仓库"
    echo "2. 仓库名: gov-weekly-ofd"
    echo "3. 选择: Public, MIT License"
    echo "4. 不勾选 'Initialize with README'"
    echo "5. 运行此脚本: ./push-to-github.sh 您的用户名 您的Token"
    exit 1
fi

USERNAME=$1
TOKEN=$2
REPO="gov-weekly-ofd"

echo "准备推送..."
echo "用户名: $USERNAME"
echo "仓库: $REPO"

git remote add origin https://${TOKEN}@github.com/${USERNAME}/${REPO}.git
git push -u origin main

echo "推送完成！"
echo "访问: https://github.com/${USERNAME}/${REPO}"
