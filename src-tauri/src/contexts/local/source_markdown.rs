//! Pure semantic Markdown projection boundary for Local source previews.
//!
//! This module owns Markdown checkpointing and the inert semantic fragment DTO.
//! File access, source revisions, verified paths, and preview sessions remain in
//! the inspection boundary and call this boundary in one direction.

use std::fmt;

pub(crate) const MAX_MARKDOWN_FRAGMENTS: usize = 512;
pub(crate) const MAX_MARKDOWN_NESTING: usize = 64;
const MAX_MARKDOWN_SEMANTIC_NODES: usize = 2_048;
pub(crate) const MARKDOWN_LOOKBEHIND_BYTES: u64 = 33;
pub(crate) const MARKDOWN_BOUNDARY_LOOKAHEAD_BYTES: usize = (4 * 1024) - 3;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum MarkdownBlockKind {
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

#[derive(Clone, PartialEq, Eq)]
pub(crate) enum MarkdownSemanticNode {
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

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct MarkdownBlockFragment {
    pub(crate) block_id: String,
    pub(crate) kind: MarkdownBlockKind,
    pub(crate) text: String,
    pub(crate) nodes: Vec<MarkdownSemanticNode>,
    pub(crate) starts_block: bool,
    pub(crate) ends_block: bool,
}

impl fmt::Debug for MarkdownBlockFragment {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("MarkdownBlockFragment")
            .field("block_id", &self.block_id)
            .field("kind", &self.kind)
            .field("text", &"<redacted>")
            .field("semantic_node_count", &self.nodes.len())
            .field("starts_block", &self.starts_block)
            .field("ends_block", &self.ends_block)
            .finish()
    }
}

#[derive(Clone, Copy)]
struct MarkdownAutolinkProbe {
    len: u8,
    scheme_valid: bool,
    overflowed: bool,
}

enum AutolinkAdvance {
    Matched(MarkdownAutolinkProbe),
    UriComplete,
    EmailComplete,
    Mismatch,
}

impl MarkdownAutolinkProbe {
    fn new() -> Self {
        Self {
            len: 0,
            scheme_valid: true,
            overflowed: false,
        }
    }

    fn advance(mut self, byte: u8) -> AutolinkAdvance {
        let len = usize::from(self.len);
        if byte == b':' && self.scheme_valid && !self.overflowed && (2..=32).contains(&len) {
            return AutolinkAdvance::UriComplete;
        }
        if byte == b'@' && len > 0 {
            return AutolinkAdvance::EmailComplete;
        }
        let scheme_byte_valid = if len == 0 {
            byte.is_ascii_alphabetic()
        } else {
            byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'.' | b'-')
        };
        let email_local_byte_valid = byte.is_ascii_alphanumeric()
            || matches!(
                byte,
                b'!' | b'#'
                    | b'$'
                    | b'%'
                    | b'&'
                    | b'\''
                    | b'*'
                    | b'+'
                    | b'-'
                    | b'/'
                    | b'='
                    | b'?'
                    | b'^'
                    | b'_'
                    | b'`'
                    | b'{'
                    | b'|'
                    | b'}'
                    | b'~'
                    | b'.'
            );
        if !email_local_byte_valid {
            return AutolinkAdvance::Mismatch;
        }
        self.scheme_valid &= scheme_byte_valid;
        if self.len == u8::MAX {
            self.overflowed = true;
        } else {
            self.len += 1;
        }
        AutolinkAdvance::Matched(self)
    }
}

#[derive(Clone, Copy)]
enum MarkdownDestinationState {
    Text,
    AfterLabel,
    Destination {
        depth: u32,
        escaped: bool,
        excessive: bool,
    },
    AutolinkProbe(MarkdownAutolinkProbe),
    AutolinkDestination,
}

impl Default for MarkdownDestinationState {
    fn default() -> Self {
        Self::Text
    }
}

impl MarkdownDestinationState {
    fn text_after(byte: u8) -> Self {
        match byte {
            b']' => Self::AfterLabel,
            b'<' => Self::AutolinkProbe(MarkdownAutolinkProbe::new()),
            _ => Self::Text,
        }
    }

    fn advance(self, byte: u8) -> Self {
        match self {
            Self::Text => Self::text_after(byte),
            Self::AfterLabel if byte == b'(' => Self::Destination {
                depth: 1,
                escaped: false,
                excessive: false,
            },
            Self::AfterLabel if byte == b']' => Self::AfterLabel,
            Self::AfterLabel => Self::text_after(byte),
            Self::Destination { .. } if byte == b'\n' => Self::Text,
            Self::Destination {
                depth,
                escaped: true,
                excessive,
            } => Self::Destination {
                depth,
                escaped: false,
                excessive,
            },
            Self::Destination {
                depth,
                escaped: false,
                excessive,
            } if byte == b'\\' => Self::Destination {
                depth,
                escaped: true,
                excessive,
            },
            Self::Destination {
                depth,
                escaped: false,
                excessive,
            } if byte == b'(' => Self::Destination {
                depth: depth.saturating_add(1),
                escaped: false,
                excessive: excessive || depth >= MAX_MARKDOWN_NESTING as u32,
            },
            Self::Destination {
                depth: 1,
                escaped: false,
                ..
            } if byte == b')' => Self::Text,
            Self::Destination {
                depth,
                escaped: false,
                excessive,
            } if byte == b')' => Self::Destination {
                depth: depth.saturating_sub(1),
                escaped: false,
                excessive,
            },
            state @ Self::Destination { .. } => state,
            Self::AutolinkProbe(probe) => match probe.advance(byte) {
                AutolinkAdvance::Matched(next) => Self::AutolinkProbe(next),
                AutolinkAdvance::UriComplete | AutolinkAdvance::EmailComplete => {
                    Self::AutolinkDestination
                }
                AutolinkAdvance::Mismatch => Self::text_after(byte),
            },
            Self::AutolinkDestination if byte == b'>' => Self::Text,
            Self::AutolinkDestination if byte == b'\n' => Self::Text,
            Self::AutolinkDestination => Self::AutolinkDestination,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum MarkdownReferenceDefinitionState {
    LineStart,
    Label { depth: u32, escaped: bool },
    AfterLabel,
    Active,
    Rejected,
}

impl Default for MarkdownReferenceDefinitionState {
    fn default() -> Self {
        Self::LineStart
    }
}

impl MarkdownReferenceDefinitionState {
    fn advance(self, byte: u8) -> Self {
        if byte == b'\n' {
            return Self::LineStart;
        }
        match self {
            Self::LineStart if matches!(byte, b' ' | b'\t' | b'\r') => Self::LineStart,
            Self::LineStart if byte == b'[' => Self::Label {
                depth: 1,
                escaped: false,
            },
            Self::LineStart => Self::Rejected,
            Self::Label {
                depth,
                escaped: true,
            } => Self::Label {
                depth,
                escaped: false,
            },
            Self::Label {
                depth,
                escaped: false,
            } if byte == b'\\' => Self::Label {
                depth,
                escaped: true,
            },
            Self::Label {
                depth,
                escaped: false,
            } if byte == b'[' => Self::Label {
                depth: depth.saturating_add(1),
                escaped: false,
            },
            Self::Label {
                depth: 1,
                escaped: false,
            } if byte == b']' => Self::AfterLabel,
            Self::Label {
                depth,
                escaped: false,
            } if byte == b']' => Self::Label {
                depth: depth.saturating_sub(1),
                escaped: false,
            },
            state @ Self::Label { .. } => state,
            Self::AfterLabel if byte == b':' => Self::Active,
            Self::AfterLabel => Self::Rejected,
            Self::Active => Self::Active,
            Self::Rejected => Self::Rejected,
        }
    }
}

#[derive(Clone, Copy)]
pub(crate) struct MarkdownCheckpoint {
    offset: u64,
    line_start: u64,
    block_start: u64,
    block_kind: MarkdownBlockKind,
    block_open: bool,
    in_fence: bool,
    in_frontmatter: bool,
    at_line_start: bool,
    marker: u8,
    marker_count: u8,
    ordered_list_candidate: bool,
    ordered_list_confirmed: bool,
    ordered_list_digits: u8,
    ordered_list_marker_seen: bool,
    ordered_list_inherited_paragraph_start: Option<u64>,
    inline_code_delimiter: u32,
    inline_backtick_run: u32,
    destination_state: MarkdownDestinationState,
    destination_start: Option<u64>,
    reference_definition_state: MarkdownReferenceDefinitionState,
}

impl Default for MarkdownCheckpoint {
    fn default() -> Self {
        Self {
            offset: 0,
            line_start: 0,
            block_start: 0,
            block_kind: MarkdownBlockKind::LiteralText,
            block_open: false,
            in_fence: false,
            in_frontmatter: false,
            at_line_start: true,
            marker: 0,
            marker_count: 0,
            ordered_list_candidate: false,
            ordered_list_confirmed: false,
            ordered_list_digits: 0,
            ordered_list_marker_seen: false,
            ordered_list_inherited_paragraph_start: None,
            inline_code_delimiter: 0,
            inline_backtick_run: 0,
            destination_state: MarkdownDestinationState::Text,
            destination_start: None,
            reference_definition_state: MarkdownReferenceDefinitionState::LineStart,
        }
    }
}

#[derive(Clone, Copy, Default)]
pub(crate) struct MarkdownScanState {
    pub(crate) checkpoint: MarkdownCheckpoint,
}

impl MarkdownScanState {
    pub(crate) fn feed(&mut self, bytes: &[u8]) {
        for &byte in bytes {
            self.checkpoint.reference_definition_state =
                if (self.checkpoint.in_fence || self.checkpoint.in_frontmatter) && byte != b'\n' {
                    MarkdownReferenceDefinitionState::Rejected
                } else {
                    self.checkpoint.reference_definition_state.advance(byte)
                };
            let inline_literal = self.advance_inline_code(byte);
            if self.checkpoint.in_fence || self.checkpoint.in_frontmatter || inline_literal {
                self.checkpoint.destination_state = MarkdownDestinationState::Text;
                self.checkpoint.destination_start = None;
            } else {
                let previous = self.checkpoint.destination_state;
                let next = previous.advance(byte);
                let was_active = matches!(
                    previous,
                    MarkdownDestinationState::Destination { .. }
                        | MarkdownDestinationState::AutolinkProbe(_)
                        | MarkdownDestinationState::AutolinkDestination
                );
                let is_active = matches!(
                    next,
                    MarkdownDestinationState::Destination { .. }
                        | MarkdownDestinationState::AutolinkProbe(_)
                        | MarkdownDestinationState::AutolinkDestination
                );
                if !was_active && is_active {
                    self.checkpoint.destination_start = Some(self.checkpoint.offset);
                } else if was_active && !is_active {
                    self.checkpoint.destination_start = None;
                }
                self.checkpoint.destination_state = next;
            }
            if self.checkpoint.at_line_start {
                if matches!(byte, b' ' | b'\t' | b'\r') {
                    self.checkpoint.offset = self.checkpoint.offset.saturating_add(1);
                    continue;
                }
                if byte == b'\n' {
                    self.checkpoint.block_start = self.checkpoint.line_start;
                    self.checkpoint.block_kind = MarkdownBlockKind::LiteralText;
                    self.checkpoint.block_open = true;
                    self.checkpoint.offset = self.checkpoint.offset.saturating_add(1);
                    self.finish_line();
                    continue;
                }
                self.checkpoint.at_line_start = false;
                if byte.is_ascii_digit() {
                    self.checkpoint.ordered_list_candidate = true;
                    self.checkpoint.ordered_list_digits = 1;
                    self.checkpoint.ordered_list_inherited_paragraph_start =
                        (self.checkpoint.block_open
                            && self.checkpoint.block_kind == MarkdownBlockKind::Paragraph)
                            .then_some(self.checkpoint.block_start);
                }
                if self.checkpoint.in_frontmatter {
                    self.checkpoint.block_kind = MarkdownBlockKind::LiteralText;
                    self.checkpoint.block_open = true;
                } else if !self.checkpoint.in_fence {
                    let next_kind = initial_markdown_kind(byte);
                    if !self.checkpoint.block_open
                        || !markdown_block_continues(self.checkpoint.block_kind, next_kind)
                    {
                        self.checkpoint.block_start = self.checkpoint.line_start;
                        self.checkpoint.block_kind = next_kind;
                    }
                    self.checkpoint.block_open = true;
                } else {
                    self.checkpoint.block_kind = MarkdownBlockKind::FencedCode;
                    self.checkpoint.block_open = true;
                }
                if matches!(byte, b'`' | b'~' | b'-' | b'*' | b'_') {
                    self.checkpoint.marker = byte;
                    self.checkpoint.marker_count = 1;
                }
            } else {
                if self.checkpoint.ordered_list_candidate && !self.checkpoint.ordered_list_confirmed
                {
                    if !self.checkpoint.ordered_list_marker_seen && byte.is_ascii_digit() {
                        self.checkpoint.ordered_list_digits =
                            self.checkpoint.ordered_list_digits.saturating_add(1);
                        if self.checkpoint.ordered_list_digits > 9 {
                            self.invalidate_ordered_list_candidate();
                        }
                    } else if !self.checkpoint.ordered_list_marker_seen
                        && matches!(byte, b'.' | b')')
                    {
                        self.checkpoint.ordered_list_marker_seen = true;
                    } else if self.checkpoint.ordered_list_marker_seen
                        && matches!(byte, b' ' | b'\t')
                    {
                        self.checkpoint.ordered_list_confirmed = true;
                        self.checkpoint.block_kind = MarkdownBlockKind::List;
                    } else {
                        self.invalidate_ordered_list_candidate();
                    }
                }
                if self.checkpoint.marker == 0 {
                    // No unordered/fence marker probe is active.
                } else if byte == self.checkpoint.marker {
                    self.checkpoint.marker_count = self.checkpoint.marker_count.saturating_add(1);
                    if self.checkpoint.marker_count >= 3 {
                        self.checkpoint.block_kind = match self.checkpoint.marker {
                            b'`' | b'~' => MarkdownBlockKind::FencedCode,
                            b'-' | b'*' | b'_' if !self.checkpoint.in_fence => {
                                MarkdownBlockKind::ThematicBreak
                            }
                            _ => self.checkpoint.block_kind,
                        };
                    }
                } else if matches!(self.checkpoint.marker, b'`' | b'~')
                    && self.checkpoint.marker_count >= 3
                {
                    // Keep a completed fence marker until the newline so an
                    // opening fence with an info string still toggles state.
                } else if !matches!(byte, b' ' | b'\t' | b'\r' | b'\n') {
                    self.checkpoint.marker = 0;
                    self.checkpoint.marker_count = 0;
                }
            }
            self.checkpoint.offset = self.checkpoint.offset.saturating_add(1);
            if byte == b'\n' {
                self.finish_line();
            }
        }
    }

    fn finish_line(&mut self) {
        if self.checkpoint.ordered_list_candidate && !self.checkpoint.ordered_list_confirmed {
            self.invalidate_ordered_list_candidate();
        }
        let fence_line =
            matches!(self.checkpoint.marker, b'`' | b'~') && self.checkpoint.marker_count >= 3;
        let frontmatter_line = self.checkpoint.marker == b'-'
            && self.checkpoint.marker_count == 3
            && (self.checkpoint.line_start == 0 || self.checkpoint.in_frontmatter);
        if frontmatter_line {
            self.checkpoint.in_frontmatter = !self.checkpoint.in_frontmatter;
            self.checkpoint.block_open = false;
            self.checkpoint.inline_code_delimiter = 0;
            self.checkpoint.inline_backtick_run = 0;
        } else if fence_line {
            self.checkpoint.in_fence = !self.checkpoint.in_fence;
            self.checkpoint.block_open = self.checkpoint.in_fence;
            self.checkpoint.inline_code_delimiter = 0;
            self.checkpoint.inline_backtick_run = 0;
        } else if !self.checkpoint.in_fence {
            self.checkpoint.block_open = matches!(
                self.checkpoint.block_kind,
                MarkdownBlockKind::Paragraph
                    | MarkdownBlockKind::List
                    | MarkdownBlockKind::Table
                    | MarkdownBlockKind::Blockquote
            );
        }
        if matches!(
            self.checkpoint.block_kind,
            MarkdownBlockKind::FencedCode
                | MarkdownBlockKind::InlineCode
                | MarkdownBlockKind::LiteralText
        ) {
            self.checkpoint.destination_state = MarkdownDestinationState::Text;
            self.checkpoint.destination_start = None;
        }
        self.checkpoint.line_start = self.checkpoint.offset;
        self.checkpoint.at_line_start = true;
        self.checkpoint.marker = 0;
        self.checkpoint.marker_count = 0;
        self.checkpoint.ordered_list_candidate = false;
        self.checkpoint.ordered_list_confirmed = false;
        self.checkpoint.ordered_list_digits = 0;
        self.checkpoint.ordered_list_marker_seen = false;
        self.checkpoint.ordered_list_inherited_paragraph_start = None;
        self.checkpoint.reference_definition_state = MarkdownReferenceDefinitionState::LineStart;
    }

    fn invalidate_ordered_list_candidate(&mut self) {
        self.checkpoint.ordered_list_candidate = false;
        self.checkpoint.block_kind = MarkdownBlockKind::Paragraph;
        self.checkpoint.block_start = self
            .checkpoint
            .ordered_list_inherited_paragraph_start
            .unwrap_or(self.checkpoint.line_start);
    }

    fn advance_inline_code(&mut self, byte: u8) -> bool {
        if self.checkpoint.in_fence || self.checkpoint.in_frontmatter {
            self.checkpoint.inline_code_delimiter = 0;
            self.checkpoint.inline_backtick_run = 0;
            return true;
        }
        if byte == b'`' {
            self.checkpoint.inline_backtick_run =
                self.checkpoint.inline_backtick_run.saturating_add(1);
            return true;
        }
        if self.checkpoint.inline_backtick_run > 0 {
            let run = self.checkpoint.inline_backtick_run;
            if self.checkpoint.inline_code_delimiter == 0 {
                self.checkpoint.inline_code_delimiter = run;
            } else if self.checkpoint.inline_code_delimiter == run {
                self.checkpoint.inline_code_delimiter = 0;
            }
            self.checkpoint.inline_backtick_run = 0;
        }
        self.checkpoint.inline_code_delimiter > 0
    }
}

fn initial_markdown_kind(byte: u8) -> MarkdownBlockKind {
    match byte {
        b'#' => MarkdownBlockKind::Heading,
        b'-' | b'*' | b'+' => MarkdownBlockKind::List,
        b'|' => MarkdownBlockKind::Table,
        b'>' => MarkdownBlockKind::Blockquote,
        b'`' => MarkdownBlockKind::InlineCode,
        b'~' => MarkdownBlockKind::Paragraph,
        b'<' => MarkdownBlockKind::LiteralText,
        b'0'..=b'9' => MarkdownBlockKind::List,
        _ => MarkdownBlockKind::Paragraph,
    }
}

fn markdown_block_continues(current: MarkdownBlockKind, next: MarkdownBlockKind) -> bool {
    current == next
        && matches!(
            current,
            MarkdownBlockKind::Paragraph
                | MarkdownBlockKind::List
                | MarkdownBlockKind::Table
                | MarkdownBlockKind::Blockquote
        )
}

pub(crate) struct MarkdownProjectionService;

struct MarkdownSemanticSource {
    fragment_index: usize,
    kind: MarkdownBlockKind,
    original: String,
    sanitized: String,
    checkpoint: MarkdownCheckpoint,
    block_id: String,
}

impl MarkdownProjectionService {
    pub(crate) fn project(
        chunk_start: u64,
        text: String,
        checkpoint: MarkdownCheckpoint,
        markdown_lookbehind: Vec<u8>,
        is_last: bool,
    ) -> Vec<MarkdownBlockFragment> {
        let mut fragments = Vec::new();
        let mut semantic_sources = Vec::new();
        let mut state = MarkdownScanState { checkpoint };
        let mut cursor = 0_usize;

        while cursor < text.len() {
            if fragments.len() >= MAX_MARKDOWN_FRAGMENTS.saturating_sub(1) {
                let block_start = active_markdown_block_start(state.checkpoint);
                let text = sanitize_markdown_fallback(
                    chunk_start.saturating_add(cursor as u64),
                    &text[cursor..],
                    state.checkpoint,
                    &markdown_lookbehind,
                    is_last,
                );
                fragments.push(MarkdownBlockFragment {
                    block_id: markdown_block_id(block_start),
                    kind: MarkdownBlockKind::LiteralText,
                    nodes: vec![MarkdownSemanticNode::LiteralText { text: text.clone() }],
                    text,
                    starts_block: false,
                    ends_block: is_last,
                });
                break;
            }

            let relative_end = text[cursor..]
                .find('\n')
                .map_or(text.len(), |index| cursor + index + 1);
            let line_fragment = &text[cursor..relative_end];
            let before = state.checkpoint;
            let absolute_start = chunk_start.saturating_add(cursor as u64);
            let classified = projected_markdown_line_kind(before, line_fragment);
            let continues_block = before.block_open
                && (before.in_fence
                    || !before.at_line_start
                    || markdown_block_continues(before.block_kind, classified));
            let block_start = if continues_block {
                active_markdown_block_start(before)
            } else {
                absolute_start
            };
            let kind = if continues_block {
                before.block_kind
            } else {
                classified
            };
            let starts_block = !continues_block;
            state.feed(line_fragment.as_bytes());
            let after = state.checkpoint;
            let reached_chunk_end = relative_end == text.len();
            let ends_line = line_fragment.ends_with('\n');
            let ends_block = if kind == MarkdownBlockKind::FencedCode {
                (before.in_fence && !after.in_fence) || (is_last && reached_chunk_end)
            } else if ends_line && !reached_chunk_end {
                let next_end = text[relative_end..]
                    .find('\n')
                    .map_or(text.len(), |index| relative_end + index + 1);
                let next_kind = classify_markdown_line(&text[relative_end..next_end]);
                !markdown_block_continues(kind, next_kind)
            } else {
                is_last && reached_chunk_end
            };
            let sanitized =
                if markdown_line_allows_destination_projection(before, line_fragment, kind) {
                    sanitize_markdown_destinations(
                        line_fragment,
                        before,
                        &markdown_lookbehind,
                        is_last && reached_chunk_end,
                        absolute_start,
                    )
                } else {
                    line_fragment.to_string()
                };
            let remaining_capacity = MAX_MARKDOWN_FRAGMENTS
                .saturating_sub(fragments.len())
                .saturating_sub(1);
            let block_id = markdown_block_id(block_start.min(absolute_start));
            let fragment_index = fragments.len();
            append_markdown_line_fragments(
                &mut fragments,
                block_id.clone(),
                kind,
                sanitized.clone(),
                starts_block,
                ends_block,
                remaining_capacity,
            );
            semantic_sources.push(MarkdownSemanticSource {
                fragment_index,
                kind,
                original: line_fragment.to_string(),
                sanitized,
                checkpoint: before,
                block_id,
            });
            cursor = relative_end;
        }

        attach_markdown_semantics(&mut fragments, &semantic_sources);
        fragments
    }
}

fn attach_markdown_semantics(
    fragments: &mut [MarkdownBlockFragment],
    sources: &[MarkdownSemanticSource],
) {
    let mut cursor = 0_usize;
    while cursor < sources.len() {
        let mut end = cursor + 1;
        while end < sources.len()
            && sources[end].block_id == sources[cursor].block_id
            && sources[end].kind == sources[cursor].kind
        {
            end += 1;
        }
        let group = &sources[cursor..end];
        let assignments = semantic_assignments(group);
        for (source, nodes) in group.iter().zip(assignments) {
            if let Some(fragment) = fragments.get_mut(source.fragment_index) {
                fragment.nodes = bounded_semantic_nodes(nodes, &source.sanitized);
            }
        }
        cursor = end;
    }
}

fn semantic_assignments(group: &[MarkdownSemanticSource]) -> Vec<Vec<MarkdownSemanticNode>> {
    if group.is_empty() {
        return Vec::new();
    }
    match group[0].kind {
        MarkdownBlockKind::List => list_semantic_assignments(group),
        MarkdownBlockKind::Table => {
            let mut assignments = vec![Vec::new(); group.len()];
            assignments[0] = vec![table_semantic_node(group)];
            assignments
        }
        _ => group
            .iter()
            .map(|source| semantic_nodes_for_source(source))
            .collect(),
    }
}

fn bounded_semantic_nodes(
    nodes: Vec<MarkdownSemanticNode>,
    sanitized: &str,
) -> Vec<MarkdownSemanticNode> {
    let count = nodes
        .iter()
        .map(markdown_semantic_node_count)
        .sum::<usize>();
    if count <= MAX_MARKDOWN_SEMANTIC_NODES {
        nodes
    } else {
        vec![MarkdownSemanticNode::LiteralText {
            text: sanitized.to_string(),
        }]
    }
}

fn markdown_semantic_node_count(node: &MarkdownSemanticNode) -> usize {
    let children = match node {
        MarkdownSemanticNode::Heading { children, .. }
        | MarkdownSemanticNode::Paragraph { children }
        | MarkdownSemanticNode::UnorderedList { children }
        | MarkdownSemanticNode::ListItem { children }
        | MarkdownSemanticNode::Strong { children }
        | MarkdownSemanticNode::Emphasis { children }
        | MarkdownSemanticNode::LinkText { children }
        | MarkdownSemanticNode::Blockquote { children }
        | MarkdownSemanticNode::Table { children }
        | MarkdownSemanticNode::TableHead { children }
        | MarkdownSemanticNode::TableBody { children }
        | MarkdownSemanticNode::TableRow { children }
        | MarkdownSemanticNode::TableHeaderCell { children }
        | MarkdownSemanticNode::TableCell { children }
        | MarkdownSemanticNode::OrderedList { children, .. } => children,
        MarkdownSemanticNode::Text { .. }
        | MarkdownSemanticNode::InlineCode { .. }
        | MarkdownSemanticNode::CodeBlock { .. }
        | MarkdownSemanticNode::ThematicBreak
        | MarkdownSemanticNode::LiteralText { .. } => return 1,
    };
    1_usize.saturating_add(
        children
            .iter()
            .map(markdown_semantic_node_count)
            .sum::<usize>(),
    )
}

fn semantic_nodes_for_source(source: &MarkdownSemanticSource) -> Vec<MarkdownSemanticNode> {
    match source.kind {
        MarkdownBlockKind::Heading => {
            let (level, original) = heading_content(&source.original);
            let (_, sanitized) = heading_content(&source.sanitized);
            vec![MarkdownSemanticNode::Heading {
                level,
                children: parse_inline_semantics(&original, &sanitized, source.checkpoint),
            }]
        }
        MarkdownBlockKind::Paragraph => vec![MarkdownSemanticNode::Paragraph {
            children: parse_inline_semantics(
                &source.original,
                &source.sanitized,
                source.checkpoint,
            ),
        }],
        MarkdownBlockKind::Blockquote => {
            let original = blockquote_content(&source.original);
            let sanitized = blockquote_content(&source.sanitized);
            vec![MarkdownSemanticNode::Blockquote {
                children: vec![MarkdownSemanticNode::Paragraph {
                    children: parse_inline_semantics(&original, &sanitized, source.checkpoint),
                }],
            }]
        }
        MarkdownBlockKind::ThematicBreak => vec![MarkdownSemanticNode::ThematicBreak],
        MarkdownBlockKind::FencedCode => {
            let text = if is_fence_line(
                source
                    .original
                    .trim_start_matches([' ', '\t'])
                    .trim_end_matches(['\r', '\n']),
            ) {
                String::new()
            } else {
                source.original.clone()
            };
            vec![MarkdownSemanticNode::CodeBlock { text }]
        }
        MarkdownBlockKind::InlineCode => vec![MarkdownSemanticNode::InlineCode {
            text: inline_code_content(&source.sanitized),
        }],
        MarkdownBlockKind::LiteralText => {
            if source.sanitized.trim_matches(['\r', '\n']).is_empty() {
                Vec::new()
            } else {
                vec![MarkdownSemanticNode::LiteralText {
                    text: source.sanitized.clone(),
                }]
            }
        }
        MarkdownBlockKind::List | MarkdownBlockKind::Table => Vec::new(),
    }
}

fn heading_content(text: &str) -> (u8, String) {
    let trimmed = text
        .trim_start_matches([' ', '\t'])
        .trim_end_matches(['\r', '\n']);
    let level = trimmed
        .bytes()
        .take_while(|byte| *byte == b'#')
        .count()
        .clamp(1, 6);
    let mut content = trimmed[level..].trim_start_matches([' ', '\t']);
    if let Some(without_hashes) = content.trim_end_matches('#').strip_suffix([' ', '\t']) {
        content = without_hashes.trim_end_matches([' ', '\t']);
    }
    (level as u8, content.to_string())
}

fn blockquote_content(text: &str) -> String {
    let mut trimmed = text.trim_start_matches([' ', '\t']);
    while let Some(rest) = trimmed.strip_prefix('>') {
        trimmed = rest.strip_prefix([' ', '\t']).unwrap_or(rest);
    }
    trimmed.trim_end_matches(['\r', '\n']).to_string()
}

fn inline_code_content(text: &str) -> String {
    let marker_len = text.bytes().take_while(|byte| *byte == b'`').count();
    if marker_len == 0 || text.len() < marker_len.saturating_mul(2) {
        return text.to_string();
    }
    let marker = "`".repeat(marker_len);
    text[marker_len..]
        .strip_suffix(&marker)
        .unwrap_or(&text[marker_len..])
        .to_string()
}

#[derive(Clone)]
struct ListSemanticEntry {
    source_index: usize,
    indent: usize,
    ordered_start: Option<u64>,
    children: Vec<MarkdownSemanticNode>,
}

fn list_semantic_assignments(group: &[MarkdownSemanticSource]) -> Vec<Vec<MarkdownSemanticNode>> {
    let mut assignments = vec![Vec::new(); group.len()];
    let entries = group
        .iter()
        .enumerate()
        .filter_map(|(source_index, source)| {
            let original = parse_list_marker(&source.original)?;
            let sanitized = parse_list_marker(&source.sanitized)?;
            Some(ListSemanticEntry {
                source_index,
                indent: original.indent,
                ordered_start: original.ordered_start,
                children: parse_inline_semantics(
                    original.content,
                    sanitized.content,
                    source.checkpoint,
                ),
            })
        })
        .collect::<Vec<_>>();
    let Some(first) = entries.first() else {
        for (index, source) in group.iter().enumerate() {
            assignments[index] = vec![MarkdownSemanticNode::LiteralText {
                text: source.sanitized.clone(),
            }];
        }
        return assignments;
    };
    let mut entry_index = 0_usize;
    for (source_index, node) in parse_list_roots(&entries, &mut entry_index, first.indent) {
        assignments[source_index].push(node);
    }
    assignments
}

struct ParsedListMarker<'a> {
    indent: usize,
    ordered_start: Option<u64>,
    content: &'a str,
}

fn parse_list_marker(text: &str) -> Option<ParsedListMarker<'_>> {
    let bytes = text.as_bytes();
    let mut cursor = 0_usize;
    let mut indent = 0_usize;
    while let Some(byte) = bytes.get(cursor) {
        match byte {
            b' ' => indent += 1,
            b'\t' => indent += 4,
            _ => break,
        }
        cursor += 1;
    }
    let ordered_start = if matches!(bytes.get(cursor), Some(b'-' | b'*' | b'+')) {
        cursor += 1;
        None
    } else {
        let digit_start = cursor;
        while bytes.get(cursor).is_some_and(u8::is_ascii_digit) {
            cursor += 1;
        }
        if cursor == digit_start || !matches!(bytes.get(cursor), Some(b'.' | b')')) {
            return None;
        }
        let start = text[digit_start..cursor].parse::<u64>().ok()?;
        cursor += 1;
        Some(start)
    };
    if !bytes
        .get(cursor)
        .is_some_and(|byte| matches!(byte, b' ' | b'\t'))
    {
        return None;
    }
    while bytes
        .get(cursor)
        .is_some_and(|byte| matches!(byte, b' ' | b'\t'))
    {
        cursor += 1;
    }
    Some(ParsedListMarker {
        indent,
        ordered_start,
        content: text[cursor..].trim_end_matches(['\r', '\n']),
    })
}

fn parse_list_roots(
    entries: &[ListSemanticEntry],
    index: &mut usize,
    indent: usize,
) -> Vec<(usize, MarkdownSemanticNode)> {
    let mut roots = Vec::new();
    while let Some(entry) = entries.get(*index) {
        if entry.indent < indent {
            break;
        }
        if entry.indent > indent {
            let nested_indent = entry.indent;
            roots.extend(parse_list_roots(entries, index, nested_indent));
            continue;
        }
        let source_index = entry.source_index;
        let ordered_start = entry.ordered_start;
        let mut children = entry.children.clone();
        *index += 1;
        if entries.get(*index).is_some_and(|next| next.indent > indent) {
            let nested_indent = entries[*index].indent;
            children.extend(
                parse_list_roots(entries, index, nested_indent)
                    .into_iter()
                    .map(|(_, node)| node),
            );
        }
        let item = MarkdownSemanticNode::ListItem { children };
        let root = match ordered_start {
            Some(start) => MarkdownSemanticNode::OrderedList {
                start,
                children: vec![item],
            },
            None => MarkdownSemanticNode::UnorderedList {
                children: vec![item],
            },
        };
        roots.push((source_index, root));
    }
    roots
}

fn table_semantic_node(group: &[MarkdownSemanticSource]) -> MarkdownSemanticNode {
    let has_header = group
        .get(1)
        .is_some_and(|source| looks_like_table_separator(source.sanitized.trim()));
    let mut children = Vec::new();
    if has_header {
        children.push(MarkdownSemanticNode::TableHead {
            children: vec![table_row_node(&group[0], true)],
        });
    }
    let body_start = if has_header { 2 } else { 0 };
    let body_rows = group[body_start..]
        .iter()
        .map(|source| table_row_node(source, false))
        .collect();
    children.push(MarkdownSemanticNode::TableBody {
        children: body_rows,
    });
    MarkdownSemanticNode::Table { children }
}

fn table_row_node(source: &MarkdownSemanticSource, header: bool) -> MarkdownSemanticNode {
    let original = markdown_table_cells(&source.original);
    let sanitized = markdown_table_cells(&source.sanitized);
    let children = sanitized
        .iter()
        .enumerate()
        .map(|(index, safe_cell)| {
            let original_cell = original.get(index).copied().unwrap_or(safe_cell);
            let children = parse_inline_semantics(original_cell, safe_cell, source.checkpoint);
            if header {
                MarkdownSemanticNode::TableHeaderCell { children }
            } else {
                MarkdownSemanticNode::TableCell { children }
            }
        })
        .collect();
    MarkdownSemanticNode::TableRow { children }
}

fn markdown_table_cells(text: &str) -> Vec<&str> {
    text.trim_end_matches(['\r', '\n'])
        .trim()
        .trim_start_matches('|')
        .trim_end_matches('|')
        .split('|')
        .map(str::trim)
        .collect()
}

fn parse_inline_semantics(
    source: &str,
    sanitized: &str,
    checkpoint: MarkdownCheckpoint,
) -> Vec<MarkdownSemanticNode> {
    if !matches!(checkpoint.destination_state, MarkdownDestinationState::Text)
        || has_incomplete_inline_destination(source)
    {
        return text_semantic_nodes(sanitized);
    }
    parse_inline_semantics_at_depth(source, 0)
}

fn parse_inline_semantics_at_depth(source: &str, depth: usize) -> Vec<MarkdownSemanticNode> {
    if depth >= MAX_MARKDOWN_NESTING {
        return text_semantic_nodes(&sanitize_inline_fallback(source));
    }
    let bytes = source.as_bytes();
    let (label_ends, _) = markdown_label_index(bytes);
    let mut nodes = Vec::new();
    let mut cursor = 0_usize;
    let mut plain_start = 0_usize;
    while cursor < bytes.len() {
        let mut matched = None;
        if matches!(bytes[cursor], b'!' | b'[') {
            if let Some(InlineDestinationMatch::Complete {
                label_start,
                label_end,
                end,
            }) = inline_destination_at(bytes, cursor, &label_ends)
            {
                let label = safe_link_label(&source[label_start..label_end]);
                matched = Some((
                    end,
                    MarkdownSemanticNode::LinkText {
                        children: parse_inline_semantics_at_depth(&label, depth + 1),
                    },
                ));
            } else if let Some(reference) = inline_reference_at(bytes, cursor, &label_ends) {
                let label = safe_link_label(&source[reference.label_start..reference.label_end]);
                matched = Some((
                    reference.end,
                    MarkdownSemanticNode::LinkText {
                        children: parse_inline_semantics_at_depth(&label, depth + 1),
                    },
                ));
            }
        }
        if matched.is_none() && bytes[cursor] == b'<' {
            if let Some(end) = complete_uri_autolink_end(bytes, cursor) {
                matched = Some((
                    end,
                    MarkdownSemanticNode::LinkText {
                        children: vec![MarkdownSemanticNode::Text {
                            text: "link".to_string(),
                        }],
                    },
                ));
            }
        }
        if matched.is_none() && bytes[cursor] == b'`' {
            let marker_len = bytes[cursor..]
                .iter()
                .take_while(|byte| **byte == b'`')
                .count();
            let marker = "`".repeat(marker_len);
            if let Some(relative_end) = source[cursor + marker_len..].find(&marker) {
                let content_start = cursor + marker_len;
                let content_end = content_start + relative_end;
                matched = Some((
                    content_end + marker_len,
                    MarkdownSemanticNode::InlineCode {
                        text: source[content_start..content_end].to_string(),
                    },
                ));
            }
        }
        if matched.is_none() {
            for (marker, strong) in [("**", true), ("__", true), ("*", false), ("_", false)] {
                if source[cursor..].starts_with(marker) {
                    let content_start = cursor + marker.len();
                    if let Some(relative_end) = source[content_start..].find(marker) {
                        let content_end = content_start + relative_end;
                        if content_end > content_start {
                            let children = parse_inline_semantics_at_depth(
                                &source[content_start..content_end],
                                depth + 1,
                            );
                            let node = if strong {
                                MarkdownSemanticNode::Strong { children }
                            } else {
                                MarkdownSemanticNode::Emphasis { children }
                            };
                            matched = Some((content_end + marker.len(), node));
                        }
                    }
                    break;
                }
            }
        }
        if let Some((end, node)) = matched {
            push_text_semantic_node(&mut nodes, &source[plain_start..cursor]);
            nodes.push(node);
            cursor = end;
            plain_start = end;
            continue;
        }
        cursor += source[cursor..].chars().next().map_or(1, char::len_utf8);
    }
    push_text_semantic_node(&mut nodes, &source[plain_start..]);
    nodes
}

fn has_incomplete_inline_destination(source: &str) -> bool {
    let bytes = source.as_bytes();
    let (label_ends, _) = markdown_label_index(bytes);
    bytes.iter().enumerate().any(|(index, byte)| {
        matches!(byte, b'!' | b'[')
            && matches!(
                inline_destination_at(bytes, index, &label_ends),
                Some(InlineDestinationMatch::Incomplete | InlineDestinationMatch::Excessive { .. })
            )
    })
}

fn safe_link_label(label: &str) -> String {
    if label.contains("](") || label.contains("][") || label.contains('<') {
        "link".to_string()
    } else {
        sanitize_inline_fallback(label)
    }
}

fn sanitize_inline_fallback(text: &str) -> String {
    sanitize_markdown_destinations(text, MarkdownCheckpoint::default(), &[], true, 0)
}

fn text_semantic_nodes(text: &str) -> Vec<MarkdownSemanticNode> {
    let mut nodes = Vec::new();
    push_text_semantic_node(&mut nodes, text);
    nodes
}

fn push_text_semantic_node(nodes: &mut Vec<MarkdownSemanticNode>, text: &str) {
    if text.is_empty() {
        return;
    }
    if let Some(MarkdownSemanticNode::Text { text: previous }) = nodes.last_mut() {
        previous.push_str(text);
    } else {
        nodes.push(MarkdownSemanticNode::Text {
            text: text.to_string(),
        });
    }
}

fn projected_markdown_line_kind(
    checkpoint: MarkdownCheckpoint,
    line_fragment: &str,
) -> MarkdownBlockKind {
    if checkpoint.in_frontmatter
        || (checkpoint.line_start == 0 && is_frontmatter_delimiter(line_fragment))
    {
        MarkdownBlockKind::LiteralText
    } else if checkpoint.in_fence {
        MarkdownBlockKind::FencedCode
    } else if !checkpoint.at_line_start {
        checkpoint.block_kind
    } else {
        classify_markdown_line(line_fragment)
    }
}

fn sanitize_markdown_fallback(
    absolute_start: u64,
    text: &str,
    checkpoint: MarkdownCheckpoint,
    markdown_lookbehind: &[u8],
    is_last: bool,
) -> String {
    let mut output = String::with_capacity(text.len());
    let mut state = MarkdownScanState { checkpoint };
    let mut cursor = 0_usize;
    while cursor < text.len() {
        let relative_end = text[cursor..]
            .find('\n')
            .map_or(text.len(), |index| cursor + index + 1);
        let line = &text[cursor..relative_end];
        let before = state.checkpoint;
        let kind = projected_markdown_line_kind(before, line);
        let reached_end = relative_end == text.len();
        if markdown_line_allows_destination_projection(before, line, kind) {
            output.push_str(&sanitize_markdown_destinations(
                line,
                before,
                markdown_lookbehind,
                is_last && reached_end,
                absolute_start.saturating_add(cursor as u64),
            ));
        } else {
            output.push_str(line);
        }
        state.feed(line.as_bytes());
        cursor = relative_end;
    }
    output
}

fn active_markdown_block_start(checkpoint: MarkdownCheckpoint) -> u64 {
    if checkpoint.block_open || checkpoint.in_fence {
        checkpoint.block_start
    } else {
        checkpoint.line_start
    }
}

fn markdown_block_id(block_start: u64) -> String {
    format!("markdown-block-{block_start}")
}

fn markdown_kind_allows_destination_projection(kind: MarkdownBlockKind) -> bool {
    matches!(
        kind,
        MarkdownBlockKind::Heading
            | MarkdownBlockKind::Paragraph
            | MarkdownBlockKind::List
            | MarkdownBlockKind::Table
            | MarkdownBlockKind::Blockquote
    )
}

fn markdown_line_allows_destination_projection(
    checkpoint: MarkdownCheckpoint,
    line: &str,
    kind: MarkdownBlockKind,
) -> bool {
    if markdown_kind_allows_destination_projection(kind) {
        return true;
    }
    if kind != MarkdownBlockKind::LiteralText
        || checkpoint.in_fence
        || checkpoint.in_frontmatter
        || is_frontmatter_delimiter(line)
    {
        return false;
    }
    let trimmed = line.trim_start_matches([' ', '\t']);
    !(trimmed.starts_with('<')
        && !looks_like_complete_uri_autolink(trimmed.as_bytes(), 0)
        && !looks_like_complete_email_autolink(trimmed.as_bytes(), 0))
}

fn classify_markdown_line(line: &str) -> MarkdownBlockKind {
    let trimmed = line
        .trim_start_matches([' ', '\t'])
        .trim_end_matches(['\r', '\n']);
    if trimmed.is_empty() {
        return MarkdownBlockKind::LiteralText;
    }
    if is_fence_line(trimmed) {
        return MarkdownBlockKind::FencedCode;
    }
    if is_thematic_break(trimmed) {
        return MarkdownBlockKind::ThematicBreak;
    }
    if trimmed.starts_with('<')
        && !looks_like_complete_uri_autolink(trimmed.as_bytes(), 0)
        && !looks_like_complete_email_autolink(trimmed.as_bytes(), 0)
    {
        return MarkdownBlockKind::LiteralText;
    }
    if is_heading_line(trimmed) {
        return MarkdownBlockKind::Heading;
    }
    if is_list_line(trimmed) {
        return MarkdownBlockKind::List;
    }
    if trimmed.starts_with('|') || looks_like_table_separator(trimmed) {
        return MarkdownBlockKind::Table;
    }
    if trimmed.starts_with('>') {
        return if markdown_blockquote_depth(trimmed) > MAX_MARKDOWN_NESTING {
            MarkdownBlockKind::LiteralText
        } else {
            MarkdownBlockKind::Blockquote
        };
    }
    MarkdownBlockKind::Paragraph
}

fn markdown_blockquote_depth(line: &str) -> usize {
    let bytes = line.as_bytes();
    let mut depth = 0_usize;
    let mut cursor = 0_usize;
    while bytes.get(cursor) == Some(&b'>') {
        depth += 1;
        cursor += 1;
        if bytes
            .get(cursor)
            .is_some_and(|byte| matches!(byte, b' ' | b'\t'))
        {
            cursor += 1;
        }
    }
    depth
}

fn is_frontmatter_delimiter(line: &str) -> bool {
    line.trim_matches([' ', '\t', '\r', '\n']) == "---"
}

fn is_fence_line(line: &str) -> bool {
    let bytes = line.as_bytes();
    let Some(&marker @ (b'`' | b'~')) = bytes.first() else {
        return false;
    };
    bytes.iter().take_while(|byte| **byte == marker).count() >= 3
}

fn is_thematic_break(line: &str) -> bool {
    let mut marker = None;
    let mut count = 0_usize;
    for byte in line.bytes() {
        if matches!(byte, b' ' | b'\t') {
            continue;
        }
        if !matches!(byte, b'-' | b'*' | b'_') {
            return false;
        }
        if marker.is_some_and(|active| active != byte) {
            return false;
        }
        marker = Some(byte);
        count += 1;
    }
    count >= 3
}

fn is_heading_line(line: &str) -> bool {
    let hashes = line.bytes().take_while(|byte| *byte == b'#').count();
    (1..=6).contains(&hashes)
        && line
            .as_bytes()
            .get(hashes)
            .is_none_or(|byte| matches!(byte, b' ' | b'\t'))
}

fn is_list_line(line: &str) -> bool {
    let bytes = line.as_bytes();
    if matches!(bytes.first(), Some(b'-' | b'*' | b'+')) {
        return bytes
            .get(1)
            .is_some_and(|byte| matches!(byte, b' ' | b'\t'));
    }
    let digits = bytes
        .iter()
        .take_while(|byte| byte.is_ascii_digit())
        .count();
    digits > 0
        && matches!(bytes.get(digits), Some(b'.' | b')'))
        && bytes
            .get(digits + 1)
            .is_some_and(|byte| matches!(byte, b' ' | b'\t'))
}

fn looks_like_table_separator(line: &str) -> bool {
    line.contains('|')
        && line
            .bytes()
            .all(|byte| matches!(byte, b'|' | b':' | b'-' | b' ' | b'\t'))
}

fn append_markdown_line_fragments(
    fragments: &mut Vec<MarkdownBlockFragment>,
    block_id: String,
    kind: MarkdownBlockKind,
    text: String,
    starts_block: bool,
    ends_block: bool,
    inline_capacity: usize,
) {
    let pieces = split_inline_code(&text, kind);
    if pieces.len() > inline_capacity.max(1) {
        fragments.push(MarkdownBlockFragment {
            block_id,
            kind,
            text,
            nodes: Vec::new(),
            starts_block,
            ends_block,
        });
        return;
    }
    let last = pieces.len().saturating_sub(1);
    for (index, (piece_kind, piece)) in pieces.into_iter().enumerate() {
        fragments.push(MarkdownBlockFragment {
            block_id: block_id.clone(),
            kind: piece_kind,
            text: piece.to_string(),
            nodes: Vec::new(),
            starts_block: starts_block && index == 0,
            ends_block: ends_block && index == last,
        });
    }
}

fn split_inline_code(text: &str, kind: MarkdownBlockKind) -> Vec<(MarkdownBlockKind, &str)> {
    if !matches!(
        kind,
        MarkdownBlockKind::Heading
            | MarkdownBlockKind::Paragraph
            | MarkdownBlockKind::List
            | MarkdownBlockKind::Table
            | MarkdownBlockKind::Blockquote
    ) {
        return vec![(kind, text)];
    }
    let mut pieces = Vec::new();
    let mut cursor = 0_usize;
    while let Some(open_relative) = text[cursor..].find('`') {
        let open = cursor + open_relative;
        let Some(close_relative) = text[open + 1..].find('`') else {
            break;
        };
        let close = open + 1 + close_relative + 1;
        if open > cursor {
            pieces.push((kind, &text[cursor..open]));
        }
        pieces.push((MarkdownBlockKind::InlineCode, &text[open..close]));
        cursor = close;
    }
    if cursor < text.len() {
        pieces.push((kind, &text[cursor..]));
    }
    if pieces.is_empty() {
        pieces.push((kind, text));
    }
    pieces
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InlineDestinationMatch {
    Complete {
        label_start: usize,
        label_end: usize,
        end: usize,
    },
    Incomplete,
    Excessive {
        end: Option<usize>,
    },
}

const NO_MARKDOWN_LABEL_END: u32 = u32::MAX;

pub(crate) fn markdown_label_index(input: &[u8]) -> (Vec<u32>, usize) {
    let mut ends = vec![NO_MARKDOWN_LABEL_END; input.len()];
    let mut stack = [0_usize; MAX_MARKDOWN_NESTING];
    let mut depth = 0_usize;
    let mut overflow_depth = 0_usize;
    let mut escaped = false;
    let mut scan_visits = 0_usize;
    for (index, &byte) in input.iter().enumerate() {
        scan_visits += 1;
        if escaped {
            escaped = false;
            continue;
        }
        if byte == b'\\' {
            escaped = true;
            continue;
        }
        if byte == b'[' {
            if overflow_depth > 0 {
                overflow_depth = overflow_depth.saturating_add(1);
            } else if depth < MAX_MARKDOWN_NESTING {
                stack[depth] = index;
                depth += 1;
            } else {
                overflow_depth = 1;
            }
        } else if byte == b']' {
            if overflow_depth > 0 {
                overflow_depth -= 1;
            } else if depth > 0 {
                depth -= 1;
                ends[stack[depth]] = u32::try_from(index).unwrap_or(NO_MARKDOWN_LABEL_END);
            }
        }
    }
    (ends, scan_visits)
}

fn markdown_label_end(label_ends: &[u32], open: usize) -> Option<usize> {
    let end = *label_ends.get(open)?;
    (end != NO_MARKDOWN_LABEL_END)
        .then(|| usize::try_from(end).ok())
        .flatten()
}

fn inline_destination_at(
    input: &[u8],
    start: usize,
    label_ends: &[u32],
) -> Option<InlineDestinationMatch> {
    let label_open = match input.get(start..) {
        Some([b'!', b'[', ..]) => start + 1,
        Some([b'[', ..]) => start,
        _ => return None,
    };
    let label_start = label_open + 1;
    let label_end = markdown_label_end(label_ends, label_open)?;
    let destination_open = label_end + 1;
    if input.get(destination_open) != Some(&b'(') {
        return None;
    }
    let mut destination_depth = 1_usize;
    let mut escaped = false;
    let mut cursor = destination_open + 1;
    let mut excessive = false;
    while let Some(&byte) = input.get(cursor) {
        if escaped {
            escaped = false;
        } else if byte == b'\\' {
            escaped = true;
        } else if byte == b'(' {
            destination_depth += 1;
            if destination_depth > MAX_MARKDOWN_NESTING {
                excessive = true;
            }
        } else if byte == b')' {
            destination_depth -= 1;
            if destination_depth == 0 {
                if excessive {
                    return Some(InlineDestinationMatch::Excessive {
                        end: Some(cursor + 1),
                    });
                }
                return Some(InlineDestinationMatch::Complete {
                    label_start,
                    label_end,
                    end: cursor + 1,
                });
            }
        }
        cursor += 1;
    }
    Some(if excessive {
        InlineDestinationMatch::Excessive { end: None }
    } else {
        InlineDestinationMatch::Incomplete
    })
}

#[derive(Clone, Copy)]
struct InlineReferenceMatch {
    label_start: usize,
    label_end: usize,
    end: usize,
}

fn inline_reference_at(
    input: &[u8],
    start: usize,
    label_ends: &[u32],
) -> Option<InlineReferenceMatch> {
    let label_open = match input.get(start..) {
        Some([b'!', b'[', ..]) => start + 1,
        Some([b'[', ..]) => start,
        _ => return None,
    };
    let label_end = markdown_label_end(label_ends, label_open)?;
    let suffix_start = label_end + 1;
    if input.get(suffix_start) == Some(&b'(') {
        return None;
    }
    let end = if input.get(suffix_start) == Some(&b'[') {
        markdown_label_end(label_ends, suffix_start)?.saturating_add(1)
    } else {
        suffix_start
    };
    Some(InlineReferenceMatch {
        label_start: label_open + 1,
        label_end,
        end,
    })
}

fn markdown_reference_definition_is_active(
    mut state: MarkdownReferenceDefinitionState,
    input: &[u8],
) -> bool {
    if state == MarkdownReferenceDefinitionState::Active {
        return true;
    }
    for &byte in input {
        state = state.advance(byte);
        if state == MarkdownReferenceDefinitionState::Active {
            return true;
        }
        if byte == b'\n' {
            break;
        }
    }
    false
}

fn append_safe_markdown_label(output: &mut Vec<u8>, label: &[u8]) {
    let nested_destination = label.windows(2).any(|window| window == b"](");
    let nested_reference = label.windows(2).any(|window| window == b"][");
    if nested_destination || nested_reference || label.contains(&b'<') {
        output.extend_from_slice(b"link");
    } else {
        output.extend_from_slice(label);
    }
}

fn markdown_line_ending(text: &str) -> &'static str {
    if text.ends_with("\r\n") {
        "\r\n"
    } else if text.ends_with('\n') {
        "\n"
    } else {
        ""
    }
}

fn complete_uri_autolink_end(input: &[u8], start: usize) -> Option<usize> {
    if input.get(start) != Some(&b'<') {
        return None;
    }
    let mut cursor = start + 1;
    if !input.get(cursor)?.is_ascii_alphabetic() {
        return None;
    }
    cursor += 1;
    while input
        .get(cursor)
        .is_some_and(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'.' | b'-'))
    {
        cursor += 1;
        if cursor.saturating_sub(start + 1) > 32 {
            return None;
        }
    }
    let scheme_length = cursor.saturating_sub(start + 1);
    if !(2..=32).contains(&scheme_length) || input.get(cursor) != Some(&b':') {
        return None;
    }
    cursor += 1;
    let destination_start = cursor;
    while let Some(&byte) = input.get(cursor) {
        if byte == b'>' {
            return (cursor > destination_start).then_some(cursor + 1);
        }
        if byte <= b' ' || byte == b'<' {
            return None;
        }
        cursor += 1;
    }
    None
}

fn looks_like_complete_uri_autolink(input: &[u8], start: usize) -> bool {
    complete_uri_autolink_end(input, start).is_some()
}

fn looks_like_complete_email_autolink(input: &[u8], start: usize) -> bool {
    if input.get(start) != Some(&b'<') {
        return false;
    }
    let mut cursor = start + 1;
    let local_start = cursor;
    let mut domain_start = None;
    while let Some(&byte) = input.get(cursor) {
        if byte == b'>' {
            return domain_start.is_some_and(|domain| cursor > domain);
        }
        if byte <= b' ' || byte == b'<' {
            return false;
        }
        if byte == b'@' {
            if domain_start.is_some() || cursor == local_start {
                return false;
            }
            domain_start = Some(cursor + 1);
        }
        cursor += 1;
    }
    false
}

fn append_verified_autolink_probe_prefix(
    output: &mut Vec<u8>,
    checkpoint: MarkdownCheckpoint,
    absolute_start: u64,
    markdown_lookbehind: &[u8],
) -> bool {
    let MarkdownDestinationState::AutolinkProbe(checkpoint_probe) = checkpoint.destination_state
    else {
        return false;
    };
    let Some(destination_start) = checkpoint.destination_start else {
        return false;
    };
    let Ok(distance) = usize::try_from(absolute_start.saturating_sub(destination_start)) else {
        return false;
    };
    let expected = usize::from(checkpoint_probe.len).saturating_add(1);
    if destination_start >= absolute_start
        || distance != expected
        || distance > markdown_lookbehind.len()
    {
        return false;
    }
    let prefix = &markdown_lookbehind[markdown_lookbehind.len() - distance..];
    if prefix.first() != Some(&b'<')
        || !prefix[1..].iter().enumerate().all(|(index, byte)| {
            if index == 0 {
                byte.is_ascii_alphabetic()
            } else {
                byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'.' | b'-')
            }
        })
    {
        return false;
    }
    output.extend_from_slice(prefix);
    true
}

pub(crate) fn sanitize_markdown_destinations(
    text: &str,
    checkpoint: MarkdownCheckpoint,
    markdown_lookbehind: &[u8],
    is_last: bool,
    absolute_start: u64,
) -> String {
    let mut state = checkpoint.destination_state;
    let mut inline_code_delimiter = checkpoint.inline_code_delimiter;
    let mut inline_backtick_run = checkpoint.inline_backtick_run;
    let input = text.as_bytes();
    let projected = input;
    let (label_ends, _label_scan_visits) = markdown_label_index(projected);
    if !checkpoint.in_fence
        && !checkpoint.in_frontmatter
        && markdown_reference_definition_is_active(checkpoint.reference_definition_state, projected)
    {
        return markdown_line_ending(text).to_string();
    }
    let mut bytes = Vec::with_capacity(input.len());
    let mut index = 0_usize;
    let mut local_autolink_probe_start = None;
    let mut continued_autolink_probe_start =
        matches!(state, MarkdownDestinationState::AutolinkProbe(_)).then_some(0_usize);
    while index < projected.len() {
        let byte = projected[index];
        if inline_code_delimiter > 0 || inline_backtick_run > 0 {
            if byte == b'`' {
                bytes.push(byte);
                inline_backtick_run = inline_backtick_run.saturating_add(1);
                index += 1;
                continue;
            }
            if inline_backtick_run > 0 {
                let run = inline_backtick_run;
                if inline_code_delimiter == 0 {
                    inline_code_delimiter = run;
                } else if inline_code_delimiter == run {
                    inline_code_delimiter = 0;
                }
                inline_backtick_run = 0;
            }
            if inline_code_delimiter > 0 {
                bytes.push(byte);
                index += 1;
                continue;
            }
        }
        match state {
            MarkdownDestinationState::Text => {
                if byte == b'`' {
                    bytes.push(byte);
                    inline_backtick_run = 1;
                    index += 1;
                    continue;
                }
                if matches!(byte, b'!' | b'[') {
                    match inline_destination_at(projected, index, &label_ends) {
                        Some(InlineDestinationMatch::Complete {
                            label_start,
                            label_end,
                            end,
                        }) => {
                            append_safe_markdown_label(
                                &mut bytes,
                                &projected[label_start..label_end],
                            );
                            index = end;
                            continue;
                        }
                        Some(
                            InlineDestinationMatch::Incomplete
                            | InlineDestinationMatch::Excessive { end: Some(_) }
                            | InlineDestinationMatch::Excessive { end: None },
                        )
                        | None => {}
                    }
                    if let Some(reference) = inline_reference_at(projected, index, &label_ends) {
                        append_safe_markdown_label(
                            &mut bytes,
                            &projected[reference.label_start..reference.label_end],
                        );
                        index = reference.end;
                        state = MarkdownDestinationState::Text;
                        continue;
                    }
                }
                if byte == b'<' {
                    if let Some(end) = complete_uri_autolink_end(projected, index) {
                        bytes.extend_from_slice(b"link");
                        index = end;
                        continue;
                    }
                }
                state = state.advance(byte);
                if byte != b'<' {
                    bytes.push(byte);
                } else {
                    local_autolink_probe_start = Some(index);
                }
            }
            MarkdownDestinationState::AfterLabel => {
                let suppress = byte == b'(';
                state = state.advance(byte);
                if !suppress {
                    bytes.push(byte);
                }
            }
            MarkdownDestinationState::Destination { .. }
            | MarkdownDestinationState::AutolinkDestination => {
                let next = state.advance(byte);
                if byte == b'\n' && matches!(next, MarkdownDestinationState::Text) {
                    bytes.push(byte);
                }
                state = next;
            }
            MarkdownDestinationState::AutolinkProbe(probe) => match probe.advance(byte) {
                AutolinkAdvance::Matched(next) => {
                    state = MarkdownDestinationState::AutolinkProbe(next);
                }
                AutolinkAdvance::UriComplete | AutolinkAdvance::EmailComplete => {
                    bytes.extend_from_slice(b"link");
                    state = MarkdownDestinationState::AutolinkDestination;
                    local_autolink_probe_start = None;
                    continued_autolink_probe_start = None;
                }
                AutolinkAdvance::Mismatch => {
                    if let Some(start) = local_autolink_probe_start.take() {
                        bytes.extend_from_slice(&projected[start..index]);
                    } else {
                        if !append_verified_autolink_probe_prefix(
                            &mut bytes,
                            checkpoint,
                            absolute_start,
                            markdown_lookbehind,
                        ) {
                            bytes.push(b'<');
                        }
                        if let Some(start) = continued_autolink_probe_start.take() {
                            bytes.extend_from_slice(&projected[start..index]);
                        }
                    }
                    state = MarkdownDestinationState::Text;
                    continue;
                }
            },
        }
        index += 1;
    }
    if is_last {
        if let MarkdownDestinationState::AutolinkProbe(_probe) = state {
            if let Some(start) = local_autolink_probe_start {
                bytes.extend_from_slice(&projected[start..]);
            } else {
                if !append_verified_autolink_probe_prefix(
                    &mut bytes,
                    checkpoint,
                    absolute_start,
                    markdown_lookbehind,
                ) {
                    bytes.push(b'<');
                }
                if let Some(start) = continued_autolink_probe_start {
                    bytes.extend_from_slice(&projected[start..]);
                }
            }
        }
    }
    String::from_utf8(bytes).unwrap_or_default()
}

#[cfg(test)]
mod semantic_contract_tests {
    use super::*;
    use crate::api::dto::local::{LocalSourcePreviewContentDto, MarkdownBlockFragmentDto};

    #[test]
    fn projection_serializes_the_shared_semantic_tree_fixture() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../../../tests/fixtures/m4e_markdown_semantic_contract.json"
        ))
        .expect("semantic Markdown fixture must be valid JSON");
        let source = fixture["source"]
            .as_str()
            .expect("semantic Markdown fixture source must be text");

        let fragments = MarkdownProjectionService::project(
            0,
            source.to_string(),
            MarkdownCheckpoint::default(),
            Vec::new(),
            true,
        );
        let actual = LocalSourcePreviewContentDto::Markdown {
            block_fragments: fragments
                .into_iter()
                .map(MarkdownBlockFragmentDto::from)
                .collect(),
        };

        assert_eq!(
            serde_json::to_value(actual).expect("semantic DTO must serialize"),
            fixture["content"]
        );
    }
}
