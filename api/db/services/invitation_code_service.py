#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#

import re
import secrets
from datetime import datetime

from api.db import StatusEnum
from api.db.db_models import DB, InvitationCode
from api.db.services.common_service import CommonService
from common.time_utils import current_timestamp, datetime_format


INVITATION_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITATION_CODE_LENGTH = 12


def normalize_invitation_code(code: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", code or "").upper()


def format_invitation_code(code: str) -> str:
    return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


class InvitationCodeService(CommonService):
    model = InvitationCode

    @classmethod
    @DB.connection_context()
    def create_code(cls, user_id: str, tenant_id: str | None = None) -> str:
        for _ in range(20):
            code = "".join(secrets.choice(INVITATION_CODE_ALPHABET) for _ in range(INVITATION_CODE_LENGTH))
            if not cls.model.select().where(cls.model.code == code).exists():
                cls.insert(
                    code=code,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    status=StatusEnum.VALID.value,
                )
                return format_invitation_code(code)
        raise RuntimeError("Failed to generate invitation code")

    @classmethod
    @DB.connection_context()
    def is_valid_code(cls, code: str | None) -> bool:
        normalized = normalize_invitation_code(code)
        if not normalized:
            return False
        return (
            cls.model.select()
            .where(
                cls.model.code == normalized,
                cls.model.status == StatusEnum.VALID.value,
            )
            .exists()
        )

    @classmethod
    @DB.connection_context()
    def consume_code(cls, code: str | None) -> bool:
        normalized = normalize_invitation_code(code)
        if not normalized:
            return False

        with DB.atomic():
            invitation = (
                cls.model.select()
                .where(
                    cls.model.code == normalized,
                    cls.model.status == StatusEnum.VALID.value,
                )
                .for_update()
                .get_or_none()
            )
            if not invitation:
                return False

            updated = (
                cls.model.update(
                    {
                        "status": StatusEnum.INVALID.value,
                        "visit_time": datetime.now(),
                        "update_time": current_timestamp(),
                        "update_date": datetime_format(datetime.now()),
                    }
                )
                .where(
                    cls.model.id == invitation.id,
                    cls.model.status == StatusEnum.VALID.value,
                )
                .execute()
            )
            return updated == 1
