//! Cross-context projection shell.

use std::os::unix::ffi::OsStrExt;
use std::path::{Component, Path};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DestinationScope {
    User,
    Project,
}

impl DestinationScope {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::Project => "project",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct DestinationKey {
    pub tool_id: String,
    pub surface_id: String,
    pub scope: DestinationScope,
    pub root_identity: String,
    pub normalized_relative_locator: String,
    pub config_entry_locator: Option<String>,
}

pub fn root_identity(device: u64, inode: u64, canonical_root: &Path) -> String {
    tuple_sha256(&[
        b"destination-root-v1",
        &device.to_be_bytes(),
        &inode.to_be_bytes(),
        canonical_root.as_os_str().as_bytes(),
    ])
}

pub fn normalized_relative_locator(relative: &Path) -> Option<String> {
    let components = relative
        .components()
        .map(|component| match component {
            Component::Normal(segment) if !segment.as_bytes().is_empty() => {
                Some(segment.as_bytes())
            }
            _ => None,
        })
        .collect::<Option<Vec<_>>>()?;
    (!components.is_empty()).then(|| {
        format!(
            "relative-locator-v1:{}",
            tuple_sha256(
                &std::iter::once(b"relative-locator-v1".as_slice())
                    .chain(components)
                    .collect::<Vec<_>>()
            )
        )
    })
}

fn tuple_sha256(parts: &[&[u8]]) -> String {
    let mut digest = Sha256::new();
    for part in parts {
        digest.update(part.len().to_be_bytes());
        digest.update(part);
    }
    digest
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}
