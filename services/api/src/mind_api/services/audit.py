from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..models import AuditLog, CostRecord, Organization, UsageEvent

_SECRET_KEYS = re.compile(r"(api[_-]?key|secret|password|token|authorization)", re.I)


def redact(value: Any) -> Any:
    """Recursively replace values under secret-looking keys."""
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def audit(
    db: Session,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    org_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: object | None = None,
    ip: str | None = None,
    **details: Any,
) -> None:
    db.add(
        AuditLog(
            org_id=org_id,
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip=ip,
            details=redact(details),
        )
    )


def record_usage(
    db: Session,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    category: str,
    provider_kind: str | None,
    model_name: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    units: Decimal | int = 0,
    cost_usd: Decimal | None,
    ref_type: str | None = None,
    ref_id: object | None = None,
) -> None:
    db.add(
        UsageEvent(
            org_id=org_id,
            user_id=user_id,
            category=category,
            provider_kind=provider_kind,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            units=Decimal(units),
            cost_usd=cost_usd,
            ref_type=ref_type,
            ref_id=str(ref_id) if ref_id is not None else None,
        )
    )
    stmt = insert(CostRecord).values(
        id=uuid.uuid4(),
        org_id=org_id,
        day=date.today(),
        category=category,
        provider_kind=provider_kind or "local",
        events=1,
        unpriced_events=1 if cost_usd is None else 0,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd or Decimal(0),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_cost_rollup",
        set_={
            "events": CostRecord.events + 1,
            "unpriced_events": CostRecord.unpriced_events + (1 if cost_usd is None else 0),
            "input_tokens": CostRecord.input_tokens + input_tokens,
            "output_tokens": CostRecord.output_tokens + output_tokens,
            "cost_usd": CostRecord.cost_usd + (cost_usd or Decimal(0)),
        },
    )
    db.execute(stmt)


class BudgetExceeded(Exception):
    pass


def check_budget(db: Session, org: Organization, user_id: uuid.UUID | None) -> None:
    """Raise BudgetExceeded if the org's monthly or the user's daily budget is spent."""
    now = datetime.now(UTC)
    if org.monthly_budget_usd is not None:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = db.scalar(
            select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(UsageEvent.org_id == org.id, UsageEvent.created_at >= month_start)
        )
        if Decimal(spent) >= org.monthly_budget_usd:
            raise BudgetExceeded(f"Organization monthly budget of ${org.monthly_budget_usd} is used up")
    if org.user_daily_budget_usd is not None and user_id is not None:
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        spent = db.scalar(
            select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
                UsageEvent.org_id == org.id, UsageEvent.user_id == user_id, UsageEvent.created_at >= day_start
            )
        )
        if Decimal(spent) >= org.user_daily_budget_usd:
            raise BudgetExceeded(f"Your daily budget of ${org.user_daily_budget_usd} in this organization is used up")
