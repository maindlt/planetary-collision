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
- SEAGen 1.6 and NumPy 2.5.2 are pinned in `src/spheres_ini/requirements-seagen.txt`. Install them into the ignored `src/spheres_ini/SEAGen/` directory using the source distribution for SEAGen; its published 1.6 wheel is malformed.
- The SEAGen wrapper prepends that local dependency directory before importing NumPy and SEAGen and uses the current `verbosity` API.
- SEAGen preserves complete shells at material boundaries. The actual particle count may differ substantially from the requested approximate count at low resolution; sweep manifests must record both values.

## Build dependencies

The existing macOS Makefile uses GCC, OpenMP, Python embedding headers/libraries, and libconfig. Its Python version and MacPorts paths are currently machine-specific.

## Initial-condition sweep workflow

- `scripts/generate_initial_conditions.py` expands the JSON grid in `examples/initial_conditions_sweep.json` and runs cases sequentially.
- Paths in a sweep configuration are relative to the JSON file. The output directory must not exist before a real run.
- The driver always calls `spheres_ini` with `-H -G 2 -O 3`: hydrostatic profiles, SEAGen shells, and nine-column hydrodynamic `miluphcuda` output without an initial density column. It does not expose solid or fragmentation modes.
- Each run receives a copy of the source material file. Per-case and top-level JSON manifests record actual counts, the collision timescale parsed from the `spheres_ini` log, and derived timing.
- `execution.miluphcuda` is planning-only and must have `enabled: false`. Its editable argument template is rendered per case, printed, and stored in both case and sweep metadata; it is never executed.

- `gamma` is the projectile-to-target mass ratio: `M_projectile / M_target`.
- `zeta_iron` is applied identically to both bodies: iron core mass fraction `zeta_iron`, basalt mantle mass fraction `1 - zeta_iron`, and no outer shell.
- Following the nomenclature in Table 1 of Winter et al. (2023), `f_i` is the initial-distance factor and `f_t` is the simulation-time factor. The requested end time is `(f_i + f_t) * (R_projectile + R_target) / v_impact`.
- The simulation-time factor is fixed at 50 unless the project requirements change.
- Production hydrostatic initial conditions should use SEAGen spherical-shell particle placement.
