"""坐席辅助种子数据导入：话术 + 告警规则"""

from __future__ import annotations

import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from smartcs.shared.orm_models import (
    AlertRule,
    AlertRuleCategory,
    AlertRuleLevel,
    ScriptStatus,
    ScriptTemplate,
)
from smartcs.shared.config import get_settings


async def seed_scripts(db: AsyncSession) -> None:
    """导入话术种子数据"""
    from smartcs.services.assist.script_service import _SEED_SCRIPTS

    count = 0
    for s in _SEED_SCRIPTS:
        exists = await db.execute(
            select(ScriptTemplate).where(ScriptTemplate.script_id == s["script_id"])
        )
        if exists.scalar_one_or_none():
            continue
        db.add(ScriptTemplate(
            script_id=s["script_id"],
            category=s["category"],
            tags=s.get("tags", []),
            title=s.get("title", ""),
            content=s["content"],
            variables=s.get("variables", []),
            priority=s.get("priority", 5),
            status=ScriptStatus.ACTIVE,
            version=1,
            created_by="seed",
        ))
        count += 1
    await db.commit()
    print(f"Seeded {count} scripts ({len(_SEED_SCRIPTS)} total, {len(_SEED_SCRIPTS) - count} skipped)")


async def seed_rules(db: AsyncSession) -> None:
    """导入告警规则种子数据"""
    from smartcs.services.assist.alert_engine import _SEED_RULES

    count = 0
    for r in _SEED_RULES:
        exists = await db.execute(
            select(AlertRule).where(AlertRule.rule_id == r["rule_id"])
        )
        if exists.scalar_one_or_none():
            continue
        db.add(AlertRule(
            rule_id=r["rule_id"],
            category=AlertRuleCategory[r["category"].upper()],
            level=AlertRuleLevel[r["level"].upper()],
            pattern=r["pattern"],
            message=r["message"],
            suggestion=r.get("suggestion", ""),
            priority=r.get("priority", 5),
            status=ScriptStatus.ACTIVE,
            created_by="seed",
        ))
        count += 1
    await db.commit()
    print(f"Seeded {count} alert rules ({len(_SEED_RULES)} total, {len(_SEED_RULES) - count} skipped)")


async def main():
    settings = get_settings()
    engine = create_async_engine(settings.database.dsn)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        await seed_scripts(session)
        await seed_rules(session)

    await engine.dispose()
    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
