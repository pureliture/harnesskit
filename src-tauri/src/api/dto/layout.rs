//! Workspace layout command DTOs.

use serde::{Deserialize, Serialize};

use crate::contexts::layout::{
    WorkspaceLayoutDiagnostic, WorkspaceLayoutState, WorkspaceLayoutUpdate, DEFAULT_LEFT_WIDTH_PX,
    DEFAULT_RIGHT_WIDTH_PX,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceLayoutStateDto {
    pub preferred_left_width_px: u32,
    pub preferred_right_width_px: u32,
    pub left_collapsed: bool,
    pub right_collapsed: bool,
    pub revision: u64,
    pub persisted: bool,
}

impl Default for WorkspaceLayoutStateDto {
    fn default() -> Self {
        Self {
            preferred_left_width_px: DEFAULT_LEFT_WIDTH_PX,
            preferred_right_width_px: DEFAULT_RIGHT_WIDTH_PX,
            left_collapsed: false,
            right_collapsed: false,
            revision: 0,
            persisted: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceLayoutDiagnosticDto {
    pub code: String,
    pub safe_message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkspaceLayoutUpdateDto {
    pub workspace_layout: WorkspaceLayoutStateDto,
    pub diagnostic: Option<WorkspaceLayoutDiagnosticDto>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SetWorkspaceLayoutRequestDto {
    pub expected_layout_revision: u64,
    pub preferred_left_width_px: u32,
    pub preferred_right_width_px: u32,
    pub left_collapsed: bool,
    pub right_collapsed: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum LayoutModeDto {
    ThreePane,
    TwoColumn,
    Stacked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PreferredPairDto {
    pub preferred_left_width_px: u32,
    pub preferred_right_width_px: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CollapsedPairDto {
    pub left_collapsed: bool,
    pub right_collapsed: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct FrameDto {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PaneFramesDto {
    pub left: Option<FrameDto>,
    pub center: FrameDto,
    pub right: Option<FrameDto>,
}

impl From<WorkspaceLayoutState> for WorkspaceLayoutStateDto {
    fn from(value: WorkspaceLayoutState) -> Self {
        Self {
            preferred_left_width_px: value.preferred_left_width_px,
            preferred_right_width_px: value.preferred_right_width_px,
            left_collapsed: value.left_collapsed,
            right_collapsed: value.right_collapsed,
            revision: value.revision,
            persisted: value.persisted,
        }
    }
}

impl From<WorkspaceLayoutDiagnostic> for WorkspaceLayoutDiagnosticDto {
    fn from(value: WorkspaceLayoutDiagnostic) -> Self {
        Self {
            code: value.code.to_owned(),
            safe_message: value.safe_message.to_owned(),
        }
    }
}

impl From<WorkspaceLayoutUpdate> for WorkspaceLayoutUpdateDto {
    fn from(value: WorkspaceLayoutUpdate) -> Self {
        Self {
            workspace_layout: value.state.into(),
            diagnostic: value.diagnostic.map(Into::into),
        }
    }
}
