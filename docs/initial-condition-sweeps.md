# Hydrostatic initial-condition sweeps

`scripts/generate_initial_conditions.py` turns a JSON parameter grid into separate `spheres_ini` runs. It uses Python's standard library only. Every case is intentionally fixed to:

- hydrostatic radial structures (`-H`);
- SEAGen spherical-shell placement (`-G 2`);
- `miluphcuda` hydro output (`-O 0`);
- no solid-body stress fields and no fragmentation or Weibull flaws;
- identical iron-core fractions in projectile and target;
- basalt mantle fraction `1 - zeta_iron` and no outer shell.

## Running a sweep

Install SEAGen and compile `spheres_ini` as described in `src/spheres_ini/README`. Then validate the supplied example:

```sh
python3 scripts/generate_initial_conditions.py examples/initial_conditions_sweep.json --dry-run
```

Remove `--dry-run` to generate the particle files. Paths in the JSON file are resolved relative to that file, not relative to the shell's current directory. The configured output directory must not already exist; this prevents accidental replacement of simulation data.

The example expands to 60 cases: three mass ratios, five impact velocities, and four impact angles. Runs are sequential so that simultaneous OpenMP and SEAGen jobs do not oversubscribe the machine.

## Parameter specifications

The `parameters` object must contain exactly these names:

- `m_tot_kg`: combined target and projectile mass in kilograms.
- `gamma`: `M_projectile / M_target`, in `(0, 1]`.
- `zeta_iron`: iron core mass fraction for both bodies, in `(0, 1)`.
- `v_imp_over_v_esc`: impact velocity at contact in units of mutual escape velocity.
- `impact_angle_deg`: 0 degrees for head-on through 90 degrees for grazing.
- `f_i`: initial separation in units of the combined radii; it must be at least 1.
- `f_t`: simulation-time factor, fixed at 50.
- `n_tot`: approximate combined SPH particle count, an integer of at least 100.

Each parameter accepts one of four forms:

```json
{"mode": "constant", "value": 5}
{"mode": "list", "values": [0, 30, 45, 60]}
{"mode": "linear", "minimum": 0.1, "maximum": 1.0, "count": 4}
{"mode": "log", "minimum": 1.0, "maximum": 8.0, "count": 5}
```

The program forms the full Cartesian product of all expanded values. `execution.max_cases` is a required guard against accidentally launching an unexpectedly large sweep.

## Results and timing

Each case directory contains:

- `impact.0000`, the hydrodynamic SPH particle input;
- the generated `spheres_ini.input`;
- a private copy of `material.cfg`, whose smoothing length is updated by `spheres_ini`;
- `projectile.structure` and `target.structure` hydrostatic profiles;
- standard-output and standard-error logs;
- `case.json` with requested parameters, exact command, actual particle counts, radii, masses, velocities, and derived timing.

The top-level `manifest.json` accumulates the status and metadata for the entire sweep. It is updated after every completed case.

SEAGen keeps material-boundary shells intact, so `n_tot` is approximate. The requested and actual particle counts are both recorded. The end time stored in the metadata is

```text
T_end = (f_i + f_t) (R_projectile + R_target) / v_impact
```

using the actual particle-distribution radii and total particle mass reported by `spheres_ini`. The unrounded hydrostatic profile radii are also retained separately in the case metadata. The program does not round this time or start an SPH evolution; it generates and describes the initial conditions only.

SEAGen currently chooses its shell orientations stochastically. Repeating the same sweep can therefore produce a different particle realization even when all hyperparameters are identical.
