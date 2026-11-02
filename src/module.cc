#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include <cstdlib>

#include "fastq.h"

namespace nb = nanobind;

static void stream_capsule_destructor(PyObject* capsule) {
  auto* stream = static_cast<ArrowArrayStream*>(PyCapsule_GetPointer(capsule, "arrow_array_stream"));
  if (stream == nullptr) {
    PyErr_Clear();
    return;
  }
  if (stream->release != nullptr) stream->release(stream);
  free(stream);
}

NB_MODULE(_core, m) {
  m.doc() = "Native FASTQ -> Arrow C stream reader";
  m.def(
      "open_stream",
      [](const std::string& path, int64_t batch_size) {
        auto* stream = static_cast<ArrowArrayStream*>(malloc(sizeof(ArrowArrayStream)));
        ArrowError err{};
        int rc = NanoFastqOpen(path.c_str(), batch_size, stream, &err);
        if (rc != 0) {
          free(stream);
          throw nb::value_error(err.message);
        }
        PyObject* cap = PyCapsule_New(stream, "arrow_array_stream", stream_capsule_destructor);
        if (!cap) {
          stream->release(stream);
          free(stream);
          throw nb::python_error();
        }
        return nb::steal(cap);
      },
      nb::arg("path"), nb::arg("batch_size") = 65536);
}
