// libFuzzer harness for the FASTQ reader. The first input byte picks the batch size (1-8), the
// rest is the file's content. Every batch handed out must pass nanoarrow's full validation.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#include "fastq.h"

int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
  if (size < 1) return 0;

  // The reader opens a path, so write the input to a file.
  static char path[64];
  if (!path[0]) snprintf(path, sizeof path, "/tmp/nanofastq-fuzz-%d", (int)getpid());
  FILE* f = fopen(path, "wb");
  if (!f) abort();
  fwrite(data + 1, 1, size - 1, f);
  fclose(f);

  struct ArrowArrayStream stream;
  struct ArrowError err;
  if (NanoFastqOpen(path, 1 + data[0] % 8, &stream, &err) != 0) return 0;

  struct ArrowSchema schema;
  if (stream.get_schema(&stream, &schema) != 0) abort();

  for (;;) {
    struct ArrowArray array;
    if (stream.get_next(&stream, &array) != 0) {
      (void)stream.get_last_error(&stream);
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
  return 0;
}
