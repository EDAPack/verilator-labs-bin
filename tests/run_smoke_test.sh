#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${SCRIPT_DIR}/smoke_test_work"

echo "=== Verilator Smoke Test ==="
echo "Compiling smoke.sv with Verilator..."

# On Windows, verify Verilator installation structure first
if [[ "$(uname -s)" =~ MINGW|MSYS ]]; then
    echo "Windows detected - checking Verilator installation structure..."
    VERILATOR_BIN=$(command -v verilator || command -v verilator.exe)
    if [ -n "$VERILATOR_BIN" ]; then
        VERILATOR_DIR=$(dirname "$VERILATOR_BIN")
        echo "Verilator binary location: $VERILATOR_BIN"
        echo "Verilator directory: $VERILATOR_DIR"
        echo "Searching for verilated_std_waiver.vlt..."
        find "$VERILATOR_DIR/.." -name "verilated_std_waiver.vlt" 2>/dev/null || echo "  Not found"
        echo "Searching for verilated_std.sv..."
        find "$VERILATOR_DIR/.." -name "verilated_std.sv" 2>/dev/null || echo "  Not found"
        echo "Listing verilator directory structure:"
        ls -la "$VERILATOR_DIR/.." 2>/dev/null || echo "  Cannot list"
        if [ -d "$VERILATOR_DIR/../share" ]; then
            echo "Contents of share directory:"
            ls -la "$VERILATOR_DIR/../share" 2>/dev/null
        fi
        if [ -d "$VERILATOR_DIR/../include" ]; then
            echo "Contents of include directory:"
            ls -la "$VERILATOR_DIR/../include" 2>/dev/null | head -20
        fi
    fi
fi

# Find verilator - check both with and without .exe extension
VERILATOR_CMD="verilator"
if ! command -v verilator >/dev/null 2>&1; then
    if command -v verilator.exe >/dev/null 2>&1; then
        VERILATOR_CMD="verilator.exe"
    else
        echo "ERROR: verilator not found in PATH"
        echo "PATH: $PATH"
        echo "Checking for verilator in PATH directories:"
        IFS=':' read -ra PATHARRAY <<< "$PATH"
        for dir in "${PATHARRAY[@]}"; do
            echo "  Checking: [$dir]"
            if [ -d "$dir" ]; then
                echo "    Directory exists, listing veril* files:"
                ls -la "$dir" 2>/dev/null | grep -i veril || echo "      (no verilator found)"
            else
                echo "    Directory does not exist or is not accessible"
            fi
        done
        exit 1
    fi
fi

$VERILATOR_CMD --version

# Verilator Labs: scripts/build.sh sets VERILATOR_SRC_VERSION to the release
# tag (v<ver>-labs.<YYYY>.<NN>), so the version string must say `labs`.
if ! $VERILATOR_CMD --version | grep -q 'labs'; then
    echo "ERROR: 'verilator --version' does not contain 'labs' (VERILATOR_SRC_VERSION not applied?)"
    exit 1
fi

# Windows: native tools (cmd, PowerShell, Python's shutil.which/subprocess)
# reach verilator through bin/verilator.cmd, not the Perl script. Check that path.
if [[ "$(uname -s)" =~ MINGW|MSYS ]]; then
    VLT_CMD="$(dirname "$(command -v verilator)")/verilator.cmd"
    [ -f "$VLT_CMD" ] || { echo "ERROR: $VLT_CMD missing"; exit 1; }
    echo "Checking verilator.cmd from cmd.exe..."
    cmd //c "$(cygpath -w "$VLT_CMD")" --version | grep -q '^Verilator ' \
        || { echo "ERROR: verilator.cmd --version failed"; exit 1; }
fi

echo "Cleaning work directory..."
rm -rf "${WORK_DIR}"
mkdir -p "${WORK_DIR}"
cd "${WORK_DIR}"

$VERILATOR_CMD --binary "${SCRIPT_DIR}/smoke.sv" -o simv

if [ ! -f "obj_dir/simv" ] && [ ! -f "obj_dir/simv.exe" ]; then
    echo "ERROR: Binary obj_dir/simv was not created"
    exit 1
fi

# Find the simulation binary (with or without .exe extension)
SIMV="obj_dir/simv"
if [ ! -f "$SIMV" ] && [ -f "obj_dir/simv.exe" ]; then
    SIMV="obj_dir/simv.exe"
fi

echo "Running simulation..."
# Use timeout if available, otherwise run directly (simulations should exit quickly)
if command -v timeout >/dev/null 2>&1; then
    timeout 5s "$SIMV" > output.log 2>&1 || {
        EXIT_CODE=$?
        if [ $EXIT_CODE -eq 124 ]; then
            echo "ERROR: Simulation timed out after 5 seconds"
            exit 1
        fi
    }
else
    # No timeout available (e.g., macOS), run directly
    "$SIMV" > output.log 2>&1
fi

echo "Checking output..."
if grep -q "Hello World" output.log; then
    echo "✓ SUCCESS: Found 'Hello World' in output"
    cat output.log
else
    echo "ERROR: 'Hello World' not found in output"
    echo "Output was:"
    cat output.log
    exit 1
fi

# Verilator Labs: --solver-dvs with the dv-solve shipped in the tarball
# (scripts/build.sh, <VERILATOR_ROOT>/dv-solve). DV_SOLVE_ROOT is unset so the
# installed verilator must find it itself. A platform built without dv-solve
# (no such directory) skips this.
VLT_ROOT="$($VERILATOR_CMD --getenv VERILATOR_ROOT)"
if [ -d "$VLT_ROOT/dv-solve" ]; then
    echo "Compiling smoke_dvs.sv with --solver-dvs..."
    rm -rf obj_dvs
    env -u DV_SOLVE_ROOT $VERILATOR_CMD --binary --solver-dvs -Mdir obj_dvs \
        "${SCRIPT_DIR}/smoke_dvs.sv" -o simv_dvs
    SIMV_DVS="obj_dvs/simv_dvs"
    [ -f "$SIMV_DVS" ] || SIMV_DVS="obj_dvs/simv_dvs.exe"
    "$SIMV_DVS" +verilator+solver+file+dvs_solver.log > output_dvs.log 2>&1 || true
    cat output_dvs.log
    grep -q "DVS OK" output_dvs.log \
        || { echo "ERROR: --solver-dvs model did not print 'DVS OK'"; exit 1; }
    grep -Eq '; seed [0-9]+, result [0-9]+' dvs_solver.log \
        || { echo "ERROR: dv-solve was not used (no solve in dvs_solver.log)"; exit 1; }
    echo "✓ SUCCESS: --solver-dvs"
else
    echo "No $VLT_ROOT/dv-solve: built without dv-solve, skipping --solver-dvs"
fi

cd "${SCRIPT_DIR}"
rm -rf "${WORK_DIR}"
echo "=== Smoke Test PASSED ==="
exit 0
