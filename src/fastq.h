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

/// Where the (optionally gzip-compressed) bytes come from.
struct NanoFastqSource {
  /// Reads up to `n` bytes into `buf`. Returns how many were read (0 only at the end of the
  /// input), or -1 after writing a message of at most `err_len` bytes to `err`.
  int64_t (*read)(void* ctx, void* buf, size_t n, char* err, size_t err_len);
  /// Frees `ctx`. Called exactly once; may be NULL.
  void (*release)(void* ctx);
  void* ctx;
};

/// Like NanoFastqOpen, but reads from `source`. Takes ownership of it whether or not it
/// succeeds: `source.release` is called when the stream is released, or before returning an
/// error.
int NanoFastqOpenSource(struct NanoFastqSource source, int64_t batch_size,
                        struct ArrowArrayStream* out, struct ArrowError* err);

#ifdef __cplusplus
}
#endif

#endif
