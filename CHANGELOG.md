# Changelog

All notable changes to this project will be documented in this file.

## [v0.6.2](https://github.com/RhetTbull/makelive/compare/v0.6.1...v0.6.2)

Updated dependencies to remove unneeded dependencies.

### 2024-10-01

#### Added

#### Changed

#### Removed

#### Fixed

#### Contributors

## [v0.6.1](https://github.com/RhetTbull/makelive/compare/v0.6.0...v0.6.1)

Fixed example in README.md.

### 2024-06-18

#### Added

#### Changed

#### Removed

#### Fixed

- Example code in README.md.

#### Contributors

## [v0.6.0](https://github.com/RhetTbull/makelive/compare/v0.5.0...v0.6.0)

Added `--pvt` option to create `.pvt` Live Photo packages.

### 2024-06-17

#### Added

- `--pvt` option to CLI to create `.pvt` packages from photo and video pairs. This is useful for creating Live Photos that can be shared via AirDrop or other methods that may not preserve the Live Photo metadata. This method does not modify the original photo and video files.
- `save_live_photo_pair_as_pvt()` function for creating a `.pvt` package from a photo and video pair.

#### Changed

#### Removed

#### Fixed

#### Contributors

- @am1006 [@am1006](https://github.com/am1006) for adding the `--pvt` option to the CLI.
