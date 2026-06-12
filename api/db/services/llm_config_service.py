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

_fanout_lock = threading.Lock()
_fanout_running = False
_fanout_pending = False


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


def _get_tenant_by_id(tenant_id):
    return TenantService.model.get_or_none(TenantService.model.id == tenant_id)


def _update_tenant_by_id(tenant_id, update_fields):
    if not update_fields:
        return 0

    data = dict(update_fields)
    data["update_time"] = current_timestamp()
    data["update_date"] = datetime_format(datetime.now())
    return TenantService.model.update(data).where(TenantService.model.id == tenant_id).execute()


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

    update_fields = {field: source.get(field) for field in DEFAULT_MODEL_FIELDS if field in source and source.get(field) is not None}

    source_llms = TenantLLMService.query(tenant_id=first_user.id)
    source_by_key = {(llm.llm_factory, llm.llm_name): llm for llm in source_llms}
    existing_by_key = {(llm.llm_factory, llm.llm_name): llm for llm in TenantLLMService.query(tenant_id=user_id)}

    now = current_timestamp()
    now_date = datetime_format(datetime.now())
    with DB.atomic():
        if update_fields:
            _update_tenant_by_id(user_id, update_fields)

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


def _tenant_has_model(tenant_id, llm_name, llm_factory=None):
    filters = [TenantLLM.tenant_id == tenant_id, TenantLLM.llm_name == llm_name]
    if llm_factory:
        filters.append(TenantLLM.llm_factory == llm_factory)
    return TenantLLM.select(TenantLLM.llm_name).where(*filters).exists()


def _default_points_to_deleted_model(tenant_id, model_id, deleted_factory, deleted_llm_name=None):
    if not model_id:
        return False

    model_name, model_factory = TenantLLMService.split_model_name_and_factory(model_id)
    if model_factory:
        if model_factory != deleted_factory:
            return False
        return deleted_llm_name is None or model_name == deleted_llm_name

    if deleted_llm_name and model_name != deleted_llm_name:
        return False
    return not _tenant_has_model(tenant_id, model_name)


def _clear_deleted_default_models(tenant_id, deleted_factory, deleted_llm_name=None):
    tenant = _get_tenant_by_id(tenant_id)
    if not tenant:
        return []

    update_fields = {}
    for field in DEFAULT_MODEL_FIELDS:
        model_id = getattr(tenant, field, None)
        if _default_points_to_deleted_model(tenant_id, model_id, deleted_factory, deleted_llm_name):
            update_fields[field] = ""

    cleared_fields = list(update_fields.keys())
    if update_fields:
        _update_tenant_by_id(tenant_id, update_fields)
    return cleared_fields


@DB.connection_context()
def delete_admin_llm_config(admin_id, llm_factory, llm_name=None):
    """Delete administrator model rows and clear default model fields that point to them."""
    filters = [
        TenantLLM.tenant_id == admin_id,
        TenantLLM.llm_factory == llm_factory,
    ]
    if llm_name is not None:
        filters.append(TenantLLM.llm_name == llm_name)

    with DB.atomic():
        deleted_count = TenantLLM.delete().where(*filters).execute()
        cleared_fields = _clear_deleted_default_models(admin_id, llm_factory, llm_name)
    return deleted_count, cleared_fields


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


def _coalesced_fanout_worker(first_user_id):
    global _fanout_pending, _fanout_running

    while True:
        try:
            _fanout_to_all_users(first_user_id)
        except Exception:
            logging.exception("llm config fanout failed")

        with _fanout_lock:
            if not _fanout_pending:
                _fanout_running = False
                return
            _fanout_pending = False


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

    global _fanout_pending, _fanout_running
    with _fanout_lock:
        if _fanout_running:
            _fanout_pending = True
            return 0
        _fanout_running = True
        _fanout_pending = False

    thread = threading.Thread(
        target=_coalesced_fanout_worker,
        args=(first_user.id,),
        name="llm-config-fanout",
        daemon=True,
    )
    thread.start()
    return 0
