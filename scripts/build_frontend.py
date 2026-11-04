"""Compile Vue using an installed or bundled Node runtime and copy it into API."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
node = shutil.which("node")
if not node:
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    node = str(bundled) if bundled.exists() else None
if not node:
    raise SystemExit("请安装 Node.js 22 并在 frontend 目录执行 npm install 后重试")
vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
if not vite.exists():
    raise SystemExit("请先在 frontend 目录执行 npm install 安装前端依赖")
subprocess.run([node, str(vite), "build"], cwd=ROOT / "frontend", check=True)
target = ROOT / "backend/public"
target.mkdir(exist_ok=True)
shutil.copytree(ROOT / "frontend/dist", target, dirs_exist_ok=True)
print("Vue 编译结果已写入 backend/public，可直接构建 Python 镜像")
