def open_stream(path: str, batch_size: int = 65536) -> object:
    """Open a FASTQ file and return an ``arrow_array_stream`` PyCapsule.

    Raises ``ValueError`` if the file cannot be opened or ``batch_size`` is not positive.
    """
