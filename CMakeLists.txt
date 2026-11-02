cmake_minimum_required(VERSION 3.28)
project(nanofastq LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Python 3.12 COMPONENTS Interpreter Development.Module Development.SABIModule REQUIRED)
find_package(ZLIB QUIET)
if(NOT ZLIB_FOUND)
  # No system zlib (e.g. Windows): build a static copy.
  include(FetchContent)
  FetchContent_Declare(
    zlib
    URL https://github.com/madler/zlib/releases/download/v1.3.2/zlib-1.3.2.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
    EXCLUDE_FROM_ALL)
  set(ZLIB_BUILD_TESTING OFF CACHE BOOL "" FORCE)
  set(ZLIB_BUILD_SHARED OFF CACHE BOOL "" FORCE)
  set(ZLIB_INSTALL OFF CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(zlib)
  add_library(ZLIB::ZLIB ALIAS zlibstatic)
endif()

execute_process(
  COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
  OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
find_package(nanobind CONFIG REQUIRED)

include(FetchContent)
FetchContent_Declare(
  nanoarrow
  URL https://github.com/apache/arrow-nanoarrow/archive/refs/tags/apache-arrow-nanoarrow-0.9.0.tar.gz
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  EXCLUDE_FROM_ALL)  # keep nanoarrow's headers/libs out of the wheel
set(NANOARROW_NAMESPACE "NanoFastq")
FetchContent_MakeAvailable(nanoarrow)

add_library(fastq_core STATIC src/fastq.c)
target_include_directories(fastq_core PUBLIC src)
target_link_libraries(fastq_core PUBLIC nanoarrow::nanoarrow ZLIB::ZLIB)

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

nanobind_add_module(_core STABLE_ABI src/module.cc)
target_link_libraries(_core PRIVATE fastq_core)
install(TARGETS _core LIBRARY DESTINATION .)
