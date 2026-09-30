use super::context::WorkspaceLayoutState;
use super::store::{MIN_LEFT_WIDTH_PX, MIN_RIGHT_WIDTH_PX};

const WORKSPACE_CENTER_MIN_WIDTH_PX: f64 = 480.0;
const WORKSPACE_FRAME_BOUND_PX: f64 = 32_768.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WorkspaceLayoutMode {
    ThreePane,
    TwoColumn,
    Stacked,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct WorkspaceLayoutFrame {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct WorkspaceLayoutPaneFrames {
    pub left: Option<WorkspaceLayoutFrame>,
    pub center: WorkspaceLayoutFrame,
    pub right: Option<WorkspaceLayoutFrame>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct WorkspaceLayoutGeometryProbe {
    pub applied_layout_revision: u64,
    pub preferred_left_width_px: u32,
    pub preferred_right_width_px: u32,
    pub left_collapsed: bool,
    pub right_collapsed: bool,
    pub mode: WorkspaceLayoutMode,
    pub shell: WorkspaceLayoutFrame,
    pub panes: WorkspaceLayoutPaneFrames,
    pub active_separator_count: u32,
    pub disabled_separator_count: u32,
}

pub(crate) fn workspace_layout_probe_matches(
    state: WorkspaceLayoutState,
    probe: &WorkspaceLayoutGeometryProbe,
) -> bool {
    if probe.applied_layout_revision != state.revision
        || probe.preferred_left_width_px != state.preferred_left_width_px
        || probe.preferred_right_width_px != state.preferred_right_width_px
        || probe.left_collapsed != state.left_collapsed
        || probe.right_collapsed != state.right_collapsed
        || !frame_is_bounded(probe.shell)
        || !frame_is_bounded(probe.panes.center)
        || !frame_contains(probe.shell, probe.panes.center)
        || probe.panes.center.width < WORKSPACE_CENTER_MIN_WIDTH_PX
        || state.left_collapsed != probe.panes.left.is_none()
        || state.right_collapsed != probe.panes.right.is_none()
        || probe
            .panes
            .left
            .is_some_and(|frame| !frame_is_bounded(frame) || !frame_contains(probe.shell, frame))
        || probe
            .panes
            .right
            .is_some_and(|frame| !frame_is_bounded(frame) || !frame_contains(probe.shell, frame))
    {
        return false;
    }

    let visible_side_count =
        u32::from(probe.panes.left.is_some()) + u32::from(probe.panes.right.is_some());

    match probe.mode {
        WorkspaceLayoutMode::ThreePane => {
            probe.active_separator_count == visible_side_count
                && probe.disabled_separator_count == 0
                && probe.panes.left.is_none_or(|left| {
                    left.width >= f64::from(MIN_LEFT_WIDTH_PX)
                        && same_row(left, probe.panes.center)
                        && frame_precedes_horizontally(left, probe.panes.center)
                })
                && probe.panes.right.is_none_or(|right| {
                    right.width >= f64::from(MIN_RIGHT_WIDTH_PX)
                        && same_row(probe.panes.center, right)
                        && frame_precedes_horizontally(probe.panes.center, right)
                })
        }
        WorkspaceLayoutMode::TwoColumn => {
            probe.active_separator_count == 0
                && probe.disabled_separator_count == visible_side_count
                && probe.panes.left.is_none_or(|left| {
                    left.width >= f64::from(MIN_LEFT_WIDTH_PX)
                        && left.x == probe.shell.x
                        && left.y == probe.shell.y
                        && left.height == probe.shell.height
                        && frame_precedes_horizontally(left, probe.panes.center)
                })
                && probe.panes.right.is_none_or(|right| {
                    right.width >= WORKSPACE_CENTER_MIN_WIDTH_PX
                        && same_column(probe.panes.center, right)
                        && probe.panes.center.y == probe.shell.y
                        && frame_bottom(right) == frame_bottom(probe.shell)
                        && frame_right(right) == frame_right(probe.shell)
                        && frame_precedes_vertically(probe.panes.center, right)
                })
        }
        WorkspaceLayoutMode::Stacked => {
            probe.active_separator_count == 0
                && probe.disabled_separator_count == visible_side_count
                && probe.panes.left.is_none_or(|left| {
                    left.x == probe.shell.x
                        && left.width == probe.shell.width
                        && frame_precedes_vertically(left, probe.panes.center)
                })
                && probe.panes.center.x == probe.shell.x
                && probe.panes.center.width == probe.shell.width
                && probe.panes.right.is_none_or(|right| {
                    right.x == probe.shell.x
                        && right.width == probe.shell.width
                        && frame_precedes_vertically(probe.panes.center, right)
                })
        }
    }
}

fn frame_is_bounded(frame: WorkspaceLayoutFrame) -> bool {
    [frame.x, frame.y, frame.width, frame.height]
        .into_iter()
        .all(|value| {
            value.is_finite()
                && value >= 0.0
                && value <= WORKSPACE_FRAME_BOUND_PX
                && value.fract() == 0.0
        })
        && frame.width > 0.0
        && frame.height > 0.0
}

fn frame_contains(container: WorkspaceLayoutFrame, child: WorkspaceLayoutFrame) -> bool {
    child.x >= container.x
        && child.y >= container.y
        && frame_right(child) <= frame_right(container)
        && frame_bottom(child) <= frame_bottom(container)
}

fn frame_precedes_horizontally(left: WorkspaceLayoutFrame, right: WorkspaceLayoutFrame) -> bool {
    frame_right(left) <= right.x
}

fn frame_precedes_vertically(top: WorkspaceLayoutFrame, bottom: WorkspaceLayoutFrame) -> bool {
    frame_bottom(top) <= bottom.y
}

fn same_row(left: WorkspaceLayoutFrame, right: WorkspaceLayoutFrame) -> bool {
    left.y == right.y && left.height == right.height
}

fn same_column(top: WorkspaceLayoutFrame, bottom: WorkspaceLayoutFrame) -> bool {
    top.x == bottom.x && top.width == bottom.width
}

fn frame_right(frame: WorkspaceLayoutFrame) -> f64 {
    frame.x + frame.width
}

fn frame_bottom(frame: WorkspaceLayoutFrame) -> f64 {
    frame.y + frame.height
}

#[cfg(test)]
mod tests {
    use super::*;

    fn layout_frame(x: f64, y: f64, width: f64, height: f64) -> WorkspaceLayoutFrame {
        WorkspaceLayoutFrame {
            x,
            y,
            width,
            height,
        }
    }

    fn three_pane_layout_probe() -> WorkspaceLayoutGeometryProbe {
        WorkspaceLayoutGeometryProbe {
            applied_layout_revision: 7,
            preferred_left_width_px: 304,
            preferred_right_width_px: 368,
            left_collapsed: false,
            right_collapsed: false,
            mode: WorkspaceLayoutMode::ThreePane,
            shell: layout_frame(0.0, 0.0, 1200.0, 800.0),
            panes: WorkspaceLayoutPaneFrames {
                left: Some(layout_frame(0.0, 0.0, 304.0, 800.0)),
                center: layout_frame(316.0, 0.0, 504.0, 800.0),
                right: Some(layout_frame(832.0, 0.0, 368.0, 800.0)),
            },
            active_separator_count: 2,
            disabled_separator_count: 0,
        }
    }

    fn layout_state() -> WorkspaceLayoutState {
        WorkspaceLayoutState {
            preferred_left_width_px: 304,
            preferred_right_width_px: 368,
            left_collapsed: false,
            right_collapsed: false,
            revision: 7,
            persisted: true,
        }
    }

    #[test]
    fn workspace_layout_bootstrap_probe_accepts_matching_three_pane_geometry() {
        assert!(workspace_layout_probe_matches(
            layout_state(),
            &three_pane_layout_probe()
        ));
    }

    #[test]
    fn workspace_layout_bootstrap_probe_rejects_stale_pair_and_unbounded_frames() {
        let mut probe = three_pane_layout_probe();
        probe.applied_layout_revision = 6;
        assert!(!workspace_layout_probe_matches(layout_state(), &probe));

        let mut probe = three_pane_layout_probe();
        probe.preferred_right_width_px = 400;
        assert!(!workspace_layout_probe_matches(layout_state(), &probe));

        let mut probe = three_pane_layout_probe();
        probe.panes.center.width = f64::NAN;
        assert!(!workspace_layout_probe_matches(layout_state(), &probe));

        let mut probe = three_pane_layout_probe();
        probe.panes.right.as_mut().unwrap().x = 1201.0;
        assert!(!workspace_layout_probe_matches(layout_state(), &probe));
    }

    #[test]
    fn workspace_layout_bootstrap_probe_enforces_mode_geometry_and_separator_counts() {
        let mut probe = three_pane_layout_probe();
        probe.panes.center.width = 479.0;
        assert!(!workspace_layout_probe_matches(layout_state(), &probe));

        let mut probe = three_pane_layout_probe();
        probe.panes.left.as_mut().unwrap().width = 199.0;
        assert!(!workspace_layout_probe_matches(layout_state(), &probe));

        let mut probe = three_pane_layout_probe();
        probe.panes.right.as_mut().unwrap().width = 183.0;
        assert!(!workspace_layout_probe_matches(layout_state(), &probe));

        let mut probe = three_pane_layout_probe();
        probe.panes.right.as_mut().unwrap().x = 800.0;
        assert!(!workspace_layout_probe_matches(layout_state(), &probe));

        let mut probe = three_pane_layout_probe();
        probe.active_separator_count = 0;
        probe.disabled_separator_count = 2;
        assert!(!workspace_layout_probe_matches(layout_state(), &probe));
    }

    #[test]
    fn workspace_layout_bootstrap_probe_rejects_visible_frame_for_a_collapsed_pane() {
        let mut collapsed_left = layout_state();
        collapsed_left.left_collapsed = true;
        let mut probe = three_pane_layout_probe();
        probe.left_collapsed = true;

        // A schema-v2 collapsed pane must disappear from the probe.
        assert!(!workspace_layout_probe_matches(collapsed_left, &probe));
    }

    #[test]
    fn workspace_layout_bootstrap_probe_rejects_missing_frame_for_an_open_pane() {
        let mut probe = three_pane_layout_probe();
        probe.panes.right = None;

        assert!(!workspace_layout_probe_matches(layout_state(), &probe));
    }

    #[test]
    fn workspace_layout_bootstrap_probe_accepts_absent_frame_and_excludes_its_divider() {
        let mut collapsed_left = layout_state();
        collapsed_left.left_collapsed = true;
        let mut probe = three_pane_layout_probe();
        probe.left_collapsed = true;
        probe.panes.left = None;
        probe.panes.center = layout_frame(0.0, 0.0, 820.0, 800.0);
        probe.active_separator_count = 1;

        assert!(workspace_layout_probe_matches(collapsed_left, &probe));
    }

    #[test]
    fn workspace_layout_bootstrap_probe_accepts_two_column_and_stacked_compositions() {
        let state = layout_state();
        let two_column = WorkspaceLayoutGeometryProbe {
            mode: WorkspaceLayoutMode::TwoColumn,
            shell: layout_frame(0.0, 0.0, 1000.0, 720.0),
            panes: WorkspaceLayoutPaneFrames {
                left: Some(layout_frame(0.0, 0.0, 304.0, 720.0)),
                center: layout_frame(304.0, 0.0, 696.0, 440.0),
                right: Some(layout_frame(304.0, 440.0, 696.0, 280.0)),
            },
            active_separator_count: 0,
            disabled_separator_count: 2,
            ..three_pane_layout_probe()
        };
        assert!(workspace_layout_probe_matches(state, &two_column));

        let stacked = WorkspaceLayoutGeometryProbe {
            mode: WorkspaceLayoutMode::Stacked,
            shell: layout_frame(0.0, 0.0, 820.0, 900.0),
            panes: WorkspaceLayoutPaneFrames {
                left: Some(layout_frame(0.0, 0.0, 820.0, 180.0)),
                center: layout_frame(0.0, 180.0, 820.0, 480.0),
                right: Some(layout_frame(0.0, 660.0, 820.0, 240.0)),
            },
            active_separator_count: 0,
            disabled_separator_count: 2,
            ..three_pane_layout_probe()
        };
        assert!(workspace_layout_probe_matches(state, &stacked));

        let mut narrow_left = two_column;
        narrow_left.panes.left.as_mut().unwrap().width = 199.0;
        assert!(!workspace_layout_probe_matches(state, &narrow_left));

        let mut mismatched_column = two_column;
        mismatched_column.panes.right.as_mut().unwrap().width = 695.0;
        assert!(!workspace_layout_probe_matches(state, &mismatched_column));

        let mut shifted_stack = stacked;
        shifted_stack.panes.right.as_mut().unwrap().x = 1.0;
        shifted_stack.panes.right.as_mut().unwrap().width = 819.0;
        assert!(!workspace_layout_probe_matches(state, &shifted_stack));
    }
}
