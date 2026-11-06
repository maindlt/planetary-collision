# Upstream source and local changes

Imported into `maindlt/planetary-collision` on 2 November 2026 from
[christophmschaefer/miluphcuda](https://github.com/christophmschaefer/miluphcuda),
revision `27903bba8c2d963adc610e33ab4d41f2203112da`, directory
[`utils/postprocessing/fast_identify_fragments_and_calc_aggregates`](https://github.com/christophmschaefer/miluphcuda/tree/27903bba8c2d963adc610e33ab4d41f2203112da/utils/postprocessing/fast_identify_fragments_and_calc_aggregates).

The complete upstream directory contained four files, all imported here:

- `fast_identify_fragments.c`, originally by Christoph Burger.
- `calc_aggregates.c`, originally by Christoph Burger and Áron Suli; no code changes (line endings normalized on import).
- `Makefile`.
- `QuickStart.md`.

`LICENSE` is a copy of the upstream repository's root GNU General Public License
version 3 text. Original attribution is retained. No upstream files were modified
in the source repository.

Local changes by Thomas Maindl, assisted by Codex (OpenAI):

- Use the arithmetic mean of included particle smoothing lengths for both ASCII
  and HDF5 input, including legacy `/hsml` datasets and `-I` material exclusion.
- Report mean/minimum/maximum and included particle count on standard output.
- Reject invalid included lengths, empty selections and HDF5 length-count mismatches.
- Check the 250-neighbour limit before writing into the neighbour array.
- Allocate the fragment traversal stack through its inclusive maximum-depth index,
  avoiding a second out-of-bounds write for fragments spanning every particle.
- Replace machine-specific HDF5 build paths with configurable `HDF5_CFLAGS` and
  `HDF5_LIBS`, defaulting to `pkg-config`; keep the aggregate tool independent of HDF5.
- Update the quickstart and add integration tests in the repository's `tests/` folder.
- Validate material IDs against the requested slot count before indexing the
  fragment composition arrays.

The tree search, fragment grouping, fragment output columns, and aggregate
algorithm otherwise retain their upstream behaviour. A global mean is an
approximation for variable smoothing lengths and does not implement a local,
pair-dependent linking criterion.
