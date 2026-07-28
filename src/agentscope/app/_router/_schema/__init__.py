# -*- coding: utf-8 -*-
"""Schema models for the agent service."""

from ._chat import ChatRequest, ChatTriggerResponse
from ._model import ListModelsResponse, ListModelsRequest
from ._tts_model import ListTTSModelsResponse, ListTTSModelsRequest
from ._schedule import (
    CreateScheduleRequest,
    CreateScheduleResponse,
    ListSchedulesResponse,
    ScheduleSessionsResponse,
    UpdateScheduleRequest,
)
from ._agent import (
    AgentListData,
    AgentListView,
    AgentSchemaResponse,
    AgentSchemaV2Response,
    ListAgentsResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
)
from ._credential import (
    CreateCredentialRequest,
    CreateCredentialResponse,
    UpdateCredentialRequest,
    ListCredentialsResponse,
    ListCredentialSchemasResponse,
)
from ._knowledge_base import (
    CreateKnowledgeBaseRequest,
    CreateKnowledgeBaseResponse,
    KbEmbeddingProvider,
    KbMiddlewareParametersSchemaResponse,
    KnowledgeDocumentView,
    ListKbEmbeddingModelsResponse,
    ListKnowledgeBasesResponse,
    ListKnowledgeDocumentsResponse,
    ListKnowledgeDocumentStatusResponse,
    ListSupportedContentTypesResponse,
    SearchKnowledgeBaseRequest,
    SearchKnowledgeBaseResponse,
    UpdateKnowledgeBaseRequest,
    UploadKnowledgeDocumentResponse,
)
from ._skillhub_catalog import (
    SkillHubCatalogConfigResponse,
    SkillHubCatalogResponse,
)
from ._session import (
    CreateSessionRequest,
    CreateSessionResponse,
    InterruptSessionResponse,
    UpdateSessionRequest,
    ListSessionsResponse,
    ListMessagesResponse,
    ListExecutionTracesResponse,
    MemoryFileResponse,
    MemoryTreeEntry,
    MemoryTreeResponse,
    SessionStatus,
    SessionStatusResponse,
    SessionView,
    TeamDetailResponse,
    TeamMemberView,
    UpdateMemoryFileRequest,
)
from ._subagent import ListSubagentRunsResponse

__all__ = [
    # Agent
    "AgentListData",
    "AgentListView",
    "AgentSchemaResponse",
    "AgentSchemaV2Response",
    "ListAgentsResponse",
    "CreateAgentRequest",
    "CreateAgentResponse",
    "UpdateAgentRequest",
    "ListSchedulesResponse",
    # Chat
    "ChatRequest",
    "ChatTriggerResponse",
    # Credential
    "CreateCredentialRequest",
    "CreateCredentialResponse",
    "UpdateCredentialRequest",
    "ListCredentialsResponse",
    "ListCredentialSchemasResponse",
    # Knowledge base
    "CreateKnowledgeBaseRequest",
    "CreateKnowledgeBaseResponse",
    "KbEmbeddingProvider",
    "KbMiddlewareParametersSchemaResponse",
    "KnowledgeDocumentView",
    "ListKbEmbeddingModelsResponse",
    "ListKnowledgeBasesResponse",
    "ListKnowledgeDocumentsResponse",
    "ListKnowledgeDocumentStatusResponse",
    "ListSupportedContentTypesResponse",
    "SearchKnowledgeBaseRequest",
    "SearchKnowledgeBaseResponse",
    "UpdateKnowledgeBaseRequest",
    "UploadKnowledgeDocumentResponse",
    # SkillHub catalog
    "SkillHubCatalogConfigResponse",
    "SkillHubCatalogResponse",
    # Model
    "ListModelsRequest",
    "ListModelsResponse",
    # TTS Model
    "ListTTSModelsRequest",
    "ListTTSModelsResponse",
    # Schedule
    "CreateScheduleRequest",
    "CreateScheduleResponse",
    "ListSchedulesResponse",
    "ScheduleSessionsResponse",
    "UpdateScheduleRequest",
    # Session
    "CreateSessionRequest",
    "CreateSessionResponse",
    "InterruptSessionResponse",
    "UpdateSessionRequest",
    "ListSessionsResponse",
    "ListMessagesResponse",
    "ListExecutionTracesResponse",
    "MemoryFileResponse",
    "MemoryTreeEntry",
    "MemoryTreeResponse",
    "SessionStatus",
    "SessionStatusResponse",
    "SessionView",
    "ListSubagentRunsResponse",
    "TeamDetailResponse",
    "TeamMemberView",
    "UpdateMemoryFileRequest",
]
