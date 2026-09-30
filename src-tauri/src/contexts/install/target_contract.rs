use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::sync::OnceLock;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

pub const EMBEDDED_TARGET_CONTRACT_ID: &str = "harnesskit.install-target-contract.v1";
pub const EMBEDDED_TARGET_CONTRACT_SHA256: &str =
    "de887f021dd5322f99016490be574c0f7ab08cb7c77e5e7ec5e78cbb54ded7a4";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ContractError {
    InvalidJson(String),
    InvalidContract(String),
    HashMismatch { expected: String, actual: String },
}

impl ContractError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::InvalidJson(_) => "invalid_contract_json",
            Self::InvalidContract(_) => "invalid_contract",
            Self::HashMismatch { .. } => "contract_hash_mismatch",
        }
    }
}

impl fmt::Display for ContractError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidJson(message) | Self::InvalidContract(message) => {
                formatter.write_str(message)
            }
            Self::HashMismatch { expected, actual } => {
                write!(
                    formatter,
                    "embedded contract hash mismatch: {expected} != {actual}"
                )
            }
        }
    }
}

impl std::error::Error for ContractError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RuntimeSurfaceKind {
    Directory,
    File,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeSurfaceContract {
    pub prefix: String,
    pub source: String,
    pub kind: RuntimeSurfaceKind,
}

impl RuntimeSurfaceContract {
    pub fn contains(&self, destination: &str) -> bool {
        destination == self.prefix
            || (self.kind == RuntimeSurfaceKind::Directory
                && destination
                    .strip_prefix(&self.prefix)
                    .is_some_and(|suffix| suffix.starts_with('/')))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TargetContract {
    pub id: String,
    pub scopes: Vec<String>,
    pub runtime_surfaces: Vec<RuntimeSurfaceContract>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CorrelationBindingContract {
    pub target: String,
    pub scope: String,
    pub tool_id: String,
    pub surface_id: String,
    #[serde(default)]
    pub authorized_root_prefix: Option<String>,
    pub locator_mode: String,
    pub content_match: String,
    pub runtime_surface_prefixes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceEquivalence {
    pub target: String,
    pub destination: String,
    pub source: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MergeDestinationContract {
    pub target: String,
    pub destination: String,
    pub strategy: String,
    #[serde(default)]
    pub merge_key: Option<String>,
    #[serde(default)]
    pub begin_marker: Option<String>,
    #[serde(default)]
    pub end_marker: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileModePolicy {
    pub minimum: u32,
    pub maximum: u32,
    pub required_group_or_other_read_mask: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InstallTargetContract {
    pub contract_id: String,
    pub version: u32,
    pub plan_modes: Vec<String>,
    pub file_mode_policy: FileModePolicy,
    pub targets: Vec<TargetContract>,
    pub correlation_bindings: Vec<CorrelationBindingContract>,
    pub source_equivalences: Vec<SourceEquivalence>,
    pub merge_destinations: Vec<MergeDestinationContract>,
}

impl InstallTargetContract {
    pub fn target(&self, target_id: &str) -> Option<&TargetContract> {
        self.targets.iter().find(|target| target.id == target_id)
    }

    pub fn runtime_surface(
        &self,
        target_id: &str,
        destination: &str,
    ) -> Option<&RuntimeSurfaceContract> {
        self.target(target_id)?
            .runtime_surfaces
            .iter()
            .filter(|surface| surface.contains(destination))
            .max_by_key(|surface| surface.prefix.len())
    }

    pub fn correlation_bindings(
        &self,
        target_id: &str,
        scope: &str,
        destination: &str,
    ) -> Vec<&CorrelationBindingContract> {
        let mut matches = self
            .correlation_bindings
            .iter()
            .filter(|binding| binding.target == target_id && binding.scope == scope)
            .filter_map(|binding| {
                let matched_prefix_length = binding
                    .runtime_surface_prefixes
                    .iter()
                    .filter_map(|prefix| {
                        self.runtime_surface(target_id, prefix)
                            .filter(|surface| surface.prefix == *prefix)
                            .filter(|surface| surface.contains(destination))
                            .map(|surface| surface.prefix.len())
                    })
                    .max()?;
                Some((matched_prefix_length, binding))
            })
            .collect::<Vec<_>>();
        let Some(longest) = matches
            .iter()
            .map(|(matched_prefix_length, _)| *matched_prefix_length)
            .max()
        else {
            return Vec::new();
        };
        matches.retain(|(matched_prefix_length, _)| *matched_prefix_length == longest);
        matches.sort_by(|(_, left), (_, right)| {
            left.tool_id
                .cmp(&right.tool_id)
                .then_with(|| left.surface_id.cmp(&right.surface_id))
                .then_with(|| {
                    left.authorized_root_prefix
                        .cmp(&right.authorized_root_prefix)
                })
        });
        matches.into_iter().map(|(_, binding)| binding).collect()
    }

    pub fn source_is_equivalent(&self, target_id: &str, destination: &str, source: &str) -> bool {
        self.source_equivalences.iter().any(|equivalence| {
            equivalence.target == target_id
                && equivalence.destination == destination
                && equivalence.source == source
        })
    }

    pub fn merge_destination(
        &self,
        target_id: &str,
        destination: &str,
    ) -> Option<&MergeDestinationContract> {
        self.merge_destinations
            .iter()
            .find(|contract| contract.target == target_id && contract.destination == destination)
    }
}

pub fn embedded_target_contract_json() -> &'static str {
    include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../schemas/install-target-contract-v1.json"
    ))
}

pub fn embedded_target_contract() -> Result<&'static InstallTargetContract, ContractError> {
    static CONTRACT: OnceLock<Result<InstallTargetContract, ContractError>> = OnceLock::new();
    match CONTRACT.get_or_init(load_embedded_contract) {
        Ok(contract) => Ok(contract),
        Err(error) => Err(error.clone()),
    }
}

pub fn embedded_target_contract_hash() -> Result<&'static str, ContractError> {
    embedded_target_contract()?;
    Ok(EMBEDDED_TARGET_CONTRACT_SHA256)
}

fn load_embedded_contract() -> Result<InstallTargetContract, ContractError> {
    let raw_value: Value = serde_json::from_str(embedded_target_contract_json())
        .map_err(|error| ContractError::InvalidJson(error.to_string()))?;
    let actual_hash = canonical_value_sha256(&raw_value)?;
    if actual_hash != EMBEDDED_TARGET_CONTRACT_SHA256 {
        return Err(ContractError::HashMismatch {
            expected: EMBEDDED_TARGET_CONTRACT_SHA256.to_string(),
            actual: actual_hash,
        });
    }

    let contract: InstallTargetContract = serde_json::from_value(raw_value)
        .map_err(|error| ContractError::InvalidContract(error.to_string()))?;
    validate_contract(&contract)?;
    Ok(contract)
}

fn canonical_value_sha256(value: &Value) -> Result<String, ContractError> {
    let canonical = canonicalize_value(value);
    let bytes = serde_json::to_vec(&canonical)
        .map_err(|error| ContractError::InvalidJson(error.to_string()))?;
    Ok(format!("{:x}", Sha256::digest(bytes)))
}

fn canonicalize_value(value: &Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(canonicalize_value).collect()),
        Value::Object(object) => {
            let sorted: BTreeMap<_, _> = object
                .iter()
                .map(|(key, value)| (key.clone(), canonicalize_value(value)))
                .collect();
            Value::Object(sorted.into_iter().collect())
        }
        _ => value.clone(),
    }
}

fn validate_contract(contract: &InstallTargetContract) -> Result<(), ContractError> {
    if contract.contract_id != EMBEDDED_TARGET_CONTRACT_ID || contract.version != 1 {
        return Err(ContractError::InvalidContract(
            "unexpected embedded contract identity".to_string(),
        ));
    }
    if contract.file_mode_policy.maximum > 0o777
        || contract.file_mode_policy.minimum > contract.file_mode_policy.maximum
    {
        return Err(ContractError::InvalidContract(
            "invalid embedded file mode policy".to_string(),
        ));
    }
    let modes: BTreeSet<_> = contract.plan_modes.iter().map(String::as_str).collect();
    if modes != BTreeSet::from(["apply", "dry-run"]) {
        return Err(ContractError::InvalidContract(
            "embedded plan modes must be apply and dry-run".to_string(),
        ));
    }

    let mut target_ids = BTreeSet::new();
    let mut surface_keys = BTreeSet::new();
    for target in &contract.targets {
        if !target_ids.insert(target.id.as_str()) {
            return Err(ContractError::InvalidContract(format!(
                "duplicate target: {}",
                target.id
            )));
        }
        if target.scopes.is_empty()
            || target
                .scopes
                .iter()
                .any(|scope| scope != "project" && scope != "user")
        {
            return Err(ContractError::InvalidContract(format!(
                "invalid target scopes: {}",
                target.id
            )));
        }
        for surface in &target.runtime_surfaces {
            validate_relative_path(&surface.prefix)?;
            validate_relative_path(&surface.source)?;
            if !surface_keys.insert((target.id.as_str(), surface.prefix.as_str())) {
                return Err(ContractError::InvalidContract(format!(
                    "duplicate runtime surface: {}:{}",
                    target.id, surface.prefix
                )));
            }
        }
    }

    let allowed_tool_surfaces = BTreeSet::from([
        ("antigravity", "antigravity_project"),
        ("antigravity", "antigravity_user"),
        ("antigravity_cli", "antigravity_cli_project"),
        ("antigravity_cli", "antigravity_cli_user"),
        ("claude_code", "claude_code_project"),
        ("claude_code", "claude_code_user"),
        ("codex", "codex_project"),
        ("codex", "codex_user"),
        ("hermes", "hermes_project"),
        ("hermes", "hermes_user"),
    ]);
    let mut correlation_surface_keys = BTreeSet::new();
    for binding in &contract.correlation_bindings {
        let target = contract.target(&binding.target).ok_or_else(|| {
            ContractError::InvalidContract(format!(
                "correlation target is unavailable: {}",
                binding.target
            ))
        })?;
        if !target.scopes.iter().any(|scope| scope == &binding.scope)
            || !matches!(binding.scope.as_str(), "project" | "user")
            || !allowed_tool_surfaces
                .contains(&(binding.tool_id.as_str(), binding.surface_id.as_str()))
            || !binding.surface_id.ends_with(&format!("_{}", binding.scope))
            || binding.runtime_surface_prefixes.is_empty()
            || (binding.scope == "user") != binding.authorized_root_prefix.is_some()
            || binding.locator_mode != "destination_relative_to_authorized_root"
            || binding.content_match != "whole_file_sha256"
        {
            return Err(ContractError::InvalidContract(format!(
                "invalid correlation binding: {}:{}:{}",
                binding.target, binding.scope, binding.surface_id
            )));
        }
        if let Some(root_prefix) = &binding.authorized_root_prefix {
            validate_relative_path(root_prefix)?;
        }
        for prefix in &binding.runtime_surface_prefixes {
            let surface = target
                .runtime_surfaces
                .iter()
                .find(|surface| surface.prefix == *prefix)
                .ok_or_else(|| {
                    ContractError::InvalidContract(format!(
                        "correlation surface is unavailable: {}:{}",
                        binding.target, prefix
                    ))
                })?;
            if !correlation_surface_keys.insert((
                binding.target.as_str(),
                binding.scope.as_str(),
                prefix.as_str(),
                binding.tool_id.as_str(),
                binding.surface_id.as_str(),
            )) || binding
                .authorized_root_prefix
                .as_ref()
                .is_some_and(|root_prefix| {
                    surface.prefix != *root_prefix
                        && !surface
                            .prefix
                            .strip_prefix(root_prefix)
                            .is_some_and(|suffix| suffix.starts_with('/'))
                })
            {
                return Err(ContractError::InvalidContract(format!(
                    "invalid correlation surface binding: {}:{}:{}",
                    binding.target, binding.scope, prefix
                )));
            }
        }
    }

    for equivalence in &contract.source_equivalences {
        validate_relative_path(&equivalence.destination)?;
        validate_relative_path(&equivalence.source)?;
        if contract
            .runtime_surface(&equivalence.target, &equivalence.destination)
            .is_none()
        {
            return Err(ContractError::InvalidContract(format!(
                "equivalence destination is outside a surface: {}:{}",
                equivalence.target, equivalence.destination
            )));
        }
    }

    let allowed_strategies =
        BTreeSet::from(["managed-block", "json-deep-merge", "toml-agents-merge"]);
    let mut merge_keys = BTreeSet::new();
    for merge in &contract.merge_destinations {
        if !allowed_strategies.contains(merge.strategy.as_str())
            || !merge_keys.insert((merge.target.as_str(), merge.destination.as_str()))
            || contract
                .runtime_surface(&merge.target, &merge.destination)
                .is_none()
        {
            return Err(ContractError::InvalidContract(format!(
                "invalid merge destination: {}:{}",
                merge.target, merge.destination
            )));
        }
        let marker_pair = merge.begin_marker.is_some() && merge.end_marker.is_some();
        if (merge.strategy == "managed-block") != marker_pair {
            return Err(ContractError::InvalidContract(format!(
                "invalid managed block contract: {}:{}",
                merge.target, merge.destination
            )));
        }
        if (merge.strategy == "managed-block") == merge.merge_key.is_some() {
            return Err(ContractError::InvalidContract(format!(
                "invalid merge key contract: {}:{}",
                merge.target, merge.destination
            )));
        }
    }
    Ok(())
}

fn validate_relative_path(path: &str) -> Result<(), ContractError> {
    if path.is_empty()
        || path.starts_with('/')
        || path.starts_with('~')
        || path.contains('\\')
        || path
            .split('/')
            .any(|segment| segment.is_empty() || segment == "." || segment == "..")
    {
        return Err(ContractError::InvalidContract(format!(
            "invalid contract-relative path: {path}"
        )));
    }
    Ok(())
}
