"""Tests de extraccion de texto de escritos subidos (PDF/DOCX). Sin red."""

import io

import pytest

from modulos.jurisprudencia.stjer import extraccion_documento as E


def _docx_de_prueba(parrafos) -> bytes:
    import docx

    documento = docx.Document()
    for p in parrafos:
        documento.add_paragraph(p)
    buffer = io.BytesIO()
    documento.save(buffer)
    return buffer.getvalue()


def test_extrae_texto_de_docx():
    datos = _docx_de_prueba(["Primer párrafo de la demanda.", "Segundo párrafo."])
    texto = E.extraer_texto(datos, "demanda.docx")
    assert "Primer párrafo de la demanda." in texto
    assert "Segundo párrafo." in texto


def test_docx_vacio_da_texto_vacio():
    datos = _docx_de_prueba([])
    assert E.extraer_texto(datos, "vacio.docx") == ""


def test_doc_antiguo_no_soportado():
    with pytest.raises(E.ErrorExtraccion, match=r"\.doc"):
        E.extraer_texto(b"cualquier cosa", "demanda.doc")


def test_extension_desconocida_no_soportada():
    with pytest.raises(E.ErrorExtraccion, match="no soportado"):
        E.extraer_texto(b"cualquier cosa", "demanda.txt")


def test_pdf_corrupto_da_error_de_extraccion():
    with pytest.raises(E.ErrorExtraccion):
        E.extraer_texto(b"esto no es un PDF valido", "demanda.pdf")


def test_docx_corrupto_da_error_de_extraccion():
    with pytest.raises(E.ErrorExtraccion):
        E.extraer_texto(b"esto no es un DOCX valido", "demanda.docx")
