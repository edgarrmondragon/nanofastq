cmake_minimum_required(VERSION 3.28)
project(nanofastq LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Build the libFuzzer target in fuzz/ instead of the Python extension. Needs clang and the
# sanitizers; see fuzz/CMakeLists.txt.
option(NANOFASTQ_FUZZ "Build the libFuzzer target instead of the Python extension" OFF)

if(NOT NANOFASTQ_FUZZ)
  find_package(Python 3.12 COMPONENTS Interpreter Development.Module Development.SABIModule REQUIRED)
endif()

# zlib-ng inflates noticeably faster than the zlib shipped by most Linux distros and than
# the one we would otherwise need on Windows. macOS ships a tuned system zlib that beats a
# stock zlib-ng build, so use that there. The fuzz build uses the system zlib too: it is there to
# test our own parser, and instrumenting a vendored decompressor only slows it down.
if(APPLE OR NANOFASTQ_FUZZ)
  set(_zlib_ng_default OFF)
else()
  set(_zlib_ng_default ON)
endif()
option(NANOFASTQ_ZLIB_NG "Statically link zlib-ng instead of the system zlib" ${_zlib_ng_default})

include(FetchContent)
if(NANOFASTQ_ZLIB_NG)
  set(_zlib_ng_url https://github.com/zlib-ng/zlib-ng/archive/refs/tags/2.3.3.tar.gz)
  FetchContent_Declare(
    zlib_ng
    URL ${_zlib_ng_url}
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    EXCLUDE_FROM_ALL)
  set(ZLIB_COMPAT OFF CACHE BOOL "" FORCE)  # keep the zng_-prefixed API
  set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
  set(ZLIB_ENABLE_TESTS OFF CACHE BOOL "" FORCE)
  set(WITH_GTEST OFF CACHE BOOL "" FORCE)
  set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(zlib_ng)
  set(FASTQ_ZLIB_TARGET zlib-ng)
else()
  find_package(ZLIB REQUIRED)
  set(FASTQ_ZLIB_TARGET ZLIB::ZLIB)
endif()

if(NOT NANOFASTQ_FUZZ)
  execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
  find_package(nanobind CONFIG REQUIRED)
endif()

set(_nanoarrow_url https://github.com/apache/arrow-nanoarrow/archive/refs/tags/apache-arrow-nanoarrow-0.9.0.tar.gz)
FetchContent_Declare(
  nanoarrow
  URL ${_nanoarrow_url}
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  EXCLUDE_FROM_ALL)  # keep nanoarrow's headers/libs out of the wheel
set(NANOARROW_NAMESPACE "NanoFastq")
FetchContent_MakeAvailable(nanoarrow)

add_library(fastq_core STATIC src/fastq.c)
target_include_directories(fastq_core PUBLIC src)
target_link_libraries(fastq_core PUBLIC nanoarrow::nanoarrow ${FASTQ_ZLIB_TARGET})
if(NANOFASTQ_ZLIB_NG)
  # zlib-ng.h only declares the zng_gz* functions when the consumer defines WITH_GZFILEOP.
  target_compile_definitions(fastq_core PUBLIC NANOFASTQ_ZLIB_NG WITH_GZFILEOP)
endif()

# Warnings for our own C code only (not nanoarrow, zlib or nanobind). Off by default as errors,
# because a newer compiler may add warnings that should not break a user's build from source;
# CI turns it on.
option(NANOFASTQ_WERROR "Treat warnings in nanofastq's own C code as errors" OFF)
if(MSVC)
  target_compile_options(fastq_core PRIVATE /W4)
else()
  target_compile_options(fastq_core PRIVATE -Wall -Wextra)
  if(NANOFASTQ_WERROR)
    target_compile_options(fastq_core PRIVATE -Werror)
  endif()
endif()

if(NANOFASTQ_FUZZ)
  # A tiny read buffer makes growth, compaction and records spanning refills reachable from the
  # small inputs a fuzzer produces; with the default 1 MiB they would need a 1 MiB input.
  target_compile_definitions(fastq_core PRIVATE READ_CHUNK=64)
  add_subdirectory(fuzz)
else()
  nanobind_add_module(_core STABLE_ABI src/module.cc)
  target_link_libraries(_core PRIVATE fastq_core)
  install(TARGETS _core LIBRARY DESTINATION .)

  # PEP 770: an SBOM of what is compiled into the wheel, in .dist-info/sboms/. The versions are
  # read from the download URLs above, which Renovate keeps current, so they cannot drift.
  string(REGEX MATCH "[0-9]+(\\.[0-9]+)+" NANOARROW_VERSION "${_nanoarrow_url}")
  execute_process(
    COMMAND "${Python_EXECUTABLE}" -c "import nanobind; print(nanobind.__version__)"
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE NANOBIND_VERSION COMMAND_ERROR_IS_FATAL ANY)
  if(NANOFASTQ_ZLIB_NG)
    string(REGEX MATCH "[0-9]+(\\.[0-9]+)+" ZLIB_NG_VERSION "${_zlib_ng_url}")
    set(NANOFASTQ_SBOM_ZLIB_NG
        ",
    {
      \"type\": \"library\",
      \"bom-ref\": \"zlib-ng\",
      \"name\": \"zlib-ng\",
      \"version\": \"${ZLIB_NG_VERSION}\",
      \"licenses\": [{\"license\": {\"id\": \"Zlib\"}}],
      \"purl\": \"pkg:github/zlib-ng/zlib-ng@${ZLIB_NG_VERSION}\",
      \"externalReferences\": [{\"type\": \"distribution\", \"url\": \"${_zlib_ng_url}\"}]
    }")
  else()
    set(NANOFASTQ_SBOM_ZLIB_NG "")
  endif()
  configure_file(sbom.cdx.json.in "${CMAKE_CURRENT_BINARY_DIR}/sbom.cdx.json" @ONLY)
  install(FILES "${CMAKE_CURRENT_BINARY_DIR}/sbom.cdx.json" DESTINATION "${SKBUILD_METADATA_DIR}/sboms")
endif()
