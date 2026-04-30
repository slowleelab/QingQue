"""gRPC Proto 文件编译脚本

从 proto/ 目录生成 Python gRPC 代码到 generated/proto/ 目录。

使用方式:
    poetry run python scripts/generate_grpc.py
"""

import subprocess
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent
PROTO_DIR = ROOT / "proto"
OUTPUT_DIR = ROOT / "generated" / "proto"

# Proto 文件列表
PROTO_FILES = [
    "classification.proto",
    "retrieval.proto",
    "safety.proto",
]


def generate():
    """编译所有 Proto 文件"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 创建 __init__.py
    (OUTPUT_DIR / "__init__.py").touch()

    for proto_file in PROTO_FILES:
        proto_path = PROTO_DIR / proto_file
        if not proto_path.exists():
            print(f"❌ Proto 文件不存在: {proto_path}")
            sys.exit(1)

        print(f"🔧 编译 {proto_file}...")

        cmd = [
            sys.executable, "-m", "grpc_tools.protoc",
            f"--proto_path={PROTO_DIR}",
            f"--python_out={OUTPUT_DIR}",
            f"--grpc_python_out={OUTPUT_DIR}",
            str(proto_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"❌ 编译失败: {proto_file}")
            print(result.stderr)
            sys.exit(1)

        print(f"✅ 编译成功: {proto_file}")

    # 修复 import 路径（grpc_tools 生成的代码使用绝对 import）
    print("\n🔧 修复 import 路径...")
    for py_file in OUTPUT_DIR.glob("*_pb2*.py"):
        content = py_file.read_text()
        # 将 "import classification_pb2" 改为 "from generated.proto import classification_pb2"
        for proto_file in PROTO_FILES:
            module_name = proto_file.replace(".proto", "_pb2")
            old_import = f"import {module_name}"
            new_import = f"from generated.proto import {module_name}"
            if old_import in content:
                content = content.replace(old_import, new_import)
                py_file.write_text(content)
                print(f"  修复: {py_file.name} 中的 {module_name} import")

    print("\n✅ 全部 Proto 文件编译完成!")
    print(f"   输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    generate()
