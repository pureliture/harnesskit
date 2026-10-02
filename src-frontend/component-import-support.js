// Qualification describes current implementation, never target-tool runtime support.
const REASONS = Object.freeze({
  import_rule_block_ambiguous: "기존 고정 HarnessKit 관리 블록이 누락·중복·역순이거나 legacy marker와 함께 있습니다. 파일 전체 관리나 새 블록 추가로 대신 처리하지 않습니다.",
  import_rule_project_required: "기존 프로젝트 root에서 승인된 규칙만 후보로 처리합니다. 사용자 지침은 관리하지 않습니다.",
  import_rule_surface_unqualified: "선택 규칙은 현재 project AGENTS.md/CLAUDE.md 쓰기 계약에 없습니다. 독립 rules 변환은 별도 계약·구현 대기입니다.",
  management_destination_collision: "동일한 프로젝트 관리 블록을 다른 컴포넌트가 선택했습니다. 기존 project-context와 함께 덮어쓰지 않습니다.",
  import_skill_option_invalid: "스킬 메타데이터 필드의 형식·값 또는 함께 필요한 context가 맞지 않습니다. 의미를 바꾸어 등록하지 않습니다.",
  import_skill_dependency_unresolved: "스킬의 사용자 정의 agent 의존성 연결은 검증되지 않았습니다. 변환 구현 대기이며 portable 불가능 판정은 아닙니다.",
  import_skill_metadata_edit_unsupported: "본문 편집에서는 가져온 메타데이터를 유지해야 합니다. 운영 옵션 편집 연결은 구현 대기입니다.",
  import_agent_toml_invalid: "Agent TOML이 유효하지 않습니다. 중복 필드와 잘못된 설정을 추측하여 등록하지 않습니다.",
  import_agent_registration_ambiguous: "Codex config.toml의 선택 등록 항목이 누락·중복되었거나 경로가 모호합니다. 다른 항목을 대신 관리하지 않습니다.",
  import_agent_registration_mismatch: "Codex agent와 등록 항목의 description 또는 config_file이 일치하지 않습니다. 원본을 바꾸어 등록하지 않습니다.",
  import_agent_name_path_mismatch: "Agent 이름과 원본 파일명이 다릅니다. 이름 변경이나 다른 목적지 관리로 대신 처리하지 않습니다.",
  import_agent_option_unsupported: "Agent 운영 옵션 형식을 손실 없이 변환할 수 없습니다. Antigravity IDE는 세 tools 필드가 모두 boolean이어야 하며 누락·추가·타입 변환 없이 검사합니다.",
  import_frontmatter_unsupported: "검증 범위 밖의 알 수 없는 메타데이터 필드가 있습니다. 보존 가능한 typed 필드와 구분하며 알 수 없는 필드를 버려 등록하지 않습니다.",
  import_support_files_unsupported: "지원 파일이 현재 검증 범위에 없습니다. 명시적 inline Markdown 링크의 비실행 .md/.txt 문서만 보존하며 미선택 파일을 누락시켜 등록하지 않습니다.",
  import_support_reference_unsupported: "지원 문서는 안전한 상대 경로의 inline Markdown 링크만 검증합니다. reference-style·autolink·외부 경로 또는 편집으로 바뀐 의존성 집합은 조용히 버리지 않고 차단합니다.",
  import_sensitive_or_dependency_content: "비밀정보·개인 경로 또는 의존성 징후를 발견했습니다. 안전한 변환 여부를 확인해야 합니다.",
  import_standalone_skill_required: "이 종류·설정 항목의 변환기는 구현 대기입니다. portable 변환 불가능 판정이 아닙니다.",
  import_hermes_package_policy_unqualified: "Hermes 외부 패키지 정책과 원본 경로 관리 연결이 아직 검증되지 않았습니다.",
  import_skill_install_path_conflict: "발견 경로와 설치 경로가 다릅니다. 원본 이외 경로를 관리 대상으로 대신 사용하지 않습니다.",
  import_hook_item_ambiguous: "공유 훅 항목 식별이 모호합니다. 여러 group/handler를 임의로 선택하지 않습니다.",
  import_hook_dependency_unsupported: "훅의 명령·지원 스크립트 의존성 변환은 구현 대기입니다. 명령을 실행하여 검사하지 않습니다.",
  import_collision: "기존 컴포넌트 ID 또는 파일과 충돌합니다. 자동으로 덮어쓰지 않습니다.",
});

export function componentImportReason(code) {
  return REASONS[code] ? `${REASONS[code]} (${code})` : String(code ?? "unknown");
}
