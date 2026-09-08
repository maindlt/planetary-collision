# Planetary Collision

Tools and supporting material for constructing and running collision simulations of protoplanets and planets.

The current source tree contains `spheres_ini`, a C program that generates particle-based initial conditions for `miluph` and `miluphcuda` simulations.

## Repository layout

- `src/spheres_ini/` — initial-condition generator, example inputs, and material configurations
- `scripts/generate_initial_conditions.py` — JSON-driven generator for hydrostatic collision sweeps
- `examples/initial_conditions_sweep.json` — example 60-case parameter sweep
- `docs/initial-condition-sweeps.md` — sweep configuration and output reference
- `aidocs/` — current technical context for coding agents

See `src/spheres_ini/README` and run `src/spheres_ini/spheres_ini -h` for the program's detailed usage instructions.

## Installation and first run

The Python sweep driver is part of this repository and does not need to be installed as a package. It requires a compiled `spheres_ini` executable and the local SEAGen dependencies used by the bundled `run_SEAGen.py` interface.

### 1. Clone the repository

```sh
git clone git@github.com:maindlt/planetary-collision.git
cd planetary-collision
```

### 2. Install system prerequisites

The current build requires:

- GNU GCC with OpenMP support;
- Python 3.14, including headers, the embedding library, and `python3.14-config`;
- the `libconfig` development headers and library;
- [`uv`](https://docs.astral.sh/uv/) for installing the pinned Python dependencies.

The checked-in `src/spheres_ini/Makefile` is configured for the tested macOS MacPorts layout under `/opt/local`. On another platform, adjust `CC`, `LOCAL_PYTHON_VERSION`, `CFLAGS`, and `LDFLAGS` in that Makefile to match the installed compiler, Python, and `libconfig` locations.

### 3. Install the SEAGen interface dependencies

The wrapper itself is already included as `src/spheres_ini/run_SEAGen.py`. From the repository root, install its pinned dependencies into the ignored local `SEAGen/` directory:

```sh
uv pip install \
  --python python3.14 \
  --target src/spheres_ini/SEAGen \
  --no-binary seagen \
  -r src/spheres_ini/requirements-seagen.txt
```

The `--no-binary seagen` option is required because the published SEAGen 1.6 wheel is malformed. The source distribution installs correctly.

### 4. Compile spheres_ini

Build the C initial-condition generator from the repository root:

```sh
make -C src/spheres_ini
```

This should produce the executable `src/spheres_ini/spheres_ini`. The build must use the same Python version selected in the Makefile because `spheres_ini` embeds Python when it invokes the SEAGen wrapper.

### 5. Configure the sweep

Copy or edit `examples/initial_conditions_sweep.json`. Its paths are resolved relative to the JSON file. In particular, choose a new `paths.output_directory`; a real run stops if that directory already exists.

The example defines a 60-case Cartesian grid. `execution.miluphcuda.enabled` must remain `false`: the generator creates a portable `run_miluphcuda.sh` in every completed case but does not start an SPH simulation.

### 6. Validate and generate

First validate the configuration and list all expanded cases without writing particle files:

```sh
python3.14 scripts/generate_initial_conditions.py \
  examples/initial_conditions_sweep.json \
  --dry-run
```

Then remove `--dry-run` to generate the hydrostatic SPH initial conditions:

```sh
python3.14 scripts/generate_initial_conditions.py \
  examples/initial_conditions_sweep.json
```

The output directory will contain `manifest.json`, the human-readable `case_table.txt`, and one directory per case. Each completed case contains the ten-column hydro input `impact.0000` with density, copied material configuration, hydrostatic structure files, logs, metadata, and the portable future `miluphcuda` launcher.

See `docs/initial-condition-sweeps.md` for the complete JSON schema, supported parameter grids, output metadata, and command-template placeholders.
