"""Safe RFC-style email extraction using the Python standard library."""

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

from lifecase.evidence.extraction.errors import (
    EmptyContentError,
    InvalidEmailError,
    UnsupportedEmailContentError,
)
from lifecase.evidence.extraction.models import (
    ExtractionNotice,
    ParsedDocument,
    RawTextPart,
)

EML_EXTRACTOR_VERSION = "eml.v1"
_DISPLAY_HEADERS = ("Subject", "From", "To", "Date")


def extract_eml(path: Path) -> ParsedDocument:
    """Extract selected headers and a plain-text body without executing content."""

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise InvalidEmailError from error

    if b"\r\n\r\n" not in raw and b"\n\n" not in raw:
        raise InvalidEmailError

    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as error:
        raise InvalidEmailError from error
    if not isinstance(message, EmailMessage) or message.defects:
        raise InvalidEmailError
    if not any(message.get(header) is not None for header in _DISPLAY_HEADERS):
        raise InvalidEmailError

    body = message.get_body(preferencelist=("plain",))
    if body is None:
        if message.get_body(preferencelist=("html",)) is not None:
            raise UnsupportedEmailContentError
        raise EmptyContentError

    try:
        body_content = body.get_content()
    except (KeyError, LookupError, UnicodeError, ValueError) as error:
        raise InvalidEmailError from error
    if not isinstance(body_content, str) or not body_content.strip():
        raise EmptyContentError

    header_lines = [
        f"{header}: {message[header]}"
        for header in _DISPLAY_HEADERS
        if message.get(header) is not None
    ]
    text = "\n".join((*header_lines, "", body_content))

    attachment_count = sum(
        1 for part in message.walk() if part.get_content_disposition() == "attachment"
    )
    html_count = sum(1 for part in message.walk() if part.get_content_type() == "text/html")
    notices = tuple(
        notice
        for notice in (
            ExtractionNotice("ATTACHMENT_SKIPPED", attachment_count) if attachment_count else None,
            ExtractionNotice("HTML_SKIPPED", html_count) if html_count else None,
        )
        if notice is not None
    )
    return ParsedDocument(
        extractor_version=EML_EXTRACTOR_VERSION,
        parts=(RawTextPart(text, None, "email", 1.0),),
        notices=notices,
    )
