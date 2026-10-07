"""要項PDFのテキスト層を取り出す。画像だけのPDF（テキスト層なし）は、OCRせず失敗として扱う。
取り出し方: ①pypdf（純Python。本人のWindows PCの実PDFで、浜松の要項4件の文字を取り出せたことを確認済み）→ ②pypdf が無い・失敗した場合は pdftotext -layout（poppler）。
どちらで取り出したかは、pdf_to_text.last_extractor に残す（実行記録用）。"""
import os
import subprocess
import tempfile


class PdfError(Exception):
    pass


def _pypdf_text(data: bytes) -> str:
    import io
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data))
    return "\f".join((pg.extract_text() or "") for pg in r.pages)


def pdf_to_text(data: bytes, run=subprocess.run) -> str:
    if not data.startswith(b"%PDF"):
        raise PdfError("PDFではありません")
    pdf_to_text.last_extractor = None
    try:
        text = _pypdf_text(data)
        if len(text.strip()) >= 50:
            try:
                import pypdf
                pdf_to_text.last_extractor = "pypdf %s" % pypdf.__version__
            except Exception:       # noqa: BLE001
                pdf_to_text.last_extractor = "pypdf"
            return text
    except ImportError:
        pass
    except Exception:       # noqa: BLE001  （pypdf が読めないPDFは、pdftotext を試す）
        pass
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.pdf"), os.path.join(d, "out.txt")
        with open(src, "wb") as f:
            f.write(data)
        try:
            r = run(["pdftotext", "-layout", src, dst], capture_output=True, timeout=60)
        except FileNotFoundError:
            raise PdfError("pdftotext が見つかりません（poppler-utils が必要）")
        except subprocess.TimeoutExpired:
            raise PdfError("pdftotext がタイムアウトしました")
        if r.returncode != 0 or not os.path.exists(dst):
            raise PdfError("pdftotext が失敗しました")
        text = open(dst, encoding="utf-8", errors="replace").read()
    pdf_to_text.last_extractor = "pdftotext"
    if len(text.strip()) < 50:
        raise PdfError("テキスト層がありません（画像だけのPDFの可能性。OCRは使いません）")
    return text
