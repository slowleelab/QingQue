"""种子知识数据入库脚本

扫描 test_data/ 目录下的 .md 文件，解析 YAML frontmatter，
上传至 MinIO 并在 PostgreSQL 中创建 KbDocument 记录。

支持参数:
    --dir      指定扫描目录（默认: test_data/）
    --dry-run  仅扫描不入库

使用方式:
    poetry run python scripts/seed_knowledge.py
    poetry run python scripts/seed_knowledge.py --dry-run
    poetry run python scripts/seed_knowledge.py --dir /path/to/docs
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date, datetime
from pathlib import Path

# 目录名 -> 业务分类 映射
DIR_CATEGORY_MAP: dict[str, str] = {
    "faq": "FAQ",
    "fee": "费率",
    "points": "积分",
    "annual_fee": "年费",
    "regulations": "章程",
    "repayment": "还款",
    "security": "安全",
    "activity": "活动",
    "other": "OTHER",
}


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 YAML frontmatter，返回 (元数据字典, 正文内容)"""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    import yaml

    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return meta, body


def compute_content_hash(content: str | bytes) -> str:
    """计算 SHA-256 内容哈希"""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def date_to_epoch_ms(d: date) -> int:
    """将 date 转换为 epoch 毫秒"""
    dt = datetime(d.year, d.month, d.day)
    return int(dt.timestamp() * 1000)


def scan_files(base_dir: Path) -> list[dict]:
    """扫描目录下所有 .md 文件并解析元数据"""
    results: list[dict] = []
    md_files = sorted(base_dir.rglob("*.md"))

    if not md_files:
        print(f"⚠️  在 {base_dir} 下未找到 .md 文件")
        return results

    print(f"📂 扫描目录: {base_dir}")
    print(f"   找到 {len(md_files)} 个 .md 文件\n")

    for fp in md_files:
        text = fp.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)

        # 从目录名推断分类
        rel_parts = fp.relative_to(base_dir).parts
        dir_name = rel_parts[0] if len(rel_parts) > 1 else ""
        mapped_category = DIR_CATEGORY_MAP.get(dir_name, "OTHER")

        # frontmatter 优先，目录映射兜底
        category = meta.get("category", mapped_category) or mapped_category
        doc_type = meta.get("doc_type", "faq") or "faq"
        title = meta.get("title", fp.stem)
        card_type = meta.get("card_type")
        customer_tier = meta.get("customer_tier")
        security_level = meta.get("security_level", "internal") or "internal"
        version = str(meta.get("version", "1.0")) if meta.get("version") else "1.0"

        # 处理日期
        raw_effective = meta.get("effective_date")
        raw_expiry = meta.get("expiry_date")

        effective_date = None
        expiry_date = None
        if isinstance(raw_effective, date):
            effective_date = raw_effective
        elif isinstance(raw_effective, str) and raw_effective:
            try:
                effective_date = date.fromisoformat(raw_effective)
            except ValueError:
                pass

        if isinstance(raw_expiry, date):
            expiry_date = raw_expiry
        elif isinstance(raw_expiry, str) and raw_expiry:
            try:
                expiry_date = date.fromisoformat(raw_expiry)
            except ValueError:
                pass

        # 关键词
        raw_keywords = meta.get("keywords", [])
        if isinstance(raw_keywords, list):
            keywords_str = ",".join(str(k) for k in raw_keywords)
        elif isinstance(raw_keywords, str):
            keywords_str = raw_keywords
        else:
            keywords_str = ""

        full_content = text
        content_hash = compute_content_hash(full_content)
        file_size = fp.stat().st_size

        # MinIO object key: {category}/{filename}
        object_key = f"{category}/{fp.name}"

        results.append({
            "file_path": str(fp),
            "filename": fp.name,
            "title": title,
            "category": category,
            "doc_type": doc_type,
            "card_type": card_type if card_type and card_type != "null" else None,
            "customer_tier": customer_tier if customer_tier and customer_tier != "null" else None,
            "security_level": security_level,
            "version": version,
            "effective_date": effective_date,
            "expiry_date": expiry_date,
            "keywords": keywords_str,
            "content_hash": content_hash,
            "file_size": file_size,
            "object_key": object_key,
            "full_content": full_content,
        })

    return results


def print_scan_summary(docs: list[dict]) -> None:
    """打印扫描结果摘要"""
    print(f"{'序号':<4} {'文件名':<40} {'分类':<8} {'类型':<10} {'标题'}")
    print("-" * 90)
    for i, doc in enumerate(docs, 1):
        print(f"{i:<4} {doc['filename']:<40} {doc['category']:<8} {doc['doc_type']:<10} {doc['title']}")
    print(f"\n共 {len(docs)} 个文件")


def upload_to_minio(docs: list[dict]) -> None:
    """上传文件到 MinIO"""
    import os

    from minio import Minio

    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")

    print(f"\n📤 上传文件到 MinIO ({endpoint})...")
    try:
        client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=False,
        )
    except Exception as e:
        print(f"❌ 连接 MinIO 失败: {e}")
        print("   请确保 MinIO 已启动: docker-compose up -d minio")
        sys.exit(1)

    bucket_name = "smartcs-docs"
    if not client.bucket_exists(bucket_name):
        print(f"🔧 Bucket '{bucket_name}' 不存在，自动创建...")
        client.make_bucket(bucket_name)

    import io

    for doc in docs:
        data = doc["full_content"].encode("utf-8")
        client.put_object(
            bucket_name,
            doc["object_key"],
            io.BytesIO(data),
            length=len(data),
            content_type="text/markdown; charset=utf-8",
        )
        print(f"   ✅ 上传: {doc['object_key']} ({len(data)} bytes)")


def insert_to_database(docs: list[dict]) -> None:
    """插入 KbDocument 记录到 PostgreSQL"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from smartcs.shared.config import get_settings
    from smartcs.shared.orm_models import KbDocStatus, KbDocument, KbSourceType

    settings = get_settings()
    sync_dsn = settings.database.sync_dsn

    print("\n💾 写入 PostgreSQL...")
    engine = create_engine(sync_dsn)

    import uuid_utils

    with Session(engine) as session:
        for doc in docs:
            # 查重：按 content_hash 检查
            existing = session.query(KbDocument).filter(
                KbDocument.content_hash == doc["content_hash"],
                KbDocument.is_deleted.is_(False),
            ).first()

            if existing:
                print(f"   ⚠️  跳过（已存在）: {doc['filename']} (hash={doc['content_hash'][:12]}...)")
                continue

            kb_doc = KbDocument(
                id=uuid_utils.uuid7(),
                title=doc["title"],
                source_type=KbSourceType.MARKDOWN,
                file_path=doc["object_key"],
                file_size=doc["file_size"],
                content_hash=doc["content_hash"],
                category=doc["category"],
                doc_type=doc["doc_type"],
                card_type=doc["card_type"],
                customer_tier=doc["customer_tier"],
                security_level=doc["security_level"],
                version=doc["version"],
                effective_date=doc["effective_date"],
                expiry_date=doc["expiry_date"],
                status=KbDocStatus.PENDING,
                is_deleted=False,
                created_by="seed_script",
            )
            session.add(kb_doc)
            print(f"   ✅ 新增: {doc['filename']}")

        session.commit()

    engine.dispose()
    print("✅ 数据库写入完成")


def main():
    parser = argparse.ArgumentParser(description="种子知识数据入库脚本")
    parser.add_argument("--dir", type=str, default="test_data", help="扫描目录（默认: test_data/）")
    parser.add_argument("--dry-run", action="store_true", help="仅扫描不入库")
    args = parser.parse_args()

    base_dir = Path(args.dir)
    if not base_dir.is_dir():
        # 尝试项目根目录下的 test_data/
        project_dir = Path(__file__).resolve().parent.parent
        alt_dir = project_dir / args.dir
        if alt_dir.is_dir():
            base_dir = alt_dir
        else:
            print(f"❌ 目录不存在: {base_dir}")
            sys.exit(1)

    docs = scan_files(base_dir)
    if not docs:
        sys.exit(0)

    print_scan_summary(docs)

    if args.dry_run:
        print("\n🏁 --dry-run 模式，跳过入库")
        return

    upload_to_minio(docs)
    insert_to_database(docs)

    print("\n🏁 种子数据入库完成!")


if __name__ == "__main__":
    main()
