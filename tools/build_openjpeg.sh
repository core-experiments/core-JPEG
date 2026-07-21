#!/usr/bin/env bash
set -euo pipefail

OPENJPEG_VERSION="2.5.4"
OPENJPEG_SHA256="a695fbe19c0165f295a8531b1e4e855cd94d0875d2f88ec4b61080677e27188a"

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cache_dir="${repo_root}/.cache/openjpeg"
source_dir="${repo_root}/.build/openjpeg-${OPENJPEG_VERSION}-source"
build_dir="${repo_root}/.build/openjpeg-${OPENJPEG_VERSION}"
archive="${cache_dir}/openjpeg-${OPENJPEG_VERSION}.tar.gz"
url="https://github.com/uclouvain/openjpeg/archive/refs/tags/v${OPENJPEG_VERSION}.tar.gz"

mkdir -p "${cache_dir}" "${repo_root}/.build"
if [[ ! -f "${archive}" ]]; then
    curl --fail --location --retry 3 --output "${archive}" "${url}"
fi

printf '%s  %s\n' "${OPENJPEG_SHA256}" "${archive}" | sha256sum --check -

rm -rf "${source_dir}"
mkdir -p "${source_dir}"
tar --extract --gzip --file "${archive}" --strip-components=1 --directory "${source_dir}"

cmake \
    -S "${source_dir}" \
    -B "${build_dir}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_CODEC=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DBUILD_TESTING=OFF
cmake --build "${build_dir}" --parallel

printf 'Built OpenJPEG %s at %s\n' "${OPENJPEG_VERSION}" "${build_dir}"
