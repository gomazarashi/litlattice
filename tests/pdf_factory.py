"""Build small PDFs for tests without any fixture files."""

from collections.abc import Sequence
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


def _add_text_page(writer: PdfWriter, text: str) -> None:
    writer.add_blank_page(width=612, height=792)
    page = writer.pages[-1]
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )


def write_pdf(
    path: Path,
    *,
    pages_text: Sequence[str] = (),
    metadata: dict[str, str] | None = None,
    xmp: str | None = None,
    encrypted: bool = False,
) -> Path:
    writer = PdfWriter()
    if pages_text:
        for text in pages_text:
            _add_text_page(writer, text)
    else:
        writer.add_blank_page(width=612, height=792)
    if metadata:
        writer.add_metadata(metadata)
    if xmp is not None:
        stream = DecodedStreamObject()
        stream.set_data(xmp.encode())
        stream[NameObject("/Type")] = NameObject("/Metadata")
        stream[NameObject("/Subtype")] = NameObject("/XML")
        writer._root_object[NameObject("/Metadata")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("secret")
    with path.open("wb") as file:
        writer.write(file)
    return path
