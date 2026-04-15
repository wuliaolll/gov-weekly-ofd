"""打包部署包 — 生成 gov-weekly-ofd-YYYYMMDD.zip（跨平台 Python 脚本）"""
import os
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
TIMESTAMP = datetime.now().strftime("%Y%m%d")
ZIP_NAME = f"gov-weekly-ofd-{TIMESTAMP}.zip"
ZIP_PATH = BASE_DIR / ZIP_NAME

# 需要打包的文件和目录
INCLUDE_FILES = [
    "app.py",
    "scraper.py",
    "doc_generator.py",
    "pdf2ofd.py",
    "scheduler.py",
    "config.json",
    "requirements.txt",
    "gunicorn.conf.py",
    "deploy.sh",
    "pack.sh",
    "LICENSE",
    "README.md",
    "fonts/README.txt",
]

INCLUDE_DIRS = [
    "templates",
    "static",
    "docs",
]

EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_DIRS = {"__pycache__"}


def main():
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    count = 0
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        # 单文件
        for f in INCLUDE_FILES:
            fp = BASE_DIR / f
            if fp.exists():
                zf.write(fp, f"gov-weekly-ofd/{f}")
                count += 1
            else:
                print(f"  [SKIP] {f} (不存在)")

        # 目录
        for d in INCLUDE_DIRS:
            dp = BASE_DIR / d
            if not dp.is_dir():
                print(f"  [SKIP] {d}/ (不存在)")
                continue
            for root, dirs, files in os.walk(dp):
                dirs[:] = [x for x in dirs if x not in EXCLUDE_DIRS]
                for fn in files:
                    if Path(fn).suffix in EXCLUDE_SUFFIXES:
                        continue
                    full = Path(root) / fn
                    rel = full.relative_to(BASE_DIR)
                    zf.write(full, f"gov-weekly-ofd/{rel}")
                    count += 1

    size_kb = ZIP_PATH.stat().st_size / 1024
    print()
    print("=" * 42)
    print(f"  打包完成: {ZIP_NAME}")
    print(f"  文件数: {count}，大小: {size_kb:.0f} KB")
    print("=" * 42)
    print()
    print("  部署步骤:")
    print(f"    1. 上传 {ZIP_NAME} 到服务器")
    print(f"    2. unzip {ZIP_NAME}")
    print("    3. cd gov-weekly-ofd")
    print("    4. 将字体文件放入 fonts/ 目录")
    print("    5. bash deploy.sh")
    print()


if __name__ == "__main__":
    main()
