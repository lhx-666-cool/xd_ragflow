from api.db import StatusEnum
from api.db.services.tenant_llm_service import TenantLLMService
from api.db.services.user_service import TenantService, UserService


def _get_first_user():
    users = UserService.query(
        status=StatusEnum.VALID.value,
        reverse=False,
        order_by="create_time",
    )
    return users[0] if users else None


def copy_llm_config_from_first_user(user_id):
    """If the user has no model configured, copy LLM config from the first registered user.
    Returns True if config was copied, False otherwise."""
    tenant_info = TenantService.get_info_by(user_id)
    if not tenant_info:
        return False
    tenant = tenant_info[0]
    if tenant.get("llm_id") and tenant.get("embd_id"):
        return False  # already configured

    first_user = _get_first_user()
    if not first_user or first_user.id == user_id:
        return False

    source_tenants = TenantService.get_info_by(first_user.id)
    if not source_tenants:
        return False
    source = source_tenants[0]
    if not source.get("llm_id") or not source.get("embd_id"):
        return False

    update_fields = {
        "llm_id": source["llm_id"],
        "embd_id": source["embd_id"],
    }
    for field in ("asr_id", "img2txt_id", "rerank_id"):
        if source.get(field):
            update_fields[field] = source[field]
    TenantService.update_by_id(user_id, update_fields)

    source_llms = TenantLLMService.query(tenant_id=first_user.id)
    existing_keys = {
        (r.llm_factory, r.llm_name)
        for r in TenantLLMService.query(tenant_id=user_id)
    }
    new_llms = [
        {
            "tenant_id": user_id,
            "llm_factory": llm.llm_factory,
            "llm_name": llm.llm_name,
            "model_type": llm.model_type,
            "api_key": llm.api_key,
            "api_base": llm.api_base,
            "max_tokens": llm.max_tokens,
        }
        for llm in source_llms
        if (llm.llm_factory, llm.llm_name) not in existing_keys
    ]
    if new_llms:
        TenantLLMService.insert_many(new_llms)
    return True
