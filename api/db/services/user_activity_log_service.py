#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
import logging
from datetime import datetime, timedelta

from peewee import IntegrityError, fn

from api.db.db_models import DB, TenantLLM, User, UserActivityLog
from api.db.services.common_service import CommonService
from rag.utils.redis_conn import REDIS_CONN


UTC8_OFFSET = timedelta(hours=8)


def utc_now() -> datetime:
    return datetime.utcnow()


def utc8_date(dt: datetime | None = None):
    return ((dt or utc_now()) + UTC8_OFFSET).date()


def format_utc8(dt: datetime) -> str:
    return (dt + UTC8_OFFSET).strftime("%Y-%m-%d %H:%M:%S")


class UserActivityLogService(CommonService):
    model = UserActivityLog

    # Coalesce writes from the same user inside this window so the DB is hit
    # at most once per ``TOUCH_THROTTLE_SECONDS``. Window is short enough that
    # ``last_seen_at`` stays meaningful, long enough to absorb chatty UIs.
    TOUCH_THROTTLE_SECONDS = 300

    @classmethod
    @DB.connection_context()
    def touch(cls, user_id: str) -> None:
        """Record that ``user_id`` is active right now.

        Idempotent at day granularity: at most one row per (user_id, date),
        with ``last_seen_at`` refreshed at most every ``TOUCH_THROTTLE_SECONDS``.
        Hot path — called on every authenticated request — so we throttle in
        Redis and only fall through to the DB upsert when the throttle key
        expires. Failures never propagate.
        """
        if not user_id:
            return
        try:
            now = utc_now()
            today = utc8_date(now).strftime("%Y-%m-%d")
            throttle_key = f"user_active:{user_id}:{today}"

            try:
                if REDIS_CONN.get(throttle_key):
                    return
                REDIS_CONN.set(throttle_key, "1", cls.TOUCH_THROTTLE_SECONDS)
            except Exception:
                # Redis is the throttle. Without it, every authenticated request
                # would hit the DB; better to skip the data point than to melt
                # the database.
                logging.exception("UserActivityLog redis throttle failed; skipping touch")
                return

            try:
                cls.model.insert(
                    user_id=user_id,
                    activity_date=today,
                    first_seen_at=now,
                    last_seen_at=now,
                ).execute()
            except IntegrityError:
                # Existing row for today — refresh last_seen_at.
                cls.model.update(last_seen_at=now).where((cls.model.user_id == user_id) & (cls.model.activity_date == today)).execute()
        except Exception:
            logging.exception("UserActivityLogService.touch failed user_id=%s", user_id)

    @classmethod
    @DB.connection_context()
    def daily_active(cls, days: int) -> list[dict]:
        """[{date, dau}] for the last ``days`` days, oldest first, gaps zero-filled."""
        days = max(1, min(int(days or 1), 90))
        today = utc8_date()
        start = today - timedelta(days=days - 1)
        start_str = start.strftime("%Y-%m-%d")

        rows = (
            cls.model.select(
                cls.model.activity_date.alias("date"),
                fn.COUNT(fn.DISTINCT(cls.model.user_id)).alias("dau"),
            )
            .where(cls.model.activity_date >= start_str)
            .group_by(cls.model.activity_date)
        )
        bucket = {r.date: int(r.dau) for r in rows}

        out = []
        for i in range(days):
            d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            out.append({"date": d, "dau": bucket.get(d, 0)})
        return out

    @classmethod
    @DB.connection_context()
    def top_active_users(cls, days: int, limit: int = 10) -> list[dict]:
        """Top users by number of distinct active days, with nickname/email/last_seen."""
        days = max(1, min(int(days or 1), 90))
        limit = max(1, min(int(limit or 10), 50))
        start_str = (utc8_date() - timedelta(days=days - 1)).strftime("%Y-%m-%d")

        rows = (
            cls.model.select(
                cls.model.user_id,
                User.nickname,
                User.email,
                fn.COUNT(cls.model.activity_date).alias("active_days"),
                fn.MAX(cls.model.last_seen_at).alias("last_seen_at"),
            )
            .join(User, on=(cls.model.user_id == User.id))
            .where(cls.model.activity_date >= start_str)
            .group_by(cls.model.user_id, User.nickname, User.email)
            .order_by(fn.COUNT(cls.model.activity_date).desc())
            .limit(limit)
            .dicts()
        )
        out = []
        for r in rows:
            last = r.get("last_seen_at")
            out.append(
                {
                    "user_id": r["user_id"],
                    "nickname": r.get("nickname") or "",
                    "email": r.get("email") or "",
                    "active_days": int(r.get("active_days") or 0),
                    "last_seen_at": format_utc8(last) if isinstance(last, datetime) else (str(last) if last else ""),
                }
            )
        return out

    @classmethod
    @DB.connection_context()
    def total_unique_users(cls, days: int) -> int:
        days = max(1, min(int(days or 1), 90))
        start_str = (utc8_date() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        return int(cls.model.select(fn.COUNT(fn.DISTINCT(cls.model.user_id))).where(cls.model.activity_date >= start_str).scalar() or 0)


@DB.connection_context()
def model_usage_summary(top_n: int = 20) -> dict:
    """Aggregate cumulative model usage from ``tenant_llm.used_tokens``.

    NOTE: ``tenant_llm.used_tokens`` is a monotonic counter incremented inside
    ``TenantLLMService.increase_usage`` on every embedding/rerank/chat/image
    call. There is no per-event log, so these totals are *all-time* per
    (tenant, factory, model) and cannot be sliced by time window. The frontend
    surfaces this as a separate "all-time" panel.
    """
    top_n = max(1, min(int(top_n or 20), 100))

    by_model_rows = (
        TenantLLM.select(
            TenantLLM.llm_factory,
            TenantLLM.llm_name,
            TenantLLM.model_type,
            fn.SUM(TenantLLM.used_tokens).alias("used_tokens"),
            fn.COUNT(fn.DISTINCT(TenantLLM.tenant_id)).alias("tenants"),
        )
        .where(TenantLLM.used_tokens > 0)
        .group_by(TenantLLM.llm_factory, TenantLLM.llm_name, TenantLLM.model_type)
        .order_by(fn.SUM(TenantLLM.used_tokens).desc())
        .limit(top_n)
        .dicts()
    )

    by_factory_rows = (
        TenantLLM.select(
            TenantLLM.llm_factory,
            fn.SUM(TenantLLM.used_tokens).alias("used_tokens"),
            fn.COUNT(fn.DISTINCT(TenantLLM.tenant_id)).alias("tenants"),
        )
        .where(TenantLLM.used_tokens > 0)
        .group_by(TenantLLM.llm_factory)
        .order_by(fn.SUM(TenantLLM.used_tokens).desc())
        .dicts()
    )

    by_model_name_rows = (
        TenantLLM.select(
            TenantLLM.llm_name,
            fn.SUM(TenantLLM.used_tokens).alias("used_tokens"),
        )
        .where(TenantLLM.used_tokens > 0)
        .group_by(TenantLLM.llm_name)
        .order_by(fn.SUM(TenantLLM.used_tokens).desc())
        .dicts()
    )

    by_type_rows = (
        TenantLLM.select(
            TenantLLM.model_type,
            fn.SUM(TenantLLM.used_tokens).alias("used_tokens"),
        )
        .where(TenantLLM.used_tokens > 0)
        .group_by(TenantLLM.model_type)
        .order_by(fn.SUM(TenantLLM.used_tokens).desc())
        .dicts()
    )

    by_model = [
        {
            "factory": r.get("llm_factory") or "",
            "llm_name": r.get("llm_name") or "",
            "model_type": r.get("model_type") or "",
            "used_tokens": int(r.get("used_tokens") or 0),
            "tenants": int(r.get("tenants") or 0),
        }
        for r in by_model_rows
    ]
    by_factory = [
        {
            "factory": r.get("llm_factory") or "",
            "used_tokens": int(r.get("used_tokens") or 0),
            "tenants": int(r.get("tenants") or 0),
        }
        for r in by_factory_rows
    ]
    by_model_name = [
        {
            "llm_name": r.get("llm_name") or "",
            "used_tokens": int(r.get("used_tokens") or 0),
        }
        for r in by_model_name_rows
    ]
    by_type = [
        {
            "model_type": r.get("model_type") or "",
            "used_tokens": int(r.get("used_tokens") or 0),
        }
        for r in by_type_rows
    ]
    total_tokens = sum(item["used_tokens"] for item in by_factory)

    return {
        "by_model": by_model,
        "by_factory": by_factory,
        "by_model_name": by_model_name,
        "by_type": by_type,
        "total_tokens": total_tokens,
    }
