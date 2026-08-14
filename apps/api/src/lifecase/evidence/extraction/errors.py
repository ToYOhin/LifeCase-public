"""Safe typed errors for extraction of untrusted content."""


class ExtractionError(RuntimeError):
    """Base class whose message is safe to expose without source content or paths."""

    code = "EXTRACTION_FAILED"
    safe_message = "Document extraction failed"

    def __init__(self) -> None:
        super().__init__(self.safe_message)


class SourceNotFoundError(ExtractionError):
    code = "SOURCE_NOT_FOUND"
    safe_message = "Source metadata was not found"


class ObjectPathError(ExtractionError):
    code = "OBJECT_PATH_INVALID"
    safe_message = "Source object path is invalid"


class ObjectIntegrityError(ExtractionError):
    code = "OBJECT_INTEGRITY_FAILED"
    safe_message = "Source object integrity validation failed"


class UnsupportedExtractionMediaTypeError(ExtractionError):
    code = "MEDIA_TYPE_UNSUPPORTED"
    safe_message = "Source media type is not supported for extraction"


class InvalidTextEncodingError(ExtractionError):
    code = "TEXT_ENCODING_INVALID"
    safe_message = "Text source is not valid UTF-8"


class EmptyContentError(ExtractionError):
    code = "EMPTY_CONTENT"
    safe_message = "Source contains no extractable text"


class InvalidEmailError(ExtractionError):
    code = "EMAIL_INVALID"
    safe_message = "Email source is malformed"


class UnsupportedEmailContentError(ExtractionError):
    code = "EMAIL_BODY_UNSUPPORTED"
    safe_message = "Email has no supported plain-text body"


class PdfMalformedError(ExtractionError):
    code = "PDF_MALFORMED"
    safe_message = "PDF source is malformed"


class PdfEncryptedError(ExtractionError):
    code = "PDF_ENCRYPTED"
    safe_message = "Encrypted PDF sources are not supported"


class PdfLimitExceededError(ExtractionError):
    code = "PDF_LIMIT_EXCEEDED"
    safe_message = "PDF exceeds configured extraction limits"


class OcrRequiredError(ExtractionError):
    code = "OCR_REQUIRED"
    safe_message = "PDF contains no extractable text and requires OCR"


class ExtractionTimeoutError(ExtractionError):
    code = "EXTRACTION_TIMEOUT"
    safe_message = "Document extraction exceeded its time limit"


class SegmentPersistenceError(ExtractionError):
    code = "SEGMENT_PERSISTENCE_FAILED"
    safe_message = "Extracted segments could not be committed"


_ERROR_TYPES: dict[str, type[ExtractionError]] = {
    error_type.code: error_type
    for error_type in (
        EmptyContentError,
        ExtractionError,
        ExtractionTimeoutError,
        InvalidEmailError,
        InvalidTextEncodingError,
        ObjectIntegrityError,
        ObjectPathError,
        OcrRequiredError,
        PdfEncryptedError,
        PdfLimitExceededError,
        PdfMalformedError,
        SegmentPersistenceError,
        SourceNotFoundError,
        UnsupportedEmailContentError,
        UnsupportedExtractionMediaTypeError,
    )
}


def error_from_code(code: str) -> ExtractionError:
    """Recreate a safe typed error returned by an isolated parser process."""

    return _ERROR_TYPES.get(code, ExtractionError)()
