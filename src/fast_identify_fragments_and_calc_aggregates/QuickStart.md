Quick Start Guide
-----------------
-----------------

fast_identify_fragments
-----------------------

and

calc_aggregates
---------------
---------------

Local update: 2 November 2026

Christoph Burger  
christoph.burger@uni-tuebingen.de

Local changes: Thomas Maindl. Assisted by Codex (OpenAI).
Original aggregate tool authors: Christoph Burger and Áron Suli.
See `UPSTREAM.md` for provenance and `LICENSE` for the upstream GPL license.

---------------------------------


Purpose
-------

The toolchain consisting of `fast_identify_fragments` and `calc_aggregates`
is intended for postprocessing `miluphcuda` SPH simulations.
It allows to identify spatially connected clumps of SPH particles (_fragments_),
and subsequently find gravitationally bound collections of those fragments (_aggregates_).


Compilation and dependencies
----------------------------
Use the Makefile to build the executables. Type simply

        make
to build both tools, or use

        make fast_identify_fragments
or

        make calc_aggregates
to build either of them individually.

**HDF5 libraries**  
For building `fast_identify_fragments` (not for `calc_aggregates`) the HDF5 dev libraries are required.

The local Makefile uses `pkg-config` to find HDF5. If your HDF5 module does not
provide a discoverable `hdf5.pc`, pass its paths explicitly. On VSC-5, after
loading your HDF5 module, for the installation used in this project's Slurm examples:

```bash
HDF5_PREFIX=$(command spack location -i /zweh35n)
make HDF5_CFLAGS="-I$HDF5_PREFIX/include" \
     HDF5_LIBS="-L$HDF5_PREFIX/lib -lhdf5"
export LD_LIBRARY_PATH="$HDF5_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

Use the hash of your actual HDF5 installation if different. Neither CUDA nor a
GPU is required by these CPU postprocessing tools. `CC`, `OPT`, `CFLAGS`, and
`LDFLAGS` can also be overridden on the `make` command line.


fast_identify_fragments
-----------------------
This tool identifies *fragments* in a miluphcuda output file.
Fragments are connected components of particles whose pairwise links have a
distance strictly smaller than the global linking length. Particles in a fragment
can be linked through intermediate particles; its total extent can exceed this length.

* HDF5 and ASCII miluphcuda output files are supported
* the global linking length is the unweighted arithmetic mean of all included
  particle smoothing lengths: `sum(h_i) / N`
* with `-I`, excluded particles do not contribute to the mean or its statistics
* constant-smoothing-length files retain the same linking criterion
* zero, negative, NaN, or infinite included smoothing lengths are rejected, as
  are empty selections and HDF5 smoothing-length arrays of the wrong size

This is a global-mean approximation for variable smoothing lengths, not a
particle-dependent linking rule. Large spreads or dilute ejecta can affect the
mean and hence connectivity; inspect the reported range when interpreting results.

Every run reports the mean, minimum, maximum, and number of included particles
to standard output, even without `-v`, for example:

```text
Linking length: mean = 2; min = 1; max = 3; particles = 4
```

These are smoothing-length statistics for one snapshot, not a moving average
over time. Units are metres. The fragment output columns are unchanged, preserving
compatibility with `calc_aggregates`.

Example for an iron/basalt simulation, including a log:

```bash
./fast_identify_fragments -H -i /path/to/impact.0100.h5 \
    -o /path/to/impact.0100.frag -m 2 -v \
    > /path/to/impact.0100.fragments.log \
    2> /path/to/impact.0100.fragments.error
```

Use the full `.h5` filename for this tool (unlike `miluphcuda`'s `-X` input
convention). `-L` selects legacy `/hsml` instead of `/sml`. ASCII input expects
simulation output with columns `x y z vx vy vz mass density energy sml`
followed by an integer field to skip and the material ID; it does not read the
nine-/ten-column `spheres_ini` initial-condition formats directly. HDF5 is
recommended for this project's simulations.

The `-m` value must cover the material IDs used as array indices. With iron ID 0
and basalt ID 1, use `-m 2`, including pure-basalt snapshots containing only ID 1.
The existing `-I` option does not renumber remaining material IDs.

The neighbour capacity remains 250 per particle. The local version checks this
limit before writing into the neighbour list and stops with an error if exceeded.
The fragment traversal stack also includes its maximum-depth slot, fixing an
upstream allocation boundary error for fragments that span every particle.

The tool is based on building and searching an octree with runtimes on the order of seconds, even for large particle numbers.

Check

        ./fast_identify_fragments -?
for details on usage options.

The output is written to a text file, with one line per fragment. Check the file header for the exact format.


calc_aggregates
---------------
Based on the output of `fast_identify_fragments`, this tool computes gravitationally bound
collections of fragments, referred to as *aggregates*.
The data on fragments is parsed as a text file containing one fragment per line, which is just
the output file format of `fast_identify_fragments` (typically a .frag file).

It computes the final mass, composition and kinetics of the largest, and optionally also the
second-largest aggregate(s). Fragments can be only part of one aggregate.
The procedure is based on an iterative solver, which tests whether fragments are gravitationally bound
to some aggregate and adds or removes them until convergence for the whole aggregate.

Two, three, or four different materials are currently supported.

Check

        ./calc_aggregates -?
for details on usage options.
