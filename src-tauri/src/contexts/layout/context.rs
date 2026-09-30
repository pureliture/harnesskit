//! Serialized workspace layout session state.

use std::fmt;
use std::sync::{Arc, Mutex};

use super::store::{WorkspaceLayoutDiagnostic, WorkspaceLayoutPreferencePort};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WorkspaceLayoutState {
    pub preferred_left_width_px: u32,
    pub preferred_right_width_px: u32,
    pub left_collapsed: bool,
    pub right_collapsed: bool,
    pub revision: u64,
    pub persisted: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceLayoutUpdate {
    pub state: WorkspaceLayoutState,
    pub diagnostic: Option<WorkspaceLayoutDiagnostic>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkspaceLayoutContextError {
    pub code: &'static str,
    pub safe_message: &'static str,
}

impl fmt::Display for WorkspaceLayoutContextError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.safe_message)
    }
}

impl std::error::Error for WorkspaceLayoutContextError {}

pub struct WorkspaceLayoutContext {
    session: Mutex<WorkspaceLayoutState>,
    store: Arc<dyn WorkspaceLayoutPreferencePort>,
}

impl WorkspaceLayoutContext {
    pub fn new(
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
        store: Arc<dyn WorkspaceLayoutPreferencePort>,
    ) -> Self {
        Self::from_loaded(
            preferred_left_width_px,
            preferred_right_width_px,
            true,
            store,
        )
    }

    pub fn from_loaded(
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
        persisted: bool,
        store: Arc<dyn WorkspaceLayoutPreferencePort>,
    ) -> Self {
        Self::from_loaded_v2(
            preferred_left_width_px,
            preferred_right_width_px,
            false,
            false,
            persisted,
            store,
        )
    }

    pub fn from_loaded_v2(
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
        left_collapsed: bool,
        right_collapsed: bool,
        persisted: bool,
        store: Arc<dyn WorkspaceLayoutPreferencePort>,
    ) -> Self {
        Self {
            session: Mutex::new(WorkspaceLayoutState {
                preferred_left_width_px,
                preferred_right_width_px,
                left_collapsed,
                right_collapsed,
                revision: 0,
                persisted,
            }),
            store,
        }
    }

    pub fn state(&self) -> WorkspaceLayoutState {
        *self
            .session
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub fn set_preference(
        &self,
        expected_revision: u64,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
    ) -> Result<WorkspaceLayoutUpdate, WorkspaceLayoutContextError> {
        let state = self.state();
        self.set_preference_with_disclosure(
            expected_revision,
            preferred_left_width_px,
            preferred_right_width_px,
            state.left_collapsed,
            state.right_collapsed,
        )
    }

    pub fn set_preference_with_disclosure(
        &self,
        expected_revision: u64,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
        left_collapsed: bool,
        right_collapsed: bool,
    ) -> Result<WorkspaceLayoutUpdate, WorkspaceLayoutContextError> {
        let mut session = self
            .session
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if session.revision != expected_revision {
            return Err(WorkspaceLayoutContextError {
                code: "workspace_layout_stale",
                safe_message: "레이아웃 설정이 변경되어 다시 시도해야 합니다.",
            });
        }
        if !super::store::valid_widths(preferred_left_width_px, preferred_right_width_px) {
            return Err(WorkspaceLayoutContextError {
                code: "workspace_layout_preference_invalid",
                safe_message: "레이아웃 설정 값이 올바르지 않습니다.",
            });
        }
        if session.persisted
            && session.preferred_left_width_px == preferred_left_width_px
            && session.preferred_right_width_px == preferred_right_width_px
            && session.left_collapsed == left_collapsed
            && session.right_collapsed == right_collapsed
        {
            return Ok(WorkspaceLayoutUpdate {
                state: *session,
                diagnostic: None,
            });
        }

        let next_revision = session
            .revision
            .checked_add(1)
            .ok_or(WorkspaceLayoutContextError {
                code: "workspace_layout_revision_exhausted",
                safe_message: "레이아웃 설정 변경 한도를 초과했습니다.",
            })?;
        session.preferred_left_width_px = preferred_left_width_px;
        session.preferred_right_width_px = preferred_right_width_px;
        session.left_collapsed = left_collapsed;
        session.right_collapsed = right_collapsed;
        session.revision = next_revision;
        let diagnostic = match self.store.save_with_disclosure(
            preferred_left_width_px,
            preferred_right_width_px,
            left_collapsed,
            right_collapsed,
        ) {
            Ok(()) => {
                session.persisted = true;
                None
            }
            Err(error) => {
                session.persisted = false;
                Some(WorkspaceLayoutDiagnostic {
                    code: error.code,
                    safe_message: error.safe_message,
                })
            }
        };
        Ok(WorkspaceLayoutUpdate {
            state: *session,
            diagnostic,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct UnusedStore;

    impl WorkspaceLayoutPreferencePort for UnusedStore {
        fn save(
            &self,
            _preferred_left_width_px: u32,
            _preferred_right_width_px: u32,
        ) -> Result<(), super::super::store::WorkspaceLayoutStoreError> {
            panic!("revision exhaustion must reject before persistence")
        }
    }

    #[test]
    fn revision_exhaustion_rejects_before_mutation_or_persistence() {
        let context = WorkspaceLayoutContext::new(304, 368, Arc::new(UnusedStore));
        context.session.lock().unwrap().revision = u64::MAX;

        let error = context.set_preference(u64::MAX, 320, 400).unwrap_err();

        assert_eq!(error.code, "workspace_layout_revision_exhausted");
        assert_eq!(context.state().revision, u64::MAX);
        assert_eq!(context.state().preferred_left_width_px, 304);
        assert_eq!(context.state().preferred_right_width_px, 368);
    }
}
