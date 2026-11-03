# SPH tree-node failures and restart diagnostics

Recorded: 3 November 2026. Based on Thomas Maindl's reports and pasted logs in
the side conversation. The cluster files and snapshots have not been inspected
directly. Root cause remains unconfirmed.

## Context and current status

The 24 lunar/Mars collision cases are stored on VSC-5 at:

```text
/gpfs/data/fs71637/tim/planetary-collision/runs/moon_mars_cases
```

Five failed simulations were continued using individual
`run_continue_miluphcuda.sh` scripts. Their directory names are listed one per
line in `failed_runs` under the sweep directory. The user reports that all five
still exhausted the tree-node allocation after their first new frame, despite
increasing the allocation factor to 5.0.

The supplied detailed excerpt is from
`case_00013_85ca8ff774/miluphcuda.continue.output`. It confirms that the
factor-five executable was used. It does not establish that all five failures
have the same cause.

## Allocation setting and interpretation of the error

In the upstream `miluphcuda` source, the allocation is set in
`src/memory_handling.cu`, function `init_allocate_memory()`:

```c
numberOfNodes = ceil(5.0 * maxNumberOfParticles);
```

Upstream source inspected during this discussion used 3.5 and retained a
commented older 2.5 expression. The tree array contains both particle slots and
internal-node slots, so a factor of five gives approximately four internal-node
slots per particle, not five. The allocation is also rounded to a warp boundary.

The tree-construction error is raised in `src/tree.cu` when:

```c
newNodeIndex = atomicSub((int *) &maxNodeIndex, 1) - 1;
if (newNodeIndex <= numParticles) {
    /* not enough nodes */
}
```

Internal nodes are allocated downwards from the high end of the array.
`maxNodeIndex` in the error is the current allocation pointer, not the original
array capacity. Reaching the particle-count boundary means that the internal-node
space has been exhausted.

For 1,083,758 particles, factor five requests 5,418,790 slots, rounded to
5,418,816. Subtracting the particle slots leaves 4,335,058 internal-node slots
(including any implementation-reserved slots).

## What the latest logs establish

- Hardware: NVIDIA A100-PCIE-40GB; compute capability 8.0; 108 multiprocessors.
- Reported CUDA driver version: 13.2; runtime version: 11.8.
- Hydro, cubic-spline kernel, self-gravity, `rk2_adaptive`, accuracy `1e-4`.
- Density is calculated by summation; smoothing lengths are variable and integrated.
- Restart input is HDF5, `impact.0046.h5`, with simulation time reported as 21285.4 s.
- The requested tree capacity is genuinely five times the particle count.
- The last completed tree used 490,604 internal nodes, approximately 11.317% of
  the available internal-node space. The following tree build exhausted that space.
- Reported tree depth reached 60–61, with cell lengths down to about
  `6.38e-10 m` in a domain roughly `7.35e8 m` across.
- Around 88.84% of particles changed nodes. This is an observation, not proof
  of a node-counter-reset or other tree-update bug.

The leading hypothesis is that identical or extremely close particle positions
cause repeated subdivision without separating the particles. At coordinate
magnitudes near `1e8 m`, double-precision coordinate spacing is substantially
larger than these smallest reported cells. Distant outliers can also increase
the ratio of global domain size to local particle separation. A tree-construction
or counter-reset bug remains another possibility. None has been demonstrated yet.

Changing the Barnes–Hut opening angle is not the recommended next step. Theta
is used when traversing the already-built tree to approximate gravitational
forces; it does not directly determine construction capacity. Keep `-a 0.5`
while diagnosing this failure. Changing theta could indirectly change later
trajectories, which would complicate comparisons.

Increasing Slurm's CPU-memory request does not change the tree's GPU allocation.
Increasing the source allocation factor consumes additional GPU memory and may
only postpone a pathological-subdivision failure.

## Relevant supplied log excerpts

The following excerpts are copied from the user's messages; omitted startup
sections listed additional device properties and available equation-of-state types.

### Earlier error

```text
(thread 13): error during tree creation: not enough nodes. newNodeIndex 1083758, maxNodeIndex 1083758, numParticles: 1083758
```

### Factor-five startup and restart

```text
Found 1083758 particles in impact.0046.
Time integrator: rk2_adaptive - with accuracy rk_epsrel: 0.0001
SPH kernel: cubic_spline
Self-gravity: yes - calculated with Barnes Hut tree with theta: 0.5

No particles: 1083758
Allocating memory for 1083758 particles...

Checking for cuda devices...
Found compute capability 8.0 (need at least 3.5).
Found #gpus: 1: NVIDIA A100-PCIE-40GB
Found cuda device with 108 multiprocessors.
Allocating memory for 5418790 nodes of tree...
Allocating memory for 1083758 particles...
After checking with WARPSIZE of 32, allocating memory for 5418816 nodes of tree...

Allocating memory for 1083758 particles...
Allocating memory for 0 pointmasses...
Number of nodes of tree: 5418816
Allocating memory for maximum of 34680464 children.
Memory size for particles: 8670064 bytes
Memory size for pointmasses: 0 bytes
Memory size for tree: 43350528 bytes
Memory size for stress: 78030576 bytes
Memory size for children: 138721856 bytes
Memory size for interactions: 4335032 bytes

Reading input file impact.0046
Reading particle data from hdf5 file: impact.0046.h5
Using hdf5 input file: impact.0046.h5
Reading data for 1083758 particles...
Found velocities of accreted particles and reading them.
Current time: 21285.4
```

### Last supplied 50 lines

```text
number of used inner nodes / number of allocated nodes: 11.3171035 %
max depth of tree: 60
largest node length: 7.35307e+08 	 smallest node length: 1.27555e-09
calling check_sml_boundary
calling nearNeighbourSearch_modify_sml
duration neighboursearch: 57.0717773 ms
duration density: 11.5507202 ms
duration soundspeed: 0.1740800 ms
duration pressure: 0.0501760 ms
duration calc center of mass: 31.2432632 ms
duration internal forces: 22.9642239 ms
962785 particles change their nodes, this is a fraction of 88.8376 % (currently allowed max is 0.1 %)
duration tree changes: 0.2027520 ms
calculating self-gravity using new tree
duration selfgravity: 230.4143372 ms
total duration rhs: 396.0902710 ms
rhs call
duration zeroing all: 35.2122879 ms
duration comp domain: 0.0921600 ms
computational domain: x [-3.529618e+08, 3.823513e+08], y [-7.701580e+07, 9.229443e+07], z [-3.512246e+08, 3.575346e+08]
duration build tree: 7.0992961 ms
number of inner nodes: 490604
highest index number in tree: 4928212
number of used inner nodes / number of allocated nodes: 11.3171266 %
max depth of tree: 61
largest node length: 7.35313e+08 	 smallest node length: 6.37782e-10
calling check_sml_boundary
calling nearNeighbourSearch_modify_sml
duration neighboursearch: 57.1657600 ms
duration density: 11.4892797 ms
duration soundspeed: 0.1751040 ms
duration pressure: 0.0512000 ms
duration calc center of mass: 30.9473286 ms
duration internal forces: 23.0256634 ms
962812 particles change their nodes, this is a fraction of 88.8401 % (currently allowed max is 0.1 %)
duration tree changes: 0.2027520 ms
calculating self-gravity using new tree
duration selfgravity: 230.8648987 ms
total duration rhs: 396.3257446 ms
>>> Timestep information system: t=  2.189827356559550e+04  dt=  2.861410769334423e-01  t2out=  3.126008344044894e+02  est.steps=    1092  step=    2  acc= 2144  rej=  215  progress=  2.45%
    with timestep: 0.286141
    total max error (relative to eps): 0.732195   (location: 0.732195, velocity: 0, density: 0, energy: 0)
    errors suggest next timestep: 0.286141
Errors were small enough, last timestep accepted, current time: 2.189827e+04   time to next output: 312.601   suggested next timestep: 0.286141

rhs call
duration zeroing all: 35.1631355 ms
duration comp domain: 0.0921600 ms
computational domain: x [-3.529618e+08, 3.823513e+08], y [-7.701580e+07, 9.229443e+07], z [-3.512246e+08, 3.575346e+08]
(thread 11): error during tree creation: not enough nodes. newNodeIndex 1083758, maxNodeIndex 1083758, numParticles: 1083758
```

## HDF5 restart command discussed

Original planned run: 100 output intervals at 462.72655 s each, starting at
`impact.0000`. To continue from a complete frame 46 toward frame 100, use 54
additional output intervals:

```sh
#!/bin/sh
cd "$(dirname "$0")" && exec miluphcuda -v -H -X -I rk2_adaptive -Q 1e-4 -n 54 -a 0.5 -t 462.72655 -f impact.0046 -m material.cfg -s -g
```

`-X` reads HDF5 input and restores its simulation time. Supply the input basename
without `.h5`. `-H` keeps HDF5 output enabled; `-r` is for ASCII restart.
The interval count must be adjusted separately for each case's actual restart
frame and intended endpoint. Use a fully written snapshot and the intended
rebuilt executable/material configuration. Do not rerun from the initial
conditions inadvertently or overwrite needed later output.

## Five-case Slurm continuation pattern

The side chat used a fixed `#SBATCH --array=0-4`, VSC-5 partition and QoS
`zen3_0512_a100x2`, `--gres=gpu:1`, and `--time=3-00:00:00`.
The essential selection and launch commands were:

```bash
sweep_dir="/gpfs/data/fs71637/tim/planetary-collision/runs/moon_mars_cases"
mapfile -t cases < "$sweep_dir/failed_runs"
case_dir="$sweep_dir/${cases[$SLURM_ARRAY_TASK_ID]}"
srun --ntasks=1 sh "$case_dir/run_continue_miluphcuda.sh" \
    > "$case_dir/miluphcuda.continue.output" \
    2> "$case_dir/miluphcuda.continue.error"
```

This assumes exactly five plain directory names with no blank lines. Keep the
list unchanged until all tasks have started. Redirections overwrite existing
continuation logs; preserve them before another diagnostic attempt. Explicit
`--ntasks=1` avoids launching multiple simulations when VSC adjusts the job's
allocated task count. These are GPU jobs, not CPU-only simulations.

The environment used CUDA module `cuda/11.8.0-gcc-12.2.0-bplw5nu`, GCC module
`gcc/11.4.0-gcc-12.2.0-mr23dvs`, and HDF5 module
`hdf5/1.12.2-gcc-12.2.0-zweh35n`. CUDA and HDF5 library directories were added
explicitly to `LD_LIBRARY_PATH`, and `/home/fs71637/tim/miluphcuda/build` to PATH.
`command spack location -i /bplw5nu` and `/zweh35n` bypass the Spack shell wrapper
when resolving those installation prefixes.

## Next diagnostic steps (not yet performed)

1. Inspect the latest complete snapshot for non-finite, identical, and very close
   particle positions; compare with the earlier restart snapshot.
2. Inspect the global domain extent and possible outliers. No duplicates in a
   saved frame would rule out neither nearly coincident particles nor a problem
   arising only in an intermediate integrator stage.
3. If needed, instrument tree insertion to report the two particle indices,
   positions, cell centre/size, and depth before subdivision becomes excessive.
4. If geometry does not explain the failure, inspect the node counter reset and
   concurrent insertion logic. Retain the failing executable's Git revision,
   compile settings, complete logs, and restart snapshot for reproducibility.

The first suggested read-only snapshot check requires Python, NumPy and h5py:

```python
import h5py
import numpy as np

filename = "impact.0047.h5"  # Replace with an actual complete pre-crash snapshot.
with h5py.File(filename, "r") as f:
    x = f["x"][:]

print("Particles:", len(x))
print("Particles with non-finite coordinates:",
      np.count_nonzero(~np.isfinite(x).all(axis=1)))
if not np.isfinite(x).all():
    raise SystemExit("Invalid coordinates found.")

positions, counts = np.unique(x, axis=0, return_counts=True)
print("Distinct positions:", len(positions))
print("Duplicate particles beyond the first at each position:",
      int(np.sum(counts - 1)))
print("Examples of duplicated positions:")
print(positions[counts > 1][:5])
```

This check has not been run on the user's snapshots. It detects exact duplicate
coordinates only; it does not measure nearest-neighbour distances.

## Other Linux diagnostic discussed

Find a running process's working directory on its compute node:

```bash
pgrep -a miluphcuda
readlink -f /proc/12345/cwd
# Alternatively:
pwdx 12345
```

Replace `12345` with the PID. Appropriate process access permissions are required.

## Source references

- [Tree allocation: src/memory_handling.cu](https://github.com/christophmschaefer/miluphcuda/blob/main/src/memory_handling.cu)
- [Construction and exhaustion check: src/tree.cu](https://github.com/christophmschaefer/miluphcuda/blob/main/src/tree.cu)
- [Opening-angle criterion: src/gravity.cu](https://github.com/christophmschaefer/miluphcuda/blob/main/src/gravity.cu)
- [Restart command-line options: src/miluph.cu](https://github.com/christophmschaefer/miluphcuda/blob/main/src/miluph.cu)
- [HDF5 input/time restoration: src/io.cu](https://github.com/christophmschaefer/miluphcuda/blob/main/src/io.cu)

These upstream links track `main`; the exact Git revision of the user's failing
cluster executable was not supplied. Source inspection in this chat is therefore
not a verification of that executable's precise implementation.

Prepared with assistance from Codex (OpenAI). This note records evidence and
proposed diagnostics; it does not record an implemented fix for the failure.
