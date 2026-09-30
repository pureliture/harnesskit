mod activation_inbox;
pub mod api;
mod app_controller;
pub mod app_service;
pub mod checkout;
pub mod contexts;
pub mod controller;
pub mod dashboard;
pub mod foreign_detector;
pub mod hash_engine;
pub mod install_flow;
pub mod inventory_builder;
pub mod models;
mod operation_coordinator;
mod projection;
pub mod registry_reader;
pub mod repo_status;
pub mod safety;
pub mod scanner;
pub mod subprocess_runner;
pub mod support;

use std::os::unix::fs::PermissionsExt;
use std::sync::Arc;
use std::time::Duration;

use activation_inbox::ActivationInbox;
use app_controller::AppController;
use contexts::ai::AiExplanationContext;
use contexts::appearance::{
    AppearanceContext, AppearanceStore, AppearanceWindowPort, BootstrapRecoveryAction,
    ResolvedMode, TauriAppearanceWindowPort, WindowThemeOverride,
};
use contexts::install::{
    BoundedPlanProcess, BundledInstallRuntime, FixedInstallPlanRunner, InstallCoordinator,
};
use contexts::layout::{WorkspaceLayoutContext, WorkspaceLayoutStore};
use contexts::local::{
    AdapterCatalog, DiscoveryScanExecutor, LocalContext, ScanEnvironment, TauriNativePathActionPort,
};
use contexts::sot::checkout_picker::{
    cancel_active_checkout_picker, SingleFlightCheckoutDirectoryPickerPort,
    TauriCheckoutDirectoryPickerPort,
};
use contexts::typography::{TypographyContext, TypographyStore};
use operation_coordinator::OperationCoordinator;
use tauri::{Manager, WindowEvent};

mod install_runtime_manifest {
    include!(concat!(env!("OUT_DIR"), "/install_runtime_manifest.rs"));
}

fn package_build_identity_marker() -> &'static str {
    concat!(
        "HARNESS_PACKAGE_BUILD_ID=",
        env!("HARNESS_PACKAGE_BUILD_ID_EMBEDDED"),
        "\nHARNESS_PACKAGE_BUILD_INPUTS_SHA256=",
        env!("HARNESS_PACKAGE_BUILD_INPUTS_SHA256_EMBEDDED"),
        "\n"
    )
}

const BOOTSTRAP_TIMEOUT: Duration = Duration::from_secs(5);

fn schedule_bootstrap_watchdog(controller: Arc<AppController>, generation: u64) {
    std::thread::spawn(move || {
        std::thread::sleep(BOOTSTRAP_TIMEOUT);
        log::info!("appearance bootstrap watchdog reached generation {generation}");
        match controller.handle_bootstrap_timeout(generation) {
            Ok(Some(recovery)) if recovery.action == BootstrapRecoveryAction::Retry => {
                log::info!(
                    "appearance bootstrap retry scheduled for generation {}",
                    recovery.generation
                );
                schedule_bootstrap_watchdog(controller, recovery.generation);
            }
            Ok(Some(recovery)) => {
                log::info!(
                    "appearance bootstrap recovery selected {:?}",
                    recovery.action
                );
            }
            Ok(None) => {
                log::info!("appearance bootstrap watchdog was stale or already completed");
            }
            Err(error) => {
                log::error!("appearance bootstrap watchdog failed: {}", error.code);
            }
        }
    });
}

fn build_install_coordinator(
    app: &tauri::App,
    app_data_dir: &std::path::Path,
) -> Result<InstallCoordinator, String> {
    let sandbox = std::fs::symlink_metadata(contexts::install::SANDBOX_EXECUTABLE)
        .map_err(|_| "install_runtime_unavailable".to_string())?;
    if sandbox.file_type().is_symlink()
        || !sandbox.file_type().is_file()
        || sandbox.permissions().mode() & 0o111 == 0
    {
        return Err("install_runtime_unavailable".to_string());
    }
    let target = format!("{}-apple-darwin", std::env::consts::ARCH);
    let resource_root = app
        .path()
        .resource_dir()
        .map_err(|_| "install_runtime_unavailable".to_string())?
        .join("install-runtime")
        .join(target);
    let runtime = BundledInstallRuntime::from_embedded_manifest(
        resource_root,
        install_runtime_manifest::INSTALL_RUNTIME_MANIFEST_JSON,
        include_str!("../install-runtime.manifest.sha256").trim(),
    )
    .map_err(|error| error.code().to_string())?;
    let runner = FixedInstallPlanRunner::new(runtime, BoundedPlanProcess)
        .map_err(|error| error.code().to_string())?;
    let app_temp_root = app_data_dir.join("install-workspaces");
    std::fs::create_dir_all(&app_temp_root)
        .map_err(|_| "install_workspace_unavailable".to_string())?;
    std::fs::set_permissions(&app_temp_root, std::fs::Permissions::from_mode(0o700))
        .map_err(|_| "install_workspace_unavailable".to_string())?;
    let app_temp_root = std::fs::canonicalize(app_temp_root)
        .map_err(|_| "install_workspace_unavailable".to_string())?;
    InstallCoordinator::new(app_temp_root, Arc::new(runner))
        .map_err(|error| error.code().to_string())
}

pub fn run() {
    std::hint::black_box(package_build_identity_marker());
    let activation_inbox = ActivationInbox::default();
    let setup_activation_inbox = activation_inbox.clone();
    let builder = tauri::Builder::default();
    #[cfg(any(target_os = "macos", windows, target_os = "linux"))]
    let builder = builder.plugin(tauri_plugin_single_instance::init(
        move |_app, _args, _cwd| {
            log::info!("single-instance relaunch received");
            activation_inbox.request();
        },
    ));
    let app = builder
        .on_window_event(|window, event| {
            if window.label() != "main" {
                return;
            }
            let theme = match event {
                WindowEvent::Focused(is_focused) => {
                    log::info!("appearance main window focus changed: focused={is_focused}");
                    return;
                }
                WindowEvent::ThemeChanged(theme) => theme,
                _ => return,
            };
            let resolved_mode = match theme {
                tauri::Theme::Dark => ResolvedMode::Dark,
                _ => ResolvedMode::Light,
            };
            if let Some(controller) = window.try_state::<Arc<AppController>>() {
                if let Err(error) = controller.enqueue_system_theme_changed(resolved_mode) {
                    log::error!("appearance system transition failed: {}", error.code);
                }
            }
        })
        .setup(move |app| {
            let app_data_dir = app.path().app_data_dir()?;
            let state_file = app_data_dir.join("state.json");
            let preference_store = AppearanceStore::new(app_data_dir.join("appearance.json"));
            let loaded = preference_store.load();
            let workspace_layout_store =
                WorkspaceLayoutStore::new(app_data_dir.join("layout.json"));
            let loaded_workspace_layout = workspace_layout_store.load();
            let typography_store = TypographyStore::new(app_data_dir.join("typography.json"));
            let loaded_typography = typography_store.load();
            let main_window = app
                .get_webview_window("main")
                .ok_or("main window is unavailable")?;
            let window_port = Arc::new(TauriAppearanceWindowPort::new(main_window.clone()));
            let local_event_port = Arc::new(api::commands::TauriLocalScanEventPort::new(
                main_window.clone(),
            ));
            let event_port = Arc::new(api::commands::TauriAppearanceEventPort::new(main_window));
            let system_mode = window_port.current_system_mode()?;
            let resolved_mode =
                crate::contexts::appearance::resolve_mode(loaded.logical_mode, system_mode);
            let theme_override = match loaded.logical_mode {
                crate::contexts::appearance::LogicalMode::System => {
                    WindowThemeOverride::FollowSystem
                }
                crate::contexts::appearance::LogicalMode::Light => WindowThemeOverride::Light,
                crate::contexts::appearance::LogicalMode::Dark => WindowThemeOverride::Dark,
            };
            window_port.apply_theme(theme_override, resolved_mode)?;
            let appearance = AppearanceContext::new(
                loaded.logical_mode,
                system_mode,
                Arc::new(preference_store),
                window_port.clone(),
                event_port,
            );
            let workspace_layout = WorkspaceLayoutContext::from_loaded_v2(
                loaded_workspace_layout.preferred_left_width_px,
                loaded_workspace_layout.preferred_right_width_px,
                loaded_workspace_layout.left_collapsed,
                loaded_workspace_layout.right_collapsed,
                loaded_workspace_layout.persisted,
                Arc::new(workspace_layout_store),
            );
            let typography = TypographyContext::from_loaded(
                loaded_typography.preset,
                loaded_typography.persisted,
                Arc::new(typography_store),
            );
            let checkout = controller::AppController::with_state_file(state_file);
            let home = app.path().home_dir()?;
            let catalog =
                AdapterCatalog::load_embedded().map_err(|_| "local_adapter_catalog_invalid")?;
            let scan_environment =
                ScanEnvironment::for_current_macos(&catalog, home, env!("CARGO_PKG_VERSION"));
            let operations = Arc::new(OperationCoordinator::default());
            let install = match build_install_coordinator(app, &app_data_dir) {
                Ok(install) => Some(install),
                Err(code) => {
                    log::warn!("install runtime disabled: {code}");
                    None
                }
            };
            let native_path_actions =
                Arc::new(TauriNativePathActionPort::new(app.handle().clone()));
            let local = LocalContext::with_executor_operations_events_and_path_actions(
                Arc::new(DiscoveryScanExecutor::new(catalog, scan_environment)),
                Arc::clone(&operations),
                local_event_port,
                native_path_actions,
            );
            let native_checkout_picker =
                Arc::new(TauriCheckoutDirectoryPickerPort::new(app.handle().clone()));
            let checkout_picker = Arc::new(SingleFlightCheckoutDirectoryPickerPort::new(
                native_checkout_picker,
            ));
            let ai = AiExplanationContext::for_app_data(app_data_dir.clone());
            let controller = Arc::new(AppController::new_with_typography(
                checkout,
                install,
                appearance,
                workspace_layout,
                typography,
                local,
                ai,
                checkout_picker,
                operations,
                loaded.diagnostic,
                loaded_workspace_layout.diagnostic,
                loaded_typography.diagnostic,
            )?);
            app.manage(Arc::clone(&controller));
            setup_activation_inbox.bind_controller(Arc::clone(&controller));
            let latest_system_mode = window_port.current_system_mode()?;
            controller.enqueue_system_theme_changed(latest_system_mode)?;
            let generation = controller.bootstrap_watchdog_generation();
            schedule_bootstrap_watchdog(controller, generation);
            Ok(())
        })
        .plugin(tauri_plugin_log::Builder::default().build())
        .invoke_handler(tauri::generate_handler![
            api::commands::get_bootstrap_state,
            api::commands::set_appearance_mode,
            api::commands::set_typography_preset,
            api::commands::set_workspace_layout,
            api::commands::complete_bootstrap,
            api::commands::get_sot_session_state,
            api::commands::clone_checkout,
            api::commands::register_checkout,
            api::commands::pick_checkout_directory,
            api::commands::load_sot_snapshot,
            api::commands::get_local_scan_state,
            api::commands::start_local_scan,
            api::commands::get_project_ignore,
            api::commands::save_project_ignore_and_rescan,
            api::commands::get_correlation_projection,
            api::commands::query_local_instances,
            api::commands::get_local_instance_detail,
            api::commands::open_local_source_preview,
            api::commands::read_local_source_preview_chunk,
            api::commands::close_local_source_preview,
            api::commands::act_on_local_instance,
            api::commands::prepare_local_removal,
            api::commands::apply_local_removal,
            api::commands::reconcile_local_removal,
            api::commands::get_ai_provider_config,
            api::commands::save_ai_provider_config,
            api::commands::delete_ai_provider_key,
            api::commands::explain_local_source,
            api::commands::preview_install,
            api::commands::apply_install
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");
    app.run(|_app_handle, event| {
        if matches!(
            event,
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
        ) {
            cancel_active_checkout_picker();
        }
    });
}
