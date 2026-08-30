# Project Knowledge

## Current scope

The repository currently contains `spheres_ini`, an initial-condition generator for particle-based planetary collision simulations. It builds differentiated projectile and target bodies and writes input for `miluph` or `miluphcuda`; the current compile-time default is `miluphcuda`.

## Important operational facts

- Source directory: `src/spheres_ini/`
- Default particle arrangement: hexagonal close-packed lattice
- Optional arrangements: simple cubic and SEAGen spherical shells
- Optional `-H` mode computes hydrostatic density and internal-energy profiles.
- Optional `-R` mode reads `projectile.profile` and `target.profile` as radius-density-energy input profiles.
- A run rewrites the smoothing length in the supplied `miluphcuda` material configuration. Always use a per-run copy.
- The bundled example input names its secondary material `Ice`, while the bundled two-material configuration names it `Water`; they must be aligned before that example can run.
- The SEAGen wrapper currently passes the obsolete keyword `verb`; current PyPI SEAGen uses `verbosity`. The integration needs that compatibility update before routine use.

## Build dependencies

The existing macOS Makefile uses GCC, OpenMP, Python embedding headers/libraries, and libconfig. Its Python version and MacPorts paths are currently machine-specific.
