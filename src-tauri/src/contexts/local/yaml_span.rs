use std::collections::VecDeque;
use std::io::{self, Read, Seek, SeekFrom};

use crate::support::content_digest::ContentDigest;

use super::source_span::SourceSpan;

const STREAM_BUFFER_BYTES: usize = 64 * 1024;
const MAX_DEPTH: usize = 64;
const MAX_KEY_BYTES: usize = 64 * 1024;
const MAX_MAPPING_KEYS: usize = 4096;

pub(super) fn resolve_yaml_path_span(
    mut reader: impl Read + Seek,
    target: &[String],
) -> io::Result<Option<SourceSpan>> {
    let plan = YamlLocatorScanner::new(&mut reader, target).validate_and_plan()?;
    if let ValidationPlan::Alias { name, offset } = plan {
        reader.seek(SeekFrom::Start(0))?;
        return YamlLocatorScanner::new(&mut reader, target).resolve_anchor_mapping(&name, offset);
    }
    Ok(match plan {
        ValidationPlan::Direct(span) => span,
        ValidationPlan::Missing | ValidationPlan::Alias { .. } => None,
    })
}

enum ValidationPlan {
    Missing,
    Direct(Option<SourceSpan>),
    Alias { name: String, offset: u64 },
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum BlockRootKind {
    Mapping,
    Sequence,
    Scalar,
    Flow,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum RootChildState {
    None,
    PendingIndentlessSequence,
    InIndentlessSequence,
}

#[derive(Debug, Clone)]
struct MappingKey {
    segment: Option<String>,
    digest: [u8; 32],
    anchor: Option<String>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum BlockValueKind {
    Scalar,
    Empty,
    BlockProperties,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum SequenceItemKind {
    Scalar,
    Empty,
    BlockProperties,
    CompactMapping,
    NestedSequence,
}

enum BlockStructureFrame {
    Mapping {
        indent: usize,
        seen: Vec<[u8; 32]>,
        last_value: BlockValueKind,
    },
    Sequence {
        indent: usize,
        last_item: SequenceItemKind,
    },
    Scalar {
        indent: usize,
    },
}

impl BlockStructureFrame {
    fn indent(&self) -> usize {
        match self {
            Self::Mapping { indent, .. }
            | Self::Sequence { indent, .. }
            | Self::Scalar { indent } => *indent,
        }
    }
}

#[derive(Default)]
struct BlockMappingTracker {
    frames: Vec<BlockStructureFrame>,
}

impl BlockMappingTracker {
    fn observe_mapping(
        &mut self,
        indent: usize,
        key: &MappingKey,
        value: &LineValue,
    ) -> io::Result<()> {
        self.enter_mapping(indent, key, value, false)
    }

    fn enter_mapping(
        &mut self,
        indent: usize,
        key: &MappingKey,
        value: &LineValue,
        explicit_child: bool,
    ) -> io::Result<()> {
        let existing = self.frames.iter().rposition(|frame| {
            matches!(frame, BlockStructureFrame::Mapping { indent: active, .. } if *active == indent)
        });
        if let Some(index) = existing {
            self.frames.truncate(index + 1);
        } else {
            if !self.frames.is_empty()
                && !explicit_child
                && !self.can_open_child(BlockRootKind::Mapping, indent)
            {
                return Err(invalid_yaml());
            }
            if explicit_child
                && self
                    .frames
                    .last()
                    .is_some_and(|parent| indent <= parent.indent())
            {
                return Err(invalid_yaml());
            }
            self.push_frame(BlockStructureFrame::Mapping {
                indent,
                seen: Vec::new(),
                last_value: BlockValueKind::Scalar,
            })?;
        }
        let Some(BlockStructureFrame::Mapping {
            seen, last_value, ..
        }) = self.frames.last_mut()
        else {
            return Err(invalid_yaml());
        };
        register_mapping_key(seen, key.digest)?;
        *last_value = block_value_kind(value);
        Ok(())
    }

    fn observe_sequence(&mut self, indent: usize, value: &SequenceValue) -> io::Result<()> {
        self.enter_sequence(indent, value, false)
    }

    fn observe_scalar(&mut self, indent: usize) -> io::Result<()> {
        if self.frames.iter().any(|frame| frame.indent() == indent) {
            return Err(invalid_yaml());
        }
        if !self.frames.is_empty() && !self.can_open_child(BlockRootKind::Scalar, indent) {
            return Err(invalid_yaml());
        }
        self.push_frame(BlockStructureFrame::Scalar { indent })
    }

    fn enter_sequence(
        &mut self,
        indent: usize,
        value: &SequenceValue,
        explicit_child: bool,
    ) -> io::Result<()> {
        let existing = self.frames.iter().rposition(|frame| {
            matches!(frame, BlockStructureFrame::Sequence { indent: active, .. } if *active == indent)
        });
        if let Some(index) = existing {
            self.frames.truncate(index + 1);
        } else {
            if !self.frames.is_empty()
                && !explicit_child
                && !self.can_open_child(BlockRootKind::Sequence, indent)
            {
                return Err(invalid_yaml());
            }
            if explicit_child
                && self
                    .frames
                    .last()
                    .is_some_and(|parent| indent <= parent.indent())
            {
                return Err(invalid_yaml());
            }
            self.push_frame(BlockStructureFrame::Sequence {
                indent,
                last_item: SequenceItemKind::Scalar,
            })?;
        }
        let Some(BlockStructureFrame::Sequence { last_item, .. }) = self.frames.last_mut() else {
            return Err(invalid_yaml());
        };
        *last_item = sequence_item_kind(value);
        match value {
            SequenceValue::CompactMapping {
                mapping_indent,
                key,
                value,
                ..
            } => self.enter_mapping(*mapping_indent, key, value, true),
            SequenceValue::NestedSequence {
                sequence_indent,
                value,
                ..
            } => self.enter_sequence(*sequence_indent, value, true),
            _ => Ok(()),
        }
    }

    fn can_open_child(&self, kind: BlockRootKind, indent: usize) -> bool {
        match self.frames.last() {
            Some(BlockStructureFrame::Mapping {
                indent: parent,
                last_value: BlockValueKind::Empty | BlockValueKind::BlockProperties,
                ..
            }) => match kind {
                BlockRootKind::Mapping => indent > *parent,
                BlockRootKind::Sequence => indent >= *parent,
                BlockRootKind::Scalar | BlockRootKind::Flow => indent > *parent,
            },
            Some(BlockStructureFrame::Sequence {
                indent: parent,
                last_item: SequenceItemKind::Empty | SequenceItemKind::BlockProperties,
            }) => indent > *parent,
            Some(BlockStructureFrame::Scalar { .. }) => false,
            _ => false,
        }
    }

    fn push_frame(&mut self, frame: BlockStructureFrame) -> io::Result<()> {
        if self.frames.len() >= MAX_DEPTH {
            return Err(invalid_yaml());
        }
        self.frames.push(frame);
        Ok(())
    }
}

#[derive(Default)]
struct AnchorRegistry {
    digests: Vec<[u8; 32]>,
}

impl AnchorRegistry {
    fn define(&mut self, name: &str) -> io::Result<()> {
        let digest = ContentDigest::sha256(name.as_bytes());
        if self.digests.contains(&digest) {
            return Ok(());
        }
        if self.digests.len() >= MAX_MAPPING_KEYS {
            return Err(invalid_yaml());
        }
        self.digests.push(digest);
        Ok(())
    }

    fn require(&self, name: &str) -> io::Result<()> {
        let digest = ContentDigest::sha256(name.as_bytes());
        self.digests
            .contains(&digest)
            .then_some(())
            .ok_or_else(invalid_yaml)
    }

    fn observe_lead(&mut self, lead: &NodeLead) -> io::Result<()> {
        if let Some(anchor) = lead.anchor.as_deref() {
            self.define(anchor)?;
        }
        if let NodeLeadKind::Alias { name, .. } = &lead.kind {
            self.require(name)?;
        }
        Ok(())
    }

    fn observe_mapping_key(&mut self, key: &MappingKey) -> io::Result<()> {
        if let Some(anchor) = key.anchor.as_deref() {
            self.define(anchor)?;
        }
        Ok(())
    }

    fn observe_sequence_value(&mut self, value: &SequenceValue) -> io::Result<()> {
        match value {
            SequenceValue::Consumed { anchor, .. } => {
                if let Some(anchor) = anchor.as_deref() {
                    self.define(anchor)?;
                }
            }
            SequenceValue::CompactMapping { key, .. } => self.observe_mapping_key(key)?,
            SequenceValue::NestedSequence { value, .. } => {
                self.observe_sequence_value(value)?;
            }
            SequenceValue::Empty { .. }
            | SequenceValue::Inline { .. }
            | SequenceValue::BlockProperties { .. } => {}
        }
        Ok(())
    }
}

fn block_value_kind(value: &LineValue) -> BlockValueKind {
    match value {
        LineValue::Empty { .. } => BlockValueKind::Empty,
        LineValue::BlockProperties => BlockValueKind::BlockProperties,
        LineValue::Inline => BlockValueKind::Scalar,
    }
}

fn sequence_item_kind(value: &SequenceValue) -> SequenceItemKind {
    match value {
        SequenceValue::Empty { .. } => SequenceItemKind::Empty,
        SequenceValue::BlockProperties { .. } => SequenceItemKind::BlockProperties,
        SequenceValue::CompactMapping { .. } => SequenceItemKind::CompactMapping,
        SequenceValue::NestedSequence { .. } => SequenceItemKind::NestedSequence,
        SequenceValue::Inline { .. } | SequenceValue::Consumed { .. } => SequenceItemKind::Scalar,
    }
}

struct YamlLocatorScanner<'a, R> {
    stream: BufferedBytes<R>,
    target: &'a [String],
    pending_head: Option<LineHead>,
    anchors: AnchorRegistry,
}

struct FlowScanResult {
    span: NodeSpan,
    found: Option<SourceSpan>,
    hooks_alias: Option<(String, u64)>,
    anchor_found: bool,
    anchor_candidate: Option<SourceSpan>,
}

impl<'a, R: Read> YamlLocatorScanner<'a, R> {
    fn new(reader: R, target: &'a [String]) -> Self {
        Self {
            stream: BufferedBytes::new(reader),
            target,
            pending_head: None,
            anchors: AnchorRegistry::default(),
        }
    }

    fn validate_and_plan(mut self) -> io::Result<ValidationPlan> {
        let mut root_indent = None;
        let mut root_kind = None;
        let mut block_mappings = BlockMappingTracker::default();
        let mut root_hooks_seen = false;
        let mut root_child_state = RootChildState::None;
        let mut document_started = false;
        let mut document_ended = false;
        let mut plan = ValidationPlan::Missing;
        loop {
            let head = self.next_line_head()?;
            if document_ended && !matches!(&head, LineHead::Blank | LineHead::Eof) {
                return Err(invalid_yaml());
            }
            match head {
                LineHead::Eof => return Ok(plan),
                LineHead::Blank => {}
                LineHead::DocumentStart if root_indent.is_none() && !document_started => {
                    document_started = true;
                }
                LineHead::DocumentEnd if root_indent.is_some() => {
                    document_ended = true;
                }
                LineHead::DocumentStart | LineHead::DocumentEnd => return Err(invalid_yaml()),
                LineHead::FlowRoot {
                    indent,
                    content_start: _,
                } if root_indent.is_none() => {
                    root_indent = Some(indent);
                    root_kind = Some(BlockRootKind::Flow);
                    let mut path = Vec::new();
                    let result = self.scan_flow_plan(&mut path, true, 0)?;
                    self.require_line_remainder()?;
                    plan = match result.hooks_alias {
                        Some((name, offset)) => ValidationPlan::Alias { name, offset },
                        None => ValidationPlan::Direct(result.found),
                    };
                }
                LineHead::Mapping {
                    indent, key, value, ..
                } => {
                    let first_root = root_indent.is_none();
                    let current_root = *root_indent.get_or_insert(indent);
                    if first_root {
                        root_kind = Some(BlockRootKind::Mapping);
                    }
                    if indent < current_root
                        || (indent == current_root && root_kind != Some(BlockRootKind::Mapping))
                    {
                        return Err(invalid_yaml());
                    }
                    block_mappings.observe_mapping(indent, &key, &value)?;
                    if indent > current_root {
                        if root_child_state == RootChildState::PendingIndentlessSequence {
                            root_child_state = RootChildState::None;
                        }
                        self.skip_value(indent, value)?;
                        continue;
                    }
                    root_child_state = if matches!(value, LineValue::Empty { .. }) {
                        RootChildState::PendingIndentlessSequence
                    } else {
                        RootChildState::None
                    };
                    if key.segment.as_deref() != Some("hooks") {
                        self.skip_value(indent, value)?;
                        continue;
                    }
                    if root_hooks_seen {
                        return Err(invalid_yaml());
                    }
                    root_hooks_seen = true;
                    plan = match value {
                        LineValue::Empty { .. } => {
                            ValidationPlan::Direct(self.scan_block_hooks(indent)?)
                        }
                        LineValue::BlockProperties | LineValue::Inline => {
                            let lead = self.scan_node_lead()?;
                            match lead.kind {
                                NodeLeadKind::Block => {
                                    ValidationPlan::Direct(self.scan_block_hooks(indent)?)
                                }
                                NodeLeadKind::Flow if self.stream.peek()? == Some(b'{') => {
                                    let mut path = vec!["hooks".to_string()];
                                    let (_, found) =
                                        self.scan_flow_node(&mut path, true, true, 0)?;
                                    self.require_line_remainder()?;
                                    ValidationPlan::Direct(found)
                                }
                                NodeLeadKind::Alias { name, .. } => {
                                    self.require_line_remainder()?;
                                    ValidationPlan::Alias {
                                        name,
                                        offset: lead.start,
                                    }
                                }
                                _ => {
                                    self.consume_node_lead(lead, indent)?;
                                    ValidationPlan::Direct(None)
                                }
                            }
                        }
                    };
                }
                head => {
                    let Some(indent) = head.indent() else {
                        self.skip_head(head)?;
                        continue;
                    };
                    let first_root = root_indent.is_none();
                    let current_root = *root_indent.get_or_insert(indent);
                    if indent < current_root {
                        return Err(invalid_yaml());
                    }
                    if indent == current_root
                        && root_kind == Some(BlockRootKind::Mapping)
                        && matches!(&head, LineHead::Sequence { .. })
                        && matches!(
                            root_child_state,
                            RootChildState::PendingIndentlessSequence
                                | RootChildState::InIndentlessSequence
                        )
                    {
                        root_child_state = RootChildState::InIndentlessSequence;
                        if let LineHead::Sequence { value, .. } = &head {
                            block_mappings.observe_sequence(indent, value)?;
                        }
                        self.skip_head(head)?;
                        continue;
                    }
                    if indent > current_root
                        && root_child_state == RootChildState::PendingIndentlessSequence
                    {
                        root_child_state = RootChildState::None;
                    }
                    match &head {
                        LineHead::Sequence { value, .. } => {
                            block_mappings.observe_sequence(indent, value)?;
                        }
                        LineHead::Other { .. } => block_mappings.observe_scalar(indent)?,
                        _ => {}
                    }
                    if indent == current_root {
                        let observed = match &head {
                            LineHead::Sequence { .. } => BlockRootKind::Sequence,
                            LineHead::Other { .. } => BlockRootKind::Scalar,
                            LineHead::FlowRoot { .. } => BlockRootKind::Flow,
                            LineHead::Mapping { .. } => BlockRootKind::Mapping,
                            LineHead::Blank
                            | LineHead::Eof
                            | LineHead::DocumentStart
                            | LineHead::DocumentEnd => unreachable!(),
                        };
                        if first_root {
                            root_kind = Some(observed);
                        } else if root_kind != Some(observed) || observed != BlockRootKind::Sequence
                        {
                            return Err(invalid_yaml());
                        }
                    }
                    self.skip_head(head)?;
                }
            }
        }
    }

    fn scan_block_hooks(&mut self, hooks_indent: usize) -> io::Result<Option<SourceSpan>> {
        let event = &self.target[1];
        let sequence_index = self
            .target
            .get(2)
            .map(|value| value.parse::<u64>().map_err(|_| invalid_yaml()))
            .transpose()?;
        let mut child_indent = None;
        let mut block_mappings = BlockMappingTracker::default();
        let mut event_seen = false;
        let mut found = None;
        loop {
            let head = self.next_line_head()?;
            match head {
                LineHead::Eof => return Ok(found),
                LineHead::Blank => continue,
                head @ (LineHead::DocumentStart | LineHead::DocumentEnd) => {
                    self.pending_head = Some(head);
                    return Ok(found);
                }
                LineHead::Mapping {
                    indent,
                    content_start,
                    key,
                    value,
                } => {
                    if indent <= hooks_indent {
                        self.pending_head = Some(LineHead::Mapping {
                            indent,
                            content_start,
                            key,
                            value,
                        });
                        return Ok(found);
                    }
                    let direct_indent = *child_indent.get_or_insert(indent);
                    if indent < direct_indent {
                        return Err(invalid_yaml());
                    }
                    block_mappings.observe_mapping(indent, &key, &value)?;
                    if indent > direct_indent {
                        self.skip_value(indent, value)?;
                        continue;
                    }
                    if key.segment.as_deref() != Some(event.as_str()) {
                        self.skip_value(indent, value)?;
                        continue;
                    }
                    if event_seen {
                        return Err(invalid_yaml());
                    }
                    event_seen = true;
                    found = match value {
                        LineValue::Empty { .. } => {
                            self.resolve_block_event(indent, sequence_index)?
                        }
                        LineValue::BlockProperties | LineValue::Inline => {
                            self.resolve_inline_event(indent, sequence_index)?
                        }
                    };
                }
                LineHead::FlowRoot {
                    indent,
                    content_start,
                } if indent > hooks_indent && child_indent.is_none() => {
                    child_indent = Some(indent);
                    let mut path = vec!["hooks".to_string()];
                    let (_, flow_found) = self.scan_flow_node(&mut path, true, true, 0)?;
                    self.require_line_remainder()?;
                    found = flow_found.filter(|span| span.start >= content_start);
                }
                head => {
                    let Some(indent) = head.indent() else {
                        self.skip_head(head)?;
                        continue;
                    };
                    if indent <= hooks_indent {
                        self.pending_head = Some(head);
                        return Ok(found);
                    }
                    match &head {
                        LineHead::Sequence { value, .. } => {
                            block_mappings.observe_sequence(indent, value)?;
                        }
                        LineHead::Other { .. } => block_mappings.observe_scalar(indent)?,
                        _ => {}
                    }
                    self.skip_head(head)?;
                }
            }
        }
    }

    fn resolve_anchor_mapping(
        &mut self,
        alias: &str,
        alias_offset: u64,
    ) -> io::Result<Option<SourceSpan>> {
        let mut found_anchor = false;
        let mut candidate = None;
        loop {
            let head = self.next_line_head()?;
            if head
                .content_start()
                .is_some_and(|start| start >= alias_offset)
            {
                break;
            }
            match head {
                LineHead::Eof => break,
                LineHead::DocumentStart => {}
                LineHead::DocumentEnd => break,
                LineHead::Mapping {
                    indent, key, value, ..
                } => {
                    if key.anchor.as_deref() == Some(alias) {
                        found_anchor = true;
                        candidate = None;
                    }
                    self.resolve_line_value_for_anchor(
                        indent,
                        value,
                        alias,
                        alias_offset,
                        &mut found_anchor,
                        &mut candidate,
                    )?;
                }
                LineHead::Sequence { indent, value, .. } => {
                    self.resolve_sequence_value_for_anchor(
                        indent,
                        value,
                        alias,
                        alias_offset,
                        &mut found_anchor,
                        &mut candidate,
                    )?;
                }
                LineHead::FlowRoot { .. } => {
                    let mut path = Vec::new();
                    let result =
                        self.scan_flow_for_anchor(&mut path, false, alias, alias_offset, 0)?;
                    self.require_line_remainder()?;
                    if result.anchor_found {
                        found_anchor = true;
                        candidate = result.anchor_candidate;
                    }
                }
                head => self.skip_head(head)?,
            }
        }
        if !found_anchor {
            return Err(invalid_yaml());
        }
        Ok(candidate)
    }

    fn resolve_line_value_for_anchor(
        &mut self,
        indent: usize,
        value: LineValue,
        alias: &str,
        alias_offset: u64,
        found_anchor: &mut bool,
        candidate: &mut Option<SourceSpan>,
    ) -> io::Result<()> {
        if !matches!(value, LineValue::BlockProperties | LineValue::Inline) {
            return Ok(());
        }
        let lead = self.scan_node_lead()?;
        let immediate_match = lead.anchor.as_deref() == Some(alias);
        if matches!(&lead.kind, NodeLeadKind::Flow) {
            let mut path = if immediate_match {
                vec!["hooks".to_string()]
            } else {
                Vec::new()
            };
            let result =
                self.scan_flow_for_anchor(&mut path, immediate_match, alias, alias_offset, 0)?;
            self.require_line_remainder()?;
            if immediate_match {
                *found_anchor = true;
                *candidate = result.found;
            }
            if result.anchor_found {
                *found_anchor = true;
                *candidate = result.anchor_candidate;
            }
        } else if immediate_match {
            *found_anchor = true;
            *candidate = if matches!(&lead.kind, NodeLeadKind::Block) {
                self.scan_block_hooks(indent)?
            } else {
                self.consume_node_lead(lead, indent)?;
                None
            };
        } else {
            self.consume_node_lead(lead, indent)?;
        }
        Ok(())
    }

    fn resolve_sequence_value_for_anchor(
        &mut self,
        indent: usize,
        value: SequenceValue,
        alias: &str,
        alias_offset: u64,
        found_anchor: &mut bool,
        candidate: &mut Option<SourceSpan>,
    ) -> io::Result<()> {
        match value {
            SequenceValue::Empty { .. } => Ok(()),
            SequenceValue::Consumed { anchor, .. } => {
                if anchor.as_deref() == Some(alias) {
                    *found_anchor = true;
                    *candidate = None;
                }
                Ok(())
            }
            SequenceValue::Inline { .. } | SequenceValue::BlockProperties { .. } => self
                .resolve_line_value_for_anchor(
                    indent,
                    LineValue::Inline,
                    alias,
                    alias_offset,
                    found_anchor,
                    candidate,
                ),
            SequenceValue::CompactMapping {
                mapping_indent,
                key,
                value,
                ..
            } => {
                if key.anchor.as_deref() == Some(alias) {
                    *found_anchor = true;
                    *candidate = None;
                }
                self.resolve_line_value_for_anchor(
                    mapping_indent,
                    value,
                    alias,
                    alias_offset,
                    found_anchor,
                    candidate,
                )
            }
            SequenceValue::NestedSequence {
                sequence_indent,
                value,
                ..
            } => self.resolve_sequence_value_for_anchor(
                sequence_indent,
                *value,
                alias,
                alias_offset,
                found_anchor,
                candidate,
            ),
        }
    }

    fn consume_node_lead(&mut self, lead: NodeLead, parent_indent: usize) -> io::Result<u64> {
        match lead.kind {
            NodeLeadKind::Block => Ok(lead.property_end),
            NodeLeadKind::Flow => {
                let mut path = Vec::new();
                let (span, _) = self.scan_flow_node(&mut path, false, false, 0)?;
                self.require_line_remainder()?;
                Ok(span.end)
            }
            NodeLeadKind::Alias { end, .. } => {
                self.require_line_remainder()?;
                Ok(end)
            }
            NodeLeadKind::Other => self.consume_inline_body(parent_indent, lead.start),
        }
    }

    fn consume_inline_value(&mut self, parent_indent: usize) -> io::Result<u64> {
        let lead = self.scan_node_lead()?;
        self.consume_node_lead(lead, parent_indent)
    }

    fn consume_inline_body(&mut self, parent_indent: usize, start: u64) -> io::Result<u64> {
        match self.stream.peek()? {
            Some(b'|' | b'>') => Ok(self.scan_block_scalar(parent_indent, start)?.end),
            Some(b'{' | b'[') => {
                let mut path = Vec::new();
                let (span, _) = self.scan_flow_node(&mut path, false, false, 0)?;
                self.require_line_remainder()?;
                Ok(span.end)
            }
            Some(b'"' | b'\'') => {
                let span = self.scan_quoted(false)?.0;
                self.require_line_remainder()?;
                Ok(span.end)
            }
            Some(_) => {
                let start = self.stream.position();
                let end = self.consume_plain_line()?;
                self.extend_plain_scalar_span(parent_indent, NodeSpan::new(start, end)?)
                    .map(|span| span.end)
            }
            None => Ok(self.stream.position()),
        }
    }

    fn scan_block_scalar(&mut self, parent_indent: usize, start: u64) -> io::Result<NodeSpan> {
        let marker = self.stream.next()?.ok_or_else(invalid_yaml)?;
        if !matches!(marker, b'|' | b'>') {
            return Err(invalid_yaml());
        }
        let mut explicit_indent = None;
        let mut chomping_seen = false;
        loop {
            match self.stream.peek()? {
                Some(b'+' | b'-') if !chomping_seen => {
                    chomping_seen = true;
                    self.stream.next()?;
                }
                Some(digit @ b'1'..=b'9') if explicit_indent.is_none() => {
                    explicit_indent = Some(usize::from(digit - b'0'));
                    self.stream.next()?;
                }
                Some(b' ' | b'\t') => {
                    skip_horizontal_space(&mut self.stream)?;
                    match self.stream.peek()? {
                        None | Some(b'\r' | b'\n' | b'#') => break,
                        _ => return Err(invalid_yaml()),
                    }
                }
                None | Some(b'\r' | b'\n' | b'#') => break,
                _ => return Err(invalid_yaml()),
            }
        }
        let mut end = self.consume_plain_line()?;
        let required_indent = explicit_indent.map(|indent| parent_indent.saturating_add(indent));
        let mut content_indent = required_indent;
        loop {
            match self.next_raw_line_probe()? {
                RawLineProbe::Eof => break,
                RawLineProbe::Blank { end: blank_end } => {
                    end = end.max(blank_end);
                }
                RawLineProbe::Content { indent } => {
                    if indent <= parent_indent {
                        self.stream.unread_spaces(indent)?;
                        break;
                    }
                    let direct_indent = *content_indent.get_or_insert(indent);
                    if indent < direct_indent {
                        return Err(invalid_yaml());
                    }
                    end = consume_raw_physical_line(&mut self.stream)?;
                }
            }
        }
        NodeSpan::new(start, end)
    }

    fn extend_plain_scalar_span(
        &mut self,
        parent_indent: usize,
        mut span: NodeSpan,
    ) -> io::Result<NodeSpan> {
        loop {
            match self.next_raw_line_probe()? {
                RawLineProbe::Eof => break,
                RawLineProbe::Blank { .. } => {}
                RawLineProbe::Content { indent } => {
                    if indent <= parent_indent {
                        self.stream.unread_spaces(indent)?;
                        break;
                    }
                    if self.stream.peek()? == Some(b'#') {
                        consume_comment(&mut self.stream)?;
                        continue;
                    }
                    span.end = consume_plain_continuation_line(&mut self.stream)?;
                }
            }
        }
        Ok(span)
    }

    fn next_raw_line_probe(&mut self) -> io::Result<RawLineProbe> {
        let mut indent = 0_usize;
        while self.stream.peek()? == Some(b' ') {
            self.stream.next()?;
            indent = indent.checked_add(1).ok_or_else(invalid_yaml)?;
            if indent > MAX_KEY_BYTES {
                return Err(invalid_yaml());
            }
        }
        match self.stream.peek()? {
            None => Ok(RawLineProbe::Eof),
            Some(b'\r' | b'\n') => {
                let end = consume_raw_physical_line(&mut self.stream)?;
                Ok(RawLineProbe::Blank { end })
            }
            Some(_) => Ok(RawLineProbe::Content { indent }),
        }
    }

    fn resolve_inline_event(
        &mut self,
        event_indent: usize,
        sequence_index: Option<u64>,
    ) -> io::Result<Option<SourceSpan>> {
        let lead = self.scan_node_lead()?;
        let start = lead.start;
        match lead.kind {
            NodeLeadKind::Block => match sequence_index {
                Some(index) => self.resolve_block_sequence_item(event_indent, index),
                None => {
                    let initial = NodeSpan::new(start, lead.property_end)?;
                    self.extend_block_span(event_indent, initial)
                }
            },
            NodeLeadKind::Flow => {
                let mut path = vec!["hooks".to_string(), self.target[1].clone()];
                let (span, found) = self.scan_flow_node(&mut path, true, true, 0)?;
                self.require_line_remainder()?;
                if sequence_index.is_some() {
                    Ok(found)
                } else {
                    NodeSpan::new(start, span.end).map(NodeSpan::to_source_span)
                }
            }
            NodeLeadKind::Alias { end, .. } => {
                self.require_line_remainder()?;
                if sequence_index.is_some() {
                    Ok(None)
                } else {
                    NodeSpan::new(start, end).map(NodeSpan::to_source_span)
                }
            }
            NodeLeadKind::Other => {
                if sequence_index.is_some() {
                    self.consume_inline_body(event_indent, start)?;
                    return Ok(None);
                }
                if matches!(self.stream.peek()?, Some(b'|' | b'>')) {
                    return self
                        .scan_block_scalar(event_indent, start)
                        .map(NodeSpan::to_source_span);
                }
                if matches!(self.stream.peek()?, Some(b'"' | b'\'')) {
                    let span = self.scan_quoted(false)?.0;
                    self.require_line_remainder()?;
                    return NodeSpan::new(start, span.end).map(NodeSpan::to_source_span);
                }
                let end = self.consume_plain_line()?;
                let initial = NodeSpan::new(start, end)?;
                self.extend_plain_scalar_span(event_indent, initial)
                    .map(NodeSpan::to_source_span)
            }
        }
    }

    fn resolve_block_event(
        &mut self,
        event_indent: usize,
        sequence_index: Option<u64>,
    ) -> io::Result<Option<SourceSpan>> {
        match sequence_index {
            Some(index) => self.resolve_block_sequence_item(event_indent, index),
            None => self.scan_block_node(event_indent),
        }
    }

    fn scan_block_node(&mut self, parent_indent: usize) -> io::Result<Option<SourceSpan>> {
        let mut span: Option<NodeSpan> = None;
        let mut indentless_sequence = false;
        let mut block_mappings = BlockMappingTracker::default();
        loop {
            let head = self.next_line_head()?;
            match head {
                LineHead::Eof => break,
                LineHead::Blank => continue,
                head @ (LineHead::DocumentStart | LineHead::DocumentEnd) => {
                    self.pending_head = Some(head);
                    break;
                }
                head => {
                    let Some(indent) = head.indent() else {
                        self.skip_head(head)?;
                        continue;
                    };
                    let direct_indentless_item =
                        indent == parent_indent && matches!(&head, LineHead::Sequence { .. });
                    if indent < parent_indent
                        || (indent == parent_indent
                            && !(direct_indentless_item && (indentless_sequence || span.is_none())))
                    {
                        self.pending_head = Some(head);
                        break;
                    }
                    match &head {
                        LineHead::Mapping { key, value, .. } => {
                            block_mappings.observe_mapping(indent, key, value)?;
                        }
                        LineHead::Sequence { value, .. } => {
                            block_mappings.observe_sequence(indent, value)?;
                        }
                        LineHead::Other { .. } => block_mappings.observe_scalar(indent)?,
                        _ => {}
                    }
                    indentless_sequence |= direct_indentless_item;
                    let content_start = head.content_start().ok_or_else(invalid_yaml)?;
                    let semantic_end = self.consume_head(head)?;
                    span = Some(match span {
                        Some(current) => NodeSpan::new(current.start, semantic_end)?,
                        None => NodeSpan::new(content_start, semantic_end)?,
                    });
                }
            }
        }
        Ok(span.and_then(NodeSpan::to_source_span))
    }

    fn extend_block_span(
        &mut self,
        parent_indent: usize,
        mut span: NodeSpan,
    ) -> io::Result<Option<SourceSpan>> {
        let mut block_mappings = BlockMappingTracker::default();
        loop {
            let head = self.next_line_head()?;
            match head {
                LineHead::Eof => break,
                LineHead::Blank => continue,
                head @ (LineHead::DocumentStart | LineHead::DocumentEnd) => {
                    self.pending_head = Some(head);
                    break;
                }
                head => {
                    let Some(indent) = head.indent() else {
                        self.skip_head(head)?;
                        continue;
                    };
                    if indent <= parent_indent {
                        self.pending_head = Some(head);
                        break;
                    }
                    match &head {
                        LineHead::Mapping { key, value, .. } => {
                            block_mappings.observe_mapping(indent, key, value)?;
                        }
                        LineHead::Sequence { value, .. } => {
                            block_mappings.observe_sequence(indent, value)?;
                        }
                        LineHead::Other { .. } => block_mappings.observe_scalar(indent)?,
                        _ => {}
                    }
                    span.end = self.consume_head(head)?;
                }
            }
        }
        Ok(span.to_source_span())
    }

    fn resolve_block_sequence_item(
        &mut self,
        parent_indent: usize,
        target_index: u64,
    ) -> io::Result<Option<SourceSpan>> {
        let mut item_indent = None;
        let mut index = 0_u64;
        let mut selected: Option<NodeSpan> = None;
        let mut selecting = false;
        let mut block_mappings = BlockMappingTracker::default();
        loop {
            let head = self.next_line_head()?;
            match head {
                LineHead::Eof => break,
                LineHead::Blank => continue,
                head @ (LineHead::DocumentStart | LineHead::DocumentEnd) => {
                    self.pending_head = Some(head);
                    break;
                }
                LineHead::Sequence {
                    indent,
                    value,
                    content_start,
                } => {
                    if indent < parent_indent {
                        self.pending_head = Some(LineHead::Sequence {
                            indent,
                            content_start,
                            value,
                        });
                        break;
                    }
                    let direct_indent = *item_indent.get_or_insert(indent);
                    if indent < direct_indent {
                        self.pending_head = Some(LineHead::Sequence {
                            indent,
                            content_start,
                            value,
                        });
                        break;
                    }
                    block_mappings.observe_sequence(indent, &value)?;
                    if indent > direct_indent {
                        let end = self.consume_sequence_value(indent, value)?;
                        if let Some(span) = selected.as_mut() {
                            span.end = end;
                        }
                        continue;
                    }
                    if selecting {
                        self.pending_head = Some(LineHead::Sequence {
                            indent,
                            content_start,
                            value,
                        });
                        break;
                    }
                    if index != target_index {
                        self.skip_sequence_value(indent, value)?;
                        index = index.saturating_add(1);
                        continue;
                    }
                    selecting = true;
                    let start = value.source_start();
                    let end = self.consume_sequence_value(indent, value)?;
                    selected =
                        start.and_then(|start| (end > start).then_some(NodeSpan { start, end }));
                    index = index.saturating_add(1);
                }
                head => {
                    let Some(indent) = head.indent() else {
                        self.skip_head(head)?;
                        continue;
                    };
                    if indent <= parent_indent {
                        self.pending_head = Some(head);
                        break;
                    }
                    match &head {
                        LineHead::Mapping { key, value, .. } => {
                            block_mappings.observe_mapping(indent, key, value)?;
                        }
                        LineHead::Sequence { value, .. } => {
                            block_mappings.observe_sequence(indent, value)?;
                        }
                        LineHead::Other { .. } => block_mappings.observe_scalar(indent)?,
                        _ => {}
                    }
                    let Some(direct_indent) = item_indent else {
                        self.skip_head(head)?;
                        return Ok(None);
                    };
                    if indent <= direct_indent {
                        self.pending_head = Some(head);
                        break;
                    }
                    let content_start = head.content_start().ok_or_else(invalid_yaml)?;
                    let end = self.consume_head(head)?;
                    if selecting {
                        if let Some(span) = selected.as_mut() {
                            span.end = end;
                        } else {
                            selected = Some(NodeSpan::new(content_start, end)?);
                        }
                    }
                }
            }
        }
        Ok(selected.and_then(NodeSpan::to_source_span))
    }

    fn scan_flow_node(
        &mut self,
        path: &mut Vec<String>,
        track_path: bool,
        reject_relevant_duplicates: bool,
        depth: usize,
    ) -> io::Result<(NodeSpan, Option<SourceSpan>)> {
        let result = self.scan_flow_node_with_options(
            path,
            track_path,
            reject_relevant_duplicates,
            false,
            None,
            depth,
        )?;
        Ok((result.span, result.found))
    }

    fn scan_flow_plan(
        &mut self,
        path: &mut Vec<String>,
        track_path: bool,
        depth: usize,
    ) -> io::Result<FlowScanResult> {
        self.scan_flow_node_with_options(path, track_path, true, true, None, depth)
    }

    fn scan_flow_for_anchor(
        &mut self,
        path: &mut Vec<String>,
        track_path: bool,
        alias: &str,
        alias_offset: u64,
        depth: usize,
    ) -> io::Result<FlowScanResult> {
        self.scan_flow_node_with_options(
            path,
            track_path,
            true,
            false,
            Some((alias, alias_offset)),
            depth,
        )
    }

    fn scan_flow_node_with_options(
        &mut self,
        path: &mut Vec<String>,
        track_path: bool,
        reject_relevant_duplicates: bool,
        collect_hooks_alias: bool,
        anchor_query: Option<(&str, u64)>,
        depth: usize,
    ) -> io::Result<FlowScanResult> {
        let target = self.target;
        let mut parser = FlowParser {
            stream: &mut self.stream,
            anchors: &mut self.anchors,
            target,
            found: None,
            reject_relevant_duplicates,
            seen_root_hooks: false,
            seen_target_event: false,
            collect_hooks_alias,
            hooks_alias: None,
            anchor_name: anchor_query.map(|(name, _)| name.to_string()),
            anchor_before: anchor_query.map_or(0, |(_, offset)| offset),
            anchor_found: false,
            anchor_candidate: None,
            anchor_candidate_offset: 0,
        };
        let span = parser.scan_node(path, track_path, depth)?;
        Ok(FlowScanResult {
            span,
            found: parser.found,
            hooks_alias: parser.hooks_alias,
            anchor_found: parser.anchor_found,
            anchor_candidate: parser.anchor_candidate,
        })
    }

    fn consume_head(&mut self, head: LineHead) -> io::Result<u64> {
        match head {
            LineHead::Mapping { indent, value, .. } => self.consume_value(indent, value),
            LineHead::Sequence { indent, value, .. } => self.consume_sequence_value(indent, value),
            LineHead::FlowRoot { .. } => {
                let mut path = Vec::new();
                let (span, _) = self.scan_flow_node(&mut path, false, false, 0)?;
                self.require_line_remainder()?;
                Ok(span.end)
            }
            LineHead::Other {
                indent,
                content_start,
                semantic_end,
            } => self
                .extend_plain_scalar_span(indent, NodeSpan::new(content_start, semantic_end)?)
                .map(|span| span.end),
            LineHead::Blank | LineHead::Eof | LineHead::DocumentStart | LineHead::DocumentEnd => {
                Err(invalid_yaml())
            }
        }
    }

    fn consume_value(&mut self, parent_indent: usize, value: LineValue) -> io::Result<u64> {
        match value {
            LineValue::Empty { semantic_end } => Ok(semantic_end),
            LineValue::BlockProperties | LineValue::Inline => {
                self.consume_inline_value(parent_indent)
            }
        }
    }

    fn skip_value(&mut self, parent_indent: usize, value: LineValue) -> io::Result<()> {
        self.consume_value(parent_indent, value).map(|_| ())
    }

    fn consume_sequence_value(
        &mut self,
        sequence_indent: usize,
        value: SequenceValue,
    ) -> io::Result<u64> {
        match value {
            SequenceValue::Empty { semantic_end } => Ok(semantic_end),
            SequenceValue::Consumed {
                start,
                semantic_end,
                ..
            } => self
                .extend_plain_scalar_span(sequence_indent, NodeSpan::new(start, semantic_end)?)
                .map(|span| span.end),
            SequenceValue::Inline { .. } => self.consume_inline_value(sequence_indent),
            SequenceValue::BlockProperties { .. } => self.consume_inline_value(sequence_indent),
            SequenceValue::CompactMapping {
                mapping_indent,
                value,
                ..
            } => self.consume_value(mapping_indent, value),
            SequenceValue::NestedSequence {
                sequence_indent,
                value,
                ..
            } => self.consume_sequence_value(sequence_indent, *value),
        }
    }

    fn skip_sequence_value(
        &mut self,
        sequence_indent: usize,
        value: SequenceValue,
    ) -> io::Result<()> {
        self.consume_sequence_value(sequence_indent, value)
            .map(|_| ())
    }

    fn skip_head(&mut self, head: LineHead) -> io::Result<()> {
        match head {
            LineHead::Mapping { indent, value, .. } => self.skip_value(indent, value),
            LineHead::Sequence { indent, value, .. } => self.skip_sequence_value(indent, value),
            LineHead::FlowRoot { .. } => {
                let mut path = Vec::new();
                self.scan_flow_node(&mut path, false, false, 0)?;
                self.require_line_remainder()
            }
            LineHead::Other {
                indent,
                content_start,
                semantic_end,
            } => self
                .extend_plain_scalar_span(indent, NodeSpan::new(content_start, semantic_end)?)
                .map(|_| ()),
            LineHead::Blank | LineHead::Eof => Ok(()),
            LineHead::DocumentStart | LineHead::DocumentEnd => Err(invalid_yaml()),
        }
    }

    fn require_line_remainder(&mut self) -> io::Result<()> {
        let start = self.stream.position();
        let end = self.consume_plain_line()?;
        if end > start {
            return Err(invalid_yaml());
        }
        Ok(())
    }

    fn scan_quoted(&mut self, capture: bool) -> io::Result<(NodeSpan, Option<Vec<u8>>)> {
        scan_quoted(&mut self.stream, capture)
    }

    fn scan_node_lead(&mut self) -> io::Result<NodeLead> {
        let lead = scan_node_lead(&mut self.stream)?;
        self.anchors.observe_lead(&lead)?;
        Ok(lead)
    }

    fn consume_plain_line(&mut self) -> io::Result<u64> {
        consume_plain_line(&mut self.stream)
    }

    fn next_line_head(&mut self) -> io::Result<LineHead> {
        let head = if let Some(head) = self.pending_head.take() {
            head
        } else {
            next_line_head(&mut self.stream)?
        };
        match &head {
            LineHead::Mapping { key, .. } => self.anchors.observe_mapping_key(key)?,
            LineHead::Sequence { value, .. } => {
                self.anchors.observe_sequence_value(value)?;
            }
            LineHead::Eof
            | LineHead::Blank
            | LineHead::DocumentStart
            | LineHead::DocumentEnd
            | LineHead::FlowRoot { .. }
            | LineHead::Other { .. } => {}
        }
        Ok(head)
    }
}

#[derive(Debug)]
enum LineHead {
    Eof,
    Blank,
    DocumentStart,
    DocumentEnd,
    Mapping {
        indent: usize,
        content_start: u64,
        key: MappingKey,
        value: LineValue,
    },
    Sequence {
        indent: usize,
        content_start: u64,
        value: SequenceValue,
    },
    FlowRoot {
        indent: usize,
        content_start: u64,
    },
    Other {
        indent: usize,
        content_start: u64,
        semantic_end: u64,
    },
}

enum RawLineProbe {
    Eof,
    Blank { end: u64 },
    Content { indent: usize },
}

impl LineHead {
    fn indent(&self) -> Option<usize> {
        match self {
            Self::Mapping { indent, .. }
            | Self::Sequence { indent, .. }
            | Self::FlowRoot { indent, .. }
            | Self::Other { indent, .. } => Some(*indent),
            Self::Eof | Self::Blank | Self::DocumentStart | Self::DocumentEnd => None,
        }
    }

    fn content_start(&self) -> Option<u64> {
        match self {
            Self::Mapping { content_start, .. }
            | Self::Sequence { content_start, .. }
            | Self::FlowRoot { content_start, .. }
            | Self::Other { content_start, .. } => Some(*content_start),
            Self::Eof | Self::Blank | Self::DocumentStart | Self::DocumentEnd => None,
        }
    }
}

#[derive(Debug, Clone, Copy)]
enum LineValue {
    Empty { semantic_end: u64 },
    BlockProperties,
    Inline,
}

#[derive(Debug, Clone)]
enum SequenceValue {
    Empty {
        semantic_end: u64,
    },
    Inline {
        start: u64,
    },
    BlockProperties {
        start: u64,
    },
    Consumed {
        start: u64,
        semantic_end: u64,
        anchor: Option<String>,
    },
    CompactMapping {
        start: u64,
        mapping_indent: usize,
        key: MappingKey,
        value: LineValue,
    },
    NestedSequence {
        start: u64,
        sequence_indent: usize,
        value: Box<SequenceValue>,
    },
}

impl SequenceValue {
    fn source_start(&self) -> Option<u64> {
        match self {
            Self::Empty { .. } => None,
            Self::Inline { start }
            | Self::BlockProperties { start }
            | Self::Consumed { start, .. }
            | Self::CompactMapping { start, .. }
            | Self::NestedSequence { start, .. } => Some(*start),
        }
    }
}

struct NodeLead {
    start: u64,
    property_end: u64,
    anchor: Option<String>,
    kind: NodeLeadKind,
}

enum NodeLeadKind {
    Block,
    Flow,
    Alias { name: String, end: u64 },
    Other,
}

fn scan_node_lead<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<NodeLead> {
    let start = stream.position();
    let mut property_end = start;
    let mut anchor = None;
    let mut saw_property = false;
    let mut saw_anchor = false;
    let mut saw_tag = false;
    loop {
        match stream.peek()? {
            Some(b'&') => {
                if saw_anchor {
                    return Err(invalid_yaml());
                }
                saw_anchor = true;
                saw_property = true;
                let name =
                    scan_yaml_indicator_token(stream, b'&', true)?.ok_or_else(invalid_yaml)?;
                anchor = Some(name);
                property_end = stream.position();
                skip_horizontal_space(stream)?;
            }
            Some(b'!') => {
                if saw_tag {
                    return Err(invalid_yaml());
                }
                saw_tag = true;
                saw_property = true;
                scan_yaml_indicator_token(stream, b'!', false)?;
                property_end = stream.position();
                skip_horizontal_space(stream)?;
            }
            _ => break,
        }
    }
    let kind = match stream.peek()? {
        None | Some(b'\r' | b'\n' | b'#') if saw_property => {
            consume_plain_line(stream)?;
            NodeLeadKind::Block
        }
        Some(b'{' | b'[') => NodeLeadKind::Flow,
        Some(b'*') => {
            if saw_property {
                return Err(invalid_yaml());
            }
            let name = scan_yaml_indicator_token(stream, b'*', true)?.ok_or_else(invalid_yaml)?;
            let end = stream.position();
            NodeLeadKind::Alias { name, end }
        }
        _ => NodeLeadKind::Other,
    };
    Ok(NodeLead {
        start,
        property_end,
        anchor,
        kind,
    })
}

fn scan_yaml_indicator_token<R: Read>(
    stream: &mut BufferedBytes<R>,
    indicator: u8,
    capture: bool,
) -> io::Result<Option<String>> {
    if stream.next()? != Some(indicator) {
        return Err(invalid_yaml());
    }
    let mut token = Vec::new();
    let mut overflow = false;
    if indicator == b'!' && stream.peek()? == Some(b'<') {
        stream.next()?;
        loop {
            let byte = stream.next()?.ok_or_else(invalid_yaml)?;
            if byte == b'>' {
                break;
            }
        }
        return Ok(None);
    }
    while let Some(byte) = stream.peek()? {
        if byte.is_ascii_whitespace() || matches!(byte, b',' | b'[' | b']' | b'{' | b'}' | b'#') {
            break;
        }
        stream.next()?;
        if capture {
            push_bounded(&mut token, byte, &mut overflow);
        }
    }
    if capture {
        if token.is_empty() || overflow {
            return Err(invalid_yaml());
        }
        let value = std::str::from_utf8(&token).map_err(|_| invalid_yaml())?;
        Ok(Some(value.to_string()))
    } else {
        Ok(None)
    }
}

fn next_line_head<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<LineHead> {
    let mut indent = 0_usize;
    while stream.peek()? == Some(b' ') {
        stream.next()?;
        indent = indent.saturating_add(1);
    }
    let content_start = stream.position();
    match stream.peek()? {
        None => return Ok(LineHead::Eof),
        Some(b'\n') => {
            stream.next()?;
            return Ok(LineHead::Blank);
        }
        Some(b'\r') => {
            consume_plain_line(stream)?;
            return Ok(LineHead::Blank);
        }
        Some(b'#') => {
            consume_comment(stream)?;
            return Ok(LineHead::Blank);
        }
        Some(b'{' | b'[') => {
            return Ok(LineHead::FlowRoot {
                indent,
                content_start,
            });
        }
        Some(b'\t') => {
            let semantic_end = consume_plain_line(stream)?;
            return Ok(LineHead::Other {
                indent,
                content_start,
                semantic_end,
            });
        }
        _ => {}
    }

    let mut initial = Vec::new();
    if indent == 0 && stream.peek()? == Some(b'.') {
        for _ in 0..3 {
            if stream.peek()? != Some(b'.') {
                break;
            }
            stream.next()?;
            initial.push(b'.');
        }
        if initial.len() == 3 && is_block_mapping_separator(stream.peek()?) {
            consume_document_marker_remainder(stream)?;
            return Ok(LineHead::DocumentEnd);
        }
    }
    if stream.peek()? == Some(b'-') {
        stream.next()?;
        initial.push(b'-');
        if indent == 0 && stream.peek()? == Some(b'-') {
            stream.next()?;
            initial.push(b'-');
            if stream.peek()? == Some(b'-') {
                stream.next()?;
                initial.push(b'-');
                if is_block_mapping_separator(stream.peek()?) {
                    consume_document_start_remainder(stream)?;
                    return Ok(LineHead::DocumentStart);
                }
            }
        }
        let dash_end = stream.position();
        if initial.len() == 1
            && matches!(
                stream.peek()?,
                None | Some(b' ' | b'\t' | b'\r' | b'\n' | b'#')
            )
        {
            skip_horizontal_space(stream)?;
            let value = sequence_value_after_separator(stream, dash_end, indent, content_start)?;
            return Ok(LineHead::Sequence {
                indent,
                content_start,
                value,
            });
        }
    }
    parse_mapping_head(stream, indent, content_start, initial)
}

fn sequence_value_after_separator<R: Read>(
    stream: &mut BufferedBytes<R>,
    empty_end: u64,
    sequence_indent: usize,
    sequence_start: u64,
) -> io::Result<SequenceValue> {
    sequence_value_after_separator_inner(stream, empty_end, sequence_indent, sequence_start, 0)
}

fn sequence_value_after_separator_inner<R: Read>(
    stream: &mut BufferedBytes<R>,
    empty_end: u64,
    sequence_indent: usize,
    sequence_start: u64,
    depth: usize,
) -> io::Result<SequenceValue> {
    if depth > MAX_DEPTH {
        return Err(invalid_yaml());
    }
    let start = stream.position();
    match stream.peek()? {
        None => Ok(SequenceValue::Empty {
            semantic_end: empty_end,
        }),
        Some(b'#') => {
            consume_comment(stream)?;
            Ok(SequenceValue::Empty {
                semantic_end: empty_end,
            })
        }
        Some(b'\r' | b'\n') => {
            consume_plain_line(stream)?;
            Ok(SequenceValue::Empty {
                semantic_end: empty_end,
            })
        }
        Some(b'{' | b'[' | b'|' | b'>' | b'*') => Ok(SequenceValue::Inline { start }),
        Some(b'-') => {
            stream.next()?;
            if !matches!(
                stream.peek()?,
                None | Some(b' ' | b'\t' | b'\r' | b'\n' | b'#')
            ) {
                return sequence_scalar_or_mapping(
                    stream,
                    start,
                    sequence_indent,
                    sequence_start,
                    SequenceNodeProperties {
                        raw: vec![b'-'],
                        anchor: None,
                    },
                );
            }
            let dash_end = stream.position();
            let nested_indent = sequence_column(sequence_indent, sequence_start, start)?;
            skip_horizontal_space(stream)?;
            let value = sequence_value_after_separator_inner(
                stream,
                dash_end,
                nested_indent,
                start,
                depth + 1,
            )?;
            Ok(SequenceValue::NestedSequence {
                start,
                sequence_indent: nested_indent,
                value: Box::new(value),
            })
        }
        Some(b'?') => {
            stream.next()?;
            if is_block_mapping_separator(stream.peek()?) {
                return Err(invalid_yaml());
            }
            sequence_scalar_or_mapping(
                stream,
                start,
                sequence_indent,
                sequence_start,
                SequenceNodeProperties {
                    raw: vec![b'?'],
                    anchor: None,
                },
            )
        }
        Some(_) => {
            let properties = scan_sequence_node_properties(stream)?;
            if !properties.raw.is_empty() {
                match stream.peek()? {
                    None | Some(b'\r' | b'\n' | b'#') => {
                        stream.unread(&properties.raw)?;
                        return Ok(SequenceValue::BlockProperties { start });
                    }
                    Some(b'{' | b'[' | b'|' | b'>' | b'*') => {
                        stream.unread(&properties.raw)?;
                        return Ok(SequenceValue::Inline { start });
                    }
                    _ => {}
                }
            }
            sequence_scalar_or_mapping(stream, start, sequence_indent, sequence_start, properties)
        }
    }
}

fn sequence_scalar_or_mapping<R: Read>(
    stream: &mut BufferedBytes<R>,
    start: u64,
    sequence_indent: usize,
    sequence_start: u64,
    mut properties: SequenceNodeProperties,
) -> io::Result<SequenceValue> {
    let mapping_indent = sequence_column(sequence_indent, sequence_start, start)?;
    if matches!(stream.peek()?, Some(b'"' | b'\'')) {
        let (span, raw) = scan_quoted(stream, true)?;
        skip_horizontal_space(stream)?;
        if stream.peek()? == Some(b':') {
            let raw = raw.ok_or_else(invalid_yaml)?;
            if raw.contains(&b'\n')
                || properties.raw.len().saturating_add(raw.len()) > MAX_KEY_BYTES
            {
                return Err(invalid_yaml());
            }
            properties.raw.extend_from_slice(&raw);
            stream.next()?;
            if !is_block_mapping_separator(stream.peek()?) {
                return Err(invalid_yaml());
            }
            let colon_end = stream.position();
            let key = decode_yaml_mapping_key(&properties.raw)?;
            skip_horizontal_space(stream)?;
            let value = line_value_after_separator(stream, colon_end)?;
            return Ok(SequenceValue::CompactMapping {
                start,
                mapping_indent,
                key,
                value,
            });
        }
        consume_scalar_remainder(stream)?;
        return Ok(SequenceValue::Consumed {
            start,
            semantic_end: span.end,
            anchor: properties.anchor,
        });
    }
    match parse_mapping_head(stream, mapping_indent, start, properties.raw)? {
        LineHead::Mapping {
            key,
            value,
            content_start,
            ..
        } => Ok(SequenceValue::CompactMapping {
            start: content_start,
            mapping_indent,
            key,
            value,
        }),
        LineHead::Other { semantic_end, .. } => Ok(SequenceValue::Consumed {
            start,
            semantic_end,
            anchor: properties.anchor,
        }),
        _ => Err(invalid_yaml()),
    }
}

fn sequence_column(
    sequence_indent: usize,
    sequence_start: u64,
    value_start: u64,
) -> io::Result<usize> {
    let column_delta =
        usize::try_from(value_start.saturating_sub(sequence_start)).map_err(|_| invalid_yaml())?;
    sequence_indent
        .checked_add(column_delta)
        .ok_or_else(invalid_yaml)
}

fn consume_scalar_remainder<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<()> {
    match stream.peek()? {
        None => Ok(()),
        Some(b'#') => consume_comment(stream),
        Some(b'\r' | b'\n') => consume_plain_line(stream).map(|_| ()),
        Some(_) => Err(invalid_yaml()),
    }
}

struct SequenceNodeProperties {
    raw: Vec<u8>,
    anchor: Option<String>,
}

fn scan_sequence_node_properties<R: Read>(
    stream: &mut BufferedBytes<R>,
) -> io::Result<SequenceNodeProperties> {
    let mut raw = Vec::new();
    let mut overflow = false;
    let mut saw_anchor = false;
    let mut saw_tag = false;
    loop {
        let Some(indicator @ (b'&' | b'!')) = stream.peek()? else {
            break;
        };
        stream.next()?;
        if indicator == b'&' {
            if saw_anchor {
                return Err(invalid_yaml());
            }
            saw_anchor = true;
        } else {
            if saw_tag {
                return Err(invalid_yaml());
            }
            saw_tag = true;
        }
        push_bounded(&mut raw, indicator, &mut overflow);
        if indicator == b'!' && stream.peek()? == Some(b'<') {
            stream.next()?;
            push_bounded(&mut raw, b'<', &mut overflow);
            loop {
                let byte = stream.next()?.ok_or_else(invalid_yaml)?;
                push_bounded(&mut raw, byte, &mut overflow);
                if byte == b'>' {
                    break;
                }
            }
        } else {
            let token_start = raw.len();
            while let Some(byte) = stream.peek()? {
                if byte.is_ascii_whitespace()
                    || matches!(byte, b',' | b'[' | b']' | b'{' | b'}' | b'#')
                {
                    break;
                }
                stream.next()?;
                push_bounded(&mut raw, byte, &mut overflow);
            }
            if indicator == b'&' && raw.len() == token_start {
                return Err(invalid_yaml());
            }
        }
        while matches!(stream.peek()?, Some(b' ' | b'\t')) {
            let byte = stream.next()?.ok_or_else(invalid_yaml)?;
            push_bounded(&mut raw, byte, &mut overflow);
        }
        if overflow {
            return Err(invalid_yaml());
        }
    }
    let anchor = decode_leading_anchor(&raw)?;
    Ok(SequenceNodeProperties { raw, anchor })
}

fn consume_document_marker_remainder<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<()> {
    skip_horizontal_space(stream)?;
    match stream.peek()? {
        None => Ok(()),
        Some(b'#') => consume_comment(stream),
        Some(b'\r' | b'\n') => consume_plain_line(stream).map(|_| ()),
        Some(_) => Err(invalid_yaml()),
    }
}

fn consume_document_start_remainder<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<()> {
    skip_horizontal_space(stream)?;
    match stream.peek()? {
        None => Ok(()),
        Some(b'#') => consume_comment(stream),
        Some(b'\r' | b'\n') => consume_plain_line(stream).map(|_| ()),
        Some(_) => Ok(()),
    }
}

fn parse_mapping_head<R: Read>(
    stream: &mut BufferedBytes<R>,
    indent: usize,
    content_start: u64,
    mut raw_key: Vec<u8>,
) -> io::Result<LineHead> {
    let mut overflow = false;
    let mut quote = None;
    let mut escaped = false;
    let mut last_non_space = raw_key
        .iter()
        .rposition(|byte| !byte.is_ascii_whitespace())
        .map_or(content_start, |index| {
            content_start.saturating_add(index as u64 + 1)
        });
    let mut previous_space = raw_key.last().is_none_or(u8::is_ascii_whitespace);
    loop {
        let Some(byte) = stream.next()? else {
            if quote.is_some() {
                return Err(invalid_yaml());
            }
            return Ok(LineHead::Other {
                indent,
                content_start,
                semantic_end: last_non_space,
            });
        };
        if byte == b'\n' {
            if quote.is_some() {
                return Err(invalid_yaml());
            }
            return Ok(LineHead::Other {
                indent,
                content_start,
                semantic_end: last_non_space,
            });
        }
        if quote.is_none() && byte == b'#' && previous_space {
            consume_comment(stream)?;
            return Ok(LineHead::Other {
                indent,
                content_start,
                semantic_end: last_non_space,
            });
        }
        if !byte.is_ascii_whitespace() {
            last_non_space = stream.position();
        }
        if escaped {
            escaped = false;
            push_bounded(&mut raw_key, byte, &mut overflow);
            continue;
        }
        match (quote, byte) {
            (Some(b'"'), b'\\') => {
                escaped = true;
                push_bounded(&mut raw_key, byte, &mut overflow);
            }
            (Some(active), current) if active == current => {
                quote = None;
                push_bounded(&mut raw_key, byte, &mut overflow);
            }
            (None, b'"' | b'\'') => {
                quote = Some(byte);
                push_bounded(&mut raw_key, byte, &mut overflow);
            }
            (None, b':') if is_block_mapping_separator(stream.peek()?) => {
                let colon_end = stream.position();
                if overflow {
                    return Err(invalid_yaml());
                }
                let key = decode_yaml_mapping_key(&raw_key)?;
                skip_horizontal_space(stream)?;
                let value = line_value_after_separator(stream, colon_end)?;
                return Ok(LineHead::Mapping {
                    indent,
                    content_start,
                    key,
                    value,
                });
            }
            _ => push_bounded(&mut raw_key, byte, &mut overflow),
        }
        previous_space = byte.is_ascii_whitespace();
    }
}

fn line_value_after_separator<R: Read>(
    stream: &mut BufferedBytes<R>,
    empty_end: u64,
) -> io::Result<LineValue> {
    match stream.peek()? {
        None => Ok(LineValue::Empty {
            semantic_end: empty_end,
        }),
        Some(b'#') => {
            consume_comment(stream)?;
            Ok(LineValue::Empty {
                semantic_end: empty_end,
            })
        }
        Some(b'\r' | b'\n') => {
            consume_plain_line(stream)?;
            Ok(LineValue::Empty {
                semantic_end: empty_end,
            })
        }
        Some(b'&' | b'!') => {
            let properties = scan_sequence_node_properties(stream)?;
            let block = matches!(stream.peek()?, None | Some(b'#' | b'\r' | b'\n'));
            stream.unread(&properties.raw)?;
            Ok(if block {
                LineValue::BlockProperties
            } else {
                LineValue::Inline
            })
        }
        Some(_) => Ok(LineValue::Inline),
    }
}

fn is_block_mapping_separator(next: Option<u8>) -> bool {
    matches!(next, None | Some(b' ' | b'\t' | b'\r' | b'\n' | b'#'))
}

fn decode_yaml_mapping_key(raw: &[u8]) -> io::Result<MappingKey> {
    let raw = trim_ascii(raw);
    let anchor = decode_leading_anchor(raw)?;
    let value = if raw.is_empty() {
        serde_yaml::Value::Null
    } else {
        serde_yaml::from_slice::<serde_yaml::Value>(raw).map_err(|_| invalid_yaml())?
    };
    let canonical = serde_yaml::to_string(&value).map_err(|_| invalid_yaml())?;
    Ok(MappingKey {
        segment: value.as_str().map(str::to_string),
        digest: ContentDigest::sha256(canonical.as_bytes()),
        anchor,
    })
}

fn decode_leading_anchor(raw: &[u8]) -> io::Result<Option<String>> {
    let raw = trim_ascii(raw);
    let mut cursor = 0_usize;
    let mut anchor = None;
    let mut tag_seen = false;
    loop {
        while raw.get(cursor).is_some_and(u8::is_ascii_whitespace) {
            cursor += 1;
        }
        match raw.get(cursor).copied() {
            Some(b'&') => {
                if anchor.is_some() {
                    return Err(invalid_yaml());
                }
                cursor += 1;
                let start = cursor;
                while raw.get(cursor).is_some_and(|byte| {
                    !byte.is_ascii_whitespace()
                        && !matches!(byte, b',' | b'[' | b']' | b'{' | b'}' | b'#')
                }) {
                    cursor += 1;
                }
                let token = &raw[start..cursor];
                if token.is_empty() || token.len() > MAX_KEY_BYTES {
                    return Err(invalid_yaml());
                }
                let name = std::str::from_utf8(token).map_err(|_| invalid_yaml())?;
                anchor = Some(name.to_string());
            }
            Some(b'!') => {
                if tag_seen {
                    return Err(invalid_yaml());
                }
                tag_seen = true;
                cursor += 1;
                if raw.get(cursor) == Some(&b'<') {
                    cursor += 1;
                    while raw.get(cursor).copied() != Some(b'>') {
                        cursor = cursor.checked_add(1).ok_or_else(invalid_yaml)?;
                        if cursor >= raw.len() {
                            return Err(invalid_yaml());
                        }
                    }
                    cursor += 1;
                } else {
                    while raw.get(cursor).is_some_and(|byte| {
                        !byte.is_ascii_whitespace()
                            && !matches!(byte, b',' | b'[' | b']' | b'{' | b'}' | b'#')
                    }) {
                        cursor += 1;
                    }
                }
            }
            _ => break,
        }
    }
    Ok(anchor)
}

struct FlowParser<'a, 'b, R> {
    stream: &'a mut BufferedBytes<R>,
    anchors: &'a mut AnchorRegistry,
    target: &'b [String],
    found: Option<SourceSpan>,
    reject_relevant_duplicates: bool,
    seen_root_hooks: bool,
    seen_target_event: bool,
    collect_hooks_alias: bool,
    hooks_alias: Option<(String, u64)>,
    anchor_name: Option<String>,
    anchor_before: u64,
    anchor_found: bool,
    anchor_candidate: Option<SourceSpan>,
    anchor_candidate_offset: u64,
}

impl<R: Read> FlowParser<'_, '_, R> {
    fn scan_node(
        &mut self,
        path: &mut Vec<String>,
        track_path: bool,
        depth: usize,
    ) -> io::Result<NodeSpan> {
        if depth > MAX_DEPTH {
            return Err(invalid_yaml());
        }
        self.skip_space_and_comments()?;
        let lead = scan_node_lead(self.stream)?;
        self.anchors.observe_lead(&lead)?;
        let node_start = lead.start;
        if self.collect_hooks_alias
            && track_path
            && path.len() == 1
            && path.first().map(String::as_str) == Some("hooks")
        {
            if let NodeLeadKind::Alias { name, .. } = &lead.kind {
                self.hooks_alias = Some((name.clone(), node_start));
            }
        }
        let anchor_matches = node_start < self.anchor_before
            && self
                .anchor_name
                .as_deref()
                .is_some_and(|name| lead.anchor.as_deref() == Some(name));
        let saved_path = anchor_matches.then(|| std::mem::take(path));
        let saved_found = anchor_matches.then(|| self.found.take());
        let saved_target_event =
            anchor_matches.then(|| std::mem::replace(&mut self.seen_target_event, false));
        if anchor_matches {
            path.push("hooks".to_string());
        }
        let body_track_path = track_path || anchor_matches;
        let body = match lead.kind {
            NodeLeadKind::Block => return Err(invalid_yaml()),
            NodeLeadKind::Alias { end, .. } => NodeSpan::new(node_start, end)?,
            NodeLeadKind::Flow => match self.stream.peek()? {
                Some(b'{') => self.scan_mapping(path, body_track_path, depth + 1)?,
                Some(b'[') => self.scan_sequence(path, body_track_path, depth + 1)?,
                _ => return Err(invalid_yaml()),
            },
            NodeLeadKind::Other => match self.stream.peek()? {
                Some(b'"' | b'\'') => scan_quoted(self.stream, false)?.0,
                Some(_) => self.scan_plain_scalar()?,
                None => return Err(invalid_yaml()),
            },
        };
        let span = NodeSpan::new(node_start, body.end)?;
        if let Some(saved_path) = saved_path {
            let candidate = self.found.take();
            *path = saved_path;
            self.found = saved_found.flatten();
            self.seen_target_event = saved_target_event.unwrap_or(false);
            self.anchor_found = true;
            if node_start >= self.anchor_candidate_offset {
                self.anchor_candidate_offset = node_start;
                self.anchor_candidate = candidate;
            }
        }
        if track_path && path.as_slice() == self.target {
            self.found = span.to_source_span();
        }
        Ok(span)
    }

    fn scan_mapping(
        &mut self,
        path: &mut Vec<String>,
        track_path: bool,
        depth: usize,
    ) -> io::Result<NodeSpan> {
        if depth > MAX_DEPTH {
            return Err(invalid_yaml());
        }
        let start = self.stream.position();
        self.expect(b'{')?;
        self.skip_space_and_comments()?;
        if self.stream.peek()? == Some(b'}') {
            self.stream.next()?;
            return NodeSpan::new(start, self.stream.position());
        }
        let mut seen_keys = Vec::new();
        loop {
            let key = self.scan_mapping_key(path, depth)?;
            self.skip_space_and_comments()?;
            self.expect(b':')?;
            self.skip_space_and_comments()?;
            if let Some(key) = key {
                register_mapping_key(&mut seen_keys, key.digest)?;
                if self.reject_relevant_duplicates {
                    if path.is_empty() && key.segment.as_deref() == Some("hooks") {
                        if self.seen_root_hooks {
                            return Err(invalid_yaml());
                        }
                        self.seen_root_hooks = true;
                    } else if path.len() == 1
                        && path.first().map(String::as_str) == Some("hooks")
                        && key.segment.as_deref() == Some(self.target[1].as_str())
                    {
                        if self.seen_target_event {
                            return Err(invalid_yaml());
                        }
                        self.seen_target_event = true;
                    }
                }
                if let Some(segment) = key.segment {
                    path.push(segment);
                    let child_track = track_path && self.target.starts_with(path.as_slice());
                    self.scan_node(path, child_track, depth)?;
                    path.pop();
                } else {
                    self.scan_node(path, false, depth)?;
                }
            } else {
                self.scan_node(path, false, depth)?;
            }
            self.skip_space_and_comments()?;
            match self.stream.next()? {
                Some(b',') => {
                    self.skip_space_and_comments()?;
                    if self.stream.peek()? == Some(b'}') {
                        self.stream.next()?;
                        return NodeSpan::new(start, self.stream.position());
                    }
                }
                Some(b'}') => return NodeSpan::new(start, self.stream.position()),
                _ => return Err(invalid_yaml()),
            }
        }
    }

    fn scan_mapping_key(
        &mut self,
        path: &mut Vec<String>,
        depth: usize,
    ) -> io::Result<Option<MappingKey>> {
        if matches!(self.stream.peek()?, Some(b'{' | b'[')) {
            self.scan_node(path, false, depth)?;
            return Ok(None);
        }
        let lead = scan_node_lead(self.stream)?;
        self.anchors.observe_lead(&lead)?;
        if lead.start < self.anchor_before
            && self
                .anchor_name
                .as_deref()
                .is_some_and(|name| lead.anchor.as_deref() == Some(name))
        {
            self.anchor_found = true;
            if lead.start >= self.anchor_candidate_offset {
                self.anchor_candidate_offset = lead.start;
                self.anchor_candidate = None;
            }
        }
        if !matches!(lead.kind, NodeLeadKind::Other) {
            return Err(invalid_yaml());
        }
        match self.stream.peek()? {
            Some(b'"' | b'\'') => {
                let (_, raw) = scan_quoted(self.stream, true)?;
                raw.map(|raw| decode_yaml_mapping_key(&raw)).transpose()
            }
            Some(_) => {
                let mut raw = Vec::new();
                let mut overflow = false;
                loop {
                    match self.stream.peek()? {
                        Some(b':') => {
                            self.stream.next()?;
                            if is_flow_mapping_separator(self.stream.peek()?) {
                                self.stream.unread(b":")?;
                                break;
                            }
                            push_bounded(&mut raw, b':', &mut overflow);
                        }
                        Some(b'\r' | b'\n' | b',') | None => return Err(invalid_yaml()),
                        Some(byte) => {
                            self.stream.next()?;
                            push_bounded(&mut raw, byte, &mut overflow);
                        }
                    }
                }
                if overflow {
                    Err(invalid_yaml())
                } else {
                    decode_yaml_mapping_key(&raw).map(Some)
                }
            }
            None => Err(invalid_yaml()),
        }
    }

    fn scan_sequence(
        &mut self,
        path: &mut Vec<String>,
        track_path: bool,
        depth: usize,
    ) -> io::Result<NodeSpan> {
        if depth > MAX_DEPTH {
            return Err(invalid_yaml());
        }
        let start = self.stream.position();
        self.expect(b'[')?;
        self.skip_space_and_comments()?;
        if self.stream.peek()? == Some(b']') {
            self.stream.next()?;
            return NodeSpan::new(start, self.stream.position());
        }
        let mut index = 0_u64;
        loop {
            if track_path {
                path.push(index.to_string());
                let child_track = self.target.starts_with(path.as_slice());
                self.scan_node(path, child_track, depth)?;
                path.pop();
            } else {
                self.scan_node(path, false, depth)?;
            }
            index = index.saturating_add(1);
            self.skip_space_and_comments()?;
            match self.stream.next()? {
                Some(b',') => {
                    self.skip_space_and_comments()?;
                    if self.stream.peek()? == Some(b']') {
                        self.stream.next()?;
                        return NodeSpan::new(start, self.stream.position());
                    }
                }
                Some(b']') => return NodeSpan::new(start, self.stream.position()),
                _ => return Err(invalid_yaml()),
            }
        }
    }

    fn scan_plain_scalar(&mut self) -> io::Result<NodeSpan> {
        let start = self.stream.position();
        let mut end = start;
        let mut previous_space = false;
        loop {
            match self.stream.peek()? {
                None | Some(b',' | b']' | b'}') => break,
                Some(b':') => {
                    self.stream.next()?;
                    if is_flow_mapping_separator(self.stream.peek()?) {
                        return Err(invalid_yaml());
                    }
                    end = self.stream.position();
                    previous_space = false;
                }
                Some(b'#') if previous_space => {
                    consume_comment(self.stream)?;
                    self.skip_space_and_comments()?;
                    break;
                }
                Some(byte) => {
                    self.stream.next()?;
                    if !byte.is_ascii_whitespace() {
                        end = self.stream.position();
                    }
                    previous_space = byte.is_ascii_whitespace();
                }
            }
        }
        NodeSpan::new(start, end)
    }

    fn skip_space_and_comments(&mut self) -> io::Result<()> {
        loop {
            while matches!(self.stream.peek()?, Some(b' ' | b'\t' | b'\r' | b'\n')) {
                self.stream.next()?;
            }
            if self.stream.peek()? != Some(b'#') {
                return Ok(());
            }
            consume_comment(self.stream)?;
        }
    }

    fn expect(&mut self, expected: u8) -> io::Result<()> {
        match self.stream.next()? {
            Some(actual) if actual == expected => Ok(()),
            _ => Err(invalid_yaml()),
        }
    }
}

fn is_flow_mapping_separator(next: Option<u8>) -> bool {
    matches!(
        next,
        None | Some(b' ' | b'\t' | b'\r' | b'\n' | b',' | b'[' | b']' | b'{' | b'}')
    )
}

fn scan_quoted<R: Read>(
    stream: &mut BufferedBytes<R>,
    capture: bool,
) -> io::Result<(NodeSpan, Option<Vec<u8>>)> {
    let start = stream.position();
    let quote = stream.next()?.ok_or_else(invalid_yaml)?;
    if !matches!(quote, b'"' | b'\'') {
        return Err(invalid_yaml());
    }
    let mut raw = capture.then(Vec::new);
    let mut overflow = false;
    if let Some(raw) = raw.as_mut() {
        push_bounded(raw, quote, &mut overflow);
    }
    loop {
        let byte = stream.next()?.ok_or_else(invalid_yaml)?;
        if let Some(raw) = raw.as_mut() {
            push_bounded(raw, byte, &mut overflow);
        }
        if quote == b'"' && byte == b'\\' {
            let escaped = stream.next()?.ok_or_else(invalid_yaml)?;
            if let Some(raw) = raw.as_mut() {
                push_bounded(raw, escaped, &mut overflow);
            }
            let hex_digits = match escaped {
                b'x' => 2,
                b'u' => 4,
                b'U' => 8,
                b'0' | b'a' | b'b' | b't' | b'n' | b'v' | b'f' | b'r' | b'e' | b' ' | b'"'
                | b'/' | b'\\' | b'N' | b'_' | b'L' | b'P' | b'\n' => 0,
                b'\r' => {
                    if stream.peek()? == Some(b'\n') {
                        let newline = stream.next()?.ok_or_else(invalid_yaml)?;
                        if let Some(raw) = raw.as_mut() {
                            push_bounded(raw, newline, &mut overflow);
                        }
                    }
                    0
                }
                _ => return Err(invalid_yaml()),
            };
            for _ in 0..hex_digits {
                let digit = stream.next()?.ok_or_else(invalid_yaml)?;
                if !digit.is_ascii_hexdigit() {
                    return Err(invalid_yaml());
                }
                if let Some(raw) = raw.as_mut() {
                    push_bounded(raw, digit, &mut overflow);
                }
            }
            continue;
        }
        if quote == b'\'' && byte == b'\'' && stream.peek()? == Some(b'\'') {
            let escaped = stream.next()?.ok_or_else(invalid_yaml)?;
            if let Some(raw) = raw.as_mut() {
                push_bounded(raw, escaped, &mut overflow);
            }
            continue;
        }
        if byte == quote {
            let span = NodeSpan::new(start, stream.position())?;
            return Ok((span, (!overflow).then_some(raw).flatten()));
        }
    }
}

fn consume_plain_line<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<u64> {
    let mut end = stream.position();
    let mut previous_space = true;
    loop {
        match stream.peek()? {
            None => return Ok(end),
            Some(b'\n') => {
                stream.next()?;
                return Ok(end);
            }
            Some(b'#') if previous_space => {
                consume_comment(stream)?;
                return Ok(end);
            }
            Some(byte) => {
                stream.next()?;
                if !byte.is_ascii_whitespace() {
                    end = stream.position();
                }
                previous_space = byte.is_ascii_whitespace();
            }
        }
    }
}

fn consume_raw_physical_line<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<u64> {
    let mut end = stream.position();
    while let Some(byte) = stream.next()? {
        if byte == b'\n' {
            break;
        }
        if byte != b'\r' {
            end = stream.position();
        }
    }
    Ok(end)
}

fn consume_plain_continuation_line<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<u64> {
    if stream.peek()? == Some(b'\t') {
        return Err(invalid_yaml());
    }
    let mut end = stream.position();
    let mut previous_space = true;
    loop {
        match stream.peek()? {
            None => return Ok(end),
            Some(b'\n') => {
                stream.next()?;
                return Ok(end);
            }
            Some(b'#') if previous_space => {
                consume_comment(stream)?;
                return Ok(end);
            }
            Some(b':') => {
                stream.next()?;
                if is_block_mapping_separator(stream.peek()?) {
                    return Err(invalid_yaml());
                }
                end = stream.position();
                previous_space = false;
            }
            Some(byte) => {
                stream.next()?;
                if !byte.is_ascii_whitespace() {
                    end = stream.position();
                }
                previous_space = byte.is_ascii_whitespace();
            }
        }
    }
}

fn consume_comment<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<()> {
    while let Some(byte) = stream.next()? {
        if byte == b'\n' {
            break;
        }
    }
    Ok(())
}

fn skip_horizontal_space<R: Read>(stream: &mut BufferedBytes<R>) -> io::Result<()> {
    while matches!(stream.peek()?, Some(b' ' | b'\t')) {
        stream.next()?;
    }
    Ok(())
}

fn push_bounded(output: &mut Vec<u8>, byte: u8, overflow: &mut bool) {
    if output.len() < MAX_KEY_BYTES {
        output.push(byte);
    } else {
        *overflow = true;
    }
}

fn register_mapping_key(seen: &mut Vec<[u8; 32]>, digest: [u8; 32]) -> io::Result<()> {
    if seen.contains(&digest) || seen.len() >= MAX_MAPPING_KEYS {
        return Err(invalid_yaml());
    }
    seen.push(digest);
    Ok(())
}

fn trim_ascii(mut bytes: &[u8]) -> &[u8] {
    while bytes.first().is_some_and(u8::is_ascii_whitespace) {
        bytes = &bytes[1..];
    }
    while bytes.last().is_some_and(u8::is_ascii_whitespace) {
        bytes = &bytes[..bytes.len() - 1];
    }
    bytes
}

#[derive(Clone, Copy)]
struct NodeSpan {
    start: u64,
    end: u64,
}

impl NodeSpan {
    fn new(start: u64, end: u64) -> io::Result<Self> {
        if end < start {
            return Err(invalid_yaml());
        }
        Ok(Self { start, end })
    }

    fn to_source_span(self) -> Option<SourceSpan> {
        (self.end > self.start).then_some(SourceSpan {
            start: self.start,
            length: self.end - self.start,
        })
    }
}

struct BufferedBytes<R> {
    reader: R,
    buffer: Box<[u8; STREAM_BUFFER_BYTES]>,
    replay: VecDeque<u8>,
    cursor: usize,
    length: usize,
    position: u64,
    eof: bool,
}

impl<R: Read> BufferedBytes<R> {
    fn new(reader: R) -> Self {
        Self {
            reader,
            buffer: Box::new([0; STREAM_BUFFER_BYTES]),
            replay: VecDeque::new(),
            cursor: 0,
            length: 0,
            position: 0,
            eof: false,
        }
    }

    fn position(&self) -> u64 {
        self.position
    }

    fn peek(&mut self) -> io::Result<Option<u8>> {
        if let Some(byte) = self.replay.front() {
            return Ok(Some(*byte));
        }
        if self.cursor == self.length && !self.eof {
            self.fill()?;
        }
        Ok((self.cursor < self.length).then(|| self.buffer[self.cursor]))
    }

    fn next(&mut self) -> io::Result<Option<u8>> {
        if let Some(byte) = self.replay.pop_front() {
            self.position = self.position.saturating_add(1);
            return Ok(Some(byte));
        }
        if self.cursor == self.length && !self.eof {
            self.fill()?;
        }
        let byte = (self.cursor < self.length).then(|| self.buffer[self.cursor]);
        if byte.is_some() {
            self.cursor += 1;
            self.position = self.position.saturating_add(1);
        }
        Ok(byte)
    }

    fn unread(&mut self, bytes: &[u8]) -> io::Result<()> {
        if bytes.len().saturating_add(self.replay.len()) > MAX_KEY_BYTES
            || self.position < bytes.len() as u64
        {
            return Err(invalid_yaml());
        }
        for byte in bytes.iter().rev() {
            self.replay.push_front(*byte);
        }
        self.position -= bytes.len() as u64;
        Ok(())
    }

    fn unread_spaces(&mut self, count: usize) -> io::Result<()> {
        if count.saturating_add(self.replay.len()) > MAX_KEY_BYTES || self.position < count as u64 {
            return Err(invalid_yaml());
        }
        for _ in 0..count {
            self.replay.push_front(b' ');
        }
        self.position -= count as u64;
        Ok(())
    }

    fn fill(&mut self) -> io::Result<()> {
        self.cursor = 0;
        loop {
            match self.reader.read(self.buffer.as_mut_slice()) {
                Ok(read) => {
                    self.length = read;
                    self.eof = read == 0;
                    return Ok(());
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => return Err(error),
            }
        }
    }
}

fn invalid_yaml() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, "malformed YAML source")
}
