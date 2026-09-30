export const inventoryFixture = Object.freeze({
  items: [
    {
      component_id: "alpha-skill",
      target: "codex",
      kind: "skill",
      status: "semi-concrete",
      title: "Alpha skill",
      summary: "Deterministic alpha summary",
      domain: "core",
      source_path: "dist/codex/skills/alpha-skill/SKILL.md",
      install_status: "Drift",
      profile_memberships: ["engineering"],
      provenance_mode: "original",
      owned_files: ["components/skills/alpha-skill/SKILL.md"],
      foreign_classification: "HarnessKitOwned",
      locations: [
        {
          scope: "Project",
          path: "/work/app/.codex/skills/alpha-skill/SKILL.md",
          install_status: "Drift",
        },
        {
          scope: "User",
          path: "/fixture-home/.codex/skills/alpha-skill/SKILL.md",
          install_status: "Match",
        },
      ],
    },
    {
      component_id: "beta-agent",
      target: "claude",
      kind: "agent",
      status: "draft",
      title: null,
      summary: null,
      domain: "work",
      source_path: "dist/claude/agents/beta-agent.md",
      install_status: "Missing",
      profile_memberships: ["core-profile"],
      provenance_mode: "adapted",
      owned_files: ["components/agents/beta-agent/agent.md"],
      foreign_classification: "HarnessKitOwned",
      locations: [
        {
          scope: "User",
          path: "/fixture-home/.claude/agents/beta-agent.md",
          install_status: "Missing",
        },
      ],
    },
  ],
  orphan_discoveries: [
    {
      scope: "Project",
      path: "/work/app/.codex/skills/loose/SKILL.md",
      classification: "Foreign",
    },
    {
      scope: "User",
      path: "/fixture-home/.codex/skills/draft/component.yml",
      classification: "RegistryUnregistered",
    },
  ],
  dashboard: {
    kind_counts: { agent: 11, skill: 29 },
    domain_counts: { core: 17, work: 23 },
    status_counts: { draft: 31, "semi-concrete": 37 },
    target_installable_counts: { claude: 41, codex: 43 },
    target_install_counts: {
      claude: { match: 47, missing: 53 },
      codex: { drift: 59, match: 61 },
    },
    profile_component_counts: { "core-profile": 67, engineering: 71 },
    unprofiled_component_count: 73,
    provenance_mode_counts: { adapted: 79, original: 83 },
  },
  scan_metadata: {
    scan_timestamp: "2026-07-11T00:00:00Z",
    app_version: "0.1.0",
    user_level_surfaces: ["Codex", "Claude"],
    skipped_path_count: 0,
  },
});
