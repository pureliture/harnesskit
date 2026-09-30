# OpenClaw Integration Design Notes

> Status: Design proposal — not yet implemented
> Last updated: 2026-05-16

## 개요

Routine-Harness는 외부 skill/agent 소스를 내부에 복제·재구성하여 독립 실행 가능한 컴포넌트로 만든다. OpenClaw는 외부 소스의 변경을 감지하고, 그 변경이 Routine-Harness의 어떤 컴포넌트에 영향을 주는지 분석한 뒤, 개선 필요 사항을 issue로 등록하는 역할을 맡는다.

이 문서는 **Routine-Harness가 OpenClaw에게 제공해야 할 데이터**와 **그 데이터의 위치/형식/경계**를 정의한다.

## 데이터 경계

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenClaw (External Tool)                  │
│  - upstream 소스 변경 감지 (GitHub API, git fetch 등)          │
│  - diff 분석                                               │
│  - issue 생성/등록                                          │
│  - automation trigger 설정                                │
└─────────────────────────────────────────────────────────────┘
                            ↑ consumes
                            │ `openclaw-impact-report.schema.json`
┌─────────────────────────────────────────────────────────────┐
│              Routine-Harness (This Repository)               │
│  - source snapshot metadata                                │
│  - component provenance map                                │
│  - component registry                                      │
│  - report schema definition                                │
│  - issue template                                          │
└─────────────────────────────────────────────────────────────┘
```

### Routine-Harness가 제공하는 것 (Data Provider)

| 데이터 | 위치 | 형식 | 설명 |
|--------|------|------|------|
| 추적 중인 source 목록 | `sources/registry.yml` | YAML | OpenClaw가 감시할 upstream 대상 목록 |
| source snapshot 메타데이터 | `sources/snapshots/<id>/source.yml` | YAML | 마지막으로 관측된 commit, hash, timestamp |
| component catalog | `components/registry.yml` | YAML | 전체 컴포넌트 목록과 상태 |
| provenance 매핑 | `components/**/provenance.map.yml` | YAML | 각 컴포넌트가 어떤 upstream source의 어떤 파일을 참조하는지 |
| report schema | `schemas/openclaw-impact-report.schema.json` | JSON Schema | OpenClaw가 생성할 impact report의 구조 정의 |
| issue 템플릿 | `components/reports/templates/upstream-impact-issue.md` | Markdown | OpenClaw가 생성할 issue의 body 템플릿 |

### OpenClaw가 처리하는 것 (Data Consumer)

| 작업 | 설명 |
|------|------|
| upstream 변경 감지 | `sources/registry.yml`의 URL을 주기적으로 polling 또는 webhook으로 감시 |
| diff 분석 | 변경된 파일이 Routine-Harness 컴포넌트의 provenance에 포함되는지 확인 |
| report 생성 | `schemas/openclaw-impact-report.schema.json`에 맞춰 structured report 생성 |
| issue 생성 | `components/reports/templates/upstream-impact-issue.md` 템플릿을 기반으로 issue body 작성 |
| 등록 | GitHub issue 또는 프로젝트 관리 도구에 자동 등록 |

## OpenClaw가 데이터를 읽는 방식 (미정)

> ⚠️ 이 섹션은 아직 확정되지 않은 설계 결정이다.

OpenClaw가 Routine-Harness 저장소를 어떻게 접근할지는 아직 결정되지 않았다. 다음은 고려 중인 옵션이다.

### 고려 중인 접근 방법

1. **Git clone + local read**: Routine-Harness 저장소를 클론하여 `sources/`, `components/`, `schemas/`를 로컬에서 읽는다.
2. **Raw GitHub URL**: `https://raw.githubusercontent.com/<user>/<repo>/main/sources/registry.yml` 등으로 직접 fetch.
3. **GitHub API**: repository contents API를 사용하여 파일 목록과 내용을 가져온다.

### OpenClaw가 생성하는 산출물 (위치 미정)

> ⚠️ 산출물의 위치와 형식은 아직 확정되지 않았다.

OpenClaw가 생성할 수 있는 산출물:

- `openclaw-impact-report-*.json` — `schemas/openclaw-impact-report.schema.json`에 맞춘 impact report
- GitHub Issue — `components/reports/templates/upstream-impact-issue.md` 템플릿 기반

이 산출물이 Routine-Harness 저장소 내부에 저장될지, 외부에 저장될지, 아니면 다른 형태로 관리될지는 아직 결정되지 않았다.

## Report Schema 상세

`schemas/openclaw-impact-report.schema.json`은 다음 핵심 필드를 정의한다:

```json
{
  "report_id": "obra-superpowers-def4567-20260516120000",
  "generated_at": "2026-05-16T12:00:00+09:00",
  "generator": "openclaw",
  "source_change": {
    "source_id": "obra-superpowers",
    "old_snapshot": { "commit": "abc1234...", "snapshot_id": "..." },
    "new_snapshot": { "commit": "def4567...", "snapshot_id": "..." },
    "diff_url": "https://github.com/obra/superpowers/compare/abc1234..def4567",
    "change_type": "commit_changed",
    "changed_files": [
      { "path": "skills/test-driven-development/SKILL.md", "change_kind": "modified" }
    ]
  },
  "affected_components": [
    {
      "component_id": "rh.skill.tdd",
      "component_path": "components/skills/tdd/",
      "impact_level": "high",
      "impact_reason": "Upstream TDD skill file modified. Copied content may need re-adaptation.",
      "provenance_refs": [
        {
          "source_id": "obra-superpowers",
          "source_path": "skills/test-driven-development/SKILL.md",
          "influence": ["TDD loop structure", "test-first discipline"],
          "copied_content": true
        }
      ]
    }
  ],
  "suggested_issues": [
    {
      "issue_type": "upstream-impact",
      "title": "[Upstream] obra/superpowers test-driven-development changed",
      "body_template_ref": "components/reports/templates/upstream-impact-issue.md",
      "priority": "p1",
      "labels": ["upstream-impact", "review-needed", "skill"],
      "affected_component_ids": ["rh.skill.tdd"]
    }
  ]
}
```

## Issue 템플릿 상세

`components/reports/templates/upstream-impact-issue.md`는 다음 변수를 placeholder로 사용한다:

- `{source_id}` — 변경된 upstream source
- `{old_commit_short}` — 이전 commit short SHA
- `{new_commit_short}` — 새로운 commit short SHA
- `{component_id}` — 영향받은 컴포넌트
- `{component_kind}` — 컴포넌트 종류 (skill, agent 등)
- `{impact_level}` — critical/high/medium/low/info
- `{impact_reason}` — 영향도 설명
- `{provenance_source_path}` — provenance에서 참조된 upstream 파일 경로
- `{diff_url}` — upstream diff URL
- `{component_path}` — 컴포넌트 디렉토리 경로

## 향후 고려사항

1. **Webhook 기반 실시간 알림**: GitHub webhook을 받아 upstream push 시 즉시 OpenClaw 트리거
2. **License 변경 감지**: upstream license 파일 변경 시 별도 `license-review` issue 생성
3. **Security advisory 연동**: upstream의 CVE/advisory가 Routine-Harness 컴포넌트에 영향을 주는지 분석
4. **자동 PR 생성**: 단순한 upstream sync (예: 오타 수정)는 OpenClaw가 직접 PR을 생성할 수 있도록 확장

## 관련 파일

| 파일 | 설명 |
|------|------|
| `schemas/openclaw-impact-report.schema.json` | OpenClaw impact report의 JSON Schema |
| `components/reports/templates/upstream-impact-issue.md` | OpenClaw issue body 템플릿 |
| `sources/registry.yml` | 추적 중인 upstream source 목록 |
| `components/registry.yml` | 전체 컴포넌트 catalog |
| `components/**/provenance.map.yml` | 컴포넌트별 provenance 매핑 |
| `schemas/provenance.schema.json` | provenance.map.yml의 검증 schema |
