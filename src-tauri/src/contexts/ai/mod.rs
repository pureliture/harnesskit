mod context;
mod provider;
mod provider_store;
mod transport;

pub(crate) use context::{
    AiExplanation, AiExplanationContext, AiExplanationError, AiExplanationInput,
};
#[cfg(test)]
pub(crate) use provider::{NormalizedProviderConfig, ParsedAiExplanation};
pub(crate) use provider_store::{ProviderConfigHeader, ProviderConfigState};
pub(crate) use transport::MAX_AI_SOURCE_BYTES;
#[cfg(test)]
pub(crate) use transport::{AiTransportError, OpenAiCompatibleTransportPort};

#[cfg(test)]
mod provider_store_tests;

#[cfg(test)]
mod context_tests;
#[cfg(test)]
mod transport_tests;
