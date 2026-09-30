use std::io;
use std::io::{Read, Seek};

use super::yaml_span::resolve_yaml_path_span;

const STREAM_BUFFER_BYTES: usize = 64 * 1024;
const MAX_JSON_DEPTH: usize = 64;
const MAX_LOCATOR_SEGMENT_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct SourceSpan {
    pub(crate) start: u64,
    pub(crate) length: u64,
}

pub(crate) fn resolve_json_pointer_span(
    reader: impl Read,
    locator: &str,
) -> io::Result<Option<SourceSpan>> {
    let target = decode_pointer(locator)?;
    JsonSpanResolver {
        stream: ByteStream::new(reader),
        target,
        found: None,
    }
    .resolve()
}

pub(crate) fn resolve_hermes_yaml_pointer_span(
    reader: impl Read + Seek,
    locator: &str,
) -> io::Result<Option<SourceSpan>> {
    let target = decode_pointer(locator)?;
    if target.len() < 2 || target.first().map(String::as_str) != Some("hooks") {
        return Ok(None);
    }
    if target.len() > 3 {
        return Ok(None);
    }
    if target
        .get(2)
        .is_some_and(|segment| segment.parse::<usize>().is_err())
    {
        return Err(invalid_data("invalid YAML sequence locator"));
    }
    resolve_yaml_path_span(reader, &target)
}

fn decode_pointer(locator: &str) -> io::Result<Vec<String>> {
    if locator == "#" {
        return Ok(Vec::new());
    }
    let suffix = locator
        .strip_prefix("#/")
        .ok_or_else(|| invalid_data("invalid source entry locator"))?;
    let segments = suffix
        .split('/')
        .map(decode_pointer_segment)
        .collect::<io::Result<Vec<_>>>()?;
    if segments.len() > MAX_JSON_DEPTH {
        return Err(invalid_data("source entry locator is too deep"));
    }
    Ok(segments)
}

fn decode_pointer_segment(segment: &str) -> io::Result<String> {
    if segment.len() > MAX_LOCATOR_SEGMENT_BYTES {
        return Err(invalid_data("source entry locator segment is too large"));
    }
    let mut decoded = String::with_capacity(segment.len());
    let mut chars = segment.chars();
    while let Some(character) = chars.next() {
        if character != '~' {
            decoded.push(character);
            continue;
        }
        match chars.next() {
            Some('0') => decoded.push('~'),
            Some('1') => decoded.push('/'),
            _ => return Err(invalid_data("invalid JSON pointer escape")),
        }
    }
    Ok(decoded)
}

struct ByteStream<R> {
    reader: R,
    buffer: Box<[u8; STREAM_BUFFER_BYTES]>,
    cursor: usize,
    length: usize,
    position: u64,
}

impl<R: Read> ByteStream<R> {
    fn new(reader: R) -> Self {
        Self {
            reader,
            buffer: Box::new([0; STREAM_BUFFER_BYTES]),
            cursor: 0,
            length: 0,
            position: 0,
        }
    }

    fn position(&self) -> u64 {
        self.position
    }

    fn peek(&mut self) -> io::Result<Option<u8>> {
        if self.cursor == self.length {
            self.fill()?;
        }
        Ok((self.cursor < self.length).then(|| self.buffer[self.cursor]))
    }

    fn next(&mut self) -> io::Result<Option<u8>> {
        let byte = self.peek()?;
        if byte.is_some() {
            self.cursor += 1;
            self.position = self.position.saturating_add(1);
        }
        Ok(byte)
    }

    fn expect(&mut self, expected: u8) -> io::Result<()> {
        match self.next()? {
            Some(actual) if actual == expected => Ok(()),
            _ => Err(invalid_data("malformed JSON source")),
        }
    }

    fn skip_json_whitespace(&mut self) -> io::Result<()> {
        while matches!(self.peek()?, Some(b' ' | b'\n' | b'\r' | b'\t')) {
            self.next()?;
        }
        Ok(())
    }

    fn fill(&mut self) -> io::Result<()> {
        self.cursor = 0;
        loop {
            match self.reader.read(self.buffer.as_mut_slice()) {
                Ok(read) => {
                    self.length = read;
                    return Ok(());
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => return Err(error),
            }
        }
    }
}

struct JsonSpanResolver<R> {
    stream: ByteStream<R>,
    target: Vec<String>,
    found: Option<SourceSpan>,
}

impl<R: Read> JsonSpanResolver<R> {
    fn resolve(mut self) -> io::Result<Option<SourceSpan>> {
        let mut path = Vec::new();
        self.scan_value(&mut path, 0)?;
        self.stream.skip_json_whitespace()?;
        if self.stream.peek()?.is_some() {
            return Err(invalid_data("trailing JSON content"));
        }
        Ok(self.found)
    }

    fn scan_value(&mut self, path: &mut Vec<String>, depth: usize) -> io::Result<()> {
        self.stream.skip_json_whitespace()?;
        let start = self.stream.position();
        if path == &self.target {
            self.skip_value(depth)?;
            let end = self.stream.position();
            self.found = Some(SourceSpan {
                start,
                length: end.saturating_sub(start),
            });
            return Ok(());
        }
        if depth > MAX_JSON_DEPTH {
            return Err(invalid_data("JSON source nesting is too deep"));
        }
        match self.stream.peek()? {
            Some(b'{') => self.scan_object(path, depth + 1),
            Some(b'[') => self.scan_array(path, depth + 1),
            _ => self.skip_value(depth),
        }
    }

    fn scan_object(&mut self, path: &mut Vec<String>, depth: usize) -> io::Result<()> {
        self.stream.expect(b'{')?;
        self.stream.skip_json_whitespace()?;
        if self.stream.peek()? == Some(b'}') {
            self.stream.next()?;
            return Ok(());
        }
        loop {
            let key = self.parse_json_key()?;
            self.stream.skip_json_whitespace()?;
            self.stream.expect(b':')?;
            path.push(key);
            if self.target.starts_with(path.as_slice()) {
                self.found = None;
            }
            self.scan_value(path, depth)?;
            path.pop();
            self.stream.skip_json_whitespace()?;
            match self.stream.next()? {
                Some(b',') => self.stream.skip_json_whitespace()?,
                Some(b'}') => return Ok(()),
                _ => return Err(invalid_data("malformed JSON object")),
            }
        }
    }

    fn scan_array(&mut self, path: &mut Vec<String>, depth: usize) -> io::Result<()> {
        self.stream.expect(b'[')?;
        self.stream.skip_json_whitespace()?;
        if self.stream.peek()? == Some(b']') {
            self.stream.next()?;
            return Ok(());
        }
        let mut index = 0_u64;
        loop {
            path.push(index.to_string());
            self.scan_value(path, depth)?;
            path.pop();
            index = index.saturating_add(1);
            self.stream.skip_json_whitespace()?;
            match self.stream.next()? {
                Some(b',') => self.stream.skip_json_whitespace()?,
                Some(b']') => return Ok(()),
                _ => return Err(invalid_data("malformed JSON array")),
            }
        }
    }

    fn skip_value(&mut self, depth: usize) -> io::Result<()> {
        if depth > MAX_JSON_DEPTH {
            return Err(invalid_data("JSON source nesting is too deep"));
        }
        self.stream.skip_json_whitespace()?;
        match self.stream.peek()? {
            Some(b'{') => self.skip_object(depth + 1),
            Some(b'[') => self.skip_array(depth + 1),
            Some(b'"') => self.skip_json_string(),
            Some(b't') => self.expect_literal(b"true"),
            Some(b'f') => self.expect_literal(b"false"),
            Some(b'n') => self.expect_literal(b"null"),
            Some(b'-' | b'0'..=b'9') => self.skip_number(),
            _ => Err(invalid_data("malformed JSON value")),
        }
    }

    fn skip_object(&mut self, depth: usize) -> io::Result<()> {
        self.stream.expect(b'{')?;
        self.stream.skip_json_whitespace()?;
        if self.stream.peek()? == Some(b'}') {
            self.stream.next()?;
            return Ok(());
        }
        loop {
            self.skip_json_string()?;
            self.stream.skip_json_whitespace()?;
            self.stream.expect(b':')?;
            self.skip_value(depth)?;
            self.stream.skip_json_whitespace()?;
            match self.stream.next()? {
                Some(b',') => self.stream.skip_json_whitespace()?,
                Some(b'}') => return Ok(()),
                _ => return Err(invalid_data("malformed JSON object")),
            }
        }
    }

    fn skip_array(&mut self, depth: usize) -> io::Result<()> {
        self.stream.expect(b'[')?;
        self.stream.skip_json_whitespace()?;
        if self.stream.peek()? == Some(b']') {
            self.stream.next()?;
            return Ok(());
        }
        loop {
            self.skip_value(depth)?;
            self.stream.skip_json_whitespace()?;
            match self.stream.next()? {
                Some(b',') => self.stream.skip_json_whitespace()?,
                Some(b']') => return Ok(()),
                _ => return Err(invalid_data("malformed JSON array")),
            }
        }
    }

    fn parse_json_key(&mut self) -> io::Result<String> {
        let mut raw = Vec::new();
        raw.push(
            self.stream
                .next()?
                .filter(|byte| *byte == b'"')
                .ok_or_else(|| invalid_data("JSON object key must be a string"))?,
        );
        let mut escaped = false;
        loop {
            let byte = self
                .stream
                .next()?
                .ok_or_else(|| invalid_data("unterminated JSON key"))?;
            raw.push(byte);
            if raw.len()
                > MAX_LOCATOR_SEGMENT_BYTES
                    .saturating_mul(6)
                    .saturating_add(2)
            {
                return Err(invalid_data("JSON object key is too large"));
            }
            if escaped {
                escaped = false;
                continue;
            }
            match byte {
                b'\\' => escaped = true,
                b'"' => break,
                0x00..=0x1f => return Err(invalid_data("control byte in JSON key")),
                _ => {}
            }
        }
        serde_json::from_slice(&raw).map_err(|_| invalid_data("malformed JSON key"))
    }

    fn skip_json_string(&mut self) -> io::Result<()> {
        self.stream.expect(b'"')?;
        loop {
            match self
                .stream
                .next()?
                .ok_or_else(|| invalid_data("unterminated JSON string"))?
            {
                b'"' => return Ok(()),
                b'\\' => match self
                    .stream
                    .next()?
                    .ok_or_else(|| invalid_data("unterminated JSON escape"))?
                {
                    b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't' => {}
                    b'u' => {
                        for _ in 0..4 {
                            let digit = self
                                .stream
                                .next()?
                                .ok_or_else(|| invalid_data("unterminated unicode escape"))?;
                            if !digit.is_ascii_hexdigit() {
                                return Err(invalid_data("invalid unicode escape"));
                            }
                        }
                    }
                    _ => return Err(invalid_data("invalid JSON escape")),
                },
                0x00..=0x1f => return Err(invalid_data("control byte in JSON string")),
                _ => {}
            }
        }
    }

    fn expect_literal(&mut self, literal: &[u8]) -> io::Result<()> {
        for &expected in literal {
            self.stream.expect(expected)?;
        }
        Ok(())
    }

    fn skip_number(&mut self) -> io::Result<()> {
        let mut token = Vec::new();
        while let Some(byte) = self.stream.peek()? {
            if matches!(byte, b' ' | b'\n' | b'\r' | b'\t' | b',' | b']' | b'}') {
                break;
            }
            if token.len() >= 256 {
                return Err(invalid_data("JSON number is too large"));
            }
            token.push(byte);
            self.stream.next()?;
        }
        match serde_json::from_slice::<serde_json::Value>(&token) {
            Ok(serde_json::Value::Number(_)) => Ok(()),
            _ => Err(invalid_data("malformed JSON number")),
        }
    }
}

fn invalid_data(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::io::{self, Read, Seek, SeekFrom};
    use std::rc::Rc;

    use super::*;

    struct ShortReader<'a> {
        bytes: &'a [u8],
        offset: usize,
        max_read: usize,
    }

    impl<'a> ShortReader<'a> {
        fn new(bytes: &'a [u8], max_read: usize) -> Self {
            Self {
                bytes,
                offset: 0,
                max_read,
            }
        }
    }

    impl Read for ShortReader<'_> {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            let remaining = &self.bytes[self.offset..];
            let read = remaining.len().min(buffer.len()).min(self.max_read);
            buffer[..read].copy_from_slice(&remaining[..read]);
            self.offset += read;
            Ok(read)
        }
    }

    impl Seek for ShortReader<'_> {
        fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
            let length = self.bytes.len() as i128;
            let current = self.offset as i128;
            let requested = match position {
                SeekFrom::Start(offset) => offset as i128,
                SeekFrom::End(offset) => length + offset as i128,
                SeekFrom::Current(offset) => current + offset as i128,
            };
            if !(0..=length).contains(&requested) {
                return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid seek"));
            }
            self.offset = requested as usize;
            Ok(self.offset as u64)
        }
    }

    struct CountingReader<'a> {
        bytes: &'a [u8],
        offset: usize,
        calls: Rc<Cell<usize>>,
    }

    impl Read for CountingReader<'_> {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            self.calls.set(self.calls.get().saturating_add(1));
            let remaining = &self.bytes[self.offset..];
            let read = remaining.len().min(buffer.len());
            buffer[..read].copy_from_slice(&remaining[..read]);
            self.offset += read;
            Ok(read)
        }
    }

    impl Seek for CountingReader<'_> {
        fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
            let length = self.bytes.len() as i128;
            let current = self.offset as i128;
            let requested = match position {
                SeekFrom::Start(offset) => offset as i128,
                SeekFrom::End(offset) => length + offset as i128,
                SeekFrom::Current(offset) => current + offset as i128,
            };
            if !(0..=length).contains(&requested) {
                return Err(io::Error::new(io::ErrorKind::InvalidInput, "invalid seek"));
            }
            self.offset = requested as usize;
            Ok(self.offset as u64)
        }
    }

    fn selected(source: &str, span: SourceSpan) -> &str {
        let start = usize::try_from(span.start).unwrap();
        let end = usize::try_from(span.start + span.length).unwrap();
        &source[start..end]
    }

    #[test]
    fn json_pointer_resolves_escaped_key_and_utf8_value_across_short_reads() {
        let source = r#"{"hooks":{"a/b~c":{"description":"한글 🧭"},"other":true}}"#;

        let span =
            resolve_json_pointer_span(ShortReader::new(source.as_bytes(), 3), "#/hooks/a~1b~0c")
                .unwrap()
                .unwrap();

        assert_eq!(selected(source, span), r#"{"description":"한글 🧭"}"#);
    }

    #[test]
    fn json_pointer_resolves_nested_array_value_without_string_search() {
        let source =
            r#"{"hooks":{"Before":[{"hooks":[{"command":"a"}]},{"hooks":[{"command":"b"}]}]}}"#;

        let span = resolve_json_pointer_span(
            ShortReader::new(source.as_bytes(), 5),
            "#/hooks/Before/1/hooks/0",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), r#"{"command":"b"}"#);
    }

    #[test]
    fn missing_json_pointer_returns_no_guessed_span() {
        let source =
            r#"{"hooks":{"Before":{"description":"same"}},"other":{"description":"same"}}"#;

        let span =
            resolve_json_pointer_span(ShortReader::new(source.as_bytes(), 7), "#/hooks/Missing")
                .unwrap();

        assert_eq!(span, None);
    }

    #[test]
    fn json_pointer_uses_last_duplicate_leaf_like_serde_json() {
        let source = r#"{"hooks":{"Before":"first","Before":"last"}}"#;

        let span =
            resolve_json_pointer_span(ShortReader::new(source.as_bytes(), 2), "#/hooks/Before")
                .unwrap()
                .unwrap();

        assert_eq!(selected(source, span), r#""last""#);
    }

    #[test]
    fn json_pointer_uses_last_duplicate_ancestor_like_serde_json() {
        let source = r#"{"hooks":{"Before":"first"},"hooks":{"Before":"last"}}"#;

        let span =
            resolve_json_pointer_span(ShortReader::new(source.as_bytes(), 3), "#/hooks/Before")
                .unwrap()
                .unwrap();

        assert_eq!(selected(source, span), r#""last""#);
    }

    #[test]
    fn json_pointer_clears_match_when_last_duplicate_ancestor_omits_target() {
        let source = r#"{"hooks":{"Before":"first"},"hooks":{"After":"last"}}"#;

        let span =
            resolve_json_pointer_span(ShortReader::new(source.as_bytes(), 4), "#/hooks/Before")
                .unwrap();

        assert_eq!(span, None);
    }

    #[test]
    fn json_pointer_resolves_exact_empty_object_without_consuming_its_parent_delimiter() {
        let source = r#"{"hooks":{},"tail":true}"#;

        let span = resolve_json_pointer_span(ShortReader::new(source.as_bytes(), 1), "#/hooks")
            .unwrap()
            .unwrap();

        assert_eq!(selected(source, span), "{}");
    }

    #[test]
    fn hermes_yaml_pointer_resolves_mapping_and_sequence_item_byte_spans() {
        let source = "hooks:\n  pre/check~x:\n    - description: 첫째\n      command: a\n    - description: 둘째 🧭\n      command: b\n  After:\n    command: c\ntail: ok\n";

        let event_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/pre~1check~0x",
        )
        .unwrap()
        .unwrap();
        let item_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/pre~1check~0x/1",
        )
        .unwrap()
        .unwrap();

        assert_eq!(
            selected(source, event_span),
            "- description: 첫째\n      command: a\n    - description: 둘째 🧭\n      command: b"
        );
        assert_eq!(
            selected(source, item_span),
            "description: 둘째 🧭\n      command: b"
        );
    }

    #[test]
    fn hermes_yaml_pointer_uses_only_root_hooks_and_direct_event_children() {
        let source = "nested:\n  hooks:\n    Before:\n      command: wrong\nhooks:\n  Before:\n    command: right\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 5),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: right");
    }

    #[test]
    fn hermes_yaml_pointer_ignores_inline_comments_on_empty_mapping_values() {
        let source = "hooks: # root comment\n  Before: # event comment\n    command: run\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: run");
    }

    #[test]
    fn hermes_yaml_pointer_resolves_flow_mapping_values() {
        let source = "hooks: {Before: {description: test, command: run}, After: stop}\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 7),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "{description: test, command: run}");
    }

    #[test]
    fn hermes_yaml_pointer_resolves_multiline_root_flow_mapping_and_sequence_item() {
        let source = "{\n  hooks: {\n    Before: [\n      {command: first},\n      {command: 둘째 🧭}\n    ]\n  }\n}\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 6),
            "#/hooks/Before/1",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "{command: 둘째 🧭}");
    }

    #[test]
    fn hermes_yaml_pointer_decodes_quoted_event_key_without_string_search() {
        let source = "hooks:\n  \"pre/check~x\":\n    command: run\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 2),
            "#/hooks/pre~1check~0x",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: run");
    }

    #[test]
    fn hermes_yaml_pointer_does_not_treat_implicit_boolean_key_as_string_event() {
        let source = "hooks:\n  true:\n    command: wrong\n  \"true\":\n    command: right\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 8),
            "#/hooks/true",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: right");
    }

    #[test]
    fn hermes_yaml_pointer_counts_only_direct_sequence_items() {
        let source = "hooks:\n  Before:\n    - command: first\n      nested:\n        - command: decoy\n    - command: second\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before/1",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: second");
    }

    #[test]
    fn hermes_yaml_pointer_buffers_underlying_reads_in_fixed_size_blocks() {
        let source = format!(
            "hooks:\n  Before:\n    command: run\n{}",
            "# deterministic filler\n".repeat(20_000)
        );
        let calls = Rc::new(Cell::new(0));
        let reader = CountingReader {
            bytes: source.as_bytes(),
            offset: 0,
            calls: Rc::clone(&calls),
        };

        let span = resolve_hermes_yaml_pointer_span(reader, "#/hooks/Before")
            .unwrap()
            .unwrap();

        assert_eq!(selected(&source, span), "command: run");
        assert!(calls.get() <= 10, "unexpected read calls: {}", calls.get());
    }

    #[test]
    fn hermes_yaml_alias_resolution_uses_at_most_two_fixed_buffer_passes() {
        let source = format!(
            "shared: &all\n  Before: run\n{}hooks: *all\n",
            "# deterministic filler\n".repeat(20_000)
        );
        let calls = Rc::new(Cell::new(0));
        let reader = CountingReader {
            bytes: source.as_bytes(),
            offset: 0,
            calls: Rc::clone(&calls),
        };

        let span = resolve_hermes_yaml_pointer_span(reader, "#/hooks/Before")
            .unwrap()
            .unwrap();

        let blocks = source.len().div_ceil(STREAM_BUFFER_BYTES);
        assert_eq!(selected(&source, span), "run");
        assert!(
            calls.get() <= blocks * 2 + 2,
            "more than two fixed-buffer passes: {} calls for {blocks} blocks",
            calls.get()
        );
    }

    #[test]
    fn hermes_yaml_pointer_streams_a_scalar_larger_than_the_fixed_buffer() {
        let scalar = "x".repeat(STREAM_BUFFER_BYTES * 4 + 17);
        let source = format!("hooks:\n  Before: {scalar}\n");
        let calls = Rc::new(Cell::new(0));
        let reader = CountingReader {
            bytes: source.as_bytes(),
            offset: 0,
            calls: Rc::clone(&calls),
        };

        let span = resolve_hermes_yaml_pointer_span(reader, "#/hooks/Before")
            .unwrap()
            .unwrap();

        assert_eq!(span.length, scalar.len() as u64);
        assert_eq!(selected(&source, span), scalar);
        assert!(calls.get() <= 7, "unexpected read calls: {}", calls.get());
    }

    #[test]
    fn hermes_yaml_pointer_does_not_promote_nested_flow_mapping_to_document_root() {
        let source = "meta:\n  {hooks: {Before: wrong}}\nhooks:\n  Before:\n    command: right\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 5),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: right");
    }

    #[test]
    fn hermes_yaml_pointer_accepts_anchor_and_tag_properties_on_mapping_nodes() {
        let anchored = "hooks: &all\n  Before: &handler\n    command: anchored\n";
        let tagged = "hooks: !!map {Before: !!map {command: tagged}}\n";

        let anchored_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(anchored.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();
        let tagged_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(tagged.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(
            selected(anchored, anchored_span),
            "&handler\n    command: anchored"
        );
        assert_eq!(selected(tagged, tagged_span), "!!map {command: tagged}");
    }

    #[test]
    fn hermes_yaml_pointer_follows_a_hooks_mapping_alias_to_its_anchor_definition() {
        let source = "shared: &all\n  Before:\n    command: anchored\nhooks: *all\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: anchored");
    }

    #[test]
    fn hermes_yaml_pointer_resolves_hooks_alias_and_anchor_inside_flow_collections() {
        let root_flow = "{shared: &all {Before: root-flow}, hooks: *all}\n";
        let nested_flow = "shared: {holder: &all {Before: nested-flow}}\nhooks: *all\n";

        let root_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(root_flow.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();
        let nested_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(nested_flow.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(root_flow, root_span), "root-flow");
        assert_eq!(selected(nested_flow, nested_span), "nested-flow");
    }

    #[test]
    fn hermes_yaml_pointer_uses_latest_flow_anchor_and_clears_older_candidate() {
        let latest =
            "first: &all {Before: old}\ncontainer: {latest: &all {Before: new}}\nhooks: *all\n";
        let omitted =
            "first: &all {Before: old}\ncontainer: {latest: &all {After: new}}\nhooks: *all\n";

        let latest_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(latest.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();
        let omitted_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(omitted.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap();

        assert_eq!(selected(latest, latest_span), "new");
        assert_eq!(omitted_span, None);
    }

    #[test]
    fn hermes_yaml_pointer_scopes_duplicate_detection_to_each_redefined_flow_anchor() {
        let latest =
            "container: {first: &all {Before: old}, second: &all {Before: new}}\nhooks: *all\n";
        let omitted =
            "container: {first: &all {Before: old}, second: &all {After: new}}\nhooks: *all\n";

        let latest_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(latest.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();
        let omitted_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(omitted.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap();

        assert_eq!(selected(latest, latest_span), "new");
        assert_eq!(omitted_span, None);
    }

    #[test]
    fn hermes_yaml_pointer_treats_a_scalar_key_anchor_as_the_latest_definition() {
        let source = "old: &all {Before: old}\ncontainer: { &all newer: x }\nhooks: *all\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap();

        assert_eq!(span, None);
    }

    #[test]
    fn hermes_yaml_pointer_treats_a_block_scalar_key_anchor_as_the_latest_definition() {
        let source = "old: &all {Before: old}\n&all newer: x\nhooks: *all\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap();

        assert_eq!(span, None);
    }

    #[test]
    fn hermes_yaml_pointer_applies_a_value_anchor_after_a_same_line_key_anchor() {
        let source = "old: &all {Before: old}\n&all newer: &all {Before: newest}\nhooks: *all\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "newest");
    }

    #[test]
    fn hermes_yaml_pointer_uses_the_latest_preceding_anchor_definition() {
        let source = "first: &all\n  Before:\n    command: first\nsecond: &all\n  Before:\n    command: latest\nhooks: *all\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: latest");
    }

    #[test]
    fn hermes_yaml_pointer_clears_an_older_anchor_match_when_latest_omits_target() {
        let source = "first: &all\n  Before:\n    command: first\nsecond: &all\n  After:\n    command: latest\nhooks: *all\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap();

        assert_eq!(span, None);
    }

    #[test]
    fn hermes_yaml_pointer_rejects_missing_or_forward_hooks_aliases() {
        let missing = "hooks: *missing\n";
        let forward = "hooks: *all\nshared: &all\n  Before:\n    command: late\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(missing.as_bytes(), 3),
            "#/hooks/Before",
        )
        .is_err());
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(forward.as_bytes(), 3),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_keeps_block_scalar_content_as_the_selected_node() {
        let source = "hooks:\n  Before: |\n    #!/bin/sh\n    echo '{not: flow}'\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 6),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(
            selected(source, span),
            "|\n    #!/bin/sh\n    echo '{not: flow}'"
        );
    }

    #[test]
    fn hermes_yaml_pointer_treats_block_scalar_flow_markers_as_opaque_text() {
        let source = "hooks:\n  Before: |\n    {literal text\n    [also literal\ntail: valid\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(
            selected(source, span),
            "|\n    {literal text\n    [also literal"
        );
    }

    #[test]
    fn hermes_yaml_pointer_skips_opaque_block_scalars_before_root_hooks() {
        let source =
            "notes: |\n  {literal text\n  [also literal\nhooks:\n  Before:\n    command: run\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: run");
    }

    #[test]
    fn hermes_yaml_pointer_resolves_indentless_block_sequence_items() {
        let source = "hooks:\n  Before:\n  - command: first\n  - command: second\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 5),
            "#/hooks/Before/1",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: second");
    }

    #[test]
    fn hermes_yaml_pointer_resolves_the_complete_indentless_event_node() {
        let source = "hooks:\n  Before:\n  - command: first\n  - command: second\n  After: done\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 5),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(
            selected(source, span),
            "- command: first\n  - command: second"
        );
    }

    #[test]
    fn hermes_yaml_pointer_accepts_single_document_markers() {
        let source = "---\nhooks:\n  Before:\n    command: run\n...\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "command: run");
    }

    #[test]
    fn hermes_yaml_pointer_accepts_inline_content_after_document_start() {
        let source = "--- {hooks: {Before: run}}\n...\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "run");
    }

    #[test]
    fn hermes_yaml_pointer_accepts_document_end_after_a_selected_block_scalar() {
        let block_scalar = "---\nhooks:\n  Before: |\n    run\n...\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(block_scalar.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();
        assert_eq!(selected(block_scalar, span), "|\n    run");
    }

    #[test]
    fn hermes_yaml_pointer_rejects_content_after_document_end() {
        let source = "---\nhooks:\n  Before:\n    command: run\n...\nother: rejected\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_rejects_a_second_document_after_nested_scan() {
        let source = "hooks:\n  Before:\n    command: run\n---\nother: rejected\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_keeps_indented_document_markers_inside_block_scalars() {
        let source = "hooks:\n  Before: |\n    ---\n    ...\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "|\n    ---\n    ...");
    }

    #[test]
    fn hermes_yaml_pointer_resolves_aliases_inside_a_single_marked_document() {
        let source = "---\nshared: &all {Before: run}\nhooks: *all\n...\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "run");
    }

    #[test]
    fn hermes_yaml_pointer_accepts_an_indentless_root_mapping_sequence_value() {
        let source = "skills:\n- one\n- two\nhooks:\n  Before: run\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "run");
    }

    #[test]
    fn hermes_yaml_pointer_accepts_valid_flow_trailing_commas() {
        let source = "hooks: {Before: [first, second,],}\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before/1",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "second");
    }

    #[test]
    fn hermes_yaml_pointer_rejects_malformed_suffix_after_a_matching_target() {
        let source = "hooks:\n  Before:\n    command: right\nbroken: [\n";

        let result = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        );

        assert!(result.is_err());
    }

    #[test]
    fn hermes_yaml_pointer_rejects_plain_invalid_suffix_and_mixed_document_roots() {
        let plain_suffix = "hooks:\n  Before: right\nbroken\n";
        let sequence_then_mapping = "- item\nhooks:\n  Before: right\n";
        let scalar_then_mapping = "scalar\nhooks:\n  Before: right\n";

        for source in [plain_suffix, sequence_then_mapping, scalar_then_mapping] {
            assert!(resolve_hermes_yaml_pointer_span(
                ShortReader::new(source.as_bytes(), 4),
                "#/hooks/Before",
            )
            .is_err());
        }
    }

    #[test]
    fn hermes_yaml_pointer_rejects_duplicate_relevant_mapping_keys() {
        let duplicate_root = "hooks: {Before: right}\nhooks: {Before: wrong}\n";
        let duplicate_event = "hooks: {Before: right, Before: wrong}\n";
        let duplicate_block_event =
            "hooks:\n  Before:\n    command: right\n  Before:\n    command: wrong\n";
        let duplicate_unrelated_root = "hooks: {Before: right}\nother: one\nother: two\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(duplicate_root.as_bytes(), 4),
            "#/hooks/Before",
        )
        .is_err());
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(duplicate_event.as_bytes(), 4),
            "#/hooks/Before",
        )
        .is_err());
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(duplicate_block_event.as_bytes(), 4),
            "#/hooks/Before",
        )
        .is_err());
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(duplicate_unrelated_root.as_bytes(), 4),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_rejects_nested_and_non_string_duplicate_keys() {
        let duplicate_event_field = "hooks:\n  Before:\n    command: first\n    command: second\n";
        let duplicate_unrelated_nested = "hooks:\n  Before: right\nmeta:\n  x: one\n  x: two\n";
        let duplicate_numeric_root = "hooks:\n  Before: right\n1: one\n1: two\n";

        for source in [
            duplicate_event_field,
            duplicate_unrelated_nested,
            duplicate_numeric_root,
        ] {
            assert!(resolve_hermes_yaml_pointer_span(
                ShortReader::new(source.as_bytes(), 4),
                "#/hooks/Before",
            )
            .is_err());
        }
    }

    #[test]
    fn hermes_yaml_pointer_rejects_duplicates_in_an_unselected_hook_subtree() {
        let source = "hooks:\n  After:\n    x: one\n    x: two\n  Before: run\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_models_compact_sequence_item_mappings() {
        let valid = "hooks:\n  Before:\n    - env:\n        A: one\n      command: run\n";
        let duplicate = "hooks:\n  Before:\n    - command: one\n      command: two\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(valid.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .unwrap()
        .unwrap();

        assert_eq!(
            selected(valid, span),
            "env:\n        A: one\n      command: run"
        );
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(duplicate.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_validates_every_compact_mapping_scope() {
        let distinct_items = "hooks:\n  Before:\n    - command: one\n    - command: two\n";
        let nested_duplicate = "hooks:\n  Before:\n    - env:\n        A: one\n        A: two\n";
        let non_selected_duplicate =
            "hooks:\n  Before:\n    - command: selected\n    - env: one\n      env: two\n";
        let root_indentless_duplicate =
            "items:\n- command: one\n  command: two\nhooks:\n  Before: run\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(distinct_items.as_bytes(), 4),
            "#/hooks/Before/1",
        )
        .unwrap()
        .unwrap();
        assert_eq!(selected(distinct_items, span), "command: two");

        for source in [
            nested_duplicate,
            non_selected_duplicate,
            root_indentless_duplicate,
        ] {
            assert!(resolve_hermes_yaml_pointer_span(
                ShortReader::new(source.as_bytes(), 4),
                "#/hooks/Before/0",
            )
            .is_err());
        }
    }

    #[test]
    fn hermes_yaml_pointer_resolves_anchor_values_in_compact_mappings() {
        let source = "list:\n  - holder: &all {Before: ok}\nhooks: *all\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "ok");
    }

    #[test]
    fn hermes_yaml_pointer_preserves_properties_on_ordinary_sequence_nodes() {
        let latest_scalar = "old: &all {Before: old}\nitems:\n  - &all newest\nhooks: *all\n";
        let latest_flow =
            "old: &all {Before: old}\nitems:\n  - &all {After: newest}\nhooks: *all\n";
        let tagged_flow = "hooks:\n  Before:\n    - !!map {command: run}\n";

        for source in [latest_scalar, latest_flow] {
            assert_eq!(
                resolve_hermes_yaml_pointer_span(
                    ShortReader::new(source.as_bytes(), 4),
                    "#/hooks/Before",
                )
                .unwrap(),
                None
            );
        }
        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(tagged_flow.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .unwrap()
        .unwrap();
        assert_eq!(selected(tagged_flow, span), "!!map {command: run}");
    }

    #[test]
    fn hermes_yaml_pointer_preserves_scalar_comment_and_quote_semantics_in_sequences() {
        for source in [
            "hooks:\n  Before:\n    - run # note\n",
            "hooks:\n  Before:\n    - run # note: not a mapping\n",
            "hooks:\n  Before:\n    - \"run\" # note\n",
        ] {
            let span = resolve_hermes_yaml_pointer_span(
                ShortReader::new(source.as_bytes(), 4),
                "#/hooks/Before/0",
            )
            .unwrap()
            .unwrap();
            let expected = if source.contains("\"run\"") {
                "\"run\""
            } else {
                "run"
            };
            assert_eq!(selected(source, span), expected);
        }

        let unterminated = "hooks:\n  Before:\n    - \"unterminated\n";
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(unterminated.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_validates_alias_and_nested_sequence_items() {
        let alias = "shared: &item value\nhooks:\n  Before:\n    - *item\n";
        let nested = "hooks:\n  Before:\n    - - one\n      - two\n";

        let alias_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(alias.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .unwrap()
        .unwrap();
        let nested_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(nested.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .unwrap()
        .unwrap();
        assert_eq!(selected(alias, alias_span), "*item");
        assert_eq!(selected(nested, nested_span), "- one\n      - two");

        let malformed_alias = "hooks:\n  Before:\n    - *\n";
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(malformed_alias.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_validates_nested_compact_mapping_scopes() {
        let valid = "hooks:\n  Before:\n    - - command: one\n        env:\n          A: two\n      - command: three\n";
        let duplicate = "hooks:\n  Before:\n    - - command: one\n        command: two\n";
        let sibling = "hooks:\n  Before:\n    - - command: one\n      - command: two\n";

        let valid_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(valid.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .unwrap()
        .unwrap();
        let sibling_span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(sibling.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .unwrap()
        .unwrap();

        assert_eq!(
            selected(valid, valid_span),
            "- command: one\n        env:\n          A: two\n      - command: three"
        );
        assert_eq!(
            selected(sibling, sibling_span),
            "- command: one\n      - command: two"
        );
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(duplicate.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .is_err());

        let malformed_alias = "hooks:\n  Before:\n    - - *\n";
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(malformed_alias.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_resolves_anchors_inside_nested_compact_sequences() {
        let source = "list:\n  - - holder: &all {Before: ok}\nhooks: *all\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "ok");
    }

    #[test]
    fn hermes_yaml_pointer_accepts_multiline_quoted_sequence_scalars() {
        let source = "hooks:\n  Before:\n    - \"multi\n      line\"\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "\"multi\n      line\"");
    }

    #[test]
    fn hermes_yaml_pointer_finds_tag_first_key_anchors() {
        let source = "old: &all {Before: old}\n!!str &all newer: x\nhooks: *all\n";

        assert_eq!(
            resolve_hermes_yaml_pointer_span(
                ShortReader::new(source.as_bytes(), 4),
                "#/hooks/Before",
            )
            .unwrap(),
            None
        );
    }

    #[test]
    fn hermes_yaml_pointer_rejects_mapping_dedent_to_an_unknown_sequence_scope() {
        let source = "hooks:\n  Before: run\nmeta:\n  list:\n    - one\n   x: two\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_rejects_invalid_collection_transitions_after_scalar_items() {
        let same_indent = "hooks:\n  Before: run\nmeta:\n  list:\n    - one\n    x: two\n";
        let deeper_indent = "hooks:\n  Before: run\nmeta:\n  list:\n    - one\n      x: two\n";
        let unknown_sequence_dedent =
            "hooks:\n  Before: run\nmeta:\n  list:\n    - one\n   - two\n";
        let mixed_indentless = "list:\n  - one\n  x: two\nhooks:\n  Before: run\n";

        for source in [
            same_indent,
            deeper_indent,
            unknown_sequence_dedent,
            mixed_indentless,
        ] {
            assert!(resolve_hermes_yaml_pointer_span(
                ShortReader::new(source.as_bytes(), 4),
                "#/hooks/Before",
            )
            .is_err());
        }
    }

    #[test]
    fn hermes_yaml_pointer_caps_active_sequence_depth_not_historical_sibling_indents() {
        let mut source = String::new();
        for index in 0..=MAX_JSON_DEPTH {
            source.push_str(&format!("s{index}:\n{}- item\n", " ".repeat(index + 1)));
        }
        source.push_str("hooks:\n  Before: run\n");

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 256),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();
        assert_eq!(selected(&source, span), "run");
    }

    #[test]
    fn hermes_yaml_pointer_keeps_flow_sequence_items_out_of_compact_mapping_mode() {
        let source = "hooks:\n  Before:\n    - {env: {A: one}, command: run}\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before/0",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "{env: {A: one}, command: run}");
    }

    #[test]
    fn hermes_yaml_pointer_caps_compact_mapping_keys() {
        fn compact_mapping(key_count: usize) -> String {
            let mut source = String::from("hooks:\n  Before:\n    - k0: value\n");
            for index in 1..key_count {
                source.push_str(&format!("      k{index}: value\n"));
            }
            source
        }

        let at_limit = compact_mapping(4096);
        let over_limit = compact_mapping(4097);

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(at_limit.as_bytes(), 4096),
            "#/hooks/Before/0",
        )
        .unwrap()
        .is_some());
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(over_limit.as_bytes(), 4096),
            "#/hooks/Before/0",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_caps_block_mapping_depth() {
        let mut source = String::from("meta:\n");
        for depth in 0..=MAX_JSON_DEPTH {
            source.push_str(&format!("{}k{depth}:\n", "  ".repeat(depth + 1)));
        }
        source.push_str(&format!("{}leaf: value\n", "  ".repeat(MAX_JSON_DEPTH + 2)));
        source.push_str("hooks:\n  Before: run\n");

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 7),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_rejects_a_new_mapping_indent_during_dedent() {
        let source = "hooks:\n  Before: right\nmeta:\n    x: one\n  y: two\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 4),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_keeps_sequence_like_lines_in_plain_scalar_continuations() {
        let source = "hooks:\n  Before: run\n    - x\n      - y\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "run\n    - x\n      - y");
    }

    #[test]
    fn hermes_yaml_pointer_rejects_mapping_after_selected_plain_scalar() {
        for source in [
            "hooks:\n  Before: run\n    command: injected\n",
            "hooks:\n  Before: run\n    {command: injected}\n",
        ] {
            assert!(resolve_hermes_yaml_pointer_span(
                ShortReader::new(source.as_bytes(), 3),
                "#/hooks/Before",
            )
            .is_err());
        }
    }

    #[test]
    fn hermes_yaml_pointer_keeps_unterminated_quote_text_opaque_in_block_scalar() {
        let source = "hooks:\n  Before: |\n    - \"unterminated\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "|\n    - \"unterminated");
    }

    #[test]
    fn hermes_yaml_pointer_rejects_mapping_after_scalar_in_compact_or_empty_sequence_item() {
        for source in [
            "hooks:\n  Before: run\nmeta:\n  list:\n    - command: one\n      scalar\n      env: two\n",
            "hooks:\n  Before: run\nmeta:\n  list:\n    -\n      scalar\n      env: two\n",
        ] {
            assert!(resolve_hermes_yaml_pointer_span(
                ShortReader::new(source.as_bytes(), 4),
                "#/hooks/Before",
            )
            .is_err());
        }
    }

    #[test]
    fn hermes_yaml_pointer_rejects_invalid_double_quoted_escape() {
        let source = "hooks:\n  Before: \"bad\\q\"\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 2),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_rejects_duplicate_node_properties() {
        let source = "invalid: &one &two scalar\nhooks: {Before: run}\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_accepts_one_tag_and_one_anchor_property() {
        let source = "meta: !!str &one scalar\nhooks: {Before: run}\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "run");
    }

    #[test]
    fn hermes_yaml_pointer_accepts_a_plain_scalar_child_and_its_continuation() {
        let source = "meta:\n  value:\n    first\n      - continuation\nhooks:\n  Before: run\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "run");
    }

    #[test]
    fn hermes_yaml_pointer_accepts_valid_double_quoted_escapes() {
        for value in [
            r#""line\nnext""#,
            r#""hex \x41 \u0041""#,
            "\"folded\\\n    line\"",
        ] {
            let source = format!("hooks:\n  Before: {value}\n");
            assert!(resolve_hermes_yaml_pointer_span(
                ShortReader::new(source.as_bytes(), 2),
                "#/hooks/Before",
            )
            .unwrap()
            .is_some());
        }
    }

    #[test]
    fn hermes_yaml_pointer_accepts_flow_plain_url_values() {
        let source = "hooks: {Before: http://example.invalid/path}\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "http://example.invalid/path");
    }

    #[test]
    fn hermes_yaml_pointer_caps_flow_collection_depth_including_empty_leaf_nodes() {
        fn source_with_frames(frame_count: usize, leaf: &str) -> String {
            let mut nested = leaf.to_string();
            for index in 0..frame_count.saturating_sub(2) {
                nested = format!("{{k{index}: {nested}}}");
            }
            format!("{{hooks: {{Before: run}}, meta: {nested}}}\n")
        }

        for leaf in ["{}", "[]"] {
            let at_limit = source_with_frames(MAX_JSON_DEPTH, leaf);
            let over_limit = source_with_frames(MAX_JSON_DEPTH + 1, leaf);
            let span = resolve_hermes_yaml_pointer_span(
                ShortReader::new(at_limit.as_bytes(), 64),
                "#/hooks/Before",
            )
            .unwrap()
            .unwrap();
            assert_eq!(selected(&at_limit, span), "run");
            let error = resolve_hermes_yaml_pointer_span(
                ShortReader::new(over_limit.as_bytes(), 64),
                "#/hooks/Before",
            )
            .unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        }
    }

    #[test]
    fn hermes_yaml_pointer_caps_root_block_mapping_keys() {
        fn root_mapping(key_count: usize) -> String {
            let mut source = String::from("hooks:\n  Before: run\n");
            for index in 1..key_count {
                source.push_str(&format!("k{index}: value\n"));
            }
            source
        }

        let at_limit = root_mapping(4096);
        let over_limit = root_mapping(4097);
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(at_limit.as_bytes(), 4096),
            "#/hooks/Before",
        )
        .unwrap()
        .is_some());
        let error = resolve_hermes_yaml_pointer_span(
            ShortReader::new(over_limit.as_bytes(), 4096),
            "#/hooks/Before",
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn hermes_yaml_pointer_caps_root_flow_mapping_keys() {
        fn flow_mapping(key_count: usize) -> String {
            let mut source = String::from("{hooks: {Before: run}");
            for index in 1..key_count {
                source.push_str(&format!(", k{index}: value"));
            }
            source.push_str("}\n");
            source
        }

        let at_limit = flow_mapping(4096);
        let over_limit = flow_mapping(4097);
        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(at_limit.as_bytes(), 4096),
            "#/hooks/Before",
        )
        .unwrap()
        .is_some());
        let error = resolve_hermes_yaml_pointer_span(
            ShortReader::new(over_limit.as_bytes(), 4096),
            "#/hooks/Before",
        )
        .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn hermes_yaml_pointer_rejects_malformed_root_flow_sequence() {
        let source = "[broken\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 2),
            "#/hooks/Before",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_accepts_colons_inside_flow_plain_keys() {
        let source = "hooks: {http://x: run}\n";

        let span = resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 2),
            "#/hooks/http:~1~1x",
        )
        .unwrap()
        .unwrap();

        assert_eq!(selected(source, span), "run");
    }

    #[test]
    fn hermes_yaml_pointer_rejects_undefined_alias_in_selected_sequence() {
        let source = "hooks:\n  Before:\n    - *missing\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 3),
            "#/hooks/Before/0",
        )
        .is_err());
    }

    #[test]
    fn hermes_yaml_pointer_rejects_a_second_mapping_separator_in_flow_scalar() {
        let source = "hooks: {Before: bad: scalar}\n";

        assert!(resolve_hermes_yaml_pointer_span(
            ShortReader::new(source.as_bytes(), 2),
            "#/hooks/Before",
        )
        .is_err());
    }
}
