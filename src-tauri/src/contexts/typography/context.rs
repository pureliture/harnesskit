use std::fmt;
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};

use super::{TypographyDiagnostic, TypographyPreferencePort};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TypographyPreset {
    Small,
    Default,
    Large,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TypographyState {
    pub preset: TypographyPreset,
    pub revision: u64,
    pub persisted: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypographyUpdate {
    pub state: TypographyState,
    pub diagnostic: Option<TypographyDiagnostic>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TypographyContextError {
    pub code: &'static str,
    pub safe_message: &'static str,
}

impl fmt::Display for TypographyContextError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.safe_message)
    }
}

impl std::error::Error for TypographyContextError {}

pub struct TypographyContext {
    session: Mutex<TypographyState>,
    store: Arc<dyn TypographyPreferencePort>,
}

impl TypographyContext {
    pub fn from_loaded(
        preset: TypographyPreset,
        persisted: bool,
        store: Arc<dyn TypographyPreferencePort>,
    ) -> Self {
        Self {
            session: Mutex::new(TypographyState {
                preset,
                revision: 0,
                persisted,
            }),
            store,
        }
    }

    pub fn state(&self) -> TypographyState {
        *self
            .session
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub fn set_preset(
        &self,
        expected_revision: u64,
        preset: TypographyPreset,
    ) -> Result<TypographyUpdate, TypographyContextError> {
        let mut session = self
            .session
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if session.revision != expected_revision {
            return Err(TypographyContextError {
                code: "typography_stale",
                safe_message: "글자 크기 설정이 변경되어 다시 시도해야 합니다.",
            });
        }
        if session.persisted && session.preset == preset {
            return Ok(TypographyUpdate {
                state: *session,
                diagnostic: None,
            });
        }
        let revision = session
            .revision
            .checked_add(1)
            .ok_or(TypographyContextError {
                code: "typography_revision_exhausted",
                safe_message: "글자 크기 설정 변경 한도를 초과했습니다.",
            })?;
        session.preset = preset;
        session.revision = revision;
        let diagnostic = match self.store.save(preset) {
            Ok(()) => {
                session.persisted = true;
                None
            }
            Err(error) => {
                session.persisted = false;
                Some(TypographyDiagnostic {
                    code: error.code,
                    safe_message: error.safe_message,
                })
            }
        };
        Ok(TypographyUpdate {
            state: *session,
            diagnostic,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::contexts::typography::TypographyStoreError;

    struct FailingStore;
    impl TypographyPreferencePort for FailingStore {
        fn save(&self, _: TypographyPreset) -> Result<(), TypographyStoreError> {
            Err(TypographyStoreError {
                code: "write_failed",
                safe_message: "write failed",
            })
        }
    }

    #[test]
    fn write_failure_keeps_new_session_value_with_nonpersisted_state() {
        let context =
            TypographyContext::from_loaded(TypographyPreset::Default, true, Arc::new(FailingStore));
        let update = context.set_preset(0, TypographyPreset::Large).unwrap();
        assert_eq!(update.state.preset, TypographyPreset::Large);
        assert_eq!(update.state.revision, 1);
        assert!(!update.state.persisted);
    }
}
