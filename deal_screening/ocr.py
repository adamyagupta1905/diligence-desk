"""Local English OCR, with bounded rendering and page/line provenance."""
from collections import OrderedDict
from pathlib import Path
import shutil


class LocalOCREngine:
    @staticmethod
    def executable():
        command = shutil.which("tesseract")
        if not command:
            command = next((p for p in ("/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract")
                            if Path(p).is_file()), None)
        if not command:
            raise ValueError("Local OCR needs Tesseract. On macOS install it with: brew install tesseract")
        return command

    @staticmethod
    def render_page(data, page_index, dpi=250):
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(data)
        try:
            page = document[page_index]
            try:
                scale = dpi / 72
                width, height = page.get_size()
                if width <= 0 or height <= 0 or width * height * scale * scale > 16_000_000:
                    raise ValueError("PDF page is too large to render safely for OCR (16 million pixel limit).")
                bitmap = page.render(scale=scale)
                try:
                    return bitmap.to_pil().convert("RGB").copy()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()

    def recognize(self, data, page_index):
        import pytesseract
        from pytesseract import Output
        pytesseract.pytesseract.tesseract_cmd = self.executable()
        image = self.render_page(data, page_index)
        try:
            result = pytesseract.image_to_data(image, lang="eng", config="--psm 3",
                                             output_type=Output.DICT, timeout=25)
            lines, confidences = OrderedDict(), []
            for i, word in enumerate(result["text"]):
                if not word.strip():
                    continue
                confidence = float(result["conf"][i])
                if confidence >= 0:
                    confidences.append(confidence)
                key = (result["block_num"][i], result["par_num"][i], result["line_num"][i])
                item = lines.setdefault(key, {"words": [], "boxes": []})
                item["words"].append(word)
                item["boxes"].append([int(result[k][i]) for k in ("left", "top", "width", "height")])
            layout = []
            for line in lines.values():
                x = min(b[0] for b in line["boxes"]); y = min(b[1] for b in line["boxes"])
                right = max(b[0] + b[2] for b in line["boxes"])
                bottom = max(b[1] + b[3] for b in line["boxes"])
                layout.append({"text": " ".join(line["words"]), "bbox": [x, y, right - x, bottom - y]})
            text = "\n".join(line["text"] for line in layout)
            if not text.strip():
                raise ValueError(f"OCR found no readable text on page {page_index + 1}.")
            return {"text": text, "extraction_method": "ocr", "ocr_language": "eng",
                    "ocr_confidence": sum(confidences) / len(confidences) if confidences else 0.,
                    "ocr_lines": layout, "ocr_image_size": list(image.size),
                    "warning": "OCR can misread digits, signs and units. Check the original page; confidence is not a probability of correctness."}
        except pytesseract.TesseractError as exc:
            raise ValueError(f"Local OCR failed on page {page_index + 1}; check Tesseract and its English language data.") from exc
        except RuntimeError as exc:
            raise ValueError(f"OCR timed out on page {page_index + 1}; use a clearer or shorter document.") from exc
        finally:
            image.close()
