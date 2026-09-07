# Glyph refactor follow-up: implementation specification

Status: proposed implementation contract; production code is not changed by this document.

Baseline: commit `9b72292efd0dffad29be57ddae009cf9636840d8` (2026-09-06). The working tree was clean when this specification was prepared. Reconcile subsequent changes before implementation; do not overwrite intervening work.

Audience: a developer or coding agent implementing the reviewed fixes and the architecture below. This document defines behavior, responsibilities, implementation order, and acceptance criteria. Internal variable names may vary; observable behavior, dependency direction, transaction guarantees, and tests are requirements. If implementation evidence contradicts a requirement, record the contradiction before changing the design.

## 1. Objective and scope

Glyph is a GTK4/Libadwaita desktop utility for discovering launchers, customizing names/icons, independently reverting those customizations, restoring stock launchers, and exchanging preference backups.

Deliver:

1. Correct host launcher and user Flatpak discovery/write paths when Glyph runs inside Flatpak.
2. Preserve icon compatibility lost during replacement of `Gio.DesktopAppInfo` metadata enumeration.
3. Handle failures in bulk reset confirmation callbacks.
4. Establish explicit service, storage, parsing, and presentation boundaries without introducing an application framework.
5. Move blocking operations out of GTK callbacks, with defined concurrency and shutdown behavior.
6. Preserve old Flatpak data and provide a usable recovery path.

Excluded: new customization features, a visual redesign, an ORM/database, a web-style routing/controller framework, generic dependency-injection infrastructure, automatic changes to GNOME Shell caches, broader Flatpak permissions, launching host applications from Flatpak, changes to the portable backup version, and automatic deletion of old Flatpak data. Converting the entire UI to Blueprint/XML or a virtualized list is not part of this work.

## 2. Review evidence and priorities

| ID | Priority | Current behavior | Required outcome |
| --- | --- | --- | --- |
| FIX-01 | P1 | Discovery includes host local launchers, but `APPLICATIONS_DIR` uses private Flatpak `XDG_DATA_HOME`. A rename creates a private copy while the host launcher stays unchanged. User Flatpak exports also use the wrong root. | Discovery, modification, import conflicts, and stock restoration agree on the host applications directory. |
| FIX-02 | P2 | `catalog._gicon('demo.png')` creates a themed icon named `demo.png`; the prior GIO path produced `demo`. | Preserve GIO's recognized suffix normalization for themed icon names. |
| FIX-03 | P2 | Confirmed bulk name/icon reset operations can raise an uncaught `OverrideError`. | Show an error, restore controls, and avoid a false success notification. |
| ARCH-01 | Improvement | `backups` and `overrides` depend on one another; `desktop_text` imports an exception from `overrides`. | Acyclic dependencies and independently testable parsing/storage. |
| ARCH-02 | Improvement | Disk scans, image decoding, archives, transactions, and subprocess waits run from UI callbacks. | Serialized background operations with main-thread presentation. |

FIX-01's write-path issue and FIX-03 existed before the latest extraction. FIX-02 is a regression in the new icon construction. The review ran all 45 existing tests successfully. Additional temporary fixtures reproduced FIX-01 and FIX-03. GIO comparisons verified these icon mappings: `.png`, `.svg`, and `.xpm` are stripped; `.jpg`, `.webp`, and uppercase `.PNG` are retained. A GTK icon-theme fixture found an icon by its extensionless name and not by its filename. No installed Flatpak smoke test has yet been performed.

## 3. Architecture decision

Use a layered desktop application with a thin presentation controller. GTK widgets are the view; a service owns application workflows; storage owns persistence. This is an MVC-like separation of responsibilities, with a service layer for filesystem operations. Do not create one controller class per widget.

Dependency direction, where `A -> B` means A may import B:

```text
main -> window, dialogs, controller, services, storage, paths
window, dialogs -> controller, models, errors
controller -> services, models, errors, GLib/Gio
services -> overrides, backups, catalog, storage, images, paths, models, errors
overrides -> storage, desktop_text, images, paths, models, errors
backups -> desktop_text, images, limits, models, errors
storage -> paths, models, limits, errors, desktop_text
catalog -> paths, models, GLib
images -> limits, errors, GdkPixbuf
desktop_text -> errors
paths, models, limits, errors -> Python standard library only
```

`overrides.py` remains the implementation of customization rules. It must not import `services`, `backups`, or any UI module. `backups.py` becomes an archive codec, not an application workflow. GTK widgets never cross a worker-thread boundary. GIO icon construction moves into the presentation layer; catalog results contain strings and booleans.

No compatibility facade is required for the old module-level Python APIs: Glyph does not publish a supported library API. Update all in-repository callers and tests in the same change. Compatibility with existing on-disk state and v1/v2 backup files is required.

## 4. Non-negotiable invariants

- Never write to system/package launcher roots.
- For a pre-existing local launcher, independent undo restores only the requested key and its saved translations.
- Delete a Glyph-created launcher on final undo only when the current ownership/content checks establish that it is safe.
- Preserve unrelated desktop groups and keys, current external-edit detection, nested desktop-ID rules, and symlink confinement.
- Every customization transaction includes launcher/icon/state changes and preserves rollback and crash recovery. Do not replace the journal with independent writes.
- State/archive corruption fails visibly; it never becomes an empty state or a successful import.
- Imports apply preferences to launchers discovered on this machine; archives never supply executable launcher content or trusted destination paths.
- Stock restoration intentionally removes pre-existing local overrides only following the existing explicit restore confirmation. It is different from independent name/icon undo.
- Batch operations remain partially successful per entry, with completed, skipped, and failed entries reported. They are not one transaction for the entire batch.
- No success message appears before a commit completes. Desktop cache refresh remains best effort.

## 5. Path contract and existing Flatpak data

### 5.1 Path resolution in `src/glyph/paths.py`

Retain `data_home()` as the process-private XDG data root. Add `host_data_home()`:

1. Outside Flatpak, return `data_home()`.
2. Inside Flatpak, use `HOST_XDG_DATA_HOME` only when nonempty and absolute.
3. Otherwise return `Path.home() / '.local/share'`.
4. Relative environment values are ignored. Do not expand arbitrary environment expressions or use the private data directory as a fallback for host writes.

Add a frozen `StoragePaths` dataclass with explicit fields: `applications_dir`, `data_dir`, `icons_dir`, `state_file`, `journal_file`, and `lock_file`. Add `storage_paths()` and `legacy_flatpak_storage_paths()` (the latter returns `None` outside Flatpak).

| Field | Native execution | Corrected Flatpak execution | Legacy Flatpak reader |
| --- | --- | --- | --- |
| applications_dir | `data_home()/applications` | `host_data_home()/applications` | `data_home()/applications` |
| data_dir | `data_home()/glyph` | `data_home()/glyph/host-overrides` | `data_home()/glyph` |
| icons_dir | `data_dir/icons` | `data_dir/icons` | `data_dir/icons` |
| state_file | `data_dir/overrides.json` | `data_dir/overrides.json` | `data_dir/overrides.json` |
| journal_file | `data_dir/transaction.json` | `data_dir/transaction.json` | `data_dir/transaction.json` |
| lock_file | `data_dir/operation.lock` | `data_dir/operation.lock` | `data_dir/operation.lock` |

The new Flatpak subdirectory is deliberate: old records contain private launcher paths and ownership information that must not be reinterpreted as host ownership. It prevents old transaction records from being replayed against the new launcher root. Native paths and file formats stay unchanged. The host can read absolute icon paths under the user's private Flatpak data directory; verify this in the packaging smoke test. Removing Flatpak application data can consequently invalidate those icons; do not promise persistence after deleting application data.

Update existing functions:

| Function | Required change |
| --- | --- |
| `user_application_dirs()` | Return only `[host_data_home()/applications]`. Never enumerate private Flatpak launcher copies as active host apps. |
| `shared_data_roots()` | Use `host_data_home()/flatpak/exports/share` for the user export fallback. Preserve current explicit XDG root ordering, host `/run/host` mapping, and stable deduplication. |
| `stock_dirs()` | Continue deriving application roots from shared data roots. |
| `application_indexes()` | Continue scanning local and stock roots once per snapshot; host local entries shadow stock. |
| `icon_dirs()` | Include host data icons/pixmaps and shared roots, with stable deduplication. Use this helper in startup instead of a second hardcoded host icon list. |
| `configure_xdg_data_dirs()` | Retain runtime `/app` and `/usr` roots for GTK; append resolved shared roots once. Do not overwrite XDG_DATA_HOME or HOST_XDG_DATA_HOME. |

Missing stock/export directories are allowed and skipped. A host applications directory that cannot be read or written must cause an actionable error for dependent operations; never silently redirect writes into the sandbox. Do not add broad home/host write access to compensate for a path failure.

### 5.2 Legacy Flatpak recovery policy

Add `services.detect_legacy_preferences()` and `services.export_legacy_preferences(target)` using a separate `OverrideStore` constructed from `legacy_flatpak_storage_paths()`. Legacy detection checks for either an old state file or old transaction journal; it does not load legacy state into the active catalog.

On startup, if legacy data exists, show a nonmodal recovery notice with an **Export Previous Preferences** action. Keep that action available in the primary menu for the session and on future launches while the old data exists. Its explanatory text must state: previous preferences were saved in private Flatpak storage; export them, then use Restore Overrides Backup to apply them to host launchers. Do not imply that private copies customized the host.

Export uses a file picker and the normal portable v2 codec. Enter the legacy store's session first, recovering an interrupted legacy transaction with its original roots before reading. If recovery or validation fails, show its actionable error and preserve the old files; never reroute that journal through the active store. Explicit export may perform this existing journal recovery but must not otherwise delete or rewrite legacy launcher/icon/state files.

After export, the user imports through the normal preview/conflict workflow. Current host launchers supply original text, key snapshots, and ownership for new records. Missing apps are skipped and existing local launchers are conflicts. No automatic replay, marking as migrated, cleanup, or suppression of the menu action is required. Retaining legacy data and allowing repeated exports is intentional. If both stores exist, regular export handles only the active store; legacy export handles only the legacy store.

Do not require legacy recovery to succeed before allowing unrelated work in the separate active store. Show detection/export errors rather than silently discarding them.

## 6. File and function implementation map

### 6.1 Shared leaf modules

Create `src/glyph/errors.py` with `OverrideError` and `DesktopTextError(OverrideError)`. Keep user-facing messages equivalent. Update both failure branches of `desktop_text._replace_lines()` to raise `DesktopTextError`; remove its imports of `glyph.overrides`.

Create `src/glyph/limits.py` and move the existing byte/count limits and allowed image suffixes there unchanged.

Create `src/glyph/models.py`:

- Move `BatchResult` unchanged.
- Move `AppEntry` from `catalog.py`, remove its `gicon` member, and add `display_icon: str` (an override asset path when present and readable, otherwise the desktop entry's Icon value). Keep `icon_value` as the actual desktop key value.
- Define `BackupContent(entries, assets, legacy)` for decoded preferences and asset bytes. It contains no machine-local source paths.
- Define `BackupPlan(content, sources, conflicts, missing)` for a validated preview. `sources` maps IDs to local filesystem paths. The worker owns its mutable internals; UI code receives counts/IDs and must not mutate the plan.
- Define `CatalogSnapshot(apps, custom_icon_count, custom_name_count)` so confirmation counts include tracked entries even when catalog visibility rules hide them.

Move image decoding/header helpers `validate_image()` and `_is_valid_image_header()` into `src/glyph/images.py` unchanged in behavior. This work does not broaden the existing header-only fallback for sandbox decoder failures. Test the move using the current valid/invalid-image cases.

### 6.2 `src/glyph/storage.py`: `OverrideStore`

Construct as `OverrideStore(paths: StoragePaths)`; no filesystem work in `__init__`. Replace import-time global storage paths with instance fields. Tests construct temporary stores instead of patching module constants.

Move these implementations from `overrides.py`, preserving their algorithms:

| Existing implementation | New owner/name |
| --- | --- |
| `_ensure_dirs` | `OverrideStore.ensure_dirs()` |
| `_session`, `_lock`, `_local` | `OverrideStore.session()` and instance reentrancy state |
| `_journal_path`, `_target`, `_restore_journal` | Store journal field, `target()`, `recover()` |
| `_validate_state`, `load_state` | `validate_state()`, `load_state()` |
| `_commit`, `save_state` | `commit()`, `save_state()` |
| `_confined` | `confined()` using an explicit approved root |
| `_read`, `_atomic`, `_sync_dir`, `_digest` | Module helpers `read_limited`, `atomic_write`, `sync_dir`, `digest` |

Move `_id()` to `desktop_text.validate_desktop_id()` so archive and override validation share one definition. Keep its validation rules unchanged.

Give the existing reusable text helpers public names: `_snapshot_lines` becomes `snapshot_lines`, `_replace_lines` becomes `replace_lines`, and `_escape` becomes `escape_value`. Update all callers. Keep `_value` private. This is a rename only: do not normalize desktop-file formatting beyond current behavior. Add direct parser tests for duplicate/missing Desktop Entry groups and localized key preservation.

`load_state`, `save_state`, and `commit` acquire a reentrant session. Service workflows may hold an outer session spanning read/modify/commit. Nested calls must not reacquire a separate process lock or trigger repeated recovery. Keep cross-process `flock`, recovery-before-work, mode preservation, directory synchronization, and error translation from the current `operation` decorator. Normalize storage `OSError`, `UnicodeError`, and malformed persisted data into `OverrideError`; preserve explicit domain errors. Do not catch programming errors as empty state.

`target()` may resolve only the configured launcher, icon, and state targets. Do not accept a root from JSON. Existing journal shape stays unchanged. Native journals are interpreted against native paths; each Flatpak store interprets only its own journal.

There is one active store instance per application and a separate legacy store only for recovery export. Different installations are not promised transactional coordination with each other; preserve external-edit detection instead of weakening it during this work.

### 6.3 `src/glyph/overrides.py`: customization rules

Create `OverrideManager(store: OverrideStore)`. Convert the following functions to instance methods using `self.store` rather than globals:

- `_local_path_for`, `_prepare`, `_apply`, `_revert`, `_batch`.
- `apply_icon`, `apply_name`, `revert_icon`, `revert_name`, `restore_stock_launcher`.
- `revert_all_icons`, `revert_all_names`, `restore_all_to_stock`.

Expose `_apply` as `apply_customization(desktop_id, source_desktop, *, image=None, suffix=None, name=None)`. This is the single transactional path shared by ordinary edits and backup import. It loads state within the session; callers do not supply an arbitrary state dictionary. Reject a call with neither image nor name. Supplying an image requires a supported suffix. Keep localized key snapshots, original text, hashes, independent undo, and unreferenced-asset cleanup intact.

All public mutation methods enter `store.session()`, including read/prepare/commit. A direct caller must get the same safety as a service caller. Store errors are normalized before batch handling, so a failure reading one launcher becomes that entry's error rather than aborting the whole import/reset unexpectedly.

Move cache refresh to `services.py` as `refresh_desktop_database(applications_dir)`. Remove per-entry refreshes from manager methods. A service calls it once after a successful single mutation or once after a batch with completed entries. Never call it for an entirely skipped/failed batch. Keep its existing five-second timeout and best-effort semantics.

`restore_all_to_stock()` builds one local index and one stock index per batch rather than rescanning stock for every local entry. Recheck stock existence before each destructive restore using the selected path; if it disappeared, fail that entry safely. Maintain removal of ambiguous flat/nested local paths for an ID and preservation of local-only applications.

### 6.4 `src/glyph/backups.py`: archive codec only

Remove `export_backup`, `preview_backup`, and `import_backup` workflows from this module. Replace them with:

```python
read_backup(source: Path) -> BackupContent
write_backup(target: Path, content: BackupContent) -> int
```

`read_backup` owns `_backup_members`, gzip/tar/JSON parsing, v1 conversion, manifest validation, IDs, display-name validation, referenced assets, and image validation. Preserve traversal, duplicate-member, link, member-count, and size rejection. It never queries installed apps or reads override state.

`write_backup` writes the existing v2 manifest and deduplicated image assets through a temporary file in the target's parent followed by atomic replacement. Validate both per-member and aggregate limits before output, so an exported file is acceptable to `read_backup`. Codec filesystem/format failures become `OverrideError`. Machine-specific output-directory restrictions belong to the service, which knows managed roots.

The codec imports neither `overrides` nor `storage`. Remove all `getattr(overrides, ...)` fallback lookups and runtime back-imports. Test it directly with byte fixtures, without patching customization internals.

### 6.5 `src/glyph/services.py`: `GlyphService`

Construct with an active store and optional legacy store. Own an `OverrideManager` for the active store. Methods are synchronous Python operations intended to be called by the controller's worker, not GTK callbacks.

Public methods:

```python
load_catalog() -> CatalogSnapshot
apply_icon(desktop_id: str, source_desktop: str, image_path: str) -> None
apply_name(desktop_id: str, source_desktop: str, name: str) -> None
revert_icon(desktop_id: str) -> None
revert_name(desktop_id: str) -> None
restore_stock_launcher(desktop_id: str) -> None
revert_all_icons() -> BatchResult
revert_all_names() -> BatchResult
restore_all_to_stock() -> BatchResult
export_backup(target: Path) -> int
preview_backup(source: Path) -> BackupPlan
import_backup(plan: BackupPlan, *, replace_existing: bool) -> BatchResult
detect_legacy_preferences() -> bool
export_legacy_preferences(target: Path) -> int
ensure_data_directory() -> Path
```

Rules:

1. `load_catalog` reads state under the active store session, enumerates catalog entries, and returns plain data plus tracked override counts. It performs no widget construction.
2. Mutation methods delegate to the manager and refresh the host desktop database once after successful changes.
3. Export holds a store session while reading state/assets. It collects only current custom names/images, deduplicates assets by content hash plus suffix, and delegates serialization. Reject destinations inside either active or legacy managed applications/data roots. Apply this restriction to both normal and legacy export. Do not export desktop text or saved original ownership paths.
4. Preview decodes the archive once, builds one local and one stock index, resolves local-before-stock sources, and identifies missing/conflicting entries. A tracked ID or any pre-existing local launcher is a conflict. Preserve current behavior for stock entries that are hidden from the UI; availability is based on an existing source file, not catalog visibility.
5. Import consumes the exact in-memory content that was previewed. Do not reopen a possibly changed archive after the user confirms. Rebuild source/conflict indexes and load state inside a fresh session immediately before import. Use current sources, never paths trusted from the preview alone.
6. With `replace_existing=False`, skip all currently conflicting IDs, including conflicts created after preview. With `True`, apply the backed-up keys to the current launcher, while normal external-edit safeguards still apply. “Overwrite” means overwrite keys present in the backup; an omitted name/icon is preserved, not reset. State this in the confirmation text.
7. For each available entry call `apply_customization`. Catch `OverrideError` per entry, retain earlier commits, and populate `BatchResult`. A missing source is skipped. Return failure details, not only counts. Refresh once after completed entries.
8. Release the process lock while waiting for any user response. Never hold an open session inside a dialog lifetime.
9. `ensure_data_directory` creates the active data directory on the worker. The UI subsequently opens it using `Gtk.FileLauncher` on the main thread.

Keep backup-content ownership in the controller during confirmation; discard it on cancel or completion to release image buffers. Cap memory through existing archive limits. Backup version remains 2; legacy v1 input remains supported.

### 6.6 `src/glyph/catalog.py` and icon rendering

`_resolve_app_folder()` must use `paths.host_data_home()/flatpak/app` for user deployments. Keep system deployment lookup. For native execution, retain current executable resolution. Inside Flatpak, do not present the runtime's `/usr/bin` as a host application's folder: map host `/usr/...` and `/usr/local/...` candidates to readable `/run/host/...` paths; for bare commands without a known deployment, return an empty folder instead of resolving against the sandbox PATH. Preserve the existing disabled-launch behavior in Flatpak.

Move `_gicon()` to the presentation side of `window.py`, as `_gicon_from_value(value)`. Its exact contract:

1. Empty string returns `None`.
2. Absolute path returns `Gio.FileIcon` for that path, even if it does not currently exist. Never interpret an unavailable absolute path as a theme name.
3. For a non-absolute value, strip one trailing, case-sensitive `.png`, `.svg`, or `.xpm` suffix; leave all other suffixes unchanged.
4. Return `Gio.ThemedIcon` for the resulting name. Do not strip a suffix from an absolute path or strip repeatedly.

Update `window._make_row()` and `_fill_detail()` to call this helper on `app.display_icon`, then use the existing `_image_from_gicon()` path. Update every `AppEntry` constructor and UI fixture for the removed `gicon` field.

This preserves the verified prior icon-name behavior without constructing `DesktopAppInfo` for host enumeration. Mapping absolute host icon paths into `/run/host` is a separate compatibility enhancement and is not silently claimed by this fix.

### 6.7 Immediate callback fix in `src/glyph/dialogs.py`

Before changing execution architecture, wrap confirmed `revert_all_icons()` and `revert_all_names()` calls in `try/except OverrideError`, toast the error, and return. Reload and show success only when an operation returns. Add tests that fail the operation after a successful pre-dialog state read. Both cancel paths must do no work.

When controller integration replaces these calls, preserve these tests' observable behavior through the centralized completion/error path. Handle state corruption at execution time, not only at confirmation setup.

## 7. Background execution and UI contract

### 7.1 `src/glyph/controller.py`: `OperationController`

Use one `concurrent.futures.ThreadPoolExecutor(max_workers=1)` per application, composed in `main.py`. Do not spawn a thread per action. Do not run GTK operations on that executor.

Provide `submit(work, on_success, on_error, *, label) -> bool`, `busy`, and `shutdown()`. Calls occur on the main thread. `work` receives no widgets and captures only immutable input values/service references. `on_success` and `on_error` always run via `GLib.idle_add` on the main thread. Return `False` if busy; do not silently queue a second user mutation. Catch `Exception` from worker futures, never `BaseException`; known `OverrideError` messages are displayed, unexpected errors are logged with a traceback and shown as a generic operation failure.

The controller stores the composed service as `controller.service`. UI callbacks may select its bound methods and copy primitive arguments into the submitted work; they must not invoke those methods synchronously. Factor mutation-plus-refresh into `controller.submit_mutation(work, on_success, on_error, *, label)` rather than duplicating it in every widget. Its success callback receives an `OperationOutcome(result, snapshot, refresh_error)` dataclass from `models.py`; `snapshot` and `refresh_error` are optional, and `refresh_error` is a displayable string. A failed mutation follows `on_error`; a committed mutation followed by a failed refresh follows `on_success` with `refresh_error` populated. For batches, `result` contains the full `BatchResult`.

Before submitting: set busy, show an indeterminate working indicator/label, disable relevant actions, and call `application.hold()`. In the main-thread completion handler, present the result/error and clear busy in `finally`; always balance `application.release()`, even if a UI callback fails. Idle callbacks return `GLib.SOURCE_REMOVE`.

One accepted mutation job includes both the service mutation and the following `load_catalog()`. Return the operation result and refreshed snapshot together. If mutation succeeds but refresh fails, report “Changes saved, but the application list could not refresh” and retain the old snapshot; never report the mutation as rolled back. If an operation fails after partial batch commits, the service must return a `BatchResult` where possible, followed by a refresh.

Do not implement cancellation during a filesystem transaction. Disable Quit and defer window close while busy; close-request returns `True` and shows “An operation is still running. Try closing when it finishes.” Do not automatically close after completion. After no work remains, shut down the executor during normal application shutdown. This avoids abandoning journal writes and keeps callbacks attached to a live application/window.

### 7.2 UI integration map

| File / methods | Change |
| --- | --- |
| `main.GlyphApplication.__init__`, startup/activation | Compose path config, store, service, controller once. Pass controller/service access to the main window; no import-time filesystem activity. |
| `main` action handlers | Delegate to dialogs/controller. Preferences, About, help, and shortcuts remain main-thread UI operations. |
| `main.on_open_data_folder` | Worker creates/returns active data directory; main thread launches Files. |
| `window.reload` | Submit snapshot load. Add `_apply_snapshot(snapshot)` to update `_apps`, menus, rows, and detail on main thread. |
| `window._on_edit_name_clicked` response | Capture app ID/source/name, submit rename plus refresh, present result on completion. |
| `window._on_name_reset`, `_on_revert`, restore confirmation | Capture target ID before asynchronous work; submit service operation. |
| `window._apply_icon_path`, `_on_icon_dropped` | Submit icon operation. Drop returns whether the request was accepted, not whether asynchronous saving succeeded. Later errors are toasted. |
| `window._on_change`, `_on_icon_chosen` | Capture app ID/source when opening the file dialog. Its callback must not read whichever `_detail_id` is current later. Cancellation is quiet; nonlocal files produce an explanatory toast. |
| `dialogs.confirm_revert_all_icons/names` | Use latest snapshot counts to prepare text; actual batch rereads state on the worker. Counts are advisory if another process changes state. |
| `dialogs.confirm_restore_all_stock` | Preserve explicit destructive confirmation; execute only after the chosen response. |
| `dialogs.export_overrides_dialog` | File picker on main thread; export on worker; success/error on completion. |
| `dialogs.handle_import_open_done` | Preview on worker, confirmation on main thread, import of retained plan on worker. Release busy and all store locks between preview and confirmation. |
| `dialogs` new legacy export action | Reuse export picker presentation with `export_legacy_preferences`; expose only when legacy data is detected. |

Update dialog signatures to take the controller/window explicitly; remove duck-typed `hasattr(parent, '_toast')` and `hasattr(parent, 'reload')` as the main coordination mechanism. A narrow presentation protocol may describe `show_message`, `apply_snapshot`, and `set_busy`; no runtime widget probing or global active-window lookup inside worker callbacks.

While busy, disable app mutation/import/export/data-folder actions, icon drop acceptance, relevant detail edit controls, and Quit. Search, navigation, appearance, help, and About may remain usable. Every mutation callback also checks submission acceptance, protecting against already-open dialogs. A rejected submission displays “Another operation is running. Try again when it finishes.” It does not enqueue stale intent.

File dialogs and confirmations do not themselves hold busy state. They capture IDs/paths when opened. A file selection callback submits for that captured app even if navigation changed. Before submitting a name/icon edit, confirm the captured source still exists on the worker; disappearance is an error, not a switch to another selected app.

On snapshot refresh, if the detail ID no longer exists, navigate back to the list and clear detail state. Preserve the current search/filter. No stale worker result may overwrite a newer snapshot: because jobs are serialized and no queue is accepted, deliver completion before allowing another submit.

For `BatchResult`, show counts and provide a details dialog listing failed IDs/messages and skipped IDs. Never discard the error dictionary. Existing short success toasts can remain for complete single-item operations.

## 8. Acceptance tests

All filesystem fixtures must live in `TemporaryDirectory`. Patch cache-refresh subprocesses; never touch the user's real launcher/state directories. Do not mock the path resolver in a test intended to validate its behavior.

| Test ID | Location | Setup / expected assertions |
| --- | --- | --- |
| PATH-01 | `tests/test_paths.py` | Native default/custom/relative XDG roots; native paths stay compatible. |
| PATH-02 | same | Simulated Flatpak with different HOME, private XDG_DATA_HOME, and absolute HOST_XDG_DATA_HOME. Active launcher root equals host root; private launcher root is excluded. |
| PATH-03 | same | Missing/empty/relative HOST_XDG_DATA_HOME falls back to HOME/.local/share, never private XDG_DATA_HOME. |
| PATH-04 | same | Actual user-export root derivation and deduplication, with an exported desktop file in the fixture. Assert discovery without patching stock_dirs to the expected answer. |
| FLOW-01 | new `tests/test_services.py` | Discover a host stock fixture, customize name/icon, reload, independently undo each; host override changes and final Glyph-owned override disappears. Private applications directory is untouched. |
| FLOW-02 | same | Existing host local launcher with localized keys survives customize/revert with original localized values intact. |
| FLOW-03 | same | Restore stock targets host local override; local-only app survives. Include nested/flat ambiguity. |
| FLOW-04 | same | Unwritable host destination fails visibly and creates no private fallback launcher. Use injected permission failure when test privileges would bypass chmod. |
| LEGACY-01 | same | Legacy private state/assets remain readable/exportable; active catalog ignores private copies; importing export creates fresh host-original snapshots. |
| LEGACY-02 | same | Interrupted legacy journal is recovered only using legacy roots during explicit export; host files stay unchanged. Corrupt/conflicting legacy recovery fails without blocking active-store reads. |
| LEGACY-03 | same | Both active and legacy stores exist: exports are separate, no implicit cleanup, active state uses new namespace. |
| ICON-01 | `tests/test_ui.py` | Themed png/svg/xpm suffixes normalized once; jpg/webp/uppercase and extensionless names preserved. Absolute and nonexistent absolute paths remain FileIcon. |
| ICON-02 | `tests/test_ui.py` | Temporary GTK icon theme proves normalized fixture icon resolves; no display needed for IconTheme construction. |
| ERR-01 | `tests/test_ui.py` | Both reset confirmations succeed in initial state read, then operation raises; error shown, no success toast, cancel does no work. |
| STORE-01 | `tests/test_overrides.py` / new `tests/test_storage.py` | Port existing rollback, recovery, external-edit, localized undo, long IDs, confinement, and corrupt-state tests to injected stores. No weakened assertions. |
| TEXT-01 | new `tests/test_desktop_text.py` | Missing/duplicate main groups raise DesktopTextError; saved localized keys round-trip; ID validation retains current accepted/rejected cases. |
| BACKUP-01 | new `tests/test_backups.py` | v1/v2 decode, encode/decode round trip, duplicate/path/link/oversize/missing-asset rejection; export limits match import limits. |
| BACKUP-02 | `tests/test_services.py` | A conflict appears after preview: keep-current skips it. Source disappears: skipped. Replace applies only specified keys. External-edit hash checks remain enforced. |
| BACKUP-03 | same | Archive file replaced after preview: import still applies original preview content. Cancel releases plan reference and performs no mutation. |
| BACKUP-04 | same | Failure for middle entry returns one error while earlier/later valid entries succeed; cache refresh called once. Export into active or legacy managed roots rejected. |
| ASYNC-01 | new `tests/test_controller.py` | Service runs off main thread; presentation callback runs through supplied main-thread dispatcher; second submission rejected while busy. Use events, not timing-dependent sleeps. |
| ASYNC-02 | same | Expected/unexpected failure both clear busy and balance hold/release; callback failure also releases. |
| ASYNC-03 | `tests/test_ui.py` | Navigate after opening icon picker; selected file still targets captured app. Busy rejection does not queue work. |
| ASYNC-04 | same | Close/Quit blocked while work active; accepted after completion. No cancellation during commit. |
| ASYNC-05 | same | Commit succeeds but snapshot refresh fails: saved-with-refresh-error message, no false rollback report. Missing detail app returns to list. |
| ARCH-01 | review / import smoke | Leaf/parser/codec/storage imports do not import GTK/UI/services; no backups-overrides cycle or module-global storage patching remains. |

Controller tests may inject a test executor and main-thread dispatcher into its constructor. Production defaults are the single executor and `GLib.idle_add`. Do not introduce a general scheduling framework for tests.

## 9. Implementation order and review units

Implement sequentially; each review unit leaves tests passing. Do not mix mechanical moves with unrelated behavior changes.

1. **Immediate regressions:** FIX-02 icon helper and FIX-03 error boundaries, with focused tests. Keep current synchronous APIs temporarily.
2. **Storage/domain separation:** shared modules, injected store, OverrideManager, and ported safety tests. Preserve current path defaults in this intermediate unit. Add each new module to Meson as it is introduced.
3. **Service/archive separation:** codec, service workflows, preview-content ownership, batch error normalization, one refresh per batch. Update UI to synchronous service calls temporarily; eliminate circular imports.
4. **Flatpak correctness and compatibility:** host root contract, corrected store namespace, legacy export UI, user export/deployment resolution, and integration fixtures. These changes ship together; do not land new paths without the legacy recovery route.
5. **Responsive UI:** plain catalog models, controller, all blocking callback conversions, busy/shutdown semantics, captured dialog targets, detailed batch results.
6. **Packaging and documentation gate:** complete checks below and reconcile user-facing path/backup documentation with actual behavior.

The host path fix depends on storage injection for safe legacy-store handling. Its P1 priority is a release blocker, not an instruction to bypass those dependencies. Do not release an intermediate review unit as the completed fix.

## 10. Build, documentation, and release checks

Update `src/meson.build` to install all newly added Python modules: `errors.py`, `limits.py`, `models.py`, `images.py`, `storage.py`, `services.py`, and `controller.py`. Register new test files in the root `meson.build`. Verify imports from the installed package, not only PYTHONPATH source execution.

Update `README.md` with native versus Flatpak data locations, legacy export/import recovery, overwrite-as-key-merge semantics, and working-indicator behavior. Update `docs/TESTING.md`: its current 37-test count is stale (the reviewed suite has 45); avoid a fixed total unless generated/maintained. Replace documentation's machine-specific file:// test links with repository-relative links. Update `docs/PERMISSIONS_AUDIT.md` only with permissions actually verified.

Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -B -m unittest discover -s tests -v
meson setup /tmp/glyph-refactor-build
meson test -C /tmp/glyph-refactor-build --print-errorlogs
```

Use a fresh temporary build directory if that path exists. Also stage a Meson install into a temporary DESTDIR and verify all installed modules import with source-tree PYTHONPATH removed. Schemas must be available for application construction. Reuse the project's release validator if release metadata changes; no version bump is required merely to implement these fixes.

Required actual Flatpak smoke test, performed in a disposable test account/environment:

1. Build/install the bundle with its checked-in scoped permissions; do not add host/home write overrides.
2. Confirm effective private/host roots and discover a user-installed Flatpak as well as a native launcher.
3. Apply a name and icon. Inspect the host applications directory and the Icon target from outside the sandbox. Confirm host-side desktop metadata reads the new values; GNOME grid caching is not the sole oracle.
4. Independently undo, restore a stock override, and preserve a local-only launcher.
5. Export/import active preferences. Exercise legacy export/import with a fixture from the previous storage layout.
6. Exercise a denied destination and confirm no private fallback write.
7. Confirm the UI remains responsive during a deliberately delayed operation and cannot close mid-commit.

If a runner cannot perform the actual sandbox test, report it as unverified; unit fixtures do not replace this release gate. Do not request broad filesystem grants to make a failing test pass.

## 11. Definition of done and handoff checklist

- All FIX and ARCH items have implementations and acceptance-test evidence.
- Existing transaction/undo/archive protections are retained.
- Native state remains compatible; prior Flatpak data remains recoverable through the defined export action.
- No UI callback directly scans files, decodes images, processes archives, waits on file locks, synchronizes writes, or waits on subprocesses.
- No circular dependency, compatibility back-import, import-time storage resolution, or test-only global path lookup remains.
- Meson includes all runtime modules and tests, and staged-install imports pass.
- Actual Flatpak smoke-test result is recorded, including any unavailable gate.
- PR description identifies behavior changes, migration/storage policy, tests, and remaining limitations. Each review unit references the relevant IDs above.

Suggested implementer instruction:

> Implement docs/IMPLEMENTATION_PLAN_REFACTOR_FOLLOWUP.md against the current checkout. First compare the baseline and current code for drift. Follow the review-unit order and preserve all stated storage, import, threading, and compatibility invariants. Add the specified behavioral tests, update packaging and documentation, and report evidence for each acceptance group. Do not broaden scope or substitute a different architecture silently. If a requirement is contradicted by code or runtime evidence, explain the specific conflict before changing the contract.

## 12. References and design rationale

- [Flatpak conventions](https://docs.flatpak.org/en/latest/conventions.html): private XDG locations differ from optional HOST_XDG values; exported user applications/assets must be resolved from the host data root.
- [Flatpak sandbox permissions](https://docs.flatpak.org/en/latest/sandbox-permissions.html): scoped host filesystem grants are distinct from application-private storage.
- [GNOME asynchronous programming](https://developer.gnome.org/documentation/tutorials/asynchronous-programming.html): blocking work must not block the main context; completion returns to the context that owns presentation.

The layered architecture, dedicated corrected Flatpak store, explicit legacy export recovery, and single-worker controller are project design decisions specified here. They are not requirements imposed by GTK or Flatpak.
