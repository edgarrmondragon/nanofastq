#ifndef NANOFASTQ_FASTQ_H
#define NANOFASTQ_FASTQ_H

#include "nanoarrow/nanoarrow.h"

#ifdef __cplusplus
extern "C" {
#endif

/// Open a (optionally gzip-compressed) FASTQ file and expose it as a stream
/// of record batches with schema:
///   name: utf8, description: utf8 (nullable), sequence: utf8, quality_scores: utf8
/// Returns 0 on success or an errno-style code (message written to `err`).
int NanoFastqOpen(const char* path, int64_t batch_size, struct ArrowArrayStream* out,
                  struct ArrowError* err);

#ifdef __cplusplus
}
#endif

#endif
