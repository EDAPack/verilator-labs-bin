#!/usr/bin/env bash
# verilator-labs-bin build driver (derived from edapack/verilator-bin's).
#
# Builds the exact verilator (labs fork) + bitwuzla commits resolved by edapack-common's
# resolve-inputs.py (passed in via $CANDIDATE_JSON, or resolved locally), so the
# shipped manifest.json truthfully records what was built. The heavy lifting is
# in CMakeLists.txt (ExternalProject fetch/build/install of verilator+bitwuzla);
# this script wires in the resolved refs and the shared release tail (skills,
# export.envrc, manifest, tarball).
#
# Runs both in CI (via edapack-common's reusable workflow) and locally (via
# edapack-common/scripts/local-build.sh). All transient state goes to WORK_DIR;
# the tarball + manifest land in OUT_DIR. Nothing is written into the source tree.
set -euo pipefail

# --- locate edapack-common --------------------------------------------------
if [ -z "${EC_COMMON:-}" ]; then
    # sibling checkout fallback for plain local runs
    _repo="$(cd "$(dirname "$0")/.." && pwd)"
    for _c in "$_repo/packages/edapack-common" "$_repo/../edapack-common"; do
        if [ -f "$_c/scripts/build-common.sh" ]; then EC_COMMON="$_c"; break; fi
    done
fi
if [ -z "${EC_COMMON:-}" ] || [ ! -f "$EC_COMMON/scripts/build-common.sh" ]; then
    echo "ERROR: edapack-common not found. Set EC_COMMON or place edapack-common beside verilator-labs-bin." >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$EC_COMMON/scripts/build-common.sh"

: "${EC_PACKAGE:=verilator-labs-bin}"
export EC_PACKAGE
ec_init_dirs
ec_prepare_candidate

os="$(uname -s)"
plat="${EC_IMAGE_NAME:-}"

# --- provision the stock manylinux image (EC_INSTALL_DEPS=1 in CI/local) -----
if [ "${EC_INSTALL_DEPS:-0}" = "1" ] && [ "$os" = "Linux" ]; then
    yum install -y glibc-static wget flex bison jq help2man \
        cmake3 autoconf make gcc gcc-c++ git perl-core patchelf || true
    # prefer the manylinux cpython for meson/ninja (used by the bitwuzla build)
    [ -d /opt/python/cp312-cp312/bin ] && export PATH=/opt/python/cp312-cp312/bin:$PATH
    pip3 install meson ninja pyyaml || true
    if [ -f /usr/bin/cmake3 ] && [ ! -f /usr/bin/cmake ]; then
        ln -s /usr/bin/cmake3 /usr/bin/cmake || true
    fi
fi

# --- platform label + per-OS quirks -----------------------------------------
REMOVE_PREGEN=0
IS_WINDOWS=0
case "$os" in
    Linux)  : "${plat:=linux}" ;;
    Darwin) : "${plat:=macos-$(uname -m)}"; REMOVE_PREGEN=1 ;;
    *)
        if echo "$os" | grep -q "MINGW\|MSYS"; then plat="${plat:-mingw64}"; IS_WINDOWS=1; fi
        if echo "$os" | grep -q "CYGWIN"; then plat="${plat:-cygwin64}"; IS_WINDOWS=1; fi
        ;;
esac

# --- MSYS2 provisioning checks (packages are installed by the caller) -------
if [ "$IS_WINDOWS" = "1" ] && [ -n "${MINGW_PREFIX:-}" ]; then
    # flex installs FlexLexer.h into the MSYS /usr/include, which the MinGW
    # gcc does not search.
    if [ ! -f "$MINGW_PREFIX/include/FlexLexer.h" ]; then
        [ -f /usr/include/FlexLexer.h ] || ec_die "FlexLexer.h not found: install the msys 'flex' package"
        cp -v /usr/include/FlexLexer.h "$MINGW_PREFIX/include/FlexLexer.h"
    fi
    for _t in gcc g++ make autoconf flex bison perl python3 cmake; do
        command -v "$_t" >/dev/null 2>&1 || ec_die "missing tool '$_t' (see scripts/msys2-packages.txt)"
    done
fi

# --- resolved input commits -------------------------------------------------
vlt_sha="$(ec_input_get verilator resolved_sha)"
bwz_sha="$(ec_input_get bitwuzla resolved_sha)"
[ -n "$vlt_sha" ] && [ -n "$bwz_sha" ] || ec_die "missing resolved input SHAs in candidate"
ec_log "verilator @ $vlt_sha"
ec_log "bitwuzla  @ $bwz_sha"

# --- Verilator Labs: version string and topic configure flags --------------
# `verilator --version` reports this (src/config_rev) instead of `git describe`
# of the ExternalProject clone, so it carries the labs tag, e.g.
# v5.051-labs.2026.02 (tests/run_smoke_test.sh checks for `labs`).
export VERILATOR_SRC_VERSION="$EC_TAG"
ec_log "VERILATOR_SRC_VERSION=$VERILATOR_SRC_VERSION"

# labs.py reads topics.yaml with PyYAML.
if ! python3 -c 'import yaml' 2>/dev/null; then
    python3 -m pip install -q pyyaml 2>/dev/null \
        || python3 -m pip install -q --break-system-packages pyyaml \
        || ec_die "PyYAML is needed to read topics.yaml"
fi

# --- dv-solve (topic `dvs`, --solver-dvs) -----------------------------------
# Built before Verilator, with the same toolchain, into the prefix Verilator's
# configure gets as --with-dv-solve. The prefix ships in the tarball: models
# built with --solver-dvs link its static runtime from $(DV_SOLVE_ROOT).
# A platform where dv-solve builds no static runtime ships without it: Windows
# and macOS (dv-solve's libdv_solve.a needs GNU ld and objcopy). Linux must
# build it.
release_root="$WORK_DIR/release/verilator"
build_dir="$WORK_DIR/build"
rm -rf "$release_root" "$build_dir"
mkdir -p "$build_dir"

dvs_prefix="$release_root/share/verilator/dv-solve"
dvs_sha="$(ec_input_get dv-solve resolved_sha)"
cdl_sha="$(ec_input_get cadical resolved_sha)"
dvs_repo="$(ec_input_get dv-solve repo)"
cdl_repo="$(ec_input_get cadical repo)"
[ -n "$dvs_sha" ] && [ -n "$cdl_sha" ] || ec_die "missing resolved dv-solve/cadical SHAs in candidate"
ec_log "dv-solve  @ $dvs_sha"
ec_log "cadical   @ $cdl_sha"

build_dv_solve() {
    local src cdl bld="$WORK_DIR/dv-solve-build"
    src="$(ec_clone_input dv-solve "${dvs_repo:-https://github.com/fvutils/dv-solve}" "$dvs_sha")" || return 1
    cdl="$(ec_clone_input cadical "${cdl_repo:-https://github.com/arminbiere/cadical}" "$cdl_sha")" || return 1
    rm -rf "$bld" "$dvs_prefix"
    cmake -S "$src" -B "$bld" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
        -DPACKAGES_DIR="$WORK_DIR" \
        -DCADICAL_SRC_ROOT="$cdl" \
        -DDV_SOLVE_BUILD_TOOLS=OFF \
        -DDVS_BUILD_SMT2=OFF \
        -DCMAKE_INSTALL_PREFIX="$dvs_prefix" || return 1
    cmake --build "$bld" -j"$jobs" || return 1
    cmake --install "$bld" || return 1
    # Static libraries only: drop shared libraries and the CMake package
    # (absolute paths); headers, .a, .pc files and licences stay.
    find "$dvs_prefix" \( -name '*.so' -o -name '*.so.*' -o -name '*.dylib' \
        -o -name '*.dll' -o -name '*.dll.a' \) -delete
    rm -rf "$dvs_prefix/bin" "$dvs_prefix"/lib*/cmake
    [ -f "$dvs_prefix/lib/libdv_solve.a" ] || [ -f "$dvs_prefix/lib64/libdv_solve.a" ]
}

jobs="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"

# Each topic's `configure` flags (topics.yaml). A --configure-arg replaces the
# topic flag of the same name, e.g. --with-dv-solve -> --with-dv-solve=<prefix>.
labs_configure_args=()
if build_dv_solve; then
    labs_configure_args+=(--configure-arg="--with-dv-solve=$dvs_prefix")
elif [ "$IS_WINDOWS" = "1" ] || [ "$os" = "Darwin" ]; then
    ec_log "WARNING: dv-solve did not build on $plat; shipping without --solver-dvs"
    rm -rf "$dvs_prefix"
    labs_configure_args+=(--omit=--with-dv-solve)
else
    ec_die "dv-solve build failed"
fi

_flags="$(python3 "$SRC_DIR/scripts/labs.py" --root "$SRC_DIR" configure-flags \
             ${labs_configure_args[@]+"${labs_configure_args[@]}"})" \
    || ec_die "labs.py configure-flags failed"
vlt_configure=()
while IFS= read -r _f; do
    [ -n "$_f" ] && vlt_configure+=("$_f")
done <<< "$_flags"
ec_log "verilator configure flags: ${vlt_configure[*]:-(none)}"
_old_ifs="$IFS"; IFS=';'; vlt_configure_list="${vlt_configure[*]:-}"; IFS="$_old_ifs"

# --- configure + build (CMake ExternalProject installs into the prefix) -----
cmake -S "$SRC_DIR" -B "$build_dir" \
    -DUSE_LATEST_BRANCH=OFF \
    -DVERILATOR_TAG="$vlt_sha" \
    -DBITWUZLA_TAG="$bwz_sha" \
    -DVERILATOR_EXTRA_CONFIGURE_ARGS="$vlt_configure_list" \
    -DCMAKE_INSTALL_PREFIX="$release_root"

if [ "$REMOVE_PREGEN" = "1" ]; then
    find "$build_dir" -name "*_pregen*" -delete 2>/dev/null || true
fi

cmake --build "$build_dir" -j"$jobs"

# --- Windows runtime DLLs ---------------------------------------------------
if [ "$IS_WINDOWS" = "1" ]; then
    mkdir -p "$release_root/bin"
    # MinGW: nothing to copy. verilator_bin.exe is linked -static and there is
    # no bundled solver (CMakeLists.txt, WITH_BITWUZLA), so bin/ has no DLLs.
    if echo "$os" | grep -q "CYGWIN"; then
        for pat in cyggcc_s- cygstdc++- cygwin1 cyggmp- cygmpfr-; do
            cp -v /usr/bin/${pat}*.dll "$release_root/bin/" 2>/dev/null || true
        done
    fi
fi

# --- shared release tail ----------------------------------------------------
ec_finalize_release "$SRC_DIR" "$release_root" "$CANDIDATE_JSON"
tarball="verilator-${plat}-${EC_VERSION}.tar.gz"
ec_make_tarball "$release_root" "$tarball"
ec_log "build complete: $tarball"
