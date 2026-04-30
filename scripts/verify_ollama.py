"""Ollama 部署与验证脚本

检查 Ollama 是否已安装运行，验证 Qwen2.5-7B 模型是否可用，
测试 OpenAI 兼容 API 调用。

使用方式:
    poetry run python scripts/verify_ollama.py

手动部署步骤:
    1. 安装 Ollama: curl -fsSL https://ollama.com/install.sh | sh
    2. 拉取模型:    ollama pull qwen2.5:7b
    3. 验证运行:    ollama run qwen2.5:7b "你好"
"""

import sys
import json
import urllib.request
import urllib.error


def check_ollama_running() -> bool:
    """检查 Ollama 服务是否运行"""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_model_available(model_name: str = "qwen2.5:7b") -> bool:
    """检查指定模型是否已下载"""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            return model_name in models or any(model_name in m for m in models)
    except Exception:
        return False


def test_openai_api() -> bool:
    """测试 OpenAI 兼容 API"""
    try:
        payload = json.dumps({
            "model": "qwen2.5:7b",
            "messages": [{"role": "user", "content": "你好，请用一句话介绍自己"}],
            "max_tokens": 100,
            "temperature": 0.1,
        }).encode("utf-8")

        req = urllib.request.Request(
            "http://localhost:11434/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            print(f"   模型回复: {content[:100]}...")
            return True
    except Exception as e:
        print(f"   API 调用失败: {e}")
        return False


def main():
    print("=" * 60)
    print("SmartCS Ollama 部署验证")
    print("=" * 60)

    # 1. 检查 Ollama 运行状态
    print("\n🔍 检查 Ollama 服务...")
    if check_ollama_running():
        print("✅ Ollama 服务运行中 (http://localhost:11434)")
    else:
        print("❌ Ollama 服务未运行")
        print("\n📋 部署步骤:")
        print("   1. 安装 Ollama:")
        print("      curl -fsSL https://ollama.com/install.sh | sh")
        print("   2. 启动 Ollama（通常安装后自动启动）:")
        print("      ollama serve")
        print("   3. 拉取模型:")
        print("      ollama pull qwen2.5:7b")
        sys.exit(1)

    # 2. 列出已下载模型
    print("\n🔍 检查已下载模型...")
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = data.get("models", [])
            if models:
                for m in models:
                    size_gb = m.get("size", 0) / (1024**3)
                    print(f"   - {m['name']} ({size_gb:.1f} GB)")
            else:
                print("   无已下载模型")
    except Exception as e:
        print(f"   获取模型列表失败: {e}")

    # 3. 检查 Qwen2.5-7B
    print("\n🔍 检查 Qwen2.5-7B 模型...")
    if check_model_available("qwen2.5:7b"):
        print("✅ Qwen2.5-7B 已就绪")
    else:
        print("❌ Qwen2.5-7B 未下载")
        print("   下载命令: ollama pull qwen2.5:7b")
        print("   预计下载量: ~4.7 GB")
        sys.exit(1)

    # 4. 测试 OpenAI 兼容 API
    print("\n🔍 测试 OpenAI 兼容 API...")
    if test_openai_api():
        print("✅ OpenAI 兼容 API 调用成功")
    else:
        print("❌ OpenAI 兼容 API 调用失败")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("🎉 Ollama + Qwen2.5-7B 部署验证通过!")
    print("=" * 60)
    print("\n使用方式:")
    print("  命令行:   ollama run qwen2.5:7b")
    print("  API:      http://localhost:11434/v1/chat/completions")
    print("  配置引用: shared/config.py → LLMSettings.base_url")


if __name__ == "__main__":
    main()
