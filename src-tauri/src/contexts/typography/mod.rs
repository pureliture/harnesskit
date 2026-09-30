//! Owner-only readable text scale preference.

mod context;
mod store;

pub use context::{
    TypographyContext, TypographyContextError, TypographyPreset, TypographyState, TypographyUpdate,
};
pub use store::{
    LoadedTypographyPreference, TypographyDiagnostic, TypographyPreferencePort, TypographyStore,
    TypographyStoreError,
};
