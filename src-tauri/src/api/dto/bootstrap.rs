//! Composite hidden-first bootstrap DTOs.

use serde::{Deserialize, Serialize};

use super::appearance::{AppearanceDiagnosticDto, AppearanceStateDto};
use super::layout::{
    CollapsedPairDto, LayoutModeDto, PaneFramesDto, PreferredPairDto, WorkspaceLayoutDiagnosticDto,
    WorkspaceLayoutStateDto,
};
use super::typography::{TypographyDiagnosticDto, TypographyPresetDto, TypographyStateDto};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BootstrapStateDto {
    pub appearance: AppearanceStateDto,
    pub workspace_layout: WorkspaceLayoutStateDto,
    pub typography: TypographyStateDto,
    pub app_version: String,
    pub appearance_diagnostic: Option<AppearanceDiagnosticDto>,
    pub workspace_layout_diagnostic: Option<WorkspaceLayoutDiagnosticDto>,
    pub typography_diagnostic: Option<TypographyDiagnosticDto>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BootstrapUiProbeDto {
    pub child_element_count: u32,
    pub text_length: u32,
    pub desktop_app_present: bool,
    pub resolved_mode: String,
    pub viewport_width: f64,
    pub viewport_height: f64,
    pub desktop_width: f64,
    pub desktop_height: f64,
    pub layout_visible: bool,
    pub applied_layout_revision: u64,
    pub applied_typography_revision: u64,
    pub applied_preferred_pair: PreferredPairDto,
    pub applied_collapsed_pair: CollapsedPairDto,
    pub applied_typography_preset: TypographyPresetDto,
    pub layout_mode: LayoutModeDto,
    pub shell_frame: super::layout::FrameDto,
    pub pane_frames: PaneFramesDto,
    pub active_separator_count: u32,
    pub disabled_separator_count: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum BootstrapInstructionDto {
    Show,
    Refresh,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BootstrapCompletionDto {
    pub instruction: BootstrapInstructionDto,
    pub appearance: AppearanceStateDto,
    pub workspace_layout: WorkspaceLayoutStateDto,
    pub typography: TypographyStateDto,
}

impl From<crate::contexts::appearance::BootstrapInstruction> for BootstrapInstructionDto {
    fn from(value: crate::contexts::appearance::BootstrapInstruction) -> Self {
        match value {
            crate::contexts::appearance::BootstrapInstruction::Show => Self::Show,
            crate::contexts::appearance::BootstrapInstruction::Refresh => Self::Refresh,
        }
    }
}
