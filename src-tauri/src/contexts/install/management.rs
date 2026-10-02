//! Private source-link adoption and last-verified-baseline protected updates.
use super::coordinator::{InstallCoordinatorError, InstallPreviewInput};
use super::plan::InstallPlan;
use super::writer::{
    read_install_destination, read_private_registration_file, write_registration_file,
};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};

pub(super) struct Adoption {
    pub root: PathBuf,
    pub name: String,
    pub before: Vec<u8>,
    pub links: Vec<serde_json::Value>,
    pub selected: Vec<(usize, String, String, String)>,
}
fn error() -> InstallCoordinatorError {
    InstallCoordinatorError::new("management_record_invalid")
}

pub(super) fn prepare(
    root: &Path,
    input: &InstallPreviewInput,
    plan: &InstallPlan,
) -> Result<Option<Adoption>, InstallCoordinatorError> {
    let name = format!("component-imports-{}.json", input.checkout_id);
    let before = read_private_registration_file(root, &name).map_err(|_| error())?;
    let links: Vec<serde_json::Value> = before
        .as_ref()
        .map(|b| serde_json::from_slice(b))
        .transpose()
        .map_err(|_| error())?
        .unwrap_or_default();
    for artifact in &plan.artifacts {
        if links.iter().any(|l| {
            (l["selected_project_rule"] == true || l["selected_project_whole_file"] == true)
                && l["component_id"].as_str() != Some(&artifact.component_id)
                && l["source_path"]
                    .as_str()
                    .is_some_and(|s| Path::new(s) == input.target_root.join(&artifact.destination))
        }) {
            return Err(InstallCoordinatorError::new(
                "management_destination_collision",
            ));
        }
    }
    let mut selected = vec![];
    for id in &input.selected_component_ids {
        let matches: Vec<_> = links
            .iter()
            .enumerate()
            .filter(|(_, l)| l["component_id"].as_str() == Some(id))
            .collect();
        if matches
            .iter()
            .filter(|(_, l)| {
                l.get("canonical_document").is_none() && l["source_role"] != "registration"
            })
            .count()
            > 1
        {
            return Err(error());
        }
        if matches.is_empty() {
            // A missing private record must not turn an imported component into an ordinary install.
            let registry: serde_yaml::Value = serde_yaml::from_slice(
                &std::fs::read(input.checkout_root.join("components/registry.yml"))
                    .map_err(|_| error())?,
            )
            .map_err(|_| error())?;
            if let Some(path) = registry["components"][id]["path"].as_str() {
                let manifest =
                    std::fs::read_to_string(input.checkout_root.join(path)).map_err(|_| error())?;
                if manifest.contains("Imported standalone agent;")
                    || manifest.contains("Imported standalone skill;")
                    || manifest.contains("Imported shared-setting hook;")
                    || manifest.contains("Imported project managed rule block;")
                    || manifest.contains("Imported Codex project whole file;")
                {
                    return Err(error());
                }
            }
            continue;
        }
        let mut selected_sources = std::collections::BTreeSet::new();
        for (index, link) in matches {
            // Count equality is not a bijection: aliased links could leave a
            // generated file unprotected while advancing another file twice.
            if !selected_sources.insert(link["source_path"].as_str().ok_or_else(error)?) {
                return Err(error());
            }
            for field in ["content_sha256", "source_revision"] {
                if link[field].as_str().is_none_or(|s| {
                    s.len() != 64
                        || !s
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                }) {
                    return Err(error());
                }
            }
            match link["managed"].as_bool() {
                Some(true) => {
                    let baseline = link["last_applied_sha256"].as_str().ok_or_else(error)?;
                    if baseline.len() != 64
                        || !baseline
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                        || link["verified_operation_id"]
                            .as_str()
                            .is_none_or(str::is_empty)
                    {
                        return Err(error());
                    }
                }
                Some(false) if link.get("last_applied_sha256").is_none() => {}
                _ => return Err(error()),
            }
            let artifacts: Vec<_> = plan
                .artifacts
                .iter()
                .filter(|a| &a.component_id == id)
                .collect();
            let source = PathBuf::from(link["source_path"].as_str().ok_or_else(error)?);
            let matching: Vec<_> = artifacts
                .iter()
                .filter(|a| input.target_root.join(&a.destination) == source)
                .collect();
            if (link["selected_project_rule"] == true
                || link["selected_project_whole_file"] == true)
                && links.iter().any(|l| {
                    l["component_id"] != link["component_id"]
                        && l["source_path"] == link["source_path"]
                })
            {
                return Err(InstallCoordinatorError::new(
                    "management_destination_collision",
                ));
            }
            if matching.len() != 1 {
                return Err(error());
            }
            let artifact = *matching[0];
            let shared = link["selected_hook_event"].as_str();
            let valid_kind = if link["selected_project_whole_file"] == true {
                artifact.target == "project"
                    && input.scope == "project"
                    && link["scope"] == "project"
                    && link["tool"] == "codex"
                    && link["project_id"].as_str().is_some_and(|s| !s.is_empty())
                    && link["source_locator"] == "AGENTS.md"
                    && link.get("selected_project_rule").is_none()
                    && link.get("begin_marker").is_none()
                    && link.get("end_marker").is_none()
                    && artifact.destination == "AGENTS.md"
                    && artifact.source == "dist/project/AGENTS.md"
                    && artifact.component_ids == vec![id.clone()]
                    && artifact.merge_strategy.is_none()
                    && artifact.begin_marker.is_none()
                    && artifact.end_marker.is_none()
                    && artifact.json_merge_key.is_none()
                    && artifact.toml_merge_key.is_none()
                    && artifact.ownership
                        == Some(
                            serde_json::json!({"type":"path","merge_policy":"full-path-overwrite"}),
                        )
                    && artifacts.len() == 1
            } else if link["selected_project_rule"] == true {
                artifact.target == "project"
                    && input.scope == "project"
                    && link["project_id"].as_str().is_some_and(|s| !s.is_empty())
                    && matches!(
                        (artifact.destination.as_str(), link["tool"].as_str()),
                        (
                            "AGENTS.md",
                            Some("codex" | "antigravity" | "antigravity_cli")
                        ) | ("CLAUDE.md", Some("claude_code"))
                    )
                    && artifact.merge_strategy.as_deref() == Some("managed-block")
                    && artifact.begin_marker.as_deref() == Some(super::merge::HARNESSKIT_BEGIN)
                    && artifact.end_marker.as_deref() == Some(super::merge::HARNESSKIT_END)
                    && link["begin_marker"] == super::merge::HARNESSKIT_BEGIN
                    && link["end_marker"] == super::merge::HARNESSKIT_END
                    && artifacts.len() == 1
            } else if let Some(name) = link["selected_codex_agent"].as_str() {
                artifact.target == "codex"
                    && link["tool"] == "codex"
                    && link["source_role"] == "registration"
                    && artifact.merge_strategy.as_deref() == Some("toml-agents-merge")
                    && artifact
                        .ownership
                        .as_ref()
                        .is_some_and(|o| o["selected_codex_agent"] == name)
            } else if let Some(event) = shared {
                artifact.target == "claude"
                    && link["tool"] == "claude_code"
                    && artifact.merge_strategy.as_deref() == Some("json-deep-merge")
                    && artifact
                        .ownership
                        .as_ref()
                        .is_some_and(|o| o["selected_hook_event"] == event)
            } else {
                artifact.merge_strategy.is_none()
                    && matches!(
                        (artifact.target.as_str(), link["tool"].as_str()),
                        ("codex", Some("codex"))
                            | ("claude", Some("claude_code"))
                            | ("antigravity", Some("antigravity"))
                            | ("antigravity-cli", Some("antigravity_cli"))
                    )
            };
            if !valid_kind
                || link["scope"].as_str() != Some(input.scope.as_str())
                || input.target_root.join(&artifact.destination) != source
            {
                return Err(error());
            }
            let bytes = read_install_destination(&input.target_root, &artifact.destination)
                .map_err(|_| error())?;
            let hash = managed_hash(link, &bytes)?;
            selected.push((
                index,
                artifact.target.clone(),
                artifact.destination.clone(),
                hash,
            ));
        }
        if plan
            .artifacts
            .iter()
            .filter(|a| &a.component_id == id)
            .count()
            != links
                .iter()
                .filter(|l| l["component_id"].as_str() == Some(id))
                .count()
        {
            return Err(error());
        }
    }
    if selected.is_empty() {
        return Ok(None);
    }
    Ok(Some(Adoption {
        root: root.into(),
        name,
        before: before.ok_or_else(error)?,
        links,
        selected,
    }))
}
pub(super) fn managed_bytes(
    link: &serde_json::Value,
    bytes: &[u8],
) -> Result<Vec<u8>, InstallCoordinatorError> {
    if link["selected_project_rule"] == true {
        super::merge::selected_project_rule(bytes)
            .map(String::into_bytes)
            .map_err(|_| InstallCoordinatorError::new("management_item_ambiguous"))
    } else if let Some(name) = link["selected_codex_agent"].as_str() {
        let item = super::merge::selected_codex_agent(bytes, name)
            .map_err(|_| InstallCoordinatorError::new("management_item_ambiguous"))?;
        serde_json::to_vec(&item).map_err(|_| error())
    } else if let Some(event) = link["selected_hook_event"].as_str() {
        let doc = serde_json::from_slice(bytes).map_err(|_| error())?;
        let item = super::merge::selected_hook(&doc, event)
            .map_err(|_| InstallCoordinatorError::new("management_item_ambiguous"))?;
        serde_json::to_vec(&item).map_err(|_| error())
    } else {
        Ok(bytes.to_vec())
    }
}
pub(super) fn managed_hash(
    link: &serde_json::Value,
    bytes: &[u8],
) -> Result<String, InstallCoordinatorError> {
    Ok(format!("{:x}", Sha256::digest(managed_bytes(link, bytes)?)))
}
impl Adoption {
    pub(super) fn authorizes_whole_file(&self, artifact: &super::plan::PlanArtifact) -> bool {
        artifact.target == "project"
            && artifact.destination == "AGENTS.md"
            && artifact.source == "dist/project/AGENTS.md"
            && artifact.component_ids == vec![artifact.component_id.clone()]
            && artifact.merge_strategy.is_none()
            && artifact.begin_marker.is_none()
            && artifact.end_marker.is_none()
            && artifact.json_merge_key.is_none()
            && artifact.toml_merge_key.is_none()
            && artifact.ownership
                == Some(serde_json::json!({"type":"path","merge_policy":"full-path-overwrite"}))
            && self.selected.iter().any(|(i, target, destination, _)| {
                let link = &self.links[*i];
                link["selected_project_whole_file"] == true
                    && link["tool"] == "codex"
                    && link["scope"] == "project"
                    && link["component_id"] == artifact.component_id
                    && target == &artifact.target
                    && destination == &artifact.destination
            })
    }
    pub fn requires_replacement(&self) -> bool {
        self.selected.iter().any(|(i, _, _, hash)| {
            self.links[*i]["managed"] == true
                && self.links[*i]["last_applied_sha256"].as_str() != Some(hash.as_str())
        })
    }
    pub fn review_label(&self, index: usize, hash: &str) -> &'static str {
        if self.links[index]["managed"] == false {
            if self.links[index]["selected_project_whole_file"] == true {
                "파일 전체 관리 전환"
            } else {
                "관리 전환"
            }
        } else if self.links[index]["last_applied_sha256"].as_str() != Some(hash) {
            "외부 수정: 교체 승인 전 적용 중단"
        } else {
            "관리 업데이트: 외부 수정 없음"
        }
    }
    pub fn revalidate(&self, target: &Path) -> Result<(), InstallCoordinatorError> {
        if read_private_registration_file(&self.root, &self.name)
            .map_err(|_| error())?
            .as_deref()
            != Some(self.before.as_slice())
        {
            return Err(InstallCoordinatorError::new("preview_stale"));
        }
        for (index, _, destination, hash) in &self.selected {
            let bytes = read_install_destination(target, destination)
                .map_err(|_| InstallCoordinatorError::new("preview_stale"))?;
            if managed_hash(&self.links[*index], &bytes)? != *hash {
                return Err(InstallCoordinatorError::new("preview_stale"));
            }
        }
        Ok(())
    }
    pub fn persist(
        mut self,
        outcomes: &[super::coordinator::InstallDestinationResult],
        verified: &[super::writer::VerifyDestinationOutcome],
        operation: &str,
    ) -> Result<(), InstallCoordinatorError> {
        let mut changed = false;
        for (index, tool, destination, _) in &self.selected {
            if outcomes.iter().any(|o| {
                &o.target == tool
                    && &o.destination == destination
                    && o.verify_state == super::writer::DestinationVerifyState::Verified
                    && matches!(
                        o.apply_state,
                        super::writer::DestinationApplyState::Applied
                            | super::writer::DestinationApplyState::Unchanged
                    )
            }) {
                let hash = verified
                    .iter()
                    .find(|v| {
                        &v.target == tool
                            && &v.destination == destination
                            && v.state == super::writer::DestinationVerifyState::Verified
                    })
                    .and_then(|v| v.content_sha256.clone())
                    .ok_or_else(error)?;
                let hash = if self.links[*index]["selected_hook_event"].is_string()
                    || self.links[*index]["selected_codex_agent"].is_string()
                    || self.links[*index]["selected_project_rule"] == true
                {
                    let path = PathBuf::from(
                        self.links[*index]["source_path"]
                            .as_str()
                            .ok_or_else(error)?,
                    );
                    let locator = self.links[*index]["source_locator"]
                        .as_str()
                        .ok_or_else(error)?;
                    let mut root = path.clone();
                    for _ in Path::new(locator).components() {
                        root.pop();
                    }
                    let bytes = read_install_destination(&root, locator).map_err(|_| error())?;
                    if format!("{:x}", Sha256::digest(&bytes)) != hash {
                        return Err(error());
                    }
                    managed_hash(&self.links[*index], &bytes)?
                } else {
                    hash
                };
                self.links[*index]["managed"] = true.into();
                self.links[*index]["last_applied_sha256"] = hash.into();
                self.links[*index]["verified_operation_id"] = operation.into();
                changed = true;
            }
        }
        if changed {
            // Private-state validation must also hold after destination verification, not only at preview.
            if read_private_registration_file(&self.root, &self.name)
                .map_err(|_| InstallCoordinatorError::new("management_state_write_failed"))?
                .as_deref()
                != Some(self.before.as_slice())
            {
                return Err(InstallCoordinatorError::new(
                    "management_state_write_failed",
                ));
            }
            let after = serde_json::to_vec(&self.links).map_err(|_| error())?;
            write_registration_file(&self.root, &self.name, Some(&self.before), &after, 0o600)
                .map_err(|_| InstallCoordinatorError::new("management_state_write_failed"))?;
        }
        Ok(())
    }
}
