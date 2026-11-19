// libFuzzer harness for the FASTQ reader. The first input byte is a set of options, the rest is
// the input:
//
//   bits 0-2  batch size - 1 (1-8)
//   bit  3    gzip the input before reading it, so the parser sees valid gzip of fuzzed records
//   bit  4    ... as two gzip members, split in the middle (what bgzip produces)
//   bit  5    ... and cut the end off the gzip data (a truncated download)
//   bits 6-7  largest read the source returns: unlimited, 1, 7 or 64 bytes (short reads)
//
// With bits 3-7 clear the input is read as is, which still reaches the gzip decoder whenever it
// starts with the gzip magic. Every batch handed out must pass nanoarrow's full validation.
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

#include "fastq.h"

struct MemorySource {
  const uint8_t* data;
  size_t size, pos, max_read;
};

static int64_t MemoryRead(void* ctx, void* buf, size_t n, char* err, size_t err_len) {
  (void)err;
  (void)err_len;
  struct MemorySource* m = ctx;
  size_t k = m->size - m->pos;
  if (k > n) k = n;
  if (m->max_read && k > m->max_read) k = m->max_read;
  memcpy(buf, m->data + m->pos, k);
  m->pos += k;
  return (int64_t)k;
}

// Appends a gzip member holding data[0, size) to out, which has room for it. Returns its length.
static size_t GzipMember(const uint8_t* data, size_t size, uint8_t* out, size_t room) {
  z_stream zs;
  memset(&zs, 0, sizeof(zs));
  if (deflateInit2(&zs, Z_DEFAULT_COMPRESSION, Z_DEFLATED, 16 + 15, 8, Z_DEFAULT_STRATEGY) != Z_OK) {
    abort();
  }
  zs.next_in = (Bytef*)data;
  zs.avail_in = (uInt)size;
  zs.next_out = out;
  zs.avail_out = (uInt)room;
  if (deflate(&zs, Z_FINISH) != Z_STREAM_END) abort();
  size_t n = room - zs.avail_out;
  deflateEnd(&zs);
  return n;
}

int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
  if (size < 1) return 0;
  uint8_t opts = data[0];
  const uint8_t* body = data + 1;
  size_t body_size = size - 1;
  static const size_t max_reads[4] = {0, 1, 7, 64};

  uint8_t* gz = NULL;
  if (opts & 0x08) {
    size_t half = opts & 0x10 ? body_size / 2 : body_size;
    size_t room = 2 * (compressBound((uLong)body_size) + 32);  // + gzip header and trailer
    gz = malloc(room);
    if (!gz) abort();
    size_t n = GzipMember(body, half, gz, room);
    if (opts & 0x10) n += GzipMember(body + half, body_size - half, gz + n, room - n);
    if (opts & 0x20) n -= 1 + body_size % (n < 13 ? n : 13);  // a gzip member is at least 20 bytes
    body = gz;
    body_size = n;
  }

  // The stream does not own the memory, so there is nothing for the source to release.
  struct MemorySource mem = {body, body_size, 0, max_reads[opts >> 6]};
  struct NanoFastqSource source = {MemoryRead, NULL, &mem};
  struct ArrowArrayStream stream;
  struct ArrowError err;
  if (NanoFastqOpenSource(source, 1 + (opts & 0x07), &stream, &err) != 0) abort();

  struct ArrowSchema schema;
  if (stream.get_schema(&stream, &schema) != 0) abort();

  for (;;) {
    struct ArrowArray array;
    if (stream.get_next(&stream, &array) != 0) {
      if (!stream.get_last_error(&stream)) abort();  // every failure comes with a message
      break;
    }
    if (!array.release) break;  // end of stream

    struct ArrowArrayView view;
    struct ArrowError verr;
    if (ArrowArrayViewInitFromSchema(&view, &schema, &verr) != NANOARROW_OK) abort();
    if (ArrowArrayViewSetArray(&view, &array, &verr) != NANOARROW_OK) abort();
    if (ArrowArrayViewValidate(&view, NANOARROW_VALIDATION_LEVEL_FULL, &verr) != NANOARROW_OK) {
      abort();
    }
    ArrowArrayViewReset(&view);
    array.release(&array);
  }

  schema.release(&schema);
  stream.release(&stream);
  free(gz);
  return 0;
}
