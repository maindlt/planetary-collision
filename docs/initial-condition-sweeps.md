# Hydrostatic initial-condition sweeps

`scripts/generate_initial_conditions.py` turns a JSON parameter grid or explicit case list into separate `spheres_ini` runs. It uses Python's standard library only. Every case uses:

- hydrostatic radial structures (`-H`);
- SEAGen spherical-shell placement (`-G 2`);
- a JSON-selectable hydro or solid `miluphcuda` output format;
- identical iron-core fractions in projectile and target;
- basalt mantle fraction `1 - zeta_iron` and no outer shell.

## Running a sweep

Install SEAGen and compile `spheres_ini` as described in `src/spheres_ini/README`. Then validate the supplied example:

```sh
python3 scripts/generate_initial_conditions.py examples/initial_conditions_sweep.json --dry-run
```

Remove `--dry-run` to generate the particle files. Paths in the JSON file are resolved relative to that file, not relative to the shell's current directory. The configured output directory must not already exist; this prevents accidental replacement of simulation data.

The optional top-level `generation` object selects the output format:

```json
"generation": {
  "mode": "hydro",
  "fragmentation": false,
  "density_column": true
}
```

The valid combinations map to `spheres_ini` as follows:

| `mode` | `fragmentation` | `density_column` | `spheres_ini` mode | Result |
|---|---:|---:|---:|---|
| `hydro` | `false` | `true` | `-O 0` | Ten-column hydro output with density |
| `hydro` | `false` | `false` | `-O 3` | Nine-column hydro output without density |
| `solid` | `false` | `true` | `-O 1` | Solid output with the initial stress tensor and no fragmentation |
| `solid` | `true` | `true` | `-O 2` | Solid output with stress, damage, and Weibull flaws |

Fragmentation is invalid in hydro mode, and solid output cannot omit density. In fragmentation mode, the basalt mantle receives Weibull flaws using `W_M` and `W_K` from the material configuration. The iron core remains without Weibull flaws because the supplied iron material has no Weibull parameters. Hydrostatic density is calculated internally in every mode, including hydro output that omits the density column.

Configurations without `generation` remain valid and default to hydro with density. The selected format is recorded in the manifest and forms part of restart compatibility; the same `generation` settings must therefore be retained for `--resume` and `--extend`.

Use `--silent` to suppress all terminal output from a valid invocation, including progress, dry-run case listings, planned `miluphcuda` commands, and handled error messages. The process still returns a nonzero exit status on failure, and each started case retains `spheres_ini.stdout.log` and `spheres_ini.stderr.log` for diagnosis. For example, to run a sweep in the background:

```sh
python3 scripts/generate_initial_conditions.py examples/initial_conditions_sweep.json --silent &
```

The example expands to 60 cases: three mass ratios, five impact velocities, and four impact angles. Runs are sequential so that simultaneous OpenMP and SEAGen jobs do not oversubscribe the machine.

## Resuming and extending a sweep

A normal run still requires a new output directory. If a run was interrupted, continue it explicitly with the unchanged JSON plan:

```sh
python3 scripts/generate_initial_conditions.py examples/initial_conditions_sweep.json --resume
```

The generator reconciles the manifest with each case's atomically written `case.json`. Valid `complete` cases are preserved and skipped. A case left `running`, missing required output, or otherwise incomplete is marked `interrupted`; its directory is moved beneath `_incomplete_attempts/` before a clean replacement attempt is started. This preserves logs and partial files for diagnosis. `SIGINT` and `SIGTERM` cancellation also mark the active case as interrupted when the process has time to handle the signal.

Cases that exited unsuccessfully have status `failed`. They are not retried by a plain resume. Resume with the explicit retry option after addressing the cause:

```sh
python3 scripts/generate_initial_conditions.py examples/initial_conditions_sweep.json \
  --resume --retry-failed
```

To add cases, edit the JSON parameter grid so that its Cartesian product is a strict superset of the stored plan, then run:

```sh
python3 scripts/generate_initial_conditions.py examples/initial_conditions_sweep.json --extend
```

Every old case must remain present and at least one new case must be added. Existing case names and completed particle arrangements are retained; new cases receive new sequential directory names. Use `--resume`, rather than `--extend`, when the plan has not changed. Removing or replacing cases is rejected.

Restart also verifies the source material file's SHA-256 checksum, the fixed `spheres_ini` generation mode, and the planned `miluphcuda` configuration. This prevents one sweep from silently mixing incompatible inputs. Executable and source-directory paths may change, allowing a sweep to be resumed after moving or repairing its software installation. An advisory `.sweep.lock` prevents two generator processes from modifying the same sweep concurrently.

Sweeps created by an earlier generator are upgraded during their first restart. The migration recovers every original case identity from `case_table.txt`, so that file must still be present and intact. The supplied JSON must contain every recovered case; it may be the exact original plan for `--resume` or a strict superset for `--extend`. The requested density-column mode must match the legacy manifest; incompatible formats are rejected rather than mixed.

## Parameter specifications

Supply exactly one top-level input section: `parameters` for a Cartesian grid, or `cases` for an explicit list. Both use the same `paths`, `generation`, and `execution` sections and produce the same output files.

The `parameters` object must contain exactly these names:

- `m_tot_kg`: combined target and projectile mass; kilograms by default, or the optional mass unit specified below.
- `gamma`: `M_projectile / M_target`, in `(0, 1]`.
- `zeta_iron`: iron core mass fraction for both bodies, in `[0, 1)`. Zero gives pure basalt bodies with no iron core; one (pure iron) is not supported by the sweep driver.
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

### Reference mass units

The Python module defines fixed conversion constants:

| Constant | Mass in kilograms |
|---|---:|
| `MOON_MASS_KG` | `7.34579e22` |
| `MARS_MASS_KG` | `6.41691e23` |
| `EARTH_MASS_KG` | `5.97217e24` |

Earth and Mars values follow [JPL's Planetary Physical Parameters](https://ssd.jpl.nasa.gov/planets/phys_par.html). The lunar value is obtained from the DE440 lunar gravitational parameter in [JPL's Astrodynamic Parameters](https://ssd.jpl.nasa.gov/astro_par.html), using the [CODATA 2022 gravitational constant](https://physics.nist.gov/cuu/pdf/all.pdf), `6.67430e-11 m^3 kg^-1 s^-2`, and rounded to six significant figures. The associated ephemeris reference is [Park et al. (2021), *The JPL Planetary and Lunar Ephemerides DE440 and DE441*, AJ 161, 105](https://doi.org/10.3847/1538-3881/abd414).

These adopted values are kept fixed for reproducibility. They can be imported when preparing a JSON configuration in Python, for example:

```python
from scripts.generate_initial_conditions import EARTH_MASS_KG

mass_specification = {"mode": "constant", "value": 2 * EARTH_MASS_KG}
```

The JSON `m_tot_kg` specification accepts an optional `unit`: `kg`, `moon`, `mars`, or `earth` (lowercase). Omitting it means kilograms, so existing configurations remain valid. Despite the parameter name, input values are interpreted in the selected unit. For example, a combined mass of two Earth masses is:

```json
"m_tot_kg": {"mode": "constant", "value": 2, "unit": "earth"}
```

The same field works with all four grid modes:

```json
"m_tot_kg": {"mode": "list", "values": [1, 2, 5], "unit": "moon"}
"m_tot_kg": {"mode": "linear", "minimum": 1, "maximum": 3, "count": 3, "unit": "mars"}
"m_tot_kg": {"mode": "log", "minimum": 0.1, "maximum": 2, "count": 5, "unit": "earth"}
```

These are separate alternative specifications. One unit applies to the entire mass grid; mixed units within a list are not supported. Other parameters cannot specify a `unit`. Values and endpoints are converted to kilograms before grid expansion, so generated inputs, case identities, manifests, and the case table continue to use kilograms. Resume and extend compare the converted physical parameters, not the input unit label; they still require exactly matching expanded floating-point values for existing cases. JSON values must be numeric; constant names and Python expressions are not evaluated.

The program forms the full Cartesian product of all expanded values. `execution.max_cases` is a required guard against accidentally launching an unexpectedly large sweep.

## Explicit case lists

Use `cases` instead of `parameters` to specify individual collisions without forming a Cartesian product. Each entry produces one case and must contain all eight parameters:

```json
"cases": [
  {
    "m_tot_kg": {"value": 2, "unit": "earth"},
    "gamma": 0.1,
    "zeta_iron": 0.3,
    "v_imp_over_v_esc": 1.5,
    "impact_angle_deg": 30,
    "f_i": 5,
    "f_t": 50,
    "n_tot": 1000000
  },
  {
    "m_tot_kg": 1e23,
    "gamma": 0.5,
    "zeta_iron": 0.25,
    "v_imp_over_v_esc": 2,
    "impact_angle_deg": 45,
    "f_i": 5,
    "f_t": 50,
    "n_tot": 1000000
  }
]
```

Mass may be a number in kilograms or an object containing `value` and an optional `unit` (`kg`, `moon`, `mars`, `earth`; default `kg`). The other parameters must be numbers. Grid specifications such as `mode`, `minimum`, or `values` are not accepted inside case entries. All existing physical validation rules apply, including `f_t = 50`.

The list must be non-empty. Unknown or missing parameters, duplicate cases after mass conversion, and lists exceeding `execution.max_cases` are rejected. New case directories follow list order. On restart, case identity depends on the normalized physical parameters rather than list order or input format: `--resume` requires the same case set, and `--extend` requires a strict superset. Existing directory names are retained even if the input list is reordered. An equivalent Cartesian grid and explicit list can therefore be used interchangeably for restart, provided their expanded values match exactly.

A complete two-case configuration is provided in `examples/initial_conditions_cases.json`. Validate it with:

```sh
python3 scripts/generate_initial_conditions.py examples/initial_conditions_cases.json --dry-run
```

## Planned miluphcuda command

The `execution.miluphcuda` object describes a future simulation command. The generator currently requires `enabled` to be `false`; it will never launch `miluphcuda` itself. After each initial condition is generated, it prints the planned command, records it in both the case metadata and the top-level manifest, and writes an executable `run_miluphcuda.sh` into the case directory.

The generated script changes into its own directory and invokes the configured executable with relative `impact.0000` and `material.cfg` paths. A complete sweep directory can therefore be moved to a cluster without retaining paths from the machine that generated it. For portability, configure `executable` as a command available on the cluster's `PATH`, or edit it after transfer. The script is deliberately minimal:

```sh
#!/bin/sh
cd "$(dirname "$0")" && exec miluphcuda ... -f impact.0000 -m material.cfg
```

Run it manually from a login or batch script only after the appropriate GPU environment and `miluphcuda` build are available.

The `arguments` list is deliberately configurable because available options can depend on how `miluphcuda` was compiled. It supports these placeholders:

- `{case_directory}`
- `{impact_file}`
- `{material_file}`
- `{simulation_end_time_s}`
- `{n_frames}`
- `{output_interval_s}`, calculated as `simulation_end_time_s / n_frames`

For example:

```json
"miluphcuda": {
  "enabled": false,
  "executable": "miluphcuda",
  "n_frames": 100,
  "arguments": [
    "-n", "{n_frames}",
    "-t", "{output_interval_s}",
    "-f", "{impact_file}",
    "-m", "{material_file}",
    "-s", "-g"
  ]
}
```

## Results and timing

Each case directory contains:

- `impact.0000`, in the hydro, solid, or fragmentation format selected by `generation`;
- the generated `spheres_ini.input`;
- a private copy of `material.cfg`, whose smoothing length is updated by `spheres_ini`;
- `projectile.structure` and `target.structure` hydrostatic profiles;
- standard-output and standard-error logs;
- executable `run_miluphcuda.sh`, containing the portable planned simulation command;
- `case.json` with requested parameters, exact `spheres_ini` command, planned `miluphcuda` command, actual particle counts, radii, masses, velocities, and derived timing.

The top-level `manifest.json` contains every planned case and its `pending`, `running`, `interrupted`, `failed`, or `complete` status. It is updated atomically before and after every attempt. Beside it, `case_table.txt` provides a fixed-width ASCII table mapping every case directory to its status, attempt count, eight requested parameter values, and validated actual SPH particle count. The `n_tot_actual` entry is `-` until validated result metadata is available. The complete table is written before particle generation starts and updated with the manifest.

SEAGen keeps material-boundary shells intact, so `n_tot` is approximate. The requested and actual particle counts are both recorded. The end time stored in the metadata is

```text
T_end = (f_i + f_t) (R_projectile + R_target) / v_impact
```

The collision timescale is parsed from the `spheres_ini` stdout log, and `T_end` uses that reported value directly. A separately recomputed timescale is retained as a diagnostic, together with the particle-distribution radii, total particle mass, and unrounded hydrostatic profile radii. The program does not round the end time or start an SPH evolution; it generates and describes the initial conditions only.

SEAGen currently chooses its shell orientations stochastically. Repeating the same sweep can therefore produce a different particle realization even when all hyperparameters are identical.
