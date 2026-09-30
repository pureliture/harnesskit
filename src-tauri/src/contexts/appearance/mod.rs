//! Appearance bounded context.

mod context;
mod resolver;
mod store;
mod window;

pub use context::{
    AppearanceChangeSource, AppearanceChanged, AppearanceContext, AppearanceContextError,
    AppearanceState, AppearanceUpdate, BootstrapCompletion, BootstrapInstruction,
    BootstrapRecovery, LogicalMode, ResolvedMode,
};
pub use resolver::resolve_mode;
pub use store::{
    AppearanceDiagnostic, AppearancePreferencePort, AppearanceStore, AppearanceStoreError,
    LoadedPreference,
};
pub(crate) use window::TauriAppearanceWindowPort;
pub use window::{
    bootstrap_recovery_action, AppearanceEventPort, AppearanceWindowError, AppearanceWindowPort,
    BootstrapRecoveryAction, WindowThemeOverride,
};
