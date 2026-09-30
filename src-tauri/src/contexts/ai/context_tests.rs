use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};

use super::context::{AiExplanationContext, AiExplanationError, AiExplanationInput};
use super::provider::{parse_provider_response, ParsedAiExplanation};
use super::provider_store::FileProviderStore;
use super::transport::{AiTransportError, OpenAiCompatibleTransportPort, MAX_AI_SOURCE_BYTES};

fn parsed_explanation() -> ParsedAiExplanation {
    let content = serde_json::json!({
        "doing": "일",
        "when_used": "때",
        "capabilities": "기능",
        "cautions": "주의"
    })
    .to_string();
    let response = serde_json::json!({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": content
            }
        }]
    })
    .to_string();
    parse_provider_response(response.as_bytes()).unwrap()
}

fn request(provider_revision: &str) -> AiExplanationInput {
    AiExplanationInput::new(
        "snapshot-1".to_string(),
        "instance-1".to_string(),
        "a".repeat(64),
        provider_revision.to_string(),
        b"exact source".to_vec(),
    )
}

fn error_code<T>(result: Result<T, AiExplanationError>) -> &'static str {
    match result {
        Ok(_) => panic!("expected AI explanation to fail closed"),
        Err(error) => error.code(),
    }
}

struct BlockingTransport {
    calls: AtomicUsize,
    entered: (Mutex<bool>, Condvar),
    released: (Mutex<bool>, Condvar),
}

impl BlockingTransport {
    fn new() -> Self {
        Self {
            calls: AtomicUsize::new(0),
            entered: (Mutex::new(false), Condvar::new()),
            released: (Mutex::new(false), Condvar::new()),
        }
    }

    fn wait_until_entered(&self) {
        let entered = self.entered.0.lock().unwrap();
        let (entered, timeout) = self
            .entered
            .1
            .wait_timeout_while(entered, std::time::Duration::from_secs(1), |value| !*value)
            .unwrap();
        assert!(*entered && !timeout.timed_out());
    }

    fn release(&self) {
        *self.released.0.lock().unwrap() = true;
        self.released.1.notify_all();
    }
}

impl OpenAiCompatibleTransportPort for BlockingTransport {
    fn explain(
        &self,
        _config: &super::provider::NormalizedProviderConfig,
        _api_key: Option<&[u8]>,
        _source: &[u8],
    ) -> Result<ParsedAiExplanation, AiTransportError> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        *self.entered.0.lock().unwrap() = true;
        self.entered.1.notify_all();
        let released = self.released.0.lock().unwrap();
        let _released = self
            .released
            .1
            .wait_while(released, |value| !*value)
            .unwrap();
        Ok(parsed_explanation())
    }
}

struct FailOnceTransport {
    calls: AtomicUsize,
}

impl OpenAiCompatibleTransportPort for FailOnceTransport {
    fn explain(
        &self,
        _config: &super::provider::NormalizedProviderConfig,
        _api_key: Option<&[u8]>,
        _source: &[u8],
    ) -> Result<ParsedAiExplanation, AiTransportError> {
        if self.calls.fetch_add(1, Ordering::SeqCst) == 0 {
            Err(AiTransportError::new("provider_request_failed"))
        } else {
            Ok(parsed_explanation())
        }
    }
}

fn configured_context(
    transport: Arc<dyn OpenAiCompatibleTransportPort>,
) -> (tempfile::TempDir, AiExplanationContext, String) {
    let app_data = tempfile::Builder::new()
        .prefix("harness-ai-context-")
        .tempdir_in("/private/tmp")
        .unwrap();
    let store = Arc::new(FileProviderStore::new(app_data.path().to_path_buf()));
    let context = AiExplanationContext::new(store, transport);
    let header = context
        .save_provider_config(None, "http://127.0.0.1:11434/v1", "model-a", Some("secret"))
        .unwrap();
    let revision = header.provider_revision().to_string();
    (app_data, context, revision)
}

#[test]
fn same_tuple_is_single_flight_but_a_completed_result_is_never_cached() {
    let transport = Arc::new(BlockingTransport::new());
    let (_app_data, context, revision) = configured_context(transport.clone());
    let context = Arc::new(context);
    let worker_context = Arc::clone(&context);
    let worker_revision = revision.clone();
    let worker = std::thread::spawn(move || worker_context.explain(request(&worker_revision)));
    transport.wait_until_entered();

    assert_eq!(
        error_code(context.explain(request(&revision))),
        "already_running"
    );
    assert_eq!(transport.calls.load(Ordering::SeqCst), 1);
    transport.release();
    let first = worker.join().unwrap().unwrap();
    assert_eq!(first.provider_revision(), revision);

    let second = context.explain(request(&revision)).unwrap();
    assert_eq!(second.doing(), "일");
    assert_eq!(transport.calls.load(Ordering::SeqCst), 2);
}

#[test]
fn stale_provider_and_oversized_source_make_zero_transport_requests() {
    let transport = Arc::new(FailOnceTransport {
        calls: AtomicUsize::new(0),
    });
    let (_app_data, context, revision) = configured_context(transport.clone());

    assert_eq!(
        error_code(context.explain(request("provider-stale"))),
        "provider_stale"
    );
    assert_eq!(transport.calls.load(Ordering::SeqCst), 0);

    let oversized = AiExplanationInput::new(
        "snapshot-1".to_string(),
        "instance-1".to_string(),
        "a".repeat(64),
        revision,
        vec![b'a'; MAX_AI_SOURCE_BYTES + 1],
    );
    assert_eq!(
        error_code(context.explain(oversized)),
        "source_too_large_for_ai"
    );
    assert_eq!(transport.calls.load(Ordering::SeqCst), 0);
}

#[test]
fn transport_failure_removes_the_in_flight_tuple_for_an_explicit_retry() {
    let transport = Arc::new(FailOnceTransport {
        calls: AtomicUsize::new(0),
    });
    let (_app_data, context, revision) = configured_context(transport.clone());

    assert_eq!(
        error_code(context.explain(request(&revision))),
        "provider_request_failed"
    );
    let result = context.explain(request(&revision)).unwrap();

    assert_eq!(result.capabilities(), "기능");
    assert_eq!(transport.calls.load(Ordering::SeqCst), 2);
}
