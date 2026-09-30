//! SoT bounded context.

pub mod checkout_picker;
mod context;
pub(crate) mod domain;
mod graph_projection;
mod relations;
mod schema;
mod snapshot;
mod source;
mod workflow;

pub use context::{selected_profile_component_closure, SotContext, SotSessionHeaders};
pub use domain::SotSnapshot;
