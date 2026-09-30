use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::Value;

use super::provider::normalize_provider_config;
use super::transport::{
    AiTransportError, OpenAiCompatibleTransportPort, ReqwestOpenAiCompatibleTransport,
    MAX_AI_SOURCE_BYTES,
};

fn strict_content() -> String {
    serde_json::json!({
        "doing": "구성 요소를 설명함",
        "when_used": "AI 도구가 로드할 때",
        "capabilities": "검증 단계를 안내함",
        "cautions": "runtime 검증은 아님"
    })
    .to_string()
}

fn success_response() -> Vec<u8> {
    let body = serde_json::json!({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": strict_content()
            }
        }]
    })
    .to_string();
    http_response("200 OK", &body, &[])
}

fn http_response(status: &str, body: &str, extra_headers: &[(&str, &str)]) -> Vec<u8> {
    let mut response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n",
        body.len()
    );
    for (name, value) in extra_headers {
        response.push_str(name);
        response.push_str(": ");
        response.push_str(value);
        response.push_str("\r\n");
    }
    response.push_str("\r\n");
    response.push_str(body);
    response.into_bytes()
}

fn read_request(stream: &mut TcpStream) -> Vec<u8> {
    stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .unwrap();
    let mut bytes = Vec::new();
    let mut scratch = [0_u8; 4096];
    let mut expected = None;
    loop {
        let read = stream.read(&mut scratch).unwrap();
        if read == 0 {
            break;
        }
        bytes.extend_from_slice(&scratch[..read]);
        if expected.is_none() {
            if let Some(header_end) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
                let headers = String::from_utf8_lossy(&bytes[..header_end]);
                let content_length = headers
                    .lines()
                    .find_map(|line| {
                        line.to_ascii_lowercase()
                            .strip_prefix("content-length:")
                            .and_then(|value| value.trim().parse::<usize>().ok())
                    })
                    .unwrap_or(0);
                expected = Some(header_end + 4 + content_length);
            }
        }
        if expected.is_some_and(|length| bytes.len() >= length) {
            break;
        }
    }
    bytes
}

fn one_request_server(response: Vec<u8>) -> (String, thread::JoinHandle<Vec<u8>>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let worker = thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let request = read_request(&mut stream);
        stream.write_all(&response).unwrap();
        request
    });
    (format!("http://{address}/v1"), worker)
}

fn split_request(request: &[u8]) -> (&str, Value) {
    let header_end = request
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .unwrap();
    let headers = std::str::from_utf8(&request[..header_end]).unwrap();
    let body: Value = serde_json::from_slice(&request[header_end + 4..]).unwrap();
    (headers, body)
}

fn error_code<T>(result: Result<T, AiTransportError>) -> &'static str {
    match result {
        Ok(_) => panic!("expected transport to fail closed"),
        Err(error) => error.code(),
    }
}

#[test]
fn transport_posts_exact_source_once_with_model_stream_false_and_optional_auth() {
    let (base_url, worker) = one_request_server(success_response());
    let config = normalize_provider_config(&base_url, "model-a").unwrap();
    let transport = ReqwestOpenAiCompatibleTransport::new().unwrap();
    let source = "# exact source\n한글도 그대로";

    let explanation = transport
        .explain(&config, Some(b"test-secret"), source.as_bytes())
        .expect("provider response");
    assert_eq!(explanation.doing(), "구성 요소를 설명함");

    let request = worker.join().unwrap();
    let (headers, body) = split_request(&request);
    assert!(headers.starts_with("POST /v1/chat/completions HTTP/1.1"));
    assert!(headers
        .to_ascii_lowercase()
        .contains("authorization: bearer test-secret"));
    assert_eq!(body["model"], "model-a");
    assert_eq!(body["stream"], false);
    assert_eq!(body["messages"][0]["role"], "system");
    assert!(body["messages"][0]["content"].as_str().unwrap().len() > 40);
    assert_eq!(body["messages"][1]["role"], "user");
    assert_eq!(body["messages"][1]["content"], source);
    assert!(!request
        .windows(base_url.len())
        .any(|bytes| bytes == base_url.as_bytes()));
}

#[test]
fn transport_omits_authorization_when_no_key_is_configured() {
    let (base_url, worker) = one_request_server(success_response());
    let config = normalize_provider_config(&base_url, "model-a").unwrap();
    ReqwestOpenAiCompatibleTransport::new()
        .unwrap()
        .explain(&config, None, b"source")
        .unwrap();

    let request = worker.join().unwrap();
    let (headers, _) = split_request(&request);
    assert!(!headers.to_ascii_lowercase().contains("authorization:"));
}

#[test]
fn transport_never_follows_redirects_to_an_alternate_origin() {
    let target = TcpListener::bind("127.0.0.1:0").unwrap();
    target.set_nonblocking(true).unwrap();
    let target_url = format!("http://{}/leak", target.local_addr().unwrap());
    let response = http_response("307 Temporary Redirect", "", &[("Location", &target_url)]);
    let (base_url, worker) = one_request_server(response);
    let config = normalize_provider_config(&base_url, "model-a").unwrap();

    assert_eq!(
        error_code(ReqwestOpenAiCompatibleTransport::new().unwrap().explain(
            &config,
            Some(b"secret"),
            b"source"
        )),
        "provider_request_failed"
    );
    worker.join().unwrap();
    assert!(
        matches!(target.accept(), Err(error) if error.kind() == std::io::ErrorKind::WouldBlock)
    );
}

#[test]
fn transport_enforces_source_and_response_bounds_and_never_retries() {
    let transport = ReqwestOpenAiCompatibleTransport::new().unwrap();
    let unreachable = normalize_provider_config("http://127.0.0.1:9/v1", "model-a").unwrap();
    let oversized_source = vec![b'a'; MAX_AI_SOURCE_BYTES + 1];
    assert_eq!(
        error_code(transport.explain(&unreachable, None, &oversized_source)),
        "source_too_large_for_ai"
    );

    let oversized_body = "x".repeat((256 * 1024) + 1);
    let (base_url, oversized_worker) =
        one_request_server(http_response("200 OK", &oversized_body, &[]));
    let config = normalize_provider_config(&base_url, "model-a").unwrap();
    assert_eq!(
        error_code(transport.explain(&config, None, b"source")),
        "provider_response_invalid"
    );
    oversized_worker.join().unwrap();

    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    listener.set_nonblocking(true).unwrap();
    let address = listener.local_addr().unwrap();
    let count = Arc::new(AtomicUsize::new(0));
    let worker_count = Arc::clone(&count);
    let worker = thread::spawn(move || {
        let deadline = Instant::now() + Duration::from_millis(500);
        while Instant::now() < deadline {
            match listener.accept() {
                Ok((mut stream, _)) => {
                    worker_count.fetch_add(1, Ordering::SeqCst);
                    let _ = read_request(&mut stream);
                    let _ =
                        stream.write_all(&http_response("500 Internal Server Error", "error", &[]));
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(5));
                }
                Err(_) => break,
            }
        }
    });
    let config = normalize_provider_config(&format!("http://{address}/v1"), "model-a").unwrap();
    assert_eq!(
        error_code(transport.explain(&config, None, b"source")),
        "provider_request_failed"
    );
    worker.join().unwrap();
    assert_eq!(count.load(Ordering::SeqCst), 1);
}
