import logging
import threading
from datetime import datetime

from api.db import StatusEnum
from api.db.db_models import DB, TenantLLM
from api.db.services.tenant_llm_service import TenantLLMService
from api.db.services.user_service import TenantService, UserService
from common.time_utils import current_timestamp, datetime_format


DEFAULT_MODEL_FIELDS = ("llm_id", "embd_id", "asr_id", "img2txt_id", "rerank_id", "tts_id")
TENANT_LLM_SYNC_FIELDS = ("model_type", "api_key", "api_base", "max_tokens")


def _get_first_user():
    users = UserService.query(
        status=StatusEnum.VALID.value,
        reverse=False,
        order_by="create_time",
    )
    return users[0] if users else None


def is_first_user(user_id):
    """Return True when ``user_id`` is the system-wide model administrator.

    The first registered user owns the canonical model configuration; every
    other tenant mirrors it. Callers that mutate model state must allow only
    this user through.
    """
    first_user = _get_first_user()
    return first_user is not None and first_user.id == user_id


@DB.connection_context()
def sync_llm_config_from_first_user(user_id):
    """Mirror the first registered user's model settings to ``user_id``'s tenant.

    Default model selections (llm_id, embd_id, ...) and ``tenant_llm`` rows are
    overwritten so the tenant matches the administrator exactly. Per-tenant
    ``used_tokens`` counters are preserved because they are usage telemetry,
    not configuration.

    Returns True when a sync ran, False when the call was a no-op (target
    tenant missing, target *is* the admin, or admin tenant missing).
    """
    tenant_info = TenantService.get_info_by(user_id)
    if not tenant_info:
        return False

    first_user = _get_first_user()
    if not first_user or first_user.id == user_id:
        return False

    source_tenants = TenantService.get_info_by(first_user.id)
    if not source_tenants:
        return False
    source = source_tenants[0]

    update_fields = {field: source.get(field) for field in DEFAULT_MODEL_FIELDS if field in source}
    TenantService.update_by_id(user_id, update_fields)

    source_llms = TenantLLMService.query(tenant_id=first_user.id)
    source_by_key = {(llm.llm_factory, llm.llm_name): llm for llm in source_llms}
    existing_by_key = {(llm.llm_factory, llm.llm_name): llm for llm in TenantLLMService.query(tenant_id=user_id)}

    now = current_timestamp()
    now_date = datetime_format(datetime.now())
    with DB.atomic():
        for llm_factory, llm_name in existing_by_key:
            if (llm_factory, llm_name) not in source_by_key:
                TenantLLM.delete().where(
                    TenantLLM.tenant_id == user_id,
                    TenantLLM.llm_factory == llm_factory,
                    TenantLLM.llm_name == llm_name,
                ).execute()

        new_llms = []
        for (llm_factory, llm_name), source_llm in source_by_key.items():
            llm_config = {field: getattr(source_llm, field) for field in TENANT_LLM_SYNC_FIELDS}
            if (llm_factory, llm_name) in existing_by_key:
                llm_config.update(
                    {
                        "update_time": now,
                        "update_date": now_date,
                    }
                )
                TenantLLM.update(llm_config).where(
                    TenantLLM.tenant_id == user_id,
                    TenantLLM.llm_factory == llm_factory,
                    TenantLLM.llm_name == llm_name,
                ).execute()
            else:
                new_llms.append(
                    {
                        "tenant_id": user_id,
                        "llm_factory": llm_factory,
                        "llm_name": llm_name,
                        **llm_config,
                        "create_time": now,
                        "create_date": now_date,
                        "update_time": now,
                        "update_date": now_date,
                    }
                )

        if new_llms:
            TenantLLM.insert_many(new_llms).execute()
    return True


def _fanout_to_all_users(first_user_id):
    synced = 0
    for user in UserService.query(status=StatusEnum.VALID.value):
        if user.id == first_user_id:
            continue
        try:
            if sync_llm_config_from_first_user(user.id):
                synced += 1
        except Exception:
            logging.exception("sync_llm_config_from_first_user failed for user_id=%s", user.id)
    return synced


def fanout_llm_config_from_admin(changed_user_id=None, blocking=False):
    """Propagate the administrator's model configuration to every other user.

    The ``changed_user_id`` guard makes this safe to call from any write
    endpoint: the fanout only runs when the change actually originated from
    the administrator. By default the work is dispatched on a background
    daemon thread so admin write requests stay responsive; pass
    ``blocking=True`` for tests that need deterministic completion.

    Returns the number of users synced when ``blocking`` is True, otherwise 0
    (the background thread reports its own count via logging on failure).
    """
    first_user = _get_first_user()
    if not first_user:
        return 0
    if changed_user_id and changed_user_id != first_user.id:
        return 0

    if blocking:
        return _fanout_to_all_users(first_user.id)

    thread = threading.Thread(
        target=_fanout_to_all_users,
        args=(first_user.id,),
        name="llm-config-fanout",
        daemon=True,
    )
    thread.start()
    return 0
