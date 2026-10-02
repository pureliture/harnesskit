use std::fmt;
use std::io;
use std::io::{Read, Seek, SeekFrom};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use crate::contexts::local::adapter::ParserId;
use crate::contexts::local::source_session::{
    OpenSessionIntent, OpenWorkerPermit, ReadWorkerPermit, ReadySessionKey, SessionCloseOutcome,
    SourceSessionError, SourceSessionStore,
};
use crate::contexts::local::source_span::{
    resolve_hermes_yaml_pointer_span, resolve_json_pointer_span, SourceSpan,
};
use crate::contexts::local::store::LocalAuthority;
use crate::contexts::local::verified_path::{
    VerificationPolicy, VerifiedLocalFile, VerifiedLocalPathResolver, VerifiedPathError,
    VerifiedReadError,
};
use crate::operation_coordinator::{BeginLocalReadError, OperationCoordinator};
use crate::support::content_digest::ContentDigest;

use super::source_markdown::{
    MarkdownBlockFragment, MarkdownBlockKind, MarkdownCheckpoint, MarkdownProjectionService,
    MarkdownScanState, MARKDOWN_BOUNDARY_LOOKAHEAD_BYTES, MARKDOWN_LOOKBEHIND_BYTES,
};
#[cfg(test)]
use super::source_markdown::{MAX_MARKDOWN_FRAGMENTS, MAX_MARKDOWN_NESTING};

const READ_BUFFER_BYTES: usize = 64 * 1024;
const SOURCE_CHUNK_BYTES: u64 = 64 * 1024;
const MAX_SOURCE_CHUNK_BYTES: u64 = 68 * 1024;
const MAX_DIGEST_GROUPS: u64 = 4_096;

pub(crate) trait SourceReadPort: Send + Sync + 'static {
    fn read_at(
        &self,
        file: &VerifiedLocalFile,
        buffer: &mut [u8],
        offset: u64,
    ) -> Result<usize, VerifiedReadError>;
}

struct VerifiedFdSourceReadPort;

impl SourceReadPort for VerifiedFdSourceReadPort {
    fn read_at(
        &self,
        file: &VerifiedLocalFile,
        buffer: &mut [u8],
        offset: u64,
    ) -> Result<usize, VerifiedReadError> {
        file.read_at(buffer, offset)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct OpenSourceRequest {
    pub(crate) snapshot_id: String,
    pub(crate) instance_id: String,
    pub(crate) view_generation: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ReadSourceChunkRequest {
    pub(crate) preview_session_id: String,
    pub(crate) source_revision: String,
    pub(crate) chunk_index: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum SourceFormat {
    Markdown,
    Json,
    Yaml,
    Text,
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) enum SourcePreviewContent {
    Markdown {
        block_fragments: Vec<MarkdownBlockFragment>,
    },
    Text {
        before: String,
        selected: Option<String>,
        after: String,
    },
}

impl fmt::Debug for SourcePreviewContent {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SourceIssue {
    pub(crate) code: String,
    pub(crate) safe_message: String,
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct SourcePreviewHeader {
    pub(crate) preview_session_id: String,
    pub(crate) view_generation: u64,
    pub(crate) snapshot_id: String,
    pub(crate) instance_id: String,
    pub(crate) canonical_path: String,
    pub(crate) source_revision: String,
    pub(crate) changed_since_snapshot: bool,
    pub(crate) format: SourceFormat,
    pub(crate) total_bytes: u64,
    pub(crate) total_chunks: u64,
    pub(crate) chunk_bytes: u64,
    pub(crate) selected_chunk_index: Option<u64>,
    pub(crate) issue: Option<SourceIssue>,
}

impl fmt::Debug for SourcePreviewHeader {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SourcePreviewHeader")
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

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct SourcePreviewChunk {
    pub(crate) preview_session_id: String,
    pub(crate) source_revision: String,
    pub(crate) chunk_index: u64,
    pub(crate) is_last: bool,
    pub(crate) content: SourcePreviewContent,
}

pub(crate) struct CapturedLocalSource {
    bytes: Vec<u8>,
    source_revision: String,
}

impl CapturedLocalSource {
    #[cfg(test)]
    pub(crate) fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub(crate) fn into_bytes(self) -> Vec<u8> {
        self.bytes
    }

    pub(crate) fn source_revision(&self) -> &str {
        &self.source_revision
    }
}

impl fmt::Debug for SourcePreviewChunk {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SourcePreviewChunk")
            .field("preview_session_id", &self.preview_session_id)
            .field("source_revision", &self.source_revision)
            .field("chunk_index", &self.chunk_index)
            .field("is_last", &self.is_last)
            .field("content", &self.content)
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct SourcePreviewClose {
    pub(crate) closed: bool,
    pub(crate) cancelled: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SourceInspectionError {
    pub(crate) code: &'static str,
    pub(crate) active_chunk_index: Option<u64>,
}

impl SourceInspectionError {
    fn new(code: &'static str) -> Self {
        Self {
            code,
            active_chunk_index: None,
        }
    }
}

impl From<SourceSessionError> for SourceInspectionError {
    fn from(value: SourceSessionError) -> Self {
        Self {
            code: value.code,
            active_chunk_index: value.active_chunk_index,
        }
    }
}

impl From<BeginLocalReadError> for SourceInspectionError {
    fn from(value: BeginLocalReadError) -> Self {
        Self::new(value.code())
    }
}

#[derive(Clone)]
struct SourceDigestGroup {
    index: u64,
    start: u64,
    length: u64,
    digest: [u8; 32],
    markdown_checkpoint: Option<MarkdownCheckpoint>,
}

struct ReadySourceSession {
    key: ReadySessionKey,
    file: VerifiedLocalFile,
    format: SourceFormat,
    total_bytes: u64,
    total_chunks: u64,
    groups: Vec<SourceDigestGroup>,
    selected_span: Option<SourceSpan>,
}

impl fmt::Debug for ReadySourceSession {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ReadySourceSession")
            .field("key", &self.key)
            .field("file", &"<verified-fd>")
            .field("format", &self.format)
            .field("total_bytes", &self.total_bytes)
            .field("total_chunks", &self.total_chunks)
            .field("group_count", &self.groups.len())
            .finish()
    }
}

pub(crate) struct LocalSourceInspectionService {
    operations: Arc<OperationCoordinator>,
    authority: Arc<LocalAuthority>,
    sessions: Arc<SourceSessionStore<ReadySourceSession>>,
    reader: Arc<dyn SourceReadPort>,
    next_session: AtomicU64,
    #[cfg(test)]
    completion_observer: std::sync::Mutex<Option<Arc<dyn Fn() + Send + Sync>>>,
}

impl fmt::Debug for LocalSourceInspectionService {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LocalSourceInspectionService")
            .field("active_worker_count", &self.sessions.active_worker_count())
            .field("ready_session_count", &self.sessions.ready_session_count())
            .finish()
    }
}

pub(crate) struct OpenSourcePreviewJob {
    service: Arc<LocalSourceInspectionService>,
    request: OpenSourceRequest,
    permit: OpenWorkerPermit<ReadySourceSession>,
}

pub(crate) struct ReadSourcePreviewJob {
    service: Arc<LocalSourceInspectionService>,
    permit: ReadWorkerPermit<ReadySourceSession>,
}

impl LocalSourceInspectionService {
    pub(crate) fn new(
        operations: Arc<OperationCoordinator>,
        authority: Arc<LocalAuthority>,
    ) -> Self {
        let sessions = Arc::new(SourceSessionStore::default());
        sessions.start_idle_reaper();
        Self {
            operations,
            authority,
            sessions,
            reader: Arc::new(VerifiedFdSourceReadPort),
            next_session: AtomicU64::new(0),
            #[cfg(test)]
            completion_observer: std::sync::Mutex::new(None),
        }
    }

    #[cfg(test)]
    pub(crate) fn with_reader(
        operations: Arc<OperationCoordinator>,
        authority: Arc<LocalAuthority>,
        reader: Arc<dyn SourceReadPort>,
    ) -> Self {
        Self {
            operations,
            authority,
            sessions: Arc::new(SourceSessionStore::default()),
            reader,
            next_session: AtomicU64::new(0),
            completion_observer: std::sync::Mutex::new(None),
        }
    }

    pub(crate) fn prepare_open(
        self: &Arc<Self>,
        request: OpenSourceRequest,
    ) -> Result<OpenSourcePreviewJob, SourceInspectionError> {
        let permit = self.sessions.begin_open(OpenSessionIntent {
            view_generation: request.view_generation,
            snapshot_id: request.snapshot_id.clone(),
            instance_id: request.instance_id.clone(),
        })?;
        Ok(OpenSourcePreviewJob {
            service: Arc::clone(self),
            request,
            permit,
        })
    }

    pub(crate) fn prepare_read(
        self: &Arc<Self>,
        request: ReadSourceChunkRequest,
    ) -> Result<ReadSourcePreviewJob, SourceInspectionError> {
        let key = self
            .sessions
            .ready_key_for(&request.preview_session_id, &request.source_revision)?;
        let permit = self.sessions.begin_read(&key, request.chunk_index)?;
        Ok(ReadSourcePreviewJob {
            service: Arc::clone(self),
            permit,
        })
    }

    pub(crate) fn close(
        &self,
        view_generation: u64,
        preview_session_id: Option<&str>,
    ) -> SourcePreviewClose {
        let SessionCloseOutcome { closed, cancelled } =
            self.sessions.close(view_generation, preview_session_id);
        SourcePreviewClose { closed, cancelled }
    }

    pub(crate) fn capture_revision_bound_source(
        &self,
        snapshot_id: &str,
        instance_id: &str,
        expected_source_revision: &str,
        max_bytes: usize,
    ) -> Result<CapturedLocalSource, SourceInspectionError> {
        if expected_source_revision.is_empty() {
            return Err(SourceInspectionError::new("source_stale"));
        }
        let lease = self.operations.begin_local_read(|| {
            self.authority
                .instance_handle(snapshot_id, instance_id)
                .map_err(|code| BeginLocalReadError::from_lookup_code(&code))
        })?;
        let file = VerifiedLocalPathResolver
            .resolve(lease.value(), VerificationPolicy::CurrentSourceAtLocator)
            .map_err(source_stale)?;
        let captured = capture_verified_source(
            &file,
            self.reader.as_ref(),
            expected_source_revision,
            max_bytes,
        )?;
        drop(lease);
        Ok(captured)
    }

    pub(crate) fn capture_current_source(
        &self,
        snapshot_id: &str,
        instance_id: &str,
        max_bytes: usize,
    ) -> Result<CapturedLocalSource, SourceInspectionError> {
        let lease = self.operations.begin_local_read(|| {
            self.authority
                .instance_handle(snapshot_id, instance_id)
                .map_err(|code| BeginLocalReadError::from_lookup_code(&code))
        })?;
        let file = VerifiedLocalPathResolver
            .resolve(lease.value(), VerificationPolicy::CurrentSourceAtLocator)
            .map_err(source_stale)?;
        capture_verified_source(&file, self.reader.as_ref(), "", max_bytes)
    }

    #[cfg(test)]
    fn probe_group_count(&self, preview_session_id: &str) -> usize {
        self.sessions
            .ready_payload_for(preview_session_id)
            .map(|session| session.groups.len())
            .unwrap_or(0)
    }

    #[cfg(test)]
    fn probe_active_worker_count(&self) -> usize {
        self.sessions.active_worker_count()
    }

    #[cfg(test)]
    fn probe_ready_session_count(&self) -> usize {
        self.sessions.ready_session_count()
    }

    #[cfg(test)]
    fn set_completion_observer(&self, observer: Arc<dyn Fn() + Send + Sync>) {
        *self
            .completion_observer
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some(observer);
    }

    #[cfg(test)]
    fn observe_before_worker_completion(&self) {
        if let Some(observer) = self
            .completion_observer
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .as_ref()
        {
            observer();
        }
    }

    #[cfg(not(test))]
    fn observe_before_worker_completion(&self) {}
}

impl OpenSourcePreviewJob {
    pub(crate) fn run(self) -> Result<SourcePreviewHeader, SourceInspectionError> {
        let Self {
            service,
            request,
            permit,
        } = self;
        if permit.is_cancelled() {
            return Err(SourceInspectionError::new("preview_cancelled"));
        }
        let lease = service.operations.begin_local_read(|| {
            service
                .authority
                .instance_handle(&request.snapshot_id, &request.instance_id)
                .map_err(|code| BeginLocalReadError::from_lookup_code(&code))
        })?;
        let handle = lease.value();
        let file = VerifiedLocalPathResolver
            .resolve(handle, VerificationPolicy::CurrentSourceAtLocator)
            .map_err(source_stale)?;
        let canonical_path = file
            .canonical_path()
            .to_str()
            .ok_or_else(|| SourceInspectionError::new("source_path_not_utf8"))?
            .to_string();
        let format = source_format(handle.source_parser_id);
        let scan = scan_source(&file, service.reader.as_ref(), &permit, format)?;
        let locator = resolve_source_locator(
            &file,
            service.reader.as_ref(),
            &permit,
            format,
            handle.config_entry_locator.as_deref(),
        );
        let (selected_span, issue) = match locator {
            Ok(resolved) => resolved,
            Err(error) => {
                file.verify_unchanged().map_err(source_stale)?;
                return Err(error);
            }
        };
        file.verify_unchanged().map_err(source_stale)?;
        let session_number = service.next_session.fetch_add(1, Ordering::Relaxed) + 1;
        let preview_session_id = format!("local-source-preview-{session_number}");
        let source_revision = source_revision(file.current_identity(), &scan.groups);
        let changed_since_snapshot = file.metadata_changed_since_snapshot()
            || handle.scan_content_sha256.is_some_and(|scan_digest| {
                !ContentDigest::constant_time_eq(&scan_digest, &scan.full_digest)
            });
        let total_chunks = total_chunks(file.current_identity().size);
        let selected_chunk_index = selected_span
            .map(|span| {
                chunk_index_for_span_start(
                    &file,
                    service.reader.as_ref(),
                    scan.total_bytes,
                    total_chunks,
                    span,
                )
            })
            .transpose()?;
        file.verify_unchanged().map_err(source_stale)?;
        let key = ReadySessionKey {
            preview_session_id: preview_session_id.clone(),
            view_generation: request.view_generation,
            snapshot_id: request.snapshot_id.clone(),
            instance_id: request.instance_id.clone(),
            source_revision: source_revision.clone(),
        };
        let ready = Arc::new(ReadySourceSession {
            key: key.clone(),
            file,
            format,
            total_bytes: scan.total_bytes,
            total_chunks,
            groups: scan.groups,
            selected_span,
        });
        let header = SourcePreviewHeader {
            preview_session_id,
            view_generation: request.view_generation,
            snapshot_id: request.snapshot_id,
            instance_id: request.instance_id,
            canonical_path,
            source_revision,
            changed_since_snapshot,
            format,
            total_bytes: scan.total_bytes,
            total_chunks,
            chunk_bytes: SOURCE_CHUNK_BYTES,
            selected_chunk_index,
            issue,
        };
        drop(lease);
        service.observe_before_worker_completion();
        permit.complete(key, ready)?;
        Ok(header)
    }
}

impl ReadSourcePreviewJob {
    pub(crate) fn run(self) -> Result<SourcePreviewChunk, SourceInspectionError> {
        let Self { service, permit } = self;
        let session = Arc::clone(&permit.payload);
        if permit.chunk_index >= session.total_chunks {
            return Err(SourceInspectionError::new("chunk_out_of_range"));
        }
        if permit.is_cancelled() {
            return Err(SourceInspectionError::new("preview_cancelled"));
        }
        let lease = service.operations.begin_local_read(|| {
            service
                .authority
                .instance_handle(&permit.key.snapshot_id, &permit.key.instance_id)
                .map_err(|code| BeginLocalReadError::from_lookup_code(&code))
        })?;
        let verified_chunk = match read_verified_chunk(&session, service.reader.as_ref(), &permit) {
            Ok(chunk) => chunk,
            Err(error) => {
                if error.code == "source_stale" {
                    service.sessions.close(
                        permit.key.view_generation,
                        Some(&permit.key.preview_session_id),
                    );
                }
                return Err(error);
            }
        };
        let is_last = permit.chunk_index + 1 == session.total_chunks;
        let content = match session.format {
            SourceFormat::Markdown => SourcePreviewContent::Markdown {
                block_fragments: MarkdownProjectionService::project(
                    verified_chunk.start,
                    verified_chunk.text,
                    verified_chunk.markdown_checkpoint.unwrap_or_default(),
                    verified_chunk.markdown_lookbehind,
                    is_last,
                ),
            },
            SourceFormat::Json | SourceFormat::Yaml | SourceFormat::Text => {
                partition_text_chunk(verified_chunk, session.selected_span)?
            }
        };
        let chunk = SourcePreviewChunk {
            preview_session_id: permit.key.preview_session_id.clone(),
            source_revision: permit.key.source_revision.clone(),
            chunk_index: permit.chunk_index,
            is_last,
            content,
        };
        drop(lease);
        service.observe_before_worker_completion();
        permit.finish_with(chunk).map_err(Into::into)
    }
}

struct OpenScan {
    total_bytes: u64,
    full_digest: [u8; 32],
    groups: Vec<SourceDigestGroup>,
}

struct VerifiedChunk {
    start: u64,
    text: String,
    markdown_checkpoint: Option<MarkdownCheckpoint>,
    markdown_lookbehind: Vec<u8>,
}

struct SourceSpanReader<'a> {
    file: &'a VerifiedLocalFile,
    reader: &'a dyn SourceReadPort,
    permit: &'a OpenWorkerPermit<ReadySourceSession>,
    offset: u64,
    total_bytes: u64,
}

impl Read for SourceSpanReader<'_> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if self.permit.is_cancelled() {
            return Err(io::Error::other("source preview cancelled"));
        }
        let remaining = self.total_bytes.saturating_sub(self.offset);
        if remaining == 0 {
            return Ok(0);
        }
        let wanted = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|_| io::Error::other("source read bound failed"))?;
        let read = self
            .reader
            .read_at(self.file, &mut buffer[..wanted], self.offset)
            .map_err(|_| io::Error::other("source read failed"))?;
        if read == 0 {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "source changed during locator resolution",
            ));
        }
        self.offset = self.offset.saturating_add(read as u64);
        self.permit.touch();
        Ok(read)
    }
}

impl Seek for SourceSpanReader<'_> {
    fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
        let requested = match position {
            SeekFrom::Start(offset) => i128::from(offset),
            SeekFrom::End(offset) => i128::from(self.total_bytes) + i128::from(offset),
            SeekFrom::Current(offset) => i128::from(self.offset) + i128::from(offset),
        };
        if requested < 0 || requested > i128::from(self.total_bytes) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "source seek exceeded verified file bounds",
            ));
        }
        self.offset =
            u64::try_from(requested).map_err(|_| io::Error::other("source seek bound failed"))?;
        Ok(self.offset)
    }
}

fn resolve_source_locator(
    file: &VerifiedLocalFile,
    reader: &dyn SourceReadPort,
    permit: &OpenWorkerPermit<ReadySourceSession>,
    format: SourceFormat,
    locator: Option<&str>,
) -> Result<(Option<SourceSpan>, Option<SourceIssue>), SourceInspectionError> {
    let Some(locator) = locator else {
        return Ok((None, None));
    };
    if !matches!(format, SourceFormat::Json | SourceFormat::Yaml) {
        return Ok((None, None));
    }
    let stream = SourceSpanReader {
        file,
        reader,
        permit,
        offset: 0,
        total_bytes: file.current_identity().size,
    };
    let resolved = match format {
        SourceFormat::Json => resolve_json_pointer_span(stream, locator),
        SourceFormat::Yaml => resolve_hermes_yaml_pointer_span(stream, locator),
        SourceFormat::Markdown | SourceFormat::Text => unreachable!(),
    };
    match resolved {
        Ok(Some(span)) => Ok((Some(span), None)),
        Ok(None) => Ok((None, Some(entry_locator_stale_issue()))),
        Err(_) if permit.is_cancelled() => Err(SourceInspectionError::new("preview_cancelled")),
        Err(error) if error.kind() == io::ErrorKind::InvalidData => {
            Ok((None, Some(entry_locator_stale_issue())))
        }
        Err(_) => Err(SourceInspectionError::new("source_read_failed")),
    }
}

fn entry_locator_stale_issue() -> SourceIssue {
    SourceIssue {
        code: "entry_locator_stale".to_string(),
        safe_message: "파일이 변경되어 이전 항목 위치를 찾을 수 없습니다.".to_string(),
    }
}

fn partition_text_chunk(
    mut chunk: VerifiedChunk,
    selected_span: Option<SourceSpan>,
) -> Result<SourcePreviewContent, SourceInspectionError> {
    let chunk_end = chunk.start.saturating_add(chunk.text.len() as u64);
    let Some(span) = selected_span else {
        return Ok(SourcePreviewContent::Text {
            before: chunk.text,
            selected: None,
            after: String::new(),
        });
    };
    let selected_start = span.start.max(chunk.start);
    let selected_end = span.start.saturating_add(span.length).min(chunk_end);
    if selected_start >= selected_end {
        return Ok(SourcePreviewContent::Text {
            before: chunk.text,
            selected: None,
            after: String::new(),
        });
    }
    let local_start = usize::try_from(selected_start - chunk.start)
        .map_err(|_| SourceInspectionError::new("source_stale"))?;
    let local_end = usize::try_from(selected_end - chunk.start)
        .map_err(|_| SourceInspectionError::new("source_stale"))?;
    if !chunk.text.is_char_boundary(local_start) || !chunk.text.is_char_boundary(local_end) {
        return Err(SourceInspectionError::new("source_stale"));
    }
    let after = chunk.text.split_off(local_end);
    let selected = chunk.text.split_off(local_start);
    Ok(SourcePreviewContent::Text {
        before: chunk.text,
        selected: Some(selected),
        after,
    })
}

fn scan_source(
    file: &VerifiedLocalFile,
    reader: &dyn SourceReadPort,
    permit: &OpenWorkerPermit<ReadySourceSession>,
    format: SourceFormat,
) -> Result<OpenScan, SourceInspectionError> {
    file.verify_unchanged().map_err(source_stale)?;
    let total_bytes = file.current_identity().size;
    let group_bytes = digest_group_bytes(total_bytes);
    let group_count = if total_bytes == 0 {
        0
    } else {
        ceil_div(total_bytes, group_bytes)
    };
    if group_count > MAX_DIGEST_GROUPS {
        return Err(SourceInspectionError::new("source_group_bound_exceeded"));
    }
    let mut groups = Vec::with_capacity(usize::try_from(group_count).unwrap_or(0));
    let mut full_digest = ContentDigest::incremental();
    let mut validator = Utf8Validator::default();
    let mut markdown_state =
        matches!(format, SourceFormat::Markdown).then(MarkdownScanState::default);
    let mut buffer = vec![0_u8; READ_BUFFER_BYTES];

    for index in 0..group_count {
        let start = index.saturating_mul(group_bytes);
        let length = group_bytes.min(total_bytes - start);
        let markdown_checkpoint = markdown_state.map(|state| state.checkpoint);
        let mut group_digest = ContentDigest::incremental();
        let mut offset = 0_u64;
        while offset < length {
            if permit.is_cancelled() {
                return Err(SourceInspectionError::new("preview_cancelled"));
            }
            let wanted = usize::try_from((length - offset).min(READ_BUFFER_BYTES as u64))
                .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
            let read = reader
                .read_at(file, &mut buffer[..wanted], start + offset)
                .map_err(source_read_failed)?;
            if read == 0 {
                return Err(SourceInspectionError::new("source_stale"));
            }
            permit.touch();
            validator.push(&buffer[..read])?;
            if let Some(state) = markdown_state.as_mut() {
                state.feed(&buffer[..read]);
            }
            group_digest.update(&buffer[..read]);
            full_digest.update(&buffer[..read]);
            offset = offset.saturating_add(read as u64);
        }
        groups.push(SourceDigestGroup {
            index,
            start,
            length,
            digest: group_digest.finalize(),
            markdown_checkpoint,
        });
    }
    validator.finish()?;
    file.verify_unchanged().map_err(source_stale)?;
    Ok(OpenScan {
        total_bytes,
        full_digest: full_digest.finalize(),
        groups,
    })
}

fn capture_verified_source(
    file: &VerifiedLocalFile,
    reader: &dyn SourceReadPort,
    expected_source_revision: &str,
    max_bytes: usize,
) -> Result<CapturedLocalSource, SourceInspectionError> {
    file.verify_single_link().map_err(source_stale)?;
    let total_bytes = file.current_identity().size;
    if total_bytes > max_bytes as u64 {
        return Err(SourceInspectionError::new("source_too_large_for_ai"));
    }
    if !expected_source_revision.is_empty()
        && (expected_source_revision.len() != 64
            || !expected_source_revision
                .as_bytes()
                .iter()
                .all(u8::is_ascii_hexdigit))
    {
        return Err(SourceInspectionError::new("source_stale"));
    }
    let capacity = usize::try_from(total_bytes)
        .map_err(|_| SourceInspectionError::new("source_too_large_for_ai"))?;
    let group_bytes = digest_group_bytes(total_bytes);
    let group_count = if total_bytes == 0 {
        0
    } else {
        ceil_div(total_bytes, group_bytes)
    };
    if group_count > MAX_DIGEST_GROUPS {
        return Err(SourceInspectionError::new("source_group_bound_exceeded"));
    }
    let mut bytes = Vec::with_capacity(capacity);
    let mut groups = Vec::with_capacity(usize::try_from(group_count).unwrap_or(0));
    let mut validator = Utf8Validator::default();
    let mut buffer = vec![0_u8; READ_BUFFER_BYTES];
    for index in 0..group_count {
        let start = index.saturating_mul(group_bytes);
        let length = group_bytes.min(total_bytes - start);
        let mut group_digest = ContentDigest::incremental();
        let mut offset = 0_u64;
        while offset < length {
            let wanted = usize::try_from((length - offset).min(READ_BUFFER_BYTES as u64))
                .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
            let read = reader
                .read_at(file, &mut buffer[..wanted], start + offset)
                .map_err(source_read_failed)?;
            if read == 0 {
                return Err(SourceInspectionError::new("source_stale"));
            }
            validator.push(&buffer[..read])?;
            group_digest.update(&buffer[..read]);
            bytes.extend_from_slice(&buffer[..read]);
            offset = offset.saturating_add(read as u64);
        }
        groups.push(SourceDigestGroup {
            index,
            start,
            length,
            digest: group_digest.finalize(),
            markdown_checkpoint: None,
        });
    }
    validator.finish()?;
    file.verify_unchanged().map_err(source_stale)?;
    if bytes.len() != capacity {
        return Err(SourceInspectionError::new("source_stale"));
    }
    let source_revision = source_revision(file.current_identity(), &groups);
    if !expected_source_revision.is_empty() && source_revision != expected_source_revision {
        return Err(SourceInspectionError::new("source_stale"));
    }
    Ok(CapturedLocalSource {
        bytes,
        source_revision,
    })
}

fn read_verified_chunk(
    session: &ReadySourceSession,
    reader: &dyn SourceReadPort,
    permit: &ReadWorkerPermit<ReadySourceSession>,
) -> Result<VerifiedChunk, SourceInspectionError> {
    session.file.verify_unchanged().map_err(source_stale)?;
    let nominal_start = permit.chunk_index.saturating_mul(SOURCE_CHUNK_BYTES);
    let nominal_end = (permit.chunk_index + 1)
        .saturating_mul(SOURCE_CHUNK_BYTES)
        .min(session.total_bytes);
    let start = source_chunk_boundary(
        &session.file,
        reader,
        session.total_bytes,
        nominal_start,
        session.format,
    )?;
    let end = source_chunk_boundary(
        &session.file,
        reader,
        session.total_bytes,
        nominal_end,
        session.format,
    )?;
    if end < start || end - start > MAX_SOURCE_CHUNK_BYTES {
        return Err(SourceInspectionError::new("source_chunk_bound_exceeded"));
    }
    let lookbehind_start = if matches!(session.format, SourceFormat::Markdown) {
        start.saturating_sub(MARKDOWN_LOOKBEHIND_BYTES)
    } else {
        start
    };
    let capacity = usize::try_from(end - start)
        .map_err(|_| SourceInspectionError::new("source_chunk_bound_exceeded"))?;
    let lookbehind_capacity = usize::try_from(start - lookbehind_start)
        .map_err(|_| SourceInspectionError::new("source_chunk_bound_exceeded"))?;
    let mut captured = Vec::with_capacity(capacity);
    let mut markdown_lookbehind = Vec::with_capacity(lookbehind_capacity);
    let mut buffer = vec![0_u8; READ_BUFFER_BYTES];
    let mut markdown_state = None;
    let mut markdown_checkpoint = None;

    for group in session
        .groups
        .iter()
        .filter(|group| group.start < end && group.start + group.length > lookbehind_start)
    {
        if markdown_state.is_none() {
            markdown_state = group
                .markdown_checkpoint
                .map(|checkpoint| MarkdownScanState { checkpoint });
            if start == group.start {
                markdown_checkpoint = group.markdown_checkpoint;
            }
        }
        let mut digest = ContentDigest::incremental();
        let mut group_offset = 0_u64;
        while group_offset < group.length {
            if permit.is_cancelled() {
                return Err(SourceInspectionError::new("preview_cancelled"));
            }
            let wanted =
                usize::try_from((group.length - group_offset).min(READ_BUFFER_BYTES as u64))
                    .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
            let absolute_start = group.start + group_offset;
            let read = reader
                .read_at(&session.file, &mut buffer[..wanted], absolute_start)
                .map_err(source_read_failed)?;
            if read == 0 {
                return Err(SourceInspectionError::new("source_stale"));
            }
            permit.touch();
            digest.update(&buffer[..read]);
            let absolute_end = absolute_start + read as u64;
            if markdown_checkpoint.is_none() {
                if let Some(state) = markdown_state.as_mut() {
                    if absolute_start < start {
                        let checkpoint_end = absolute_end.min(start);
                        let checkpoint_length = usize::try_from(checkpoint_end - absolute_start)
                            .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
                        state.feed(&buffer[..checkpoint_length]);
                    }
                    if absolute_start <= start && start <= absolute_end {
                        markdown_checkpoint = Some(state.checkpoint);
                    }
                }
            }
            let lookbehind_overlap_start = absolute_start.max(lookbehind_start);
            let lookbehind_overlap_end = absolute_end.min(start);
            if lookbehind_overlap_start < lookbehind_overlap_end {
                let local_start = usize::try_from(lookbehind_overlap_start - absolute_start)
                    .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
                let local_end = usize::try_from(lookbehind_overlap_end - absolute_start)
                    .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
                markdown_lookbehind.extend_from_slice(&buffer[local_start..local_end]);
            }
            let overlap_start = absolute_start.max(start);
            let overlap_end = absolute_end.min(end);
            if overlap_start < overlap_end {
                let local_start = usize::try_from(overlap_start - absolute_start)
                    .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
                let local_end = usize::try_from(overlap_end - absolute_start)
                    .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
                captured.extend_from_slice(&buffer[local_start..local_end]);
            }
            group_offset = group_offset.saturating_add(read as u64);
        }
        if !ContentDigest::constant_time_eq(&digest.finalize(), &group.digest) {
            return Err(SourceInspectionError::new("source_stale"));
        }
    }
    if captured.len() != capacity || markdown_lookbehind.len() != lookbehind_capacity {
        return Err(SourceInspectionError::new("source_stale"));
    }
    session.file.verify_unchanged().map_err(source_stale)?;
    let text = String::from_utf8(captured)
        .map_err(|_| SourceInspectionError::new("source_invalid_utf8"))?;
    Ok(VerifiedChunk {
        start,
        text,
        markdown_checkpoint: if matches!(session.format, SourceFormat::Markdown) {
            Some(markdown_checkpoint.unwrap_or_default())
        } else {
            None
        },
        markdown_lookbehind,
    })
}

fn source_chunk_boundary(
    file: &VerifiedLocalFile,
    reader: &dyn SourceReadPort,
    total_bytes: u64,
    nominal: u64,
    format: SourceFormat,
) -> Result<u64, SourceInspectionError> {
    let utf8_boundary = utf8_boundary_at_or_before(file, reader, total_bytes, nominal)?;
    if !matches!(format, SourceFormat::Markdown)
        || utf8_boundary != nominal
        || nominal == 0
        || nominal >= total_bytes
    {
        return Ok(utf8_boundary);
    }

    let scan_end = nominal
        .saturating_add(MARKDOWN_BOUNDARY_LOOKAHEAD_BYTES as u64)
        .min(total_bytes);
    let mut buffer = [0_u8; MARKDOWN_BOUNDARY_LOOKAHEAD_BYTES];
    let mut offset = 0_usize;
    let wanted = usize::try_from(scan_end - nominal)
        .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
    while offset < wanted {
        let read = reader
            .read_at(file, &mut buffer[offset..wanted], nominal + offset as u64)
            .map_err(source_read_failed)?;
        if read == 0 {
            return Err(SourceInspectionError::new("source_stale"));
        }
        if let Some(relative) = buffer[offset..offset + read]
            .iter()
            .position(|byte| *byte == b'\n')
        {
            let candidate = nominal + (offset + relative + 1) as u64;
            return Ok(if candidate < total_bytes {
                candidate
            } else {
                nominal
            });
        }
        offset += read;
    }
    Ok(nominal)
}

fn utf8_boundary_at_or_before(
    file: &VerifiedLocalFile,
    reader: &dyn SourceReadPort,
    total_bytes: u64,
    nominal: u64,
) -> Result<u64, SourceInspectionError> {
    if nominal == 0 || nominal >= total_bytes {
        return Ok(nominal.min(total_bytes));
    }
    let start = nominal.saturating_sub(3);
    let wanted = usize::try_from(nominal - start + 1)
        .map_err(|_| SourceInspectionError::new("source_read_failed"))?;
    let mut bytes = [0_u8; 4];
    let mut offset = 0usize;
    while offset < wanted {
        let read = reader
            .read_at(file, &mut bytes[offset..wanted], start + offset as u64)
            .map_err(source_read_failed)?;
        if read == 0 {
            return Err(SourceInspectionError::new("source_stale"));
        }
        offset += read;
    }
    let nominal_index = wanted - 1;
    if !is_utf8_continuation(bytes[nominal_index]) {
        return Ok(nominal);
    }
    for index in (0..nominal_index).rev() {
        if !is_utf8_continuation(bytes[index]) {
            return Ok(start + index as u64);
        }
    }
    Err(SourceInspectionError::new("source_invalid_utf8"))
}

fn chunk_index_for_span_start(
    file: &VerifiedLocalFile,
    reader: &dyn SourceReadPort,
    total_bytes: u64,
    total_chunks: u64,
    span: SourceSpan,
) -> Result<u64, SourceInspectionError> {
    let index = (span.start / SOURCE_CHUNK_BYTES).min(total_chunks.saturating_sub(1));
    if index.saturating_add(1) >= total_chunks {
        return Ok(index);
    }
    let next_nominal = index.saturating_add(1).saturating_mul(SOURCE_CHUNK_BYTES);
    let next_start = utf8_boundary_at_or_before(file, reader, total_bytes, next_nominal)?;
    Ok(if span.start >= next_start {
        index + 1
    } else {
        index
    })
}

fn digest_group_bytes(total_bytes: u64) -> u64 {
    let minimum = SOURCE_CHUNK_BYTES.max(ceil_div(total_bytes, MAX_DIGEST_GROUPS));
    ceil_div(minimum, SOURCE_CHUNK_BYTES).saturating_mul(SOURCE_CHUNK_BYTES)
}

fn total_chunks(total_bytes: u64) -> u64 {
    ceil_div(total_bytes, SOURCE_CHUNK_BYTES).max(1)
}

fn ceil_div(value: u64, divisor: u64) -> u64 {
    value / divisor + u64::from(value % divisor != 0)
}

fn source_revision(
    identity: crate::contexts::local::domain::FileIdentity,
    groups: &[SourceDigestGroup],
) -> String {
    let mut digest = ContentDigest::incremental();
    digest.update(b"harnesskit.local-source-revision.v1\0");
    digest.update(&identity.device.to_le_bytes());
    digest.update(&identity.inode.to_le_bytes());
    digest.update(&identity.size.to_le_bytes());
    digest.update(&identity.mtime_ns.to_le_bytes());
    for group in groups {
        digest.update(&group.index.to_le_bytes());
        digest.update(&group.start.to_le_bytes());
        digest.update(&group.length.to_le_bytes());
        digest.update(&group.digest);
    }
    ContentDigest::hex(&digest.finalize())
}

fn source_format(parser_id: ParserId) -> SourceFormat {
    match parser_id {
        ParserId::SkillFrontmatterV1 | ParserId::MarkdownRuleV1 => SourceFormat::Markdown,
        ParserId::HookJsonV1
        | ParserId::ClaudeSettingsV1
        | ParserId::AntigravityManifestV1
        | ParserId::AntigravityCliJsonV1 => SourceFormat::Json,
        ParserId::HermesYamlV1 => SourceFormat::Yaml,
        ParserId::CodexTomlV1 | ParserId::ClaudeWorkflowJsMetadataV1 => SourceFormat::Text,
    }
}

fn source_stale(_error: VerifiedPathError) -> SourceInspectionError {
    SourceInspectionError::new("source_stale")
}

fn source_read_failed(_error: VerifiedReadError) -> SourceInspectionError {
    SourceInspectionError::new("source_read_failed")
}

fn is_utf8_continuation(byte: u8) -> bool {
    byte & 0b1100_0000 == 0b1000_0000
}

#[derive(Default)]
struct Utf8Validator {
    remaining: u8,
    next_min: u8,
    next_max: u8,
}

impl Utf8Validator {
    fn push(&mut self, bytes: &[u8]) -> Result<(), SourceInspectionError> {
        for &byte in bytes {
            if matches!(byte, 0x00..=0x08 | 0x0b..=0x0c | 0x0e..=0x1f | 0x7f) {
                return Err(SourceInspectionError::new("source_binary"));
            }
            if self.remaining == 0 {
                match byte {
                    0x00..=0x7f => {}
                    0xc2..=0xdf => self.start(1, 0x80, 0xbf),
                    0xe0 => self.start(2, 0xa0, 0xbf),
                    0xe1..=0xec | 0xee..=0xef => self.start(2, 0x80, 0xbf),
                    0xed => self.start(2, 0x80, 0x9f),
                    0xf0 => self.start(3, 0x90, 0xbf),
                    0xf1..=0xf3 => self.start(3, 0x80, 0xbf),
                    0xf4 => self.start(3, 0x80, 0x8f),
                    _ => return Err(SourceInspectionError::new("source_invalid_utf8")),
                }
                continue;
            }
            if byte < self.next_min || byte > self.next_max {
                return Err(SourceInspectionError::new("source_invalid_utf8"));
            }
            self.remaining -= 1;
            self.next_min = 0x80;
            self.next_max = 0xbf;
        }
        Ok(())
    }

    fn finish(self) -> Result<(), SourceInspectionError> {
        if self.remaining == 0 {
            Ok(())
        } else {
            Err(SourceInspectionError::new("source_invalid_utf8"))
        }
    }

    fn start(&mut self, remaining: u8, next_min: u8, next_max: u8) {
        self.remaining = remaining;
        self.next_min = next_min;
        self.next_max = next_max;
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::fs::{FileTimes, OpenOptions};
    use std::io::Write;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::sync::{Arc, Condvar, Mutex};

    use tempfile::tempdir;

    use super::*;
    use crate::contexts::local::scanner::LocalScanner;
    use crate::contexts::local::{
        AdapterCatalog, LocalScanExecutorResult, LocalScanPublication, LocalSnapshot,
        LocalSnapshotStatus, ToolId,
    };
    use crate::operation_coordinator::OperationCoordinator;

    fn write(path: &Path, bytes: &[u8]) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, bytes).unwrap();
    }

    fn service_for(home: &Path) -> (Arc<LocalSourceInspectionService>, String, String) {
        service_for_with_reader(home, Arc::new(VerifiedFdSourceReadPort))
    }

    fn service_for_with_reader(
        home: &Path,
        reader: Arc<dyn SourceReadPort>,
    ) -> (Arc<LocalSourceInspectionService>, String, String) {
        service_for_tool_with_reader(home, ToolId::Codex, "release", reader)
    }

    fn service_for_tool_with_reader(
        home: &Path,
        tool_id: ToolId,
        instance_name: &str,
        reader: Arc<dyn SourceReadPort>,
    ) -> (Arc<LocalSourceInspectionService>, String, String) {
        let catalog = AdapterCatalog::load_embedded().unwrap();
        let adapter = catalog.for_tool(tool_id).unwrap().clone();
        let output = LocalScanner::scan_with_handles(home, &[adapter]).unwrap();
        let instance_id = output
            .result
            .instances
            .iter()
            .find(|instance| instance.name.as_deref() == Some(instance_name))
            .or_else(|| output.result.instances.first())
            .unwrap()
            .instance_id
            .clone();
        let snapshot_id = "source-preview-snapshot".to_string();
        let operations = Arc::new(OperationCoordinator::default());
        let authority = Arc::new(LocalAuthority::default());
        operations
            .begin_local_scan("source-preview-scan", || {
                authority.begin_attempt("source-preview-scan")
            })
            .unwrap();
        operations
            .finish_local_scan("source-preview-scan", || {
                authority.finish_attempt(
                    "source-preview-scan",
                    LocalScanExecutorResult::Complete(LocalScanPublication {
                        snapshot: LocalSnapshot {
                            snapshot_id: snapshot_id.clone(),
                            attempt_id: "source-preview-scan".to_string(),
                            status: LocalSnapshotStatus::Complete,
                            scan_timestamp: "2026-07-13T00:00:00Z".to_string(),
                            app_version: "test".to_string(),
                            adapter_set_fingerprint: "a".repeat(64),
                            content_fingerprint: "b".repeat(64),
                            qualified_tools: output.result.qualified_tools,
                            instances: output.result.instances,
                            coverage: output.result.coverage,
                            skipped_paths: output.result.skipped_paths,
                            issues: output.result.issues,
                            project_ignore_summaries: output.result.project_ignore_summaries,
                        },
                        instance_handle_ids: vec![instance_id.clone()],
                        instance_handles: output.instance_handles,
                        project_locations: output.project_locations,
                    }),
                )
            })
            .unwrap();
        (
            Arc::new(LocalSourceInspectionService::with_reader(
                operations, authority, reader,
            )),
            snapshot_id,
            instance_id,
        )
    }

    #[derive(Default)]
    struct BlockingReader {
        entered: (Mutex<bool>, Condvar),
        released: (Mutex<bool>, Condvar),
        blocked_once: AtomicBool,
    }

    impl BlockingReader {
        fn arm(&self) {
            *self.entered.0.lock().unwrap() = false;
            *self.released.0.lock().unwrap() = false;
            self.blocked_once.store(false, Ordering::Release);
        }

        fn wait_until_entered(&self) {
            let (lock, wake) = &self.entered;
            let entered = lock.lock().unwrap();
            let (entered, timeout) = wake
                .wait_timeout_while(entered, std::time::Duration::from_secs(1), |entered| {
                    !*entered
                })
                .unwrap();
            assert!(
                *entered && !timeout.timed_out(),
                "source read did not start"
            );
        }

        fn release(&self) {
            let (lock, wake) = &self.released;
            *lock.lock().unwrap() = true;
            wake.notify_all();
        }
    }

    impl SourceReadPort for BlockingReader {
        fn read_at(
            &self,
            file: &VerifiedLocalFile,
            buffer: &mut [u8],
            offset: u64,
        ) -> Result<usize, VerifiedReadError> {
            if !self.blocked_once.swap(true, Ordering::AcqRel) {
                let (entered_lock, entered_wake) = &self.entered;
                *entered_lock.lock().unwrap() = true;
                entered_wake.notify_all();
                let (release_lock, release_wake) = &self.released;
                let released = release_lock.lock().unwrap();
                let _released = release_wake
                    .wait_while(released, |released| !*released)
                    .unwrap();
            }
            file.read_at(buffer, offset)
        }
    }

    struct OverlayReader {
        enabled: AtomicBool,
        changed_offset: u64,
    }

    impl OverlayReader {
        fn new(changed_offset: u64) -> Self {
            Self {
                enabled: AtomicBool::new(false),
                changed_offset,
            }
        }

        fn enable(&self) {
            self.enabled.store(true, Ordering::Release);
        }
    }

    impl SourceReadPort for OverlayReader {
        fn read_at(
            &self,
            file: &VerifiedLocalFile,
            buffer: &mut [u8],
            offset: u64,
        ) -> Result<usize, VerifiedReadError> {
            let read = file.read_at(buffer, offset)?;
            if self.enabled.load(Ordering::Acquire)
                && offset <= self.changed_offset
                && self.changed_offset < offset + read as u64
            {
                let index = usize::try_from(self.changed_offset - offset).unwrap();
                buffer[index] ^= 0x01;
            }
            Ok(read)
        }
    }

    #[derive(Default)]
    struct RecordingReader {
        max_requested: AtomicUsize,
        calls: Mutex<Vec<(u64, usize)>>,
    }

    impl RecordingReader {
        fn clear(&self) {
            self.max_requested.store(0, Ordering::Release);
            self.calls.lock().unwrap().clear();
        }

        fn calls(&self) -> Vec<(u64, usize)> {
            self.calls.lock().unwrap().clone()
        }
    }

    impl SourceReadPort for RecordingReader {
        fn read_at(
            &self,
            file: &VerifiedLocalFile,
            buffer: &mut [u8],
            offset: u64,
        ) -> Result<usize, VerifiedReadError> {
            self.max_requested.fetch_max(buffer.len(), Ordering::AcqRel);
            self.calls.lock().unwrap().push((offset, buffer.len()));
            file.read_at(buffer, offset)
        }
    }

    struct BoundaryMutatingReader {
        path: PathBuf,
        mutated: AtomicBool,
    }

    impl SourceReadPort for BoundaryMutatingReader {
        fn read_at(
            &self,
            file: &VerifiedLocalFile,
            buffer: &mut [u8],
            offset: u64,
        ) -> Result<usize, VerifiedReadError> {
            let read = file.read_at(buffer, offset)?;
            if offset > 0 && buffer.len() <= 4 && !self.mutated.swap(true, Ordering::AcqRel) {
                let mut bytes = fs::read(&self.path).unwrap();
                let last = bytes.last_mut().unwrap();
                *last = if *last == b'\n' { b' ' } else { b'\n' };
                fs::write(&self.path, bytes).unwrap();
            }
            Ok(read)
        }
    }

    struct LocatorSecondPassMutatingReader {
        path: PathBuf,
        zero_offset_reads: AtomicUsize,
    }

    struct LocatorTruncatingReader {
        path: PathBuf,
        zero_offset_reads: AtomicUsize,
    }

    impl SourceReadPort for LocatorTruncatingReader {
        fn read_at(
            &self,
            file: &VerifiedLocalFile,
            buffer: &mut [u8],
            offset: u64,
        ) -> Result<usize, VerifiedReadError> {
            if offset == 0 && self.zero_offset_reads.fetch_add(1, Ordering::AcqRel) == 1 {
                fs::OpenOptions::new()
                    .write(true)
                    .truncate(true)
                    .open(&self.path)
                    .unwrap();
            }
            file.read_at(buffer, offset)
        }
    }

    impl SourceReadPort for LocatorSecondPassMutatingReader {
        fn read_at(
            &self,
            file: &VerifiedLocalFile,
            buffer: &mut [u8],
            offset: u64,
        ) -> Result<usize, VerifiedReadError> {
            if offset == 0 && self.zero_offset_reads.fetch_add(1, Ordering::AcqRel) == 2 {
                let mut bytes = fs::read(&self.path).unwrap();
                let marker = bytes.iter_mut().find(|byte| **byte == b'r').unwrap();
                *marker = b'R';
                fs::write(&self.path, bytes).unwrap();
            }
            file.read_at(buffer, offset)
        }
    }

    fn chunk_text(chunk: SourcePreviewChunk) -> String {
        match chunk.content {
            SourcePreviewContent::Markdown { block_fragments } => block_fragments
                .into_iter()
                .map(|fragment| fragment.text)
                .collect(),
            SourcePreviewContent::Text {
                before,
                selected,
                after,
            } => format!("{before}{}{after}", selected.unwrap_or_default()),
        }
    }

    #[test]
    fn open_and_first_to_last_reads_preserve_the_full_utf8_source_with_bounded_state() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let mut source = String::from("---\nname: release\ndescription: fixture\n---\n# 문서\n");
        while source.len() < 170_000 {
            source.push_str("- 한글과 emoji 🧭가 있는 deterministic line\n");
        }
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id: snapshot_id.clone(),
                instance_id: instance_id.clone(),
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        assert_eq!(header.snapshot_id, snapshot_id);
        assert_eq!(header.instance_id, instance_id);
        assert_eq!(header.format, SourceFormat::Markdown);
        assert_eq!(header.total_bytes, source.len() as u64);
        assert!(header.total_chunks >= 3);
        assert_eq!(header.chunk_bytes, 64 * 1024);
        assert!(!header.changed_since_snapshot);
        assert!(header
            .canonical_path
            .ends_with("/.codex/skills/release/SKILL.md"));
        assert!(service.probe_group_count(&header.preview_session_id) <= 4_096);

        let mut rebuilt = String::new();
        for chunk_index in 0..header.total_chunks {
            let chunk = service
                .prepare_read(ReadSourceChunkRequest {
                    preview_session_id: header.preview_session_id.clone(),
                    source_revision: header.source_revision.clone(),
                    chunk_index,
                })
                .unwrap()
                .run()
                .unwrap();
            assert_eq!(chunk.chunk_index, chunk_index);
            assert_eq!(chunk.is_last, chunk_index + 1 == header.total_chunks);
            rebuilt.push_str(&chunk_text(chunk));
        }
        assert_eq!(rebuilt.as_bytes(), source.as_bytes());
        assert_eq!(service.probe_active_worker_count(), 0);
    }

    #[test]
    fn markdown_preview_returns_typed_inert_blocks_without_link_destinations() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let source = concat!(
            "---\nname: release\ndescription: fixture\n---\n",
            "# Heading\n\n",
            "Paragraph with `inline code` and [label](https://example.invalid/path).\n\n",
            "- list item\n\n",
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n",
            "> quote\n\n",
            "---\n\n",
            "```rust\nfn main() {}\n```\n\n",
            "<section onclick=\"run()\">literal html</section>\n",
            "![image alt](https://images.invalid/secret.png)\n",
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id,
                source_revision: header.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();
        let SourcePreviewContent::Markdown { block_fragments } = chunk.content else {
            panic!("Markdown fixture must use Markdown fragments");
        };
        let kinds: Vec<_> = block_fragments
            .iter()
            .map(|fragment| fragment.kind)
            .collect();
        for expected in [
            MarkdownBlockKind::Heading,
            MarkdownBlockKind::Paragraph,
            MarkdownBlockKind::List,
            MarkdownBlockKind::Table,
            MarkdownBlockKind::Blockquote,
            MarkdownBlockKind::ThematicBreak,
            MarkdownBlockKind::FencedCode,
            MarkdownBlockKind::InlineCode,
            MarkdownBlockKind::LiteralText,
        ] {
            assert!(kinds.contains(&expected), "missing {expected:?}");
        }
        let projected: String = block_fragments
            .iter()
            .map(|fragment| fragment.text.as_str())
            .collect();
        assert!(projected.contains("label"));
        assert!(projected.contains("image alt"));
        assert!(!projected.contains("https://"));
        let html = block_fragments
            .iter()
            .find(|fragment| fragment.text.contains("<section"))
            .unwrap();
        assert_eq!(html.kind, MarkdownBlockKind::LiteralText);
    }

    #[test]
    fn markdown_large_block_uses_one_stable_continuation_identity() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let body = "x".repeat(160_000);
        let source = format!("---\nname: release\ndescription: fixture\n---\n{body}\n");
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let mut paragraph_fragments = Vec::new();
        let mut rebuilt = String::new();
        for chunk_index in 0..header.total_chunks {
            let chunk = service
                .prepare_read(ReadSourceChunkRequest {
                    preview_session_id: header.preview_session_id.clone(),
                    source_revision: header.source_revision.clone(),
                    chunk_index,
                })
                .unwrap()
                .run()
                .unwrap();
            let SourcePreviewContent::Markdown { block_fragments } = chunk.content else {
                panic!("Markdown fixture must use Markdown fragments");
            };
            for fragment in block_fragments {
                rebuilt.push_str(&fragment.text);
                if fragment.kind == MarkdownBlockKind::Paragraph
                    && fragment.text.as_bytes().contains(&b'x')
                {
                    paragraph_fragments.push(fragment);
                }
            }
        }

        assert_eq!(rebuilt.as_bytes(), source.as_bytes());
        assert!(paragraph_fragments.len() >= 3);
        assert!(
            paragraph_fragments
                .windows(2)
                .all(|pair| pair[0].block_id == pair[1].block_id),
            "paragraph block ids: {:?}",
            paragraph_fragments
                .iter()
                .map(|fragment| fragment.block_id.as_str())
                .collect::<Vec<_>>()
        );
        assert_eq!(
            paragraph_fragments
                .iter()
                .filter(|fragment| fragment.starts_block)
                .count(),
            1
        );
        assert_eq!(
            paragraph_fragments
                .iter()
                .filter(|fragment| fragment.ends_block)
                .count(),
            1
        );
    }

    #[test]
    fn markdown_link_destination_is_omitted_when_it_crosses_a_chunk_boundary() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let header = "---\nname: release\ndescription: fixture\n---\n";
        let link_prefix = "[label](";
        let filler_length =
            usize::try_from(SOURCE_CHUNK_BYTES).unwrap() - header.len() - link_prefix.len();
        let source = format!(
            "{header}{}{link_prefix}https://private.invalid/secret)\n",
            "x".repeat(filler_length)
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        assert!(preview.total_chunks >= 2);
        let mut projected = String::new();
        for chunk_index in 0..preview.total_chunks {
            let chunk = service
                .prepare_read(ReadSourceChunkRequest {
                    preview_session_id: preview.preview_session_id.clone(),
                    source_revision: preview.source_revision.clone(),
                    chunk_index,
                })
                .unwrap()
                .run()
                .unwrap();
            projected.push_str(&chunk_text(chunk));
        }

        assert!(projected.contains("label"));
        assert!(!projected.contains("https://"));
        assert!(!projected.contains("private.invalid/secret"));
    }

    #[test]
    fn markdown_autolink_destination_is_omitted_when_its_scheme_crosses_a_chunk_boundary() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let header = "---\nname: release\ndescription: fixture\n---\n";
        let autolink_prefix = "<htt";
        let filler_length =
            usize::try_from(SOURCE_CHUNK_BYTES).unwrap() - header.len() - autolink_prefix.len();
        let source = format!(
            "{header}{}{autolink_prefix}ps://private.invalid/secret>\n",
            "x".repeat(filler_length)
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let mut projected = String::new();
        for chunk_index in 0..preview.total_chunks {
            projected.push_str(&chunk_text(
                service
                    .prepare_read(ReadSourceChunkRequest {
                        preview_session_id: preview.preview_session_id.clone(),
                        source_revision: preview.source_revision.clone(),
                        chunk_index,
                    })
                    .unwrap()
                    .run()
                    .unwrap(),
            ));
        }

        assert!(!projected.contains("https://"));
        assert!(!projected.contains("private.invalid/secret"));
    }

    #[test]
    fn markdown_multiline_blocks_keep_one_stable_identity_until_the_blank_boundary() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let source = concat!(
            "---\nname: release\ndescription: fixture\n---\n",
            "first paragraph line\nsecond paragraph line\n\n",
            "- first item\n- second item\n\n",
            "> first quote line\n> second quote line\n",
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: preview.preview_session_id,
                source_revision: preview.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();
        let SourcePreviewContent::Markdown { block_fragments } = chunk.content else {
            panic!("Markdown fixture must use Markdown fragments");
        };

        for (kind, needles) in [
            (
                MarkdownBlockKind::Paragraph,
                ["first paragraph line", "second paragraph line"],
            ),
            (MarkdownBlockKind::List, ["first item", "second item"]),
            (
                MarkdownBlockKind::Blockquote,
                ["first quote line", "second quote line"],
            ),
        ] {
            let matching: Vec<_> = block_fragments
                .iter()
                .filter(|fragment| {
                    fragment.kind == kind
                        && needles.iter().any(|needle| fragment.text.contains(needle))
                })
                .collect();
            assert_eq!(matching.len(), 2, "{kind:?} fragments: {matching:?}");
            assert_eq!(matching[0].block_id, matching[1].block_id, "{kind:?}");
            assert!(matching[0].starts_block, "{kind:?}");
            assert!(!matching[0].ends_block, "{kind:?}");
            assert!(!matching[1].starts_block, "{kind:?}");
            assert!(matching[1].ends_block, "{kind:?}");
        }
    }

    #[test]
    fn markdown_numbered_list_lines_keep_one_stable_identity() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let source = concat!(
            "---\nname: release\ndescription: fixture\n---\n",
            "1. first item\n2. second item\n",
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: preview.preview_session_id,
                source_revision: preview.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();
        let SourcePreviewContent::Markdown { block_fragments } = chunk.content else {
            panic!("Markdown fixture must use Markdown fragments");
        };
        let lists: Vec<_> = block_fragments
            .iter()
            .filter(|fragment| fragment.kind == MarkdownBlockKind::List)
            .collect();

        assert_eq!(lists.len(), 2);
        assert_eq!(lists[0].block_id, lists[1].block_id);
        assert!(lists[0].starts_block);
        assert!(!lists[0].ends_block);
        assert!(!lists[1].starts_block);
        assert!(lists[1].ends_block);
    }

    #[test]
    fn markdown_literal_regions_preserve_text_but_unterminated_destinations_stay_redacted() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let header = concat!(
            "---\n",
            "name: release\n",
            "description: '[frontmatter](https://frontmatter.invalid)'\n",
            "---\n",
            "```text\n",
            "[fenced](https://fenced.invalid)\n",
            "```\n",
            "`[inline](https://inline.invalid)`\n",
            "<div data-link=\"[raw](https://raw.invalid)\">literal</div>\n",
        );
        let unmatched = "[not-a-link](https://unterminated.invalid/secret\nstill literal\n";
        let filler_length = usize::try_from(SOURCE_CHUNK_BYTES)
            .unwrap()
            .saturating_sub(header.len() + "[not-a-link](".len());
        let source = format!("{header}{}{unmatched}", "x".repeat(filler_length));
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let mut projected = String::new();
        for chunk_index in 0..preview.total_chunks {
            projected.push_str(&chunk_text(
                service
                    .prepare_read(ReadSourceChunkRequest {
                        preview_session_id: preview.preview_session_id.clone(),
                        source_revision: preview.source_revision.clone(),
                        chunk_index,
                    })
                    .unwrap()
                    .run()
                    .unwrap(),
            ));
        }

        assert!(projected.contains("[frontmatter](https://frontmatter.invalid)"));
        assert!(projected.contains("[fenced](https://fenced.invalid)"));
        assert!(projected.contains("`[inline](https://inline.invalid)`"));
        assert!(projected.contains("<div data-link=\"[raw](https://raw.invalid)\">literal</div>"));
        assert!(projected.contains("not-a-link"));
        assert!(projected.contains("still literal"));
        assert!(!projected.contains("unterminated.invalid/secret"));
    }

    #[test]
    fn markdown_reference_links_and_definitions_project_only_labels_and_alt_text() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let source = concat!(
            "---\nname: release\ndescription: fixture\n---\n",
            "[general label][general-id]\n",
            "![image alt][image-id]\n",
            "[collapsed label][]\n",
            "[shortcut label]\n",
            "[general-id]: https://private.invalid/general\n",
            "[image-id]: https://private.invalid/image\n",
            "[collapsed label]: https://private.invalid/collapsed\n",
            "[shortcut label]: https://private.invalid/shortcut\n",
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let projected = chunk_text(
            service
                .prepare_read(ReadSourceChunkRequest {
                    preview_session_id: preview.preview_session_id,
                    source_revision: preview.source_revision,
                    chunk_index: 0,
                })
                .unwrap()
                .run()
                .unwrap(),
        );

        for label in [
            "general label",
            "image alt",
            "collapsed label",
            "shortcut label",
        ] {
            assert!(projected.contains(label), "missing label: {label}");
        }
        assert!(!projected.contains("https://"));
        assert!(!projected.contains("private.invalid"));
    }

    #[test]
    fn markdown_reference_definition_destination_is_omitted_across_a_chunk_boundary() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let header = "---\nname: release\ndescription: fixture\n---\n";
        let definition_prefix = "[private-id]: ";
        let padding_length = usize::try_from(SOURCE_CHUNK_BYTES).unwrap()
            - header.len()
            - 1
            - definition_prefix.len();
        let source = format!(
            "{header}{}\n{definition_prefix}https://private.invalid/cross{}\n",
            "x".repeat(padding_length),
            "y".repeat(MARKDOWN_BOUNDARY_LOOKAHEAD_BYTES + 32),
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let mut projected = String::new();
        for chunk_index in 0..preview.total_chunks {
            projected.push_str(&chunk_text(
                service
                    .prepare_read(ReadSourceChunkRequest {
                        preview_session_id: preview.preview_session_id.clone(),
                        source_revision: preview.source_revision.clone(),
                        chunk_index,
                    })
                    .unwrap()
                    .run()
                    .unwrap(),
            ));
        }

        assert!(!projected.contains("https://"));
        assert!(!projected.contains("private.invalid/cross"));
    }

    #[test]
    fn markdown_email_autolink_never_exposes_address_single_or_cross_chunk() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let header = concat!(
            "---\nname: release\ndescription: fixture\n---\n",
            "<single.user@", "example.invalid>\n",
        );
        let cross_prefix = "<cross.user";
        let padding_length =
            usize::try_from(SOURCE_CHUNK_BYTES).unwrap() - header.len() - cross_prefix.len();
        let source = format!(
            "{header}{}{cross_prefix}@example.invalid>{}\n",
            "x".repeat(padding_length),
            "y".repeat(MARKDOWN_BOUNDARY_LOOKAHEAD_BYTES + 32),
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let mut projected = String::new();
        for chunk_index in 0..preview.total_chunks {
            projected.push_str(&chunk_text(
                service
                    .prepare_read(ReadSourceChunkRequest {
                        preview_session_id: preview.preview_session_id.clone(),
                        source_revision: preview.source_revision.clone(),
                        chunk_index,
                    })
                    .unwrap()
                    .run()
                    .unwrap(),
            ));
        }

        assert!(!projected.contains(concat!("single.user@", "example.invalid")));
        assert!(!projected.contains(concat!("cross.user@", "example.invalid")));
        assert!(projected.matches("link").count() >= 2);
    }

    #[test]
    fn markdown_inline_code_preserves_link_shaped_text_across_a_chunk_boundary() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let header = "---\nname: release\ndescription: fixture\n---\n";
        let code_prefix = "`";
        let filler_length =
            usize::try_from(SOURCE_CHUNK_BYTES).unwrap() - header.len() - code_prefix.len();
        let source = format!(
            "{header}{}{code_prefix}[label](https://inside.invalid/private) tail`\n",
            "x".repeat(filler_length)
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let mut projected = String::new();
        for chunk_index in 0..preview.total_chunks {
            projected.push_str(&chunk_text(
                service
                    .prepare_read(ReadSourceChunkRequest {
                        preview_session_id: preview.preview_session_id.clone(),
                        source_revision: preview.source_revision.clone(),
                        chunk_index,
                    })
                    .unwrap()
                    .run()
                    .unwrap(),
            ));
        }

        assert_eq!(projected.as_bytes(), source.as_bytes());
    }

    #[test]
    fn markdown_multibacktick_inline_code_preserves_link_text_across_chunk_boundaries() {
        let header = "---\nname: release\ndescription: fixture\n---\n";
        let tail = format!("{}\n", "y".repeat(MARKDOWN_BOUNDARY_LOOKAHEAD_BYTES + 32));
        let opening_prefix = "`";
        let opening_filler =
            usize::try_from(SOURCE_CHUNK_BYTES).unwrap() - header.len() - opening_prefix.len();
        let opening_split = format!(
            "{header}{}{opening_prefix}``[label](https://inside.invalid/open)```{tail}",
            "x".repeat(opening_filler)
        );
        let before_closing = "```[label](https://inside.invalid/close) code";
        let closing_filler =
            usize::try_from(SOURCE_CHUNK_BYTES).unwrap() - header.len() - before_closing.len() - 1;
        let closing_split = format!(
            "{header}{}{before_closing}```{tail}",
            "x".repeat(closing_filler)
        );
        let long_delimiter = "`".repeat(300);
        let long_run = format!(
            "{header}x{long_delimiter}[label](https://inside.invalid/long){long_delimiter} tail\n"
        );

        for (case, source) in [
            ("opening-run-split", opening_split),
            ("closing-run-split", closing_split),
            ("run-over-u8", long_run),
        ] {
            let fixture = tempdir().unwrap();
            let home = fixture.path().join("home");
            let path = home.join(".codex/skills/release/SKILL.md");
            write(&path, source.as_bytes());
            let (service, snapshot_id, instance_id) = service_for(&home);

            let preview = service
                .prepare_open(OpenSourceRequest {
                    snapshot_id,
                    instance_id,
                    view_generation: 1,
                })
                .unwrap()
                .run()
                .unwrap();
            let mut projected = String::new();
            for chunk_index in 0..preview.total_chunks {
                projected.push_str(&chunk_text(
                    service
                        .prepare_read(ReadSourceChunkRequest {
                            preview_session_id: preview.preview_session_id.clone(),
                            source_revision: preview.source_revision.clone(),
                            chunk_index,
                        })
                        .unwrap()
                        .run()
                        .unwrap(),
                ));
            }

            assert_eq!(projected.as_bytes(), source.as_bytes(), "{case}");
        }
    }

    #[test]
    fn markdown_autolinks_omit_case_insensitive_and_non_http_uri_destinations() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let source = concat!(
            "---\nname: release\ndescription: fixture\n---\n",
            "<HTTPS://private.invalid/secret>\n",
            "<mailto:private@", "example.invalid>\n",
            "<custom+scheme://private.invalid/other>\n",
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let projected = chunk_text(
            service
                .prepare_read(ReadSourceChunkRequest {
                    preview_session_id: preview.preview_session_id,
                    source_revision: preview.source_revision,
                    chunk_index: 0,
                })
                .unwrap()
                .run()
                .unwrap(),
        );

        assert!(!projected.contains("private.invalid"));
        assert!(!projected.contains(concat!("private@", "example.invalid")));
        assert!(!projected.contains("custom+scheme"));
    }

    #[test]
    fn markdown_links_and_images_project_only_label_and_alt_text() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let source = concat!(
            "---\nname: release\ndescription: fixture\n---\n",
            "Before [label](https://private.invalid/a) and ",
            "![image alt](file:///fixture-home/private.png) after.\n",
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let projected = chunk_text(
            service
                .prepare_read(ReadSourceChunkRequest {
                    preview_session_id: preview.preview_session_id,
                    source_revision: preview.source_revision,
                    chunk_index: 0,
                })
                .unwrap()
                .run()
                .unwrap(),
        );

        assert!(projected.contains("Before label and image alt after.\n"));
        assert!(!projected.contains('['));
        assert!(!projected.contains("private.invalid"));
        assert!(!projected.contains("file:///"));
    }

    #[test]
    fn markdown_non_autolink_angle_text_preserves_original_case_exactly() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let source = concat!(
            "---\nname: release\ndescription: fixture\n---\n",
            "prefix <HTx-not-an-autolink> suffix\n",
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let projected = chunk_text(
            service
                .prepare_read(ReadSourceChunkRequest {
                    preview_session_id: preview.preview_session_id,
                    source_revision: preview.source_revision,
                    chunk_index: 0,
                })
                .unwrap()
                .run()
                .unwrap(),
        );

        assert!(projected.contains("prefix <HTx-not-an-autolink> suffix\n"));
    }

    #[test]
    fn markdown_custom_scheme_autolink_is_omitted_across_a_chunk_boundary() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let header = "---\nname: release\ndescription: fixture\n---\n";
        let prefix = "<git+";
        let filler_length =
            usize::try_from(SOURCE_CHUNK_BYTES).unwrap() - header.len() - prefix.len();
        let source = format!(
            "{header}{}{prefix}ssh://private.invalid/repository>\n",
            "x".repeat(filler_length)
        );
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let mut projected = String::new();
        for chunk_index in 0..preview.total_chunks {
            projected.push_str(&chunk_text(
                service
                    .prepare_read(ReadSourceChunkRequest {
                        preview_session_id: preview.preview_session_id.clone(),
                        source_revision: preview.source_revision.clone(),
                        chunk_index,
                    })
                    .unwrap()
                    .run()
                    .unwrap(),
            ));
        }

        assert!(!projected.contains("git+ssh"));
        assert!(!projected.contains("private.invalid/repository"));
    }

    #[test]
    fn markdown_invalid_custom_scheme_probe_preserves_exact_text_across_a_chunk_boundary() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let header = "---\nname: release\ndescription: fixture\n---\n";
        let prefix = "<CuSt";
        let literal = "om-no-colon> tail\n";
        let filler_length =
            usize::try_from(SOURCE_CHUNK_BYTES).unwrap() - header.len() - prefix.len();
        let source = format!("{header}{}{prefix}{literal}", "x".repeat(filler_length));
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let mut projected = String::new();
        for chunk_index in 0..preview.total_chunks {
            projected.push_str(&chunk_text(
                service
                    .prepare_read(ReadSourceChunkRequest {
                        preview_session_id: preview.preview_session_id.clone(),
                        source_revision: preview.source_revision.clone(),
                        chunk_index,
                    })
                    .unwrap()
                    .run()
                    .unwrap(),
            ));
        }

        assert!(
            projected.contains("<CuStom-no-colon> tail\n"),
            "projected tail: {:?}",
            &projected[projected.len().saturating_sub(64)..]
        );
    }

    #[test]
    fn markdown_nesting_above_64_falls_back_to_exact_literal_text() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let within = format!("{}within\n", "> ".repeat(MAX_MARKDOWN_NESTING));
        let excessive = format!("{}excessive\n", "> ".repeat(MAX_MARKDOWN_NESTING + 1));
        let source = format!("---\nname: release\ndescription: fixture\n---\n{within}{excessive}");
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: preview.preview_session_id,
                source_revision: preview.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();
        let SourcePreviewContent::Markdown { block_fragments } = chunk.content else {
            panic!("Markdown fixture must use Markdown fragments");
        };

        assert!(block_fragments.iter().any(|fragment| {
            fragment.kind == MarkdownBlockKind::Blockquote && fragment.text.contains("within")
        }));
        assert!(block_fragments.iter().any(|fragment| {
            fragment.kind == MarkdownBlockKind::LiteralText && fragment.text == excessive
        }));
    }

    #[test]
    fn markdown_fragment_cap_fallback_never_exposes_later_destinations() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let mut source = String::from("---\nname: release\ndescription: fixture\n---\n");
        for index in 0..300 {
            source.push_str(&format!("line {index} with `inline` code\n"));
        }
        source.push_str("tail [label](https://private.invalid/after-cap)\n");
        source.push_str("<git+ssh://private.invalid/autolink-after-cap>\n");
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: preview.preview_session_id,
                source_revision: preview.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();
        let SourcePreviewContent::Markdown { block_fragments } = chunk.content else {
            panic!("Markdown fixture must use Markdown fragments");
        };
        let projected: String = block_fragments
            .iter()
            .map(|fragment| fragment.text.as_str())
            .collect();

        assert!(block_fragments.len() <= MAX_MARKDOWN_FRAGMENTS);
        assert!(!projected.contains("private.invalid"));
        assert!(!projected.contains("after-cap"));
    }

    #[test]
    fn markdown_destination_nesting_above_64_still_omits_destination() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let destination = format!(
            "{}private.invalid{}",
            "(".repeat(MAX_MARKDOWN_NESTING + 1),
            ")".repeat(MAX_MARKDOWN_NESTING + 1)
        );
        let line = format!("prefix [label]({destination}) suffix\n");
        let source = format!("---\nname: release\ndescription: fixture\n---\n{line}");
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let projected = chunk_text(
            service
                .prepare_read(ReadSourceChunkRequest {
                    preview_session_id: preview.preview_session_id,
                    source_revision: preview.source_revision,
                    chunk_index: 0,
                })
                .unwrap()
                .run()
                .unwrap(),
        );

        assert!(projected.contains("label"));
        assert!(projected.contains("prefix"));
        assert!(projected.contains("suffix"));
        assert!(!projected.contains("private.invalid"));
    }

    #[test]
    fn markdown_excessive_blockquote_literal_still_omits_destinations() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let line = format!(
            "{}[label](https://private.invalid/excessive) tail\n",
            "> ".repeat(MAX_MARKDOWN_NESTING + 1)
        );
        let source = format!("---\nname: release\ndescription: fixture\n---\n{line}");
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for(&home);

        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: preview.preview_session_id,
                source_revision: preview.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();
        let SourcePreviewContent::Markdown { block_fragments } = chunk.content else {
            panic!("Markdown fixture must use Markdown fragments");
        };
        let projected: String = block_fragments
            .iter()
            .map(|fragment| fragment.text.as_str())
            .collect();

        assert!(block_fragments.iter().any(|fragment| {
            fragment.kind == MarkdownBlockKind::LiteralText && fragment.text.contains("label")
        }));
        assert!(projected.contains("tail"));
        assert!(!projected.contains("private.invalid"));
    }

    #[test]
    fn invalid_utf8_fails_open_without_publishing_a_ready_session() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let mut source = b"---\nname: release\ndescription: fixture\n---\n".to_vec();
        source.extend_from_slice(&[0xf0, 0x28, 0x8c, 0x28]);
        write(&path, &source);
        let (service, snapshot_id, instance_id) = service_for(&home);

        let error = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap_err();
        assert_eq!(error.code, "source_invalid_utf8");
        assert_eq!(service.probe_ready_session_count(), 0);
        assert_eq!(service.probe_active_worker_count(), 0);
    }

    #[test]
    fn nul_and_disallowed_control_bytes_fail_open_as_binary() {
        for forbidden in [0_u8, 0x01, 0x0b, 0x0c, 0x1f, 0x7f] {
            let fixture = tempdir().unwrap();
            let home = fixture.path().join("home");
            let path = home.join(".codex/skills/release/SKILL.md");
            let mut source = b"---\nname: release\ndescription: fixture\n---\n".to_vec();
            source.push(forbidden);
            write(&path, &source);
            let (service, snapshot_id, instance_id) = service_for(&home);

            let error = service
                .prepare_open(OpenSourceRequest {
                    snapshot_id,
                    instance_id,
                    view_generation: 1,
                })
                .unwrap()
                .run()
                .unwrap_err();

            assert_eq!(error.code, "source_binary", "forbidden byte: {forbidden}");
            assert_eq!(service.probe_ready_session_count(), 0);
            assert_eq!(service.probe_active_worker_count(), 0);
        }
    }

    #[test]
    fn newer_generation_cancels_open_without_starting_or_queueing_a_second_worker() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let source = "---\nname: release\ndescription: fixture\n---\nbody\n".repeat(2_000);
        write(&path, source.as_bytes());
        let reader = Arc::new(BlockingReader::default());
        let (service, snapshot_id, instance_id) = service_for_with_reader(&home, reader.clone());
        let first = service
            .prepare_open(OpenSourceRequest {
                snapshot_id: snapshot_id.clone(),
                instance_id: instance_id.clone(),
                view_generation: 1,
            })
            .unwrap();
        let worker = std::thread::spawn(move || first.run());
        reader.wait_until_entered();

        let busy = match service.prepare_open(OpenSourceRequest {
            snapshot_id: snapshot_id.clone(),
            instance_id: instance_id.clone(),
            view_generation: 2,
        }) {
            Err(error) => error,
            Ok(_) => panic!("new worker must not start while cancellation is settling"),
        };
        assert_eq!(busy.code, "preview_busy");
        assert_eq!(service.probe_active_worker_count(), 1);
        reader.release();
        assert_eq!(
            worker.join().unwrap().unwrap_err().code,
            "preview_cancelled"
        );
        assert_eq!(service.probe_active_worker_count(), 0);

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 2,
            })
            .unwrap()
            .run()
            .unwrap();
        assert_eq!(header.view_generation, 2);
    }

    #[test]
    fn scan_digest_marks_same_metadata_content_change_and_read_rejects_later_change() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let original = b"---\nname: release\ndescription: before0\n---\nbody0\n";
        let changed = b"---\nname: release\ndescription: after00\n---\nbody1\n";
        assert_eq!(original.len(), changed.len());
        write(&path, original);
        let before = fs::metadata(&path).unwrap();
        let (service, snapshot_id, instance_id) = service_for(&home);

        let mut stream = OpenOptions::new()
            .write(true)
            .truncate(true)
            .open(&path)
            .unwrap();
        stream.write_all(changed).unwrap();
        stream
            .set_times(FileTimes::new().set_modified(before.modified().unwrap()))
            .unwrap();
        drop(stream);
        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        assert!(header.changed_since_snapshot);

        let later = b"---\nname: release\ndescription: later00\n---\nbody2\n";
        assert_eq!(changed.len(), later.len());
        let modified = fs::metadata(&path).unwrap().modified().unwrap();
        let mut stream = OpenOptions::new()
            .write(true)
            .truncate(true)
            .open(&path)
            .unwrap();
        stream.write_all(later).unwrap();
        stream
            .set_times(FileTimes::new().set_modified(modified))
            .unwrap();
        drop(stream);
        let error = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id,
                source_revision: header.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap_err();
        assert_eq!(error.code, "source_stale");
        assert_eq!(service.probe_ready_session_count(), 0);
        assert_eq!(service.probe_active_worker_count(), 0);
    }

    #[test]
    fn group_digest_rejects_in_memory_overlay_without_metadata_change() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        write(
            &path,
            b"---\nname: release\ndescription: fixture\n---\nbody that stays metadata-stable\n",
        );
        let reader = Arc::new(OverlayReader::new(12));
        let (service, snapshot_id, instance_id) = service_for_with_reader(&home, reader.clone());
        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();

        reader.enable();
        let error = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id,
                source_revision: header.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap_err();

        assert_eq!(error.code, "source_stale");
        assert_eq!(service.probe_ready_session_count(), 0);
        assert_eq!(service.probe_active_worker_count(), 0);
    }

    #[test]
    fn utf8_boundary_chunk_rehashes_every_intersecting_group_with_64k_buffers() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let mut source = b"---\nname: release\ndescription: fixture\n---\n".to_vec();
        source.resize((64 * 1024) - 1, b'a');
        source.extend_from_slice("🧭".as_bytes());
        source.resize((2 * 64 * 1024) + 256, b'b');
        write(&path, &source);
        let reader = Arc::new(RecordingReader::default());
        let (service, snapshot_id, instance_id) = service_for_with_reader(&home, reader.clone());

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        assert!(reader.max_requested.load(Ordering::Acquire) <= READ_BUFFER_BYTES);
        reader.clear();

        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id,
                source_revision: header.source_revision,
                chunk_index: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let calls = reader.calls();

        assert!(reader.max_requested.load(Ordering::Acquire) <= READ_BUFFER_BYTES);
        assert!(calls.contains(&(0, READ_BUFFER_BYTES)));
        assert!(calls.contains(&((64 * 1024) as u64, READ_BUFFER_BYTES)));
        assert_eq!(
            chunk_text(chunk).as_bytes(),
            &source[(64 * 1024) - 1..2 * 64 * 1024]
        );
    }

    #[test]
    fn markdown_block_boundary_chunk_reads_three_intersecting_groups_without_gap_or_overlap() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let mut source = b"---\nname: release\ndescription: fixture\n---\n".to_vec();
        source.resize((64 * 1024) - 1, b'a');
        source.extend_from_slice("🧭".as_bytes());
        source.resize((2 * 64 * 1024) + 4_092, b'b');
        source.push(b'\n');
        source.resize((3 * 64 * 1024) + 512, b'c');
        write(&path, &source);
        let reader = Arc::new(RecordingReader::default());
        let (service, snapshot_id, instance_id) = service_for_with_reader(&home, reader.clone());

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        reader.clear();

        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id.clone(),
                source_revision: header.source_revision.clone(),
                chunk_index: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let calls = reader.calls();
        let chunk_projection = chunk_text(chunk);
        let expected_start = (64 * 1024) - 1;
        let expected_end = (2 * 64 * 1024) + 4_093;

        assert!(calls.contains(&(0, READ_BUFFER_BYTES)));
        assert!(calls.contains(&((64 * 1024) as u64, READ_BUFFER_BYTES)));
        assert!(calls.contains(&((2 * 64 * 1024) as u64, READ_BUFFER_BYTES)));
        assert!(chunk_projection.len() <= usize::try_from(MAX_SOURCE_CHUNK_BYTES).unwrap());
        assert_eq!(
            chunk_projection.as_bytes(),
            &source[expected_start..expected_end]
        );

        let mut reconstructed = String::new();
        for chunk_index in 0..header.total_chunks {
            reconstructed.push_str(&chunk_text(
                service
                    .prepare_read(ReadSourceChunkRequest {
                        preview_session_id: header.preview_session_id.clone(),
                        source_revision: header.source_revision.clone(),
                        chunk_index,
                    })
                    .unwrap()
                    .run()
                    .unwrap(),
            ));
        }
        assert_eq!(reconstructed.as_bytes(), source.as_slice());
    }

    #[test]
    fn markdown_boundary_past_68k_cap_falls_back_without_skipping_source() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let mut source = b"---\nname: release\ndescription: fixture\n---\n".to_vec();
        source.resize(64 * 1024, b'a');
        source.resize((64 * 1024) + MARKDOWN_BOUNDARY_LOOKAHEAD_BYTES, b'b');
        source.push(b'\n');
        source.resize((2 * 64 * 1024) + 256, b'c');
        write(&path, &source);
        let (service, snapshot_id, instance_id) = service_for(&home);

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let first = chunk_text(
            service
                .prepare_read(ReadSourceChunkRequest {
                    preview_session_id: header.preview_session_id.clone(),
                    source_revision: header.source_revision.clone(),
                    chunk_index: 0,
                })
                .unwrap()
                .run()
                .unwrap(),
        );

        assert_eq!(first.as_bytes(), &source[..64 * 1024]);
        let mut reconstructed = String::new();
        for chunk_index in 0..header.total_chunks {
            reconstructed.push_str(&chunk_text(
                service
                    .prepare_read(ReadSourceChunkRequest {
                        preview_session_id: header.preview_session_id.clone(),
                        source_revision: header.source_revision.clone(),
                        chunk_index,
                    })
                    .unwrap()
                    .run()
                    .unwrap(),
            ));
        }
        assert_eq!(reconstructed.as_bytes(), source.as_slice());
    }

    #[test]
    fn local_read_lease_is_released_before_open_and_read_worker_completion() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        write(
            &path,
            b"---\nname: release\ndescription: fixture\n---\nbody\n",
        );
        let (service, snapshot_id, instance_id) = service_for(&home);
        let operations = Arc::clone(&service.operations);
        let observed = Arc::new(Mutex::new(Vec::new()));
        let observed_by_callback = Arc::clone(&observed);
        service.set_completion_observer(Arc::new(move || {
            observed_by_callback
                .lock()
                .unwrap()
                .push(operations.probe_active_local_reads());
        }));

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id,
                source_revision: header.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();

        assert_eq!(*observed.lock().unwrap(), vec![0, 0]);
    }

    #[test]
    fn blocked_source_workers_hold_the_install_gate_until_the_read_lease_drops() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        write(
            &path,
            b"---\nname: release\ndescription: fixture\n---\nbody\n",
        );
        let reader = Arc::new(BlockingReader::default());
        let (service, snapshot_id, instance_id) = service_for_with_reader(&home, reader.clone());
        let open = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap();
        let open_worker = std::thread::spawn(move || open.run());
        reader.wait_until_entered();
        assert_eq!(service.operations.probe_active_local_reads(), 1);
        assert!(matches!(
            service
                .operations
                .begin_install("blocked-by-open", "preview", None),
            Err(crate::operation_coordinator::BeginInstallError::OperationBusy)
        ));
        reader.release();
        let header = open_worker.join().unwrap().unwrap();
        assert_eq!(service.operations.probe_active_local_reads(), 0);
        drop(
            service
                .operations
                .begin_install("after-open", "preview", None)
                .unwrap(),
        );

        reader.arm();
        let read = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id,
                source_revision: header.source_revision,
                chunk_index: 0,
            })
            .unwrap();
        let read_worker = std::thread::spawn(move || read.run());
        reader.wait_until_entered();
        assert_eq!(service.operations.probe_active_local_reads(), 1);
        assert!(matches!(
            service
                .operations
                .begin_install("blocked-by-read", "apply", None),
            Err(crate::operation_coordinator::BeginInstallError::OperationBusy)
        ));
        reader.release();
        read_worker.join().unwrap().unwrap();
        assert_eq!(service.operations.probe_active_local_reads(), 0);
        assert!(service
            .operations
            .begin_install("after-read", "apply", None)
            .is_ok());
    }

    #[test]
    fn ready_session_state_whitelists_fd_identity_and_bounded_index_only() {
        let module = include_str!("source_inspection.rs");
        let body = module
            .split_once("struct ReadySourceSession {")
            .unwrap()
            .1
            .split_once("\n}")
            .unwrap()
            .0;

        let fields = body
            .lines()
            .filter_map(|line| line.trim().split_once(':').map(|(name, _)| name))
            .collect::<Vec<_>>();

        assert_eq!(
            fields,
            vec![
                "key",
                "file",
                "format",
                "total_bytes",
                "total_chunks",
                "groups",
                "selected_span",
            ]
        );
    }

    #[test]
    fn markdown_checkpoint_contains_only_bounded_counters_offsets_and_digests() {
        let module = include_str!("source_markdown.rs");
        let probe_body = module
            .split_once("struct MarkdownAutolinkProbe {")
            .unwrap()
            .1
            .split_once("\n}")
            .unwrap()
            .0;
        let checkpoint_body = module
            .split_once(&format!("{}{}", "struct Markdown", "Checkpoint {"))
            .unwrap()
            .1
            .split_once("\n}")
            .unwrap()
            .0;

        assert_eq!(
            probe_body.trim(),
            "len: u8,\n    scheme_valid: bool,\n    overflowed: bool,"
        );
        for forbidden in ["String", "Vec<", "[u8;", "&str", "&[u8]"] {
            assert!(!checkpoint_body.contains(forbidden));
        }
        assert!(!std::mem::needs_drop::<MarkdownCheckpoint>());
        assert!(std::mem::size_of::<MarkdownCheckpoint>() <= 128);
    }

    #[test]
    fn markdown_unmatched_brackets_use_one_bounded_label_scan() {
        let source = "[".repeat(usize::try_from(MAX_SOURCE_CHUNK_BYTES).unwrap());
        let (_, scan_visits) =
            super::super::source_markdown::markdown_label_index(source.as_bytes());

        let projected = super::super::source_markdown::sanitize_markdown_destinations(
            &source,
            MarkdownCheckpoint::default(),
            &[],
            true,
            0,
        );

        assert_eq!(projected, source);
        assert_eq!(scan_visits, source.len());
    }

    #[test]
    fn huge_logical_source_uses_at_most_4096_adaptive_digest_groups() {
        let total_bytes = (16_u64 * 1024 * 1024 * 1024) + 1;
        let group_bytes = digest_group_bytes(total_bytes);
        let group_count = ceil_div(total_bytes, group_bytes);

        assert!(group_bytes > SOURCE_CHUNK_BYTES);
        assert_eq!(group_bytes % SOURCE_CHUNK_BYTES, 0);
        assert!(group_count <= MAX_DIGEST_GROUPS);
        assert!(
            usize::try_from(group_count).unwrap() * std::mem::size_of::<SourceDigestGroup>()
                <= 4096 * std::mem::size_of::<SourceDigestGroup>()
        );
        assert_eq!(READ_BUFFER_BYTES, 64 * 1024);
    }

    #[test]
    fn json_config_entry_locator_selects_exact_value_bytes_in_the_text_partition() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".claude/settings.json");
        let source =
            r#"{"hooks":{"a/b~c":[{"description":"한글 🧭","command":"private"}],"Other":[]}}"#;
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for_tool_with_reader(
            &home,
            ToolId::ClaudeCode,
            "a/b~c",
            Arc::new(VerifiedFdSourceReadPort),
        );

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id.clone(),
                source_revision: header.source_revision.clone(),
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();

        assert_eq!(header.format, SourceFormat::Json);
        assert_eq!(header.selected_chunk_index, Some(0));
        assert_eq!(header.issue, None);
        match chunk.content {
            SourcePreviewContent::Text {
                before,
                selected,
                after,
            } => {
                assert_eq!(
                    selected.as_deref(),
                    Some(r#"{"description":"한글 🧭","command":"private"}"#)
                );
                assert_eq!(format!("{before}{}{after}", selected.unwrap()), source);
            }
            SourcePreviewContent::Markdown { .. } => panic!("JSON must use text partition"),
        }
    }

    #[test]
    fn selected_chunk_index_uses_the_utf8_adjusted_chunk_boundary() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".hermes/config.yaml");
        let prefix = "padding: ";
        let before_value = "\nhooks:\n  Before: ";
        let padding_length =
            usize::try_from(SOURCE_CHUNK_BYTES - 1).unwrap() - prefix.len() - before_value.len();
        let source = format!(
            "{prefix}{}{before_value}한글 🧭\n",
            "a".repeat(padding_length)
        );
        assert_eq!(source.find("한글").unwrap() as u64, SOURCE_CHUNK_BYTES - 1);
        write(&path, source.as_bytes());
        let (service, snapshot_id, instance_id) = service_for_tool_with_reader(
            &home,
            ToolId::Hermes,
            "Before",
            Arc::new(VerifiedFdSourceReadPort),
        );

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();

        assert_eq!(header.selected_chunk_index, Some(1));
        let first = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id.clone(),
                source_revision: header.source_revision.clone(),
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();
        let second = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id,
                source_revision: header.source_revision,
                chunk_index: 1,
            })
            .unwrap()
            .run()
            .unwrap();

        match first.content {
            SourcePreviewContent::Text { selected, .. } => assert_eq!(selected, None),
            SourcePreviewContent::Markdown { .. } => panic!("YAML must use text partition"),
        }
        match second.content {
            SourcePreviewContent::Text { selected, .. } => {
                assert_eq!(selected.as_deref(), Some("한글 🧭"));
            }
            SourcePreviewContent::Markdown { .. } => panic!("YAML must use text partition"),
        }
    }

    #[test]
    fn open_reverifies_identity_after_selected_chunk_boundary_read() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".hermes/config.yaml");
        let prefix = "padding: ";
        let before_value = "\nhooks:\n  Before: ";
        let padding_length =
            usize::try_from(SOURCE_CHUNK_BYTES - 1).unwrap() - prefix.len() - before_value.len();
        let source = format!(
            "{prefix}{}{before_value}한글 🧭\n",
            "a".repeat(padding_length)
        );
        write(&path, source.as_bytes());
        let reader = Arc::new(BoundaryMutatingReader {
            path: path.clone(),
            mutated: AtomicBool::new(false),
        });
        let (service, snapshot_id, instance_id) =
            service_for_tool_with_reader(&home, ToolId::Hermes, "Before", reader);

        let error = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap_err();

        assert_eq!(error.code, "source_stale");
        assert_eq!(service.probe_ready_session_count(), 0);
    }

    #[test]
    fn open_rejects_mutation_between_yaml_alias_locator_passes() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".hermes/config.yaml");
        write(&path, b"shared: &all\n  Before: run\nhooks: *all\n");
        let reader = Arc::new(LocatorSecondPassMutatingReader {
            path: path.clone(),
            zero_offset_reads: AtomicUsize::new(0),
        });
        let (service, snapshot_id, instance_id) =
            service_for_tool_with_reader(&home, ToolId::Hermes, "Before", reader);

        let error = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap_err();

        assert_eq!(error.code, "source_stale");
        assert_eq!(service.probe_ready_session_count(), 0);
    }

    #[test]
    fn open_classifies_locator_truncation_as_source_stale() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".hermes/config.yaml");
        write(&path, b"hooks:\n  Before: run\n");
        let reader = Arc::new(LocatorTruncatingReader {
            path: path.clone(),
            zero_offset_reads: AtomicUsize::new(0),
        });
        let (service, snapshot_id, instance_id) =
            service_for_tool_with_reader(&home, ToolId::Hermes, "Before", reader);

        let error = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap_err();

        assert_eq!(error.code, "source_stale");
        assert_eq!(service.probe_ready_session_count(), 0);
    }

    #[test]
    fn changed_json_with_missing_locator_keeps_full_preview_without_guessed_highlight() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".claude/settings.json");
        write(
            &path,
            br#"{"hooks":{"Before":[{"description":"same"}]},"other":{"description":"same"}}"#,
        );
        let (service, snapshot_id, instance_id) = service_for_tool_with_reader(
            &home,
            ToolId::ClaudeCode,
            "Before",
            Arc::new(VerifiedFdSourceReadPort),
        );
        let changed =
            r#"{"hooks":{"After":[{"description":"same"}]},"other":{"description":"same"}}"#;
        write(&path, changed.as_bytes());

        let header = service
            .prepare_open(OpenSourceRequest {
                snapshot_id,
                instance_id,
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        let chunk = service
            .prepare_read(ReadSourceChunkRequest {
                preview_session_id: header.preview_session_id,
                source_revision: header.source_revision,
                chunk_index: 0,
            })
            .unwrap()
            .run()
            .unwrap();

        assert!(header.changed_since_snapshot);
        assert_eq!(header.selected_chunk_index, None);
        assert_eq!(
            header.issue.as_ref().map(|issue| issue.code.as_str()),
            Some("entry_locator_stale")
        );
        match chunk.content {
            SourcePreviewContent::Text {
                before,
                selected,
                after,
            } => {
                assert_eq!(before, changed);
                assert_eq!(selected, None);
                assert_eq!(after, "");
            }
            SourcePreviewContent::Markdown { .. } => panic!("JSON must use text partition"),
        }
    }

    #[test]
    fn ai_capture_returns_exact_utf8_bytes_bound_to_the_preview_revision() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        let source = format!(
            "---\nname: release\ndescription: fixture\n---\n# 설명\n{}\n",
            "component body\n".repeat(5_000)
        );
        write(&path, source.as_bytes());
        let reader = Arc::new(RecordingReader::default());
        let (service, snapshot_id, instance_id) = service_for_with_reader(&home, reader.clone());
        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id: snapshot_id.clone(),
                instance_id: instance_id.clone(),
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        reader.clear();

        let captured = service
            .capture_revision_bound_source(
                &snapshot_id,
                &instance_id,
                &preview.source_revision,
                256 * 1024,
            )
            .expect("revision-bound AI source");

        assert_eq!(captured.bytes(), source.as_bytes());
        assert_eq!(captured.source_revision(), preview.source_revision);
        assert!(!reader.calls().is_empty());
        assert!(reader.max_requested.load(Ordering::Acquire) <= READ_BUFFER_BYTES);
        assert_eq!(service.probe_active_worker_count(), 0);
    }

    #[test]
    fn ai_capture_rejects_an_oversized_source_before_any_source_read() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        write(&path, &vec![b'a'; (256 * 1024) + 1]);
        let reader = Arc::new(RecordingReader::default());
        let (service, snapshot_id, instance_id) = service_for_with_reader(&home, reader.clone());

        let error = match service.capture_revision_bound_source(
            &snapshot_id,
            &instance_id,
            "untrusted-frontend-revision",
            256 * 1024,
        ) {
            Ok(_) => panic!("oversized source must fail before reading"),
            Err(error) => error,
        };

        assert_eq!(error.code, "source_too_large_for_ai");
        assert!(reader.calls().is_empty());
    }

    #[test]
    fn ai_capture_reopens_current_source_and_rejects_a_stale_preview_revision() {
        let fixture = tempdir().unwrap();
        let home = fixture.path().join("home");
        let path = home.join(".codex/skills/release/SKILL.md");
        write(
            &path,
            b"---\nname: release\ndescription: before\n---\nbefore\n",
        );
        let (service, snapshot_id, instance_id) = service_for(&home);
        let preview = service
            .prepare_open(OpenSourceRequest {
                snapshot_id: snapshot_id.clone(),
                instance_id: instance_id.clone(),
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap();
        write(
            &path,
            b"---\nname: release\ndescription: after!\n---\nafter!\n",
        );

        let error = match service.capture_revision_bound_source(
            &snapshot_id,
            &instance_id,
            &preview.source_revision,
            256 * 1024,
        ) {
            Ok(_) => panic!("stale preview revision must not be captured"),
            Err(error) => error,
        };

        assert_eq!(error.code, "source_stale");
    }
}
