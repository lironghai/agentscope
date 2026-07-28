# -*- coding: utf-8 -*-
"""Tests for shared-credential admin enforcement in the credential router."""

from unittest.async_case import IsolatedAsyncioTestCase

from fastapi import HTTPException

from agentscope.app._router._credential import create_credential
from agentscope.app._router._schema import CreateCredentialRequest
from agentscope.app.storage import CredentialRecord
from agentscope.credential import CredentialBase


class _FakeStorage:
    """Minimal storage double for credential router tests."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], CredentialRecord] = {}

    async def upsert_credential(
        self,
        user_id: str,
        credential_data: CredentialBase,
    ) -> str:
        """Persist a credential record."""
        data = credential_data.model_dump(mode="json")
        record = CredentialRecord(
            id=credential_data.id,
            user_id=user_id,
            data=data,
        )
        self.records[(user_id, record.id)] = record
        return record.id


class CredentialRouterShareConfigTest(IsolatedAsyncioTestCase):
    """Validate only admins can opt credentials into global sharing."""

    async def test_non_admin_cannot_create_shared_credential(self) -> None:
        """Non-admin users must not be able to create shared credentials."""
        with self.assertRaises(HTTPException) as ctx:
            await create_credential(
                CreateCredentialRequest(
                    data={
                        "type": "ollama_credential",
                        "host": "http://localhost:11434",
                        "shared": True,
                    },
                ),
                user_id="alice",
                storage=_FakeStorage(),  # type: ignore[arg-type]
                admin_user_ids={"admin"},
            )

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_admin_can_create_shared_credential(self) -> None:
        """Admin users may create shared credentials."""
        storage = _FakeStorage()

        response = await create_credential(
            CreateCredentialRequest(
                data={
                    "type": "ollama_credential",
                    "host": "http://localhost:11434",
                    "shared": True,
                },
            ),
            user_id="admin",
            storage=storage,  # type: ignore[arg-type]
            admin_user_ids={"admin"},
        )

        record = storage.records[("admin", response.credential_id)]
        self.assertIs(record.data["shared"], True)
