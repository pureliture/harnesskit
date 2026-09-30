//! App-wide workspace layout bounded context.

mod context;
mod geometry;
mod store;

pub use context::{
    WorkspaceLayoutContext, WorkspaceLayoutContextError, WorkspaceLayoutState,
    WorkspaceLayoutUpdate,
};
pub(crate) use geometry::{
    workspace_layout_probe_matches, WorkspaceLayoutFrame, WorkspaceLayoutGeometryProbe,
    WorkspaceLayoutMode, WorkspaceLayoutPaneFrames,
};
pub use store::{
    LoadedWorkspaceLayoutPreference, WorkspaceLayoutDiagnostic, WorkspaceLayoutPreferencePort,
    WorkspaceLayoutStore, WorkspaceLayoutStoreError, DEFAULT_LEFT_WIDTH_PX, DEFAULT_RIGHT_WIDTH_PX,
    MAX_LEFT_WIDTH_PX, MAX_RIGHT_WIDTH_PX, MIN_LEFT_WIDTH_PX, MIN_RIGHT_WIDTH_PX,
};
