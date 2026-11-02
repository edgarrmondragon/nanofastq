# The fuzz target. Only built with -DNANOFASTQ_FUZZ=ON (see the top-level CMakeLists.txt). Needs
# clang, with the sanitizers and libFuzzer's coverage instrumentation (fuzzer-no-link) enabled for
# the whole build, so fastq.c and nanoarrow are instrumented too. Without fuzzer-no-link the fuzzer
# gets no coverage feedback from the code under test (cov stays near 10):
#
#   cmake -S . -B build -DNANOFASTQ_FUZZ=ON \
#     -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
#     -DCMAKE_C_FLAGS="-g -O1 -fsanitize=address,undefined,fuzzer-no-link -fno-sanitize-recover=undefined -fno-omit-frame-pointer"
#   cmake --build build --target fuzz_reader
#   mkdir -p work && cp fuzz/corpus/* work/ && build/fuzz/fuzz_reader -max_len=2048 work
add_executable(fuzz_reader fuzz_reader.c)
target_link_libraries(fuzz_reader PRIVATE fastq_core)
target_compile_options(fuzz_reader PRIVATE -fsanitize=fuzzer-no-link)
target_link_options(fuzz_reader PRIVATE -fsanitize=fuzzer)
