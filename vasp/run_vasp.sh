#!/bin/bash
head /opt/intel/oneapi/setvars.sh
source /opt/intel/oneapi/setvars.sh
mpiexec -n 35 vasp > vasp_output.txt
