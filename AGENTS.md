# Planetary Collision

This project develops tooling for simulations of collisions between protoplanets and planets.

Read `aidocs/KNOWLEDGE.md` before changing the initial-condition workflow. The current implementation is under `src/spheres_ini/` and targets `miluphcuda` by default.

Before editing, check the Git worktree and preserve unrelated changes. Treat material configurations as simulation inputs: `spheres_ini` writes the computed smoothing length back into the supplied material file, so tests must operate on copies.

Build from `src/spheres_ini/` with `make`. Do not commit generated particle files, object files, or executables.
