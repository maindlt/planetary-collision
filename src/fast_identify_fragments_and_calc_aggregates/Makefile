# This is the Makefile for fast_identify_fragments and calc_aggregates

# set compiler
CC = cc

OPT = -O3 # -Wall

# Override these for a module-provided HDF5 installation without pkg-config.
HDF5_CFLAGS ?= $(shell pkg-config --cflags hdf5)
HDF5_LIBS ?= $(shell pkg-config --libs hdf5)


# default target, build both
all: fast_identify_fragments calc_aggregates


fast_identify_fragments: fast_identify_fragments.c Makefile
	@echo ""
	@echo "Compiling fast_identify_fragments ..."
	$(CC) $@.c $(OPT) $(CPPFLAGS) $(CFLAGS) $(HDF5_CFLAGS) $(LDFLAGS) $(HDF5_LIBS) -lm -o $@
	@echo "fast_identify_fragments built successfully."


calc_aggregates: calc_aggregates.c Makefile
	@echo ""
	@echo "Compiling calc_aggregates ..."
	$(CC) $@.c $(OPT) $(CPPFLAGS) $(CFLAGS) $(LDFLAGS) -lm -o $@
	@echo "calc_aggregates built successfully."


clean:
	@echo "Cleaning up ..."
	@rm -vf fast_identify_fragments calc_aggregates
