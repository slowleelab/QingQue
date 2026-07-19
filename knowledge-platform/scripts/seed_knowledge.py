"""种子知识数据入库脚本

扫描 test_data/ 目录下的 .md 文件，解析 YAML frontmatter，
上传至 MinIO 并在 PG 中创建 KbDocument 记录。
然后投递 Kafka ETL 任务。

使用方式:
    python scripts/seed_knowledge.py
    python scripts/seed_knowledge.py --dry-run
    python scripts/seed_knowledge.py --dir /path/to/docs
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from datetime import date
from pathlib import Path

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


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 YAML frontmatter"""
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
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def scan_files(base_dir: Path) -> list[dict]:
    """扫描目录下所有 .md 文件"""
    results: list[dict] = []
    md_files = sorted(base_dir.rglob("*.md"))
    if not md_files:
        print(f"在 {base_dir} 下未找到 .md 文件")
        return results

    print(f"扫描目录: {base_dir}")
    print(f"  找到 {len(md_files)} 个 .md 文件\n")

    for fp in md_files:
        text = fp.read_text(encoding="utf-8")
        meta, _body = parse_frontmatter(text)

        rel_parts = fp.relative_to(base_dir).parts
        dir_name = rel_parts[0] if len(rel_parts) > 1 else ""
        mapped_category = DIR_CATEGORY_MAP.get(dir_name, "OTHER")

        category = meta.get("category", mapped_category) or mapped_category
        doc_type = meta.get("doc_type", "faq") or "faq"
        title = meta.get("title", fp.stem)
        card_type = meta.get("card_type")
        customer_tier = meta.get("customer_tier")
        security_level = meta.get("security_level", "internal") or "internal"
        version = str(meta.get("version", "1.0")) if meta.get("version") else "1.0"

        raw_effective = meta.get("effective_date")
        raw_expiry = meta.get("expiry_date")
        effective_date = _parse_date(raw_effective)
        expiry_date = _parse_date(raw_expiry)

        raw_keywords = meta.get("keywords", [])
        if isinstance(raw_keywords, list):
            keywords_str = ",".join(str(k) for k in raw_keywords)
        elif isinstance(raw_keywords, str):
            keywords_str = raw_keywords
        else:
            keywords_str = ""

        content_hash = compute_content_hash(text)
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
            "file_size": fp.stat().st_size,
            "object_key": object_key,
            "full_content": text,
        })
    return results


def _parse_date(val) -> date | None:
    if isinstance(val, date):
        return val
    if isinstance(val, str) and val:
        try:
            return date.fromisoformat(val)
        except ValueError:
            pass
    return None


def print_scan_summary(docs: list[dict]) -> None:
    print(f"{'序号':<4} {'文件名':<40} {'分类':<8} {'类型':<10} {'标题'}")
    print("-" * 90)
    for i, doc in enumerate(docs, 1):
        print(f"{i:<4} {doc['filename']:<40} {doc['category']:<8} {doc['doc_type']:<10} {doc['title']}")
    print(f"\n共 {len(docs)} 个文件")


async def upload_and_ingest(docs: list[dict]) -> None:
    """上传 MinIO + 建 PG 记录 + 投递 Kafka 任务"""
    import uuid_utils
    from minio import Minio

    from app.config import get_settings
    from app.database import get_session_factory
    from app.orm.kb import KbDocStatus, KbDocument, KbSourceType
    from app.storage.kafka import init_producer, publish_ingest_request, close_producer

    settings = get_settings()

    # MinIO
    print("\n上传文件到 MinIO...")
    minio_client = Minio(
        settings.minio.endpoint,
        access_key=settings.minio.access_key,
        secret_key=settings.minio.secret_key,
        secure=settings.minio.secure,
    )
    if not minio_client.bucket_exists(settings.minio.bucket):
        minio_client.make_bucket(settings.minio.bucket)

    # Kafka
    await init_producer()

    # PG
    session_factory = get_session_factory()

    async with session_factory() as session:
        for doc in docs:
            # 上传 MinIO
            data = doc["full_content"].encode("utf-8")
            minio_client.put_object(
                settings.minio.bucket,
                doc["object_key"],
                io.BytesIO(data),
                length=len(data),
                content_type="text/markdown; charset=utf-8",
            )
            print(f"  上传: {doc['object_key']}")

            # 查重
            from sqlalchemy import select

            existing = await session.execute(
                select(KbDocument).where(
                    KbDocument.content_hash == doc["content_hash"],
                    KbDocument.is_deleted.is_(False),
                )
            )
            if existing.scalar_one_or_none():
                print(f"  跳过（已存在）: {doc['filename']}")
                continue

            # 建 PG 记录
            doc_id = uuid_utils.uuid7()
            kb_doc = KbDocument(
                id=doc_id,
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
            await session.flush()

            # 投递 Kafka
            kw_list = [k.strip() for k in doc["keywords"].split(",") if k.strip()] if doc["keywords"] else []
            payload = {
                "doc_id": str(doc_id),
                "file_path": doc["object_key"],
                "source_type": "MARKDOWN",
                "metadata": {
                    "title": doc["title"],
                    "category": doc["category"],
                    "doc_type": doc["doc_type"],
                    "card_type": doc["card_type"] or "",
                    "customer_tier": doc["customer_tier"] or "",
                    "security_level": doc["security_level"],
                    "version": doc["version"],
                    "effective_date": doc["effective_date"].isoformat() if doc["effective_date"] else None,
                    "expiry_date": doc["expiry_date"].isoformat() if doc["expiry_date"] else None,
                    "keywords": kw_list,
                    "approval_status": "PUBLISHED",
                    "is_current_version": True,
                    "doc_group": str(doc_id),
                },
            }
            await publish_ingest_request(str(doc_id), payload)
            kb_doc.status = KbDocStatus.KAFKA_QUEUED
            print(f"  入库+投递: {doc['filename']} -> {doc_id}")

        await session.commit()

    await close_producer()
    print("\n种子数据入库完成!")


def main() -> None:
    parser = argparse.ArgumentParser(description="种子知识数据入库脚本")
    parser.add_argument("--dir", type=str, default="test_data", help="扫描目录")
    parser.add_argument("--dry-run", action="store_true", help="仅扫描不入库")
    args = parser.parse_args()

    base_dir = Path(args.dir)
    if not base_dir.is_dir():
        project_dir = Path(__file__).resolve().parent.parent
        alt_dir = project_dir / args.dir
        if alt_dir.is_dir():
            base_dir = alt_dir
        else:
            print(f"目录不存在: {base_dir}")
            sys.exit(1)

    docs = scan_files(base_dir)
    if not docs:
        sys.exit(0)

    print_scan_summary(docs)

    if args.dry_run:
        print("\n--dry-run 模式，跳过入库")
        return

    import asyncio

    asyncio.run(upload_and_ingest(docs))


if __name__ == "__main__":
    main()
