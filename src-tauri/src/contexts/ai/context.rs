use std::collections::HashSet;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use super::provider::normalize_provider_config;
use super::provider_store::{
    FileProviderStore, ProviderConfigHeader, ProviderConfigState, ProviderStoreError,
    ProviderStorePort,
};
use super::transport::{
    AiTransportError, OpenAiCompatibleTransportPort, ReqwestOpenAiCompatibleTransport,
    MAX_AI_SOURCE_BYTES,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AiExplanationError {
    code: &'static str,
}

impl AiExplanationError {
    pub(crate) const fn code(&self) -> &'static str {
        self.code
    }

    const fn new(code: &'static str) -> Self {
        Self { code }
    }

    pub(crate) const fn from_code(code: &'static str) -> Self {
        Self::new(code)
    }
}

impl From<ProviderStoreError> for AiExplanationError {
    fn from(error: ProviderStoreError) -> Self {
        Self::new(error.code())
    }
}

impl From<AiTransportError> for AiExplanationError {
    fn from(error: AiTransportError) -> Self {
        Self::new(error.code())
    }
}

pub(crate) struct AiExplanationInput {
    snapshot_id: String,
    instance_id: String,
    source_revision: String,
    provider_revision: String,
    source: Vec<u8>,
}

impl AiExplanationInput {
    pub(crate) fn new(
        snapshot_id: String,
        instance_id: String,
        source_revision: String,
        provider_revision: String,
        source: Vec<u8>,
    ) -> Self {
        Self {
            snapshot_id,
            instance_id,
            source_revision,
            provider_revision,
            source,
        }
    }
}

pub(crate) struct AiExplanation {
    snapshot_id: String,
    instance_id: String,
    source_revision: String,
    provider_revision: String,
    doing: String,
    when_used: String,
    capabilities: String,
    cautions: String,
}

impl AiExplanation {
    #[cfg(test)]
    pub(crate) fn provider_revision(&self) -> &str {
        &self.provider_revision
    }

    #[cfg(test)]
    pub(crate) fn doing(&self) -> &str {
        &self.doing
    }

    #[cfg(test)]
    pub(crate) fn capabilities(&self) -> &str {
        &self.capabilities
    }

    pub(crate) fn into_parts(
        self,
    ) -> (
        String,
        String,
        String,
        String,
        String,
        String,
        String,
        String,
    ) {
        (
            self.snapshot_id,
            self.instance_id,
            self.source_revision,
            self.provider_revision,
            self.doing,
            self.when_used,
            self.capabilities,
            self.cautions,
        )
    }
}

#[derive(Clone, PartialEq, Eq, Hash)]
struct InFlightKey {
    snapshot_id: String,
    instance_id: String,
    source_revision: String,
    provider_revision: String,
}

pub(crate) struct AiExplanationContext {
    store: Arc<dyn ProviderStorePort>,
    transport: Arc<dyn OpenAiCompatibleTransportPort>,
    in_flight: Mutex<HashSet<InFlightKey>>,
}

impl AiExplanationContext {
    pub(crate) fn new(
        store: Arc<dyn ProviderStorePort>,
        transport: Arc<dyn OpenAiCompatibleTransportPort>,
    ) -> Self {
        Self {
            store,
            transport,
            in_flight: Mutex::new(HashSet::new()),
        }
    }

    pub(crate) fn for_app_data(app_data_dir: PathBuf) -> Self {
        let store = Arc::new(FileProviderStore::new(app_data_dir));
        let transport: Arc<dyn OpenAiCompatibleTransportPort> =
            match ReqwestOpenAiCompatibleTransport::new() {
                Ok(transport) => Arc::new(transport),
                Err(_) => Arc::new(UnavailableTransport),
            };
        Self::new(store, transport)
    }

    #[cfg(test)]
    pub(crate) fn with_app_data_and_transport(
        app_data_dir: PathBuf,
        transport: Arc<dyn OpenAiCompatibleTransportPort>,
    ) -> Self {
        Self::new(Arc::new(FileProviderStore::new(app_data_dir)), transport)
    }

    pub(crate) fn provider_state(&self) -> Result<ProviderConfigState, AiExplanationError> {
        self.store.state().map_err(Into::into)
    }

    pub(crate) fn save_provider_config(
        &self,
        expected_provider_revision: Option<&str>,
        base_url: &str,
        model: &str,
        api_key: Option<&str>,
    ) -> Result<ProviderConfigHeader, AiExplanationError> {
        let config = normalize_provider_config(base_url, model)
            .map_err(|error| AiExplanationError::new(error.code()))?;
        self.store
            .save(expected_provider_revision, config, api_key)
            .map_err(Into::into)
    }

    pub(crate) fn delete_provider_key(
        &self,
        expected_provider_revision: &str,
    ) -> Result<ProviderConfigHeader, AiExplanationError> {
        self.store
            .delete_key(expected_provider_revision)
            .map_err(Into::into)
    }

    pub(crate) fn ensure_active_revision(
        &self,
        expected_provider_revision: &str,
    ) -> Result<ProviderConfigHeader, AiExplanationError> {
        let active = self.store.active()?;
        if active.header().provider_revision() != expected_provider_revision {
            return Err(AiExplanationError::new("provider_stale"));
        }
        Ok(active.header().clone())
    }

    pub(crate) fn explain(
        &self,
        input: AiExplanationInput,
    ) -> Result<AiExplanation, AiExplanationError> {
        if input.source.len() > MAX_AI_SOURCE_BYTES {
            return Err(AiExplanationError::new("source_too_large_for_ai"));
        }
        let active = self.store.active()?;
        if active.header().provider_revision() != input.provider_revision {
            return Err(AiExplanationError::new("provider_stale"));
        }
        let config = normalize_provider_config(active.header().base_url(), active.header().model())
            .map_err(|error| AiExplanationError::new(error.code()))?;
        let key = InFlightKey {
            snapshot_id: input.snapshot_id.clone(),
            instance_id: input.instance_id.clone(),
            source_revision: input.source_revision.clone(),
            provider_revision: input.provider_revision.clone(),
        };
        {
            let mut in_flight = self
                .in_flight
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if !in_flight.insert(key.clone()) {
                return Err(AiExplanationError::new("already_running"));
            }
        }
        let _guard = InFlightGuard {
            in_flight: &self.in_flight,
            key,
        };
        let parsed = self
            .transport
            .explain(&config, active.api_key(), &input.source)?;
        let (doing, when_used, capabilities, cautions) = parsed.into_parts();
        Ok(AiExplanation {
            snapshot_id: input.snapshot_id,
            instance_id: input.instance_id,
            source_revision: input.source_revision,
            provider_revision: input.provider_revision,
            doing,
            when_used,
            capabilities,
            cautions,
        })
    }
}

struct UnavailableTransport;

impl OpenAiCompatibleTransportPort for UnavailableTransport {
    fn explain(
        &self,
        _config: &super::provider::NormalizedProviderConfig,
        _api_key: Option<&[u8]>,
        _source: &[u8],
    ) -> Result<super::provider::ParsedAiExplanation, AiTransportError> {
        Err(AiTransportError::new("provider_transport_unavailable"))
    }
}

struct InFlightGuard<'a> {
    in_flight: &'a Mutex<HashSet<InFlightKey>>,
    key: InFlightKey,
}

impl Drop for InFlightGuard<'_> {
    fn drop(&mut self) {
        self.in_flight
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(&self.key);
    }
}
