# Windows Installer

Updated: 2026-10-15. This public repository currently provides source only, with no Release or downloadable installer. The packaging code remains available; the release evidence below belongs to the original private source repository.

## What the installer provides

`LifeCaseSetup.exe` is an unsigned, per-user Windows installer for the existing local daily-use application. It requires no administrator rights and installs the self-contained program to `%LOCALAPPDATA%\Programs\LifeCase`. The installed target does not require a source checkout, Python, uv, Node or npm.

The installer creates Start Menu and Desktop shortcuts and a current-user Apps & Features entry. `LifeCase.exe` initializes `%LOCALAPPDATA%\LifeCase` when needed, starts one loopback-only API/Web process, verifies readiness and opens the browser. The default Provider remains disabled.

Uninstall removes the owned program directory, shortcuts and uninstall registration. It intentionally preserves `%LOCALAPPDATA%\LifeCase`, including the SQLite database, sources, exports and backups. Data deletion remains an explicit separate user action.

## Current limits

- The original private source repository's [v0.2.0 Release](https://github.com/ToYOhin/LifeCase/releases/tag/v0.2.0) contains `LifeCaseSetup.exe` and `SHA256SUMS.txt` and requires access permission. It is not a Release of this public repository. Generated binaries are not tracked source files.
- The published asset corresponds to tag commit `e7ce75c`, not later mainline changes. New source behavior needs a separately built and verified asset; it does not update installed copies automatically.
- They are not code-signed, so Windows SmartScreen may show an unknown-publisher warning.
- This is not MSI, portable mode, a native desktop UI, auto-update, a Windows service, multi-user hosting or production-readiness evidence.
- Package acceptance is separate from exact-SHA CI, the annotated tag, formal Release, 27/30 evaluation evidence and dogfood `NOT_RUN`.

## Low-resource build

The build scripts set Idle process priority and, when available, restrict themselves and child processes to two logical processors. Artifacts must be written outside the repository.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-windows-installer.ps1 -OutputRoot "$env:TEMP\LifeCase-v0.2.0-windows"
```

This serially restores locked dependencies, builds the Web client and application directory, then packages `LifeCaseSetup.exe`. PyInstaller is pinned only in the `packaging` dependency group and is not part of the application runtime dependency set.

For a prebuilt application directory, the installer-only build can reuse it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-windows-installer.ps1 -OutputRoot "$env:TEMP\LifeCase-v0.2.0-windows" -ApplicationRoot "$env:TEMP\LifeCase-app\app-dist\LifeCase" -SkipDependencySync
```

## Acceptance

The installer acceptance requires an unused LifeCase Start Menu/Desktop/uninstall-registration boundary and a new isolated data root. It fails before mutation if those boundaries are already occupied.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-windows-installer.ps1 -SetupExe "$env:TEMP\LifeCase-v0.2.0-windows\LifeCaseSetup.exe" -DataRoot "$env:TEMP\LifeCase-v0.2.0-windows\acceptance-data"
```

The check installs to a random direct child of `%LOCALAPPDATA%\Programs`, verifies installed files, shortcuts and Apps & Features registration, starts the installed application against the isolated data root, checks API health and Web HTTP 200, stops it, invokes the installed uninstaller, confirms integration removal and confirms that the SQLite database remains.
