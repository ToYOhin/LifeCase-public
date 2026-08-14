"""Isolated parser process with a hard timeout and safe error transport."""

import multiprocessing
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path

from lifecase.evidence.extraction.eml_extractor import extract_eml
from lifecase.evidence.extraction.errors import (
    ExtractionError,
    ExtractionTimeoutError,
    UnsupportedExtractionMediaTypeError,
    error_from_code,
)
from lifecase.evidence.extraction.models import ExtractionConfig, ParsedDocument
from lifecase.evidence.extraction.pdf_extractor import extract_pdf
from lifecase.evidence.extraction.text_extractor import extract_plain_text

type _WorkerPayload = tuple[str, ParsedDocument | str]


def parse_in_isolated_process(
    path: Path, media_type: str, config: ExtractionConfig
) -> ParsedDocument:
    """Parse an immutable object in a spawn process and terminate it on timeout."""

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker_entry,
        args=(sender, str(path), media_type, config),
        daemon=True,
    )
    started = False
    try:
        process.start()
        started = True
        sender.close()
        if not receiver.poll(config.parser_timeout_seconds):
            _stop_process(process)
            raise ExtractionTimeoutError
        try:
            payload: _WorkerPayload = receiver.recv()
        except EOFError as error:
            raise ExtractionError from error
        process.join(timeout=1.0)
        if process.is_alive():
            _stop_process(process)

        kind, value = payload
        if kind == "ok" and isinstance(value, ParsedDocument):
            return value
        if kind == "error" and isinstance(value, str):
            raise error_from_code(value)
        raise ExtractionError
    finally:
        receiver.close()
        sender.close()
        if started and process.is_alive():
            _stop_process(process)
        if started:
            process.close()


def _worker_entry(
    sender: Connection,
    path_value: str,
    media_type: str,
    config: ExtractionConfig,
) -> None:
    try:
        parsed = _parse(Path(path_value), media_type, config)
        sender.send(("ok", parsed))
    except ExtractionError as error:
        sender.send(("error", error.code))
    except Exception:
        sender.send(("error", ExtractionError.code))
    finally:
        sender.close()


def _parse(path: Path, media_type: str, config: ExtractionConfig) -> ParsedDocument:
    if media_type == "text/plain":
        return extract_plain_text(path)
    if media_type == "message/rfc822":
        return extract_eml(path)
    if media_type == "application/pdf":
        return extract_pdf(
            path,
            max_pages=config.max_pdf_pages,
            max_content_stream_bytes=config.max_pdf_content_stream_bytes,
        )
    raise UnsupportedExtractionMediaTypeError


def _stop_process(process: BaseProcess) -> None:
    if not process.is_alive():
        return
    process.terminate()
    process.join(timeout=1.0)
    if process.is_alive():
        process.kill()
        process.join(timeout=1.0)
