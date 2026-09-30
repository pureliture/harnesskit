use serde::{Deserialize, Serialize};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct OpenLocalSourcePreviewRequestDto {
    pub snapshot_id: String,
    pub instance_id: String,
    pub view_generation: u64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReadLocalSourcePreviewChunkRequestDto {
    pub preview_session_id: String,
    pub source_revision: String,
    pub chunk_index: u64,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CloseLocalSourcePreviewRequestDto {
    pub view_generation: u64,
    pub preview_session_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalSourceFormatDto {
    Markdown,
    Json,
    Yaml,
    Text,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalSourceIssueDto {
    pub code: String,
    pub safe_message: String,
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalSourcePreviewHeaderDto {
    pub preview_session_id: String,
    pub view_generation: u64,
    pub snapshot_id: String,
    pub instance_id: String,
    pub canonical_path: String,
    pub source_revision: String,
    pub changed_since_snapshot: bool,
    pub format: LocalSourceFormatDto,
    pub total_bytes: u64,
    pub total_chunks: u64,
    pub chunk_bytes: u64,
    pub selected_chunk_index: Option<u64>,
    pub issue: Option<LocalSourceIssueDto>,
}

impl std::fmt::Debug for LocalSourcePreviewHeaderDto {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("LocalSourcePreviewHeaderDto")
            .field("preview_session_id", &self.preview_session_id)
            .field("view_generation", &self.view_generation)
            .field("snapshot_id", &self.snapshot_id)
            .field("instance_id", &self.instance_id)
            .field("canonical_path", &"<redacted-path>")
            .field("source_revision", &self.source_revision)
            .field("changed_since_snapshot", &self.changed_since_snapshot)
            .field("format", &self.format)
            .field("total_bytes", &self.total_bytes)
            .field("total_chunks", &self.total_chunks)
            .field("chunk_bytes", &self.chunk_bytes)
            .field("selected_chunk_index", &self.selected_chunk_index)
            .field("issue", &self.issue)
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalSourceMarkdownBlockKindDto {
    Heading,
    Paragraph,
    List,
    Table,
    Blockquote,
    ThematicBreak,
    FencedCode,
    InlineCode,
    LiteralText,
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum MarkdownSemanticNodeDto {
    Text { text: String },
    Heading { level: u8, children: Vec<Self> },
    Paragraph { children: Vec<Self> },
    UnorderedList { children: Vec<Self> },
    OrderedList { start: u64, children: Vec<Self> },
    ListItem { children: Vec<Self> },
    Strong { children: Vec<Self> },
    Emphasis { children: Vec<Self> },
    LinkText { children: Vec<Self> },
    InlineCode { text: String },
    Blockquote { children: Vec<Self> },
    Table { children: Vec<Self> },
    TableHead { children: Vec<Self> },
    TableBody { children: Vec<Self> },
    TableRow { children: Vec<Self> },
    TableHeaderCell { children: Vec<Self> },
    TableCell { children: Vec<Self> },
    CodeBlock { text: String },
    ThematicBreak,
    LiteralText { text: String },
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MarkdownBlockFragmentDto {
    pub block_id: String,
    pub kind: LocalSourceMarkdownBlockKindDto,
    #[serde(default)]
    pub nodes: Vec<MarkdownSemanticNodeDto>,
    pub starts_block: bool,
    pub ends_block: bool,
}

impl std::fmt::Debug for MarkdownBlockFragmentDto {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("MarkdownBlockFragmentDto")
            .field("block_id", &self.block_id)
            .field("kind", &self.kind)
            .field("semantic_node_count", &self.nodes.len())
            .field("starts_block", &self.starts_block)
            .field("ends_block", &self.ends_block)
            .finish()
    }
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "format", rename_all = "snake_case")]
pub enum LocalSourcePreviewContentDto {
    Markdown {
        block_fragments: Vec<MarkdownBlockFragmentDto>,
    },
    Text {
        before: String,
        selected: Option<String>,
        after: String,
    },
}

impl std::fmt::Debug for LocalSourcePreviewContentDto {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Markdown { block_fragments } => formatter
                .debug_struct("Markdown")
                .field("block_fragment_count", &block_fragments.len())
                .finish(),
            Self::Text {
                before,
                selected,
                after,
            } => formatter
                .debug_struct("Text")
                .field("before_bytes", &before.len())
                .field("selected_bytes", &selected.as_ref().map(String::len))
                .field("after_bytes", &after.len())
                .finish(),
        }
    }
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalSourcePreviewChunkDto {
    pub preview_session_id: String,
    pub source_revision: String,
    pub chunk_index: u64,
    pub is_last: bool,
    pub content: LocalSourcePreviewContentDto,
}

impl std::fmt::Debug for LocalSourcePreviewChunkDto {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("LocalSourcePreviewChunkDto")
            .field("preview_session_id", &self.preview_session_id)
            .field("source_revision", &self.source_revision)
            .field("chunk_index", &self.chunk_index)
            .field("is_last", &self.is_last)
            .field("content", &self.content)
            .finish()
    }
}

impl LocalSourcePreviewChunkDto {
    pub(crate) fn ensure_serialized_bound(&self) -> Result<(), &'static str> {
        const MAX_SERIALIZED_BYTES: usize = 256 * 1024;

        let mut counter = SerializedSizeCounter {
            bytes: 0,
            limit: MAX_SERIALIZED_BYTES,
        };
        serde_json::to_writer(&mut counter, self).map_err(|_| "source_chunk_bound_exceeded")?;
        Ok(())
    }
}

struct SerializedSizeCounter {
    bytes: usize,
    limit: usize,
}

impl std::io::Write for SerializedSizeCounter {
    fn write(&mut self, buffer: &[u8]) -> std::io::Result<usize> {
        let next = self.bytes.saturating_add(buffer.len());
        if next > self.limit {
            return Err(std::io::Error::other(
                "serialized source DTO bound exceeded",
            ));
        }
        self.bytes = next;
        Ok(buffer.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalSourcePreviewCloseDto {
    pub closed: bool,
    pub cancelled: bool,
}

impl From<OpenLocalSourcePreviewRequestDto> for OpenSourceRequest {
    fn from(value: OpenLocalSourcePreviewRequestDto) -> Self {
        Self {
            snapshot_id: value.snapshot_id,
            instance_id: value.instance_id,
            view_generation: value.view_generation,
        }
    }
}

impl From<ReadLocalSourcePreviewChunkRequestDto> for ReadSourceChunkRequest {
    fn from(value: ReadLocalSourcePreviewChunkRequestDto) -> Self {
        Self {
            preview_session_id: value.preview_session_id,
            source_revision: value.source_revision,
            chunk_index: value.chunk_index,
        }
    }
}

impl From<DomainSourceFormat> for LocalSourceFormatDto {
    fn from(value: DomainSourceFormat) -> Self {
        match value {
            DomainSourceFormat::Markdown => Self::Markdown,
            DomainSourceFormat::Json => Self::Json,
            DomainSourceFormat::Yaml => Self::Yaml,
            DomainSourceFormat::Text => Self::Text,
        }
    }
}

impl From<DomainSourceIssue> for LocalSourceIssueDto {
    fn from(value: DomainSourceIssue) -> Self {
        Self {
            code: value.code,
            safe_message: value.safe_message,
        }
    }
}

impl From<DomainMarkdownBlockKind> for LocalSourceMarkdownBlockKindDto {
    fn from(value: DomainMarkdownBlockKind) -> Self {
        match value {
            DomainMarkdownBlockKind::Heading => Self::Heading,
            DomainMarkdownBlockKind::Paragraph => Self::Paragraph,
            DomainMarkdownBlockKind::List => Self::List,
            DomainMarkdownBlockKind::Table => Self::Table,
            DomainMarkdownBlockKind::Blockquote => Self::Blockquote,
            DomainMarkdownBlockKind::ThematicBreak => Self::ThematicBreak,
            DomainMarkdownBlockKind::FencedCode => Self::FencedCode,
            DomainMarkdownBlockKind::InlineCode => Self::InlineCode,
            DomainMarkdownBlockKind::LiteralText => Self::LiteralText,
        }
    }
}

impl From<DomainMarkdownSemanticNode> for MarkdownSemanticNodeDto {
    fn from(value: DomainMarkdownSemanticNode) -> Self {
        match value {
            DomainMarkdownSemanticNode::Text { text } => Self::Text { text },
            DomainMarkdownSemanticNode::Heading { level, children } => Self::Heading {
                level,
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::Paragraph { children } => Self::Paragraph {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::UnorderedList { children } => Self::UnorderedList {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::OrderedList { start, children } => Self::OrderedList {
                start,
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::ListItem { children } => Self::ListItem {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::Strong { children } => Self::Strong {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::Emphasis { children } => Self::Emphasis {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::LinkText { children } => Self::LinkText {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::InlineCode { text } => Self::InlineCode { text },
            DomainMarkdownSemanticNode::Blockquote { children } => Self::Blockquote {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::Table { children } => Self::Table {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::TableHead { children } => Self::TableHead {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::TableBody { children } => Self::TableBody {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::TableRow { children } => Self::TableRow {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::TableHeaderCell { children } => Self::TableHeaderCell {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::TableCell { children } => Self::TableCell {
                children: children.into_iter().map(Into::into).collect(),
            },
            DomainMarkdownSemanticNode::CodeBlock { text } => Self::CodeBlock { text },
            DomainMarkdownSemanticNode::ThematicBreak => Self::ThematicBreak,
            DomainMarkdownSemanticNode::LiteralText { text } => Self::LiteralText { text },
        }
    }
}

impl From<DomainMarkdownBlockFragment> for MarkdownBlockFragmentDto {
    fn from(value: DomainMarkdownBlockFragment) -> Self {
        Self {
            block_id: value.block_id,
            kind: value.kind.into(),
            nodes: value.nodes.into_iter().map(Into::into).collect(),
            starts_block: value.starts_block,
            ends_block: value.ends_block,
        }
    }
}

impl From<DomainSourcePreviewHeader> for LocalSourcePreviewHeaderDto {
    fn from(value: DomainSourcePreviewHeader) -> Self {
        Self {
            preview_session_id: value.preview_session_id,
            view_generation: value.view_generation,
            snapshot_id: value.snapshot_id,
            instance_id: value.instance_id,
            canonical_path: value.canonical_path,
            source_revision: value.source_revision,
            changed_since_snapshot: value.changed_since_snapshot,
            format: value.format.into(),
            total_bytes: value.total_bytes,
            total_chunks: value.total_chunks,
            chunk_bytes: value.chunk_bytes,
            selected_chunk_index: value.selected_chunk_index,
            issue: value.issue.map(Into::into),
        }
    }
}

impl From<DomainSourcePreviewChunk> for LocalSourcePreviewChunkDto {
    fn from(value: DomainSourcePreviewChunk) -> Self {
        let content = match value.content {
            DomainSourcePreviewContent::Markdown { block_fragments } => {
                LocalSourcePreviewContentDto::Markdown {
                    block_fragments: block_fragments.into_iter().map(Into::into).collect(),
                }
            }
            DomainSourcePreviewContent::Text {
                before,
                selected,
                after,
            } => LocalSourcePreviewContentDto::Text {
                before,
                selected,
                after,
            },
        };
        Self {
            preview_session_id: value.preview_session_id,
            source_revision: value.source_revision,
            chunk_index: value.chunk_index,
            is_last: value.is_last,
            content,
        }
    }
}

impl From<DomainSourcePreviewClose> for LocalSourcePreviewCloseDto {
    fn from(value: DomainSourcePreviewClose) -> Self {
        Self {
            closed: value.closed,
            cancelled: value.cancelled,
        }
    }
}

#[cfg(test)]
mod source_preview_size_tests {
    use super::*;

    fn chunk_with_text(text: String) -> LocalSourcePreviewChunkDto {
        LocalSourcePreviewChunkDto {
            preview_session_id: "local-source-preview-1".to_string(),
            source_revision: "a".repeat(64),
            chunk_index: 0,
            is_last: true,
            content: LocalSourcePreviewContentDto::Markdown {
                block_fragments: vec![MarkdownBlockFragmentDto {
                    block_id: "literal-chunk-0".to_string(),
                    kind: LocalSourceMarkdownBlockKindDto::LiteralText,
                    nodes: vec![MarkdownSemanticNodeDto::LiteralText { text }],
                    starts_block: true,
                    ends_block: true,
                }],
            },
        }
    }

    #[test]
    fn worst_case_allowed_68k_text_stays_within_the_serialized_dto_bound() {
        let pattern = "\"\\\n\t\r";
        let mut text = pattern.repeat((68 * 1024) / pattern.len() + 1);
        text.truncate(68 * 1024);
        let dto = chunk_with_text(text);

        assert!(dto.ensure_serialized_bound().is_ok());
        assert!(serde_json::to_vec(&dto).unwrap().len() <= 256 * 1024);
    }

    #[test]
    fn oversized_serialized_source_dto_is_rejected_before_ipc() {
        let dto = chunk_with_text("\"".repeat(140 * 1024));

        assert_eq!(
            dto.ensure_serialized_bound(),
            Err("source_chunk_bound_exceeded")
        );
    }
}

use crate::contexts::correlation::{
    CorrelationAvailability, CorrelationProjection, CorrelationState, InstanceCorrelation,
};
use crate::contexts::local::domain::{ParseState, SafeIssue, SafeSetting, Scope, SkippedPath};
use crate::contexts::local::MarkdownSemanticNode as DomainMarkdownSemanticNode;
use crate::contexts::local::{
    CoverageStatus, IgnoreSaveRescanOutcome, LocalAttemptReport, LocalAttemptState,
    LocalInstanceDetail, LocalKindCount, LocalPathAction, LocalPathActionOutcome, LocalQueryCounts,
    LocalQueryItem, LocalQueryRequest, LocalQueryResult, LocalRemovalApplyOutcome,
    LocalRemovalEffectKind, LocalRemovalMemberSummary, LocalRemovalPlanPreview,
    LocalRemovalReconciliation, LocalRemovalReconciliationState, LocalRemovalRescanState,
    LocalRemovalSourceGroupOutcome, LocalRemovalSourceGroupState, LocalScanProgress,
    LocalSessionState, LocalSnapshotHeader, LocalSnapshotStatus, LocalSnapshotSummary,
    LocationFilter, LocationScope, MarkdownBlockFragment as DomainMarkdownBlockFragment,
    MarkdownBlockKind as DomainMarkdownBlockKind, OpenSourceRequest, ProjectIgnoreSource,
    ProjectIgnoreSummary, ProjectLocationRecord, QualifiedTool, ReadSourceChunkRequest,
    SourceFormat as DomainSourceFormat, SourceIssue as DomainSourceIssue,
    SourcePreviewChunk as DomainSourcePreviewChunk, SourcePreviewClose as DomainSourcePreviewClose,
    SourcePreviewContent as DomainSourcePreviewContent,
    SourcePreviewHeader as DomainSourcePreviewHeader, StartLocalScanOutcome, SurfaceKind,
    SurfacePresence, ToolId,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalAttemptStateDto {
    Running,
    Complete,
    Partial,
    Failed,
}

impl From<LocalAttemptState> for LocalAttemptStateDto {
    fn from(value: LocalAttemptState) -> Self {
        match value {
            LocalAttemptState::Running => Self::Running,
            LocalAttemptState::Complete => Self::Complete,
            LocalAttemptState::Partial => Self::Partial,
            LocalAttemptState::Failed => Self::Failed,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalAttemptReportDto {
    pub attempt_id: String,
    pub state: LocalAttemptStateDto,
    pub error_code: Option<String>,
    pub progress: Option<LocalScanProgressDto>,
}

impl From<LocalAttemptReport> for LocalAttemptReportDto {
    fn from(value: LocalAttemptReport) -> Self {
        Self {
            attempt_id: value.attempt_id,
            state: value.state.into(),
            error_code: value.error_code,
            progress: value.progress.map(Into::into),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalScanProgressDto {
    pub adapter_id: String,
    pub surface_id: String,
    pub coverage_id: String,
    pub item_count: u64,
}

impl From<LocalScanProgress> for LocalScanProgressDto {
    fn from(value: LocalScanProgress) -> Self {
        Self {
            adapter_id: value.adapter_id,
            surface_id: value.surface_id,
            coverage_id: value.coverage_id,
            item_count: value.item_count,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalSnapshotStatusDto {
    Complete,
    Partial,
}

impl From<LocalSnapshotStatus> for LocalSnapshotStatusDto {
    fn from(value: LocalSnapshotStatus) -> Self {
        match value {
            LocalSnapshotStatus::Complete => Self::Complete,
            LocalSnapshotStatus::Partial => Self::Partial,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalSnapshotHeaderDto {
    pub snapshot_id: String,
    pub attempt_id: String,
    pub status: LocalSnapshotStatusDto,
}

impl From<LocalSnapshotHeader> for LocalSnapshotHeaderDto {
    fn from(value: LocalSnapshotHeader) -> Self {
        Self {
            snapshot_id: value.snapshot_id,
            attempt_id: value.attempt_id,
            status: value.status.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalScanStateDto {
    pub state_revision: u64,
    pub current_attempt: Option<LocalAttemptReportDto>,
    pub latest_terminal_report: Option<LocalAttemptReportDto>,
    pub latest_complete: Option<LocalSnapshotHeaderDto>,
    pub latest_partial: Option<LocalSnapshotHeaderDto>,
}

impl From<LocalSessionState> for LocalScanStateDto {
    fn from(value: LocalSessionState) -> Self {
        Self {
            state_revision: value.state_revision,
            current_attempt: value.current_attempt.map(Into::into),
            latest_terminal_report: value.latest_terminal_report.map(Into::into),
            latest_complete: value.latest_complete.map(Into::into),
            latest_partial: value.latest_partial.map(Into::into),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum StartLocalScanOutcomeDto {
    Accepted { attempt_id: String },
    AlreadyRunning { attempt_id: String },
    OperationBusy,
}

impl From<StartLocalScanOutcome> for StartLocalScanOutcomeDto {
    fn from(value: StartLocalScanOutcome) -> Self {
        match value {
            StartLocalScanOutcome::Accepted { attempt_id } => Self::Accepted { attempt_id },
            StartLocalScanOutcome::AlreadyRunning { attempt_id } => {
                Self::AlreadyRunning { attempt_id }
            }
            StartLocalScanOutcome::OperationBusy => Self::OperationBusy,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct GetProjectIgnoreRequestDto {
    pub snapshot_id: String,
    pub project_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveProjectIgnoreAndRescanRequestDto {
    pub snapshot_id: String,
    pub project_id: String,
    pub expected_source_revision: String,
    pub exact_text: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectIgnoreDto {
    pub project_id: String,
    pub source_revision: String,
    pub exact_text: String,
    pub byte_len: usize,
    pub missing: bool,
}

impl From<ProjectIgnoreSource> for ProjectIgnoreDto {
    fn from(value: ProjectIgnoreSource) -> Self {
        Self {
            project_id: value.project_id,
            source_revision: value.source_revision,
            exact_text: value.exact_text,
            byte_len: value.byte_len,
            missing: value.missing,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum IgnoreSaveRescanOutcomeDto {
    Accepted { attempt_id: String },
    QueuedAfterCurrent { current_attempt_id: String },
    OperationBusy,
}

impl From<IgnoreSaveRescanOutcome> for IgnoreSaveRescanOutcomeDto {
    fn from(value: IgnoreSaveRescanOutcome) -> Self {
        match value {
            IgnoreSaveRescanOutcome::Accepted { attempt_id } => Self::Accepted { attempt_id },
            IgnoreSaveRescanOutcome::QueuedAfterCurrent { current_attempt_id } => {
                Self::QueuedAfterCurrent { current_attempt_id }
            }
            IgnoreSaveRescanOutcome::OperationBusy => Self::OperationBusy,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SaveProjectIgnoreAndRescanResponseDto {
    pub source: ProjectIgnoreDto,
    pub rescan: IgnoreSaveRescanOutcomeDto,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LocationScopeDto {
    All,
    User,
    Project,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocationFilterDto {
    pub scope: LocationScopeDto,
    pub project_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ToolIdDto {
    Codex,
    ClaudeCode,
    Antigravity,
    AntigravityCli,
    Hermes,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SurfaceKindDto {
    Skill,
    Agent,
    Hook,
    Rule,
    Command,
    Workflow,
    Unclassified,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalInstanceActionDto {
    Reveal,
    CopyPath,
}

impl From<LocalInstanceActionDto> for LocalPathAction {
    fn from(value: LocalInstanceActionDto) -> Self {
        match value {
            LocalInstanceActionDto::Reveal => Self::Reveal,
            LocalInstanceActionDto::CopyPath => Self::CopyPath,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum LocalInstanceActionOutcomeDto {
    Revealed,
    PathCopied,
}

impl From<LocalPathActionOutcome> for LocalInstanceActionOutcomeDto {
    fn from(value: LocalPathActionOutcome) -> Self {
        match value {
            LocalPathActionOutcome::Revealed => Self::Revealed,
            LocalPathActionOutcome::PathCopied => Self::PathCopied,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PrepareLocalRemovalRequestDto {
    pub snapshot_id: String,
    pub instance_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ApplyLocalRemovalRequestDto {
    pub plan_id: String,
    pub plan_digest: String,
    pub confirmed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ReconcileLocalRemovalRequestDto {
    pub snapshot_id: String,
    pub expected_attempt_id: String,
    pub instance_ids: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalRemovalEffectDto {
    SharedConfigEntry,
    DedicatedFile,
}

impl From<LocalRemovalEffectKind> for LocalRemovalEffectDto {
    fn from(value: LocalRemovalEffectKind) -> Self {
        match value {
            LocalRemovalEffectKind::SharedConfigEntry => Self::SharedConfigEntry,
            LocalRemovalEffectKind::DedicatedFile => Self::DedicatedFile,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalRemovalMemberDto {
    pub instance_id: String,
    pub display_name: String,
    pub effect: Option<LocalRemovalEffectDto>,
    pub code: Option<String>,
}

impl From<LocalRemovalMemberSummary> for LocalRemovalMemberDto {
    fn from(value: LocalRemovalMemberSummary) -> Self {
        Self {
            instance_id: value.instance_id,
            display_name: value.display_name,
            effect: value.effect.map(Into::into),
            code: value.code,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RemovalPlanDto {
    pub plan_id: String,
    pub plan_digest: String,
    pub eligible_members: Vec<LocalRemovalMemberDto>,
    pub blocked_members: Vec<LocalRemovalMemberDto>,
    pub shared_config_entry_removal_count: usize,
    pub dedicated_file_deletion_count: usize,
    pub parent_folder_deletion_count: usize,
}

impl From<LocalRemovalPlanPreview> for RemovalPlanDto {
    fn from(value: LocalRemovalPlanPreview) -> Self {
        Self {
            plan_id: value.plan_id,
            plan_digest: value.plan_digest,
            eligible_members: value.eligible_members.into_iter().map(Into::into).collect(),
            blocked_members: value.blocked_members.into_iter().map(Into::into).collect(),
            shared_config_entry_removal_count: value.shared_config_entry_removal_count,
            dedicated_file_deletion_count: value.dedicated_file_deletion_count,
            parent_folder_deletion_count: value.parent_folder_deletion_count,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalRemovalSourceGroupStateDto {
    Success,
    FailedUnchanged,
    Indeterminate,
}

impl From<LocalRemovalSourceGroupState> for LocalRemovalSourceGroupStateDto {
    fn from(value: LocalRemovalSourceGroupState) -> Self {
        match value {
            LocalRemovalSourceGroupState::Success => Self::Success,
            LocalRemovalSourceGroupState::FailedUnchanged => Self::FailedUnchanged,
            LocalRemovalSourceGroupState::Indeterminate => Self::Indeterminate,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalRemovalSourceGroupOutcomeDto {
    pub state: LocalRemovalSourceGroupStateDto,
    pub member_ids: Vec<String>,
    pub code: Option<String>,
}

impl From<LocalRemovalSourceGroupOutcome> for LocalRemovalSourceGroupOutcomeDto {
    fn from(value: LocalRemovalSourceGroupOutcome) -> Self {
        Self {
            state: value.state.into(),
            member_ids: value.member_ids,
            code: value.code,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalRemovalRescanStateDto {
    NotStarted,
    Accepted,
    Queued,
    Unavailable,
    FailedToStart,
}

impl From<LocalRemovalRescanState> for LocalRemovalRescanStateDto {
    fn from(value: LocalRemovalRescanState) -> Self {
        match value {
            LocalRemovalRescanState::NotStarted => Self::NotStarted,
            LocalRemovalRescanState::Accepted => Self::Accepted,
            LocalRemovalRescanState::Queued => Self::Queued,
            LocalRemovalRescanState::Unavailable => Self::Unavailable,
            LocalRemovalRescanState::FailedToStart => Self::FailedToStart,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalRemovalOutcomeDto {
    pub source_group_outcomes: Vec<LocalRemovalSourceGroupOutcomeDto>,
    pub rescan_state: LocalRemovalRescanStateDto,
    pub rescan_attempt_id: Option<String>,
}

impl From<LocalRemovalApplyOutcome> for LocalRemovalOutcomeDto {
    fn from(value: LocalRemovalApplyOutcome) -> Self {
        Self {
            source_group_outcomes: value
                .source_group_outcomes
                .into_iter()
                .map(Into::into)
                .collect(),
            rescan_state: value.rescan_state.into(),
            rescan_attempt_id: value.rescan_attempt_id,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LocalRemovalReconciliationStateDto {
    Confirmed,
    CoverageIncomplete,
}

impl From<LocalRemovalReconciliationState> for LocalRemovalReconciliationStateDto {
    fn from(value: LocalRemovalReconciliationState) -> Self {
        match value {
            LocalRemovalReconciliationState::Confirmed => Self::Confirmed,
            LocalRemovalReconciliationState::CoverageIncomplete => Self::CoverageIncomplete,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalRemovalReconciliationDto {
    pub snapshot_id: String,
    pub state: LocalRemovalReconciliationStateDto,
    pub confirmed_absent_instance_ids: Vec<String>,
    pub unresolved_instance_ids: Vec<String>,
}

impl From<LocalRemovalReconciliation> for LocalRemovalReconciliationDto {
    fn from(value: LocalRemovalReconciliation) -> Self {
        Self {
            snapshot_id: value.snapshot_id,
            state: value.state.into(),
            confirmed_absent_instance_ids: value.confirmed_absent_instance_ids,
            unresolved_instance_ids: value.unresolved_instance_ids,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScopeDto {
    User,
    Project,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ParseStateDto {
    Parsed,
    Malformed,
    Unreadable,
    Unclassified,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalQueryRequestDto {
    pub snapshot_id: String,
    pub location_filter: LocationFilterDto,
    pub tool_id: Option<ToolIdDto>,
    pub kind: Option<SurfaceKindDto>,
    pub query: String,
    pub correlation_projection_id: Option<String>,
    pub verified_component_id: Option<String>,
}

impl From<LocationScopeDto> for LocationScope {
    fn from(value: LocationScopeDto) -> Self {
        match value {
            LocationScopeDto::All => Self::All,
            LocationScopeDto::User => Self::User,
            LocationScopeDto::Project => Self::Project,
        }
    }
}

impl From<ToolIdDto> for ToolId {
    fn from(value: ToolIdDto) -> Self {
        match value {
            ToolIdDto::Codex => Self::Codex,
            ToolIdDto::ClaudeCode => Self::ClaudeCode,
            ToolIdDto::Antigravity => Self::Antigravity,
            ToolIdDto::AntigravityCli => Self::AntigravityCli,
            ToolIdDto::Hermes => Self::Hermes,
        }
    }
}

impl From<SurfaceKindDto> for SurfaceKind {
    fn from(value: SurfaceKindDto) -> Self {
        match value {
            SurfaceKindDto::Skill => Self::Skill,
            SurfaceKindDto::Agent => Self::Agent,
            SurfaceKindDto::Hook => Self::Hook,
            SurfaceKindDto::Rule => Self::Rule,
            SurfaceKindDto::Command => Self::Command,
            SurfaceKindDto::Workflow => Self::Workflow,
            SurfaceKindDto::Unclassified => Self::Unclassified,
        }
    }
}

impl From<LocalQueryRequestDto> for LocalQueryRequest {
    fn from(value: LocalQueryRequestDto) -> Self {
        Self {
            snapshot_id: value.snapshot_id,
            location_filter: LocationFilter {
                scope: value.location_filter.scope.into(),
                project_id: value.location_filter.project_id,
            },
            tool_id: value.tool_id.map(Into::into),
            kind: value.kind.map(Into::into),
            query: value.query,
            correlation_projection_id: value.correlation_projection_id,
            verified_component_id: value.verified_component_id,
        }
    }
}

impl From<ToolId> for ToolIdDto {
    fn from(value: ToolId) -> Self {
        match value {
            ToolId::Codex => Self::Codex,
            ToolId::ClaudeCode => Self::ClaudeCode,
            ToolId::Antigravity => Self::Antigravity,
            ToolId::AntigravityCli => Self::AntigravityCli,
            ToolId::Hermes => Self::Hermes,
        }
    }
}

impl From<SurfaceKind> for SurfaceKindDto {
    fn from(value: SurfaceKind) -> Self {
        match value {
            SurfaceKind::Skill => Self::Skill,
            SurfaceKind::Agent => Self::Agent,
            SurfaceKind::Hook => Self::Hook,
            SurfaceKind::Rule => Self::Rule,
            SurfaceKind::Command => Self::Command,
            SurfaceKind::Workflow => Self::Workflow,
            SurfaceKind::Unclassified => Self::Unclassified,
        }
    }
}

impl From<Scope> for ScopeDto {
    fn from(value: Scope) -> Self {
        match value {
            Scope::User => Self::User,
            Scope::Project => Self::Project,
        }
    }
}

impl From<ParseState> for ParseStateDto {
    fn from(value: ParseState) -> Self {
        match value {
            ParseState::Parsed => Self::Parsed,
            ParseState::Malformed => Self::Malformed,
            ParseState::Unreadable => Self::Unreadable,
            ParseState::Unclassified => Self::Unclassified,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalQueryItemDto {
    pub instance_id: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub tool_id: ToolIdDto,
    pub surface_id: String,
    pub scope: ScopeDto,
    pub project_id: Option<String>,
    pub safe_locator: String,
    pub kind: SurfaceKindDto,
    pub display_name: String,
    pub name: Option<String>,
    pub description: Option<String>,
    pub description_source: Option<String>,
    pub parse_state: ParseStateDto,
    pub issue_codes: Vec<String>,
    pub correlation: CorrelationStateDto,
}

impl From<LocalQueryItem> for LocalQueryItemDto {
    fn from(value: LocalQueryItem) -> Self {
        Self::from_domain(value, CorrelationState::Uncorrelated)
    }
}

impl LocalQueryItemDto {
    fn from_domain(value: LocalQueryItem, correlation: CorrelationState) -> Self {
        Self {
            instance_id: value.instance_id,
            adapter_id: value.adapter_id,
            adapter_version: value.adapter_version,
            tool_id: value.tool_id.into(),
            surface_id: value.surface_id,
            scope: value.scope.into(),
            project_id: value.project_id,
            safe_locator: value.safe_locator,
            kind: value.kind.into(),
            display_name: value.display_name,
            name: value.name,
            description: value.description,
            description_source: value.description_source,
            parse_state: value.parse_state.into(),
            issue_codes: value.issue_codes,
            correlation: correlation.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalKindCountDto {
    pub kind: SurfaceKindDto,
    pub count: usize,
}

impl From<LocalKindCount> for LocalKindCountDto {
    fn from(value: LocalKindCount) -> Self {
        Self {
            kind: value.kind.into(),
            count: value.count,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalQueryCountsDto {
    pub total_instances: usize,
    pub matched_instances: usize,
    pub project_count: usize,
    pub tool_count: usize,
}

impl From<LocalQueryCounts> for LocalQueryCountsDto {
    fn from(value: LocalQueryCounts) -> Self {
        Self {
            total_instances: value.total_instances,
            matched_instances: value.matched_instances,
            project_count: value.project_count,
            tool_count: value.tool_count,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CoverageStatusDto {
    Complete,
    Partial,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SurfacePresenceDto {
    Present,
    NotPresent,
    Unknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CoverageSummaryDto {
    pub adapter_id: String,
    pub adapter_version: String,
    pub surface_id: String,
    pub root_id: Option<String>,
    pub scope: ScopeDto,
    pub project_id: Option<String>,
    pub status: CoverageStatusDto,
    pub presence: SurfacePresenceDto,
    pub item_count: usize,
    pub skipped_count: usize,
    pub issue_codes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SkippedPathSummaryDto {
    pub root_id: Option<String>,
    pub safe_locator: String,
    pub reason_code: String,
}

impl From<SkippedPath> for SkippedPathSummaryDto {
    fn from(value: SkippedPath) -> Self {
        Self {
            root_id: value.root_id,
            safe_locator: value.safe_relative_locator,
            reason_code: value.reason_code,
        }
    }
}

#[cfg(test)]
mod skipped_path_summary_dto_tests {
    use super::*;

    #[test]
    fn preserves_root_id_option_and_camel_case_json_field() {
        let root_owned = SkippedPathSummaryDto::from(SkippedPath {
            root_id: Some("root-1".to_owned()),
            safe_relative_locator: "settings.json".to_owned(),
            reason_code: "file_size_limit_exceeded".to_owned(),
        });
        let pre_root = SkippedPathSummaryDto::from(SkippedPath {
            root_id: None,
            safe_relative_locator: "marker:claude".to_owned(),
            reason_code: "marker_discovery_timeout".to_owned(),
        });

        assert_eq!(root_owned.root_id.as_deref(), Some("root-1"));
        assert_eq!(
            serde_json::to_value(root_owned).expect("serialize root-owned skipped path DTO"),
            serde_json::json!({
                "rootId": "root-1",
                "safeLocator": "settings.json",
                "reasonCode": "file_size_limit_exceeded",
            })
        );
        assert_eq!(pre_root.root_id, None);
        assert_eq!(
            serde_json::to_value(pre_root).expect("serialize skipped path DTO"),
            serde_json::json!({
                "rootId": null,
                "safeLocator": "marker:claude",
                "reasonCode": "marker_discovery_timeout",
            })
        );
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SafeIssueSummaryDto {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub project_id: Option<String>,
    pub code: String,
    pub safe_message: String,
    pub safe_locator: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProjectIgnoreSummaryDto {
    pub project_id: String,
    pub source_revision: String,
    pub rule_count: usize,
    pub excluded_path_count: usize,
}

impl From<ProjectIgnoreSummary> for ProjectIgnoreSummaryDto {
    fn from(value: ProjectIgnoreSummary) -> Self {
        Self {
            project_id: value.project_id,
            source_revision: value.source_revision,
            rule_count: value.rule_count,
            excluded_path_count: value.excluded_path_count,
        }
    }
}

impl From<SafeIssue> for SafeIssueSummaryDto {
    fn from(value: SafeIssue) -> Self {
        Self {
            project_id: value.project_id,
            code: value.code,
            safe_message: value.safe_message,
            safe_locator: value.safe_relative_locator,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalSnapshotSummaryDto {
    pub snapshot_id: String,
    pub status: LocalSnapshotStatusDto,
    pub scan_timestamp: String,
    pub app_version: String,
    pub coverage: Vec<CoverageSummaryDto>,
    pub skipped_paths: Vec<SkippedPathSummaryDto>,
    pub issues: Vec<SafeIssueSummaryDto>,
    pub project_ignore_summaries: Vec<ProjectIgnoreSummaryDto>,
}

impl From<LocalSnapshotSummary> for LocalSnapshotSummaryDto {
    fn from(value: LocalSnapshotSummary) -> Self {
        Self {
            snapshot_id: value.snapshot_id,
            status: value.status.into(),
            scan_timestamp: value.scan_timestamp,
            app_version: value.app_version,
            coverage: value.coverage.into_iter().map(Into::into).collect(),
            skipped_paths: value.skipped_paths.into_iter().map(Into::into).collect(),
            issues: value.issues.into_iter().map(Into::into).collect(),
            project_ignore_summaries: value
                .project_ignore_summaries
                .into_iter()
                .map(Into::into)
                .collect(),
        }
    }
}

impl From<crate::contexts::local::domain::CoverageRecord> for CoverageSummaryDto {
    fn from(value: crate::contexts::local::domain::CoverageRecord) -> Self {
        Self {
            adapter_id: value.adapter_id,
            adapter_version: value.adapter_version,
            surface_id: value.surface_id,
            root_id: value.root_id,
            scope: value.scope.into(),
            project_id: value.project_id,
            status: match value.status {
                CoverageStatus::Complete => CoverageStatusDto::Complete,
                CoverageStatus::Partial => CoverageStatusDto::Partial,
                CoverageStatus::Failed => CoverageStatusDto::Failed,
            },
            presence: match value.presence {
                SurfacePresence::Present => SurfacePresenceDto::Present,
                SurfacePresence::NotPresent => SurfacePresenceDto::NotPresent,
                SurfacePresence::Unknown => SurfacePresenceDto::Unknown,
            },
            item_count: value.item_count,
            skipped_count: value.skipped_count,
            issue_codes: value.issue_codes,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalQueryResultDto {
    pub snapshot_id: String,
    pub snapshot_summary: LocalSnapshotSummaryDto,
    pub projects: Vec<LocalProjectLocationDto>,
    pub qualified_tools: Vec<QualifiedToolDto>,
    pub items: Vec<LocalQueryItemDto>,
    pub kind_counts: Vec<LocalKindCountDto>,
    pub counts: LocalQueryCountsDto,
}

impl LocalQueryResultDto {
    pub fn from_domain_with_projects(
        value: LocalQueryResult,
        projection: Option<&CorrelationProjection>,
        projects: &[ProjectLocationRecord],
    ) -> Self {
        Self {
            snapshot_id: value.snapshot_id,
            snapshot_summary: value.snapshot_summary.into(),
            projects: projects.iter().map(Into::into).collect(),
            qualified_tools: value.qualified_tools.into_iter().map(Into::into).collect(),
            items: value
                .items
                .into_iter()
                .map(|item| {
                    let correlation = projection
                        .map(|projection| projection.correlation_for(&item.instance_id))
                        .unwrap_or(CorrelationState::Uncorrelated);
                    LocalQueryItemDto::from_domain(item, correlation)
                })
                .collect(),
            kind_counts: value.kind_counts.into_iter().map(Into::into).collect(),
            counts: value.counts.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct QualifiedToolDto {
    pub tool_id: ToolIdDto,
    pub adapter_id: String,
    pub adapter_version: String,
}

impl From<QualifiedTool> for QualifiedToolDto {
    fn from(value: QualifiedTool) -> Self {
        Self {
            tool_id: value.tool_id.into(),
            adapter_id: value.adapter_id,
            adapter_version: value.adapter_version,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalProjectLocationDto {
    pub project_id: String,
    pub display_name: String,
    pub canonical_path: String,
}

impl From<&ProjectLocationRecord> for LocalProjectLocationDto {
    fn from(value: &ProjectLocationRecord) -> Self {
        Self {
            project_id: value.project_id.clone(),
            display_name: display_os_bytes(value.display_name.as_encoded_bytes()),
            canonical_path: display_os_bytes(value.canonical_path.as_os_str().as_encoded_bytes()),
        }
    }
}

fn display_os_bytes(bytes: &[u8]) -> String {
    let mut rendered = String::new();
    let mut remaining = bytes;
    while !remaining.is_empty() {
        match std::str::from_utf8(remaining) {
            Ok(valid) => {
                rendered.push_str(valid);
                break;
            }
            Err(error) => {
                let valid_up_to = error.valid_up_to();
                if valid_up_to > 0 {
                    rendered.push_str(std::str::from_utf8(&remaining[..valid_up_to]).unwrap());
                }
                let invalid_length = error.error_len().unwrap_or(remaining.len() - valid_up_to);
                for byte in &remaining[valid_up_to..valid_up_to + invalid_length] {
                    rendered.push_str(&format!("%{byte:02X}"));
                }
                remaining = &remaining[valid_up_to + invalid_length..];
            }
        }
    }
    rendered
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SafeSettingDto {
    pub key: String,
    pub present: bool,
    pub redacted: bool,
    pub value: Option<String>,
}

impl From<SafeSetting> for SafeSettingDto {
    fn from(value: SafeSetting) -> Self {
        Self {
            key: value.key,
            present: value.present,
            redacted: value.redacted,
            value: value.value,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum CorrelationStateDto {
    Uncorrelated,
    Verified {
        component_id: String,
        method: String,
        artifact_record_ref: String,
        install_evidence_ref: Option<String>,
    },
    Drift {
        component_id: String,
        artifact_record_ref: String,
        expected_sha256: String,
        current_sha256: String,
    },
    Ambiguous {
        candidate_component_ids: Vec<String>,
        safe_reason: String,
    },
}

impl From<CorrelationState> for CorrelationStateDto {
    fn from(value: CorrelationState) -> Self {
        match value {
            CorrelationState::Uncorrelated => Self::Uncorrelated,
            CorrelationState::Verified {
                component_id,
                method,
                artifact_record_ref,
                install_evidence_ref,
            } => Self::Verified {
                component_id,
                method,
                artifact_record_ref,
                install_evidence_ref,
            },
            CorrelationState::Drift {
                component_id,
                artifact_record_ref,
                expected_sha256,
                current_sha256,
            } => Self::Drift {
                component_id,
                artifact_record_ref,
                expected_sha256,
                current_sha256,
            },
            CorrelationState::Ambiguous {
                candidate_component_ids,
                safe_reason,
            } => Self::Ambiguous {
                candidate_component_ids,
                safe_reason,
            },
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum CorrelationAvailabilityDto {
    Available,
    Unavailable { safe_reason: String },
}

impl From<CorrelationAvailability> for CorrelationAvailabilityDto {
    fn from(value: CorrelationAvailability) -> Self {
        match value {
            CorrelationAvailability::Available => Self::Available,
            CorrelationAvailability::Unavailable { safe_reason } => {
                Self::Unavailable { safe_reason }
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LocalInstanceDetailDto {
    pub instance_id: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub tool_id: ToolIdDto,
    pub surface_id: String,
    pub scope: ScopeDto,
    pub project_id: Option<String>,
    pub safe_locator: String,
    pub kind: SurfaceKindDto,
    pub display_name: String,
    pub name: Option<String>,
    pub description: Option<String>,
    pub description_source: Option<String>,
    pub settings: Vec<SafeSettingDto>,
    pub size: Option<u64>,
    pub modified_unix_millis: Option<u64>,
    pub parse_state: ParseStateDto,
    pub issue_codes: Vec<String>,
    pub correlation: CorrelationStateDto,
}

impl LocalInstanceDetailDto {
    pub fn from_domain(value: LocalInstanceDetail, correlation: CorrelationState) -> Self {
        Self {
            instance_id: value.instance_id,
            adapter_id: value.adapter_id,
            adapter_version: value.adapter_version,
            tool_id: value.tool_id.into(),
            surface_id: value.surface_id,
            scope: value.scope.into(),
            project_id: value.project_id,
            safe_locator: value.safe_locator,
            kind: value.kind.into(),
            display_name: value.display_name,
            name: value.name,
            description: value.description,
            description_source: value.description_source,
            settings: value.settings.into_iter().map(Into::into).collect(),
            size: value.size,
            modified_unix_millis: value.modified_unix_millis,
            parse_state: value.parse_state.into(),
            issue_codes: value.issue_codes,
            correlation: correlation.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstanceCorrelationDto {
    pub instance_id: String,
    pub correlation: CorrelationStateDto,
}

impl From<InstanceCorrelation> for InstanceCorrelationDto {
    fn from(value: InstanceCorrelation) -> Self {
        Self {
            instance_id: value.instance_id,
            correlation: value.correlation.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CorrelationProjectionDto {
    pub projection_id: String,
    pub projector_version: String,
    pub local_snapshot_id: String,
    pub sot_snapshot_id: Option<String>,
    pub artifact_projection_id: Option<String>,
    pub install_evidence_id: Option<String>,
    pub availability: CorrelationAvailabilityDto,
    pub correlations: Vec<InstanceCorrelationDto>,
}

impl From<CorrelationProjection> for CorrelationProjectionDto {
    fn from(value: CorrelationProjection) -> Self {
        Self {
            projection_id: value.projection_id,
            projector_version: value.projector_version,
            local_snapshot_id: value.local_snapshot_id,
            sot_snapshot_id: value.sot_snapshot_id,
            artifact_projection_id: value.artifact_projection_id,
            install_evidence_id: value.install_evidence_id,
            availability: value.availability.into(),
            correlations: value.correlations.into_iter().map(Into::into).collect(),
        }
    }
}
