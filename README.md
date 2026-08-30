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

Validate the example sweep without creating particle files:

```sh
python3 scripts/generate_initial_conditions.py examples/initial_conditions_sweep.json --dry-run
```
