#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include <memory>

#include "fastq.h"

namespace nb = nanobind;

namespace {

// Releases the stream (if it was initialized) and frees it.
struct StreamDeleter {
  void operator()(ArrowArrayStream* stream) const noexcept {
    if (stream->release != nullptr) stream->release(stream);
    delete stream;
  }
};
using StreamPtr = std::unique_ptr<ArrowArrayStream, StreamDeleter>;

void stream_capsule_destructor(PyObject* capsule) {
  auto* stream = static_cast<ArrowArrayStream*>(PyCapsule_GetPointer(capsule, "arrow_array_stream"));
  if (stream == nullptr) {
    PyErr_Clear();
    return;
  }
  StreamPtr{stream};
}

}  // namespace

NB_MODULE(_core, m) {
  m.doc() = "Native FASTQ -> Arrow C stream reader";
  m.def(
      "open_stream",
      [](const std::string& path, int64_t batch_size) {
        // Value-initialized, so release is null until NanoFastqOpen succeeds.
        StreamPtr stream{new ArrowArrayStream{}};
        ArrowError err{};
        if (NanoFastqOpen(path.c_str(), batch_size, stream.get(), &err) != 0) {
          throw nb::value_error(err.message);
        }
        PyObject* cap = PyCapsule_New(stream.get(), "arrow_array_stream", stream_capsule_destructor);
        if (!cap) throw nb::python_error();
        // The capsule owns the stream now. (unique_ptr::release, not the Arrow callback.)
        stream.release();
        return nb::steal(cap);
      },
      nb::arg("path"), nb::arg("batch_size") = 65536);
}
