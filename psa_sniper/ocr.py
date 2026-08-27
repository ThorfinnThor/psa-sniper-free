from __future__ import annotations

import io
import os
import re

import requests

from .models import CertCandidate


def ocr_enabled() -> bool:
    return os.getenv("ENABLE_OCR", "false").strip().lower() in {"1", "true", "yes", "on"}


def extract_cert_from_images(image_urls: list[str], max_images: int = 3) -> CertCandidate | None:
    if not ocr_enabled():
        return None
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageOps
    except ImportError:
        return None

    session = requests.Session()
    session.headers.update({"User-Agent": "psa-sniper-free/1.0"})

    for url in image_urls[:max_images]:
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            image = Image.open(io.BytesIO(response.content)).convert("RGB")
        except Exception:
            continue

        crops = [
            image.crop((0, 0, image.width, max(1, int(image.height * 0.45)))),
            image,
        ]
        for crop in crops:
            target_width = max(1400, crop.width * 2)
            ratio = target_width / crop.width
            resized = crop.resize((int(target_width), int(crop.height * ratio)))
            gray = ImageOps.autocontrast(ImageOps.grayscale(resized))
            gray = ImageEnhance.Contrast(gray).enhance(1.8)
            try:
                text = pytesseract.image_to_string(gray, config="--psm 6")
            except Exception:
                continue

            labeled = re.search(
                r"(?:cert(?:ification)?|psa)\s*(?:#|no\.?|number)?\s*[:#-]?\s*(\d{7,12})",
                text,
                flags=re.I,
            )
            if labeled:
                return CertCandidate(labeled.group(1), "OCR (beschriftet)", 0.86)

            compact = re.sub(r"\s+", "", text)
            numbers = re.findall(r"(?<!\d)(\d{8,12})(?!\d)", compact)
            if numbers:
                return CertCandidate(max(numbers, key=len), "OCR (Fallback)", 0.55)
    return None
