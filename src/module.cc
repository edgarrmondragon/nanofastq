#include <nanobind/nanobind.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/string.h>

#include <cstdio>
#include <cstring>
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

// Hands a stream that NanoFastqOpen* filled in to Python, as an "arrow_array_stream" capsule.
nb::object to_capsule(StreamPtr stream) {
  PyObject* cap = PyCapsule_New(stream.get(), "arrow_array_stream", stream_capsule_destructor);
  if (!cap) throw nb::python_error();
  // The capsule owns the stream now. (unique_ptr::release, not the Arrow callback.)
  stream.release();
  return nb::steal(cap);
}

// A NanoFastqSource over a Python binary file object.
//
// The stream's consumer may call get_next, and so read, from any thread, with or without the
// GIL (DuckDB scans on its own threads), so every call into Python takes the GIL first. When
// `seekable` is set, each read seeks to this stream's own position first, so several streams
// over the same file object, and other reads of it in between, do not disturb one another.
struct PyFileSource {
  nb::object file;
  nb::object readinto;  // None if the object has no readinto: read() is used instead
  bool seekable;
  int64_t offset;
};

void set_error(char* err, size_t err_len, const nb::python_error& e) {
  std::snprintf(err, err_len, "%s: %s", nb::type_name(e.type()).c_str(), nb::str(e.value()).c_str());
}

int64_t py_read(void* ctx, void* buf, size_t n, char* err, size_t err_len) {
  auto* src = static_cast<PyFileSource*>(ctx);
  if (!nb::is_alive()) {
    std::snprintf(err, err_len, "the Python interpreter has shut down");
    return -1;
  }
  nb::gil_scoped_acquire gil;
  try {
    if (src->seekable) src->file.attr("seek")(src->offset);
    size_t got;
    if (!src->readinto.is_none()) {
      // Let the file write straight into our buffer, then make sure it kept no view of it.
      nb::object view = nb::steal(
          PyMemoryView_FromMemory(static_cast<char*>(buf), static_cast<Py_ssize_t>(n), PyBUF_WRITE));
      if (!view) throw nb::python_error();
      nb::object r = src->readinto(view);
      view.attr("release")();
      if (r.is_none()) {
        std::snprintf(err, err_len, "the file object is non-blocking and had no data");
        return -1;
      }
      got = nb::cast<size_t>(r);
    } else {
      nb::object r = src->file.attr("read")(n);
      Py_buffer data;
      if (PyObject_GetBuffer(r.ptr(), &data, PyBUF_SIMPLE) != 0) {
        PyErr_Clear();
        std::snprintf(err, err_len, "read() returned %s, not bytes; open the file in binary mode",
                      nb::type_name(r.type()).c_str());
        return -1;
      }
      got = static_cast<size_t>(data.len);
      if (got <= n) std::memcpy(buf, data.buf, got);
      PyBuffer_Release(&data);
    }
    if (got > n) {
      std::snprintf(err, err_len, "the file object returned more bytes than requested");
      return -1;
    }
    src->offset += static_cast<int64_t>(got);
    return static_cast<int64_t>(got);
  } catch (const nb::python_error& e) {
    set_error(err, err_len, e);
    return -1;
  } catch (const std::exception& e) {  // e.g. nb::cast_error: readinto returned a non-integer
    std::snprintf(err, err_len, "%s", e.what());
    return -1;
  }
}

void py_release(void* ctx) {
  auto* src = static_cast<PyFileSource*>(ctx);
  if (!nb::is_alive()) {
    // Too late to drop references: leak them rather than touch a dead interpreter.
    src->file.release();
    src->readinto.release();
    delete src;
    return;
  }
  nb::gil_scoped_acquire gil;
  delete src;
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
        return to_capsule(std::move(stream));
      },
      nb::arg("path"), nb::arg("batch_size") = 65536);
  m.def(
      "open_file_stream",
      [](nb::object file, int64_t batch_size, std::optional<int64_t> offset) {
        auto* src = new PyFileSource{file, nb::getattr(file, "readinto", nb::none()),
                                     offset.has_value(), offset.value_or(0)};
        NanoFastqSource source{py_read, py_release, src};
        StreamPtr stream{new ArrowArrayStream{}};
        ArrowError err{};
        // Takes ownership of src, even when it fails. The GIL is held here, which py_release
        // takes again; that is fine, it nests.
        if (NanoFastqOpenSource(source, batch_size, stream.get(), &err) != 0) {
          throw nb::value_error(err.message);
        }
        return to_capsule(std::move(stream));
      },
      nb::arg("file"), nb::arg("batch_size") = 65536, nb::arg("offset") = nb::none());
}
