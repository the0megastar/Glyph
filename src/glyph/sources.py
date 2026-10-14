"""Typed source and version classification for application entries."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import subprocess
import time
from typing import Literal

import glyph.paths as paths

PackageKind = Literal["rpm", "deb", "pacman", "flatpak", "snap", "appimage", "unknown"]
InstallScope = Literal["system", "user", "unknown"]
Management = Literal["package_managed", "portable_file", "glyph_registered", "external_launcher"]


@dataclass(frozen=True)
class SourceInfo:
    package_kind: str
    install_scope: str
    management: str
    version: str
    version_provenance: str
    source_evidence: str
    display_name: str


# Module-level query caches per catalog refresh
_system_pkg_cache: dict[str, tuple[str, str, str]] = {}
_flatpak_cache: dict[str, tuple[str, str]] | None = None


def clear_source_caches() -> None:
    global _system_pkg_cache, _flatpak_cache
    _system_pkg_cache.clear()
    _flatpak_cache = None


MAX_HOST_QUERY_BYTES = 512 * 1024
MAX_HOST_QUERY_STDERR_BYTES = 64 * 1024
HOST_QUERY_TIMEOUT_SECONDS = 3.0

ALLOWED_HOST_PROBES: dict[str, list[str]] = {
    "rpm": ["rpm", "--version"],
    "dpkg": ["dpkg-query", "--version"],
    "pacman": ["pacman", "-V"],
}

HOST_DESKTOP_PATH_PATTERN = re.compile(r"^/(usr|usr/local)/share/applications/[\w.\-]+(?:/[\w.\-]+)*\.desktop$")
PACKAGE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.\-]+(:[a-z0-9]+)?$")


def _translate_to_host_desktop_path(path_str: str) -> str | None:
    """Translate a sandbox-mounted path (/run/host/usr/...) to its host path and validate grammar."""
    norm = os.path.normpath(path_str).replace("\\", "/")
    if norm.startswith("/run/host/"):
        norm = norm[len("/run/host") :]
    if not (norm.startswith("/usr/share/applications/") or norm.startswith("/usr/local/share/applications/")):
        return None
    if ".." in norm or not HOST_DESKTOP_PATH_PATTERN.fullmatch(norm):
        return None
    return norm


def _is_allowed_host_command(cmd: list[str]) -> bool:
    """Validate that cmd strictly matches the allowlist of read-only host query templates."""
    if not cmd:
        return False
    # 1. Fixed probes and Flatpak query
    if cmd in [
        ALLOWED_HOST_PROBES["rpm"],
        ALLOWED_HOST_PROBES["dpkg"],
        ALLOWED_HOST_PROBES["pacman"],
        ["flatpak", "list", "--app", "--columns=application,version,installation"],
    ]:
        return True

    # 2. RPM query template: rpm -qf <paths...> --queryformat %{NAME}\t%{VERSION}-%{RELEASE}\n
    if (
        len(cmd) >= 5
        and cmd[0] == "rpm"
        and cmd[1] == "-qf"
        and cmd[-2] == "--queryformat"
        and cmd[-1] == "%{NAME}\t%{VERSION}-%{RELEASE}\n"
    ):
        paths_part = cmd[2:-2]
        return bool(paths_part) and all(HOST_DESKTOP_PATH_PATTERN.fullmatch(p) for p in paths_part)

    # 3. DPKG search template: dpkg-query -S <paths...>
    if len(cmd) >= 3 and cmd[0] == "dpkg-query" and cmd[1] == "-S":
        paths_part = cmd[2:]
        return bool(paths_part) and all(HOST_DESKTOP_PATH_PATTERN.fullmatch(p) for p in paths_part)

    # 4. DPKG version template: dpkg-query -W -f=${Version} <pkg>
    if len(cmd) == 4 and cmd[0] == "dpkg-query" and cmd[1] == "-W" and cmd[2] == "-f=${Version}":
        return bool(PACKAGE_NAME_PATTERN.fullmatch(cmd[3]))

    # 5. Pacman query template: pacman -Qo <path>
    if len(cmd) == 3 and cmd[0] == "pacman" and cmd[1] == "-Qo":
        return bool(HOST_DESKTOP_PATH_PATTERN.fullmatch(cmd[2]))

    return False


def _run_host_command(
    cmd: list[str],
    timeout: float = HOST_QUERY_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_HOST_QUERY_BYTES,
) -> str | None:
    """Run an allowlisted host query with streaming output and time limits."""
    if not _is_allowed_host_command(cmd):
        return None
    spawn_bin = shutil.which("flatpak-spawn")
    if not spawn_bin:
        return None

    args = [spawn_bin, "--host", *cmd]
    proc: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    stdout_buf = bytearray()
    stderr_buf = bytearray()
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        for stream, kind in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, data=kind)

        deadline = time.monotonic() + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)

            events = selector.select(timeout=min(remaining, 0.2))
            for key, _ in events:
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue

                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue

                if key.data == "stdout":
                    output, limit = stdout_buf, max_output_bytes
                else:
                    output, limit = stderr_buf, MAX_HOST_QUERY_STDERR_BYTES
                if len(output) + len(chunk) > limit:
                    return None
                output.extend(chunk)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, timeout)
        if proc.wait(timeout=remaining) != 0:
            return None

        return stdout_buf.decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=0.2)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                    proc.wait(timeout=0.2)
                except (subprocess.TimeoutExpired, OSError):
                    pass
        selector.close()
        if proc is not None:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except OSError:
                        pass


def _query_rpm_database(desktop_files: list[str]) -> dict[str, tuple[str, str, str]]:
    """Query RPM package ownership for given desktop files."""
    results: dict[str, tuple[str, str, str]] = {}
    try:
        import rpm  # pyrefly: ignore[missing-module]
        ts = rpm.TransactionSet()
        for filepath in desktop_files:
            try:
                bname = os.path.basename(filepath)
                norm_fp = os.path.abspath(filepath)
                mi = ts.dbMatch(rpm.RPMTAG_BASENAMES, bname)
                candidates: list[tuple[str, str, str]] = []
                for header in mi:
                    try:
                        dirnames = header[rpm.RPMTAG_DIRNAMES]
                        dirindexes = header[rpm.RPMTAG_DIRINDEXES]
                        basenames = header[rpm.RPMTAG_BASENAMES]
                        for i, b in enumerate(basenames):
                            if b == bname:
                                try:
                                    full = os.path.normpath(os.path.join(dirnames[dirindexes[i]], b))
                                    if full == norm_fp:
                                        name = str(header["name"])
                                        version = str(header["version"])
                                        try:
                                            release = str(header["release"])
                                        except Exception:
                                            release = ""
                                        ver_str = f"{version}-{release}" if release else version
                                        candidates.append((name, ver_str, "rpm"))
                                        break
                                except (IndexError, TypeError):
                                    continue
                    except Exception:
                        continue
                if candidates:
                    # Deterministic tie-break: if multiple packages claim ownership of the same file,
                    # sort by (package_name, version) ascending and select the first.
                    candidates.sort(key=lambda c: (c[0], c[1]))
                    pkg_name, ver_str, kind = candidates[0]
                    results[filepath] = (kind, ver_str, pkg_name)
            except Exception:
                continue
    except Exception:
        pass

    # Fallback to rpm command-line query for any files unresolved by bindings
    unresolved = [fp for fp in desktop_files if fp not in results]
    if not unresolved:
        return results

    rpm_bin = shutil.which("rpm")
    if not rpm_bin:
        return results

    # Chunk into reasonable batch sizes
    batch_size = 50
    for i in range(0, len(unresolved), batch_size):
        chunk = unresolved[i : i + batch_size]
        try:
            res = subprocess.run(
                [rpm_bin, "-qf", *chunk, "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\n"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            if res.returncode == 0 and len(lines) == len(chunk):
                for fp, line in zip(chunk, lines):
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        results[fp] = ("rpm", parts[1], parts[0])
            else:
                for fp in chunk:
                    try:
                        sub = subprocess.run(
                            [rpm_bin, "-qf", fp, "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\n"],
                            capture_output=True,
                            text=True,
                            timeout=2,
                        )
                        if sub.returncode == 0 and sub.stdout.strip():
                            parts = sub.stdout.strip().split("\t", 1)
                            if len(parts) == 2:
                                results[fp] = ("rpm", parts[1], parts[0])
                    except (subprocess.TimeoutExpired, OSError):
                        continue
        except (subprocess.TimeoutExpired, OSError):
            continue
    return results


def _query_dpkg_database(desktop_files: list[str]) -> dict[str, tuple[str, str, str]]:
    """Query DPKG package ownership for given desktop files."""
    results: dict[str, tuple[str, str, str]] = {}
    dpkg_query = shutil.which("dpkg-query")
    if not dpkg_query:
        return results

    batch_size = 50
    for i in range(0, len(desktop_files), batch_size):
        chunk = desktop_files[i : i + batch_size]
        try:
            res = subprocess.run(
                [dpkg_query, "-S", *chunk],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Output lines: '<package-name>: <path>'
            for line in res.stdout.splitlines():
                if ":" in line:
                    pkg, path_part = line.split(":", 1)
                    pkg = pkg.strip()
                    path_clean = path_part.strip()
                    if path_clean in chunk and PACKAGE_NAME_PATTERN.match(pkg):
                        # Query version for this package
                        results[path_clean] = ("deb", "", pkg)
        except (subprocess.TimeoutExpired, OSError):
            continue

    # Fill versions for discovered deb packages
    packages_to_check = {pkg for _, _, pkg in results.values() if pkg}
    for pkg in packages_to_check:
        if not PACKAGE_NAME_PATTERN.match(pkg):
            continue
        try:
            vres = subprocess.run(
                [dpkg_query, "-W", "-f=${Version}", pkg],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if vres.returncode == 0 and vres.stdout.strip():
                version = vres.stdout.strip()
                for path_key, (k, _, p) in list(results.items()):
                    if p == pkg:
                        results[path_key] = (k, version, p)
        except (subprocess.TimeoutExpired, OSError):
            continue

    return results


def _query_pacman_database(desktop_files: list[str]) -> dict[str, tuple[str, str, str]]:
    """Query Pacman package ownership for given desktop files."""
    results: dict[str, tuple[str, str, str]] = {}
    pacman = shutil.which("pacman")
    if not pacman:
        return results

    for fp in desktop_files:
        try:
            res = subprocess.run(
                [pacman, "-Qo", fp],
                capture_output=True,
                text=True,
                timeout=2,
            )
            # Format: '/path/to/file is owned by <pkgname> <version>'
            if res.returncode == 0 and "is owned by" in res.stdout:
                tail = res.stdout.split("is owned by", 1)[1].strip()
                parts = tail.split()
                if len(parts) >= 2:
                    pkg = parts[0]
                    version = parts[1]
                    results[fp] = ("pacman", version, pkg)
        except (subprocess.TimeoutExpired, OSError):
            continue
    return results


def _prime_flatpak_host_package_cache(desktop_files: list[str]) -> dict[str, tuple[str, str, str]]:
    """Query host package manager from Flatpak using flatpak-spawn --host with fixed allowlisted templates."""
    results: dict[str, tuple[str, str, str]] = {}
    host_to_orig: dict[str, str] = {}
    for fp in desktop_files:
        host_path = _translate_to_host_desktop_path(fp)
        if host_path:
            host_to_orig[host_path] = fp

    if not host_to_orig:
        return results

    valid_host_files = list(host_to_orig.keys())

    # Probe which package manager is present on host using allowlisted templates
    if _run_host_command(ALLOWED_HOST_PROBES["rpm"]) is not None:
        batch_size = 50
        for i in range(0, len(valid_host_files), batch_size):
            chunk = valid_host_files[i : i + batch_size]
            out = _run_host_command(["rpm", "-qf", *chunk, "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\n"])
            if out:
                lines = [line.strip() for line in out.splitlines() if line.strip()]
                if len(lines) == len(chunk):
                    for h_fp, line in zip(chunk, lines):
                        parts = line.split("\t", 1)
                        if len(parts) == 2:
                            results[host_to_orig[h_fp]] = ("rpm", parts[1], parts[0])
                else:
                    for h_fp in chunk:
                        sub_out = _run_host_command(["rpm", "-qf", h_fp, "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\n"], timeout=2.0)
                        if sub_out and sub_out.strip():
                            parts = sub_out.strip().split("\t", 1)
                            if len(parts) == 2:
                                results[host_to_orig[h_fp]] = ("rpm", parts[1], parts[0])
    elif _run_host_command(ALLOWED_HOST_PROBES["dpkg"]) is not None:
        batch_size = 50
        for i in range(0, len(valid_host_files), batch_size):
            chunk = valid_host_files[i : i + batch_size]
            out = _run_host_command(["dpkg-query", "-S", *chunk])
            if out:
                for line in out.splitlines():
                    if ":" in line:
                        pkg, path_part = line.split(":", 1)
                        pkg = pkg.strip()
                        path_clean = path_part.strip()
                        if path_clean in host_to_orig and PACKAGE_NAME_PATTERN.match(pkg):
                            results[host_to_orig[path_clean]] = ("deb", "", pkg)
        packages_to_check = {pkg for _, _, pkg in results.values() if pkg}
        for pkg in packages_to_check:
            if PACKAGE_NAME_PATTERN.match(pkg):
                vout = _run_host_command(["dpkg-query", "-W", "-f=${Version}", pkg], timeout=2.0)
                if vout and vout.strip():
                    version = vout.strip()
                    for orig_fp, (k, _, p) in list(results.items()):
                        if p == pkg:
                            results[orig_fp] = (k, version, p)
    elif _run_host_command(ALLOWED_HOST_PROBES["pacman"]) is not None:
        for h_fp in valid_host_files:
            out = _run_host_command(["pacman", "-Qo", h_fp], timeout=2.0)
            if out and "is owned by" in out:
                tail = out.split("is owned by", 1)[1].strip()
                parts = tail.split()
                if len(parts) >= 2:
                    pkg = parts[0]
                    version = parts[1]
                    results[host_to_orig[h_fp]] = ("pacman", version, pkg)

    return results


def prime_system_package_cache(desktop_files: list[str]) -> None:
    """Batch-query host package metadata for system desktop files."""
    global _system_pkg_cache
    if paths.in_flatpak():
        _system_pkg_cache = _prime_flatpak_host_package_cache(desktop_files)
        return

    # Check which package manager is present
    if shutil.which("rpm") or _has_rpm_module():
        _system_pkg_cache = _query_rpm_database(desktop_files)
    elif shutil.which("dpkg-query"):
        _system_pkg_cache = _query_dpkg_database(desktop_files)
    elif shutil.which("pacman"):
        _system_pkg_cache = _query_pacman_database(desktop_files)


def _has_rpm_module() -> bool:
    try:
        import rpm  # pyrefly: ignore[missing-module] # noqa: F401
        return True
    except ImportError:
        return False


def _get_flatpak_cache() -> dict[str, tuple[str, str]]:
    """Cache of installed Flatpak apps: {app_id: (version, scope)}."""
    global _flatpak_cache
    if _flatpak_cache is not None:
        return _flatpak_cache

    _flatpak_cache = {}
    raw_lines: list[str] = []
    flatpak_bin = shutil.which("flatpak")
    if flatpak_bin:
        try:
            res = subprocess.run(
                [flatpak_bin, "list", "--app", "--columns=application,version,installation"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                raw_lines = res.stdout.splitlines()
        except (subprocess.TimeoutExpired, OSError):
            pass
    elif paths.in_flatpak():
        out = _run_host_command(["flatpak", "list", "--app", "--columns=application,version,installation"], timeout=3.0)
        if out:
            raw_lines = out.splitlines()

    for line in raw_lines:
        parts = line.split()
        if len(parts) >= 3:
            app_id = parts[0]
            version = parts[1]
            scope = "user" if parts[2].lower() == "user" else "system"
            _flatpak_cache[app_id] = (version, scope)
        elif len(parts) == 2:
            app_id = parts[0]
            scope = "user" if parts[1].lower() == "user" else "system"
            _flatpak_cache[app_id] = ("", scope)
    return _flatpak_cache


def is_appimage_target(command: str) -> str:
    """Return canonical path if command launches an AppImage, else empty string."""
    if not command:
        return ""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens:
        return ""
    binary = tokens[0]
    if binary.endswith(".AppImage") or binary.endswith(".appimage"):
        return binary
    # If the binary is an absolute path or in user's home, check extension
    candidate = Path(binary).expanduser()
    if candidate.name.lower().endswith(".appimage"):
        return str(candidate)
    if candidate.is_symlink():
        try:
            target = candidate.resolve(strict=True)
        except OSError:
            return ""
        if target.name.lower().endswith(".appimage"):
            return str(target)
    return ""


def _is_user_path(path_str: str) -> bool:
    if not path_str:
        return False
    norm = path_str.replace("\\", "/")
    home_str = str(Path.home()).replace("\\", "/")
    return norm.startswith(home_str + "/") or norm.startswith("/home/") or norm.startswith("~")


def classify_source_info(
    *,
    desktop_id: str,
    filename: str,
    command: str,
    stock_filename: str = "",
    flatpak_id: str = "",
    override: dict | None = None,
    resolve_external_metadata: bool = True,
) -> SourceInfo:
    """Analyze an application's launcher metadata and determine typed provenance."""
    override = override or {}
    effective_file = stock_filename or filename
    normalized_path = effective_file.replace("\\", "/")

    # 1. Flatpak detection
    is_flatpak = bool(flatpak_id) or "/flatpak/" in normalized_path
    if is_flatpak:
        resolved_flatpak_id = flatpak_id or desktop_id.removesuffix(".desktop")
        fp_cache = _get_flatpak_cache() if resolve_external_metadata else {}
        version, scope = fp_cache.get(resolved_flatpak_id, ("", ""))
        if not scope:
            user_roots = [str(r).replace("\\", "/") for r in paths.user_application_dirs()]
            user_roots.append(str(paths.data_home() / "flatpak").replace("\\", "/"))
            scope = "user" if any(normalized_path.startswith(r) for r in user_roots) or _is_user_path(normalized_path) else "system"
        return SourceInfo(
            package_kind="flatpak",
            install_scope=scope,
            management="package_managed",
            version=version,
            version_provenance="Flatpak metadata" if version else "",
            source_evidence=f"Flatpak application ({resolved_flatpak_id})" if resolved_flatpak_id else "Discovered in Flatpak export",
            display_name="Flatpak",
        )

    # 2. Snap detection
    is_snap = "/snapd/" in normalized_path or "/snap/" in normalized_path
    if is_snap:
        return SourceInfo(
            package_kind="snap",
            install_scope="system",
            management="package_managed",
            version="",
            version_provenance="",
            source_evidence="Discovered in Snap desktop exports",
            display_name="Snap",
        )

    # 3. AppImage detection
    appimage_bin = is_appimage_target(command)
    is_glyph_registered = bool(override.get("glyph_managed_appimage") or override.get("appimage_managed"))
    if appimage_bin or is_glyph_registered:
        target_path = override.get("appimage_managed") or appimage_bin
        scope = "user" if _is_user_path(target_path) else "system"
        mgmt = "glyph_registered" if is_glyph_registered else "external_launcher"
        evidence = f"AppImage binary at {target_path}" if target_path else "AppImage application"
        return SourceInfo(
            package_kind="appimage",
            install_scope=scope,
            management=mgmt,
            version="",
            version_provenance="",
            source_evidence=evidence,
            display_name="AppImage",
        )

    # 4. Check system package database (RPM / DEB / Pacman)
    if effective_file in _system_pkg_cache:
        pkg_kind, version, pkg_name = _system_pkg_cache[effective_file]
        display_map = {"rpm": "RPM", "deb": "DEB", "pacman": "Pacman"}
        provenance_map = {
            "rpm": "RPM package database",
            "deb": "DPKG package database",
            "pacman": "Pacman package database",
        }
        return SourceInfo(
            package_kind=pkg_kind,
            install_scope="system",
            management="package_managed",
            version=version,
            version_provenance=provenance_map.get(pkg_kind, ""),
            source_evidence=f"Owned by {display_map.get(pkg_kind, pkg_kind)} package '{pkg_name}'",
            display_name=display_map.get(pkg_kind, "System app"),
        )

    # 5. Fallback based on verified location evidence
    user_roots = tuple(str(root).replace("\\", "/") + "/" for root in paths.user_application_dirs())
    if any(normalized_path.startswith(root) for root in user_roots):
        return SourceInfo(
            package_kind="unknown",
            install_scope="user",
            management="external_launcher",
            version="",
            version_provenance="",
            source_evidence="Installed in user applications directory",
            display_name="User app",
        )

    if "/usr/" in normalized_path or normalized_path.startswith("/run/host/usr/"):
        return SourceInfo(
            package_kind="unknown",
            install_scope="system",
            management="external_launcher",
            version="",
            version_provenance="",
            source_evidence="Installed in system applications directory",
            display_name="System app",
        )

    return SourceInfo(
        package_kind="unknown",
        install_scope="unknown",
        management="external_launcher",
        version="",
        version_provenance="",
        source_evidence="Launcher location cannot be reliably verified",
        display_name="Unknown source",
    )
