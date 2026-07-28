# -*- coding: utf-8 -*-
"""Service layer for the AgentScope app."""
from ._access import (
    AgentView,
    CredentialView,
    KnowledgeBaseView,
    ResourceAccessService,
)
from ._agent_workspace import (
    AgentWorkspaceFile,
    AgentWorkspaceListing,
    AgentWorkspaceService,
)
from ._agent_binding import (
    AgentBindingMaterializationResult,
    AgentBindingResolver,
)
from ._chat import ChatService
from ._embedding import get_embedding_model
from ._index_sweeper import IndexSweeper
from ._index_task_consumer import IndexTaskConsumer
from ._index_worker import IndexWorker
from ._knowledge_base import KnowledgeBaseService
from ._long_term_memory_browser import LongTermMemoryBrowser
from ._model import get_model
from ._tts_model import get_tts_model
from ._session import SessionService, SessionStatus
from ._session_projection import SessionProjection
from ._skillhub_catalog import SkillHubCatalogClient
from ._projectors import SubagentHitlProjector
from ._subagent_registry import SubagentRegistryService
from ._toolkit import get_toolkit

__all__ = [
    "AgentView",
    "AgentBindingMaterializationResult",
    "AgentBindingResolver",
    "AgentWorkspaceFile",
    "AgentWorkspaceListing",
    "AgentWorkspaceService",
    "ChatService",
    "CredentialView",
    "IndexSweeper",
    "IndexTaskConsumer",
    "IndexWorker",
    "KnowledgeBaseService",
    "KnowledgeBaseView",
    "LongTermMemoryBrowser",
    "ResourceAccessService",
    "SessionService",
    "SessionStatus",
    "SessionProjection",
    "SkillHubCatalogClient",
    "SubagentHitlProjector",
    "SubagentRegistryService",
    "get_embedding_model",
    "get_model",
    "get_tts_model",
    "get_toolkit",
]
