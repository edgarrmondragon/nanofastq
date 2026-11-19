#include "fastq.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef NANOFASTQ_ZLIB_NG
// zlib-ng's native (zng_-prefixed) API: statically linked, so the symbols can never be
// interposed by a different libz already loaded into the process.
#include <zlib-ng.h>
#define z_stream zng_stream
#define inflateInit2 zng_inflateInit2
#define inflate zng_inflate
#define inflateReset zng_inflateReset
#define inflateEnd zng_inflateEnd
#else
#include <zlib.h>
#endif

// How much the read buffer grows and refills by. Overridable (-DREAD_CHUNK=64) so the fuzz build
// can reach buffer growth, compaction and records spanning refills with small inputs.
#ifndef READ_CHUNK
#define READ_CHUNK (1 << 20)
#endif
#define NO_PIN ((size_t)-1)
// How much raw (compressed) input is read from the source at a time. Overridable like
// READ_CHUNK, so the fuzz build fills the input buffer and carries bytes over between members.
#ifndef IN_CHUNK
#define IN_CHUNK (1 << 18)
#endif

// What the decoder is doing with the source's bytes.
enum DecodeMode {
  MODE_SNIFF,   // at the start of the input or after a gzip member: look for the gzip magic
  MODE_PLAIN,   // not gzip: pass the bytes through
  MODE_GZIP,    // inside a gzip member
  MODE_DONE,    // the end of the input
  MODE_FAILED,  // a read or decompression error, in decode_error; reported again on every call
};

struct FastqState {
  struct NanoFastqSource source;
  // Decoder. zs.next_in/avail_in is the unconsumed part of `in` in every mode, not just gzip.
  z_stream zs;
  int zs_ready;  // inflateInit2 succeeded, so inflateEnd is due
  enum DecodeMode mode;
  int members;  // gzip members fully decoded so far
  int in_eof;   // the source returned 0
  unsigned char* in;
  char decode_error[200];
  int64_t batch_size;
  int64_t record_no;
  int done;
  // line reader: [pos, end) holds unconsumed bytes in buf
  char* buf;
  size_t cap, pos, end;
  // Start of the record being parsed, or NO_PIN. Buffer compaction keeps bytes from
  // here on so the record's earlier lines stay addressable while later ones are read.
  size_t pin;
  // Error found after good rows were already built: reported on the next get_next.
  int pending_rc;
  // A read failed (e.g. truncated gzip) but the data returned with it is still being consumed.
  // NextLine reports `read_error` once the buffered bytes run out.
  int read_failed;
  char read_error[256];
  struct ArrowSchema schema;
  char error[256];
};

// Records an error message and returns a negative errno-style code.
static int Fail(struct FastqState* s, const char* fmt, long long n) {
  snprintf(s->error, sizeof(s->error), fmt, n);
  return -EINVAL;
}

// Reports a read failure that was deferred until the bytes read before it were consumed.
static int ReadFailed(struct FastqState* s) {
  memcpy(s->error, s->read_error, sizeof(s->error));
  return -1;
}

// Strict UTF-8 check (rejects overlongs, surrogates and code points > U+10FFFF).
// Has an 8-bytes-at-a-time fast path since FASTQ is almost always pure ASCII.
static int IsValidUtf8(const char* data, size_t n) {
  const unsigned char* p = (const unsigned char*)data;
  const unsigned char* end = p + n;
  while (p < end) {
    if (end - p >= 8) {
      uint64_t w;
      memcpy(&w, p, 8);
      if ((w & 0x8080808080808080ULL) == 0) {
        p += 8;
        continue;
      }
    }
    unsigned char c = *p;
    if (c < 0x80) {
      p++;
      continue;
    }
    size_t need;
    unsigned char lo = 0x80, hi = 0xBF;  // allowed range of the 2nd byte
    if (c >= 0xC2 && c <= 0xDF) {
      need = 1;
    } else if (c >= 0xE0 && c <= 0xEF) {
      need = 2;
      if (c == 0xE0) lo = 0xA0;
      if (c == 0xED) hi = 0x9F;
    } else if (c >= 0xF0 && c <= 0xF4) {
      need = 3;
      if (c == 0xF0) lo = 0x90;
      if (c == 0xF4) hi = 0x8F;
    } else {
      return 0;
    }
    if ((size_t)(end - p) <= need) return 0;
    if (p[1] < lo || p[1] > hi) return 0;
    for (size_t i = 2; i <= need; i++) {
      if ((p[i] & 0xC0) != 0x80) return 0;
    }
    p += need + 1;
  }
  return 1;
}

// True if every byte is < 0x80. Written as a branch-free OR-reduction so the compiler
// can vectorize it.
static int IsAscii(const char* data, size_t n) {
  uint64_t acc = 0;
  size_t i = 0;
  for (; i + 8 <= n; i += 8) {
    uint64_t w;
    memcpy(&w, data + i, 8);
    acc |= w;
  }
  unsigned char tail = 0;
  for (; i < n; i++) tail |= (unsigned char)data[i];
  return ((acc & 0x8080808080808080ULL) | (tail & 0x80)) == 0;
}

// Moves the unconsumed input to the front of `in` and reads from the source until at least `want`
// bytes are buffered or the source ends. Returns 0, or -1 with decode_error set.
static int FillInput(struct FastqState* s, size_t want) {
  if (s->zs.avail_in > 0 && s->zs.next_in != s->in) {
    memmove(s->in, s->zs.next_in, s->zs.avail_in);
  }
  s->zs.next_in = s->in;
  while (s->zs.avail_in < want && !s->in_eof) {
    int64_t got = s->source.read(s->source.ctx, s->in + s->zs.avail_in, IN_CHUNK - s->zs.avail_in,
                                 s->decode_error, sizeof(s->decode_error));
    if (got < 0) return -1;
    if (got == 0) s->in_eof = 1;
    s->zs.avail_in += (unsigned)got;
  }
  return 0;
}

// Records a decoding failure. It sticks: every later Decode call reports it again.
static size_t DecodeFail(struct FastqState* s, const char* msg, size_t produced, int* failed) {
  if (msg) snprintf(s->decode_error, sizeof(s->decode_error), "%s", msg);
  s->mode = MODE_FAILED;
  *failed = 1;
  return produced;
}

// Writes up to n decoded bytes to out and returns how many (0 at the end of the input). Input
// that starts with the gzip magic is decompressed, anything else is passed through, which is
// what gzread did. Concatenated gzip members (bgzip) are read one after another, and bytes after
// the last member that are not another member are ignored, also like gzread.
//
// On a read or decompression error *failed is set and decode_error holds the message. The bytes
// decoded before the error are still returned with it, so the records before a truncation or a
// corrupt block are delivered first.
static size_t Decode(struct FastqState* s, unsigned char* out, size_t n, int* failed) {
  *failed = 0;
  for (;;) {
    switch (s->mode) {
      case MODE_SNIFF:
        if (FillInput(s, 2) != 0) return DecodeFail(s, NULL, 0, failed);
        if (s->zs.avail_in >= 2 && s->in[0] == 0x1f && s->in[1] == 0x8b) {
          int rc;
          if (s->zs_ready) {
            rc = inflateReset(&s->zs);
          } else {
            rc = inflateInit2(&s->zs, 16 + 15);  // gzip wrapper, 32 KiB window
            s->zs_ready = rc == Z_OK;
          }
          if (rc != Z_OK) return DecodeFail(s, "cannot start decompressing", 0, failed);
          s->mode = MODE_GZIP;
        } else {
          s->mode = s->members > 0 ? MODE_DONE : MODE_PLAIN;
        }
        break;

      case MODE_PLAIN:
        if (s->zs.avail_in > 0) {  // the bytes read while sniffing
          size_t k = s->zs.avail_in < n ? s->zs.avail_in : n;
          memcpy(out, s->zs.next_in, k);
          s->zs.next_in += k;
          s->zs.avail_in -= (unsigned)k;
          return k;
        }
        if (s->in_eof) return 0;
        {
          // Straight into the caller's buffer: no copy for uncompressed input.
          int64_t got = s->source.read(s->source.ctx, out, n, s->decode_error,
                                       sizeof(s->decode_error));
          if (got < 0) return DecodeFail(s, NULL, 0, failed);
          if (got == 0) s->in_eof = 1;
          return (size_t)got;
        }

      case MODE_GZIP: {
        if (s->zs.avail_in == 0 && FillInput(s, 1) != 0) return DecodeFail(s, NULL, 0, failed);
        s->zs.next_out = out;
        s->zs.avail_out = (unsigned)n;
        int rc = inflate(&s->zs, Z_NO_FLUSH);
        size_t produced = n - s->zs.avail_out;
        if (rc == Z_STREAM_END) {
          s->members++;
          s->mode = MODE_SNIFF;
        } else if (rc == Z_BUF_ERROR && s->zs.avail_in == 0 && s->in_eof) {
          // The input ended inside a member: a truncated file or a missing trailer.
          return DecodeFail(s, "unexpected end of file", produced, failed);
        } else if (rc != Z_OK && rc != Z_BUF_ERROR) {
          const char* msg = rc == Z_MEM_ERROR ? "out of memory"
                            : s->zs.msg       ? s->zs.msg
                                              : "invalid compressed data";
          return DecodeFail(s, msg, produced, failed);
        }
        if (produced > 0) return produced;
        break;  // consumed input without output yet (a header, or a member just ended)
      }

      case MODE_DONE:
        return 0;

      case MODE_FAILED:
        *failed = 1;
        return 0;
    }
  }
}

// Returns 1 with the line's offset into buf and length (without the trailing \n or
// \r\n), 0 on EOF, -1 on error. Offsets, unlike pointers, survive later calls as long
// as they are taken relative to s->pin.
static int NextLine(struct FastqState* s, size_t* off, size_t* len) {
  for (;;) {
    char* nl = s->pos < s->end ? memchr(s->buf + s->pos, '\n', s->end - s->pos) : NULL;
    if (nl) {
      size_t start = s->pos;
      size_t n = (size_t)(nl - (s->buf + start));
      s->pos = start + n + 1;
      if (n > 0 && s->buf[start + n - 1] == '\r') n--;
      *off = start;
      *len = n;
      return 1;
    }
    if (s->read_failed) return ReadFailed(s);  // the bytes read before the failure are used up
    // Need more data: compact (keeping the pinned record), grow, then refill.
    size_t from = s->pin < s->pos ? s->pin : s->pos;
    if (from > 0) {
      memmove(s->buf, s->buf + from, s->end - from);
      s->end -= from;
      s->pos -= from;
      if (s->pin != NO_PIN) s->pin -= from;
    }
    if (s->cap - s->end < READ_CHUNK) {
      size_t new_cap = s->cap * 2;
      char* nb = realloc(s->buf, new_cap);
      if (!nb) {
        snprintf(s->error, sizeof(s->error), "out of memory");
        return -1;
      }
      s->buf = nb;
      s->cap = new_cap;
    }
    int failed;
    size_t got = Decode(s, (unsigned char*)s->buf + s->end, s->cap - s->end, &failed);
    if (failed) {
      if (got == 0) {
        snprintf(s->error, sizeof(s->error), "read error: %.200s", s->decode_error);
        return -1;
      }
      // The same call also returned data. Keep it so the records before the failure are still
      // delivered, and raise the error once they have been consumed.
      snprintf(s->read_error, sizeof(s->read_error), "read error: %.200s", s->decode_error);
      s->read_failed = 1;
    }
    if (got == 0) {
      if (s->pos == s->end) return 0;
      // final line without trailing newline
      size_t start = s->pos, n = s->end - s->pos;
      s->pos = s->end;
      if (s->buf[start + n - 1] == '\r') n--;
      *off = start;
      *len = n;
      return 1;
    }
    s->end += got;
  }
}

struct Record {
  const char *name, *desc, *seq, *qual;  // desc is NULL when the header has none
  size_t name_len, desc_len, seq_len;    // qual has the same length as seq
};

// Reads and fully validates one record before anything is appended to Arrow.
// Returns 1 with `r` filled in (pointers valid until the next NextLine call),
// 0 on EOF, or a negative errno-style code with s->error set.
static int ParseRecordLines(struct FastqState* s, struct Record* r) {
  size_t rel[4], len[4], off;  // rel[i]: offset of line i relative to s->pin
  int got;

  for (;;) {
    s->pin = s->pos;
    if ((got = NextLine(s, &off, &len[0])) < 0) return -EIO;
    if (got == 0) return 0;
    if (len[0] > 0) break;  // tolerate blank lines between records
  }
  rel[0] = off - s->pin;
  s->record_no++;
  if (s->buf[off] != '@') {
    return Fail(s, "record %lld: header does not start with '@'", s->record_no);
  }

  static const char* const truncated[3] = {
      "record %lld: truncated after header",
      "record %lld: missing '+' separator line",
      "record %lld: truncated before quality line",
  };
  for (int i = 1; i < 4; i++) {
    if ((got = NextLine(s, &off, &len[i])) < 0) return -EIO;
    if (got == 0) return Fail(s, truncated[i - 1], s->record_no);
    rel[i] = off - s->pin;
  }

  const char* base = s->buf + s->pin;
  const char* header = base + rel[0];
  const char* plus = base + rel[2];
  if (len[2] == 0 || plus[0] != '+') return Fail(s, truncated[1], s->record_no);
  if (len[3] != len[1]) {
    return Fail(s, "record %lld: sequence and quality lengths differ", s->record_no);
  }

  r->name = header + 1;
  r->name_len = len[0] - 1;
  r->desc = NULL;
  r->desc_len = 0;
  for (size_t i = 0; i < r->name_len; i++) {
    if (r->name[i] == ' ' || r->name[i] == '\t') {
      r->desc = r->name + i + 1;
      r->desc_len = r->name_len - i - 1;
      r->name_len = i;
      break;
    }
  }
  r->seq = base + rel[1];
  r->seq_len = len[1];
  r->qual = base + rel[3];

  // The four lines are contiguous in the buffer, so one pass over the whole record
  // settles the (overwhelmingly common) pure-ASCII case; only otherwise check per field.
  if (IsAscii(base, rel[3] + len[3])) return 1;
  if (!IsValidUtf8(r->name, r->name_len) || (r->desc && !IsValidUtf8(r->desc, r->desc_len))) {
    return Fail(s, "record %lld: header is not valid UTF-8", s->record_no);
  }
  if (!IsValidUtf8(r->seq, r->seq_len)) {
    return Fail(s, "record %lld: sequence is not valid UTF-8", s->record_no);
  }
  if (!IsValidUtf8(r->qual, r->seq_len)) {
    return Fail(s, "record %lld: quality scores are not valid UTF-8", s->record_no);
  }
  return 1;
}

// Appends one value to a non-nullable utf8 column by writing its offset and data buffers
// directly. This skips ArrowArrayAppendString's type dispatch and validity-bitmap handling,
// which are pure overhead for columns that can never hold a null (no bitmap is allocated).
static inline int AppendNonNull(struct ArrowArray* col, const char* data, size_t n) {
  struct ArrowBuffer* offsets = ArrowArrayBuffer(col, 1);
  struct ArrowBuffer* values = ArrowArrayBuffer(col, 2);
  if (values->size_bytes + (int64_t)n > INT32_MAX) return EOVERFLOW;
  int rc = ArrowBufferReserve(offsets, (int64_t)sizeof(int32_t));
  if (rc != NANOARROW_OK) return rc;
  if ((rc = ArrowBufferReserve(values, (int64_t)n)) != NANOARROW_OK) return rc;
  // An empty value (e.g. an empty read name) can arrive before anything has been allocated, when
  // values->data is still NULL; NULL + 0 and memcpy(NULL, ..., 0) are undefined behaviour in C.
  if (n) memcpy(values->data + values->size_bytes, data, n);
  values->size_bytes += (int64_t)n;
  int32_t end = (int32_t)values->size_bytes;
  memcpy(offsets->data + offsets->size_bytes, &end, sizeof(end));
  offsets->size_bytes += (int64_t)sizeof(end);
  col->length++;
  return NANOARROW_OK;
}

static int AppendRecord(struct ArrowArray* array, const struct Record* r) {
  int rc;
  if ((rc = AppendNonNull(array->children[0], r->name, r->name_len)) != NANOARROW_OK) return rc;
  if (r->desc) {
    struct ArrowStringView dv = {r->desc, (int64_t)r->desc_len};
    rc = ArrowArrayAppendString(array->children[1], dv);
  } else {
    rc = ArrowArrayAppendNull(array->children[1], 1);
  }
  if (rc != NANOARROW_OK) return rc;
  if ((rc = AppendNonNull(array->children[2], r->seq, r->seq_len)) != NANOARROW_OK) return rc;
  if ((rc = AppendNonNull(array->children[3], r->qual, r->seq_len)) != NANOARROW_OK) return rc;
  // Equivalent to ArrowArrayFinishElement for a struct; the children's lengths are
  // validated once per batch by ArrowArrayFinishBuildingDefault.
  array->length++;
  return NANOARROW_OK;
}

static int GetSchema(struct ArrowArrayStream* stream, struct ArrowSchema* out) {
  struct FastqState* s = stream->private_data;
  return ArrowSchemaDeepCopy(&s->schema, out);
}

static int GetNext(struct ArrowArrayStream* stream, struct ArrowArray* out) {
  struct FastqState* s = stream->private_data;
  out->release = NULL;
  if (s->pending_rc) {
    int rc = s->pending_rc;
    s->pending_rc = 0;
    s->done = 1;
    return rc;
  }
  if (s->done) return 0;

  struct ArrowArray array;
  struct ArrowError nerr;
  int rc = ArrowArrayInitFromSchema(&array, &s->schema, &nerr);
  if (rc != NANOARROW_OK) {
    snprintf(s->error, sizeof(s->error), "%.200s", nerr.message);
    return rc;
  }
  if ((rc = ArrowArrayStartAppending(&array)) != NANOARROW_OK) goto fail;

  int64_t n = 0;
  while (n < s->batch_size) {
    struct Record rec;
    int got = ParseRecordLines(s, &rec);
    if (got < 0 && s->read_failed) {
      // Bytes decoded just before a corrupt or truncated stretch can be garbage, so a parse error
      // here is most likely a consequence of the read failure: report that instead.
      ReadFailed(s);
      got = -EIO;
    }
    if (got == 0) {
      s->done = 1;
      break;
    }
    if (got < 0) {
      s->pin = NO_PIN;
      if (n == 0) {
        s->done = 1;
        rc = -got;
        goto fail;
      }
      // Hand back the good rows now; the error surfaces on the next call.
      s->pending_rc = -got;
      break;
    }
    rc = AppendRecord(&array, &rec);
    s->pin = NO_PIN;  // the record's bytes were copied into Arrow buffers
    if (rc != NANOARROW_OK) goto fail;
    n++;
  }
  s->pin = NO_PIN;

  if (n == 0) {
    array.release(&array);
    return 0;  // end of stream: out->release stays NULL
  }
  if ((rc = ArrowArrayFinishBuildingDefault(&array, &nerr)) != NANOARROW_OK) {
    snprintf(s->error, sizeof(s->error), "%.200s", nerr.message);
    array.release(&array);
    return rc;
  }
  *out = array;
  return 0;

fail:
  if (!s->error[0]) snprintf(s->error, sizeof(s->error), "failed to build batch (%d)", rc);
  array.release(&array);
  return rc;
}

static const char* GetLastError(struct ArrowArrayStream* stream) {
  struct FastqState* s = stream->private_data;
  return s->error[0] ? s->error : NULL;
}

static void Release(struct ArrowArrayStream* stream) {
  struct FastqState* s = stream->private_data;
  if (s->zs_ready) inflateEnd(&s->zs);
  if (s->source.release) s->source.release(s->source.ctx);
  free(s->in);
  if (s->schema.release) s->schema.release(&s->schema);
  free(s->buf);
  free(s);
  stream->release = NULL;
}

static int64_t FileRead(void* ctx, void* buf, size_t n, char* err, size_t err_len) {
  FILE* f = ctx;
  size_t got = fread(buf, 1, n, f);
  if (got == 0 && ferror(f)) {
    snprintf(err, err_len, "%s", strerror(errno));
    return -1;
  }
  return (int64_t)got;
}

static void FileRelease(void* ctx) { fclose(ctx); }

int NanoFastqOpen(const char* path, int64_t batch_size, struct ArrowArrayStream* out,
                  struct ArrowError* err) {
  FILE* f = fopen(path, "rb");
  if (!f) {
    ArrowErrorSet(err, "cannot open '%s'", path);
    return ENOENT;
  }
  setvbuf(f, NULL, _IONBF, 0);  // the decoder reads in large chunks and has its own buffers
  struct NanoFastqSource source = {FileRead, FileRelease, f};
  return NanoFastqOpenSource(source, batch_size, out, err);
}

int NanoFastqOpenSource(struct NanoFastqSource source, int64_t batch_size,
                        struct ArrowArrayStream* out, struct ArrowError* err) {
  if (batch_size <= 0) {
    ArrowErrorSet(err, "batch_size must be positive");
    if (source.release) source.release(source.ctx);
    return EINVAL;
  }
  struct FastqState* s = calloc(1, sizeof(*s));
  if (!s) {
    if (source.release) source.release(source.ctx);
    return ENOMEM;
  }
  s->source = source;  // released with the stream from here on, including on failure below
  s->batch_size = batch_size;
  s->pin = NO_PIN;
  s->cap = 2 * READ_CHUNK;
  s->buf = malloc(s->cap);
  s->in = malloc(IN_CHUNK);
  s->zs.next_in = s->in;
  if (!s->buf || !s->in) {
    ArrowErrorSet(err, "out of memory");
    struct ArrowArrayStream tmp = {0};
    tmp.private_data = s;
    Release(&tmp);
    return ENOMEM;
  }

  ArrowSchemaInit(&s->schema);
  int rc = ArrowSchemaSetTypeStruct(&s->schema, 4);
  static const char* names[4] = {"name", "description", "sequence", "quality_scores"};
  for (int i = 0; rc == NANOARROW_OK && i < 4; i++) {
    rc = ArrowSchemaSetType(s->schema.children[i], NANOARROW_TYPE_STRING);
    if (rc == NANOARROW_OK) rc = ArrowSchemaSetName(s->schema.children[i], names[i]);
    if (rc == NANOARROW_OK && i != 1) s->schema.children[i]->flags &= ~ARROW_FLAG_NULLABLE;
  }
  if (rc != NANOARROW_OK) {
    ArrowErrorSet(err, "failed to build schema");
    struct ArrowArrayStream tmp = {0};
    tmp.private_data = s;
    Release(&tmp);
    return rc;
  }

  out->get_schema = GetSchema;
  out->get_next = GetNext;
  out->get_last_error = GetLastError;
  out->release = Release;
  out->private_data = s;
  return 0;
}
