use std::fs;
use std::path::PathBuf;

fn source(relative: &str) -> String {
    fs::read_to_string(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(relative)).unwrap_or_default()
}

#[test]
fn workspace_cleanup_and_ffi_preserve_the_declared_msrv_and_fd_authority() {
    let workspace = source("src/contexts/install/workspace.rs");

    assert!(!workspace.contains("unsafe extern \"C\""));
    assert!(workspace.contains("parent_fd"));
    assert!(workspace.contains("root_name"));
    assert!(!workspace.contains("remove_dir_all(&self.root)"));
    assert!(workspace.contains("pub(crate) fn root_fd"));
}

#[test]
fn runtime_declares_sealed_bundle_stage_and_bounded_process_gates() {
    let runtime = source("src/contexts/install/runtime.rs");

    for declaration in [
        "pub struct BoundedPlanProcess",
        "from_embedded_manifest",
        "revalidate_spawn_authority",
        "validate_stage_transition",
        "manifest_sha256",
        "target_triple",
    ] {
        assert!(
            runtime.contains(declaration),
            "missing hardening seam: {}",
            declaration
        );
    }
}
