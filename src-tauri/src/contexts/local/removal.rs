//! Pure, fail-closed primitives for Local batch removal.
//!
//! This module deliberately has no filesystem authority. The coordinator owns
//! plan lifetime and verified I/O; this core only qualifies an already-resolved
//! [`InstanceHandle`] and transforms parsed JSON values.

use std::cmp::Ordering;
use std::fmt;

use serde_json::Value;

use super::adapter::{ParserId, SurfaceKind};
use super::domain::{FileIdentity, InstanceHandle};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RemovalStrategy {
    JsonPointerRemovalV1,
    DedicatedFileRemovalV1,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RemovalBlock {
    UnsupportedRemovalShape,
    InvalidJsonPointer,
}

impl RemovalBlock {
    pub(crate) const fn code(self) -> &'static str {
        match self {
            Self::UnsupportedRemovalShape => "unsupported_removal_shape",
            Self::InvalidJsonPointer => "invalid_json_pointer",
        }
    }
}

#[derive(Clone)]
pub(crate) struct RemovalSnapshotIdentity {
    file_identity: FileIdentity,
    content_sha256: Option<[u8; 32]>,
}

impl RemovalSnapshotIdentity {
    fn from_handle(handle: &InstanceHandle) -> Self {
        Self {
            file_identity: handle.scan_file_identity,
            content_sha256: handle.scan_content_sha256,
        }
    }

    pub(crate) fn file_identity(&self) -> FileIdentity {
        self.file_identity
    }

    pub(crate) fn content_sha256(&self) -> Option<[u8; 32]> {
        self.content_sha256
    }
}

impl fmt::Debug for RemovalSnapshotIdentity {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RemovalSnapshotIdentity")
            .field("content_sha256_present", &self.content_sha256.is_some())
            .finish_non_exhaustive()
    }
}

#[derive(Clone)]
pub(crate) struct RemovalMember {
    instance_id: String,
    strategy: RemovalStrategy,
    snapshot_identity: RemovalSnapshotIdentity,
    json_pointer: Option<JsonPointer>,
}

impl RemovalMember {
    pub(crate) fn qualify(
        instance_id: impl Into<String>,
        kind: SurfaceKind,
        handle: &InstanceHandle,
    ) -> Result<Self, RemovalBlock> {
        let (strategy, json_pointer) = match (handle.source_parser_id, kind) {
            (ParserId::HookJsonV1 | ParserId::ClaudeSettingsV1, _) => {
                let locator = handle
                    .config_entry_locator
                    .as_deref()
                    .ok_or(RemovalBlock::UnsupportedRemovalShape)?;
                let pointer =
                    JsonPointer::parse(locator).map_err(|_| RemovalBlock::InvalidJsonPointer)?;
                if pointer.is_root() {
                    return Err(RemovalBlock::UnsupportedRemovalShape);
                }
                (RemovalStrategy::JsonPointerRemovalV1, Some(pointer))
            }
            (ParserId::SkillFrontmatterV1, SurfaceKind::Skill)
                if handle.config_entry_locator.is_none() =>
            {
                (RemovalStrategy::DedicatedFileRemovalV1, None)
            }
            _ => return Err(RemovalBlock::UnsupportedRemovalShape),
        };

        Ok(Self {
            instance_id: instance_id.into(),
            strategy,
            snapshot_identity: RemovalSnapshotIdentity::from_handle(handle),
            json_pointer,
        })
    }

    pub(crate) fn instance_id(&self) -> &str {
        &self.instance_id
    }

    pub(crate) const fn strategy(&self) -> RemovalStrategy {
        self.strategy
    }

    pub(crate) fn snapshot_identity(&self) -> &RemovalSnapshotIdentity {
        &self.snapshot_identity
    }

    pub(crate) fn json_selection(
        &self,
        expected: ExpectedJsonValue,
    ) -> Option<JsonRemovalSelection> {
        self.json_pointer
            .clone()
            .map(|pointer| JsonRemovalSelection { pointer, expected })
    }
}

impl fmt::Debug for RemovalMember {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RemovalMember")
            .field("instance_id", &self.instance_id)
            .field("strategy", &self.strategy)
            .field("snapshot_identity", &self.snapshot_identity)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct RemovalEffect {
    dedicated_file_deletions: usize,
    shared_json_entry_removals: usize,
}

impl RemovalEffect {
    pub(crate) fn from_members(members: &[RemovalMember]) -> Self {
        let mut effect = Self::default();
        for member in members {
            match member.strategy {
                RemovalStrategy::JsonPointerRemovalV1 => effect.shared_json_entry_removals += 1,
                RemovalStrategy::DedicatedFileRemovalV1 => effect.dedicated_file_deletions += 1,
            }
        }
        effect
    }

    pub(crate) const fn dedicated_file_deletions(self) -> usize {
        self.dedicated_file_deletions
    }

    pub(crate) const fn shared_json_entry_removals(self) -> usize {
        self.shared_json_entry_removals
    }
}

#[derive(Clone)]
pub(crate) struct RemovalPlan {
    plan_id: String,
    digest: String,
    snapshot_id: String,
    members: Vec<RemovalMember>,
    effect: RemovalEffect,
}

impl RemovalPlan {
    pub(crate) fn new(
        plan_id: impl Into<String>,
        digest: impl Into<String>,
        snapshot_id: impl Into<String>,
        members: Vec<RemovalMember>,
    ) -> Self {
        let effect = RemovalEffect::from_members(&members);
        Self {
            plan_id: plan_id.into(),
            digest: digest.into(),
            snapshot_id: snapshot_id.into(),
            members,
            effect,
        }
    }

    pub(crate) fn plan_id(&self) -> &str {
        &self.plan_id
    }

    pub(crate) fn digest(&self) -> &str {
        &self.digest
    }

    pub(crate) fn snapshot_id(&self) -> &str {
        &self.snapshot_id
    }

    pub(crate) fn members(&self) -> &[RemovalMember] {
        &self.members
    }

    pub(crate) const fn effect(&self) -> RemovalEffect {
        self.effect
    }
}

impl fmt::Debug for RemovalPlan {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RemovalPlan")
            .field("plan_id", &self.plan_id)
            .field("snapshot_id", &self.snapshot_id)
            .field("member_count", &self.members.len())
            .field("effect", &self.effect)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RemovalGroupStatus {
    Applied,
    FailedUnchanged,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RemovalGroupOutcome {
    status: RemovalGroupStatus,
    member_count: usize,
    failure_code: Option<&'static str>,
}

impl RemovalGroupOutcome {
    pub(crate) const fn applied(member_count: usize) -> Self {
        Self {
            status: RemovalGroupStatus::Applied,
            member_count,
            failure_code: None,
        }
    }

    pub(crate) const fn failed_unchanged(member_count: usize, failure_code: &'static str) -> Self {
        Self {
            status: RemovalGroupStatus::FailedUnchanged,
            member_count,
            failure_code: Some(failure_code),
        }
    }

    pub(crate) const fn status(&self) -> RemovalGroupStatus {
        self.status
    }

    pub(crate) const fn member_count(&self) -> usize {
        self.member_count
    }

    pub(crate) const fn failure_code(&self) -> Option<&'static str> {
        self.failure_code
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct RemovalOutcome {
    groups: Vec<RemovalGroupOutcome>,
}

impl RemovalOutcome {
    pub(crate) fn new(groups: Vec<RemovalGroupOutcome>) -> Self {
        Self { groups }
    }

    pub(crate) fn groups(&self) -> &[RemovalGroupOutcome] {
        &self.groups
    }

    pub(crate) fn applied_group_count(&self) -> usize {
        self.groups
            .iter()
            .filter(|group| group.status == RemovalGroupStatus::Applied)
            .count()
    }
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct JsonPointer {
    segments: Vec<String>,
}

impl JsonPointer {
    pub(crate) fn parse(locator: &str) -> Result<Self, JsonRemovalError> {
        if locator == "#" {
            return Ok(Self {
                segments: Vec::new(),
            });
        }
        let suffix = locator
            .strip_prefix("#/")
            .ok_or(JsonRemovalError::InvalidJsonPointer)?;
        let segments = suffix
            .split('/')
            .map(decode_pointer_segment)
            .collect::<Result<Vec<_>, _>>()?;
        Ok(Self { segments })
    }

    const fn is_root(&self) -> bool {
        self.segments.is_empty()
    }
}

impl fmt::Debug for JsonPointer {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("JsonPointer")
            .field("segment_count", &self.segments.len())
            .finish()
    }
}

#[derive(Clone)]
pub(crate) struct ExpectedJsonValue(Value);

impl ExpectedJsonValue {
    pub(crate) fn new(value: Value) -> Self {
        Self(value)
    }

    fn matches(&self, actual: &Value) -> bool {
        self.0 == *actual
    }
}

impl fmt::Debug for ExpectedJsonValue {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExpectedJsonValue")
            .field("kind", &json_kind(&self.0))
            .finish()
    }
}

#[derive(Clone)]
pub(crate) struct JsonRemovalSelection {
    pointer: JsonPointer,
    expected: ExpectedJsonValue,
}

impl JsonRemovalSelection {
    pub(crate) fn new(pointer: JsonPointer, expected: ExpectedJsonValue) -> Self {
        Self { pointer, expected }
    }
}

impl fmt::Debug for JsonRemovalSelection {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("JsonRemovalSelection")
            .field("pointer", &self.pointer)
            .field("expected", &self.expected)
            .finish()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum JsonRemovalError {
    InvalidJsonPointer,
    JsonRootRemovalDisallowed,
    JsonSourceNotObject,
    JsonPointerNotFound,
    JsonPointerExpectedValueMismatch,
    JsonPointerOverlap,
    JsonResultSemanticMismatch,
}

impl JsonRemovalError {
    pub(crate) const fn code(self) -> &'static str {
        match self {
            Self::InvalidJsonPointer => "invalid_json_pointer",
            Self::JsonRootRemovalDisallowed => "json_root_removal_disallowed",
            Self::JsonSourceNotObject => "json_source_not_object",
            Self::JsonPointerNotFound => "json_pointer_not_found",
            Self::JsonPointerExpectedValueMismatch => "json_pointer_expected_value_mismatch",
            Self::JsonPointerOverlap => "json_pointer_overlap",
            Self::JsonResultSemanticMismatch => "json_result_semantic_mismatch",
        }
    }
}

/// Validates every selected node before any caller-visible mutation occurs.
pub(crate) fn validate_json_removal(
    source: &Value,
    selections: &[JsonRemovalSelection],
) -> Result<(), JsonRemovalError> {
    if !source.is_object() {
        return Err(JsonRemovalError::JsonSourceNotObject);
    }
    validate_selection_shape(selections)?;
    for selection in selections {
        let actual = resolve_json_pointer(source, &selection.pointer)
            .ok_or(JsonRemovalError::JsonPointerNotFound)?;
        if !selection.expected.matches(actual) {
            return Err(JsonRemovalError::JsonPointerExpectedValueMismatch);
        }
    }
    Ok(())
}

/// Removes the selected entries using deepest-first ordering and descending
/// indices for entries that share an array parent.
pub(crate) fn apply_json_removal(
    source: &mut Value,
    selections: &[JsonRemovalSelection],
) -> Result<(), JsonRemovalError> {
    validate_json_removal(source, selections)?;
    let mut ordered = selections.iter().collect::<Vec<_>>();
    ordered.sort_by(|left, right| pointer_removal_order(&left.pointer, &right.pointer));
    for selection in ordered {
        remove_json_pointer(source, &selection.pointer)?;
    }
    Ok(())
}

/// Checks the semantic postcondition after a caller has serialized and parsed
/// the rewritten JSON. Formatting and object-key layout are intentionally not
/// part of this comparison.
pub(crate) fn verify_json_removal_semantics(
    original: &Value,
    rewritten: &Value,
    selections: &[JsonRemovalSelection],
) -> Result<(), JsonRemovalError> {
    let mut expected = original.clone();
    apply_json_removal(&mut expected, selections)?;
    if !rewritten.is_object() {
        return Err(JsonRemovalError::JsonSourceNotObject);
    }
    if expected != *rewritten {
        return Err(JsonRemovalError::JsonResultSemanticMismatch);
    }
    Ok(())
}

fn validate_selection_shape(selections: &[JsonRemovalSelection]) -> Result<(), JsonRemovalError> {
    for (index, selection) in selections.iter().enumerate() {
        if selection.pointer.is_root() {
            return Err(JsonRemovalError::JsonRootRemovalDisallowed);
        }
        for other in &selections[index + 1..] {
            if pointers_overlap(&selection.pointer, &other.pointer) {
                return Err(JsonRemovalError::JsonPointerOverlap);
            }
        }
    }
    Ok(())
}

fn pointers_overlap(left: &JsonPointer, right: &JsonPointer) -> bool {
    let shared = left.segments.len().min(right.segments.len());
    left.segments[..shared] == right.segments[..shared]
}

fn pointer_removal_order(left: &JsonPointer, right: &JsonPointer) -> Ordering {
    let depth_order = right.segments.len().cmp(&left.segments.len());
    if depth_order != Ordering::Equal {
        return depth_order;
    }

    let same_parent =
        left.segments[..left.segments.len() - 1] == right.segments[..right.segments.len() - 1];
    if same_parent {
        let left_index = parse_array_index(left.segments.last().expect("non-root pointer"));
        let right_index = parse_array_index(right.segments.last().expect("non-root pointer"));
        if let (Some(left_index), Some(right_index)) = (left_index, right_index) {
            return right_index.cmp(&left_index);
        }
    }
    left.segments.cmp(&right.segments)
}

fn remove_json_pointer(source: &mut Value, pointer: &JsonPointer) -> Result<(), JsonRemovalError> {
    let (leaf, parent_segments) = pointer
        .segments
        .split_last()
        .ok_or(JsonRemovalError::JsonRootRemovalDisallowed)?;
    let parent = resolve_json_pointer_mut(source, parent_segments)
        .ok_or(JsonRemovalError::JsonPointerNotFound)?;
    match parent {
        Value::Object(object) => object
            .remove(leaf)
            .map(|_| ())
            .ok_or(JsonRemovalError::JsonPointerNotFound),
        Value::Array(array) => {
            let index = parse_array_index(leaf).ok_or(JsonRemovalError::JsonPointerNotFound)?;
            if index >= array.len() {
                return Err(JsonRemovalError::JsonPointerNotFound);
            }
            array.remove(index);
            Ok(())
        }
        _ => Err(JsonRemovalError::JsonPointerNotFound),
    }
}

fn resolve_json_pointer<'a>(source: &'a Value, pointer: &JsonPointer) -> Option<&'a Value> {
    resolve_json_pointer_segments(source, &pointer.segments)
}

fn resolve_json_pointer_mut<'a>(
    source: &'a mut Value,
    segments: &[String],
) -> Option<&'a mut Value> {
    let mut current = source;
    for segment in segments {
        current = match current {
            Value::Object(object) => object.get_mut(segment)?,
            Value::Array(array) => array.get_mut(parse_array_index(segment)?)?,
            _ => return None,
        };
    }
    Some(current)
}

fn resolve_json_pointer_segments<'a>(source: &'a Value, segments: &[String]) -> Option<&'a Value> {
    let mut current = source;
    for segment in segments {
        current = match current {
            Value::Object(object) => object.get(segment)?,
            Value::Array(array) => array.get(parse_array_index(segment)?)?,
            _ => return None,
        };
    }
    Some(current)
}

fn parse_array_index(segment: &str) -> Option<usize> {
    if segment.is_empty() || (segment.len() > 1 && segment.starts_with('0')) {
        return None;
    }
    segment.parse().ok()
}

fn decode_pointer_segment(segment: &str) -> Result<String, JsonRemovalError> {
    let mut decoded = String::with_capacity(segment.len());
    let mut characters = segment.chars();
    while let Some(character) = characters.next() {
        if character != '~' {
            decoded.push(character);
            continue;
        }
        match characters.next() {
            Some('0') => decoded.push('~'),
            Some('1') => decoded.push('/'),
            _ => return Err(JsonRemovalError::InvalidJsonPointer),
        }
    }
    Ok(decoded)
}

fn json_kind(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::contexts::local::domain::FileIdentityType;

    fn selection(pointer: &str, expected: Value) -> JsonRemovalSelection {
        JsonRemovalSelection::new(
            JsonPointer::parse(pointer).expect("test pointer is valid"),
            ExpectedJsonValue::new(expected),
        )
    }

    fn handle(parser_id: ParserId, locator: Option<&str>) -> InstanceHandle {
        InstanceHandle {
            canonical_root: "/local-only".into(),
            root_device: 1,
            root_inode: 2,
            raw_relative_components: vec![b"source.json".to_vec()],
            config_entry_locator: locator.map(str::to_owned),
            source_parser_id: parser_id,
            scan_content_sha256: Some([3; 32]),
            scan_file_identity: FileIdentity {
                device: 1,
                inode: 2,
                file_type: FileIdentityType::Regular,
                mode: 0o600,
                owner_uid: 1,
                owner_gid: 1,
                link_count: 1,
                size: 3,
                mtime_ns: 4,
                ctime_ns: 5,
            },
        }
    }

    #[test]
    fn qualification_only_accepts_the_v1_policy_table() {
        let json_member = RemovalMember::qualify(
            "opaque-instance",
            SurfaceKind::Hook,
            &handle(ParserId::HookJsonV1, Some("#/hooks/0")),
        )
        .unwrap();
        assert_eq!(
            json_member.strategy(),
            RemovalStrategy::JsonPointerRemovalV1
        );
        assert!(json_member
            .json_selection(ExpectedJsonValue::new(json!("hook")))
            .is_some());

        let dedicated = RemovalMember::qualify(
            "opaque-instance",
            SurfaceKind::Skill,
            &handle(ParserId::SkillFrontmatterV1, None),
        )
        .unwrap();
        assert_eq!(
            dedicated.strategy(),
            RemovalStrategy::DedicatedFileRemovalV1
        );
        assert!(dedicated
            .json_selection(ExpectedJsonValue::new(json!("unused")))
            .is_none());

        assert!(matches!(
            RemovalMember::qualify(
                "opaque-instance",
                SurfaceKind::Agent,
                &handle(ParserId::SkillFrontmatterV1, None),
            ),
            Err(RemovalBlock::UnsupportedRemovalShape)
        ));
    }

    #[test]
    fn removes_same_array_entries_in_descending_index_order() {
        let original = json!({
            "hooks": ["zero", "one", "two", "three"],
            "unrelated": { "preserved": true }
        });
        let selections = vec![
            selection("#/hooks/0", json!("zero")),
            selection("#/hooks/2", json!("two")),
        ];
        let mut rewritten = original.clone();

        apply_json_removal(&mut rewritten, &selections).unwrap();

        assert_eq!(
            rewritten,
            json!({
                "hooks": ["one", "three"],
                "unrelated": { "preserved": true }
            })
        );
        verify_json_removal_semantics(&original, &rewritten, &selections).unwrap();
    }

    #[test]
    fn rejects_overlapping_selection_before_mutating_source() {
        let mut source = json!({ "hooks": [{ "name": "one" }] });
        let original = source.clone();
        let selections = vec![
            selection("#/hooks/0", json!({ "name": "one" })),
            selection("#/hooks/0/name", json!("one")),
        ];

        assert_eq!(
            apply_json_removal(&mut source, &selections),
            Err(JsonRemovalError::JsonPointerOverlap)
        );
        assert_eq!(source, original);
    }

    #[test]
    fn detects_expected_value_or_non_selected_semantic_changes() {
        let original = json!({ "hooks": ["one"], "unrelated": 1 });
        let selections = vec![selection("#/hooks/0", json!("one"))];

        assert_eq!(
            validate_json_removal(&original, &[selection("#/hooks/0", json!("different"))]),
            Err(JsonRemovalError::JsonPointerExpectedValueMismatch)
        );
        assert_eq!(
            verify_json_removal_semantics(
                &original,
                &json!({ "hooks": [], "unrelated": 2 }),
                &selections
            ),
            Err(JsonRemovalError::JsonResultSemanticMismatch)
        );
    }

    #[test]
    fn decodes_json_pointer_escapes_without_exposing_them_in_debug_output() {
        let pointer = JsonPointer::parse("#/a~1b/~0key").unwrap();
        let selection = JsonRemovalSelection::new(pointer, ExpectedJsonValue::new(json!(1)));
        let mut source = json!({ "a/b": { "~key": 1 } });

        apply_json_removal(&mut source, &[selection]).unwrap();

        assert_eq!(source, json!({ "a/b": {} }));
    }
}
