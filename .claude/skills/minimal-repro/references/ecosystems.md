# Matching versions per ecosystem

For each ecosystem: where the project records the resolved versions, how to pin them in the repro, and how to print the version at run time. Always read the resolved version from the lockfile. The manifest gives only a range.

| Ecosystem | Runtime version | Resolved package versions | Pin in the repro | Print at run time |
| --- | --- | --- | --- | --- |
| Node (npm, pnpm, yarn) | `.nvmrc`, `.node-version`, `engines` in `package.json`, `.tool-versions` | `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`; or `npm ls <pkg>` in the project | `npm install --save-exact <pkg>@<ver>` | `require('<pkg>/package.json').version`, `process.version` |
| Python | `.python-version`, `pyproject.toml` `requires-python`, `.tool-versions` | `uv.lock`, `poetry.lock`, `requirements*.txt` with `==`; or `pip show <pkg>` in the project venv | A new venv with the same Python; `pip install <pkg>==<ver>` | `importlib.metadata.version('<pkg>')`, `sys.version` |
| Go | `go` and `toolchain` lines in `go.mod` | `go.mod`, `go.sum` | `go mod init repro`; `go get <mod>@<ver>`; copy `replace` lines if they apply | `runtime.Version()`; `debug.ReadBuildInfo()` for module versions |
| Rust | `rust-toolchain.toml`, `rust-version` in `Cargo.toml` | `Cargo.lock` | `cargo new repro`; `<crate> = "=<ver>"` with the same features | `rustc -V` and `cargo tree -i <crate>` from the shell |
| Dart / Flutter | `environment` in `pubspec.yaml`, `.fvmrc`, `flutter --version` | `pubspec.lock` | `dart create repro` or `flutter create repro`; exact version in `pubspec.yaml`; `dependency_overrides` for a transitive one | `Platform.version`; `dart pub deps` from the shell |
| Swift (SwiftPM) | `swift-tools-version` in `Package.swift`, `xcodebuild -version` | `Package.resolved` | `.package(url: …, exact: "<ver>")` | `swift --version` from the shell |
| JVM (Gradle, Maven) | `toolchain` in the build file, `.java-version`, `.sdkmanrc` | `gradle.lockfile`, `./gradlew dependencies`, `mvn dependency:tree` | A minimal build file with the exact coordinate | `System.getProperty("java.version")`; the package version from its manifest |
| Ruby | `.ruby-version`, `ruby` in `Gemfile` | `Gemfile.lock` | A `Gemfile` with `gem '<gem>', '= <ver>'`; `bundle exec` | `Gem.loaded_specs['<gem>'].version`, `RUBY_VERSION` |

## Notes

- **Transitive versions.** If the behavior comes from a transitive dependency, pin that one too. Most ecosystems have an override mechanism: `overrides` (npm), `resolutions` (yarn), `dependency_overrides` (Dart), `[patch]` (Cargo), `replace` (Go).
- **Native and platform parts.** Packages with native code, platform channels, or OS APIs can behave differently on another OS or architecture. Record both and report a difference.
- **Project patches.** Check for `patches/`, `patch-package`, vendored copies, and forks in the manifest. A repro against the upstream package does not have those patches.
- **Build mode.** Debug and release, JIT and AOT, and dev and prod bundles can differ. Run the repro in the mode where the project shows the behavior.
- **Two versions side by side.** To compare versions, use two repro directories that are the same except for the pin, and run the same program in both.
