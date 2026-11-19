# Copyright 2026 Edgar Ramírez-Mondragón

def open_stream(path: str, batch_size: int = 65536) -> object: ...
def open_file_stream(
    file: object,
    batch_size: int = 65536,
    offset: int | None = None,
) -> object: ...
