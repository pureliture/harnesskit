use ignore::gitignore::GitignoreBuilder;
use std::path::Path;
use walkdir::WalkDir;

use crate::models::{ScanIssue, ScanItem, ScanResult, Scope, SurfaceMarker};

/// ignore rule — scan에서 배제할 디렉터리/패턴
const IGNORED_DIRS: &[&str] = &[
    "node_modules",
    ".git",
    "Library",
    ".Trash",
    ".cache",
    "__pycache__",
    "dist",
    "target",
    ".next",
    ".nuxt",
    ".svelte-kit",
    "venv",
    ".venv",
];

/// user-level 고정 경로 목록
const USER_LEVEL_PATHS: &[(&str, SurfaceMarker)] = &[
    (".claude", SurfaceMarker::Claude),
    (".codex", SurfaceMarker::Codex),
    (".agents", SurfaceMarker::Agents),
    (".acli", SurfaceMarker::Acli),
    (".harnesskit", SurfaceMarker::HarnessKit),
    (".gemini", SurfaceMarker::Gemini),
    (".hermes", SurfaceMarker::Hermes),
];

/// project-level surface markers (디렉터리)
const PROJECT_DIR_MARKERS: &[&str] = &[".claude", ".codex", ".agents", ".acli", ".harnesskit"];

/// project-level surface markers (파일)
const PROJECT_FILE_MARKERS: &[&str] = &["AGENTS.md", "CLAUDE.md"];

pub struct Scanner;

impl Scanner {
    fn traversal_issue(error: &walkdir::Error, fallback: &Path) -> ScanIssue {
        let category = if error
            .io_error()
            .is_some_and(|source| source.kind() == std::io::ErrorKind::PermissionDenied)
        {
            "permission_denied"
        } else {
            "scan_path_unreadable"
        };

        ScanIssue {
            category: category.to_string(),
            path: error
                .path()
                .unwrap_or(fallback)
                .to_string_lossy()
                .to_string(),
            message: "경로를 읽지 못해 일부 항목을 스캔하지 못했습니다.".to_string(),
        }
    }

    fn scan_user_level_with_issues(home: &Path) -> (Vec<ScanItem>, Vec<ScanIssue>) {
        let mut items = Vec::new();
        let mut issues = Vec::new();
        for (dir_name, marker) in USER_LEVEL_PATHS {
            let path = home.join(dir_name);
            if path.is_dir() {
                for result in WalkDir::new(&path).max_depth(3) {
                    let entry = match result {
                        Ok(entry) => entry,
                        Err(error) => {
                            issues.push(Self::traversal_issue(&error, &path));
                            continue;
                        }
                    };
                    if entry.file_type().is_file() {
                        let file_name = entry.file_name().to_string_lossy().to_string();
                        if Self::is_harness_file(&file_name) {
                            items.push(ScanItem {
                                path: entry.path().to_string_lossy().to_string(),
                                surface: marker.clone(),
                                scope: Scope::User,
                            });
                        }
                    }
                }
            }
        }
        (items, issues)
    }

    /// user-level surface 자동 식별
    pub fn scan_user_level(home: &Path) -> Vec<ScanItem> {
        Self::scan_user_level_with_issues(home).0
    }

    /// user-level surface 존재 여부만 확인
    pub fn detect_user_level_surfaces(home: &Path) -> Vec<SurfaceMarker> {
        let mut surfaces = Vec::new();
        for (dir_name, marker) in USER_LEVEL_PATHS {
            let path = home.join(dir_name);
            if path.exists() && path.is_dir() {
                surfaces.push(marker.clone());
            }
        }
        surfaces
    }

    /// project-level home 전수 검색 (ignore rule 기반)
    pub fn scan_project_level(home: &Path) -> (Vec<ScanItem>, Vec<String>) {
        let (items, skipped, _) = Self::scan_project_level_with_issues(home);
        (items, skipped)
    }

    fn scan_project_level_with_issues(home: &Path) -> (Vec<ScanItem>, Vec<String>, Vec<ScanIssue>) {
        let mut items = Vec::new();
        let mut skipped = Vec::new();
        let mut issues = Vec::new();

        let mut ig_builder = GitignoreBuilder::new(home);
        for dir in IGNORED_DIRS {
            let _ = ig_builder.add_line(None, &format!("{}/", dir));
        }
        let ig = ig_builder
            .build()
            .unwrap_or_else(|_| GitignoreBuilder::new(home).build().unwrap());

        for result in WalkDir::new(home)
            .max_depth(8)
            .follow_links(false)
            .into_iter()
            .filter_entry(|e| {
                let rel = e.path().strip_prefix(home).unwrap_or(e.path());
                let rel_str = rel.to_string_lossy().to_string();
                if rel_str.is_empty() {
                    return true;
                }
                if USER_LEVEL_PATHS
                    .iter()
                    .any(|(dir_name, _)| rel == Path::new(dir_name))
                {
                    return false;
                }
                let matched = ig.matched(rel, e.file_type().is_dir());
                if matched.is_ignore() {
                    skipped.push(e.path().to_string_lossy().to_string());
                    false
                } else {
                    true
                }
            })
        {
            let entry = match result {
                Ok(entry) => entry,
                Err(error) => {
                    issues.push(Self::traversal_issue(&error, home));
                    continue;
                }
            };
            if !entry.file_type().is_dir() {
                continue;
            }

            let dir_name = entry.file_name().to_string_lossy().to_string();

            // harness surface marker 디렉터리인지 확인
            if PROJECT_DIR_MARKERS.contains(&dir_name.as_str()) {
                let marker = Self::dir_name_to_marker(&dir_name);
                // 이 디렉터리 내부의 harness component 파일 탐지
                for file_result in WalkDir::new(entry.path()).max_depth(3) {
                    let file_entry = match file_result {
                        Ok(file_entry) => file_entry,
                        Err(error) => {
                            issues.push(Self::traversal_issue(&error, entry.path()));
                            continue;
                        }
                    };
                    if file_entry.file_type().is_file() {
                        let file_name = file_entry.file_name().to_string_lossy().to_string();
                        if Self::is_harness_file(&file_name) {
                            items.push(ScanItem {
                                path: file_entry.path().to_string_lossy().to_string(),
                                surface: marker.clone(),
                                scope: Scope::Project,
                            });
                        }
                    }
                }
            }

            // harness surface marker 파일인지 확인
            for file_marker in PROJECT_FILE_MARKERS {
                let file_path = entry.path().join(file_marker);
                if file_path.exists() && file_path.is_file() {
                    let marker = Self::file_name_to_marker(file_marker);
                    items.push(ScanItem {
                        path: file_path.to_string_lossy().to_string(),
                        surface: marker,
                        scope: Scope::Project,
                    });
                }
            }
        }

        (items, skipped, issues)
    }

    /// harness component 파일 판별
    fn is_harness_file(file_name: &str) -> bool {
        matches!(
            file_name,
            "SKILL.md"
                | "component.yml"
                | "agent.yml"
                | "prompt.md"
                | "hook.md"
                | "workflow.yml"
                | "composite.yml"
                | "provenance.map.yml"
        )
    }

    fn dir_name_to_marker(name: &str) -> SurfaceMarker {
        match name {
            ".claude" => SurfaceMarker::Claude,
            ".codex" => SurfaceMarker::Codex,
            ".agents" => SurfaceMarker::Agents,
            ".acli" => SurfaceMarker::Acli,
            ".harnesskit" => SurfaceMarker::HarnessKit,
            ".gemini" => SurfaceMarker::Gemini,
            ".hermes" => SurfaceMarker::Hermes,
            _ => SurfaceMarker::Claude,
        }
    }

    fn file_name_to_marker(name: &str) -> SurfaceMarker {
        match name {
            "AGENTS.md" => SurfaceMarker::AgentsMd,
            "CLAUDE.md" => SurfaceMarker::ClaudeMd,
            _ => SurfaceMarker::Claude,
        }
    }

    /// 전체 scan 실행
    pub fn scan(home: &Path) -> ScanResult {
        if !home.is_dir() {
            return ScanResult {
                items: Vec::new(),
                user_level_surfaces: Vec::new(),
                skipped_paths: Vec::new(),
                issues: vec![ScanIssue {
                    category: "scan_root_unavailable".to_string(),
                    path: home.to_string_lossy().to_string(),
                    message: "스캔 루트를 읽을 수 없습니다.".to_string(),
                }],
            };
        }

        let (user_items, mut issues) = Self::scan_user_level_with_issues(home);
        let user_surfaces = Self::detect_user_level_surfaces(home);
        let (project_items, skipped, project_issues) = Self::scan_project_level_with_issues(home);
        issues.extend(project_issues);

        let mut all_items = user_items;
        all_items.extend(project_items);

        // stable sort: scope → surface → path
        all_items.sort_by(|a, b| {
            let scope_ord = a.scope.cmp(&b.scope);
            if scope_ord != std::cmp::Ordering::Equal {
                return scope_ord;
            }
            let surface_ord = format!("{:?}", a.surface).cmp(&format!("{:?}", b.surface));
            if surface_ord != std::cmp::Ordering::Equal {
                return surface_ord;
            }
            a.path.cmp(&b.path)
        });
        issues.sort_by(|a, b| {
            (&a.category, &a.path, &a.message).cmp(&(&b.category, &b.path, &b.message))
        });
        issues.dedup();

        ScanResult {
            items: all_items,
            user_level_surfaces: user_surfaces,
            skipped_paths: skipped,
            issues,
        }
    }
}
