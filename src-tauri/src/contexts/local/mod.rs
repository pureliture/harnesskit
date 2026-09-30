mod action;
mod adapter;
mod adapters;
mod catalog;
mod context;
pub mod domain;
mod engine;
pub mod parser;
pub mod path_action;
mod probe;
mod project_ignore;
pub mod query;
mod removal;
pub mod roots;
pub mod scanner;
mod snapshot;
mod source_inspection;
mod source_markdown;
mod source_session;
mod source_span;
pub(crate) mod store;
pub mod verified_path;
mod yaml_span;

pub use action::TauriNativePathActionPort;
pub use adapter::*;
pub use catalog::{
    canonical_descriptor_sha256, fixture_manifest_sha256, AdapterCatalog, CatalogAdapter,
    CatalogError,
};
pub use context::{
    IgnoreSaveRescanOutcome, LocalContext, LocalContextError, LocalRemovalApplyOutcome,
    LocalRemovalEffectKind, LocalRemovalMemberSummary, LocalRemovalPlanPreview,
    LocalRemovalReconciliation, LocalRemovalReconciliationState, LocalRemovalRescanState,
    LocalRemovalSourceGroupOutcome, LocalRemovalSourceGroupState, LocalScanDiagnostics,
    LocalScanEventEnvelope, LocalScanEventError, LocalScanEventPayload, LocalScanEventPort,
    LocalScanExecutor, LocalScanExecutorResult, LocalScanProgress, LocalScanProgressPort,
    LocalScanPublication, StartLocalScanOutcome,
};
pub use domain::{
    CoverageStatus, ProjectIgnoreSummary, ProjectLocationRecord, QualifiedTool, SurfacePresence,
};
pub use engine::{DiscoveryScanExecutor, ScanEnvironment};
pub use path_action::{
    LocalPathAction, LocalPathActionError, LocalPathActionOutcome, NativePathActionError,
    NativePathActionPort, VerifiedNativePath,
};
pub use probe::*;
pub use project_ignore::{
    load_project_ignore, save_project_ignore, ProjectIgnoreError, ProjectIgnorePolicy,
    ProjectIgnoreSave, ProjectIgnoreSource, ProjectScanScope,
};
pub use query::*;
pub use snapshot::{LocalSnapshot, LocalSnapshotStatus};
#[cfg(test)]
pub(crate) use source_inspection::SourceReadPort;
pub(crate) use source_inspection::{
    OpenSourcePreviewJob, OpenSourceRequest, ReadSourceChunkRequest, ReadSourcePreviewJob,
    SourceFormat, SourceInspectionError, SourceIssue, SourcePreviewChunk, SourcePreviewClose,
    SourcePreviewContent, SourcePreviewHeader,
};
pub(crate) use source_markdown::{MarkdownBlockFragment, MarkdownBlockKind, MarkdownSemanticNode};
pub use store::{LocalAttemptReport, LocalAttemptState, LocalSessionState, LocalSnapshotHeader};
